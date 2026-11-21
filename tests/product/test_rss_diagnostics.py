from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime, timedelta, timezone
from email.utils import format_datetime
from io import StringIO
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from market_intelligence import primary_news_probe as probe, rss_diagnostics as diagnostics, sources
from market_intelligence.models import Invalid


NOW = datetime(2000, 1, 1, 12, tzinfo=UTC)
RSS = "application/rss+xml"
PRIVATE = "unretained-diagnostic-input"
METADATA = {"schema_version": 1, "mode": "offline", "historical_cause": "unknown_unretained"}
ZERO = dict.fromkeys(
    ("fresh", "stale", "duplicate", "conflict", "future", "invalid", "out_of_scope",
     "attribution", "total", "within_24h"), 0
)


def _feed(content=""):
    return f'<rss version="2.0" xmlns:dc="urn:test"><channel>{content}</channel></rss>'.encode()


def _item(key="a", *, title=PRIVATE, published=NOW, link=None, extra=""):
    if link is None:
        link = f"https://{sources.FED_HOST}/newsevents/pressreleases/diagnostic19000101{key}.htm"
    date = format_datetime(published, usegmt=True) if isinstance(published, datetime) else published
    return (f"<item><title>{title}</title><link>{link}</link>"
            f"<pubDate>{date}</pubDate>{extra}</item>")


class RssDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        for owner, name in ((sources, "fetch"), (sources.Source, "authorize"),
                            (sources.socket, "create_connection"), (probe, "_observe"),
                            (probe, "_pinned_source"), (probe, "_path")):
            guard = self.enterContext(patch.object(owner, name, side_effect=AssertionError(PRIVATE)))
            self.addCleanup(guard.assert_not_called)

    def assert_result(self, response, reason, counts=None, *, captured=NOW):
        expected = METADATA | {"reason": reason}
        if counts is not None:
            expected["counts"] = counts
        result = diagnostics.diagnose_response(response, captured)
        self.assertEqual(result, expected)
        return result

    def test_only_exact_response_envelopes_reach_validation(self):
        class ResponseSubclass(sources.Response):
            pass

        for response in (None, _feed(), SimpleNamespace(body=_feed(), content_type=RSS, status=200),
                         ResponseSubclass(_feed(), RSS), sources.Response(bytearray(_feed()), RSS),
                         sources.Response(_feed(), [PRIVATE]), sources.Response(_feed(), RSS, True)):
            with self.subTest(response_type=type(response)), patch.object(sources.Response, "validate") as validate:
                self.assert_result(response, "invalid_envelope")
                validate.assert_not_called()

    def test_clock_is_explicit_and_fixed_offset(self):
        response = sources.Response(_feed(_item()), RSS)
        self.assert_result(response, "invalid_envelope", captured=NOW.replace(tzinfo=None))
        self.assert_result(response, "invalid_envelope", captured=PRIVATE)
        self.assert_result(response, "parsed", ZERO | {"total": 1, "fresh": 1, "within_24h": 1},
                           captured=NOW.astimezone(timezone(timedelta(hours=8))))

    def test_bounds_delegate_to_original_response_validation(self):
        body = _feed().ljust(sources.MAX_BYTES)
        good = sources.Response(body, RSS)
        bad = sources.Response(body + b" ", RSS)
        original = sources.Response.validate
        with (patch.object(sources.Response, "validate", autospec=True, side_effect=original) as validate,
              patch.object(probe, "_parse_counts", wraps=probe._parse_counts) as parse):
            self.assert_result(bad, "response_size")
            parse.assert_not_called()
            self.assert_result(good, "parsed", ZERO)
            parse.assert_called_once_with(body, NOW)
        self.assertEqual([call.args for call in validate.call_args_list], [(bad,), (good,)])

    def test_media_not_sniffed_or_normalized(self):
        for media in ("text/html", RSS + "; charset=UTF-8", PRIVATE):
            with self.subTest(media=media):
                self.assert_result(sources.Response(_feed(), media), "response_media")
        for media in (RSS, "application/xml", "text/xml"):
            with self.subTest(media=media):
                self.assert_result(sources.Response(_feed(), media), "parsed", ZERO)

    def test_non_200_http_never_parses_body(self):
        with patch.object(probe, "_parse_counts") as parse:
            for status in (99, 204, 302, 404, 503, 600, 10**5000):
                self.assert_result(sources.Response(_feed(), RSS, status), "response_http")
            parse.assert_not_called()

    def test_decoding_and_declared_encoding(self):
        for body in (b"\xff", b"<rss/>\0", b"<?xml version='1.0' encoding='UTF-16'?>" + _feed()):
            self.assert_result(sources.Response(body, RSS), "invalid_encoding")
        body = b"\xef\xbb\xbf<?xml version='1.0' encoding='UTF-8'?>" + _feed()
        self.assert_result(sources.Response(body, RSS), "parsed", ZERO)

    def test_dtd_and_entity_do_not_reach_xml_read(self):
        with patch.object(diagnostics.ET, "fromstring") as read:
            for prefix in (b"<!DOCTYPE rss>", b"<!ENTITY authored 'text'>"):
                self.assert_result(sources.Response(prefix + _feed(), RSS), "unsafe_xml")
            read.assert_not_called()

    def test_malformed_xml_is_distinct_from_rss_root_shape(self):
        for body in (b"", b"<rss><channel></rss>"):
            self.assert_result(sources.Response(body, RSS), "malformed_xml")
        for body in (b"<feed/>", b'<rss version="1.0"><channel/></rss>',
                     b"<rss><other/></rss>", b"<rss><channel/><channel/></rss>"):
            self.assert_result(sources.Response(body, RSS), "invalid_rss_root")

    def test_channel_node_and_item_limits(self):
        self.assert_result(sources.Response(_feed("<item/>" * 1000), RSS), "parsed",
                           ZERO | {"total": 1000, "invalid": 1000})
        self.assert_result(sources.Response(_feed("<item/>" * 1001), RSS), "channel_limits")
        self.assert_result(sources.Response(_feed("<node/>" * 4998), RSS), "parsed", ZERO)
        self.assert_result(sources.Response(_feed("<node/>" * 4999), RSS), "channel_limits")

    def test_channel_layout_rejects_hidden_or_namespaced_items(self):
        for content in ("<wrapper><item/></wrapper>", "<dc:item/>", "<Item/>", "<item><item/></item>"):
            self.assert_result(sources.Response(_feed(content), RSS), "channel_layout")

    def test_channel_identity_rejects_ambiguous_or_unapproved_link(self):
        link = f"<link>{sources.FED_URL}</link>"
        self.assert_result(sources.Response(_feed(link), RSS), "parsed", ZERO)
        for content in (link + link, "<link><name/></link>", f"<link>{PRIVATE}</link>"):
            self.assert_result(sources.Response(_feed(content), RSS), "channel_identity")

    def test_root_and_channel_attribution_use_original_policy(self):
        approved = _feed("<dc:publisher>Federal Reserve Board</dc:publisher>")
        self.assert_result(sources.Response(approved, RSS), "parsed", ZERO)
        for body in (_feed(f"<dc:publisher>{PRIVATE}</dc:publisher>"),
                     b'<rss publisher="third party"><channel/></rss>'):
            self.assert_result(sources.Response(body, RSS), "channel_attribution")

    def test_secondary_read_is_only_for_original_invalid_channel(self):
        with patch.object(diagnostics, "_channel_reason", wraps=diagnostics._channel_reason) as refine:
            for body, expected in ((_feed(), "parsed"), (b"<rss>", "malformed_xml"),
                                   (b"<feed/>", "invalid_rss_root"), (b"\xff", "invalid_encoding"),
                                   (b"<!DOCTYPE rss>" + _feed(), "unsafe_xml")):
                self.assert_result(sources.Response(body, RSS), expected, ZERO if expected == "parsed" else None)
            refine.assert_not_called()
            body = _feed(f"<link>{PRIVATE}</link>")
            self.assert_result(sources.Response(body, RSS), "channel_identity")
            refine.assert_called_once_with(body)

    def test_secondary_read_fails_closed_on_inconsistent_parser_failure(self):
        with patch.object(probe, "_parse_counts", side_effect=Invalid("invalid_channel")):
            with patch.object(diagnostics.ET, "fromstring") as read:
                for body in (b"<!DOCTYPE rss>" + _feed(), b"\xff",
                             b"<?xml encoding='UTF-16'?>" + _feed()):
                    self.assert_result(sources.Response(body, RSS), "opaque_failure")
                read.assert_not_called()
            for body in (_feed(), b"<rss>", b"<other/>"):
                self.assert_result(sources.Response(body, RSS), "opaque_failure")

    def test_age_boundaries_and_same_locator_duplicate_aggregate(self):
        today = _item()
        body = _feed(today + today
                     + _item("b", published=NOW - timedelta(days=1))
                     + _item("c", published=NOW - timedelta(days=31))
                     + _item("d", published=NOW - timedelta(days=31, seconds=1))
                     + _item("e", published=NOW + timedelta(seconds=1)))
        counts = ZERO | {"total": 6, "fresh": 3, "stale": 1, "future": 1, "duplicate": 1, "within_24h": 2}
        self.assert_result(sources.Response(body, RSS), "parsed", counts)

    def test_conflicts_and_admission_rejections_are_aggregate_only(self):
        body = _feed(_item() + _item(title="Different title")
                     + _item("b") + _item("b", published="bad date")
                     + _item(link=f"https://example.invalid/{PRIVATE}")
                     + _item("c", extra="<dc:creator>Third party</dc:creator>"))
        counts = ZERO | {"total": 6, "conflict": 3, "invalid": 1, "out_of_scope": 1, "attribution": 1}
        self.assert_result(sources.Response(body, RSS), "parsed", counts)

    def test_unexpected_errors_and_parser_results_are_opaque(self):
        response = sources.Response(_feed(), RSS)
        for error in (Invalid(PRIVATE), RuntimeError(PRIVATE), ValueError(PRIVATE)):
            with patch.object(probe, "_parse_counts", side_effect=error):
                self.assert_result(response, "opaque_failure")
        with patch.object(sources.Response, "validate", side_effect=RuntimeError(PRIVATE)):
            self.assert_result(response, "opaque_failure")
        for counts in (None, ZERO | {"fresh": PRIVATE}, ZERO | {PRIVATE: 0}, ZERO | {"total": True}):
            with patch.object(probe, "_parse_counts", return_value=counts):
                self.assert_result(response, "opaque_failure")

    def test_injected_content_and_exceptions_are_not_disclosed(self):
        response = sources.Response(_feed(_item()), RSS)
        stdout, stderr = StringIO(), StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            parsed = diagnostics.diagnose_response(response, NOW)
            with patch.object(probe, "_parse_counts", side_effect=RuntimeError(PRIVATE + ".xml")):
                fallback = diagnostics.diagnose_response(response, NOW)
        self.assertEqual((stdout.getvalue(), stderr.getvalue()), ("", ""))
        output = json.dumps([parsed, fallback])
        for token in (PRIVATE, "https://", format_datetime(NOW, usegmt=True), "<rss", ".xml", NOW.isoformat()):
            self.assertNotIn(token, output)

    def test_demo_is_authored_deterministic_and_offline(self):
        stdout, stderr = StringIO(), StringIO()
        with (redirect_stdout(stdout), redirect_stderr(stderr),
              patch.object(Path, "open", side_effect=AssertionError(PRIVATE)) as read):
            self.assertEqual(diagnostics.main(["demo"]), 0)
            read.assert_not_called()
        self.assertEqual(stderr.getvalue(), "")
        output = json.loads(stdout.getvalue())
        self.assertEqual(output, diagnostics.demo())
        self.assertEqual({key: output[key] for key in METADATA}, METADATA)
        self.assertEqual(output["fixture_origin"], "authored")
        by_case = {
            "aggregates": "parsed", "envelope": "invalid_envelope", "http": "response_http",
            "media": "response_media", "size": "response_size", "encoding": "invalid_encoding",
            "unallowed_xml": "unsafe_xml", "malformed": "malformed_xml", "root": "invalid_rss_root",
            "limits": "channel_limits", "layout": "channel_layout", "identity": "channel_identity",
            "attribution": "channel_attribution",
        }
        counts = ZERO | {"total": 8, "fresh": 1, "stale": 1, "within_24h": 1, "duplicate": 1,
                         "conflict": 2, "future": 1, "invalid": 1, "attribution": 1}
        self.assertEqual(output["cases"], {
            label: {"reason": reason} | ({"counts": counts} if reason == "parsed" else {})
            for label, reason in by_case.items()
        })
        for token in ("https://", "<rss", "diagnostic19000101", "Authored example", "01 Jan 2000"):
            self.assertNotIn(token, stdout.getvalue())

    def test_cli_does_not_echo_or_accept_extra_arguments(self):
        for argv in ([], [PRIVATE + ".xml"], [f"https://example.invalid/{PRIVATE}"],
                     ["demo", "--endpoint", PRIVATE], ["demo", "--input", PRIVATE]):
            stdout, stderr = StringIO(), StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr), patch.object(diagnostics, "demo") as demo:
                self.assertEqual(diagnostics.main(argv), 2)
                demo.assert_not_called()
            self.assertEqual((stdout.getvalue(), stderr.getvalue()), ("", "offline_demo_only\n"))
