"""One independently released Tencent technical probe; results-only journal."""

import argparse
import gc
import http.client
import os
from pathlib import Path
import queue
import re
import socket
import ssl
import threading
import time
import uuid

from . import sources, tencent_daily as daily
from .models import Invalid, canonical, digest, exact, fingerprint, instant, integer, stamp, utc_now
from .store import CHECKOUT, external_path

ROOT = CHECKOUT
TIMEOUT = 20
ERRORS = frozenset({"transport_unknown", "transport_deadline", "worker_incomplete", "cleanup_unknown", "empty_dns",
                    "private_dns", "http_status", "response_size", "header_size", "content_type",
                    "content_encoding", "content_length", "transfer_encoding", "truncated_response",
                    "response_schema", "observation_time", "replay_mismatch", "local_failure"})


class _Failure(Invalid):
    def __init__(self, reason, outcome="unknown"):
        super().__init__(reason if reason in ERRORS else "local_failure")
        self.outcome = outcome


class _Deadline:
    def __init__(self):
        self.end = time.monotonic() + TIMEOUT
        self.cancelled = threading.Event()
        self.lock = threading.Lock()
        self.sockets = []

    def remaining(self):
        left = self.end - time.monotonic()
        if self.cancelled.is_set() or left <= 0:
            raise _Failure("transport_deadline")
        return left

    def bind(self, sock):
        with self.lock:
            if self.cancelled.is_set():
                sock.close()
                raise _Failure("transport_deadline")
            self.sockets.append(sock)
        sock.settimeout(self.remaining())
        return sock

    def abort(self):
        self.cancelled.set()
        with self.lock:
            sockets = list(self.sockets)
        failed = False
        for sock in sockets:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                failed = True
        if failed:
            raise _Failure("cleanup_unknown")


class _BoundedInput:
    def __init__(self, handle):
        self.handle, self.count, self.headers = handle, 0, True

    def _read(self, method, size=-1):
        limit = 16384 if self.headers else daily.MAX_BYTES
        remaining = limit - self.count
        size = remaining + 1 if size < 0 else min(size, remaining + 1)
        value = getattr(self.handle, method)(size)
        self.count += len(value)
        if self.count > limit:
            raise _Failure("header_size" if self.headers else "response_size", "failed")
        return value

    def read(self, size=-1):
        return self._read("read", size)

    def read1(self, size=-1):
        return self._read("read1", size)

    def readline(self, size=-1):
        return self._read("readline", size)

    def close(self):
        self.handle.close()


class _Response(http.client.HTTPResponse):
    def begin(self):
        self.bounded = _BoundedInput(self.fp)
        self.fp = self.bounded
        super().begin()
        self.bounded.headers = False


class _TLS(http.client.HTTPSConnection):
    response_class = _Response

    def __init__(self, address, deadline):
        super().__init__(daily.HOST, timeout=TIMEOUT, context=ssl.create_default_context())
        self.address, self.deadline = address, deadline

    def connect(self):
        family, kind, proto, _, address = self.address
        raw = self.deadline.bind(socket.socket(family, kind, proto))
        self.sock = raw
        raw.connect(address)
        raw.settimeout(self.deadline.remaining())
        self.sock = self.deadline.bind(self._context.wrap_socket(
            raw, server_hostname=daily.HOST, do_handshake_on_connect=False))
        self.sock.do_handshake()
        self.sock.settimeout(self.deadline.remaining())


