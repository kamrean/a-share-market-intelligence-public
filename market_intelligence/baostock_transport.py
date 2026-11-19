"""Internal, one-session SDK transport. The capture gate owns authorization.

Importing this module does not import third-party packages or contact a source.
Only sanitized metadata leaves run_once; response callbacks are RAM-only custody.
"""

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import codecs
import hashlib
import importlib
import importlib.machinery
import importlib.metadata
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import platform
import socket
import stat
import subprocess
import sys
import sysconfig
import threading
import time
import types
import zlib


HOST = "public-api.baostock.com"
PORT = 10030
VERSION = "00.9.40"
SYMBOLS = ("sh.000001", "sz.399001")
DAILY_FIELDS = ("date", "code", "open", "high", "low", "close", "preclose",
                "volume", "amount", "pctChg")
CALENDAR_FIELDS = ("calendar_date", "is_trading_day")
IDENTITY_FIELDS = ("code", "code_name", "ipoDate", "outDate", "type", "status")
MAX_WIRE = 512000
MAX_FRAME = 128000
MAX_DECODED = 1048576
MAX_REQUESTS = 12
DEADLINE = 20.0
TERMINATOR = b"<![CDATA[]]>\n"
MANIFEST_HASH = "f4fa91e3cb84ca72e5ea106d131dba0550682c3c1f1e9cc28b2e7016ba15ea7f"
REASONS = frozenset({
    "complete", "unsupported_platform", "sdk_integrity", "sdk_context",
    "concurrent_use", "dns", "deadline", "clock_rollback", "connection",
    "send", "recv", "eof", "wire_limit", "frame_limit", "decoded_limit",
    "compression", "protocol", "pagination", "provider_error",
    "callback_rejected", "interrupted", "cleanup", "internal_error",
})
_LOCK = threading.Lock()
_monotonic = time.monotonic
_create_socket = socket.socket


def _now():
    return datetime.now(timezone.utc)


def _stamp(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, repr=False)
class ResponseView:
    index: int
    method: str
    symbol: str | None
    fields: tuple[str, ...]
    data: tuple[tuple[str, ...], ...]
    start_date: str | None
    end_date: str | None
    first_seen_at: str
    completed_at: str
    wire_bytes: int
    wire_sha256: str
    decoded_bytes: int
    response_type: str
    client_version: str
    user_id: str
    frequency: str | None
    adjustflag: str | None
    page_number: int | None
    page_size: int | None


@dataclass(frozen=True)
class ResponseMetadata:
    index: int
    method: str
    symbol: str | None
    start_date: str | None
    end_date: str | None
    first_seen_at: str
    completed_at: str
    wire_bytes: int
    wire_sha256: str
    decoded_bytes: int
    response_type: str
    client_version: str
    row_count: int
    page_number: int | None
    page_size: int | None

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class TransportReceipt:
    status: str
    reason: str
    started_at: str
    finished_at: str
    window_start: str | None
    window_end: str | None
    requests_sent: int
    wire_bytes: int
    decoded_bytes: int
    wire_sha256: str
    responses: tuple[ResponseMetadata, ...]
    socket_closed: bool
    bindings_restored: bool
    transport_payloads_disposed: bool

    def to_dict(self):
        result = asdict(self)
        result["responses"] = [item.to_dict() for item in self.responses]
        return result


class _Fault(Exception):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


class _Discard:
    def write(self, text):
        return len(text)

    def flush(self):
        pass


class _Budget:
    def __init__(self):
        self.start = self.last_mono = _monotonic()
        self.started = self.last_wall = _now()

    def check(self):
        mono, wall = _monotonic(), _now()
        if mono < self.last_mono or wall < self.last_wall:
            raise _Fault("clock_rollback")
        self.last_mono, self.last_wall = mono, wall
        left = DEADLINE - (mono - self.start)
        if left <= 0:
            raise _Fault("deadline")
        return left


