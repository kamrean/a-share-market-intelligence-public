"""Authored offline observations; no market request, payload file or SDK discovery."""

from contextlib import ExitStack, closing, redirect_stderr, redirect_stdout
import copy
from dataclasses import replace
from datetime import timedelta
import inspect
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import zlib

from market_intelligence import baostock_capture as capture
from market_intelligence import baostock_transport as transport
from market_intelligence import intelligence, preparation
from market_intelligence.models import Invalid, canonical, digest, instant, stamp
from market_intelligence.store import BudgetDenied, Busy, Store, external_path

SNAPSHOT = "a" * 64
NOW = instant("2026-11-19T04:30:00Z")
SECRET = "NEVER_RETAIN_PROVIDER_NAME_731"
PRICE = "1731.12345678"
NETWORK = None


def forbidden(*args, **kwargs):
    raise AssertionError("offline test attempted external IO")


def setUpModule():
    global NETWORK
    NETWORK = ExitStack()
    for name in ("socket.socket", "socket.create_connection", "socket.getaddrinfo", "socket.gethostbyname",
                 "socket.gethostbyname_ex", "socket.gethostbyaddr", "urllib.request.urlopen",
                 "urllib.request.OpenerDirector.open", "http.client.HTTPConnection.request",
                 "http.client.HTTPSConnection.request", "market_intelligence.baostock_transport._create_socket",
                 "market_intelligence.baostock_transport._resolve_ipv4"):
        NETWORK.enter_context(patch(name, side_effect=forbidden))


def tearDownModule():
    NETWORK.close()


def authored_views(empty=(), old=(), mutation=None):
    end = NOW.astimezone(capture.SHANGHAI).date() - timedelta(days=1)
    start = end - timedelta(days=13)
    views = []
    for index, (method, symbol, response_type) in enumerate(capture.STEPS, 1):
        fields, data = (), ()
        if index == 2:
            fields = ("calendar_date", "is_trading_day")
            data = tuple(((start + timedelta(days=n)).isoformat(), "1") for n in range(14))
        if index in (3, 4):
            fields = ("code", "code_name", "ipoDate", "outDate", "type", "status")
            data = ((symbol, SECRET, "1991-04-03", "", "2", "1"),)
        if index in (5, 6):
            fields = capture.daily.HEADER
            day = end - timedelta(days=int(symbol in old))
            data = () if symbol in empty else ((day.isoformat(), symbol, "1700", "1800", "1600", PRICE, "1700",
                                                "987654321", "123456789", "1.830791575"),)
        window = index in (2, 5, 6)
        view = transport.ResponseView(index, method, symbol, fields, data,
            start.isoformat() if window else None, end.isoformat() if window else None,
            stamp(NOW + timedelta(seconds=index)), stamp(NOW + timedelta(seconds=index, milliseconds=100)),
            100, digest("authored-frame-" + str(index)), 90, response_type, "00.9.40", "anonymous",
            "d" if index in (5, 6) else None, "3" if index in (5, 6) else None,
            1 if index not in (1, 7) else None, 2000 if index not in (1, 7) else None)
        views.append(mutation(view) if mutation else view)
    return views


def response_metadata(view):
    return transport.ResponseMetadata(**{k: len(view.data) if k == "row_count" else getattr(view, k)
                                         for k in capture.META_KEYS})


def authored_transport(views=None, failure=None, owner=None):
    views = authored_views() if views is None else views

    def run(*, accept_response):
        if owner:
            with closing(sqlite3.connect(owner.path)) as db:
                assert db.execute("SELECT outcome FROM attempts").fetchall() == [("reserved",)]
        responses = []
        reason = "complete"
        for view in views:
            if failure == view.index:
                reason = "eof"
                break
            responses.append(response_metadata(view))
            try:
                if accept_response(view) is not None:
                    raise Invalid("callback returned a value")
            except Exception:
                reason = "callback_rejected"
                break
        if owner:
            owner.calls.extend(r.index for r in responses)
            owner.clock.return_value = NOW + timedelta(seconds=10)
        return transport.TransportReceipt("COMPLETE" if reason == "complete" else "FAILED", reason,
            stamp(NOW), stamp(NOW + timedelta(seconds=8)), views[1].start_date, views[1].end_date,
            len(responses) + int(failure is not None and reason == "eof"), 100 * len(responses),
            90 * len(responses), digest("authored-stream"), tuple(responses), True, True, True)
    return run