def _exchange(symbol, window, deadline):
    path = daily.request_path(symbol, window)
    addresses = sources.public_addresses(socket.getaddrinfo(daily.HOST, 443, type=socket.SOCK_STREAM))
    deadline.remaining()
    connection = _TLS(addresses[0], deadline)
    try:
        connection.request("GET", path, headers={"User-Agent": "Market-Intelligence-Technical-Probe/1.0",
            "Accept": "application/json, application/javascript, text/javascript, text/plain",
            "Accept-Encoding": "identity", "Connection": "close"})
        response = connection.getresponse()
        deadline.remaining()
        if response.status != 200:
            raise _Failure("http_status", "failed")
        for header in ("Content-Type", "Content-Encoding", "Content-Length", "Transfer-Encoding"):
            if len(response.headers.get_all(header, [])) > 1:
                raise _Failure("content_length", "failed")
        if response.getheader("Content-Encoding", "identity").lower() != "identity":
            raise _Failure("content_encoding", "failed")
        media = response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
        if media not in {"application/json", "application/javascript", "application/x-javascript",
                         "text/javascript", "text/plain"}:
            raise _Failure("content_type", "failed")
        length = response.getheader("Content-Length")
        if length is not None and (re.fullmatch(r"[0-9]{1,6}", length) is None or int(length) > daily.MAX_BYTES):
            raise _Failure("content_length", "failed")
        transfer = response.getheader("Transfer-Encoding")
        if transfer is not None and (transfer.lower() != "chunked" or length is not None):
            raise _Failure("transfer_encoding", "failed")
        body = bytearray()
        while True:
            deadline.remaining()
            chunk = response.read1(min(16384, daily.MAX_BYTES + 1 - len(body)))
            deadline.remaining()
            if not chunk:
                break
            body.extend(chunk)
            if len(body) > daily.MAX_BYTES:
                raise _Failure("response_size", "failed")
        if length is not None and len(body) != int(length):
            raise _Failure("truncated_response")
        return bytes(body), response.bounded.count
    finally:
        connection.close()


def _fetch(symbol, window):
    """Hard caller deadline, pinned public address/TLS and no ambient credentials."""
    deadline, result = _Deadline(), queue.Queue(maxsize=1)

    def work():
        try:
            response = _exchange(symbol, window, deadline)
            deadline.remaining()
            result.put(response)
        except BaseException as exc:
            if type(exc) is _Failure:
                failure = _Failure(str(exc), exc.outcome)
            elif isinstance(exc, sources.TransportError) and str(exc) in {"empty_dns", "private_dns"}:
                failure = _Failure(str(exc), "failed")
            else:
                failure = _Failure("transport_unknown")
            result.put(failure)

    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    try:
        try:
            value = result.get(timeout=deadline.remaining())
        except queue.Empty:
            raise _Failure("transport_deadline") from None
        deadline.remaining()
        if isinstance(value, _Failure):
            raise value
        body, count = value
        if type(body) is not bytes or len(body) > daily.MAX_BYTES:
            raise _Failure("response_size", "failed")
        integer(count, len(body), daily.MAX_BYTES)
        return body, count
    finally:
        failed = False
        try:
            deadline.abort()
        except Exception:
            failed = True
        worker.join(timeout=0.25)
        if worker.is_alive():
            raise _Failure("worker_incomplete") from None
        if failed:
            raise _Failure("cleanup_unknown") from None


def _path():
    return external_path(Path.home() / ".a-share-market-intelligence" / "p4t-tencent-daily.jsonl")


def _authorize():
    from tools.governance_p4t import tencent_gate
    value = tencent_gate(ROOT)
    fingerprint(value)
    return value


def not_run_receipt(snapshot_id):
    fingerprint(snapshot_id)
    return {"schema_version": 1, "package_id": "P4T", "snapshot_id": snapshot_id,
            "status": "NOT_RUN", "observation": None}


