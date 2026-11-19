"""Authored wire fixtures parsed by the mandatory installed, pinned BaoStock SDK."""

from contextlib import contextmanager, ExitStack, redirect_stderr, redirect_stdout
from datetime import date, datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import types
import unittest
from unittest.mock import patch
import zlib

from market_intelligence import baostock_transport as transport


FIXED_NOW = datetime(2026, 11, 19, 3, 0, tzinfo=timezone.utc)
START = "2026-11-05"
END = "2026-11-18"


def frame(response_type, parts, *, compressed=False, version="00.9.40"):
    """Build authored server bytes, not a replacement product protocol client."""
    # This fixture explicitly declares UTF-8 octets for uncompressed bodies.
    # It is not independent evidence of the server's Unicode length convention.
    body = "\x01".join(parts).encode()
    if compressed:
        body = zlib.compress(body + b"\n")
        return version.encode() + b"\x01" + response_type.encode() + b"\x01" + f"{len(body):010d}".encode() + body + transport.TERMINATOR
    header = version.encode() + b"\x01" + response_type.encode() + b"\x01" + f"{len(body):010d}".encode()
    return header + body + b"\x01" + str(zlib.crc32(header + body)).encode() + transport.TERMINATOR


def response_parts(method, *, symbol=None, start=START, end=END, rows=None):
    parts = ["0", "success", method, "anonymous"]
    if method in {"login", "logout"}:
        return parts
    if rows is None:
        if method == "query_trade_dates":
            first = date.fromisoformat(start)
            rows = [[(first + timedelta(days=i)).isoformat(), "1" if (first + timedelta(days=i)).weekday() < 5 else "0"] for i in range(14)]
        elif method == "query_stock_basic":
            rows = [[symbol, "AUTHORED_INDEX_NOT_MARKET_DATA", "1990-01-01", "", "2", "1"]]
        else:
            rows = [[end, symbol, "100", "102", "98", "101", "100", "1000", "100000", "1"]]
    parts += ["1", "2000", json.dumps({"record": rows}, separators=(",", ":"))]
    if method == "query_trade_dates":
        parts += [start, end, ",".join(transport.CALENDAR_FIELDS)]
    elif method == "query_stock_basic":
        parts += [symbol, "", ",".join(transport.IDENTITY_FIELDS)]
    else:
        parts += [symbol, ",".join(transport.DAILY_FIELDS), start, end, "d", "3"]
    return parts


def wire_frames(*, start=START, end=END):
    result = [frame("01", response_parts("login")),
              frame("34", response_parts("query_trade_dates", start=start, end=end))]
    result.extend(frame("46", response_parts("query_stock_basic", symbol=symbol)) for symbol in transport.SYMBOLS)
    result.extend(frame("96", response_parts("query_history_k_data_plus", symbol=symbol, start=start, end=end), compressed=True) for symbol in transport.SYMBOLS)
    result.append(frame("03", response_parts("logout")))
    return result


class FakeSocket:
    def __init__(self, frames=None, *, send_size=4096, recv_size=8192):
        self.frames = list(wire_frames() if frames is None else frames)
        self.send_size, self.recv_size = send_size, recv_size
        self.sent = []
        self.pending = b""
        self.reading = b""
        self.connections = []
        self.timeouts = []
        self.closed = False
        self.close_calls = 0
        self.send_calls = 0
        self.recv_calls = 0

    def settimeout(self, value):
        self.timeouts.append(value)

    def connect(self, endpoint):
        self.connections.append(endpoint)

    def send(self, value):
        self.send_calls += 1
        count = min(len(value), self.send_size)
        self.pending += bytes(value[:count])
        if self.pending.endswith(b"\n"):
            self.sent.append(self.pending)
            self.pending = b""
            self.reading = self.frames.pop(0) if self.frames else b""
        return count

    def recv(self, size):
        self.recv_calls += 1
        count = min(size, self.recv_size)
        result, self.reading = self.reading[:count], self.reading[count:]
        return result

    def close(self):
        self.close_calls += 1
        self.closed = True


@contextmanager
def offline_session(frames=None, *, raw=None, now=FIXED_NOW):
    """Patch only product-private I/O seams; the SDK itself is never mocked."""
    raw = raw if raw is not None else FakeSocket(frames)
    with ExitStack() as stack:
        stack.enter_context(patch.object(transport, "_resolve_ipv4", return_value=["8.8.8.8"]))
        stack.enter_context(patch.object(transport, "_create_socket", return_value=raw))
        stack.enter_context(patch.object(transport, "_now", return_value=now))
        yield raw


class TransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Never let a fixture escape through another real networking path.
        def no_network(event, args):
            if event in {"socket.__new__", "socket.connect", "socket.getaddrinfo", "socket.gethostbyname"}:
                raise AssertionError("real network forbidden in offline SDK tests")
        sys.addaudithook(no_network)

    def run_wire(self, frames=None, callback=None, **kwargs):
        views = []
        with offline_session(frames, **kwargs) as raw:
            receipt = transport.run_once(accept_response=callback or views.append)
        self.assertEqual(transport._sdk_names(), [])
        self.assertTrue(receipt.bindings_restored)
        self.assertTrue(receipt.transport_payloads_disposed)
        return receipt, raw, views

    def assert_failed(self, receipt, reason, count=None):
        self.assertEqual((receipt.status, receipt.reason), ("FAILED", reason))
        if count is not None:
            self.assertEqual(receipt.requests_sent, count)

    def test_real_pinned_sdk_positive_seven_responses(self):
        receipt, raw, views = self.run_wire()
        self.assertEqual(receipt.status, "COMPLETE", receipt.reason)
        self.assertEqual(receipt.reason, "complete")
        self.assertEqual(receipt.requests_sent, 7)
        self.assertEqual(len(views), 7)
        self.assertEqual([v.response_type for v in views], ["01", "34", "46", "46", "96", "96", "03"])
        self.assertEqual([v.symbol for v in views[4:6]], list(transport.SYMBOLS))
        self.assertEqual(views[4].data[0][5], "101")
        self.assertEqual(views[1].page_number, 1)
        self.assertEqual(views[1].page_size, 2000)
        self.assertEqual(raw.connections, [("8.8.8.8", 10030)])
        self.assertEqual(raw.close_calls, 1)
        self.assertTrue(receipt.socket_closed)
        self.assertEqual((receipt.window_start, receipt.window_end), (START, END))
        self.assertEqual(receipt.wire_bytes, sum(map(len, wire_frames())))
        self.assertEqual(receipt.wire_sha256, hashlib.sha256(b"".join(wire_frames())).hexdigest())
        self.assertEqual(sum(r.wire_bytes for r in receipt.responses), receipt.wire_bytes)

    def test_short_sends_are_one_protocol_request_each(self):
        raw = FakeSocket(send_size=3, recv_size=7)
        receipt, _, views = self.run_wire(raw=raw)
        self.assertEqual(receipt.status, "COMPLETE", receipt.reason)
        self.assertEqual(receipt.requests_sent, 7)
        self.assertGreater(raw.send_calls, 7)
        self.assertTrue(all(0 < timeout <= 20 for timeout in raw.timeouts))
        self.assertEqual(len(views), 7)

    def test_module_import_without_site_packages_is_lazy(self):
        code = "import sys; from market_intelligence import baostock_transport; assert not any(n.split('.')[0] in {'baostock','pandas','numpy'} for n in sys.modules)"
        result = subprocess.run([sys.executable, "-B", "-S", "-c", code], capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0)

    def test_installed_sdk_all_raw_hashes_and_versions(self):
        sdk = transport._SDK()
        try:
            loaded = sdk.load()
            self.assertEqual(loaded.__version__, "00.9.40")
            self.assertEqual(transport.importlib.metadata.version("baostock"), "0.9.4")
            self.assertEqual(len(sdk.modules), 23)
        finally:
            self.assertTrue(sdk.close())

    def test_existing_sdk_context_is_not_reused_or_deleted(self):
        sentinel = types.ModuleType("baostock")
        with patch.dict(sys.modules, {"baostock": sentinel}):
            with offline_session() as raw:
                receipt = transport.run_once(accept_response=lambda _: None)
            self.assertIs(sys.modules["baostock"], sentinel)
        self.assert_failed(receipt, "sdk_context", 0)
        self.assertEqual(raw.connections, [])

    def test_forged_manifest_fails_before_import(self):
        with patch.object(transport, "MANIFEST_HASH", "0" * 64):
            receipt, raw, _ = self.run_wire()
        self.assert_failed(receipt, "sdk_integrity", 0)
        self.assertEqual(raw.connections, [])

    def test_changed_sdk_source_bytes_fail_preflight(self):
        original = transport._regular
        def changed(path, maximum):
            data = original(path, maximum)
            return data + b"# altered\n" if str(path).endswith("socketutil.py") else data
        with patch.object(transport, "_regular", side_effect=changed):
            receipt, _, _ = self.run_wire()
        self.assert_failed(receipt, "sdk_integrity", 0)

    def test_wrong_distribution_version_fails_preflight(self):
        with patch.object(transport.importlib.metadata, "distributions", return_value=[]):
            receipt, _, _ = self.run_wire()
        self.assert_failed(receipt, "sdk_integrity", 0)

    def test_unsupported_platform_fails_closed(self):
        with patch.object(transport.sys, "platform", "darwin"):
            receipt, _, _ = self.run_wire()
        self.assert_failed(receipt, "unsupported_platform", 0)

    def test_no_global_socket_or_zlib_monkeypatch(self):
        before_socket = dict(vars(socket))
        before_zlib = dict(vars(zlib))
        receipt, _, _ = self.run_wire()
        self.assertEqual(receipt.status, "COMPLETE", receipt.reason)
        self.assertEqual(dict(vars(socket)), before_socket)
        self.assertEqual(dict(vars(zlib)), before_zlib)

    def test_callback_rejection_stops_without_logout(self):
        def callback(view):
            if view.index == 2:
                raise ValueError("AUTHORED_PRIVATE_ERROR")
        receipt, raw, _ = self.run_wire(callback=callback)
        self.assert_failed(receipt, "callback_rejected", 2)
        self.assertEqual(len(receipt.responses), 2)
        self.assertFalse(any(b"logout" in request for request in raw.sent))
        self.assertNotIn("AUTHORED_PRIVATE_ERROR", json.dumps(receipt.to_dict()))

    def test_callback_must_return_none(self):
        receipt, raw, _ = self.run_wire(callback=lambda _: True)
        self.assert_failed(receipt, "callback_rejected", 1)
        self.assertEqual(len(raw.sent), 1)

    def test_callback_interruption_is_terminal(self):
        def callback(_):
            raise KeyboardInterrupt("AUTHORED_PRIVATE_ERROR")
        receipt, _, _ = self.run_wire(callback=callback)
        self.assert_failed(receipt, "interrupted", 1)

    def test_concurrent_entry_rejected_without_modifying_active_sdk(self):
        nested = []
        def callback(view):
            if view.index == 1:
                nested.append(transport.run_once(accept_response=lambda _: None))
        receipt, _, _ = self.run_wire(callback=callback)
        self.assertEqual(receipt.status, "COMPLETE", receipt.reason)
        self.assert_failed(nested[0], "concurrent_use", 0)

    def test_callback_time_counts_towards_deadline(self):
        mono = [100.0]
        def callback(_):
            mono[0] += 21
        with patch.object(transport, "_monotonic", side_effect=lambda: mono[0]):
            receipt, raw, _ = self.run_wire(callback=callback)
        self.assert_failed(receipt, "deadline", 1)
        self.assertEqual(len(raw.sent), 1)

    def test_wall_clock_rollback_stops_next_send(self):
        wall = [FIXED_NOW]
        def callback(_):
            wall[0] -= timedelta(seconds=1)
        with offline_session() as raw, patch.object(transport, "_now", side_effect=lambda: wall[0]):
            receipt = transport.run_once(accept_response=callback)
        self.assert_failed(receipt, "clock_rollback", 1)
        self.assertTrue(raw.closed)

    def test_monotonic_rollback_stops_next_send(self):
        mono = [100.0]
        def callback(_):
            mono[0] -= 1
        with patch.object(transport, "_monotonic", side_effect=lambda: mono[0]):
            receipt, _, _ = self.run_wire(callback=callback)
        self.assert_failed(receipt, "clock_rollback", 1)

    def test_private_and_mixed_dns_addresses_rejected(self):
        for addresses in (["127.0.0.1"], ["10.0.0.1"], ["8.8.8.8", "192.168.1.1"], ["224.0.0.1"], ["::1"], [], ["8.8.8.8"] * 65):
            with self.subTest(addresses=addresses):
                with offline_session() as raw, patch.object(transport, "_resolve_ipv4", return_value=addresses):
                    receipt = transport.run_once(accept_response=lambda _: None)
                self.assert_failed(receipt, "dns", 0)
                self.assertEqual(raw.connections, [])

    def test_dns_timeout_is_terminal_and_no_connection(self):
        with offline_session() as raw, patch.object(transport, "_resolve_ipv4", side_effect=subprocess.TimeoutExpired("resolver", 20)):
            receipt = transport.run_once(accept_response=lambda _: None)
        self.assert_failed(receipt, "deadline", 0)
        self.assertEqual(raw.connections, [])

    def test_failed_connection_swallowed_by_sdk_stays_failed(self):
        raw = FakeSocket()
        raw.connect = lambda _: (_ for _ in ()).throw(OSError("AUTHORED_PRIVATE_ERROR"))
        receipt, _, _ = self.run_wire(raw=raw)
        self.assert_failed(receipt, "connection", 0)
        self.assertTrue(raw.closed)

    def test_send_failure_count_is_ambiguous_attempt_not_zero(self):
        raw = FakeSocket()
        raw.send = lambda _: (_ for _ in ()).throw(OSError("AUTHORED_PRIVATE_ERROR"))
        receipt, _, _ = self.run_wire(raw=raw)
        self.assert_failed(receipt, "send", 1)

    def test_zero_short_send_fails_without_retry(self):
        raw = FakeSocket(send_size=0)
        receipt, _, _ = self.run_wire(raw=raw)
        self.assert_failed(receipt, "send", 1)
        self.assertEqual(raw.send_calls, 1)

    def test_eof_after_partial_response_preserves_wire_metadata(self):
        partial = wire_frames()[0][:25]
        receipt, raw, _ = self.run_wire([partial])
        self.assert_failed(receipt, "eof", 1)
        self.assertEqual(receipt.responses, ())
        self.assertEqual(receipt.wire_bytes, len(partial))
        self.assertEqual(receipt.wire_sha256, hashlib.sha256(partial).hexdigest())
        self.assertTrue(raw.closed)

    def test_actual_wrong_response_type_rejected_before_callback(self):
        frames = wire_frames()
        frames[4] = frames[4][:8] + b"34" + frames[4][10:]
        receipt, _, views = self.run_wire(frames)
        self.assert_failed(receipt, "protocol", 5)
        self.assertEqual(len(views), 4)

    def test_actual_wrong_response_version_rejected(self):
        frames = wire_frames()
        frames[0] = b"00.9.41" + frames[0][7:]
        receipt, _, _ = self.run_wire(frames)
        self.assert_failed(receipt, "protocol", 1)

    def test_bad_declared_frame_length_rejected(self):
        frames = wire_frames()
        frames[0] = frames[0][:11] + b"9999999999" + frames[0][21:]
        receipt, _, _ = self.run_wire(frames)
        self.assert_failed(receipt, "frame_limit", 1)

    def test_frame_cap_is_enforced_before_growth(self):
        frames = [b"00.9.40\x0101\x010000000001" + b"x" * transport.MAX_FRAME]
        receipt, _, _ = self.run_wire(frames)
        self.assert_failed(receipt, "frame_limit", 1)
        self.assertEqual(receipt.wire_bytes, transport.MAX_FRAME)

    def test_total_wire_cap_is_enforced(self):
        with patch.object(transport, "MAX_WIRE", 100):
            receipt, _, _ = self.run_wire()
        self.assert_failed(receipt, "wire_limit", 2)
        self.assertEqual(receipt.wire_bytes, 100)

    def test_concatenated_wire_frames_rejected(self):
        frames = wire_frames()
        frames[0] += frames[0]
        receipt, _, _ = self.run_wire(frames)
        self.assert_failed(receipt, "protocol", 1)

    def test_compression_bomb_is_bounded_before_sdk_json_decode(self):
        frames = wire_frames()
        blob = zlib.compress(b"x" * (transport.MAX_DECODED + 1))
        frames[4] = b"00.9.40\x0196\x01" + f"{len(blob):010d}".encode() + blob + transport.TERMINATOR
        receipt, _, _ = self.run_wire(frames)
        self.assert_failed(receipt, "decoded_limit", 5)
        self.assertLessEqual(receipt.decoded_bytes, transport.MAX_DECODED)

    def test_truncated_trailing_concatenated_compressed_streams(self):
        original = wire_frames()[4][21:-len(transport.TERMINATOR)]
        for blob in (original[:-2], original + b"extra", original + zlib.compress(b"extra"), b"not-zlib"):
            with self.subTest(size=len(blob)):
                frames = wire_frames()
                frames[4] = b"00.9.40\x0196\x01" + f"{len(blob):010d}".encode() + blob + transport.TERMINATOR
                receipt, _, _ = self.run_wire(frames)
                self.assert_failed(receipt, "compression", 5)

    def test_provider_error_is_sanitized_and_terminal(self):
        frames = wire_frames()
        frames[0] = frame("01", ["9", "AUTHORED_PRIVATE_ERROR", "login", "anonymous"])
        receipt, raw, _ = self.run_wire(frames)
        self.assert_failed(receipt, "provider_error", 1)
        self.assertEqual(len(raw.sent), 1)
        self.assertNotIn("AUTHORED_PRIVATE_ERROR", json.dumps(receipt.to_dict()))

    def test_method_user_and_echo_parameters_are_checked(self):
        for index, replacement in ((2, "unexpected_method"), (3, "other_user"), (7, "sz.399001"), (9, "1900-01-01"), (10, "1900-01-02"), (11, "5"), (12, "2")):
            with self.subTest(index=index):
                parts = response_parts("query_history_k_data_plus", symbol="sh.000001")
                parts[index] = replacement
                frames = wire_frames()
                frames[4] = frame("96", parts, compressed=True)
                receipt, _, _ = self.run_wire(frames)
                self.assert_failed(receipt, "protocol", 5)

    def test_fields_rows_and_cell_limits_are_checked(self):
        for rows in ([["too-short"]], [[END, "sh.000001", "x" * 129] + ["1"] * 7], [[END, "sh.000001", 100] + ["1"] * 7], [[END, "sh.000001", "bad\ntext"] + ["1"] * 7]):
            with self.subTest(rows=rows):
                parts = response_parts("query_history_k_data_plus", symbol="sh.000001", rows=rows)
                frames = wire_frames()
                frames[4] = frame("96", parts, compressed=True)
                receipt, _, _ = self.run_wire(frames)
                self.assert_failed(receipt, "protocol", 5)

    def test_full_page_or_wrong_page_prevents_pagination(self):
        for page, count, rows in (("2", "2000", []), ("1", "1", []), ("1", "2000", [["x", "1"]] * 2000)):
            parts = response_parts("query_trade_dates", rows=rows)
            parts[4], parts[5] = page, count
            frames = wire_frames()
            frames[1] = frame("34", parts)
            receipt, raw, _ = self.run_wire(frames)
            self.assert_failed(receipt, "pagination", 2)
            self.assertEqual(len(raw.sent), 2)

    def test_logout_failure_is_not_complete(self):
        frames = wire_frames()
        frames[-1] = b""
        receipt, _, views = self.run_wire(frames)
        self.assert_failed(receipt, "eof", 7)
        self.assertEqual(len(views), 6)

    def test_cleanup_failure_cannot_claim_closed(self):
        raw = FakeSocket()
        raw.close = lambda: (_ for _ in ()).throw(OSError("AUTHORED_PRIVATE_ERROR"))
        receipt, _, _ = self.run_wire(raw=raw)
        self.assert_failed(receipt, "cleanup", 7)
        self.assertFalse(receipt.socket_closed)

    def test_sdk_outputs_and_raw_values_do_not_escape(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            receipt, _, views = self.run_wire()
        self.assertEqual((stdout.getvalue(), stderr.getvalue()), ("", ""))
        serialized = json.dumps(receipt.to_dict())
        self.assertNotIn("AUTHORED_INDEX_NOT_MARKET_DATA", serialized)
        self.assertNotIn('"data"', serialized)
        self.assertNotIn('"fields"', serialized)
        self.assertNotIn("AUTHORED_INDEX_NOT_MARKET_DATA", repr(views[2]))
        self.assertIs(type(receipt.to_dict()["responses"]), list)

    def test_sdk_bindings_and_singleton_restored_on_callback_failure(self):
        captured = []
        def callback(_):
            module = sys.modules["baostock.util.socketutil"]
            captured.append((module, sys.modules["baostock.common.context"]))
            raise ValueError("reject")
        receipt, _, _ = self.run_wire(callback=callback)
        self.assert_failed(receipt, "callback_rejected", 1)
        module, context = captured[0]
        self.assertIs(module.socket, socket)
        self.assertIs(module.zlib, zlib)
        self.assertIsNone(module.SocketUtil.instance)
        self.assertIs(module.SocketUtil.init_flag, False)
        self.assertFalse(any(not name.startswith("__") for name in vars(context)))

    def test_shadowed_sdk_origin_rejected_before_execution(self):
        real = transport.importlib.util.find_spec
        def shadow(name, *args):
            if name == "baostock":
                return types.SimpleNamespace(origin=str(Path.cwd() / "baostock.py"))
            return real(name, *args)
        with patch.object(transport.importlib.util, "find_spec", side_effect=shadow):
            receipt, _, _ = self.run_wire()
        self.assert_failed(receipt, "sdk_integrity", 0)

    def test_unknown_sdk_file_or_bytecode_rejected(self):
        root = Path(transport.sysconfig.get_path("purelib")) / "baostock"
        for name in ("unexpected.py", "__init__.pyc", "native.pyd"):
            with self.subTest(name=name):
                with patch.object(transport.os, "walk", return_value=[(str(root), [], [name])]):
                    receipt, _, _ = self.run_wire()
                self.assert_failed(receipt, "sdk_integrity", 0)

    def test_context_contamination_during_import_fails_without_key_read(self):
        original = transport.importlib.import_module
        def contaminated(name, *args, **kwargs):
            result = original(name, *args, **kwargs)
            if name == "baostock":
                sys.modules["baostock.common.context"].apiKey = object()
            return result
        with patch.object(transport.importlib, "import_module", side_effect=contaminated):
            receipt, raw, _ = self.run_wire()
        self.assert_failed(receipt, "sdk_context", 0)
        self.assertEqual(raw.sent, [])

    def test_import_failure_unloads_partial_sdk_and_restores_stdout(self):
        original = transport.importlib.import_module
        def broken(name, *args, **kwargs):
            result = original(name, *args, **kwargs)
            if name == "baostock":
                raise RuntimeError("AUTHORED_PRIVATE_ERROR")
            return result
        stdout, stderr = sys.stdout, sys.stderr
        with patch.object(transport.importlib, "import_module", side_effect=broken):
            receipt, _, _ = self.run_wire()
        self.assert_failed(receipt, "sdk_integrity", 0)
        self.assertIs(sys.stdout, stdout)
        self.assertIs(sys.stderr, stderr)

    def test_wrong_loaded_module_identity_rejected(self):
        original = transport.importlib.import_module
        def changed(name, *args, **kwargs):
            result = original(name, *args, **kwargs)
            if name == "baostock":
                sys.modules["baostock.security.history"].__file__ = "shadow.py"
            return result
        with patch.object(transport.importlib, "import_module", side_effect=changed):
            receipt, _, _ = self.run_wire()
        self.assert_failed(receipt, "sdk_integrity", 0)

    def test_dns_return_after_deadline_never_connects(self):
        mono = [100.0]
        def resolve(_):
            mono[0] += 21
            return ["8.8.8.8"]
        with offline_session() as raw, patch.object(transport, "_monotonic", side_effect=lambda: mono[0]), patch.object(transport, "_resolve_ipv4", side_effect=resolve):
            receipt = transport.run_once(accept_response=lambda _: None)
        self.assert_failed(receipt, "deadline", 0)
        self.assertEqual(raw.connections, [])

    def test_dns_worker_has_deadline_and_no_site_loading(self):
        result = types.SimpleNamespace(stdout=b'["8.8.8.8"]')
        with patch.object(transport.subprocess, "run", return_value=result) as run:
            self.assertEqual(transport._resolve_ipv4(1.25), ["8.8.8.8"])
        args, options = run.call_args
        self.assertEqual(args[0][1:5], ["-I", "-S", "-B", "-c"])
        self.assertEqual(options["timeout"], 1.25)
        self.assertIs(options["check"], True)
        self.assertEqual(options["stderr"], subprocess.DEVNULL)

    def test_receive_error_swallowed_by_sdk_stays_terminal(self):
        raw = FakeSocket()
        raw.recv = lambda _: (_ for _ in ()).throw(OSError("AUTHORED_PRIVATE_ERROR"))
        receipt, _, _ = self.run_wire(raw=raw)
        self.assert_failed(receipt, "recv", 1)
        self.assertTrue(raw.closed)

    def test_receive_return_after_deadline_counts_bytes_but_not_response(self):
        raw = FakeSocket()
        mono = [100.0]
        real = raw.recv
        def late(size):
            result = real(size)
            mono[0] += 21
            return result
        raw.recv = late
        with patch.object(transport, "_monotonic", side_effect=lambda: mono[0]):
            receipt, _, _ = self.run_wire(raw=raw)
        self.assert_failed(receipt, "deadline", 1)
        self.assertGreater(receipt.wire_bytes, 0)
        self.assertEqual(receipt.responses, ())

    def test_protocol_request_limit_blocks_before_os_send(self):
        with offline_session():
            state = transport._Session(transport._Budget())
            state.begin("login", None, "00", "01")
            sock = transport._BoundedSocket(state)
            sock.connect((transport.HOST, transport.PORT))
            state.requests = transport.MAX_REQUESTS
            with self.assertRaises(transport._Fault) as caught:
                sock.send(b"00.9.40\x0100\x010000000010login\x01anonymous\n")
            self.assertEqual(caught.exception.reason, "protocol")
            self.assertEqual(sock.raw.send_calls, 0)
            sock.close()

    def test_vip_reconnection_and_second_socket_rejected(self):
        with offline_session():
            state = transport._Session(transport._Budget())
            module = transport._SocketModule(state)
            sock = module.socket(socket.AF_INET, socket.SOCK_STREAM)
            with self.assertRaises(transport._Fault):
                sock.connect(("vip-api.baostock.com", 10030))
        with offline_session():
            state = transport._Session(transport._Budget())
            module = transport._SocketModule(state)
            sock = module.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect((transport.HOST, transport.PORT))
            with self.assertRaises(transport._Fault):
                module.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.close()
        with offline_session():
            state = transport._Session(transport._Budget())
            sock = transport._BoundedSocket(state)
            sock.connect((transport.HOST, transport.PORT))
            with self.assertRaises(transport._Fault):
                sock.connect((transport.HOST, transport.PORT))
            self.assertEqual(len(sock.raw.connections), 1)
            sock.close()

    def test_sends_are_fixed_seven_methods_and_anonymous_only(self):
        receipt, raw, _ = self.run_wire()
        self.assertEqual(receipt.status, "COMPLETE", receipt.reason)
        bodies = [request[21:].split(b"\x01") for request in raw.sent]
        self.assertEqual([body[0].decode() for body in bodies], [
            "login", "query_trade_dates", "query_stock_basic", "query_stock_basic",
            "query_history_k_data_plus", "query_history_k_data_plus", "logout"])
        self.assertTrue(all(body[1] == b"anonymous" for body in bodies))
        self.assertEqual(bodies[0][3], b"0")

    def test_staggered_response_times_preserved_before_callback(self):
        wall = [FIXED_NOW]
        raw = FakeSocket()
        original = raw.recv
        def later(size):
            wall[0] += timedelta(microseconds=1)
            return original(size)
        raw.recv = later
        views = []
        def callback(view):
            views.append(view)
            wall[0] += timedelta(microseconds=1)
        with offline_session(raw=raw), patch.object(transport, "_now", side_effect=lambda: wall[0]):
            receipt = transport.run_once(accept_response=callback)
        self.assertEqual(receipt.status, "COMPLETE", receipt.reason)
        self.assertLess(views[0].completed_at, views[1].first_seen_at)
        self.assertLess(views[5].completed_at, views[6].completed_at)
        self.assertLess(views[6].completed_at, receipt.finished_at)

    def test_response_view_is_immutable_and_not_an_sdk_object(self):
        receipt, _, views = self.run_wire()
        self.assertEqual(receipt.status, "COMPLETE", receipt.reason)
        self.assertIs(type(views[4].data), tuple)
        self.assertTrue(all(type(row) is tuple for row in views[4].data))
        with self.assertRaises(AttributeError):
            views[4].data = ()
        self.assertFalse(hasattr(views[4], "next"))
        self.assertFalse(hasattr(views[4], "socket"))

    def test_noncallable_callback_is_rejected_without_sdk_load(self):
        with offline_session() as raw:
            receipt = transport.run_once(accept_response=None)
        self.assert_failed(receipt, "callback_rejected", 0)
        self.assertEqual(raw.connections, [])
        self.assertEqual(transport._sdk_names(), [])

    def test_transport_has_no_file_writes_or_credential_file_reads(self):
        active = [True]
        def guard(event, args):
            if active[0] and event == "open":
                path, mode, flags = args
                if (isinstance(mode, str) and any(char in mode for char in "wax+")) or flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
                    raise AssertionError("transport file write forbidden")
                if isinstance(path, str) and Path(path).name.lower() in {".netrc", "_netrc", "credentials", ".env"}:
                    raise AssertionError("credential file read forbidden")
        sys.addaudithook(guard)
        try:
            receipt, _, _ = self.run_wire()
            self.assertEqual(receipt.status, "COMPLETE", receipt.reason)
        finally:
            active[0] = False

    def test_malformed_json_or_unicode_is_terminal_without_output(self):
        for content in (b"not-json", b"\xff"):
            frames = wire_frames()
            parts = response_parts("query_history_k_data_plus", symbol="sh.000001")
            parts[6] = "PLACEHOLDER"
            body = "\x01".join(parts).encode().replace(b"PLACEHOLDER", content) + b"\n"
            blob = zlib.compress(body)
            frames[4] = b"00.9.40\x0196\x01" + f"{len(blob):010d}".encode() + blob + transport.TERMINATOR
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                receipt, raw, _ = self.run_wire(frames)
            self.assert_failed(receipt, "protocol", 5)
            self.assertEqual(len(raw.sent), 5)
            self.assertEqual((stdout.getvalue(), stderr.getvalue()), ("", ""))

    def _r1_rejected(self, frames, request_count):
        stdout, stderr = io.StringIO(), io.StringIO()
        captured = []
        original_bind = transport._SDK.bind
        def bind(sdk, state):
            cls = sdk.modules["baostock.data.resultset"].ResultData
            captured.append((cls, cls.setData, sdk.socketutil, sdk.context, state))
            return original_bind(sdk, state)
        with patch.object(transport._SDK, "bind", bind), redirect_stdout(stdout), redirect_stderr(stderr):
            receipt, raw, views = self.run_wire(frames)
        self.assert_failed(receipt, "protocol", request_count)
        self.assertEqual(len(raw.sent), request_count)
        self.assertEqual(len(views), request_count - 1)
        self.assertEqual(len(receipt.responses), request_count - 1)
        self.assertFalse(any(b"logout" in request for request in raw.sent))
        self.assertTrue(raw.closed)
        self.assertTrue(receipt.socket_closed)
        self.assertEqual((stdout.getvalue(), stderr.getvalue()), ("", ""))
        cls, original, module, context, state = captured[0]
        self.assertIs(cls.setData, original)
        self.assertIs(module.socket, socket)
        self.assertIs(module.zlib, zlib)
        self.assertIsNone(module.SocketUtil.instance)
        self.assertFalse(any(not key.startswith("__") for key in vars(context)))
        self.assertIsNone(state.plain_frame)
        self.assertIsNone(state.header)
        self.assertIsNone(state.tail)
        self.assertIsNone(state.socket)
        self.assertEqual(state.reason, "protocol")
        return receipt

    def test_r1_original_code_and_numeric_spaces_cannot_be_repaired(self):
        for column, value in ((1, "sh. 000001"), (5, "10 1"), (0, "2026-11- 18"),
                              (2, " 100"), (6, "100 "), (7, "1\u00a0000")):
            with self.subTest(column=column, value=value):
                parts = response_parts("query_history_k_data_plus", symbol=transport.SYMBOLS[0])
                parsed = json.loads(parts[6])
                parsed["record"][0][column] = value
                parts[6] = json.dumps(parsed)
                frames = wire_frames()
                frames[4] = frame("96", parts, compressed=True)
                self._r1_rejected(frames, 5)

    def test_r1_joint_reviewer_whitespace_repro(self):
        parts = response_parts("query_history_k_data_plus", symbol=transport.SYMBOLS[0])
        parsed = json.loads(parts[6])
        parsed["record"][0][1], parsed["record"][0][5] = "sh. 000001", "10 1"
        parts[6] = json.dumps(parsed)
        frames = wire_frames()
        frames[4] = frame("96", parts, compressed=True)
        self._r1_rejected(frames, 5)

    def test_r1_calendar_and_identity_cells_checked_before_sdk(self):
        for index, method, symbol, column, value in (
            (1, "query_trade_dates", None, 0, "2026-11- 05"),
            (1, "query_trade_dates", None, 1, " 1"),
            (2, "query_stock_basic", transport.SYMBOLS[0], 0, "sh. 000001"),
            (2, "query_stock_basic", transport.SYMBOLS[0], 4, "2 "),
            (2, "query_stock_basic", transport.SYMBOLS[0], 5, " 1"),
        ):
            with self.subTest(index=index, column=column):
                parts = response_parts(method, symbol=symbol)
                parsed = json.loads(parts[6])
                parsed["record"][0][column] = value
                parts[6] = json.dumps(parsed)
                frames = wire_frames()
                frames[index] = frame("34" if index == 1 else "46", parts)
                self._r1_rejected(frames, index + 1)

    def test_r1_escaped_cell_whitespace_cannot_bypass_raw_validation(self):
        for encoded in ('"sh.\\u0020000001"', '"sh.\\u00a0000001"', '"sh.\\t000001"'):
            parts = response_parts("query_history_k_data_plus", symbol=transport.SYMBOLS[0])
            parts[6] = parts[6].replace('"sh.000001"', encoded)
            frames = wire_frames()
            frames[4] = frame("96", parts, compressed=True)
            self._r1_rejected(frames, 5)

    def test_r1_malformed_json_is_rejected_before_sdk_can_repair_it(self):
        for document in ('{"record": [[1 0]]}', '{"record": [["bad\nvalue"]]}',
                         '{"record": [["bad\tvalue"]]}', '{"rec ord": []}', " \t", "[]", "null"):
            with self.subTest(document=document):
                parts = response_parts("query_history_k_data_plus", symbol=transport.SYMBOLS[0])
                parts[6] = document
                frames = wire_frames()
                frames[4] = frame("96", parts, compressed=True)
                self._r1_rejected(frames, 5)

    def test_r1_duplicate_json_keys_and_nonstandard_constants_are_rejected(self):
        for document in ('{"record": [], "record": []}', '{"record": [], "rec\\u006frd": []}',
                         '{"record": [], "extra": 1}', '{"record": NaN}',
                         '{"record": Infinity}', '{"record": -Infinity}'):
            with self.subTest(document=document):
                parts = response_parts("query_history_k_data_plus", symbol=transport.SYMBOLS[0])
                parts[6] = document
                frames = wire_frames()
                frames[4] = frame("96", parts, compressed=True)
                self._r1_rejected(frames, 5)

    def test_r1_unpaired_surrogate_cell_is_rejected(self):
        parts = response_parts("query_stock_basic", symbol=transport.SYMBOLS[0])
        parsed = json.loads(parts[6])
        parsed["record"][0][1] = "\ud800"
        parts[6] = json.dumps(parsed)
        frames = wire_frames()
        frames[2] = frame("46", parts)
        self._r1_rejected(frames, 3)

    def test_r1_opaque_name_spaces_survive_real_sdk_parser(self):
        for name in ("AUTHORED Index Name", "\u4e0a\u8bc1 \u6307\u6570", "\u4e0a\u8bc1\u3000\u6307\u6570", "\U00020000 Index"):
            with self.subTest(name=name):
                parts = response_parts("query_stock_basic", symbol=transport.SYMBOLS[0])
                parsed = json.loads(parts[6])
                parsed["record"][0][1] = name
                parts[6] = json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
                frames = wire_frames()
                frames[2] = frame("46", parts)
                receipt, _, views = self.run_wire(frames, raw=FakeSocket(frames, recv_size=1))
                self.assertEqual(receipt.status, "COMPLETE", receipt.reason)
                self.assertEqual(views[2].data[0][1], name)
                self.assertNotIn(name, json.dumps(receipt.to_dict(), ensure_ascii=False))

    def test_r1_formatted_json_preserves_all_original_cells(self):
        parts = response_parts("query_history_k_data_plus", symbol=transport.SYMBOLS[0])
        parsed = json.loads(parts[6])
        parts[6] = json.dumps(parsed, indent=4)
        frames = wire_frames()
        frames[4] = frame("96", parts, compressed=True)
        receipt, _, views = self.run_wire(frames)
        self.assertEqual(receipt.status, "COMPLETE", receipt.reason)
        self.assertEqual(views[4].data, tuple(tuple(row) for row in parsed["record"]))

    def test_r1_empty_json_record_remains_a_valid_empty_response(self):
        frames = wire_frames()
        parts = response_parts("query_history_k_data_plus", symbol=transport.SYMBOLS[0], rows=[])
        frames[4] = frame("96", parts, compressed=True)
        receipt, _, views = self.run_wire(frames)
        self.assertEqual(receipt.status, "COMPLETE", receipt.reason)
        self.assertEqual(views[4].data, ())

    def test_r1_zero_or_short_body_length_with_recomputed_crc_is_rejected(self):
        body = "\x01".join(response_parts("login")).encode()
        for length in (0, len(body) - 1, len(body) + 1):
            with self.subTest(length=length):
                header = b"00.9.40\x0101\x01" + f"{length:010d}".encode()
                frames = wire_frames()
                frames[0] = header + body + b"\x01" + str(zlib.crc32(header + body)).encode() + transport.TERMINATOR
                self._r1_rejected(frames, 1)

    def test_r1_unexpected_uncompressed_tail_with_valid_crc_is_rejected(self):
        body = "\x01".join(response_parts("login")).encode()
        header = b"00.9.40\x0101\x01" + f"{len(body):010d}".encode()
        for extra in (b"unexpected", b"\x01unexpected", b"\x01", b"\n"):
            with self.subTest(extra=extra):
                changed = header + body + extra
                frames = wire_frames()
                frames[0] = changed + b"\x01" + str(zlib.crc32(changed)).encode() + transport.TERMINATOR
                self._r1_rejected(frames, 1)

    def test_r1_illegal_or_wrong_crc_trailer_is_rejected(self):
        body = "\x01".join(response_parts("login")).encode()
        header = b"00.9.40\x0101\x01" + f"{len(body):010d}".encode()
        good_crc = str(zlib.crc32(header + body)).encode()
        for trailer in (b"", b"\x010", b"\x01+" + good_crc, b"\x010" + good_crc,
                        b"\x01" + good_crc + b"garbage", b"\x014294967296", good_crc):
            with self.subTest(trailer=trailer):
                frames = wire_frames()
                frames[0] = header + body + trailer + transport.TERMINATOR
                self._r1_rejected(frames, 1)

    def test_r1_unicode_byte_and_character_lengths_have_exact_positive_cases(self):
        parts = response_parts("query_stock_basic", symbol=transport.SYMBOLS[0])
        parsed = json.loads(parts[6])
        parsed["record"][0][1] = "\u4e0a\u8bc1\U00020000\u6307\u6570"
        parts[6] = json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
        text = "\x01".join(parts)
        body = text.encode()
        self.assertNotEqual(len(text), len(body))
        for length in (len(text), len(body)):
            header = b"00.9.40\x0146\x01" + f"{length:010d}".encode()
            frames = wire_frames()
            frames[2] = header + body + b"\x01" + str(zlib.crc32(header + body)).encode() + transport.TERMINATOR
            receipt, _, views = self.run_wire(raw=FakeSocket(frames, recv_size=2))
            self.assertEqual(receipt.status, "COMPLETE", receipt.reason)
            self.assertEqual(views[2].data[0][1], parsed["record"][0][1])

    def test_r1_unicode_lengths_between_characters_and_bytes_are_not_tolerated(self):
        parts = response_parts("query_stock_basic", symbol=transport.SYMBOLS[0])
        parts[6] = parts[6].replace("AUTHORED_INDEX_NOT_MARKET_DATA", "\u4e0a\u8bc1\u6307\u6570")
        text = "\x01".join(parts)
        body = text.encode()
        for length in (0, len(text) - 1, len(text) + 1, len(body) - 1, len(body) + 1):
            header = b"00.9.40\x0146\x01" + f"{length:010d}".encode()
            frames = wire_frames()
            frames[2] = header + body + b"\x01" + str(zlib.crc32(header + body)).encode() + transport.TERMINATOR
            self._r1_rejected(frames, 3)

    def test_r1_new_sdk_guard_restored_after_success_and_callback_failure(self):
        for fail_callback in (False, True):
            captured = []
            original_bind = transport._SDK.bind
            def bind(sdk, state):
                cls = sdk.modules["baostock.data.resultset"].ResultData
                captured.append((cls, cls.setData, state))
                return original_bind(sdk, state)
            def accept(view):
                if fail_callback and view.index == 3:
                    raise ValueError("authored callback rejection")
            with patch.object(transport._SDK, "bind", bind):
                receipt, _, _ = self.run_wire(callback=accept)
            self.assertEqual(receipt.status, "FAILED" if fail_callback else "COMPLETE")
            cls, original, state = captured[0]
            self.assertIs(cls.setData, original)
            self.assertIsNone(state.plain_frame)


class RecoveryTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        TransportTests.setUpClass()

    def run_diagnostic(self, frames=None, **kwargs):
        views = []
        with offline_session(frames, **kwargs) as raw:
            receipt, diagnostic = transport.run_recovery(phase="diagnostic", accept_response=views.append)
        self.assertTrue(receipt.socket_closed)
        self.assertTrue(receipt.bindings_restored)
        self.assertTrue(receipt.transport_payloads_disposed)
        self.assertEqual(transport._sdk_names(), [])
        self.assertLessEqual(receipt.requests_sent, 1)
        self.assertEqual(set(diagnostic), set(transport.empty_diagnostic()))
        return receipt, diagnostic, raw, views

    def test_diagnostic_only_login_and_no_logout(self):
        receipt, d, raw, views = self.run_diagnostic()
        self.assertEqual(receipt.status, "COMPLETE")
        self.assertEqual(len(raw.sent), 1)
        self.assertEqual([v.method for v in views], ["login"])
        self.assertIsNone(d["first_failure_point"])
        for field in ("header_structure", "declared_length_syntax", "utf8", "crc_syntax",
                      "crc_match", "length_bytes_match", "length_characters_match"):
            self.assertEqual(d[field], "pass", field)

    def test_version_failure_is_located_without_acceptance(self):
        receipt, d, _, views = self.run_diagnostic([frame("01", response_parts("login"), version="99.9.99")])
        self.assertEqual(receipt.reason, "protocol")
        self.assertEqual(d["first_failure_point"], "response_version")
        self.assertEqual(d["version"], "other")
        self.assertEqual(d["crc_match"], "pass")
        self.assertEqual(views, [])
        self.assertNotIn("99.9.99", json.dumps(d))

    def test_exception_response_is_category_not_error_text(self):
        receipt, d, _, views = self.run_diagnostic([frame("04", ["7", "PRIVATE_ERROR_MESSAGE"])])
        self.assertEqual(receipt.reason, "protocol")
        self.assertEqual(d["first_failure_point"], "response_type")
        self.assertEqual(d["response_type"], "exception_04")
        self.assertNotIn("PRIVATE", json.dumps(d))
        self.assertEqual(views, [])

    def test_invalid_length_syntax_retains_unreached_distinction(self):
        wire = frame("01", response_parts("login"))
        receipt, d, _, _ = self.run_diagnostic([wire[:11] + b"XXXXXXXXXX" + wire[21:]])
        self.assertEqual(receipt.reason, "protocol")
        self.assertEqual(d["first_failure_point"], "length_syntax")
        self.assertIsNone(d["declared_length"])
        self.assertEqual(d["length_bytes_match"], "not_reached")

    def test_actual_crc_mismatch_is_latched(self):
        wire = frame("01", response_parts("login")).replace(b"success", b"succeSs")
        receipt, d, _, _ = self.run_diagnostic([wire])
        self.assertEqual(receipt.reason, "protocol")
        self.assertEqual(d["first_failure_point"], "plain_crc")
        self.assertEqual(d["crc_match"], "fail")

    def test_body_length_mismatch_preserves_both_candidates(self):
        wire = frame("01", response_parts("login"))
        head_body = (wire[:11] + b"0000000000" + wire[21:]).split(transport.TERMINATOR)[0].rpartition(b"\x01")[0]
        wire = head_body + b"\x01" + str(zlib.crc32(head_body)).encode() + transport.TERMINATOR
        receipt, d, _, _ = self.run_diagnostic([wire])
        self.assertEqual(receipt.reason, "protocol")
        self.assertEqual(d["first_failure_point"], "plain_length")
        self.assertEqual(d["crc_match"], "pass")
        self.assertEqual(d["length_bytes_match"], "fail")
        self.assertEqual(d["length_characters_match"], "fail")

    def test_fragmented_diagnostic_has_same_closed_result(self):
        _, expected, _, _ = self.run_diagnostic()
        _, actual, _, _ = self.run_diagnostic(raw=FakeSocket(recv_size=1))
        self.assertEqual(actual, expected)

    def test_partial_response_does_not_fabricate_completed_checks(self):
        receipt, d, _, _ = self.run_diagnostic([b"00.9"])
        self.assertEqual(receipt.reason, "eof")
        self.assertEqual(d["first_failure_point"], "transport_eof")
        self.assertEqual(d["header_structure"], "not_reached")
        self.assertEqual(d["crc_match"], "not_reached")
        self.assertEqual(d["utf8"], "indeterminate")

    def test_invalid_phase_never_loads_sdk(self):
        with patch.object(transport._SDK, "load") as load:
            with self.assertRaises(ValueError):
                transport.run_recovery(phase="retry", accept_response=lambda _: None)
            load.assert_not_called()

    def test_verification_uses_seven_requests_and_unchanged_sdk(self):
        views = []
        with offline_session() as raw:
            receipt, d = transport.run_recovery(phase="verification", accept_response=views.append)
        self.assertEqual(receipt.status, "COMPLETE")
        self.assertEqual((len(raw.sent), len(views)), (7, 7))
        self.assertEqual(d["method"], "logout")

    def test_failure_point_survives_cleanup_failure(self):
        class BadClose(FakeSocket):
            def close(self):
                raise OSError("PRIVATE_CLEANUP_ERROR")
        raw = BadClose([frame("04", ["7", "PRIVATE_PROVIDER_ERROR"])])
        with offline_session(raw=raw):
            receipt, d = transport.run_recovery(phase="diagnostic", accept_response=lambda _: None)
        self.assertEqual(receipt.reason, "cleanup")
        self.assertEqual(d["first_failure_point"], "response_type")
        self.assertNotIn("PRIVATE", json.dumps(d))

    def assert_direct_failure_survives_cleanup(self, error, expected):
        def login():
            raise error

        def bind(sdk, state):
            def close():
                state.fail("cleanup")
            state.socket = types.SimpleNamespace(closed=False, close=close)

        with patch.object(transport._SDK, "load", return_value=types.SimpleNamespace(login=login)), \
                patch.object(transport._SDK, "bind", bind), \
                patch.object(transport._SDK, "close", return_value=True), \
                patch.object(transport, "_create_socket") as connect:
            receipt, d = transport.run_recovery(phase="diagnostic", accept_response=lambda _: None)
        connect.assert_not_called()
        self.assertEqual(receipt.status, "FAILED")
        self.assertEqual(receipt.reason, "cleanup")
        self.assertFalse(receipt.socket_closed)
        self.assertTrue(receipt.bindings_restored)
        self.assertTrue(receipt.transport_payloads_disposed)
        self.assertEqual(receipt.requests_sent, 0)
        self.assertEqual(d["first_failure_point"], expected)
        self.assertEqual(d["header_structure"], "not_reached")
        self.assertNotIn("PRIVATE", json.dumps(d))

    def test_p4d_r1_direct_sdk_exception_precedes_cleanup_failure(self):
        self.assert_direct_failure_survives_cleanup(IndexError("PRIVATE_SDK_ERROR"), "transport_protocol")

    def test_p4d_r1_direct_fault_precedes_cleanup_failure(self):
        self.assert_direct_failure_survives_cleanup(transport._Fault("sdk_integrity"), "transport_sdk_integrity")

    def test_p4d_r1_direct_interruptions_precede_cleanup_failure(self):
        for error in (KeyboardInterrupt("PRIVATE_INTERRUPT"), SystemExit("PRIVATE_EXIT")):
            with self.subTest(error=type(error).__name__):
                self.assert_direct_failure_survives_cleanup(error, "transport_interrupted")


if __name__ == "__main__":
    unittest.main()