def failed_receipt(snapshot_id=SNAPSHOT):
    """Closed failed zero-byte fixture for offline governance tests, not evidence."""
    t = transport.TransportReceipt("FAILED", "dns", stamp(NOW), stamp(NOW + timedelta(seconds=1)),
        "2026-11-05", "2026-11-18", 0, 0, 0, digest(b""), (), True, True, True).to_dict()
    memory = capture._Memory()
    inp = memory.metadata(t)
    memory.dispose()
    return capture._make_receipt(snapshot_id, {"run_id": "1" * 32, "started_at": stamp(NOW)}, "2" * 32,
        t, inp, capture._not_computed(), None, True, stamp(NOW + timedelta(seconds=2)), "FAILED", "transport_failed")


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="p4b-capture-test-")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.path = self.home / "p4b.sqlite"
        self.calls = []
        self.enterContext(patch.object(capture, "_path", return_value=self.path))
        self.enterContext(patch.object(capture, "_authorize", return_value=SNAPSHOT))
        self.enterContext(patch.object(capture, "_snapshot", return_value=SNAPSHOT))
        self.clock = self.enterContext(patch.object(capture, "utc_now", return_value=NOW - timedelta(seconds=1)))
        self.enterContext(patch("market_intelligence.store.utc_now", side_effect=lambda: self.clock()))
        self.enterContext(patch.object(intelligence, "utc_now", return_value=NOW + timedelta(seconds=10)))
        self.enterContext(patch.object(preparation, "utc_now", return_value=NOW + timedelta(seconds=10)))
        self.runner = self.enterContext(patch.object(transport, "run_once", side_effect=authored_transport(owner=self)))

    def sql(self, query, args=()):
        with closing(sqlite3.connect(self.path)) as db:
            with db:
                return db.execute(query, args).fetchall()

    def observe(self, **kwargs):
        if not self.path.exists():
            self.clock.return_value = NOW - timedelta(seconds=1)
        if kwargs:
            self.runner.side_effect = authored_transport(owner=self, **kwargs)
        return capture.observe(True)

    def assert_empty(self):
        for table in ("captures", "evidence", "bundles", "tombstones"):
            self.assertEqual(self.sql("SELECT count(*) FROM " + table), [(0,)])
        self.assertEqual(self.sql("SELECT summary FROM runs"), [(None,)])

    def assert_no_payload(self, result):
        text = canonical(result)
        self.assertNotIn(SECRET, text)
        self.assertNotIn(PRICE, text)
        for file in self.home.iterdir():
            data = file.read_bytes()
            self.assertNotIn(SECRET.encode(), data)
            self.assertNotIn(PRICE.encode(), data)
            self.assertNotIn(b"987654321", data)
        self.assert_empty()

    def test_positive_two_indices_existing_build_and_verify(self):
        with patch.object(capture.daily, "build_daily_replay_bundle", wraps=capture.daily.build_daily_replay_bundle) as build:
            with patch.object(capture.daily, "verify_daily_replay_bundle", wraps=capture.daily.verify_daily_replay_bundle) as verify:
                result = self.observe()
        self.assertEqual(result["status"], "OBSERVED")
        self.assertEqual(build.call_count, 1)
        self.assertEqual(verify.call_count, 1)
        self.assertEqual(result["observation"]["input"]["accepted_rows"], 2)
        self.assertEqual(result["observation"]["live_market_coverage"]["covered"], 0)
        self.assert_no_payload(result)
        self.assertEqual(self.sql("SELECT status,summary FROM runs"), [("complete", None)])
        self.assertEqual(self.calls, list(range(1, 8)))

    def test_batch_time_excludes_logout_and_does_not_refresh_expiry(self):
        result = self.observe()["observation"]
        self.assertEqual(result["batch_possession_at"], authored_views()[5].completed_at)
        self.assertEqual(result["composition"]["expires_at"], stamp(NOW + timedelta(seconds=62)))

    def test_partial_empty_and_stale_input(self):
        for empty, old, expected, covered in [((capture.SYMBOLS[1],), (), "PARTIAL", 1),
                (capture.SYMBOLS, (), "EMPTY", 0), ((), capture.SYMBOLS, "PARTIAL", 0)]:
            with self.subTest(expected=expected, old=old):
                self.path = self.home / (str(len(list(self.home.iterdir()))) + ".sqlite")
                with patch.object(capture, "_path", return_value=self.path):
                    result = self.observe(views=authored_views(empty=empty, old=old))
                    self.assertEqual(result["status"], expected)
                    self.assertEqual(len(result["observation"]["input"]["latest_session_symbols"]), covered)
                    self.assert_no_payload(result)

    def test_reservation_before_dispatch_and_permanent_after_success(self):
        self.observe()
        with self.assertRaises(Invalid):
            self.observe()
        self.assertEqual(self.runner.call_count, 1)
        self.assertEqual(self.sql("SELECT count(*) FROM attempts"), [(1,)])

    def test_failed_session_still_permanently_consumed(self):
        result = self.observe(failure=1)
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["observation"]["transport"]["requests_sent"], 1)
        with self.assertRaises(Invalid):
            capture.assert_journal_unconsumed(SNAPSHOT)
        self.assert_no_payload(result)

    def test_logout_failure_never_computes(self):
        with patch.object(capture.daily, "build_daily_replay_bundle", side_effect=forbidden):
            result = self.observe(failure=7)
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["observation"]["composition"]["status"], "NOT_RUN")
        self.assert_no_payload(result)

    def test_sdk_unknown_and_interrupted_preserve_unknown_usage(self):
        for error in (RuntimeError(SECRET), KeyboardInterrupt(SECRET)):
            with self.subTest(error=type(error).__name__):
                self.path = self.home / (str(len(list(self.home.iterdir()))) + ".sqlite")
                with patch.object(capture, "_path", return_value=self.path):
                    self.runner.side_effect = error
                    result = capture.observe(True)
                    self.assertEqual(result["status"], "UNKNOWN")
                    self.assertEqual(self.sql("SELECT outcome,observed_bytes FROM attempts"), [("unknown", None)])
                    self.assertIsNone(result["observation"]["cleanup"]["socket_closed"])
                    self.assert_no_payload(result)

    def test_callback_calendar_missing_duplicate_or_bad_flag_stops(self):
        for change in (lambda d: d[:-1], lambda d: d[:-1] + (d[0],), lambda d: ((d[0][0], "true"),) + d[1:]):
            views = authored_views(mutation=lambda v: replace(v, data=change(v.data)) if v.index == 2 else v)
            memory = capture._Memory()
            receipt = authored_transport(views)(accept_response=memory.accept)
            self.assertEqual(receipt.reason, "callback_rejected")
            self.assertEqual(receipt.requests_sent, 2)
            memory.dispose()
            self.assertTrue(memory.disposed)

    def test_callback_identity_and_header_rejection(self):
        mutations = [lambda v: replace(v, data=((v.symbol, SECRET, "", "", "1", "1"),)),
                     lambda v: replace(v, user_id=SECRET), lambda v: replace(v, response_type="45"),
                     lambda v: replace(v, client_version="0.9.4"), lambda v: replace(v, fields=v.fields[::-1]),
                     lambda v: replace(v, page_number=True), lambda v: replace(v, page_size=10)]
        for mutation in mutations:
            with self.subTest(mutation=mutations.index(mutation)):
                views = authored_views(mutation=lambda v: mutation(v) if v.index == 3 else v)
                receipt = authored_transport(views)(accept_response=capture._Memory().accept)
                self.assertEqual(receipt.reason, "callback_rejected")
                self.assertEqual(receipt.requests_sent, 3)

    def test_callback_daily_symbol_date_duplicate_and_values_rejection(self):
        for column, value in ((0, "2026-11-19"), (0, "2026-10-01"), (1, "sz.399006"), (5, "NaN"), (7, "-1")):
            def mutation(view):
                if view.index != 5:
                    return view
                row = list(view.data[0])
                row[column] = value
                return replace(view, data=(tuple(row),))
            receipt = authored_transport(authored_views(mutation=mutation))(accept_response=capture._Memory().accept)
            self.assertEqual(receipt.requests_sent, 5)
            self.assertEqual(receipt.reason, "callback_rejected")
        views = authored_views(mutation=lambda v: replace(v, data=v.data * 2) if v.index == 5 else v)
        self.assertEqual(authored_transport(views)(accept_response=capture._Memory().accept).reason, "callback_rejected")

    def test_callback_clock_rollback_rejected_not_max_masked(self):
        views = authored_views(mutation=lambda v: replace(v, first_seen_at=stamp(NOW)) if v.index == 4 else v)
        receipt = authored_transport(views)(accept_response=capture._Memory().accept)
        self.assertEqual(receipt.requests_sent, 4)
        self.assertEqual(receipt.reason, "callback_rejected")

    def test_callback_failure_cleanup_and_no_later_calls(self):
        views = authored_views(mutation=lambda v: replace(v, fields=("bad",)) if v.index == 5 else v)
        result = self.observe(views=views)
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(self.calls, list(range(1, 6)))
        self.assert_no_payload(result)

    def test_bundle_tamper_fails_recomputation_without_leak(self):
        original = capture.daily.build_daily_replay_bundle
        def tampered(*args):
            bundle = original(*args)
            bundle["content"]["results"]["market"]["benchmarks"][0]["value"] = 999
            bundle["bundle_id"] = digest(bundle["content"])
            return bundle
        with patch.object(capture.daily, "build_daily_replay_bundle", side_effect=tampered):
            result = self.observe()
        self.assertEqual(result["reason"], "computation_failed")
        self.assertFalse(result["observation"]["composition"]["verified"])
        self.assert_no_payload(result)

    def test_expiry_and_compute_clock_rollback(self):
        for now in (NOW + timedelta(seconds=70), NOW - timedelta(seconds=1)):
            with self.subTest(now=now.isoformat()):
                self.path = self.home / (str(len(list(self.home.iterdir()))) + ".sqlite")
                with patch.object(capture, "_path", return_value=self.path):
                    with patch.object(intelligence, "utc_now", return_value=now):
                        result = self.observe()
                self.assertEqual(result["reason"], "computation_failed")

    def test_lease_lost_retains_unknown_and_prevents_redispatch(self):
        original = Store.renew
        calls = []
        def lose(store, lease, now=None):
            calls.append(1)
            if len(calls) == 2:
                raise Busy("stale_or_foreign_lease")
            return original(store, lease, now)
        with patch.object(Store, "renew", new=lose):
            with self.assertRaises(Busy):
                self.observe()
        result = capture.status()
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertIsNone(result["observation"]["cleanup"]["capture_payloads_disposed"])
        with self.assertRaises(Invalid):
            capture.assert_journal_unconsumed(SNAPSHOT)
        self.assert_empty()

    def test_failure_after_settle_does_not_fake_completion(self):
        with patch.object(capture, "_finish", side_effect=RuntimeError(SECRET)):
            with self.assertRaises(RuntimeError):
                self.observe()
        result = capture.status()
        self.assertEqual(result["reason"], "prior_attempt_incomplete")
        with self.assertRaises(Invalid):
            self.observe()

    def test_atomic_finish_rollback(self):
        original = Store._event
        def fail(store, run_id, now, kind, detail):
            original(store, run_id, now, kind, detail)
            if kind == "p4b_result":
                raise RuntimeError(SECRET)
        with patch.object(Store, "_event", new=fail):
            with self.assertRaises(RuntimeError):
                self.observe()
        self.assertEqual(self.sql("SELECT count(*) FROM events WHERE kind='p4b_result'"), [(0,)])
        self.assertEqual(capture.status()["status"], "UNKNOWN")

    def test_literal_opt_in_before_gate_store_or_path(self):
        for value in (False, 1, "true", None):
            with patch.object(capture, "_path", side_effect=forbidden):
                with self.assertRaises(Invalid):
                    capture.observe(value)
        self.assertFalse(self.path.exists())

    def test_gate_before_store_change_and_no_bypass(self):
        with patch.object(capture, "_authorize", side_effect=Invalid("market_gate_blocked")):
            with patch.object(Store, "__init__", side_effect=forbidden):
                with self.assertRaises(Invalid):
                    self.observe()
        self.assertFalse(self.path.exists())

    def test_changed_snapshot_before_reservation_never_dispatches(self):
        with patch.object(capture, "_snapshot", return_value="b" * 64):
            with self.assertRaises(Invalid):
                self.observe()
        self.runner.assert_not_called()
        self.assertEqual(self.sql("SELECT count(*) FROM attempts"), [(0,)])

    def test_status_readonly_byte_unchanged_no_gate_or_sdk(self):
        result = self.observe()
        before = self.path.read_bytes(), self.path.stat().st_mtime_ns
        with patch.object(capture, "_authorize", side_effect=forbidden), patch.object(transport, "run_once", side_effect=forbidden):
            self.assertEqual(capture.status(), result)
            self.assertEqual(capture.status(), result)
        self.assertEqual(before, (self.path.read_bytes(), self.path.stat().st_mtime_ns))

    def test_missing_status_and_journal_gate_never_create(self):
        self.assertEqual(capture.status(), capture.not_run_receipt(SNAPSHOT))
        self.assertIsNone(capture.assert_journal_unconsumed(SNAPSHOT))
        self.assertFalse(self.path.exists())

    def test_unsafe_empty_foreign_wal_journal_rejected_unchanged(self):
        self.path.touch()
        with self.assertRaises(Invalid):
            capture.assert_journal_unconsumed(SNAPSHOT)
        self.assertEqual(self.path.read_bytes(), b"")
        self.path.unlink()
        with closing(sqlite3.connect(self.path)) as db:
            with db:
                db.execute("CREATE TABLE foreign_data(x)")
        before = self.path.read_bytes()
        with self.assertRaises(Invalid):
            capture.assert_journal_unconsumed(SNAPSHOT)
        self.assertEqual(before, self.path.read_bytes())

    def test_existing_empty_store_allowed_but_any_preparatory_run_blocks(self):
        with Store(self.path):
            pass
        self.assertIsNone(capture.assert_journal_unconsumed(SNAPSHOT))
        with Store(self.path) as store:
            lease, _ = store.begin(capture.TASK, "live_observation", intent=capture.INTENT + SNAPSHOT)
            store.release(lease)
        with self.assertRaises(Invalid):
            capture.assert_journal_unconsumed(SNAPSHOT)

    def test_payload_taint_in_any_table_is_rejected_without_payload_read(self):
        self.observe()
        self.sql("INSERT INTO bundles VALUES (?,?)", ("a" * 64, SECRET))
        with self.assertRaises(Invalid):
            capture.status()

    def test_writable_journal_prohibits_payload_table_writes(self):
        with capture._Journal(self.path) as store:
            for table in ("captures", "evidence", "bundles", "tombstones"):
                with self.assertRaises(sqlite3.DatabaseError):
                    store.db.execute("DELETE FROM " + table)
            with self.assertRaises(sqlite3.DatabaseError):
                store.db.execute("INSERT INTO bundles VALUES (?,?)", ("a" * 64, SECRET))
            capture._empty_payloads(store)
        self.assertNotIn(SECRET.encode(), self.path.read_bytes())

    def test_hardlinked_path_denied_before_store_changes(self):
        with Store(self.path):
            pass
        other = self.home / "linked.sqlite"
        os.link(self.path, other)
        before = self.path.read_bytes()
        with patch.object(capture, "_path", side_effect=lambda: external_path(self.path)):
            with self.assertRaises(Invalid):
                capture.assert_journal_unconsumed(SNAPSHOT)
        self.assertEqual(before, self.path.read_bytes())

    def test_wal_or_orphan_sidecar_is_not_read_or_reset(self):
        sidecar = Path(str(self.path) + "-wal")
        sidecar.touch()
        with self.assertRaises(Invalid):
            capture.assert_journal_unconsumed(SNAPSHOT)
        with self.assertRaises(Invalid):
            capture.status()
        self.assertTrue(sidecar.exists())
        self.assertFalse(self.path.exists())

    def test_capacity_refusal_does_not_dispatch(self):
        with patch.object(Store, "_capacity", side_effect=BudgetDenied("storage_limit")):
            with self.assertRaises(BudgetDenied):
                self.observe()
        self.runner.assert_not_called()
        self.assertEqual(self.sql("SELECT count(*) FROM attempts"), [(0,)])

    def test_second_observer_cannot_dispatch_during_reserved_session(self):
        entered, release = threading.Event(), threading.Event()
        original = self.runner.side_effect
        results, errors = [], []
        def delayed(*, accept_response):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("test_timeout")
            return original(accept_response=accept_response)
        def first():
            try:
                results.append(capture.observe(True))
            except BaseException as exc:
                errors.append(type(exc).__name__)
        self.runner.side_effect = delayed
        worker = threading.Thread(target=first)
        worker.start()
        try:
            self.assertTrue(entered.wait(5))
            with self.assertRaises(Invalid):
                capture.observe(True)
        finally:
            release.set()
            worker.join(10)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 1)
        self.assertEqual(self.runner.call_count, 1)

    def test_expiry_after_verified_compute_demotes_before_retention(self):
        original = capture._compose
        def expire(*args):
            result = original(*args)
            self.clock.return_value = NOW + timedelta(seconds=63)
            return result
        with patch.object(capture, "_compose", side_effect=expire):
            with self.assertRaises(Busy):
                self.observe()
        self.assertEqual(capture.status()["status"], "UNKNOWN")
        self.assert_empty()

    def test_failed_cleanup_never_allows_compute(self):
        original = self.runner.side_effect
        def unclean(*, accept_response):
            return replace(original(accept_response=accept_response), status="FAILED", reason="cleanup", bindings_restored=False)
        self.runner.side_effect = unclean
        with patch.object(capture.daily, "build_daily_replay_bundle", side_effect=forbidden):
            result = self.observe()
        self.assertEqual(result["status"], "FAILED")
        self.assertFalse(result["observation"]["cleanup"]["bindings_restored"])
        self.assert_no_payload(result)

    def test_tampered_event_receipt_or_hash_rejected(self):
        self.observe()
        row = self.sql("SELECT detail FROM events WHERE kind='p4b_result'")[0][0]
        value = json.loads(row)
        value["receipt"]["observation"]["input"]["accepted_rows"] = 13
        value["receipt_hash"] = digest(value["receipt"])
        self.sql("UPDATE events SET detail=? WHERE kind='p4b_result'", (canonical(value),))
        with self.assertRaises(Invalid):
            capture.status()

    def test_extra_event_foreign_snapshot_summary_and_attempt_tampering(self):
        self.observe()
        self.sql("UPDATE runs SET summary=?", (SECRET,))
        with self.assertRaises(Invalid):
            capture.status()
        with self.assertRaises(Invalid):
            capture.assert_journal_unconsumed("b" * 64)

    def test_api_signatures_and_exact_cli(self):
        self.assertEqual(list(inspect.signature(capture.observe).parameters), ["allow_network"])
        self.assertEqual(list(inspect.signature(capture.status).parameters), [])
        for args in (["observe", "--ci"], ["observe", "--allow-network", "--store", "x"], ["sweep"], ["status", "x"]):
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(capture.main(args), 2)
            self.assertEqual(json.loads(output.getvalue())["reason"], "invalid_cli_arguments")

    def test_cli_sanitizes_exceptions_and_capture_compute_output(self):
        original = capture.daily.build_daily_replay_bundle
        def noisy(*args):
            print(SECRET)
            print(PRICE, file=sys.stderr)
            return original(*args)
        out, err = io.StringIO(), io.StringIO()
        with patch.object(capture.daily, "build_daily_replay_bundle", side_effect=noisy), redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(capture.main(["observe", "--allow-network"]), 0)
        self.assertNotIn(SECRET, out.getvalue() + err.getvalue())
        self.assertNotIn(PRICE, out.getvalue() + err.getvalue())
        with patch.object(capture, "observe", side_effect=RuntimeError(SECRET)), redirect_stdout(out):
            self.assertEqual(capture.main(["observe", "--allow-network"]), 2)
        self.assertNotIn(SECRET, out.getvalue())