def _regular(path, maximum):
    path = Path(path).absolute()
    for ancestor in (path, *path.parents):
        info = ancestor.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise _Fault("sdk_integrity")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
        raise _Fault("sdk_integrity")
    with path.open("rb") as stream:
        data = stream.read(maximum + 1)
    if len(data) > maximum or len(data) != info.st_size:
        raise _Fault("sdk_integrity")
    return data


def _supported_platform():
    if (sys.implementation.name != "cpython" or sys.version_info[:2] != (3, 12)
            or sys.maxsize <= 2**32 or sys.prefix == sys.base_prefix):
        raise _Fault("unsupported_platform")
    if sys.platform == "win32" and platform.machine().lower() in {"amd64", "x86_64"}:
        return
    if sys.platform == "linux" and platform.machine().lower() == "x86_64":
        name, version = platform.libc_ver()
        if name == "glibc" and tuple(int(x) for x in version.split(".")[:2]) >= (2, 27):
            return
    raise _Fault("unsupported_platform")


def _sdk_names():
    return [name for name in sys.modules if name == "baostock" or name.startswith("baostock.")]


class _SDK:
    def __init__(self):
        self.modules = {}
        self.saved = []
        self.restored = True
        self.entered = False

    def load(self):
        _supported_platform()
        if _sdk_names():
            raise _Fault("sdk_context")
        manifest_path = Path(__file__).absolute().parents[1] / "config/p4b_sdk_manifest.json"
        raw = _regular(manifest_path, 100000)
        if hashlib.sha256(raw).hexdigest() != MANIFEST_HASH:
            raise _Fault("sdk_integrity")
        manifest = json.loads(raw)
        root = Path(sysconfig.get_path("purelib")).absolute()
        if not root.is_relative_to(Path(sys.prefix).absolute()):
            raise _Fault("sdk_integrity")
        for pin in manifest["dependencies"]:
            distributions = list(importlib.metadata.distributions(name=pin["name"]))
            if len(distributions) != 1 or distributions[0].version != pin["version"]:
                raise _Fault("sdk_integrity")
            if Path(distributions[0].locate_file("")).absolute() != root:
                raise _Fault("sdk_integrity")
        expected = manifest["sdk"]["critical_files"]
        actual = set()
        for directory, directories, files in os.walk(root / "baostock", followlinks=False):
            for name in directories:
                child = Path(directory) / name
                info = child.lstat()
                if child.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
                    raise _Fault("sdk_integrity")
            for name in files:
                path = Path(directory) / name
                relative = path.relative_to(root).as_posix()
                if relative not in expected:
                    raise _Fault("sdk_integrity")
                if hashlib.sha256(_regular(path, 1000000)).hexdigest() != expected[relative]:
                    raise _Fault("sdk_integrity")
                actual.add(relative)
        if actual != set(expected):
            raise _Fault("sdk_integrity")
        spec = importlib.util.find_spec("baostock")
        if (spec is None or spec.origin is None
                or Path(spec.origin).absolute() != root / "baostock/__init__.py"):
            raise _Fault("sdk_integrity")
        self.entered = True
        self.restored = False
        previous = sys.dont_write_bytecode
        try:
            sys.dont_write_bytecode = True
            self.sdk = importlib.import_module("baostock")
        finally:
            sys.dont_write_bytecode = previous
        self.modules = {name: sys.modules[name] for name in _sdk_names()}
        for name, module in self.modules.items():
            spec = getattr(module, "__spec__", None)
            origin = getattr(spec, "origin", None)
            relative = name.replace(".", "/")
            relative += "/__init__.py" if getattr(spec, "submodule_search_locations", None) is not None else ".py"
            if (not isinstance(module, types.ModuleType) or not origin
                    or relative not in expected or Path(origin).absolute() != root / relative
                    or getattr(module, "__file__", None) != origin
                    or spec.name != name or module.__name__ != name
                    or not isinstance(spec.loader, importlib.machinery.SourceFileLoader)):
                raise _Fault("sdk_integrity")
            if name.startswith("baostock.demo."):
                raise _Fault("sdk_context")
        self.context = self.modules["baostock.common.context"]
        self.socketutil = self.modules["baostock.util.socketutil"]
        cls = self.socketutil.SocketUtil
        if (any(not name.startswith("__") for name in vars(self.context))
                or cls.instance is not None or cls.init_flag is not False
                or self.socketutil.socket is not socket or self.socketutil.zlib is not zlib
                or self.sdk.__version__ != VERSION):
            raise _Fault("sdk_context")
        self.context_before = dict(vars(self.context))
        self.class_before = (cls.instance, cls.init_flag)
        return self.sdk

    def bind(self, session):
        result_class = self.modules["baostock.data.resultset"].ResultData
        original_set_data = result_class.setData
        self.saved = [(self.socketutil, "socket", self.socketutil.socket),
                      (self.socketutil, "zlib", self.socketutil.zlib),
                      (result_class, "setData", original_set_data)]
        self.socketutil.socket = _SocketModule(session)
        self.socketutil.zlib = _ZlibModule(session)
        result_class.setData = _guard_set_data(session, original_set_data)

    def close(self):
        if not self.entered:
            return self.restored
        try:
            for module, name, value in reversed(self.saved):
                setattr(module, name, value)
            if hasattr(self, "context_before"):
                self.context.__dict__.clear()
                self.context.__dict__.update(self.context_before)
                cls = self.socketutil.SocketUtil
                cls.instance, cls.init_flag = self.class_before
            self.restored = all(getattr(module, name) is value for module, name, value in self.saved)
        except BaseException:
            self.restored = False
        finally:
            for name in _sdk_names():
                sys.modules.pop(name, None)
            self.saved.clear()
            self.modules.clear()
            for name in ("sdk", "socketutil", "context", "context_before", "class_before"):
                self.__dict__.pop(name, None)
        return self.restored


