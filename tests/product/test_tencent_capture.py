"""Offline transport, durable quota and sanitized receipt contract tests."""

from contextlib import contextmanager
from copy import deepcopy
from datetime import timedelta
from email.message import Message
import io
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from market_intelligence import tencent_capture as c, tencent_daily as d
from market_intelligence.models import Invalid, canonical, instant
from test_tencent_daily import body, row

NOW = instant("2026-11-19T08:00:00Z")
SNAPSHOT = "a" * 64
ADDRESS = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]


class TencentCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="p4t-test-")
        self.path = Path(self.temp.name) / "one-use.jsonl"
        self.addCleanup(self.temp.cleanup)
        for target, value in (("_path", lambda: self.path), ("_authorize", lambda: SNAPSHOT), ("utc_now", lambda: NOW)):
            fixture = patch.object(c, target, value)
            fixture.start()
            self.addCleanup(fixture.stop)

    def success(self, symbol, window):
        data = body([row(day) for day in window["expected_sessions"]], symbol=symbol)
        return data, len(data) + 100

    def observe(self, side_effect=None):
        with patch.object(c, "_fetch", side_effect=side_effect or self.success) as fetch:
            result = c.observe(True)
        return result, fetch

    def test_three_requests_parse_replay_and_no_payload_retention(self):
        result, fetch = self.observe()
        self.assertEqual(fetch.call_count, 3)
        self.assertEqual(result["observation"]["outcome"], "OBSERVED")
        self.assertEqual(result["observation"]["live_verified_benchmark_count"], 0)
        self.assertTrue(all(r["replay_verified"] for r in result["observation"]["results"]))
        saved = self.path.read_text()
        self.assertNotIn("9999.123", saved)
        self.assertNotIn('"facts"', saved)
        self.assertNotIn('"payload":', saved)
        self.assertEqual(c.status(), result)
        c.validate_observation_receipt(result, SNAPSHOT)

    def test_whole_run_and_dispatch_are_durable_before_fetch(self):
        def inspect(symbol, window):
            records = [json.loads(line) for line in self.path.read_text().splitlines()]
            self.assertEqual(records[0]["reserved_requests"], 3)
            self.assertEqual(records[-1]["kind"], "dispatch_reserved")
            self.assertEqual(records[-1]["symbol"], symbol)
            return self.success(symbol, window)
        self.observe(inspect)

    def test_requires_explicit_optin(self):
        for optin in (False, None, 1, "true"):
            with self.assertRaises(Invalid):
                c.observe(optin)
        self.assertFalse(self.path.exists())

    def test_gate_block_no_reservation_or_fetch(self):
        with patch.object(c, "_authorize", side_effect=Invalid("BLOCK")), patch.object(c, "_fetch") as fetch:
            with self.assertRaises(Invalid):
                c.observe(True)
        fetch.assert_not_called()
        self.assertFalse(self.path.exists())

    def test_existing_empty_partial_complete_files_never_reset(self):
        for data in (b"", b"partial", b'{}\n'):
            self.path.write_bytes(data)
            with patch.object(c, "_fetch") as fetch, self.assertRaises(FileExistsError):
                c.observe(True)
            fetch.assert_not_called()
            self.assertEqual(self.path.read_bytes(), data)
            self.assertEqual(c.status()["status"], "UNKNOWN_CONSUMED")
        self.path.unlink()
        self.observe()
        saved = self.path.read_bytes()
        with patch.object(c, "_fetch") as fetch, self.assertRaises(FileExistsError):
            c.observe(True)
        fetch.assert_not_called()
        self.assertEqual(saved, self.path.read_bytes())

    def test_snapshot_change_consumes_but_does_not_dispatch(self):
        with patch.object(c, "_authorize", side_effect=[SNAPSHOT, "b" * 64]), patch.object(c, "_fetch") as fetch:
            result = c.observe(True)
        fetch.assert_not_called()
        self.assertEqual(result["observation"]["local_error"], "snapshot_changed")
        self.assertEqual(result["observation"]["dispatch_attempts"], 0)
        self.assertTrue(self.path.exists())

    def test_reservation_failure_does_not_dispatch(self):
        with patch.object(c.os, "fsync", side_effect=OSError("disk")), patch.object(c, "_fetch") as fetch:
            with self.assertRaises(OSError):
                c.observe(True)
        fetch.assert_not_called()
        self.assertTrue(self.path.exists())

    def test_namespace_sync_happens_after_reservation_before_fetch(self):
        order = []
        def sync(path):
            self.assertEqual(path, self.path.resolve())
            self.assertEqual(json.loads(path.read_text())["reserved_requests"], 3)
            order.append("durable_namespace")
        def fetch(symbol, window):
            self.assertIn("durable_namespace", order)
            order.append("fetch")
            return self.success(symbol, window)
        with patch.object(c, "_sync_namespace", side_effect=sync):
            self.observe(fetch)
        self.assertEqual(order, ["durable_namespace", "fetch", "fetch", "fetch"])

    def test_namespace_sync_failure_leaves_consumed_allowance_without_fetch(self):
        with patch.object(c, "_sync_namespace", side_effect=OSError("sync failure")):
            with patch.object(c, "_fetch") as fetch, self.assertRaises(OSError):
                c.observe(True)
        fetch.assert_not_called()
        self.assertEqual(c.status()["status"], "UNKNOWN_CONSUMED")
        saved = self.path.read_bytes()
        with patch.object(c, "_fetch") as fetch, self.assertRaises(FileExistsError):
            c.observe(True)
        fetch.assert_not_called()
        self.assertEqual(saved, self.path.read_bytes())

    def test_missing_parent_is_not_created_and_no_fetch(self):
        self.path = self.path.parent / "absent" / "journal.jsonl"
        with patch.object(c, "_fetch") as fetch, self.assertRaisesRegex(Invalid, "journal_parent_must_exist"):
            c.observe(True)
        fetch.assert_not_called()
        self.assertFalse(self.path.parent.exists())

    def test_posix_syncs_all_namespace_ancestors_and_fails_closed(self):
        with patch.object(c.os, "name", "posix"), patch.object(c.os, "O_DIRECTORY", 65536, create=True):
            with patch.object(c.os, "open", return_value=77) as opened, patch.object(c.os, "fsync") as sync, patch.object(c.os, "close") as closed:
                c._sync_namespace(self.path)
                self.assertEqual([call.args[0] for call in opened.call_args_list], list(self.path.parents))
                self.assertEqual(sync.call_count, len(self.path.parents))
                self.assertEqual(closed.call_count, len(self.path.parents))
            with patch.object(c.os, "open", return_value=77), patch.object(c.os, "fsync", side_effect=OSError("sync")), patch.object(c.os, "close") as closed:
                with self.assertRaises(OSError):
                    c._sync_namespace(self.path)
                closed.assert_called_once_with(77)

    def test_windows_volume_rejects_unknown_remote_or_non_ntfs(self):
        kernel = Mock()
        for drive, ok, name in ((4, True, "NTFS"), (3, False, "NTFS"), (3, True, "FAT32")):
            kernel.GetDriveTypeW.return_value = drive
            def volume(*args):
                args[6].value = name
                return ok
            kernel.GetVolumeInformationByHandleW.side_effect = volume
            with self.subTest(drive=drive, ok=ok, name=name), self.assertRaises(Invalid):
                c._windows_volume(kernel, 77, self.path)

    @unittest.skipUnless(os.name == "nt", "Windows write-through handle")
    def test_windows_verification_failure_preserves_exclusive_file_and_no_fetch(self):
        with patch.object(c, "_windows_volume", side_effect=Invalid("unsupported")):
            with patch.object(c, "_fetch") as fetch, self.assertRaises(Invalid):
                c.observe(True)
        fetch.assert_not_called()
        self.assertTrue(self.path.exists())
        with self.assertRaises(FileExistsError):
            self.observe()

    def test_rollback_between_requests_stops_before_second_fetch(self):
        values = iter([0, 0, 0, 10, 10, 5, 20, 20, 20])
        with patch.object(c, "utc_now", side_effect=lambda: NOW + timedelta(seconds=next(values, 20))):
            result, fetch = self.observe()
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(result["observation"]["local_error"], "clock_rollback")
        self.assertEqual(result["observation"]["outcome"], "PARTIAL")

    def test_rollback_inside_request_never_dispatches_the_next_request(self):
        for times in ([0, 0, 10, 5, 10, 20], [0, 0, 0, 10, 5, 20]):
            if self.path.exists():
                self.path.unlink()
            values = iter(times)
            with patch.object(c, "utc_now", side_effect=lambda: NOW + timedelta(seconds=next(values, 20))):
                with patch.object(c, "_fetch", side_effect=self.success) as fetch, self.assertRaises(Invalid):
                    c.observe(True)
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(c.status()["status"], "UNKNOWN_CONSUMED")

    def test_rollback_after_dispatch_reservation_never_fetches(self):
        values = iter([0, 10, 5, 20, 20, 20, 20])
        with patch.object(c, "utc_now", side_effect=lambda: NOW + timedelta(seconds=next(values, 20))):
            with patch.object(c, "_fetch") as fetch:
                result = c.observe(True)
        fetch.assert_not_called()
        self.assertEqual(result["observation"]["local_error"], "clock_rollback")
        self.assertEqual(result["observation"]["dispatch_attempts"], 1)

    def test_known_and_unknown_failure_stop_without_retry(self):
        for outcome in ("failed", "unknown"):
            if self.path.exists():
                self.path.unlink()
            result, fetch = self.observe(c._Failure("http_status", outcome))
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(result["observation"]["outcome"], outcome.upper())
            self.assertEqual(result["observation"]["reserved_requests"], 3)
            self.assertEqual(c.status(), result)

    def test_parse_failure_retains_only_transport_metadata(self):
        result, fetch = self.observe(lambda *_: (b'not json', 108))
        self.assertEqual(fetch.call_count, 1)
        first = result["observation"]["results"][0]
        self.assertEqual(first["error"], "response_schema")
        self.assertEqual(first["body_bytes"], 8)
        self.assertEqual(first["http_bytes"], 108)
        self.assertNotIn("not json", self.path.read_text())

    def test_stale_completed_response_is_not_latestness_pass(self):
        def old(symbol, _):
            data = body([row("2026-11-18")], symbol=symbol)
            return data, len(data) + 100
        result, fetch = self.observe(old)
        self.assertEqual(fetch.call_count, 3)
        self.assertEqual(result["observation"]["outcome"], "PARTIAL")
        self.assertTrue(all(r["parse"]["freshness"] == "STALE" for r in result["observation"]["results"]))

    def test_interrupt_is_consumed_and_never_retried(self):
        result, fetch = self.observe(KeyboardInterrupt())
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(result["observation"]["outcome"], "UNKNOWN")

    def test_cleanup_unknown_not_claimed_success(self):
        result, _ = self.observe(c._Failure("worker_incomplete"))
        self.assertFalse(result["observation"]["connections_closed"])
        self.assertFalse(result["observation"]["payload_references_discarded"])
        self.assertEqual(result["observation"]["outcome"], "UNKNOWN")
        changed = deepcopy(result)
        changed["observation"].update(connections_closed=True, payload_references_discarded=True)
        with self.assertRaises(Invalid):
            c.validate_observation_receipt(changed, SNAPSHOT)

    def test_partial_success_needs_a_recorded_stop_reason(self):
        result, _ = self.observe()
        changed = deepcopy(result)
        o = changed["observation"]
        o["results"] = o["results"][:1]
        o["dispatch_attempts"] = 1
        o["outcome"] = "PARTIAL"
        with self.assertRaises(Invalid):
            c.validate_observation_receipt(changed, SNAPSHOT)

    def test_final_result_requires_durable_readback(self):
        with patch.object(c, "status", return_value={"status": "UNKNOWN_CONSUMED"}):
            with self.assertRaisesRegex(Invalid, "journal_finalization_unverified"):
                self.observe()
        self.assertTrue(self.path.exists())

    def test_status_checks_reservation_and_result_bindings(self):
        self.observe()
        rows = self.path.read_text().splitlines()
        reservation = json.loads(rows[0])
        reservation["run_id"] = "b" * 32
        self.path.write_text(canonical(reservation) + "\n" + "\n".join(rows[1:]) + "\n")
        self.assertEqual(c.status()["status"], "UNKNOWN_CONSUMED")

    def test_receipt_mutation_and_raw_data_rejected(self):
        result, _ = self.observe()
        for key, value in (("upstream_rights", "permitted"), ("live_verified_benchmark_count", 3),
                           ("dispatch_attempts", 2), ("reserved_requests", 4), ("outcome", "PASS"),
                           ("payloads_persisted", True)):
            changed = deepcopy(result)
            changed["observation"][key] = value
            with self.subTest(key=key), self.assertRaises(Invalid):
                c.validate_observation_receipt(changed, SNAPSHOT)
        changed = deepcopy(result)
        changed["observation"]["results"][0]["raw"] = "forbidden"
        with self.assertRaises(Invalid):
            c.validate_observation_receipt(changed, SNAPSHOT)
        with self.assertRaises(Invalid):
            c.validate_observation_receipt(result, "b" * 64)

    def test_not_run_is_not_a_completed_observation(self):
        value = c.not_run_receipt(SNAPSHOT)
        self.assertEqual(c.validate_observation_receipt(value, SNAPSHOT), value)
        value["status"] = "RECORDED"
        with self.assertRaises(Invalid):
            c.validate_observation_receipt(value, SNAPSHOT)

    def test_hard_link_rejected_before_dispatch(self):
        target = Path(self.temp.name) / "historical.txt"
        target.write_text("keep")
        os.link(target, self.path)
        with patch.object(c, "_fetch") as fetch, self.assertRaises(Invalid):
            c.observe(True)
        fetch.assert_not_called()
        self.assertEqual(target.read_text(), "keep")

    def test_two_contenders_only_one_can_reserve(self):
        entered, release = threading.Event(), threading.Event()
        results = []
        def fetch(symbol, window):
            entered.set()
            release.wait(2)
            return self.success(symbol, window)
        def run():
            try:
                results.append(c.observe(True))
            except Exception as exc:
                results.append(type(exc).__name__)
        with patch.object(c, "_fetch", side_effect=fetch):
            first = threading.Thread(target=run)
            first.start()
            self.assertTrue(entered.wait(2))
            second = threading.Thread(target=run)
            second.start()
            second.join(2)
            release.set()
            first.join(3)
        self.assertFalse(first.is_alive() or second.is_alive())
        self.assertEqual(results.count("FileExistsError"), 1)
        self.assertEqual(sum(isinstance(x, dict) for x in results), 1)