def validate_observation_receipt(value, snapshot_id):
    try:
        exact(value, {"schema_version", "package_id", "snapshot_id", "status", "observation"})
        fingerprint(snapshot_id)
        integer(value["schema_version"], 1, 1)
        if value["package_id"] != "P4T" or value["snapshot_id"] != snapshot_id:
            raise Invalid("receipt_binding")
        if value["status"] == "NOT_RUN":
            if value["observation"] is not None:
                raise Invalid("receipt_status")
            return value
        if value["status"] != "RECORDED":
            raise Invalid("receipt_status")
        o = value["observation"]
        exact(o, {"run_id", "started_at", "finished_at", "window", "reserved_requests", "dispatch_attempts",
                  "allowance_consumed", "results", "outcome", "local_error", "payloads_persisted",
                  "payload_references_discarded", "connections_closed", "upstream_rights",
                  "normal_composition", "live_verified_benchmark_count"})
        if re.fullmatch(r"[0-9a-f]{32}", o["run_id"]) is None:
            raise Invalid("receipt_id")
        started, finished = instant(o["started_at"]), instant(o["finished_at"])
        if not started <= finished <= utc_now():
            raise Invalid("receipt_time")
        if o["window"] != daily.plan(o["started_at"]):
            raise Invalid("receipt_window")
        integer(o["reserved_requests"], 3, 3)
        integer(o["dispatch_attempts"], 0, 3)
        if (o["allowance_consumed"] is not True or o["payloads_persisted"] is not False
                or type(o["payload_references_discarded"]) is not bool
                or type(o["connections_closed"]) is not bool or o["upstream_rights"] != "unknown"
                or o["normal_composition"] != "BLOCKED_SOURCE_RIGHTS"):
            raise Invalid("receipt_permission")
        integer(o["live_verified_benchmark_count"], 0, 0)
        results = o["results"]
        if type(results) is not list or len(results) != o["dispatch_attempts"]:
            raise Invalid("receipt_attempts")
        previous = started
        for index, row in enumerate(results):
            exact(row, {"symbol", "started_at", "finished_at", "status", "http_bytes", "body_bytes",
                        "response_sha256", "retrieved_at", "parse", "replay_verified", "error"})
            begin, end = instant(row["started_at"]), instant(row["finished_at"])
            if not previous <= begin <= end <= finished or row["symbol"] != list(daily.SYMBOLS)[index]:
                raise Invalid("receipt_order")
            previous = end
            if row["retrieved_at"] is None:
                if any(row[k] is not None for k in ("http_bytes", "body_bytes", "response_sha256")):
                    raise Invalid("receipt_transport")
            else:
                if not begin <= instant(row["retrieved_at"]) <= end:
                    raise Invalid("receipt_transport")
                integer(row["body_bytes"], 0, daily.MAX_BYTES)
                integer(row["http_bytes"], row["body_bytes"], daily.MAX_BYTES)
                fingerprint(row["response_sha256"])
            if row["status"] == "OBSERVED":
                if row["retrieved_at"] is None:
                    raise Invalid("receipt_transport")
                daily.validate_summary(row["parse"], o["window"])
                if row["parse"]["symbol"] != row["symbol"] or row["error"] is not None or row["replay_verified"] is not True:
                    raise Invalid("receipt_parse")
            elif row["status"] in {"FAILED", "UNKNOWN"}:
                if (index != len(results) - 1 or row["error"] not in ERRORS
                        or row["parse"] is not None
                        or row["replay_verified"] is not False):
                    raise Invalid("receipt_failure")
            else:
                raise Invalid("receipt_status")
        if o["local_error"] not in {None, "snapshot_changed", "clock_rollback", "local_failure", "interrupted"}:
            raise Invalid("receipt_error")
        if o["outcome"] != _outcome(results, o["local_error"]):
            raise Invalid("receipt_outcome")
        if (len(results) < 3 and o["local_error"] is None
                and (not results or results[-1]["status"] == "OBSERVED")):
            raise Invalid("receipt_unexplained_stop")
        cleanup = not any(r["error"] in {"worker_incomplete", "cleanup_unknown"} for r in results)
        if o["payload_references_discarded"] is not cleanup or o["connections_closed"] is not cleanup:
            raise Invalid("receipt_cleanup")
        canonical(value)
    except Exception:
        raise Invalid("invalid_observation_receipt") from None
    return value


def _outcome(rows, local_error):
    if any(r["status"] == "UNKNOWN" for r in rows) or local_error == "interrupted":
        return "UNKNOWN"
    if (len(rows) == 3 and local_error is None and all(r["status"] == "OBSERVED"
            and r["parse"]["freshness"] == "CURRENT" and not r["parse"]["rejected"] for r in rows)):
        return "OBSERVED"
    return "PARTIAL" if any(r["status"] == "OBSERVED" for r in rows) else "FAILED"


def _append(handle, record):
    body = (canonical(record) + "\n").encode()
    if len(body) > 32768:
        raise Invalid("journal_record_limit")
    if handle.write(body) != len(body):
        raise Invalid("journal_write_incomplete")
    handle.flush()
    os.fsync(handle.fileno())


def _attached(handle, path):
    external_path(path)
    opened, named = os.fstat(handle.fileno()), path.stat()
    if opened.st_nlink != 1 or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino):
        raise Invalid("journal_identity_changed")