def _resolve_ipv4(timeout):
    # A killable stdlib-only resolver avoids an orphaned blocking DNS thread.
    source = (
        "import json,socket; "
        "r=socket.getaddrinfo('public-api.baostock.com',10030,socket.AF_INET,socket.SOCK_STREAM); "
        "print(json.dumps([x[4][0] for x in r[:65]]))"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", source],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        timeout=timeout, check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if len(result.stdout) > 4096:
        raise _Fault("dns")
    return json.loads(result.stdout)


class _Session:
    def __init__(self, budget):
        self.budget = budget
        self.reason = None
        self.requests = self.wire = self.decoded = 0
        self.hash = hashlib.sha256()
        self.socket = None
        self.index = 0
        self.responses = []

    def fail(self, reason):
        if self.reason is None:
            self.reason = reason
        raise _Fault(self.reason)

    def check(self):
        if self.reason is not None:
            raise _Fault(self.reason)
        try:
            return self.budget.check()
        except _Fault as exc:
            self.fail(exc.reason)

    def begin(self, method, symbol, request_type, response_type):
        self.check()
        self.index += 1
        if self.index > 7:
            self.fail("protocol")
        self.method, self.symbol = method, symbol
        self.request_type, self.response_type = request_type, response_type
        self.sent = self.complete = self.decompressed = False
        self.frame_size = self.frame_decoded = 0
        self.frame_hash = hashlib.sha256()
        self.header = b""
        self.tail = b""
        self.body_length = None
        self.first_seen = self.completed = None
        self.utf8 = codecs.getincrementaldecoder("utf-8")()
        self.plain_frame = bytearray()

    def dispose(self):
        for name in ("header", "tail", "first_seen", "completed", "utf8", "plain_frame"):
            setattr(self, name, None)
        self.socket = None


class _SocketModule:
    AF_INET = socket.AF_INET
    SOCK_STREAM = socket.SOCK_STREAM

    def __init__(self, session):
        self.session = session

    def socket(self, family, kind):
        state = self.session
        state.check()
        if state.socket is not None or family != socket.AF_INET or kind != socket.SOCK_STREAM:
            state.fail("connection")
        state.socket = _BoundedSocket(state)
        return state.socket


class _BoundedSocket:
    def __init__(self, session):
        self.state = session
        self.raw = None
        self.connected = False
        self.attempted = False
        self.closed = True

    def connect(self, endpoint):
        state = self.state
        state.check()
        if endpoint != (HOST, PORT) or self.attempted:
            state.fail("connection")
        self.attempted = True
        try:
            addresses = _resolve_ipv4(state.check())
            state.check()
            if type(addresses) is not list or not 1 <= len(addresses) <= 64:
                state.fail("dns")
            for address in addresses:
                if type(address) is not str:
                    state.fail("dns")
                ip = ipaddress.ip_address(address)
                if ip.version != 4 or not ip.is_global or ip.is_multicast:
                    state.fail("dns")
        except _Fault:
            raise
        except subprocess.TimeoutExpired:
            state.fail("deadline")
        except BaseException as exc:
            state.fail("interrupted" if isinstance(exc, (KeyboardInterrupt, SystemExit)) else "dns")
        try:
            self.raw = _create_socket(socket.AF_INET, socket.SOCK_STREAM)
            self.closed = False
            self.raw.settimeout(state.check())
            self.raw.connect((addresses[0], PORT))
            state.check()
            self.connected = True
        except _Fault:
            raise
        except BaseException as exc:
            state.fail("interrupted" if isinstance(exc, (KeyboardInterrupt, SystemExit)) else "connection")

    def send(self, payload):
        state = self.state
        state.check()
        if not self.connected or self.closed or state.sent or state.requests >= MAX_REQUESTS:
            state.fail("protocol")
        if type(payload) is not bytes or not 21 <= len(payload) <= 4096:
            state.fail("protocol")
        head = payload[:21].split(b"\x01")
        body = payload[21:].split(b"\x01")
        if (len(head) != 3 or head[0] != VERSION.encode() or head[1] != state.request_type.encode()
                or body[:2] != [state.method.encode(), b"anonymous"]):
            state.fail("protocol")
        state.sent = True
        state.requests += 1
        offset = 0
        try:
            while offset < len(payload):
                self.raw.settimeout(state.check())
                count = self.raw.send(memoryview(payload)[offset:])
                state.check()
                if type(count) is not int or not 0 < count <= len(payload) - offset:
                    state.fail("send")
                offset += count
        except _Fault:
            raise
        except BaseException as exc:
            state.fail("interrupted" if isinstance(exc, (KeyboardInterrupt, SystemExit)) else "send")
        return len(payload)

    def recv(self, size):
        state = self.state
        state.check()
        if not state.sent or state.complete or type(size) is not int or size <= 0:
            state.fail("protocol")
        if state.wire >= MAX_WIRE:
            state.fail("wire_limit")
        if state.frame_size >= MAX_FRAME:
            state.fail("frame_limit")
        maximum = min(size, 8192, MAX_WIRE - state.wire, MAX_FRAME - state.frame_size)
        try:
            self.raw.settimeout(state.check())
            chunk = self.raw.recv(maximum)
        except BaseException as exc:
            state.fail("interrupted" if isinstance(exc, (KeyboardInterrupt, SystemExit)) else "recv")
        if type(chunk) is not bytes or len(chunk) > maximum:
            state.fail("recv")
        if not chunk:
            state.fail("eof")
        state.wire += len(chunk)
        state.frame_size += len(chunk)
        state.hash.update(chunk)
        state.frame_hash.update(chunk)
        state.check()
        if state.first_seen is None:
            state.first_seen = _stamp(state.budget.last_wall)
        if state.response_type != "96":
            state.plain_frame.extend(chunk)
            try:
                state.utf8.decode(chunk, final=False)
            except UnicodeDecodeError:
                state.fail("protocol")
        if len(state.header) < 21:
            state.header += chunk[:21 - len(state.header)]
            if len(state.header) == 21:
                pieces = state.header.split(b"\x01")
                if (len(pieces) != 3 or pieces[0] != VERSION.encode()
                        or pieces[1] != state.response_type.encode()
                        or len(pieces[2]) != 10 or not pieces[2].isdigit()):
                    state.fail("protocol")
                state.body_length = int(pieces[2])
                if state.body_length > MAX_FRAME - 21:
                    state.fail("frame_limit")
        end_check = state.tail + chunk
        position = end_check.find(TERMINATOR)
        if position >= 0:
            if position != len(end_check) - len(TERMINATOR) or state.body_length is None:
                state.fail("protocol")
            if state.frame_size < 21 + state.body_length + len(TERMINATOR):
                state.fail("protocol")
            if state.response_type == "96":
                if state.frame_size != 21 + state.body_length + len(TERMINATOR):
                    state.fail("protocol")
            else:
                try:
                    state.utf8.decode(b"", final=True)
                except UnicodeDecodeError:
                    state.fail("protocol")
                _validate_plain_frame(state)
                state.plain_frame.clear()
                count = state.frame_size - 21
                if state.decoded + count > MAX_DECODED:
                    state.fail("decoded_limit")
                state.decoded += count
                state.frame_decoded = count
            state.complete = True
            state.completed = _stamp(state.budget.last_wall)
        state.tail = end_check[-len(TERMINATOR):]
        return chunk

    def close(self):
        if self.raw is not None and not self.closed:
            try:
                self.raw.close()
                self.closed = True
            except BaseException:
                self.state.fail("cleanup")


class _ZlibModule:
    crc32 = staticmethod(zlib.crc32)

    def __init__(self, session):
        self.state = session

    def decompress(self, data):
        state = self.state
        state.check()
        if (not state.complete or state.response_type != "96" or state.decompressed
                or type(data) is not bytes or len(data) != state.body_length):
            state.fail("compression")
        state.decompressed = True
        remaining = MAX_DECODED - state.decoded
        if remaining <= 0:
            state.fail("decoded_limit")
        try:
            decoder = zlib.decompressobj()
            result = decoder.decompress(data, remaining)
        except zlib.error:
            state.fail("compression")
        state.decoded += len(result)
        state.frame_decoded = len(result)
        if decoder.unconsumed_tail:
            state.fail("decoded_limit")
        if not decoder.eof or decoder.unused_data:
            state.fail("compression")
        try:
            for offset in range(0, len(result), 8192):
                state.utf8.decode(result[offset:offset + 8192], final=False)
            state.utf8.decode(b"", final=True)
        except UnicodeDecodeError:
            state.fail("protocol")
        state.check()
        return result


def _validate_plain_frame(state):
    # Bounded framing only; the SDK still interprets every business field.
    # Client requests count Python characters; authored response fixtures count
    # UTF-8 bytes. Accept only an exact match to either explicit representation,
    # not an arbitrary lower bound. Neither is certified server documentation.
    frame = bytes(state.plain_frame)
    head_body, separator, checksum = frame[:-len(TERMINATOR)].rpartition(b"\x01")
    if (not separator or len(head_body) < 21 or not 1 <= len(checksum) <= 10
            or not checksum.isdigit()):
        state.fail("protocol")
    body = head_body[21:]
    try:
        characters = len(body.decode("utf-8"))
    except UnicodeDecodeError:
        state.fail("protocol")
    if (state.body_length not in {len(body), characters}
            or checksum != str(zlib.crc32(head_body)).encode("ascii")):
        state.fail("protocol")


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_json_constant(_):
    raise ValueError("nonstandard JSON constant")


def _guard_set_data(state, original):
    def checked(result, raw):
        state.check()
        if type(raw) is not str or len(raw) > MAX_DECODED:
            state.fail("protocol")
        expected_fields = (CALENDAR_FIELDS if state.method == "query_trade_dates" else
                           IDENTITY_FIELDS if state.method == "query_stock_basic" else DAILY_FIELDS)
        if state.method not in {"query_trade_dates", "query_stock_basic", "query_history_k_data_plus"}:
            state.fail("protocol")
        try:
            # Inspect exactly the SDK-selected JSON field, BEFORE split/join.
            parsed = ({"record": []} if raw == "" else json.loads(
                raw, object_pairs_hook=_unique_json_object, parse_constant=_reject_json_constant))
        except (ValueError, RecursionError):
            state.fail("protocol")
        if type(parsed) is not dict or set(parsed) != {"record"} or type(parsed["record"]) is not list:
            state.fail("protocol")
        records = parsed["record"]
        if len(records) >= 2000:
            state.fail("pagination")
        maximum = 1 if state.method == "query_stock_basic" else 14
        if len(records) > maximum:
            state.fail("protocol")
        for row in records:
            if type(row) is not list or len(row) != len(expected_fields):
                state.fail("protocol")
            for column, value in enumerate(row):
                if (type(value) is not str or len(value) > 128
                        or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in value)):
                    state.fail("protocol")
                opaque_name = state.method == "query_stock_basic" and column == 1
                if not opaque_name and any(c.isspace() for c in value):
                    state.fail("protocol")
        # Compact JSON has no lexical whitespace outside strings. Escape literal
        # spaces, without changing any value, so the official lossy parser cannot
        # erase legitimate opaque name spacing. It remains the result producer.
        safe = json.dumps(parsed, ensure_ascii=True, separators=(",", ":")).replace(" ", "\\u0020")
        if len(safe) > MAX_DECODED:
            state.fail("protocol")
        state.check()
        original(result, safe)
        if result.data != records:
            state.fail("protocol")
        state.check()
    return checked


