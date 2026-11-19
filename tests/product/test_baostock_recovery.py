"""Offline two-phase tests with authored frames and the pinned real SDK."""

from contextlib import closing, redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from market_intelligence import baostock_recovery as recovery
from market_intelligence import baostock_capture as capture
from market_intelligence import baostock_transport as transport
from market_intelligence import intelligence, preparation
from market_intelligence.models import Invalid, canonical, digest
import test_baostock_transport as wire
from test_baostock_transport import (FIXED_NOW, frame,
                                    offline_session, response_parts, wire_frames)


class RecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        wire.TransportTests.setUpClass()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="p4d-recovery-test-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "recovery.sqlite"
        self.old = Path(self.temp.name) / "p4b-immutable-result.txt"
        self.old.write_bytes(b"AUTHORED_OLD_RESULT_KEEP")
        self.snapshot = "a" * 64
        self.prior_digest = None
        self.enterContext(patch.object(recovery, "_path", return_value=self.path))
        self.enterContext(patch.object(recovery, "_snapshot", side_effect=lambda: self.snapshot))
        self.auth = self.enterContext(patch.object(recovery, "_authorize", side_effect=lambda phase: (self.snapshot, self.prior_digest)))
        for target in (recovery, capture, intelligence, preparation):
            self.enterContext(patch.object(target, "utc_now", return_value=FIXED_NOW))
        self.enterContext(patch("market_intelligence.store.utc_now", return_value=FIXED_NOW))

    def sql(self, query, args=()):
        with closing(sqlite3.connect(self.path)) as db:
            with db:
                return db.execute(query, args).fetchall()

    def run_phase(self, phase="diagnostic", frames=None, raw=None):
        with offline_session(frames, raw=raw) as socket:
            result = recovery.observe(phase, True)
        self.assertEqual(transport._sdk_names(), [])
        self.assertEqual(self.old.read_bytes(), b"AUTHORED_OLD_RESULT_KEEP")
        self.assertNotIn("AUTHORED_INDEX_NOT_MARKET_DATA", canonical(result))
        self.assertNotIn(b"AUTHORED_INDEX_NOT_MARKET_DATA", self.path.read_bytes())
        for table in ("captures", "evidence", "bundles", "tombstones"):
            self.assertEqual(self.sql("SELECT count(*) FROM " + table), [(0,)])
        return result, socket

    def prepare_verification(self):
        result, _ = self.run_phase(frames=[frame("01", response_parts("login"), version="99.9.99")])
        self.assertEqual(result["observation"]["protocol"]["first_failure_point"], "response_version")
        self.prior_digest = digest(result)
        self.snapshot = "b" * 64
        return result

    def test_diagnostic_records_failed_parser_not_source_pass(self):
        result, raw = self.run_phase(frames=[frame("04", ["7", "PRIVATE_PROVIDER_ERROR"])])
        self.assertEqual(result["status"], "RECORDED")
        self.assertEqual(result["observation"]["transport"]["status"], "FAILED")
        self.assertEqual(result["observation"]["transport"]["requests_sent"], 1)
        self.assertEqual(result["observation"]["protocol"]["response_type"], "exception_04")
        self.assertNotIn("PRIVATE_PROVIDER", canonical(result))
        self.assertNotIn(b"PRIVATE_PROVIDER", self.path.read_bytes())
        self.assertEqual(len(raw.sent), 1)

    def test_two_phases_use_one_plus_seven_and_reuse_composition(self):
        prior = self.prepare_verification()
        result, raw = self.run_phase("verification")
        self.assertEqual(result["status"], "OBSERVED")
        self.assertEqual(len(raw.sent), 7)
        self.assertEqual(result["observation"]["input"]["accepted_rows"], 2)
        self.assertEqual(result["observation"]["input"]["rejected_rows"], 0)
        self.assertEqual(result["observation"]["input"]["latest_expected_session"], "2026-11-18")
        self.assertEqual(result["observation"]["live_market_coverage"]["covered"], 0)
        self.assertTrue(result["observation"]["composition"]["verified"])
        self.assertEqual(recovery.status("diagnostic"), prior)
        intents = self.sql("SELECT detail FROM events WHERE kind='p4d_intent'")
        self.assertEqual(sum(json.loads(row[0])["reserved_requests"] for row in intents), 8)
        self.assertEqual(self.sql("SELECT count(*) FROM attempts"), [(2,)])

    def test_no_opt_in_does_not_create_journal_or_authorize(self):
        with self.assertRaises(Invalid):
            recovery.observe("diagnostic")
        self.assertFalse(self.path.exists())
        self.auth.assert_not_called()

    def test_gate_failure_never_creates_store(self):
        self.auth.side_effect = Invalid("recovery_gate_blocked")
        with self.assertRaises(Invalid), patch.object(transport, "run_recovery") as run:
            recovery.observe("diagnostic", True)
        run.assert_not_called()
        self.assertFalse(self.path.exists())

    def test_verification_before_diagnostic_denied(self):
        self.prior_digest = "d" * 64
        with self.assertRaises(Invalid), patch.object(transport, "run_recovery") as run:
            recovery.observe("verification", True)
        run.assert_not_called()
        self.assertFalse(self.path.exists())

    def test_successful_login_is_not_supported_repair_evidence(self):
        prior, _ = self.run_phase()
        self.prior_digest = digest(prior)
        self.snapshot = "b" * 64
        with self.assertRaises(Invalid), patch.object(transport, "run_recovery") as run:
            recovery.observe("verification", True)
        run.assert_not_called()

    def test_failed_phase_cannot_be_retried_or_reset_by_snapshot(self):
        self.prepare_verification()
        before = self.path.read_bytes()
        for snap in ("a" * 64, "b" * 64, "c" * 64):
            self.snapshot = snap
            with self.assertRaises(Invalid), patch.object(transport, "run_recovery") as run:
                recovery.observe("diagnostic", True)
            run.assert_not_called()
        self.assertEqual(self.path.read_bytes(), before)

    def test_verification_cannot_be_repeated_after_failure(self):
        self.prepare_verification()
        result, _ = self.run_phase("verification", frames=[b"partial"])
        self.assertEqual(result["status"], "FAILED")
        with self.assertRaises(Invalid), patch.object(transport, "run_recovery") as run:
            recovery.observe("verification", True)
        run.assert_not_called()

    def test_wrong_diagnostic_digest_blocks_verification(self):
        self.prepare_verification()
        self.prior_digest = "f" * 64
        with self.assertRaises(Invalid), patch.object(transport, "run_recovery") as run:
            recovery.observe("verification", True)
        run.assert_not_called()

    def test_reservation_is_durable_before_sdk_dispatch(self):
        original = transport.run_recovery
        def check_reservation(**kwargs):
            self.assertEqual(self.sql("SELECT outcome FROM attempts"), [("reserved",)])
            self.assertEqual(self.sql("SELECT kind FROM events"), [("started",), ("p4d_intent",), ("reserved",)])
            return original(**kwargs)
        with patch.object(transport, "run_recovery", side_effect=check_reservation):
            self.run_phase()

    def test_snapshot_change_after_reservation_consumes_phase_without_dispatch(self):
        with patch.object(recovery, "_snapshot", return_value="c" * 64), patch.object(transport, "run_recovery") as run:
            with self.assertRaises(Invalid):
                recovery.observe("diagnostic", True)
        run.assert_not_called()
        self.assertEqual(recovery.status("diagnostic")["status"], "UNKNOWN")
        with self.assertRaises(Invalid):
            recovery.assert_phase_available("diagnostic", self.snapshot)

    def test_unexpected_dispatch_error_retains_unknown_and_no_retry(self):
        with patch.object(transport, "run_recovery", side_effect=RuntimeError("PRIVATE_EXCEPTION")):
            result = recovery.observe("diagnostic", True)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertNotIn("PRIVATE", canonical(result))
        self.assertEqual(self.sql("SELECT outcome FROM attempts"), [("unknown",)])
        with self.assertRaises(Invalid):
            recovery.assert_phase_available("diagnostic", self.snapshot)

    def test_atomic_result_failure_leaves_unknown_not_released_success(self):
        original = recovery._Journal._event
        def fail(store, run_id, now, kind, detail):
            if kind == "p4d_result":
                raise RuntimeError("authored result persistence failure")
            return original(store, run_id, now, kind, detail)
        with patch.object(recovery._Journal, "_event", fail), offline_session():
            with self.assertRaises(RuntimeError):
                recovery.observe("diagnostic", True)
        self.assertEqual(recovery.status("diagnostic")["status"], "UNKNOWN")
        self.assertEqual(self.sql("SELECT outcome FROM attempts"), [("unknown",)])
        self.assertEqual(self.sql("SELECT count(*) FROM events WHERE kind='settled'"), [(0,)])

    def test_atomic_reservation_failure_never_dispatches(self):
        original = recovery._Journal._event
        def fail(store, run_id, now, kind, detail):
            if kind == "reserved":
                raise RuntimeError("authored reservation failure")
            return original(store, run_id, now, kind, detail)
        with patch.object(recovery._Journal, "_event", fail), patch.object(transport, "run_recovery") as run:
            with self.assertRaises(RuntimeError):
                recovery.observe("diagnostic", True)
        run.assert_not_called()
        self.assertEqual(self.sql("SELECT count(*) FROM runs"), [(0,)])
        self.assertEqual(self.sql("SELECT count(*) FROM attempts"), [(0,)])

    def test_read_status_keeps_original_snapshot_and_bytes(self):
        prior = self.prepare_verification()
        before = self.path.read_bytes()
        self.assertEqual(recovery.status("diagnostic"), prior)
        self.assertEqual(self.path.read_bytes(), before)

    def test_payload_table_writes_are_denied(self):
        with recovery._Journal(self.path) as store:
            for statement in ("DELETE FROM captures", "DELETE FROM evidence", "DELETE FROM bundles", "DELETE FROM tombstones"):
                with self.assertRaises(sqlite3.DatabaseError):
                    store.db.execute(statement)

    def test_sidecar_blocks_without_mutation(self):
        sidecar = Path(str(self.path) + "-wal")
        sidecar.write_bytes(b"AUTHORED_SIDECAR")
        with self.assertRaises(Invalid), patch.object(transport, "run_recovery") as run:
            recovery.observe("diagnostic", True)
        run.assert_not_called()
        self.assertEqual(sidecar.read_bytes(), b"AUTHORED_SIDECAR")

    def test_forged_event_text_cannot_be_returned(self):
        self.prepare_verification()
        self.sql("UPDATE events SET detail=? WHERE kind='p4d_result'", ('{"private":"SECRET"}',))
        with self.assertRaises(Invalid):
            recovery.status("diagnostic")
        with self.assertRaises(Invalid):
            recovery.assert_phase_available("verification", self.snapshot, self.prior_digest)

    def test_extra_attempt_blocks_even_if_known_attempt_succeeded(self):
        self.prepare_verification()
        self.sql("UPDATE attempts SET reserved_bytes=1")
        with self.assertRaises(Invalid):
            recovery.status("diagnostic")

    def test_result_validator_rejects_raw_fields_and_false_numeric_types(self):
        result = self.prepare_verification()
        for change in (lambda v: v.update(raw="PRIVATE"),
                       lambda v: v["observation"]["protocol"].update(raw="PRIVATE"),
                       lambda v: v["observation"]["protocol"].update(frame_bytes=True),
                       lambda v: v["observation"]["transport"].update(requests_sent=2),
                       lambda v: v["observation"]["protocol"].update(first_failure_point="PRIVATE_ERROR")):
            value = deepcopy(result)
            change(value)
            with self.assertRaises(Invalid):
                recovery.validate_recovery_receipt(value, "a" * 64, "diagnostic")

    def test_not_run_is_inert_and_requires_exact_phase_snapshot(self):
        value = recovery.not_run_receipt(self.snapshot, "verification")
        self.assertEqual(value["status"], "NOT_RUN")
        self.assertFalse(self.path.exists())
        for phase, snapshot in (("diagnostic", self.snapshot), ("verification", "b" * 64)):
            with self.assertRaises(Invalid):
                recovery.validate_recovery_receipt(value, snapshot, phase)

    def test_duplicate_daily_rows_are_rejected_without_composition(self):
        self.prepare_verification()
        frames = wire_frames()
        parts = response_parts("query_history_k_data_plus", symbol="sh.000001")
        payload = json.loads(parts[6])
        payload["record"] *= 2
        parts[6] = json.dumps(payload)
        frames[4] = frame("96", parts, compressed=True)
        result, raw = self.run_phase("verification", frames=frames)
        self.assertEqual(result["status"], "FAILED")
        self.assertFalse(result["observation"]["composition"]["verified"])
        self.assertEqual(len(raw.sent), 5)

    def test_concurrent_second_reservation_cannot_dispatch(self):
        entered, release = threading.Event(), threading.Event()
        results, errors = [], []
        original = transport.run_recovery
        def delayed(**kwargs):
            entered.set()
            if not release.wait(10):
                raise RuntimeError("test timeout")
            return original(**kwargs)
        def worker():
            try:
                results.append(recovery.observe("diagnostic", True))
            except BaseException as exc:
                errors.append(type(exc).__name__)
        with offline_session(), patch.object(transport, "run_recovery", side_effect=delayed) as dispatch:
            thread = threading.Thread(target=worker)
            thread.start()
            try:
                self.assertTrue(entered.wait(10))
                with self.assertRaises(Invalid):
                    recovery.observe("diagnostic", True)
            finally:
                release.set()
                thread.join(15)
            self.assertFalse(thread.is_alive())
            self.assertEqual(dispatch.call_count, 1)
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 1)

    def test_cli_error_is_sanitized(self):
        output = io.StringIO()
        with patch.object(recovery, "observe", side_effect=RuntimeError("PRIVATE_ERROR")), redirect_stdout(output):
            self.assertEqual(recovery.main(["observe", "diagnostic", "--allow-network"]), 2)
        self.assertNotIn("PRIVATE", output.getvalue())
        self.assertFalse(self.path.exists())

    def test_false_numeric_reservation_cannot_be_reused_as_one(self):
        self.prepare_verification()
        detail = json.loads(self.sql("SELECT detail FROM events WHERE kind='p4d_intent'")[0][0])
        detail["reserved_requests"] = True
        self.sql("UPDATE events SET detail=? WHERE kind='p4d_intent'", (canonical(detail),))
        with self.assertRaises(Invalid):
            recovery.status("diagnostic")

    def test_duplicate_json_key_in_result_is_rejected(self):
        self.prepare_verification()
        detail = self.sql("SELECT detail FROM events WHERE kind='p4d_result'")[0][0]
        self.sql("UPDATE events SET detail=? WHERE kind='p4d_result'", ('{"receipt_hash":"bad",' + detail[1:],))
        with self.assertRaises(Invalid):
            recovery.status("diagnostic")

    def test_partial_wire_failure_does_not_authorize_second_probe(self):
        prior, _ = self.run_phase(frames=[b"00.9"])
        self.prior_digest = digest(prior)
        self.snapshot = "b" * 64
        with self.assertRaises(Invalid), patch.object(transport, "run_recovery") as run:
            recovery.observe("verification", True)
        run.assert_not_called()

    def test_cli_has_no_reset_retry_or_endpoint_options(self):
        for args in (["reset"], ["observe", "retry", "--allow-network"],
                     ["observe", "diagnostic", "--endpoint", "example.com"],
                     ["observe", "diagnostic"]):
            with redirect_stdout(io.StringIO()), patch.object(transport, "run_recovery") as run:
                self.assertEqual(recovery.main(args), 2)
            run.assert_not_called()
        self.assertFalse(self.path.exists())

    def test_atomic_reservation_rechecks_phase_after_preflight(self):
        self.prepare_verification()
        with patch.object(recovery, "assert_phase_available"), patch.object(transport, "run_recovery") as run:
            with self.assertRaises(Invalid):
                recovery.observe("diagnostic", True)
        run.assert_not_called()
        self.assertEqual(self.sql("SELECT count(*) FROM attempts"), [(1,)])


if __name__ == "__main__":
    unittest.main()