class ReceiptTests(unittest.TestCase):
    def test_minimal_failed_fixture_and_not_run(self):
        value = failed_receipt()
        self.assertIs(capture.validate_observation_receipt(value, SNAPSHOT), value)
        self.assertEqual(capture.not_run_receipt(SNAPSHOT)["status"], "NOT_RUN")

    def test_validator_is_pure_no_sdk_file_store_clock_environment(self):
        value = failed_receipt()
        with ExitStack() as stack:
            for name in ("builtins.open", "io.open", "os.open", "os.getenv", "os._Environ.__getitem__",
                         "sqlite3.connect", "market_intelligence.baostock_capture.utc_now",
                         "market_intelligence.baostock_transport.run_once"):
                stack.enter_context(patch(name, side_effect=forbidden))
            self.assertIs(capture.validate_observation_receipt(value, SNAPSHOT), value)

    def test_unknown_keys_types_status_and_payload_rejected(self):
        changes = [lambda v: v.update(raw=SECRET), lambda v: v.update(schema_version=True),
                   lambda v: v.update(snapshot_id="b" * 64), lambda v: v.update(reason=SECRET),
                   lambda v: v["observation"].update(name=SECRET),
                   lambda v: v["observation"]["cleanup"].update(payload_tables_empty=1),
                   lambda v: v["observation"]["transport"].update(requests_sent=True),
                   lambda v: v["observation"]["transport"].update(reason=SECRET),
                   lambda v: v["observation"]["input"].update(validated_symbols=["KOSPI"]),
                   lambda v: v["observation"]["live_market_coverage"].update(covered=2)]
        for change in changes:
            value = failed_receipt()
            change(value)
            with self.assertRaisesRegex(Invalid, "^invalid_observation_receipt$"):
                capture.validate_observation_receipt(value, SNAPSHOT)

    def test_import_has_no_sdk_or_pandas(self):
        script = "import sys; import market_intelligence.baostock_capture; assert not any(k == 'baostock' or k.startswith('baostock.') or k == 'pandas' for k in sys.modules); print('SDK_FREE')"
        result = subprocess.run([sys.executable, "-B", "-c", script], cwd=capture.ROOT,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "SDK_FREE")

    def test_gate_returns_snapshot_only_after_exact_market_verdict(self):
        result = {"package_id": "P4B", "verdict": "LOCAL_READY_FOR_BAOSTOCK_PROBE", "snapshot_id": SNAPSHOT}
        with patch("tools.governance.market_gate", return_value=result) as gate:
            self.assertEqual(capture._authorize(), SNAPSHOT)
            gate.assert_called_once_with(capture.ROOT)
        for value in ("wrong", None, {**result, "verdict": "PASS"}, {**result, "package_id": "P4N"}):
            with patch("tools.governance.market_gate", return_value=value):
                with self.assertRaisesRegex(Invalid, "^market_gate_blocked$"):
                    capture._authorize()

    def test_unknown_not_promoted_to_zero_or_cleanup_claim(self):
        memory = capture._Memory()
        value = capture._make_receipt(SNAPSHOT, {"run_id": "1" * 32, "started_at": stamp(NOW)}, "2" * 32,
            None, memory.metadata(None), capture._not_computed(), None, None, None, "UNKNOWN", "prior_attempt_incomplete")
        self.assertIsNone(value["observation"]["input"]["daily_rows_received"])
        self.assertIsNone(value["observation"]["cleanup"]["checked_at"])
        value["observation"]["input"]["daily_rows_received"] = 0
        with self.assertRaises(Invalid):
            capture.validate_observation_receipt(value, SNAPSHOT)


class WireCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="p4b-wire-capture-test-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "journal.sqlite"
        self.enterContext(patch.object(capture, "_path", return_value=self.path))
        self.enterContext(patch.object(capture, "_authorize", return_value=SNAPSHOT))
        self.enterContext(patch.object(capture, "_snapshot", return_value=SNAPSHOT))
        for target in (capture, intelligence, preparation):
            self.enterContext(patch.object(target, "utc_now", return_value=NOW))
        self.enterContext(patch("market_intelligence.store.utc_now", return_value=NOW))

    def wire(self, frames=None):
        from test_baostock_transport import offline_session
        with offline_session(frames, now=NOW) as raw:
            result = capture.observe(True)
        self.assertTrue(raw.closed)
        self.assertEqual(transport._sdk_names(), [])
        self.assertNotIn("AUTHORED_INDEX_NOT_MARKET_DATA", canonical(result))
        with closing(sqlite3.connect(self.path)) as db:
            for table in ("captures", "evidence", "bundles"):
                self.assertEqual(db.execute("SELECT count(*) FROM " + table).fetchone()[0], 0)
        self.assertNotIn(b"AUTHORED_INDEX_NOT_MARKET_DATA", self.path.read_bytes())
        return result, raw

    def test_real_installed_sdk_wire_to_two_index_replay(self):
        result, raw = self.wire()
        self.assertEqual(result["status"], "OBSERVED")
        self.assertEqual(result["observation"]["input"]["accepted_rows"], 2)
        self.assertTrue(result["observation"]["composition"]["verified"])
        self.assertEqual(result["observation"]["input"]["observed_input_coverage"], {"covered": 2, "expected": 5})
        self.assertEqual(len(raw.sent), 7)
        self.assertEqual(result["observation"]["live_market_coverage"]["covered"], 0)

    def test_real_sdk_callback_calendar_rejection_halts_at_two(self):
        from test_baostock_transport import wire_frames, frame, response_parts
        frames = wire_frames()
        frames[1] = frame("34", response_parts("query_trade_dates", rows=[]))
        result, raw = self.wire(frames)
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["observation"]["transport"]["reason"], "callback_rejected")
        self.assertEqual(result["observation"]["composition"]["status"], "NOT_RUN")
        self.assertEqual(len(raw.sent), 2)

    def test_real_sdk_logout_eof_disallows_composition(self):
        from test_baostock_transport import wire_frames
        frames = wire_frames()
        frames[-1] = b""
        with patch.object(capture.daily, "build_daily_replay_bundle", side_effect=forbidden):
            result, raw = self.wire(frames)
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["observation"]["transport"]["reason"], "eof")
        self.assertEqual(result["observation"]["composition"]["status"], "NOT_RUN")
        self.assertEqual(len(raw.sent), 7)

    def test_real_sdk_partial_valid_response_is_partial(self):
        from test_baostock_transport import wire_frames, frame, response_parts
        frames = wire_frames()
        frames[5] = frame("96", response_parts("query_history_k_data_plus", symbol=capture.SYMBOLS[1], rows=[]), compressed=True)
        result, raw = self.wire(frames)
        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual(result["observation"]["input"]["accepted_rows"], 1)
        self.assertEqual(len(raw.sent), 7)

    def assert_wire_rejected_before_composition(self, frames, request_count):
        with patch.object(capture.daily, "build_daily_replay_bundle", side_effect=forbidden) as build:
            result, raw = self.wire(frames)
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["observation"]["transport"]["reason"], "protocol")
        self.assertEqual(result["observation"]["composition"]["status"], "NOT_RUN")
        self.assertIsNone(result["observation"]["input"]["accepted_rows"])
        self.assertEqual(len(raw.sent), request_count)
        build.assert_not_called()
        self.assertTrue(result["observation"]["cleanup"]["capture_payloads_disposed"])
        self.assertTrue(result["observation"]["cleanup"]["bindings_restored"])

    def test_r1_raw_symbol_space_cannot_be_repaired_into_verified_input(self):
        from test_baostock_transport import wire_frames, frame, response_parts, END
        frames = wire_frames()
        rows = [[END, "sh. 000001", "100", "102", "98", "101", "100", "1000", "100000", "1"]]
        frames[4] = frame("96", response_parts("query_history_k_data_plus", symbol=capture.SYMBOLS[0], rows=rows), compressed=True)
        self.assert_wire_rejected_before_composition(frames, 5)
        self.assertNotIn(b"sh. 000001", self.path.read_bytes())

    def test_r1_raw_numeric_space_cannot_be_repaired_into_verified_input(self):
        from test_baostock_transport import wire_frames, frame, response_parts, END
        frames = wire_frames()
        rows = [[END, capture.SYMBOLS[0], "100", "102", "98", "10 1", "100", "1000", "100000", "1"]]
        frames[4] = frame("96", response_parts("query_history_k_data_plus", symbol=capture.SYMBOLS[0], rows=rows), compressed=True)
        self.assert_wire_rejected_before_composition(frames, 5)

    def test_r1_zero_declared_login_body_stops_before_data_requests(self):
        from test_baostock_transport import wire_frames, response_parts
        frames = wire_frames()
        body = "\x01".join(response_parts("login")).encode()
        header = transport.VERSION.encode() + b"\x0101\x010000000000"
        frames[0] = header + body + b"\x01" + str(zlib.crc32(header + body)).encode() + transport.TERMINATOR
        self.assert_wire_rejected_before_composition(frames, 1)

    def test_r1_unexpected_uncompressed_tail_stops_before_data_requests(self):
        from test_baostock_transport import wire_frames, response_parts
        frames = wire_frames()
        body = "\x01".join(response_parts("login")).encode()
        header = transport.VERSION.encode() + b"\x0101\x01" + f"{len(body):010d}".encode()
        malformed = header + body + b"\x01unexpected"
        frames[0] = malformed + b"\x01" + str(zlib.crc32(malformed)).encode() + transport.TERMINATOR
        self.assert_wire_rejected_before_composition(frames, 1)

    def test_r1_valid_unicode_identity_remains_ephemeral(self):
        from test_baostock_transport import wire_frames, frame, response_parts
        frames = wire_frames()
        name = "\u4e0a\u8bc1\u6307\u6570"
        rows = [[capture.SYMBOLS[0], name, "1990-01-01", "", "2", "1"]]
        parts = response_parts("query_stock_basic", symbol=capture.SYMBOLS[0], rows=rows)
        parts[6] = json.dumps({"record": rows}, ensure_ascii=False, separators=(",", ":"))
        frames[2] = frame("46", parts)
        result, raw = self.wire(frames)
        self.assertEqual(result["status"], "OBSERVED")
        self.assertEqual(result["observation"]["input"]["accepted_rows"], 2)
        self.assertEqual(len(raw.sent), 7)
        self.assertNotIn(name, canonical(result))
        self.assertNotIn(name.encode(), self.path.read_bytes())


if __name__ == "__main__":
    unittest.main()