def _view(state, result, start, end):
    state.check()
    if not state.complete or result is None:
        state.fail("protocol")
    if getattr(result, "error_code", None) != "0":
        state.fail("provider_error")
    if result.method != state.method or result.user_id != "anonymous":
        state.fail("protocol")
    fields, rows = (), ()
    page_number = page_size = frequency = adjustflag = None
    window_start = window_end = None
    if state.method not in {"login", "logout"}:
        if result.cur_page_num != "1" or result.per_page_count != "2000":
            state.fail("pagination")
        page_number, page_size = 1, 2000
        expected_fields = (CALENDAR_FIELDS if state.method == "query_trade_dates" else
                           IDENTITY_FIELDS if state.method == "query_stock_basic" else DAILY_FIELDS)
        if type(result.fields) is not list or tuple(result.fields) != expected_fields:
            state.fail("protocol")
        fields = expected_fields
        maximum = 1 if state.method == "query_stock_basic" else 14
        if type(result.data) is not list:
            state.fail("protocol")
        if len(result.data) >= 2000:
            state.fail("pagination")
        if len(result.data) > maximum:
            state.fail("protocol")
        for row in result.data:
            if type(row) is not list or len(row) != len(fields):
                state.fail("protocol")
            for value in row:
                if (type(value) is not str or len(value) > 128
                        or any(ord(char) < 32 for char in value)):
                    state.fail("protocol")
        rows = tuple(tuple(row) for row in result.data)
        if state.symbol is not None and result.code != state.symbol:
            state.fail("protocol")
        if state.method == "query_stock_basic" and result.code_name != "":
            state.fail("protocol")
        if state.method in {"query_trade_dates", "query_history_k_data_plus"}:
            if result.start_date != start or result.end_date != end:
                state.fail("protocol")
            window_start, window_end = start, end
        if state.method == "query_history_k_data_plus":
            if result.frequency != "d" or result.adjustflag != "3":
                state.fail("protocol")
            frequency, adjustflag = "d", "3"
    return ResponseView(
        state.index, state.method, state.symbol, fields, rows, window_start, window_end,
        state.first_seen, state.completed, state.frame_size, state.frame_hash.hexdigest(),
        state.frame_decoded, state.response_type, VERSION, "anonymous", frequency,
        adjustflag, page_number, page_size,
    )