def _windows_volume(kernel, handle, path):
    import ctypes
    name = ctypes.create_unicode_buffer(32)
    if (kernel.GetDriveTypeW(path.anchor) != 3
            or not kernel.GetVolumeInformationByHandleW(handle, None, 0, None, None, None, name, len(name))
            or name.value != "NTFS"):
        raise Invalid("journal_durability_unsupported")


def _open_journal(path):
    # Do not introduce unsynchronized parent directories into a one-use quota path.
    if not path.parent.is_dir():
        raise Invalid("journal_parent_must_exist")
    if os.name == "posix":
        return path.open("xb")
    if os.name != "nt":
        raise Invalid("journal_durability_unsupported")
    import ctypes
    from ctypes import wintypes
    import msvcrt
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.GetDriveTypeW.argtypes = (wintypes.LPCWSTR,)
    kernel.GetDriveTypeW.restype = wintypes.UINT
    kernel.GetVolumeInformationByHandleW.argtypes = (wintypes.HANDLE, wintypes.LPWSTR,
        wintypes.DWORD, wintypes.LPDWORD, wintypes.LPDWORD, wintypes.LPDWORD,
        wintypes.LPWSTR, wintypes.DWORD)
    kernel.GetVolumeInformationByHandleW.restype = wintypes.BOOL
    # CREATE_NEW, FILE_SHARE_READ, FILE_FLAG_WRITE_THROUGH; no overwrite or delete sharing.
    # NTFS metadata flushing: learn.microsoft.com/en-us/windows/win32/fileio/file-caching
    handle = kernel.CreateFileW(str(path), 0x40000000, 1, None, 1, 0x80000080, None)
    if handle == ctypes.c_void_p(-1).value:
        error = ctypes.get_last_error()
        if error in {80, 183}:
            raise FileExistsError("journal_already_consumed")
        raise OSError("journal_create_failed")
    fd = None
    try:
        _windows_volume(kernel, handle, path)
        fd = msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY)
        handle = None
        result = os.fdopen(fd, "wb")
        fd = None
        return result
    finally:
        if fd is not None:
            os.close(fd)
        if handle is not None:
            kernel.CloseHandle(handle)


def _sync_namespace(path):
    # Windows uses a write-through NTFS handle and fsync in _append. POSIX also
    # needs directory fsync; sync ancestors so even a recently provisioned parent is covered.
    if os.name == "nt":
        return
    if os.name != "posix" or not hasattr(os, "O_DIRECTORY"):
        raise Invalid("journal_durability_unsupported")
    for parent in path.parents:
        fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _one(symbol, window, not_before):
    row = {"symbol": symbol, "started_at": stamp(utc_now()), "finished_at": None,
           "status": "UNKNOWN", "http_bytes": None, "body_bytes": None, "response_sha256": None,
           "retrieved_at": None, "parse": None, "replay_verified": False, "error": None}
    body = parsed = repeated = None
    try:
        if instant(row["started_at"]) < instant(not_before):
            raise _Failure("observation_time", "failed")
        body, count = _fetch(symbol, window)
        seen = stamp(utc_now())
        row.update(http_bytes=count, body_bytes=len(body), response_sha256=digest(body), retrieved_at=seen)
        parsed = daily.parse(body, symbol, window, seen)
        repeated = daily.parse(body, symbol, window, seen)
        if canonical(parsed) != canonical(repeated):
            raise _Failure("replay_mismatch", "failed")
        daily.validate_summary(parsed["summary"], window)
        row.update(status="OBSERVED", parse=parsed["summary"], replay_verified=True)
    except _Failure as exc:
        row.update(status=exc.outcome.upper(), error=str(exc))
    except Invalid as exc:
        row.update(status="FAILED", error=str(exc) if str(exc) in ERRORS else "local_failure")
    except (KeyboardInterrupt, SystemExit):
        row.update(status="UNKNOWN", error="transport_unknown")
    except Exception:
        row.update(status="UNKNOWN", error="local_failure")
    finally:
        body = parsed = repeated = None
        row["finished_at"] = stamp(utc_now())
    return row


