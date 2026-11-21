import copy
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from io import StringIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from xml.sax.saxutils import escape

from market_intelligence import primary_news_probe as probe, sources
from market_intelligence.models import Invalid


SNAPSHOT = "a" * 64
CHAIN = "c" * 64
APPROVAL = (SNAPSHOT, CHAIN)
NOW = datetime(2026, 11, 21, 12, tzinfo=UTC)
WINDOW = timedelta(seconds=2678400)
HOST = "https://www.federalreserve.gov"
RELEASES = HOST + "/newsevents/pressreleases/"
PRIVATE = "do-not-persist-item-or-error"


def _item(key="a", *, title=PRIVATE, link=None, date=NOW, extra=""):
    link = RELEASES + f"monetary20261121{key}.htm" if link is None else link
    date = format_datetime(date, usegmt=True) if isinstance(date, datetime) else date
    return (f"<item><title>{escape(title)}</title><link>{escape(link)}</link>"
            f"<pubDate>{date}</pubDate>{extra}</item>")


def _feed(*items, channel=""):
    return ('<rss version="2.0" xmlns:dc="urn:dc"><channel>' + channel +
            "".join(items) + "</channel></rss>").encode()


class JournalPathTests(unittest.TestCase):
    def test_os_account_path_ignores_environment(self):
        initial = probe._path()
        with patch.dict(os.environ, {"HOME": "changed-home", "USERPROFILE": "changed-profile",
                                     "LOCALAPPDATA": "changed-local"}):
            self.assertTrue(probe._path() == initial)
        suffix = (("AppData", "Local", "a-share-market-intelligence") if os.name == "nt"
                  else (".a-share-market-intelligence",))
        self.assertEqual(initial.parts[-len(suffix) - 1:], (*suffix, probe._OPERATION + ".jsonl"))

    def test_invalid_account_paths_are_rejected(self):
        if os.name == "nt":
            target = "market_intelligence.primary_news_probe._windows_profile"
            values = ("", "relative/profile", "C:/", "C:/profile/../other", "C:/profile:stream", "C:/bad\0path")
        else:
            target = "pwd.getpwuid"
            values = ("", "relative/profile", "/", "/profile/../other", "/bad\0path")
        for value in values:
            with self.subTest(value=value):
                result = value if os.name == "nt" else SimpleNamespace(pw_dir=value)
                with patch(target, return_value=result) as lookup, self.assertRaisesRegex(
                        Invalid, "^invalid_journal_account_path$"):
                    probe._path()
                if os.name == "posix":
                    lookup.assert_called_once_with(os.getuid())

    def test_windows_query_uses_bounded_buffer_and_closes_token(self):
        import ctypes

        for failed in (None, "token", "profile", "size", "close"):
            with self.subTest(failed=failed):
                process = Mock(return_value=17)
                close = Mock(return_value=failed != "close")

                def open_token(process_handle, access, result):
                    self.assertEqual((process_handle, access), (17, 0x0008))
                    result._obj.value = 41
                    return failed != "token"

                def get_profile(token, buffer, size):
                    self.assertEqual(token.value, 41)
                    self.assertEqual(len(buffer), 32768)
                    self.assertEqual(size._obj.value, len(buffer))
                    buffer.value = "C:/Synthetic/Profile"
                    size._obj.value = len(buffer) + 1 if failed == "size" else len(buffer.value) + 1
                    return failed != "profile"

                native = {
                    "kernel32": SimpleNamespace(GetCurrentProcess=process, CloseHandle=close),
                    "advapi32": SimpleNamespace(OpenProcessToken=Mock(side_effect=open_token)),
                    "userenv": SimpleNamespace(GetUserProfileDirectoryW=Mock(side_effect=get_profile)),
                }
                with patch.object(ctypes, "WinDLL", create=True, side_effect=lambda name, **kw: native[name]):
                    if failed is None:
                        self.assertEqual(probe._windows_profile(), "C:/Synthetic/Profile")
                    else:
                        with self.assertRaisesRegex(Invalid, "^journal_account_unavailable$"):
                            probe._windows_profile()
                if failed == "token":
                    close.assert_not_called()
                    native["userenv"].GetUserProfileDirectoryW.assert_not_called()
                else:
                    close.assert_called_once()
                    self.assertEqual(close.call_args.args[0].value, 41)