def _metadata(view):
    return ResponseMetadata(
        view.index, view.method, view.symbol, view.start_date, view.end_date,
        view.first_seen_at, view.completed_at, view.wire_bytes, view.wire_sha256,
        view.decoded_bytes, view.response_type, view.client_version, len(view.data),
        view.page_number, view.page_size,
    )


def run_once(*, accept_response):
    """Run after the caller's durable reservation and exact-snapshot release gate.

An attempted protocol request is counted before its first OS send, even if that
send fails ambiguously. Short-write completions are not additional requests.
"""
    budget = _Budget()
    state, loaded = _Session(budget), _SDK()
    start = end = None
    reason = "internal_error"
    acquired = _LOCK.acquire(blocking=False)
    result = view = sdk = accepted = None
    closed = restored = disposed = True
    try:
        if not acquired:
            raise _Fault("concurrent_use")
        if not callable(accept_response):
            raise _Fault("callback_rejected")
        with redirect_stdout(_Discard()), redirect_stderr(_Discard()):
            try:
                sdk = loaded.load()
            except _Fault:
                raise
            except Exception:
                raise _Fault("sdk_integrity") from None
            state.check()
            # This source's current mainland window uses Shanghai civil dates.
            from zoneinfo import ZoneInfo
            end_day = budget.started.astimezone(ZoneInfo("Asia/Shanghai")).date() - timedelta(days=1)
            start, end = (end_day - timedelta(days=13)).isoformat(), end_day.isoformat()
            loaded.bind(state)
            steps = (
                ("login", None, "00", "01"),
                ("query_trade_dates", None, "33", "34"),
                ("query_stock_basic", SYMBOLS[0], "45", "46"),
                ("query_stock_basic", SYMBOLS[1], "45", "46"),
                ("query_history_k_data_plus", SYMBOLS[0], "95", "96"),
                ("query_history_k_data_plus", SYMBOLS[1], "95", "96"),
                ("logout", None, "02", "03"),
            )
            for method, symbol, request_type, response_type in steps:
                state.begin(method, symbol, request_type, response_type)
                if method == "login":
                    result = sdk.login()
                elif method == "logout":
                    result = sdk.logout()
                elif method == "query_trade_dates":
                    result = sdk.query_trade_dates(start_date=start, end_date=end)
                elif method == "query_stock_basic":
                    result = sdk.query_stock_basic(code=symbol, code_name="")
                else:
                    result = sdk.query_history_k_data_plus(
                        symbol, ",".join(DAILY_FIELDS), start_date=start, end_date=end,
                        frequency="d", adjustflag="3",
                    )
                state.check()
                view = _view(state, result, start, end)
                state.responses.append(_metadata(view))
                state.check()
                try:
                    accepted = accept_response(view)
                    if accepted is not None:
                        state.fail("callback_rejected")
                except (KeyboardInterrupt, SystemExit):
                    state.fail("interrupted")
                except Exception:
                    state.fail("callback_rejected")
                finally:
                    view = result = accepted = None
                state.check()
            reason = "complete"
    except _Fault as exc:
        reason = state.reason or exc.reason
    except (KeyboardInterrupt, SystemExit):
        reason = state.reason or "interrupted"
    except Exception:
        reason = state.reason or "protocol"
    finally:
        result = view = sdk = accepted = None
        if state.socket is not None:
            try:
                state.socket.close()
            except BaseException:
                reason = state.reason or "cleanup"
            closed = state.socket.closed
        restored = loaded.close()
        state.dispose()
        disposed = (state.socket is None and state.header is None and state.tail is None
                    and state.utf8 is None and state.plain_frame is None)
        if acquired:
            _LOCK.release()
        if not closed or not restored or not disposed:
            reason = "cleanup"
        try:
            budget.check()
        except _Fault as exc:
            if reason == "complete":
                reason = exc.reason
    return TransportReceipt(
        "COMPLETE" if reason == "complete" else "FAILED", reason,
        _stamp(budget.started), _stamp(budget.last_wall), start, end, state.requests,
        state.wire, state.decoded, state.hash.hexdigest(), tuple(state.responses),
        closed, restored, disposed,
    )