def observe(allow_network=False):
    if allow_network is not True:
        raise Invalid("explicit_allow_network_required")
    snapshot = _authorize()
    start = stamp(utc_now())
    window = daily.plan(start)
    path = _path()
    path = external_path(path)
    reservation = {"kind": "reservation", "schema_version": 1, "snapshot_id": snapshot,
                   "run_id": uuid.uuid4().hex, "started_at": start, "reserved_requests": 3}
    # The same exclusive handle stays open; an empty/partial prior file still consumes the allowance.
    with _open_journal(path) as handle:
        _attached(handle, path)
        _append(handle, reservation)
        _sync_namespace(path)
        rows, local_error = [], None
        try:
            for symbol in daily.SYMBOLS:
                if _authorize() != snapshot:
                    local_error = "snapshot_changed"
                    break
                previous = rows[-1]["finished_at"] if rows else start
                dispatch_at = stamp(utc_now())
                if instant(dispatch_at) < instant(previous):
                    local_error = "clock_rollback"
                    break
                _attached(handle, path)
                _append(handle, {"kind": "dispatch_reserved", "symbol": symbol, "ordinal": len(rows) + 1})
                row = _one(symbol, window, dispatch_at)
                rows.append(row)
                _append(handle, {"kind": "result", "result": row})
                begin, end = instant(row["started_at"]), instant(row["finished_at"])
                seen = instant(row["retrieved_at"]) if row["retrieved_at"] is not None else begin
                if not instant(dispatch_at) <= begin <= seen <= end:
                    local_error = "clock_rollback"
                    break
                if row["status"] != "OBSERVED":
                    break
        except (KeyboardInterrupt, SystemExit):
            local_error = "interrupted"
        except Exception:
            local_error = "local_failure"
        gc.collect()
        cleanup = not any(r["error"] in {"worker_incomplete", "cleanup_unknown"} for r in rows)
        receipt = {"schema_version": 1, "package_id": "P4T", "snapshot_id": snapshot, "status": "RECORDED",
            "observation": {"run_id": reservation["run_id"], "started_at": start, "finished_at": stamp(utc_now()),
                "window": window, "reserved_requests": 3, "dispatch_attempts": len(rows),
                "allowance_consumed": True, "results": rows, "outcome": _outcome(rows, local_error),
                "local_error": local_error, "payloads_persisted": False,
                "payload_references_discarded": cleanup, "connections_closed": cleanup,
                "upstream_rights": "unknown", "normal_composition": "BLOCKED_SOURCE_RIGHTS",
                "live_verified_benchmark_count": 0}}
        validate_observation_receipt(receipt, snapshot)
        _attached(handle, path)
        _append(handle, {"kind": "final", "receipt": receipt})
    if status() != receipt:
        raise Invalid("journal_finalization_unverified")
    return receipt


def status():
    path = _path()
    if not path.exists():
        return {"status": "NOT_RUN", "allowance_consumed": False}
    if path.stat().st_size > 262144:
        raise Invalid("journal_size_limit")
    try:
        with path.open("rb") as handle:
            data = handle.read(262145)
        if len(data) > 262144:
            raise Invalid("journal_size_limit")
        records = [daily.decode_input(line) for line in data.splitlines()]
        if records and records[-1].get("kind") == "final":
            receipt = records[-1]["receipt"]
            validate_observation_receipt(receipt, receipt["snapshot_id"])
            o = receipt["observation"]
            expected = [{"kind": "reservation", "schema_version": 1,
                         "snapshot_id": receipt["snapshot_id"], "run_id": o["run_id"],
                         "started_at": o["started_at"], "reserved_requests": 3}]
            for index, row in enumerate(o["results"]):
                expected += [{"kind": "dispatch_reserved", "symbol": row["symbol"], "ordinal": index + 1},
                             {"kind": "result", "result": row}]
            expected.append({"kind": "final", "receipt": receipt})
            if records != expected:
                raise Invalid("journal_binding")
            return receipt
    except (ValueError, KeyError, TypeError, AttributeError):
        pass
    return {"status": "UNKNOWN_CONSUMED", "allowance_consumed": True}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("observe", "status"))
    parser.add_argument("--allow-network", action="store_true")
    args = parser.parse_args(argv)
    try:
        value = observe(args.allow_network) if args.command == "observe" else status()
        print(canonical(value))
        return 0
    except BaseException:
        print('{"status":"BLOCKED_OR_UNKNOWN","allowance_may_be_consumed":true}')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