class TencentTransportTests(unittest.TestCase):
    @contextmanager
    def exchange(self, status=200, headers=None, payload=b'{}'):
        response = Mock()
        response.status = status
        message = Message()
        for name, value in (headers or [("Content-Type", "application/json"), ("Content-Length", str(len(payload)))]):
            message[name] = value
        response.headers = message
        response.getheader.side_effect = lambda name, default=None: message.get(name, default)
        chunks = iter([payload, b""])
        response.read1.side_effect = lambda _: next(chunks)
        response.bounded.count = len(payload) + 100
        connection = Mock()
        connection.getresponse.return_value = response
        with patch.object(c.socket, "getaddrinfo", return_value=ADDRESS) as dns, patch.object(c, "_TLS", return_value=connection) as tls:
            yield connection, response, dns, tls

    def test_fixed_https_get_one_connection_no_credentials(self):
        window = d.plan("2026-11-19T08:00:00Z")
        with self.exchange() as (connection, _, dns, tls):
            result = c._exchange("sh000001", window, c._Deadline())
        self.assertEqual(result, (b'{}', 102))
        dns.assert_called_once_with(d.HOST, 443, type=socket.SOCK_STREAM)
        tls.assert_called_once()
        args, kwargs = connection.request.call_args
        self.assertEqual(args, ("GET", d.request_path("sh000001", window)))
        self.assertNotIn("Cookie", kwargs["headers"])
        self.assertNotIn("Authorization", kwargs["headers"])
        self.assertEqual(kwargs["headers"]["Accept-Encoding"], "identity")
        connection.close.assert_called_once()

    def test_redirects_compression_duplicate_lengths_and_mime_rejected(self):
        tests = [(302, None, "http_status"), (200, [("Content-Encoding", "gzip")], "content_encoding"),
                 (200, [("Content-Type", "text/html")], "content_type"),
                 (200, [("Content-Length", "1"), ("Content-Length", "1")], "content_length"),
                 (200, [("Content-Type", "application/json"), ("Content-Length", "512001")], "content_length"),
                 (200, [("Content-Type", "application/json"), ("Content-Length", "2"), ("Transfer-Encoding", "chunked")], "transfer_encoding")]
        for status, headers, reason in tests:
            with self.subTest(reason=reason), self.exchange(status, headers), self.assertRaisesRegex(c._Failure, reason):
                c._exchange("sh000001", d.plan("2026-11-19T08:00:00Z"), c._Deadline())

    def test_truncated_body_and_size_cap(self):
        with self.exchange(headers=[("Content-Type", "application/json"), ("Content-Length", "3")]), self.assertRaisesRegex(c._Failure, "truncated_response"):
            c._exchange("sh000001", d.plan("2026-11-19T08:00:00Z"), c._Deadline())
        with self.exchange(headers=[("Content-Type", "application/json")], payload=b'x' * 512001), self.assertRaisesRegex(c._Failure, "response_size"):
            c._exchange("sh000001", d.plan("2026-11-19T08:00:00Z"), c._Deadline())

    def test_private_dns_never_connects(self):
        with patch.object(c.socket, "getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", 443))]), patch.object(c, "_TLS") as tls:
            with self.assertRaisesRegex(c._Failure, "private_dns"):
                c._fetch("sh000001", d.plan("2026-11-19T08:00:00Z"))
        tls.assert_not_called()

    def test_deadline_includes_dns_and_no_late_connect(self):
        def lookup(*_, **__):
            time.sleep(0.06)
            return ADDRESS
        started = time.monotonic()
        with patch.object(c, "TIMEOUT", .02), patch.object(c.socket, "getaddrinfo", side_effect=lookup), patch.object(c, "_TLS") as tls:
            with self.assertRaisesRegex(c._Failure, "transport_deadline"):
                c._fetch("sh000001", d.plan("2026-11-19T08:00:00Z"))
        self.assertLess(time.monotonic() - started, .4)
        tls.assert_not_called()

    def test_verified_tls_sni_and_cleanup(self):
        raw, wrapped, context = Mock(), Mock(), Mock()
        context.wrap_socket.return_value = wrapped
        deadline = c._Deadline()
        with patch.object(c.ssl, "create_default_context", return_value=context), patch.object(c.socket, "socket", return_value=raw):
            connection = c._TLS(ADDRESS[0], deadline)
            connection.connect()
            deadline.abort()
        context.wrap_socket.assert_called_once_with(raw, server_hostname=d.HOST, do_handshake_on_connect=False)
        wrapped.do_handshake.assert_called_once()
        raw.close.assert_called_once()
        wrapped.close.assert_called_once()

    def test_actual_http_response_framing_is_bounded(self):
        class Sock:
            def __init__(self, value):
                self.value = value
            def makefile(self, *_):
                return io.BytesIO(self.value)
        response = c._Response(Sock(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}'))
        response.begin()
        self.assertEqual(response.read1(), b'{}')
        self.assertGreater(response.bounded.count, 2)
        huge = c._Response(Sock(b'HTTP/1.1 200 OK\r\nX-Test: ' + b'x' * 17000 + b'\r\n\r\n'))
        with self.assertRaisesRegex(c._Failure, "header_size"):
            huge.begin()


if __name__ == "__main__":
    unittest.main()
