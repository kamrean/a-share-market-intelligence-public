"""Authored in-memory RSS and temporary stores only; no real source observation."""

from contextlib import ExitStack, closing, contextmanager, redirect_stdout
import copy
from datetime import timedelta
from email.message import Message
import inspect
import io
import json
import os
from pathlib import Path
import socket
import sqlite3
import ssl
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from market_intelligence import nbs, intelligence, models, sources, gate
from market_intelligence.models import Invalid, digest, instant, stamp
from market_intelligence.source_policy import validate_source_policy
from market_intelligence.store import Store, Busy

SNAPSHOT = "a" * 64
NOW = instant("2020-01-02T12:00:00Z")
TITLE = "AUTHORED PRIVATE METADATA SENTINEL"
BODY = "AUTHORED RAW DESCRIPTION NEVER RETAIN"
NETWORK = None
AUTHORIZE = nbs._authorize
FETCH = nbs._fetch


def forbidden(*args, **kwargs):
    raise AssertionError("NBS tests prohibit real network")


def setUpModule():
    global NETWORK
    NETWORK = ExitStack()
    for name in ("socket.socket", "socket.create_connection", "socket.getaddrinfo", "socket.gethostbyname",
                 "socket.gethostbyname_ex", "socket.gethostbyaddr", "http.client.HTTPConnection.request",
                 "http.client.HTTPSConnection.request", "urllib.request.urlopen", "urllib.request.OpenerDirector.open"):
        NETWORK.enter_context(patch(name, side_effect=forbidden))


def tearDownModule():
    NETWORK.close()


def item(title=TITLE, author="National Bureau of Statistics", date="Thu, 02 Jan 2020 19:00:00 +0800",
         link=nbs.PREFIX + "202001/t20200102_123456.html", extra=""):
    attribution = "" if author is None else "<author>" + author + "</author>"
    return ("<item><title>" + title + "</title><link>" + link + "</link><pubDate>" + date
            + "</pubDate>" + attribution + "<description>" + BODY + "</description>" + extra + "</item>")


def feed(*items, extra=""):
    return ('<rss version="2.0"><channel>' + extra + "".join(items) + "</channel></rss>").encode()


def response(body=None):
    return sources.Response(feed(item()) if body is None else body, "application/rss+xml")


class NBSTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.path = self.home / ".a-share-market-intelligence" / "p4n-nbs-ingestion.sqlite"
        self.now = NOW
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(Path, "home", return_value=self.home))
        for module in (nbs, nbs.preparation, nbs.runtime, intelligence, models, sources, gate,
                       __import__("market_intelligence.store", fromlist=["utc_now"])):
            if hasattr(module, "utc_now"):
                self.stack.enter_context(patch.object(module, "utc_now", side_effect=lambda: self.now))
        self.auth = self.stack.enter_context(patch.object(nbs, "_authorize", return_value=SNAPSHOT))
        self.fetch = self.stack.enter_context(patch.object(nbs, "_fetch", return_value=response()))

    def collect(self, body=None):
        if body is not None:
            self.fetch.return_value = response(body)
        return nbs.collect(True)

    def sql(self, statement, parameters=()):
        with closing(sqlite3.connect(self.path)) as connection:
            with connection:
                return connection.execute(statement, parameters).fetchall()

    def capture(self, body):
        return models.Capture(nbs.SOURCE, nbs.ENDPOINT, stamp(self.now), stamp(self.now), None,
            digest(body), response(body).response_hash, "application/rss+xml", "rss-v1", "metadata_only",
            "p4n-normalized-only-project-24h-v1", stamp(self.now + timedelta(days=1)), nbs.MODE, "f" * 32)

    def parse(self, body):
        return nbs._parse(body, self.capture(body))

    def test_api_no_caller_overrides(self):
        self.assertEqual(str(inspect.signature(nbs.collect)), "(allow_network=False)")
        for function in (nbs.status, nbs.compose, nbs.sweep):
            self.assertEqual(str(inspect.signature(function)), "()")

    def test_collect_requires_literal_true_before_path_or_gate(self):
        with patch.object(nbs, "_path", side_effect=AssertionError("path read")):
            for flag in (False, None, 1, "true"):
                with self.subTest(flag=flag), self.assertRaises(Invalid):
                    nbs.collect(flag)
        self.auth.assert_not_called()
        self.fetch.assert_not_called()

    def test_gate_before_any_store_or_path(self):
        self.auth.side_effect = Invalid("nbs_gate_blocked")
        with patch.object(nbs, "_path", side_effect=AssertionError("path read")):
            for function in (lambda: nbs.collect(True), nbs.status, nbs.compose, nbs.sweep):
                with self.assertRaises(Invalid):
                    function()
        self.assertFalse(self.path.exists())

    def test_authorize_uses_exact_governance_gate(self):
        from tools import governance
        with patch.object(governance, "nbs_gate", return_value=SNAPSHOT, create=True) as actual:
            self.assertEqual(AUTHORIZE(), SNAPSHOT)
            actual.assert_called_once_with(nbs.ROOT)

    def test_recheck_changed_snapshot_before_reservation(self):
        self.auth.side_effect = [SNAPSHOT, "b" * 64]
        with self.assertRaises(Invalid):
            self.collect()
        self.fetch.assert_not_called()
        self.assertEqual(self.sql("SELECT count(*) FROM attempts")[0][0], 0)
        self.auth.side_effect = None
        with self.assertRaises(Invalid):
            self.collect()
        self.fetch.assert_not_called()

    def test_missing_read_commands_never_create_store(self):
        self.assertEqual(nbs.status()["status"], "NOT_RUN")
        self.assertIsNone(nbs.compose().private_bundle)
        self.assertEqual(nbs.sweep()["status"], "NOT_RUN")
        self.assertFalse(self.path.parent.exists())

    def test_collect_provenance_private_composition(self):
        record = self.collect()
        self.assertEqual(record["status"], "RECORDED")
        observation = record["observation"]
        self.assertEqual(observation["parse"], {"total": 1, "accepted": 1, "rejected": 0, "reasons": {}})
        self.assertEqual(observation["composition"]["status"], "COMPOSED")
        self.assertEqual(observation["benchmark_coverage"], nbs._coverage())
        self.assertEqual(observation["new_live_research_runs"], 0)
        self.assertNotIn(TITLE, json.dumps(record))
        self.assertNotIn(nbs.ENDPOINT, json.dumps(record))
        self.assertNotIn(str(self.home), json.dumps(record))
        composed = nbs.compose()
        self.assertIn(nbs.PREFIX, json.dumps(composed.private_bundle))
        inner = composed.private_bundle["bundle"]["content"]
        self.assertEqual(inner["provenance_scope"], "caller_supplied_only_not_acquisition_proof")
        self.assertEqual(inner["runtime_store_reads"], 0)
        self.assertEqual(inner["source_as_of"], observation["capture"]["retrieved_at"])
        self.assertEqual(inner["processing_mode"], "historical_replay")
        self.assertEqual(inner["readiness"], "BLOCKED_EXTERNAL_PREREQUISITES")
        self.assertTrue(all(value is False for value in inner["operations_enabled"].values()))

    def test_reservation_persists_before_fetch(self):
        def fake():
            self.assertEqual(self.sql("SELECT outcome FROM attempts"), [("reserved",)])
            self.assertEqual(self.sql("SELECT kind FROM events ORDER BY event_id"),
                             [("started",), ("p4n_intent",), ("reserved",)])
            return response()
        self.fetch.side_effect = fake
        self.collect()

    def test_never_persist_raw_or_description(self):
        self.collect()
        self.assertEqual(self.sql("SELECT payload IS NULL FROM captures"), [(1,)])
        self.assertNotIn(BODY.encode(), self.path.read_bytes())
        self.assertNotIn(b"<rss", self.path.read_bytes())
        for table, field in (("runs", "summary"), ("events", "detail"), ("bundles", "record"), ("captures", "metadata")):
            self.assertNotIn(TITLE, repr(self.sql("SELECT " + field + " FROM " + table)))

    def test_raw_replay_unavailable(self):
        self.collect()
        with Store(self.path) as store:
            with self.assertRaises(Invalid):
                store.retained_parse(self.sql("SELECT capture_id FROM captures")[0][0])

    def test_repeat_never_resends(self):
        first = self.collect()
        second = self.collect()
        self.assertEqual(first, second)
        self.fetch.assert_called_once()
        self.assertEqual(len(self.sql("SELECT * FROM attempts")), 1)

    def test_read_commands_are_byte_unchanged(self):
        self.collect()
        before = self.path.read_bytes(), self.path.stat().st_mtime_ns
        nbs.status()
        nbs.compose()
        self.assertEqual(before, (self.path.read_bytes(), self.path.stat().st_mtime_ns))
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_all_100_rejected_preserves_denominator(self):
        record = self.collect(feed(*(item(author=None) for _ in range(100))))
        self.assertEqual(record["status"], "FAILED")
        self.assertEqual(record["observation"]["parse"], {
            "total": 100, "accepted": 0, "rejected": 100, "reasons": {"unknown_attribution": 100}})
        self.assertEqual(record["observation"]["composition"]["reason"], "no_eligible_metadata")
        self.assertEqual(self.sql("SELECT count(*) FROM evidence"), [(0,)])
        self.assertNotIn(TITLE.encode(), self.path.read_bytes())
        self.collect()
        self.fetch.assert_called_once()

    def test_zero_items_is_failed_not_empty_event_day(self):
        record = self.collect(feed())
        self.assertEqual(record["status"], "FAILED")
        self.assertEqual(record["observation"]["parse"]["total"], 0)
        self.assertIsNone(nbs.compose().private_bundle)

    def test_original_and_composition_rejections_separate(self):
        record = self.collect(feed(item(date="Mon, 30 Dec 2019 10:00:00 +0000"), item(author=None)))
        o = record["observation"]
        self.assertEqual(o["parse"], {"total": 2, "accepted": 1, "rejected": 1, "reasons": {"unknown_attribution": 1}})
        self.assertEqual(o["composition"]["status"], "COMPOSED")
        self.assertEqual(o["composition"]["reason_counts"], {"stale_news": 1})

    def test_failed_transport_consumes_single_allowance(self):
        self.fetch.side_effect = nbs._Transport("http_status", "failed", 0)
        record = self.collect()
        self.assertEqual(record["status"], "FAILED")
        self.assertIsNone(record["observation"]["parse"])
        self.assertEqual(self.collect(), record)
        self.fetch.assert_called_once()

    def test_unknown_transport_usage_stays_unknown(self):
        self.fetch.side_effect = OSError(TITLE + str(self.home))
        record = self.collect()
        self.assertEqual(record["status"], "UNKNOWN")
        self.assertIsNone(record["observation"]["observed_bytes"])
        self.assertNotIn(TITLE, json.dumps(record))
        self.collect()
        self.fetch.assert_called_once()

    def test_keyboard_interrupt_consumes_allowance(self):
        self.fetch.side_effect = KeyboardInterrupt()
        record = self.collect()
        self.assertEqual(record["status"], "UNKNOWN")
        self.assertEqual(record["observation"]["reason"], "collection_interrupted")
        self.collect()
        self.fetch.assert_called_once()

    def test_failure_after_settle_preserves_unknown_completion(self):
        with patch.object(nbs, "_parse", side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                self.collect()
        record = self.collect()
        self.assertEqual(record["status"], "UNKNOWN")
        self.assertEqual(record["observation"]["request_outcome"], "success")
        self.assertEqual(record["observation"]["reason"], "prior_attempt_incomplete")
        self.fetch.assert_called_once()

    def test_parse_failure_unknown_denominator_not_zero_claim(self):
        record = self.collect(b"not XML")
        self.assertEqual(record["status"], "FAILED")
        self.assertEqual(record["observation"]["reason"], "parse_failed")
        self.assertIsNone(record["observation"]["parse"])
        self.assertIsNotNone(record["observation"]["capture"])

    def test_oversized_item_count_fails_whole_feed(self):
        record = self.collect(feed(*(item() for _ in range(101))))
        self.assertEqual(record["observation"]["reason"], "parse_failed")
        self.assertIsNone(record["observation"]["parse"])
        self.assertEqual(self.sql("SELECT count(*) FROM evidence"), [(0,)])

    def test_two_processes_no_second_fetch(self):
        def fake():
            with self.assertRaises(Busy):
                nbs.collect(True)
            return response()
        self.fetch.side_effect = fake
        self.collect()
        self.fetch.assert_called_once()

    def test_expired_payload_not_read(self):
        self.collect()
        self.now += timedelta(days=1)
        with patch.object(nbs._ReadOnlyStore, "records", side_effect=AssertionError("expired read")):
            result = nbs.compose()
        self.assertEqual(result.record["observation"]["composition"]["reason"], "expired_input")
        self.assertIsNone(result.private_bundle)
        self.assertEqual(nbs.status()["status"], "RECORDED")

    def test_sweep_removes_every_title_copy_preserves_quota(self):
        self.collect()
        self.now += timedelta(days=1)
        self.assertIn(TITLE.encode(), self.path.read_bytes())
        nbs.sweep()
        self.assertNotIn(TITLE.encode(), self.path.read_bytes())
        self.assertEqual(self.sql("SELECT count(*) FROM evidence"), [(0,)])
        self.assertEqual(self.sql("SELECT count(*) FROM tombstones"), [(1,)])
        self.assertEqual(self.sql("SELECT count(*) FROM attempts"), [(1,)])
        self.assertEqual(self.collect()["observation"]["composition"]["reason"], "tombstoned_input")
        self.fetch.assert_called_once()

    def test_sweep_before_expiry_no_mutation(self):
        self.collect()
        before = self.path.read_bytes()
        nbs.sweep()
        self.assertEqual(before, self.path.read_bytes())

    def test_clock_rollback_during_composition(self):
        self.collect()
        self.now += timedelta(hours=1)
        original = intelligence.build_replay_bundle
        def rollback(*args):
            result = original(*args)
            self.now -= timedelta(seconds=1)
            return result
        with patch.object(intelligence, "build_replay_bundle", side_effect=rollback):
            result = nbs.compose()
        self.assertIsNone(result.private_bundle)
        self.assertEqual(result.record["observation"]["composition"]["reason"], "clock_rollback")

    def test_expiry_after_costly_composition(self):
        self.collect()
        original = intelligence.build_replay_bundle
        def expire(*args):
            result = original(*args)
            self.now += timedelta(days=1)
            return result
        with patch.object(intelligence, "build_replay_bundle", side_effect=expire):
            result = nbs.compose()
        self.assertIsNone(result.private_bundle)
        self.assertEqual(result.record["observation"]["composition"]["reason"], "expired_input")

    def test_tampered_normalized_record_rejected(self):
        self.collect()
        self.sql("UPDATE evidence SET record=replace(record,?,?)", (TITLE, "TAMPERED"))
        self.assertIsNone(nbs.compose().private_bundle)

    def test_deleted_evidence_rejected(self):
        self.collect()
        self.sql("DELETE FROM evidence")
        with self.assertRaises(Invalid):
            nbs.compose()

    def test_tampered_manifest_rejected(self):
        self.collect()
        self.sql("UPDATE events SET detail='{}' WHERE kind='p4n_manifest'")
        with self.assertRaises(Invalid):
            nbs.status()

    def test_tampered_summary_rejected(self):
        self.collect()
        self.sql("UPDATE runs SET summary=replace(summary,'metadata_recorded','parse_failed')")
        with self.assertRaises(Invalid):
            nbs.status()

    def test_foreign_run_rejected(self):
        self.collect()
        self.sql("UPDATE runs SET task_key='foreign'")
        with self.assertRaises(Invalid):
            nbs.status()

    def test_foreign_snapshot_rejected(self):
        self.collect()
        self.auth.return_value = "b" * 64
        with self.assertRaises(Invalid):
            self.collect()
        self.fetch.assert_called_once()

    def test_foreign_schema_not_modified(self):
        self.path.parent.mkdir()
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("CREATE TABLE foreign_data(value)")
        before = self.path.read_bytes()
        with self.assertRaises(Invalid):
            nbs.status()
        self.assertEqual(before, self.path.read_bytes())

    def test_empty_store_not_reinitialized(self):
        self.path.parent.mkdir()
        self.path.touch()
        with self.assertRaises(Invalid):
            self.collect()
        self.assertEqual(self.path.stat().st_size, 0)
        self.fetch.assert_not_called()

    def test_wal_rejected_without_side_effect(self):
        self.collect()
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
        before = {p.name: p.read_bytes() for p in self.path.parent.iterdir()}
        with self.assertRaises(Invalid):
            nbs.status()
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.path.parent.iterdir()})

    def test_hard_link_rejected(self):
        self.collect()
        try:
            os.link(self.path, self.home / "linked.sqlite")
        except OSError:
            self.skipTest("hard links unavailable on this platform")
        with self.assertRaises(Invalid):
            nbs.status()

    def test_private_bundle_rejected_by_legacy_engine(self):
        self.collect()
        bundle = nbs.compose().private_bundle["bundle"]
        with self.assertRaises(Invalid):
            intelligence.verify_bundle(bundle, {}, {}, {})

    def test_cli_only_sanitized_receipt(self):
        self.collect()
        for args in (["status"], ["compose"], ["collect", "--allow-network"], ["sweep"]):
            with self.subTest(args=args), redirect_stdout(io.StringIO()) as output:
                nbs.main(args)
            text = output.getvalue()
            record = json.loads(text)
            nbs.validate_observation_record(record, SNAPSHOT)
            for secret in (TITLE, BODY, str(self.home), "https://"):
                self.assertNotIn(secret, text)

    def test_cli_rejects_overrides_and_sanitizes_exceptions(self):
        for args in (["collect", "--url", nbs.ENDPOINT], ["status", "--allow-network"], ["compose", "--store", str(self.path)]):
            with redirect_stdout(io.StringIO()) as output:
                self.assertEqual(nbs.main(args), 2)
            self.assertEqual(json.loads(output.getvalue()), {"status": "ERROR", "reason": "invalid_cli_arguments"})
        with patch.object(nbs, "status", side_effect=RuntimeError(TITLE)), redirect_stdout(io.StringIO()) as output:
            nbs.main(["status"])
        self.assertNotIn(TITLE, output.getvalue())

    def test_validator_no_store_or_file_or_network(self):
        record = self.collect()
        with ExitStack() as stack:
            for name in ("builtins.open", "io.open", "os.open", "os.getenv", "os._Environ.__getitem__", "sqlite3.connect"):
                stack.enter_context(patch(name, side_effect=AssertionError("validator I/O")))
            self.assertEqual(nbs.validate_observation_record(record, SNAPSHOT), record)
            self.assertEqual(nbs.not_run_record(SNAPSHOT)["status"], "NOT_RUN")

    def test_validator_closed_shape_types_and_bindings(self):
        record = self.collect()
        mutations = [lambda r: r.update(title=TITLE), lambda r: r.update(schema_version=True),
            lambda r: r.update(snapshot_id="b" * 64), lambda r: r.update(status="NOT_RUN"),
            lambda r: r["observation"].update(new_live_research_runs=1),
            lambda r: r["observation"].update(summary_hash=None, source_gate_id=None),
            lambda r: r["observation"]["parse"].update(total=2),
            lambda r: r["observation"]["records"][0].update(item_id="not hash"),
            lambda r: r["observation"]["composition"].update(rejected_count=1),
            lambda r: r["observation"].update(manifest_hash="f" * 64),
            lambda r: r["observation"].update(reason=TITLE)]
        for mutate in mutations:
            bad = copy.deepcopy(record)
            mutate(bad)
            with self.subTest(bad=bad), self.assertRaises(Invalid):
                nbs.validate_observation_record(bad, SNAPSHOT)

    def test_validator_rejects_future_not_historical_expired_receipts(self):
        record = self.collect()
        self.now -= timedelta(seconds=1)
        with self.assertRaises(Invalid):
            nbs.validate_observation_record(record, SNAPSHOT)
        self.now += timedelta(days=2)
        self.assertEqual(nbs.validate_observation_record(record, SNAPSHOT), record)

    def test_not_run_is_not_terminal(self):
        record = nbs.not_run_record(SNAPSHOT)
        for status in ("RECORDED", "FAILED", "UNKNOWN"):
            with self.assertRaises(Invalid):
                nbs.validate_observation_record(dict(record, status=status), SNAPSHOT)

    def test_policy_normalized_only_denies_raw(self):
        policy = nbs._policy()
        self.assertEqual(policy["sources"][0]["retention"]["class"], "normalized_only")
        for state in ("unknown", "permitted"):
            bad = copy.deepcopy(policy)
            bad["sources"][0]["rights"]["raw_retention"]["state"] = state
            with self.assertRaises(Invalid):
                validate_source_policy(bad)

    def test_policy_positive_bounded_duration(self):
        for age in (0, -1, True, 86401):
            bad = nbs._policy()
            bad["sources"][0]["retention"]["max_age_seconds"] = age
            with self.assertRaises(Invalid):
                validate_source_policy(bad)

    def test_policy_old_restricted_semantics_unchanged(self):
        for retention in ("metadata_only", "restricted"):
            bad = nbs._policy()
            bad["sources"][0]["retention"].update({"class": retention, "max_age_seconds": 0})
            with self.assertRaises(Invalid):
                validate_source_policy(bad)

    def test_parse_explicit_own_attribution_alternatives(self):
        for tag in ("author", "publisher", "source"):
            body = feed(item(author=None, extra="<" + tag + ">National Bureau of Statistics</" + tag + ">"))
            self.assertEqual(self.parse(body).counts["accepted"], 1)

    def test_parse_attribution_is_exact_not_host_inference(self):
        for author in (None, "NBS", "National Bureau of Statistics via Foreign Publisher", "Foreign Publisher"):
            self.assertEqual(self.parse(feed(item(author=author))).counts["rejected"], 1)

    def test_parse_conflicting_item_attribution(self):
        for extra in ("<publisher>Foreign Publisher</publisher>", "<author>Foreign Publisher</author>"):
            self.assertEqual(self.parse(feed(item(extra=extra))).rejections, {"conflicting_attribution": 1})

    def test_parse_channel_attribution_not_item_proof(self):
        self.assertEqual(self.parse(feed(item(author=None), extra="<author>National Bureau of Statistics</author>")).counts["accepted"], 0)

    def test_parse_unknown_rights_rejected_without_title(self):
        for rights in ("All rights reserved", "unknown", "", "Third-party copyright"):
            parsed = self.parse(feed(item(extra="<rights>" + rights + "</rights>")))
            self.assertEqual(parsed.rejections, {"unknown_rights": 1})
            self.assertNotIn(TITLE, repr(parsed))

    def test_parse_known_terms_reference(self):
        self.assertEqual(self.parse(feed(item(extra="<rights>" + nbs.TERMS + "</rights>"))).counts["accepted"], 1)

    def test_parse_channel_unknown_rights_rejected(self):
        self.assertEqual(self.parse(feed(item(), extra="<copyright>unknown</copyright>")).rejections, {"unknown_rights": 1})

    def test_parse_missing_or_unknown_timezone_not_guessed(self):
        for date in ("Thu, 02 Jan 2020 11:00:00", "2020-01-02", "Thu, 02 Jan 2020 11:00:00 -0000", "Thu, 02 Jan 2020 11:00:00 CST"):
            self.assertEqual(self.parse(feed(item(date=date))).rejections, {"unknown_publication_timezone": 1})

    def test_parse_future_or_invalid_time(self):
        for date in ("Thu, 02 Jan 2030 11:00:00 +0000", "bad +0000", "Thu, 99 Jan 2020 11:00:00 +0000"):
            self.assertEqual(self.parse(feed(item(date=date))).rejections, {"invalid_publication_time": 1})

    def test_parse_rejects_unsafe_link_variants(self):
        for link in (nbs.ENDPOINT, nbs.PREFIX, nbs.PREFIX + "../x.html", nbs.PREFIX + "%2e/x.html",
                     nbs.PREFIX + "x.html?key=a", nbs.PREFIX + "x.html#x", "http://www.stats.gov.cn/sj/zxfb/x.html",
                     "https://www.stats.gov.cn.evil.invalid/sj/zxfb/x.html", "https://user@www.stats.gov.cn/sj/zxfb/x.html"):
            self.assertEqual(self.parse(feed(item(link=link))).rejections, {"invalid_locator": 1})

    def test_parse_rejects_title_markup_and_controls(self):
        for title in ("&lt;b&gt;private&lt;/b&gt;", "line&#10;break", "", "x" * 2001):
            self.assertEqual(self.parse(feed(item(title=title))).counts["accepted"], 0)

    def test_parse_duplicate_fields_and_items(self):
        self.assertEqual(self.parse(feed(item(extra="<title>second</title>"))).rejections, {"invalid_fields": 1})
        self.assertEqual(self.parse(feed(item(), item())).counts, {"total": 2, "accepted": 1, "rejected": 1, "reasons": {"duplicate_item": 1}})

    def test_parse_xml_entity_depth_encoding_bounds(self):
        for body in (b'<!DOCTYPE rss [<!ENTITY x "value">]>' + feed(item()),
                     b'<?xml version="1.0" encoding="utf-16"?>' + feed(), b"\x00" + feed(),
                     feed(extra="<x>" * 13 + "</x>" * 13), feed(extra="<x/>" * 5001), b"x" * 512001):
            with self.assertRaises(Invalid):
                self.parse(body)

    def test_interruption_before_capture_never_refetches(self):
        with patch.object(nbs._MetadataStore, "put_capture", side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                self.collect()
        self.assertEqual(self.collect()["status"], "UNKNOWN")
        self.fetch.assert_called_once()

    def test_interruption_after_capture_never_refetches(self):
        with patch.object(nbs, "_make_manifest", side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                self.collect()
        self.assertEqual(self.collect()["status"], "UNKNOWN")
        self.fetch.assert_called_once()
        self.now += timedelta(days=1)
        nbs.sweep()
        self.assertNotIn(TITLE.encode(), self.path.read_bytes())

    def test_interruption_after_manifest_never_refetches(self):
        with patch.object(nbs, "_finish", side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                self.collect()
        record = self.collect()
        self.assertEqual(record["status"], "UNKNOWN")
        self.assertEqual(record["observation"]["parse"]["accepted"], 1)
        self.fetch.assert_called_once()

    def test_interruption_after_freeze_never_refetches(self):
        with patch.object(nbs._MetadataStore, "finish", side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                self.collect()
        self.assertEqual(self.collect()["status"], "UNKNOWN")
        self.fetch.assert_called_once()

    def test_interruption_after_commit_uses_original_result(self):
        original = nbs._MetadataStore.finish
        def interrupt(store, *args, **kwargs):
            original(store, *args, **kwargs)
            raise KeyboardInterrupt()
        with patch.object(nbs._MetadataStore, "finish", side_effect=interrupt, autospec=True):
            with self.assertRaises(KeyboardInterrupt):
                self.collect()
        self.assertEqual(self.collect()["status"], "RECORDED")
        self.fetch.assert_called_once()

    def test_expiry_after_verifier_no_private_return(self):
        self.collect()
        original = intelligence.verify_replay_bundle
        def expire(*args):
            result = original(*args)
            self.now += timedelta(days=1)
            return result
        with patch.object(intelligence, "verify_replay_bundle", side_effect=expire):
            result = nbs.compose()
        self.assertIsNone(result.private_bundle)
        self.assertEqual(result.record["observation"]["composition"]["reason"], "expired_input")

    def test_expiry_after_record_read_no_private_return(self):
        self.collect()
        original = nbs._ReadOnlyStore.records
        def expire(store, *args, **kwargs):
            result = original(store, *args, **kwargs)
            self.now += timedelta(days=1)
            return result
        with patch.object(nbs._ReadOnlyStore, "records", side_effect=expire, autospec=True):
            result = nbs.compose()
        self.assertIsNone(result.private_bundle)
        self.assertEqual(result.record["observation"]["composition"]["reason"], "expired_input")

    def test_clock_rollback_before_read_rejected(self):
        self.collect()
        self.now -= timedelta(seconds=1)
        with self.assertRaises(Invalid):
            nbs.compose()

    def test_expiry_after_store_close_no_private_return(self):
        self.collect()
        original = nbs._ReadOnlyStore.close
        def expire(store):
            original(store)
            self.now += timedelta(days=1)
        with patch.object(nbs._ReadOnlyStore, "close", side_effect=expire, autospec=True):
            result = nbs.compose()
        self.assertIsNone(result.private_bundle)
        composition = result.record["observation"]["composition"]
        self.assertEqual(composition["status"], "UNAVAILABLE")
        self.assertEqual(composition["reason"], "expired_input")
        self.assertIsNone(composition["bundle_id"])
        self.assertIsNone(composition["fingerprint"])
        self.assertNotIn(TITLE, json.dumps(result.record))
        nbs.validate_observation_record(result.record, SNAPSHOT)

    def test_clock_rollback_after_store_close_no_private_return(self):
        self.collect()
        self.now += timedelta(hours=1)
        original = nbs._ReadOnlyStore.close
        def rollback(store):
            original(store)
            self.now -= timedelta(seconds=1)
        with patch.object(nbs._ReadOnlyStore, "close", side_effect=rollback, autospec=True):
            result = nbs.compose()
        self.assertIsNone(result.private_bundle)
        composition = result.record["observation"]["composition"]
        self.assertEqual(composition["status"], "UNAVAILABLE")
        self.assertEqual(composition["reason"], "clock_rollback")
        self.assertIsNone(composition["bundle_id"])
        self.assertIsNone(composition["fingerprint"])
        self.assertNotIn(TITLE, json.dumps(result.record))
        nbs.validate_observation_record(result.record, SNAPSHOT)

    def test_attempt_tampering_rejected(self):
        self.collect()
        self.sql("UPDATE attempts SET observed_bytes=0")
        with self.assertRaises(Invalid):
            nbs.status()

    def test_extra_event_rejected(self):
        self.collect()
        self.sql("INSERT INTO events(run_id,at,kind,detail) SELECT run_id,started_at,'foreign','{}' FROM runs")
        with self.assertRaises(Invalid):
            nbs.status()

    def test_no_raw_payload_injected_into_metadata_store(self):
        self.collect()
        self.sql("UPDATE captures SET payload=?", (b"raw xml",))
        with self.assertRaises(Invalid):
            nbs.status()

    def test_parse_nested_or_foreign_namespace_attribution_rejected(self):
        for extra in ('<x:author xmlns:x="urn:unknown">foreign</x:author>',
                      '<other><author>foreign</author></other>', '<other><rights>unknown</rights></other>'):
            self.assertEqual(self.parse(feed(item(extra=extra))).counts["accepted"], 0)


    def test_channel_namespaced_rights_fail_before_evidence(self):
        for namespace in ("http://purl.org/dc/terms/", "urn:authored-unknown-rights"):
            for tag in ("rights", "copyright", "license", "Rights"):
                for value in ("AUTHORED RESTRICTION", nbs.TERMS, ""):
                    extra = '<r:' + tag + ' xmlns:r="' + namespace + '">' + value + '</r:' + tag + '>'
                    with self.subTest(namespace=namespace, tag=tag, value=value):
                        with patch.object(nbs, "evidence_for", side_effect=AssertionError("restricted evidence constructed")):
                            parsed = self.parse(feed(item(), extra=extra))
                        self.assertEqual(parsed.counts, {"total": 1, "accepted": 0, "rejected": 1,
                                                        "reasons": {"unknown_rights": 1}})
                        self.assertNotIn(TITLE, repr(parsed))

    def test_channel_nested_rights_fail_before_evidence(self):
        for tag in ("rights", "copyright", "license", "dc:rights"):
            for value in ("AUTHORED RESTRICTION", nbs.TERMS):
                field = '<' + tag + ' xmlns:dc="http://purl.org/dc/elements/1.1/">' + value + '</' + tag + '>'
                for depth in (1, 3):
                    extra = "<metadata>" * depth + field + "</metadata>" * depth
                    with self.subTest(tag=tag, value=value, depth=depth):
                        with patch.object(nbs, "evidence_for", side_effect=AssertionError("restricted evidence constructed")):
                            parsed = self.parse(feed(item(), extra=extra))
                        self.assertEqual(parsed.counts, {"total": 1, "accepted": 0, "rejected": 1,
                                                        "reasons": {"unknown_rights": 1}})

    def test_channel_known_direct_terms_remain_accepted(self):
        for tag in ("rights", "copyright", "license", "dc:rights"):
            extra = '<' + tag + ' xmlns:dc="http://purl.org/dc/elements/1.1/">' + nbs.TERMS + '</' + tag + '>'
            with self.subTest(tag=tag):
                parsed = self.parse(feed(item(), extra=extra))
                self.assertEqual(parsed.counts, {"total": 1, "accepted": 1, "rejected": 0, "reasons": {}})
                self.assertEqual(parsed.records[0].title, TITLE)

    def test_channel_unknown_rights_override_known_terms(self):
        known = "<rights>" + nbs.TERMS + "</rights>"
        ambiguous = (
            '<dcterms:rights xmlns:dcterms="http://purl.org/dc/terms/">unknown</dcterms:rights>',
            '<metadata><copyright>unknown</copyright></metadata>',
            '<metadata xmlns="urn:authored-rights"><license>unknown</license></metadata>',
        )
        for restriction in ambiguous:
            for extra in (known + restriction, restriction + known):
                with self.subTest(extra=extra):
                    parsed = self.parse(feed(item(), extra=extra))
                    self.assertEqual(parsed.rejections, {"unknown_rights": 1})
                    self.assertEqual(parsed.records, ())

    def test_channel_rights_all_100_items_rejected_without_retention(self):
        titles = [TITLE + " RESTRICTED " + str(index) for index in range(100)]
        extra = '<dcterms:rights xmlns:dcterms="http://purl.org/dc/terms/">AUTHORED RESTRICTION</dcterms:rights>'
        with patch.object(nbs, "evidence_for", side_effect=AssertionError("restricted evidence constructed")):
            record = self.collect(feed(*(item(title=title) for title in titles), extra=extra))
        self.assertEqual(record["status"], "FAILED")
        self.assertEqual(record["observation"]["parse"], {
            "total": 100, "accepted": 0, "rejected": 100, "reasons": {"unknown_rights": 100}})
        self.assertEqual(self.sql("SELECT count(*) FROM evidence"), [(0,)])
        stored = self.path.read_bytes()
        for title in titles:
            self.assertNotIn(title.encode(), stored)
            self.assertNotIn(title, json.dumps(record))
        self.assertNotIn(b"AUTHORED RESTRICTION", stored)
        self.fetch.assert_called_once()

    def test_channel_nested_rights_all_items_rejected_without_retention(self):
        titles = [TITLE + " NESTED " + str(index) for index in range(3)]
        extra = "<metadata><licensing><license>AUTHORED RESTRICTION</license></licensing></metadata>"
        with patch.object(nbs, "evidence_for", side_effect=AssertionError("restricted evidence constructed")):
            record = self.collect(feed(*(item(title=title) for title in titles), extra=extra))
        self.assertEqual(record["observation"]["parse"], {
            "total": 3, "accepted": 0, "rejected": 3, "reasons": {"unknown_rights": 3}})
        self.assertEqual(record["observation"]["composition"]["reason"], "no_eligible_metadata")
        self.assertEqual(self.sql("SELECT count(*) FROM evidence"), [(0,)])
        stored = self.path.read_bytes()
        for title in titles:
            self.assertNotIn(title.encode(), stored)
            self.assertNotIn(title, json.dumps(record))
        self.assertNotIn(b"AUTHORED RESTRICTION", stored)

    def test_item_rights_do_not_become_global_channel_restrictions(self):
        rejected_titles = [TITLE + " ITEM RESTRICTION " + str(index) for index in range(3)]
        restrictions = (
            "<rights>unknown</rights>",
            '<dcterms:rights xmlns:dcterms="http://purl.org/dc/terms/">unknown</dcterms:rights>',
            "<metadata><license>unknown</license></metadata>",
        )
        body = feed(item(), *(item(title=title, extra=restriction)
                              for title, restriction in zip(rejected_titles, restrictions)),
                    extra="<rights>" + nbs.TERMS + "</rights>")
        record = self.collect(body)
        self.assertEqual(record["status"], "RECORDED")
        self.assertEqual(record["observation"]["parse"], {
            "total": 4, "accepted": 1, "rejected": 3,
            "reasons": {"unknown_rights": 1, "unknown_attribution": 2}})
        self.assertEqual(record["observation"]["composition"]["input_count"], 1)
        rows = self.sql("SELECT record FROM evidence")
        self.assertEqual(len(rows), 1)
        self.assertEqual(json.loads(rows[0][0])["title"], TITLE)
        stored = self.path.read_bytes()
        for title in rejected_titles:
            self.assertNotIn(title.encode(), stored)
            self.assertNotIn(title, json.dumps(record))

    def test_channel_ordinary_nested_metadata_preserves_valid_items(self):
        extra = "<image><title>Authored channel image</title><url>https://example.invalid/image.png</url></image>"
        parsed = self.parse(feed(item(), item(title="SECOND AUTHORED ITEM", link=nbs.PREFIX + "second.html"), extra=extra))
        self.assertEqual(parsed.counts, {"total": 2, "accepted": 2, "rejected": 0, "reasons": {}})
        self.assertEqual([row.title for row in parsed.records], [TITLE, "SECOND AUTHORED ITEM"])


class FakeHTTPResponse:
    def __init__(self, body=b"<rss/>", status=200, headers=None, chunks=None):
        self.body, self.status = body, status
        self.headers = Message()
        for key, value in (headers if headers is not None else [("Content-Type", "application/rss+xml")]):
            self.headers[key] = value
        self.chunks = list(chunks) if chunks is not None else None

    def getheader(self, key, default=None):
        return self.headers.get(key, default)

    def read1(self, size):
        if self.chunks is not None:
            return self.chunks.pop(0) if self.chunks else b""
        result, self.body = self.body[:size], self.body[size:]
        return result


class TransportTests(unittest.TestCase):
    @contextmanager
    def exchange(self, response=None, addresses=None):
        address = (2, 1, 6, "", ("8.8.8.8", 443))
        with patch.object(nbs.socket, "getaddrinfo", return_value=[address] if addresses is None else addresses) as dns:
            with patch.object(nbs, "_TLS") as tls:
                tls.return_value.getresponse.return_value = response or FakeHTTPResponse()
                yield tls.return_value, dns, tls

    def test_fixed_request_ignores_proxy_and_account_environment(self):
        with self.exchange() as (connection, dns, tls), ExitStack() as stack:
            for name in ("os.getenv", "os._Environ.__getitem__", "netrc.netrc"):
                stack.enter_context(patch(name, side_effect=AssertionError("credential/environment read")))
            result = nbs._exchange(nbs._Deadline())
        self.assertEqual(result.status, 200)
        dns.assert_called_once_with(nbs.HOST, 443, type=socket.SOCK_STREAM)
        args, kwargs = connection.request.call_args
        self.assertEqual(args, ("GET", "/sj/zxfb/rss.xml"))
        self.assertEqual(set(kwargs["headers"]), {"User-Agent", "Accept", "Accept-Encoding", "Connection"})
        self.assertEqual(kwargs["headers"]["Accept-Encoding"], "identity")
        connection.request.assert_called_once()
        connection.close.assert_called_once()

    def test_redirect_refused_never_followed(self):
        with self.exchange(FakeHTTPResponse(status=302)) as (connection, _, _):
            with self.assertRaisesRegex(nbs._Transport, "http_status"):
                nbs._exchange(nbs._Deadline())
            connection.request.assert_called_once()

    def test_private_and_mixed_dns_refused(self):
        for addresses in ([], [(2, 1, 6, "", ("127.0.0.1", 443))],
                          [(2, 1, 6, "", ("8.8.8.8", 443)), (2, 1, 6, "", ("10.0.0.1", 443))]):
            with self.exchange(addresses=addresses) as (_, _, tls):
                with self.assertRaises(sources.TransportError):
                    nbs._exchange(nbs._Deadline())
                tls.assert_not_called()

    def test_ambiguous_or_oversized_headers_refused(self):
        cases = [("Content-Type", "text/html"), ("Content-Encoding", "gzip"),
                 ("Content-Length", "512001"), ("Content-Length", "-1"),
                 ("Content-Length", "1" * 9000), ("Transfer-Encoding", "gzip")]
        for header in cases:
            headers = [header] if header[0] == "Content-Type" else [("Content-Type", "application/rss+xml"), header]
            with self.subTest(header=header[0]), self.exchange(FakeHTTPResponse(headers=headers)):
                with self.assertRaises(nbs._Transport):
                    nbs._exchange(nbs._Deadline())

    def test_duplicate_length_and_transfer_conflict_refused(self):
        for tail in ([('Content-Length', '1'), ('Content-Length', '1')],
                     [('Content-Length', '1'), ('Transfer-Encoding', 'chunked')]):
            with self.exchange(FakeHTTPResponse(headers=[("Content-Type", "text/xml"), *tail])):
                with self.assertRaises(nbs._Transport):
                    nbs._exchange(nbs._Deadline())

    def test_body_limit_and_unknown_usage(self):
        with self.exchange(FakeHTTPResponse(body=b"x" * 512001)):
            with self.assertRaises(nbs._Transport) as result:
                nbs._exchange(nbs._Deadline())
            self.assertEqual(str(result.exception), "response_size")
            self.assertIsNone(result.exception.observed_bytes)

    def test_truncated_body_unknown_not_zero(self):
        with self.exchange(FakeHTTPResponse(body=b"x", headers=[("Content-Type", "text/xml"), ("Content-Length", "20")])):
            with self.assertRaises(nbs._Transport) as result:
                nbs._exchange(nbs._Deadline())
            self.assertEqual(str(result.exception), "truncated_response")
            self.assertEqual(result.exception.outcome, "unknown")

    def test_exact_maximum_response(self):
        with self.exchange(FakeHTTPResponse(body=b"x" * 512000)):
            self.assertEqual(len(nbs._exchange(nbs._Deadline()).body), 512000)

    def test_hard_deadline_includes_stuck_dns(self):
        release, done = threading.Event(), threading.Event()
        def dns(*args, **kwargs):
            try:
                release.wait(2)
                return [(2, 1, 6, "", ("8.8.8.8", 443))]
            finally:
                done.set()
        with patch.object(nbs, "TIMEOUT", .03), patch.object(nbs.socket, "getaddrinfo", side_effect=dns), patch.object(nbs, "_TLS") as tls:
            started = time.monotonic()
            try:
                with self.assertRaisesRegex(nbs._Transport, "transport_deadline"):
                    FETCH()
                self.assertLess(time.monotonic() - started, .5)
            finally:
                release.set()
                self.assertTrue(done.wait(1))
            time.sleep(.02)
            tls.assert_not_called()

    def test_hard_deadline_includes_slow_header_reader(self):
        release, done = threading.Event(), threading.Event()
        with self.exchange() as (connection, _, _), patch.object(nbs, "TIMEOUT", .03):
            def headers():
                try:
                    release.wait(2)
                    return FakeHTTPResponse()
                finally:
                    done.set()
            connection.getresponse.side_effect = headers
            try:
                with self.assertRaisesRegex(nbs._Transport, "transport_deadline"):
                    FETCH()
            finally:
                release.set()
                self.assertTrue(done.wait(1))
            connection.request.assert_called_once()

    def test_tls_verifies_hostname_and_pins_one_address(self):
        from unittest.mock import MagicMock
        raw, secured, context = MagicMock(), MagicMock(), MagicMock()
        context.wrap_socket.return_value = secured
        deadline = nbs._Deadline()
        address = (2, 1, 6, "", ("8.8.8.8", 443))
        with patch.object(nbs.ssl, "create_default_context", return_value=context) as verify, patch.object(nbs.socket, "socket", return_value=raw):
            connection = nbs._TLS(address, deadline)
            connection.connect()
        verify.assert_called_once_with()
        raw.connect.assert_called_once_with(address[4])
        context.wrap_socket.assert_called_once_with(raw, server_hostname=nbs.HOST, do_handshake_on_connect=False)
        secured.do_handshake.assert_called_once()
        deadline.abort()
        secured.shutdown.assert_called_once()

    def test_tls_failure_never_retries(self):
        with self.exchange() as (connection, _, tls):
            connection.request.side_effect = ssl.SSLError("authored TLS failure")
            with self.assertRaisesRegex(nbs._Transport, "transport_unknown"):
                FETCH()
            tls.assert_called_once()
            connection.request.assert_called_once()


if __name__ == "__main__":
    unittest.main()
