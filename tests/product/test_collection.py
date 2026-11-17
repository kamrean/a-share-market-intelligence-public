"""P4S boundary regressions. All DNS and socket access is prohibited."""

from contextlib import ExitStack, closing, redirect_stdout
from datetime import timedelta
from email.utils import format_datetime
import copy
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from market_intelligence import collection as c, models, sources
from market_intelligence.store import Store
from tools import governance

SNAPSHOT = "a" * 64
NETWORK = None


def forbidden(*args, **kwargs):
    raise AssertionError("P4S OFFLINE TEST ATTEMPTED NETWORK")


def setUpModule():
    global NETWORK
    NETWORK = ExitStack()
    for name in ("socket.socket", "socket.create_connection", "socket.getaddrinfo",
                 "http.client.HTTPConnection.request", "urllib.request.urlopen"):
        NETWORK.enter_context(patch(name, side_effect=forbidden))


def tearDownModule():
    NETWORK.close()


def feed(age=timedelta(hours=1), extra=""):
    date = format_datetime(models.utc_now() - age)
    return ("<rss><channel><item><title>PRIVATE SYNTHETIC TEST TITLE</title>"
            "<link>https://example.invalid/private-item</link><pubDate>" + date +
            "</pubDate></item>" + extra + "</channel></rss>").encode()


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.home = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.object(Path, "home", return_value=self.home))
        self.gate = self.stack.enter_context(patch.object(governance, "probe_gate", create=True, return_value=SNAPSHOT))
        self.fetch = self.stack.enter_context(patch.object(sources, "fetch", return_value=sources.Response(feed(), "application/rss+xml")))
        self.path = self.home / ".a-share-market-intelligence" / "p4s-fed-probe.sqlite"

    def cli(self, args):
        output = io.StringIO()
        with redirect_stdout(output):
            code = c.main(args)
        result = json.loads(output.getvalue())
        self.assertNotIn(str(self.home), output.getvalue())
        self.assertNotIn("PRIVATE", output.getvalue())
        self.assertNotIn("private-item", output.getvalue())
        if result["status"] != "ERROR":
            self.assertIs(c.validate_probe_record(result, result["snapshot_id"]), result)
        else:
            self.assertEqual(set(result), {"status", "reason"})
            self.assertIn(result["reason"], c.LOCAL_ERRORS)
        return code, result

    def run_probe(self):
        result = c.probe(True)
        self.assertIs(c.validate_probe_record(result, SNAPSHOT), result)
        self.assertEqual(result["status"], "RECORDED")
        return result

    def reserve(self, *, outcome=None, reason=None, stale=False):
        with Store(self.path) as store:
            lease, run = store.begin(c.TASK, c.MODE, intent=c.INTENT + SNAPSHOT)
            c._bind(store, lease, SNAPSHOT)
            attempt = store.reserve(lease)
            if outcome:
                store.settle(lease, attempt, outcome, None if outcome == "unknown" else 0, reason)
            if stale:
                store.db.execute("UPDATE lease SET expires_at=?", (models.stamp(models.utc_now() - timedelta(seconds=1)),))
            else:
                store.release(lease)
        return lease, run

    def test_fixed_live_capability_and_reservation_precedes_fetch(self):
        def response(source):
            self.assertEqual(source.source_id, "fed_press")
            self.assertEqual(source.endpoint, sources.FED_URL)
            with Store(self.path) as store:
                rows = store.db.execute("SELECT * FROM attempts").fetchall()
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["outcome"], "reserved")
                run = store.db.execute("SELECT * FROM runs").fetchone()
                self.assertEqual((run["task_key"], run["intent"]), (c.TASK, c.INTENT + SNAPSHOT))
            return sources.Response(feed(), "application/rss+xml")
        self.fetch.side_effect = response
        result = self.run_probe()
        observation = result["observation"]
        self.assertEqual(observation["mode"], "live_observation")
        self.assertEqual(observation["source_state"], "live_verified")
        self.assertEqual(observation["requests"]["attempted"], 1)
        self.assertEqual(observation["gate"]["status"], "BLOCK")
        self.assertEqual(observation["live_market_coverage"]["covered"], 0)
        self.assertEqual(self.gate.call_count, 2)
        for call in self.gate.call_args_list:
            self.assertEqual(call.args, (c.ROOT,))
            self.assertEqual(call.kwargs, {})
        self.fetch.assert_called_once()
        self.assertNotIn("PRIVATE", models.canonical(result))

    def test_explicit_opt_in_and_gate_fail_closed_without_store(self):
        code, result = self.cli(["probe"])
        self.assertEqual((code, result["reason"]), (2, "explicit_allow_network_required"))
        self.gate.assert_not_called()
        self.fetch.assert_not_called()
        self.assertFalse(self.path.exists())
        self.gate.side_effect = governance.Block("PRIVATE /path/provider/body")
        code, result = self.cli(["probe", "--allow-network"])
        self.assertEqual((code, result["reason"]), (2, "probe_gate_blocked"))
        self.fetch.assert_not_called()
        self.assertFalse(self.path.exists())

    def test_gate_generic_exceptions_and_invalid_snapshot(self):
        for value in (None, True, {}, "PRIVATE", "A" * 64):
            with self.subTest(value=value):
                self.gate.return_value = value
                self.assertEqual(self.cli(["probe", "--allow-network"])[1]["reason"], "probe_gate_blocked")
        self.gate.side_effect = OSError("PRIVATE")
        self.assertEqual(self.cli(["probe", "--allow-network"])[1]["reason"], "probe_gate_blocked")
        self.fetch.assert_not_called()

    def test_second_gate_block_and_changed_snapshot_prevent_request(self):
        for second, reason in ((governance.Block("PRIVATE"), "probe_gate_blocked"),
                               ("b" * 64, "snapshot_changed")):
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as directory:
                with patch.object(Path, "home", return_value=Path(directory)):
                    self.gate.side_effect = [SNAPSHOT, second]
                    result = c.probe(True)
                    self.assertEqual(result["status"], "BLOCKED_NO_REQUEST")
                    self.assertEqual(result["reason"], reason)
                    self.assertIsNone(result["observation"])
        self.fetch.assert_not_called()

    def test_catalog_authorization_failure_no_request(self):
        with patch.object(sources, "load_catalog", side_effect=models.Invalid("PRIVATE")):
            result = c.probe(True)
        self.assertEqual(result["reason"], "source_not_authorized")
        self.fetch.assert_not_called()

    def test_cli_no_overrides_or_abbreviated_flags(self):
        for flag in ("--ci", "--store", "--source", "--url", "--transport", "--authorization", "--task-key", "--allow-net"):
            with self.subTest(flag=flag):
                self.assertEqual(self.cli(["probe", "--allow-network", flag, "PRIVATE"])[1]["reason"], "invalid_cli_arguments")
        for command in ("status", "sweep"):
            self.assertEqual(self.cli([command, "--allow-network"])[1]["reason"], "invalid_cli_arguments")
        self.fetch.assert_not_called()
        self.gate.assert_not_called()

    def test_completed_idempotent_read_without_gate_or_writes(self):
        result = self.run_probe()
        before = self.path.read_bytes()
        self.gate.side_effect = AssertionError("read must not reauthorize")
        self.fetch.side_effect = AssertionError("read must not dispatch")
        self.assertEqual(c.probe(), result)
        self.assertEqual(c.probe(True), result)
        self.assertEqual(c.status(), result)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.fetch.call_count, 1)

    def test_status_and_sweep_absent_never_fetch_or_create_store(self):
        self.assertEqual(c.status()["status"], "NOT_RUN")
        self.assertEqual(c.sweep()["status"], "NOT_RUN")
        self.assertFalse(self.path.exists())
        self.fetch.assert_not_called()

    def test_http_403_no_retry_and_completed_failure(self):
        self.fetch.side_effect = sources.TransportError("http_status_403")
        result = self.run_probe()
        self.assertEqual(result["observation"]["error_codes"], ["http_status_403"])
        self.assertEqual(result["observation"]["requests"]["failed"], 1)
        self.assertEqual(c.probe(True), result)
        self.assertEqual(c.status(), result)
        self.assertEqual(self.fetch.call_count, 1)
        self.assertEqual(self.cli(["probe", "--allow-network"])[0], 2)

    def test_unknown_timeout_and_arbitrary_provider_errors_are_sanitized(self):
        for error, expected in ((TimeoutError("PRIVATE"), "transport_unknown"),
                                (sources.TransportError("dns_timeout", "unknown"), "dns_timeout"),
                                (sources.TransportError("PRIVATE", "failed"), "transport_unknown"),
                                (sources.TransportError("http_status_403", "PRIVATE"), "transport_unknown"),
                                (models.Invalid("PRIVATE"), "transport_unknown")):
            with self.subTest(error=type(error)), tempfile.TemporaryDirectory() as directory:
                with patch.object(Path, "home", return_value=Path(directory)):
                    self.fetch.side_effect = error
                    result = self.run_probe()
                    self.assertEqual(result["observation"]["requests"]["unknown"], 1)
                    self.assertEqual(result["observation"]["error_codes"], [expected])
                    self.assertEqual(c.probe(True), result)

    def test_invalid_response_and_response_status_never_retry(self):
        for response, error in ((None, "invalid_transport_response"),
                                (sources.Response(b"", "application/rss+xml", 403), "http_status_403"),
                                (sources.Response(b"", "PRIVATE"), "content_type")):
            with self.subTest(error=error), tempfile.TemporaryDirectory() as directory:
                with patch.object(Path, "home", return_value=Path(directory)):
                    self.fetch.return_value = response
                    before = self.fetch.call_count
                    result = self.run_probe()
                    self.assertEqual(result["observation"]["error_codes"], [error])
                    self.assertEqual(c.probe(True), result)
                    self.assertEqual(self.fetch.call_count, before + 1)

    def test_parse_failed_preserves_raw_capture(self):
        self.fetch.return_value = sources.Response(b"<!DOCTYPE rss><rss/>", "application/rss+xml")
        result = self.run_probe()["observation"]
        self.assertEqual(result["error_codes"], ["parse_failed"])
        self.assertEqual(result["requests"]["success"], 1)
        self.assertEqual(result["quality"]["parse_success_count"], 0)
        self.assertEqual(len(result["capture_ids"]), 1)
        with Store(self.path) as store:
            self.assertEqual(store.db.execute("SELECT payload FROM captures").fetchone()[0], b"<!DOCTYPE rss><rss/>")

    def test_empty_future_and_rejected_feed_never_verified(self):
        for body in (b"<rss><channel/></rss>", feed(age=timedelta(days=-1)),
                     b"<rss><channel><item/></channel></rss>"):
            with self.subTest(body=body), tempfile.TemporaryDirectory() as directory:
                with patch.object(Path, "home", return_value=Path(directory)):
                    self.fetch.return_value = sources.Response(body, "application/rss+xml")
                    result = self.run_probe()["observation"]
                    self.assertEqual(result["outcome"], "failed")
                    self.assertEqual(result["source_state"], "degraded")
                    self.assertEqual(result["error_codes"], ["empty_or_all_rejected_feed"])
                    self.assertEqual(result["gate"]["domains"]["news"]["status"], "BLOCK")

    def test_partial_parse_degraded(self):
        self.fetch.return_value = sources.Response(feed(extra="<item/>"), "application/rss+xml")
        result = self.run_probe()["observation"]
        self.assertEqual(result["parse"]["accepted"], 1)
        self.assertEqual(result["parse"]["rejected"], 1)
        self.assertEqual(result["source_state"], "degraded")
        self.assertEqual(result["error_codes"], ["partial_parse"])

    def test_stale_news_and_existing_31_day_profile(self):
        for days, state in ((2, "live_verified"), (30, "live_verified"), (32, "degraded")):
            with self.subTest(days=days), tempfile.TemporaryDirectory() as directory:
                with patch.object(Path, "home", return_value=Path(directory)):
                    self.fetch.return_value = sources.Response(feed(age=timedelta(days=days)), "application/rss+xml")
                    result = self.run_probe()["observation"]
                    self.assertEqual(result["source_state"], state)
                    self.assertEqual(result["error_codes"], ["news_gate_blocked"] if days == 32 else [])
                    if days == 32:
                        self.assertIn("stale evidence", result["gate"]["domains"]["news"]["reasons"])

    def test_original_publication_time_preserved(self):
        self.fetch.return_value = sources.Response(feed(age=timedelta(days=20)), "application/rss+xml")
        result = self.run_probe()["observation"]
        with Store(self.path) as store:
            records, unavailable = store.records()
            self.assertFalse(unavailable)
            self.assertGreater((models.instant(records[0].retrieved_at) - models.instant(records[0].published_at)).days, 19)
            self.assertEqual(records[0].first_seen_at, records[0].retrieved_at)
        self.assertIsNone(result["observations"][0]["source_timestamp"])

    def test_interruption_releases_reservation_and_restart_never_resends(self):
        self.fetch.side_effect = KeyboardInterrupt
        code, result = self.cli(["probe", "--allow-network"])
        self.assertEqual((code, result["reason"]), (2, "collection_interrupted"))
        with Store(self.path) as store:
            self.assertIsNone(store.db.execute("SELECT owner FROM lease").fetchone()[0])
            self.assertEqual(store.db.execute("SELECT outcome FROM attempts").fetchone()[0], "unknown")
        self.gate.side_effect = AssertionError("no new gate")
        result = c.status()
        self.assertEqual(result["observation"]["error_codes"], ["prior_attempt_incomplete"])
        self.assertEqual(c.probe(True), result)
        self.assertEqual(self.fetch.call_count, 1)

    def test_stale_reservation_recovers_unknown(self):
        self.reserve(stale=True)
        result = c.probe(True)
        self.assertEqual(result["observation"]["requests"]["unknown"], 1)
        self.assertEqual(result["observation"]["outcome"], "unknown")
        self.fetch.assert_not_called()
        self.gate.assert_not_called()

    def test_prior_failed_and_unknown_attempt_never_resend(self):
        for outcome, reason in (("failed", "http_status_403"), ("unknown", "transport_unknown")):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as directory:
                old_path = self.path
                self.path = Path(directory) / ".a-share-market-intelligence" / "p4s-fed-probe.sqlite"
                with patch.object(Path, "home", return_value=Path(directory)):
                    self.reserve(outcome=outcome, reason=reason)
                    self.assertEqual(c.probe(True)["observation"]["outcome"], "unknown")
                self.path = old_path
        self.fetch.assert_not_called()

    def test_active_lease_blocks_concurrent_operation(self):
        with Store(self.path) as store:
            lease, _ = store.begin(c.TASK, c.MODE, intent=c.INTENT + SNAPSHOT)
            c._bind(store, lease, SNAPSHOT)
            store.reserve(lease)
            try:
                self.assertEqual(self.cli(["probe", "--allow-network"])[1]["reason"], "probe_in_progress")
            finally:
                store.release(lease)
        self.fetch.assert_not_called()

    def test_expired_lease_cleanup_after_timeout(self):
        def expire(source):
            with Store(self.path) as store:
                store.db.execute("UPDATE lease SET expires_at=?", (models.stamp(models.utc_now() - timedelta(seconds=1)),))
            raise TimeoutError("PRIVATE")
        self.fetch.side_effect = expire
        self.assertEqual(self.cli(["probe", "--allow-network"])[0], 2)
        with Store(self.path) as store:
            self.assertIsNone(store.db.execute("SELECT owner FROM lease").fetchone()[0])
        self.assertEqual(c.status()["observation"]["requests"]["unknown"], 1)
        self.assertEqual(self.fetch.call_count, 1)

    def test_incomplete_run_without_attempt_does_not_dispatch(self):
        with Store(self.path) as store:
            lease, _ = store.begin(c.TASK, c.MODE, intent=c.INTENT + SNAPSHOT)
            c._bind(store, lease, SNAPSHOT)
            store.release(lease)
        self.assertEqual(c.probe(True)["reason"], "prior_run_without_attempt")
        self.fetch.assert_not_called()

    def test_retention_tombstones_preserve_attempt_and_summary(self):
        result = self.run_probe()
        future = models.utc_now() + timedelta(days=2)
        with patch.object(c, "utc_now", return_value=future), patch.object(models, "utc_now", return_value=future), patch("market_intelligence.store.utc_now", return_value=future):
            self.assertEqual(c.sweep(), result)
            self.assertEqual(c.status(), result)
            self.assertEqual(c.probe(True), result)
            with Store(self.path) as store:
                self.assertIsNone(store.db.execute("SELECT payload FROM captures").fetchone()[0])
                self.assertEqual(store.db.execute("SELECT count(*) FROM evidence").fetchone()[0], 0)
                self.assertEqual(store.db.execute("SELECT count(*) FROM tombstones").fetchone()[0], 1)
                self.assertEqual(store.db.execute("SELECT count(*) FROM attempts").fetchone()[0], 1)
        self.assertEqual(self.fetch.call_count, 1)

    def test_corrupt_unrelated_empty_database_rejected_without_network(self):
        self.path.parent.mkdir()
        for body in (b"PRIVATE not sqlite", b""):
            self.path.write_bytes(body)
            self.assertEqual(self.cli(["probe", "--allow-network"])[0], 2)
            self.assertEqual(self.path.read_bytes(), body)
        self.path.unlink()
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("CREATE TABLE private_data (value TEXT)")
        before = self.path.read_bytes()
        self.assertEqual(self.cli(["probe", "--allow-network"])[0], 2)
        self.assertEqual(self.path.read_bytes(), before)
        self.fetch.assert_not_called()

    def _expired_boundary(self, at, action):
        statements = []
        original_init = c._MetadataStore.__init__

        def traced_init(store, *args, **kwargs):
            original_init(store, *args, **kwargs)
            store.db.set_trace_callback(statements.append)

        with ExitStack() as stack:
            for name in ("market_intelligence.collection.utc_now", "market_intelligence.models.utc_now",
                         "market_intelligence.store.utc_now", "market_intelligence.runtime.utc_now",
                         "market_intelligence.gate.utc_now"):
                stack.enter_context(patch(name, return_value=at))
            stack.enter_context(patch.object(c._MetadataStore, "__init__", traced_init))
            parser = stack.enter_context(patch.object(sources, "parse", side_effect=AssertionError("expired raw parse")))
            result = action()
            parser.assert_not_called()
        for statement in statements:
            sql = " ".join(statement.lower().split())
            self.assertNotIn("select * from captures", sql)
            self.assertNotIn("select payload from captures", sql)
            self.assertNotIn("select * from evidence", sql)
            self.assertNotIn("select record from evidence", sql)
        return result

    def test_exact_expiry_status_and_idempotent_probe_use_metadata_only(self):
        result = self.run_probe()
        expiry = models.instant(result["observation"]["observations"][0]["expires_at"])
        self.assertEqual(self._expired_boundary(expiry, c.status), result)
        self.assertEqual(self._expired_boundary(expiry, lambda: c.probe(True)), result)
        with Store(self.path) as store:
            self.assertEqual(store.db.execute("SELECT count(*) FROM tombstones").fetchone()[0], 0)
            self.assertEqual(store.db.execute("SELECT count(*) FROM attempts").fetchone()[0], 1)
        self.fetch.assert_called_once()

    def test_expired_sweep_never_loads_or_parses_payload(self):
        result = self.run_probe()
        future = models.utc_now() + timedelta(days=2)
        self.assertEqual(self._expired_boundary(future, c.sweep), result)
        self.assertEqual(self._expired_boundary(future, c.status), result)
        self.assertEqual(self._expired_boundary(future, c.sweep), result)
        with Store(self.path) as store:
            self.assertIsNone(store.db.execute("SELECT payload FROM captures").fetchone()[0])
            self.assertEqual(store.db.execute("SELECT count(*) FROM evidence").fetchone()[0], 0)
            self.assertEqual(store.db.execute("SELECT count(*) FROM tombstones").fetchone()[0], 1)
            self.assertEqual(store.db.execute("SELECT count(*) FROM attempts").fetchone()[0], 1)
            self.assertEqual(store.capture(result["observation"]["capture_ids"][0]).content_hash,
                             result["observation"]["observations"][0]["content_hash"])
        self.fetch.assert_called_once()

    def test_expired_interrupted_capture_recovers_and_sweeps_metadata_only(self):
        with patch.object(Store, "finish", side_effect=KeyboardInterrupt):
            self.assertEqual(self.cli(["probe", "--allow-network"])[1]["reason"], "collection_interrupted")
        future = models.utc_now() + timedelta(days=2)
        result = self._expired_boundary(future, c.status)
        self.assertEqual(result["observation"]["error_codes"], ["prior_attempt_incomplete"])
        self.assertEqual(result["observation"]["source_state"], "degraded")
        self.assertEqual(self._expired_boundary(future, c.sweep), result)
        self.assertEqual(self._expired_boundary(future, lambda: c.probe(True)), result)
        with Store(self.path) as store:
            self.assertIsNone(store.db.execute("SELECT payload FROM captures").fetchone()[0])
            self.assertEqual(store.db.execute("SELECT count(*) FROM attempts").fetchone()[0], 1)
        self.fetch.assert_called_once()

    def test_expired_malformed_capture_does_not_reparse_before_cleanup(self):
        self.fetch.return_value = sources.Response(b"not RSS", "application/rss+xml")
        result = self.run_probe()
        self.assertEqual(result["observation"]["error_codes"], ["parse_failed"])
        future = models.utc_now() + timedelta(days=2)
        self.assertEqual(self._expired_boundary(future, c.status), result)
        self.assertEqual(self._expired_boundary(future, c.sweep), result)
        self.fetch.assert_called_once()

    def test_checkout_home_path_rejected(self):
        with patch.object(Path, "home", return_value=c.ROOT):
            self.assertEqual(self.cli(["probe", "--allow-network"])[0], 2)
        self.fetch.assert_not_called()
        self.gate.assert_not_called()

    def test_any_git_checkout_path_rejected(self):
        (self.home / ".git").mkdir()
        self.assertEqual(self.cli(["probe", "--allow-network"])[0], 2)
        self.fetch.assert_not_called()

    def test_completed_attempt_deletion_cannot_resend(self):
        self.run_probe()
        with Store(self.path) as store:
            store.db.execute("DELETE FROM attempts")
        self.assertEqual(self.cli(["probe", "--allow-network"])[1]["reason"], "invalid_local_state")
        self.assertEqual(self.fetch.call_count, 1)

    def test_unknown_attempt_deletion_cannot_resend(self):
        self.reserve()
        with Store(self.path) as store:
            store.db.execute("DELETE FROM attempts")
        self.assertEqual(self.cli(["probe", "--allow-network"])[1]["reason"], "invalid_local_state")
        self.fetch.assert_not_called()

    def test_wrong_task_or_intent_cannot_return_summary_or_dispatch(self):
        self.run_probe()
        for column, bad in (("task_key", "different"), ("intent", c.INTENT + "INVALID"),
                            ("intent", c.INTENT + "b" * 64), ("mode", "synthetic_test")):
            with self.subTest(column=column), Store(self.path) as store:
                original = store.db.execute("SELECT " + column + " FROM runs").fetchone()[0]
                store.db.execute("UPDATE runs SET " + column + "=?", (bad,))
                self.assertEqual(self.cli(["status"])[1]["reason"], "invalid_local_state")
                store.db.execute("UPDATE runs SET " + column + "=?", (original,))
        self.assertEqual(self.fetch.call_count, 1)

    def test_attempt_outcome_conflict_and_payload_tampering(self):
        self.run_probe()
        with Store(self.path) as store:
            store.db.execute("UPDATE attempts SET outcome='unknown',observed_bytes=NULL,reason='transport_unknown'")
        self.assertEqual(self.cli(["status"])[1]["reason"], "invalid_local_state")
        with Store(self.path) as store:
            store.db.execute("UPDATE attempts SET outcome='success',reason='response_received',observed_bytes=?", (len(self.fetch.return_value.body),))
            store.db.execute("UPDATE captures SET payload=?", (b"PRIVATE",))
        self.assertEqual(self.cli(["probe", "--allow-network"])[1]["reason"], "invalid_local_state")
        self.assertEqual(self.fetch.call_count, 1)

    def test_forged_summary_with_rehashed_event_still_checks_relationships(self):
        result = self.run_probe()
        summary = copy.deepcopy(result["observation"])
        summary["run_id"] = "b" * 32
        with Store(self.path) as store:
            store.db.execute("UPDATE runs SET summary=?", (models.canonical(summary),))
            store.db.execute("UPDATE events SET detail=? WHERE kind='completed'", (models.canonical({"summary_hash": models.digest(summary)}),))
        self.assertEqual(self.cli(["status"])[1]["reason"], "invalid_local_state")

    def test_validator_exact_placeholder_and_predispatch_record(self):
        result = c._record(SNAPSHOT)
        self.assertEqual(result["status"], "NOT_RUN")
        for key, value in (("observed_at", models.stamp(models.utc_now())), ("reason", "PRIVATE"), ("observation", {}), ("schema_version", True), ("extra", "PRIVATE")):
            with self.subTest(key=key), self.assertRaises(models.Invalid):
                c.validate_probe_record(result | {key: value}, SNAPSHOT)
        for reason in c.PRE_DISPATCH_ERRORS:
            self.assertEqual(c._record(SNAPSHOT, reason=reason)["status"], "BLOCKED_NO_REQUEST")
        with self.assertRaises(models.Invalid):
            c._record(SNAPSHOT, reason="PRIVATE")

    def test_validator_rejects_synthetic_counts_times_urls_and_secrets(self):
        result = self.run_probe()
        changes = [
            (("snapshot_id",), "b" * 64), (("observed_at",), "2020-01-01T00:00:00Z"),
            (("observation", "mode"), "synthetic_test"),
            (("observation", "source_id"), "baostock"),
            (("observation", "run_id"), "PRIVATE"),
            (("observation", "error_codes"), ["PRIVATE"]),
            (("observation", "requests", "attempted"), 2),
            (("observation", "requests", "success"), True),
            (("observation", "requests", "observed_bytes"), None),
            (("observation", "quality", "request_sample_size"), True),
            (("observation", "quality", "nonempty_parse_count"), 0),
            (("observation", "quality", "correction_rate"), 0.0),
            (("observation", "live_market_coverage", "covered"), False),
            (("observation", "observations", 0, "locator"), "https://example.invalid/PRIVATE"),
            (("observation", "observations", 0, "retention"), "reprocessable_raw"),
            (("observation", "observations", 0, "expires_at"), "2999-01-01T00:00:00Z"),
            (("observation", "observations", 0, "source_timestamp"), "2020-01-01T00:00:00Z"),
            (("observation", "observations", 0, "title"), "PRIVATE"),
            (("observation", "gate", "domains", "news", "reasons"), ["PRIVATE"]),
        ]
        for path, value in changes:
            bad = copy.deepcopy(result)
            target = bad
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            with self.subTest(path=path), self.assertRaises(models.Invalid) as caught:
                c.validate_probe_record(bad, SNAPSHOT)
            self.assertEqual(str(caught.exception), "invalid_probe_record")

    def test_validator_arbitrary_types_fail_with_typed_generic_error(self):
        for value in (None, True, [], "PRIVATE", {"status": []}):
            with self.subTest(value=value), self.assertRaises(models.Invalid) as caught:
                c.validate_probe_record(value, SNAPSHOT)
            self.assertEqual(str(caught.exception), "invalid_probe_record")

    def test_hardlinked_store_and_linked_parent_rejected(self):
        target = self.home / "target.sqlite"
        target.write_bytes(b"PRIVATE")
        self.path.parent.mkdir()
        os.link(target, self.path)
        self.assertEqual(self.cli(["probe", "--allow-network"])[0], 2)
        self.path.unlink()
        self.path.parent.rmdir()
        directory = self.home / "target-directory"
        directory.mkdir()
        try:
            self.path.parent.symlink_to(directory, target_is_directory=True)
        except OSError:
            # Windows without symlink privilege still exercises the link guard.
            original = Path.is_symlink
            with patch.object(Path, "is_symlink", lambda value: value == self.path.parent or original(value)):
                self.assertEqual(self.cli(["probe", "--allow-network"])[0], 2)
        else:
            self.assertEqual(self.cli(["probe", "--allow-network"])[0], 2)
        self.fetch.assert_not_called()

    def test_sidecar_hardlink_rejected(self):
        self.path.parent.mkdir()
        target = self.home / "target"
        target.write_bytes(b"PRIVATE")
        os.link(target, Path(str(self.path) + "-journal"))
        self.assertEqual(self.cli(["probe", "--allow-network"])[0], 2)
        self.fetch.assert_not_called()

    def test_summary_parse_count_forgery_rejected_against_capture(self):
        result = self.run_probe()
        summary = copy.deepcopy(result["observation"])
        summary["parse"]["accepted"] = summary["parse"]["total"] = 2
        summary["quality"]["item_denominator"] = summary["quality"]["accepted_records"] = 2
        with Store(self.path) as store:
            store.db.execute("UPDATE runs SET summary=?", (models.canonical(summary),))
            store.db.execute("UPDATE events SET detail=? WHERE kind='completed'", (models.canonical({"summary_hash": models.digest(summary)}),))
        self.assertEqual(self.cli(["status"])[1]["reason"], "invalid_local_state")

    def test_successful_capture_interruption_recovers_without_resend(self):
        original_finish = Store.finish
        with patch.object(Store, "finish", side_effect=KeyboardInterrupt):
            self.assertEqual(self.cli(["probe", "--allow-network"])[1]["reason"], "collection_interrupted")
        self.assertIs(Store.finish, original_finish)
        result = c.status()
        self.assertEqual(result["observation"]["requests"]["success"], 1)
        self.assertEqual(result["observation"]["source_state"], "degraded")
        self.assertEqual(result["observation"]["error_codes"], ["prior_attempt_incomplete"])
        self.assertEqual(c.probe(True), result)
        self.assertEqual(self.fetch.call_count, 1)


if __name__ == "__main__":
    unittest.main()