class PrimaryNewsProbeTests(unittest.TestCase):
    def setUp(self):
        directory = Path(self.enterContext(TemporaryDirectory()))
        self.path = directory / "p4fed-board-rss-20261121-v1.jsonl"
        self.enterContext(patch.object(probe, "_path", side_effect=lambda: self.path))
        self.enterContext(patch.object(probe, "utc_now", return_value=NOW))
        self.fetch = self.enterContext(patch.object(
            sources, "fetch", side_effect=AssertionError("unstubbed transport")))
        self.addCleanup(self._assert_fetch_stubbed)

    def _assert_fetch_stubbed(self):
        if isinstance(self.fetch.side_effect, AssertionError):
            self.fetch.assert_not_called()

    def _body(self, body):
        self.fetch.side_effect = None
        self.fetch.return_value = sources.Response(body, "application/rss+xml")

    def _observe(self, authorize=lambda: APPROVAL):
        return probe._observe(authorize)

    def _consumed(self):
        attempts = self.fetch.call_count
        with self.assertRaisesRegex(Invalid, "^probe_already_consumed$"):
            self._observe()
        self.assertEqual(self.fetch.call_count, attempts)

    def test_pending_closed_schema(self):
        value = probe.pending(SNAPSHOT)
        self.assertEqual(value, {"schema_version": 1, "package": "P4FED", "snapshot_id": SNAPSHOT,
                                 "status": "NOT_RUN", "observation": None})
        self.assertIs(probe.validate_receipt(value, SNAPSHOT), value)
        with self.assertRaises(Invalid):
            probe.validate_receipt(value, SNAPSHOT, terminal=True)
        with self.assertRaises(Invalid):
            probe.pending("not-a-fingerprint")
        with self.assertRaises(Invalid):
            probe.validate_receipt(value, "b" * 64)
        for changes in ({"observation": {}}, {"schema_version": True}, {"package": "P4T"}, {"extra": None}):
            with self.subTest(changes=changes), self.assertRaises(Invalid):
                probe.validate_receipt(value | changes, SNAPSHOT)

    def test_one_shot_durability_order_and_disclosure(self):
        events, handles = [], []
        append, attached, sync = probe._append, probe._attached, probe._sync_namespace

        def record(handle, value):
            append(handle, value)
            handles.append(handle)
            events.append(value["kind"])

        def synchronize(path):
            sync(path)
            self.assertEqual(path, self.path)
            events.append("sync")

        def reassert(handle, path):
            attached(handle, path)
            if handles:
                self.assertIs(handle, handles[0])
            events.append("attached")

        def authorize():
            events.append("gate")
            if len(events) > 1:
                marker = json.loads(self.path.read_text(encoding="utf-8"))
                self.assertEqual(marker, {"kind": "reservation", "operation_id": self.path.stem,
                                          "snapshot_id": SNAPSHOT, "approval_chain_sha256": CHAIN,
                                          "started_at": "2026-11-21T12:00:00Z"})
            return APPROVAL

        def fetch(source):
            events.append("fetch")
            self.assertEqual((source.source_id, source.endpoint), ("fed_press", sources.FED_URL))
            body = _feed(_item(extra="<description>private-description</description>"))
            return sources.Response(body, "application/rss+xml")

        self.fetch.side_effect = fetch
        output = StringIO()
        with (patch.multiple(probe, _append=record, _sync_namespace=synchronize, _attached=reassert),
              redirect_stdout(output), redirect_stderr(output)):
            receipt = self._observe(authorize)
        self.assertEqual(events, ["gate", "attached", "reservation", "sync", "gate", "attached", "fetch",
                                  "attached", "final", "attached"])
        self.assertEqual(len(handles), 2)
        self.assertIs(handles[0], handles[1])
        self.assertEqual(receipt["status"], "FRESH")
        self.assertEqual(receipt["observation"]["dispatch_attempts"], 1)
        self.assertIs(probe.validate_receipt(receipt, SNAPSHOT, terminal=True), receipt)
        journal = self.path.read_text(encoding="utf-8")
        self.assertEqual(json.loads(journal.splitlines()[1]), {"kind": "final", "receipt": receipt})
        self.assertNotIn(CHAIN, journal.splitlines()[1])
        for token in (PRIVATE, "<rss", "private-description", RELEASES, str(self.path), "20261121a.htm"):
            self.assertNotIn(token, journal)
            self.assertNotIn(token, json.dumps(receipt))
        self.assertEqual(output.getvalue(), "")
        self._consumed()
        self.assertEqual(self.path.read_text(encoding="utf-8"), journal)

    def test_count_partition_and_same_capture(self):
        same = _item()
        body = _feed(
            same, same, _item("b", date=NOW - timedelta(days=1)),
            _item("c", date=NOW - WINDOW), _item("d", date=NOW - WINDOW - timedelta(seconds=1)),
            _item("e", date=NOW + timedelta(seconds=1)), _item("f", date="21 Nov 2026 12:00:00 -0000"),
            _item("g"), _item("g", title="conflicting title"),
            _item("h"), _item("h", date=NOW + timedelta(seconds=1)),
            _item("i", extra="<dc:rights>Third-party rights</dc:rights>"),
            _item("j", link=RELEASES + "monetary20261121j.htm?tracking=1"),
        )
        expected = {"total": 13, "fresh": 3, "within_24h": 2, "stale": 1, "duplicate": 1, "conflict": 4,
                    "future": 1, "invalid": 1, "out_of_scope": 1, "attribution": 1}
        self.assertEqual(probe._parse_counts(body, NOW), expected)
        self._body(body)
        receipt = self._observe()
        self.assertEqual(receipt["status"], "FRESH")
        self.assertEqual(receipt["observation"]["counts"], expected)
        self.assertEqual(receipt["observation"]["freshness_seconds"], 2678400)

    def test_canonical_release_links(self):
        filename = "monetary20261121a.htm"
        for link in (
                RELEASES.replace("https:", "http:") + filename,
                RELEASES.replace("www.", "") + filename,
                RELEASES.replace("www.", "WWW.") + filename,
                RELEASES.replace(".gov/", ".gov:443/") + filename,
                RELEASES.replace("//www.", "//ignored@www.") + filename,
                RELEASES + filename + "#fragment", RELEASES + filename + "?",
                RELEASES + "monetary20261121%61.htm", RELEASES + "../pressreleases/" + filename,
                RELEASES + "/" + filename, RELEASES + "default.htm", RELEASES + filename + " ",
                HOST + "/newsevents/speech/" + filename):
            with self.subTest(link=link):
                counts = probe._parse_counts(_feed(_item(link=link)), NOW)
                self.assertEqual((counts["out_of_scope"], counts["fresh"]), (1, 0))

    def test_item_attribution_fails_closed(self):
        for marker in (
                "<source>Third Party</source>",
                '<source url="https://other.example/">Federal Reserve Board</source>',
                "<author>Federal Reserve Board and third party</author>",
                "<dc:creator>Federal Reserve Bank of New York</dc:creator>",
                "<dc:rights>All rights reserved</dc:rights>", "<copyright/>",
                '<extension dc:publisher="Third Party"/>'):
            with self.subTest(marker=marker):
                counts = probe._parse_counts(_feed(_item(extra=marker)), NOW)
                self.assertEqual((counts["attribution"], counts["fresh"]), (1, 0))

    def test_board_identity_and_opaque_title_and_description(self):
        board = "<dc:creator>Board of Governors of the Federal Reserve System</dc:creator>"
        source = f'<source url="{sources.FED_URL}">Federal Reserve Board</source>'
        description = "<description><dc:creator>Third Party</dc:creator>ignored</description>"
        body = _feed(_item(title="Third party words alone are not attribution", extra=source + description),
                     channel=board + f"<link>{HOST}/newsevents/pressreleases.htm</link>")
        self.assertEqual(probe._parse_counts(body, NOW)["fresh"], 1)

    def test_clean_duplicate_cannot_rescue_conflicted_attribution(self):
        body = _feed(_item(), _item(extra="<source>Third Party</source>"))
        self.assertEqual(probe._parse_counts(body, NOW)["conflict"], 2)

    def test_malformed_third_party_duplicate_blocks_fresh_receipt(self):
        ambiguous = _item(extra="<title>ambiguous</title><source>Third Party</source>")
        self._body(_feed(_item(), ambiguous))
        receipt = self._observe()
        row = receipt["observation"]
        self.assertEqual((receipt["status"], row["error"]), ("UNKNOWN", "inconclusive_items"))
        self.assertEqual(row["counts"], {"total": 2, "fresh": 0, "within_24h": 0, "stale": 0,
                                         "duplicate": 0, "conflict": 1, "future": 0, "invalid": 1,
                                         "out_of_scope": 0, "attribution": 0})
        self.assertIs(probe.validate_receipt(receipt, SNAPSHOT, terminal=True), receipt)

    def test_malformed_row_taints_each_immediate_locator_but_not_description(self):
        second = RELEASES + "monetary20261121b.htm"
        ignored = RELEASES + "monetary20261121c.htm"
        description = f"<description><link>{ignored}</link></description>"
        for ambiguous in (_item(extra=f'<dc:link href="{second}"/>' + description),
                          _item(extra=description).replace("<link>", f'<link href="{second}">', 1)):
            with self.subTest(ambiguous=ambiguous):
                counts = probe._parse_counts(_feed(ambiguous, _item(), _item("b"), _item("c")), NOW)
                self.assertEqual((counts["total"], counts["invalid"], counts["conflict"], counts["fresh"]),
                                 (4, 1, 2, 1))
        for item in (_item(date="21 Nov 2026 12:00:00"), _item(title="")):
            with self.subTest(item=item):
                counts = probe._parse_counts(_feed(_item(), item), NOW)
                self.assertEqual((counts["invalid"], counts["conflict"], counts["fresh"]), (1, 1, 0))

    def test_ambiguous_dates_and_fields(self):
        for date in ("21 Nov 2026 12:00:00", "21 Nov 2026 12:00:00 XYZ", "21 Nov 2026 12:00:00 +0060",
                     "Wed, 21 Nov 2026 12:00:00 GMT", "21 Nov 2026 12:00:00 GMT ignored"):
            with self.subTest(date=date):
                counts = probe._parse_counts(_feed(_item(date=date)), NOW)
                self.assertEqual((counts["invalid"], counts["fresh"]), (1, 0))
        for extra in ("<title>duplicate</title>", "<dc:title>duplicate</dc:title>", "<pubDate/>"):
            with self.subTest(extra=extra):
                self.assertEqual(probe._parse_counts(_feed(_item(extra=extra)), NOW)["invalid"], 1)

    def test_receipt_freshness_states(self):
        for index, (body, status, reason) in enumerate((
                (_feed(), "NO_FRESH", None),
                (_feed(_item(date=NOW - WINDOW - timedelta(seconds=1))), "NO_FRESH", None),
                (_feed(_item(date=NOW + timedelta(seconds=1))), "UNKNOWN", "inconclusive_items"),
                (_feed(_item(), _item(title="conflict")), "UNKNOWN", "inconclusive_items"))):
            with self.subTest(index=index):
                self.path = self.path.with_name(f"freshness-{index}.jsonl")
                self._body(body)
                receipt = self._observe()
                self.assertEqual((receipt["status"], receipt["observation"]["error"]), (status, reason))
                self._consumed()

    def test_invalid_response_consumes(self):
        for index, body in enumerate((
                b"", b"<invalid-" + PRIVATE.encode(), b"\xff", b"<rss>\x00</rss>",
                b'<!DOCTYPE rss [<!ENTITY a "text">]><rss><channel/></rss>',
                b'<?xml version="1.0" encoding="iso-8859-1"?><rss><channel/></rss>',
                b"<rss><channel/><channel/></rss>", b"<rss><channel><wrap><item/></wrap></channel></rss>",
                _feed(_item(), channel="<dc:rights>Third Party</dc:rights>"),
                _feed(_item(), channel="<link>https://other.example/</link>"),
                _feed(*(["<item/>"] * 1001)), b" " * 512001)):
            with self.subTest(index=index):
                self.path = self.path.with_name(f"invalid-{index}.jsonl")
                self._body(body)
                receipt = self._observe()
                row = receipt["observation"]
                self.assertEqual((receipt["status"], row["error"], row["counts"]),
                                 ("FAILED", "invalid_response", None))
                self.assertNotIn(PRIVATE, self.path.read_text(encoding="utf-8"))
                self._consumed()

    def test_invalid_response_object(self):
        for index, response in enumerate((None, sources.Response(b"", PRIVATE),
                                          sources.Response(b"", "application/xml", True))):
            with self.subTest(index=index):
                self.path = self.path.with_name(f"response-{index}.jsonl")
                self.fetch.side_effect = None
                self.fetch.return_value = response
                self.assertEqual(self._observe()["status"], "FAILED")
                self._consumed()

    def test_exact_item_and_byte_bounds(self):
        body = _feed(*(["<item/>"] * 1000))
        counts = probe._parse_counts(body.ljust(512000), NOW)
        self.assertEqual((counts["invalid"], counts["total"]), (1000, 1000))

    def test_failure_and_interruption_are_consumed(self):
        for index, (exception, status, reason) in enumerate((
                (sources.TransportError(PRIVATE, "failed"), "FAILED", "transport_failed"),
                (sources.TransportError(PRIVATE, "unknown"), "UNKNOWN", "transport_unknown"),
                (RuntimeError(PRIVATE), "UNKNOWN", "transport_unknown"),
                (KeyboardInterrupt(PRIVATE), "UNKNOWN", "interrupted"),
                (SystemExit(PRIVATE), "UNKNOWN", "interrupted"))):
            with self.subTest(index=index):
                self.path = self.path.with_name(f"failure-{index}.jsonl")
                self.fetch.side_effect = exception
                output = StringIO()
                with redirect_stdout(output), redirect_stderr(output):
                    receipt = self._observe()
                row = receipt["observation"]
                self.assertEqual((receipt["status"], row["error"], row["dispatch_attempts"]), (status, reason, 1))
                self.assertNotIn(PRIVATE, self.path.read_text(encoding="utf-8"))
                self.assertEqual(output.getvalue(), "")
                self._consumed()

    def test_existing_marker_is_neither_read_nor_changed(self):
        original_open = Path.open

        def guarded_open(path, mode="r", *args, **kwargs):
            if path == self.path:
                self.assertEqual(mode, "xb")
            return original_open(path, mode, *args, **kwargs)

        for index, marker in enumerate((b"", b'{ "kind": "reser', b"not a receipt")):
            with self.subTest(index=index):
                self.path = self.path.with_name(f"marker-{index}.jsonl")
                self.path.write_bytes(marker)
                with patch.object(Path, "open", guarded_open):
                    self._consumed()
                self.assertEqual(self.path.read_bytes(), marker)

    def test_reservation_failures_never_dispatch(self):
        for index, (target, error) in enumerate((("_sync_namespace", OSError(PRIVATE)),
                                                 ("_append", KeyboardInterrupt(PRIVATE)))):
            with self.subTest(target=target):
                self.path = self.path.with_name(f"reservation-{index}.jsonl")
                with patch.object(probe, target, side_effect=error):
                    with self.assertRaisesRegex(Invalid, "^probe_journal_unavailable$"):
                        self._observe()
                self.assertTrue(self.path.is_file())
                if target == "_append":
                    self.assertEqual(self.path.stat().st_size, 0)
                self._consumed()

    def test_second_gate_refusal_is_terminal(self):
        for index, second in enumerate((("b" * 64, CHAIN), None, Invalid(PRIVATE), KeyboardInterrupt(PRIVATE))):
            with self.subTest(index=index):
                self.path = self.path.with_name(f"gate-{index}.jsonl")

                def gate():
                    if not self.path.exists():
                        return APPROVAL
                    if isinstance(second, BaseException):
                        raise second
                    return second

                receipt = self._observe(gate)
                row = receipt["observation"]
                self.assertEqual((receipt["status"], row["dispatch_attempts"]), ("UNKNOWN", 0))
                expected = "interrupted" if isinstance(second, KeyboardInterrupt) else "gate_rejected"
                self.assertEqual(row["error"], expected)
                self.assertNotIn(PRIVATE, self.path.read_text(encoding="utf-8"))
                self._consumed()

    def test_chain_only_change_consumes_reservation_without_dispatch(self):
        replaced_chain = "e" * 64
        gate = Mock(side_effect=[APPROVAL, (SNAPSHOT, replaced_chain)])
        receipt = self._observe(gate)
        self.assertEqual(gate.call_count, 2)
        row = receipt["observation"]
        self.assertEqual((receipt["status"], row["error"], row["dispatch_attempts"]),
                         ("UNKNOWN", "gate_rejected", 0))
        self.fetch.assert_not_called()
        reservation, final = self.path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(json.loads(reservation)["approval_chain_sha256"], CHAIN)
        self.assertNotIn(replaced_chain, reservation)
        self.assertNotIn(CHAIN, final)
        self.assertNotIn(replaced_chain, final)
        self._consumed()

    def test_invalid_first_gate_does_not_reserve(self):
        for approval in (None, SNAPSHOT, [SNAPSHOT, CHAIN], (SNAPSHOT,), (SNAPSHOT, CHAIN, CHAIN),
                         (SNAPSHOT, CHAIN.upper()), (False, CHAIN), (SNAPSHOT, True)):
            with self.subTest(approval=approval), self.assertRaisesRegex(Invalid, "^probe_not_authorized$"):
                self._observe(lambda: approval)
            self.assertFalse(self.path.exists())

    def test_same_handle_must_still_be_attached_after_gate(self):
        attached = probe._attached
        revalidated = False

        def gate():
            nonlocal revalidated
            revalidated = self.path.exists()
            return APPROVAL

        def check(handle, path):
            attached(handle, path)
            if revalidated:
                raise Invalid("journal_identity_changed")

        with patch.object(probe, "_attached", check):
            with self.assertRaisesRegex(Invalid, "^probe_journal_unavailable$"):
                self._observe(gate)
        self.assertEqual(len(self.path.read_bytes().splitlines()), 1)
        self._consumed()

    def test_catalogue_and_transport_are_pinned(self):
        contents = {name: (probe.CHECKOUT / name).read_bytes() for name in probe._PINNED}
        root = self.path.parent
        for name, original in contents.items():
            path = root / name
            path.parent.mkdir()
            path.write_bytes(original)
        with patch.object(probe, "CHECKOUT", root):
            for name, original in contents.items():
                with self.subTest(name=name):
                    path = root / name
                    path.write_bytes(original + b"\n# Different bytes\n")
                    with self.assertRaisesRegex(Invalid, "^probe_not_authorized$"):
                        self._observe()
                    self.assertFalse(self.path.exists())
                    path.write_bytes(original)

    def test_terminal_receipt_rejects_bad_values(self):
        self._body(_feed(_item()))
        valid = self._observe()
        for field, value in (
                ("operation_id", PRIVATE), ("freshness_seconds", True), ("dispatch_attempts", True),
                ("captured_at", "2026-11-21T12:00:01Z"), ("error", PRIVATE),
                ("counts", {"total": 1, "title": PRIVATE})):
            with self.subTest(field=field), self.assertRaises(Invalid):
                receipt = copy.deepcopy(valid)
                receipt["observation"][field] = value
                probe.validate_receipt(receipt, SNAPSHOT, terminal=True)
        for extra in ({"status": ["FRESH"]}, {"response_url": RELEASES}, {"status": "NO_FRESH"},
                      {"approval_chain_sha256": CHAIN}):
            with self.subTest(extra=extra), self.assertRaises(Invalid):
                probe.validate_receipt(valid | extra, SNAPSHOT, terminal=True)

    def test_terminal_receipt_rejects_bad_partition(self):
        self._body(_feed(_item()))
        valid = self._observe()
        for field, value in (("fresh", False), ("total", 2), ("within_24h", 2), ("future", 1), ("extra", 0)):
            with self.subTest(field=field), self.assertRaises(Invalid):
                receipt = copy.deepcopy(valid)
                receipt["observation"]["counts"][field] = value
                probe.validate_receipt(receipt, SNAPSHOT, terminal=True)
