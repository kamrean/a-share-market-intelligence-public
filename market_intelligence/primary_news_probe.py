"""P4FED's fixed one-use Board RSS observation; only a summary can leave RAM."""

from datetime import timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
import os
import re
import tomllib
import xml.etree.ElementTree as ET

from . import sources
from .models import Invalid, digest, exact, fingerprint, instant, integer, stamp, text, utc_now
from .store import CHECKOUT, external_path
from .tencent_capture import _append, _attached, _open_journal, _sync_namespace

_OPERATION = "p4fed-board-rss-20261121-v1"
_WINDOW = 2678400
_MAX_ITEMS = 1000
_PINNED = {
    "config/sources.toml": "d51fd5f8da88fe5ba80cd98dc785d601d41fd6ea7ce603904c17415d197c1761",
    "market_intelligence/sources.py": "321a7e1eabc0592420dbca5ade7cccd2c957b5f1c43137c1c575152001cc2953",
}
_RELEASE = re.compile(
    r"https://www\.federalreserve\.gov/newsevents/pressreleases/[a-z]+[0-9]{8}[a-z][0-9]*\.htm"
)
_DATE = re.compile(
    r"(?:(Mon|Tue|Wed|Thu|Fri|Sat|Sun), )?[0-9]{1,2} [A-Za-z]{3} [0-9]{4} "
    r"[0-9]{2}:[0-9]{2}:[0-9]{2} (?:GMT|UTC|UT|[+-](?:[01][0-9]|2[0-3])[0-5][0-9])"
)
_BOARD = r"(?:Federal Reserve Board|Board of Governors of the Federal Reserve System)"
_BOARD_URLS = {"https://www.federalreserve.gov", "https://www.federalreserve.gov/",
               "https://www.federalreserve.gov/aboutthefed.htm", sources.FED_URL}
_CHANNEL_URLS = _BOARD_URLS | {
    "https://www.federalreserve.gov/newsevents.htm",
    "https://www.federalreserve.gov/newsevents/pressreleases.htm",
    "https://www.federalreserve.gov/newsevents/pressreleases/default.htm",
}
_RIGHTS = {"copyright", "rights", "rightsholder", "license"}
_ATTRIBUTION = _RIGHTS | {
    "source", "originalsource", "author", "creator", "publisher", "contributor",
    "attribution", "credit", "owner", "managingeditor", "webmaster",
}
_PARTITION = ("fresh", "stale", "duplicate", "conflict", "future", "invalid",
              "out_of_scope", "attribution")


def pending(snapshot_id):
    fingerprint(snapshot_id)
    return {"schema_version": 1, "package": "P4FED", "snapshot_id": snapshot_id,
            "status": "NOT_RUN", "observation": None}


def _summary_status(counts):
    if counts["fresh"]:
        return "FRESH", None
    if any(counts[key] for key in ("conflict", "future", "invalid", "out_of_scope", "attribution")):
        return "UNKNOWN", "inconclusive_items"
    return "NO_FRESH", None


def _time(value):
    parsed = instant(value)
    if stamp(parsed) != value:
        raise Invalid("noncanonical_time")
    return parsed


def validate_receipt(value, snapshot_id, *, terminal=False):
    """Validate the closed, results-only envelope used by the staged gate."""
    try:
        base = pending(snapshot_id)
        exact(value, base)
        for key in ("schema_version", "package", "snapshot_id"):
            if type(value[key]) is not type(base[key]) or value[key] != base[key]:
                raise Invalid("receipt_binding")
        status, row = value["status"], value["observation"]
        if type(status) is not str or type(terminal) is not bool:
            raise Invalid("receipt_type")
        if status == "NOT_RUN":
            if terminal or row is not None:
                raise Invalid("receipt_pending")
            return value
        exact(row, {"operation_id", "started_at", "finished_at", "captured_at",
                    "freshness_seconds", "dispatch_attempts", "counts", "error"})
        if type(row["operation_id"]) is not str or row["operation_id"] != _OPERATION:
            raise Invalid("receipt_operation")
        integer(row["freshness_seconds"], _WINDOW, _WINDOW)
        attempts = integer(row["dispatch_attempts"], 0, 1)
        started = _time(row["started_at"])
        error, counts = row["error"], row["counts"]
        if error is not None and type(error) is not str:
            raise Invalid("receipt_error")
        finished = None if row["finished_at"] is None else _time(row["finished_at"])
        if (finished is None) != (error == "clock_rollback") or (
                finished is not None and finished < started):
            raise Invalid("receipt_timing")
        if counts is not None:
            exact(counts, (*_PARTITION, "total", "within_24h"))
            total = integer(counts["total"], 0, _MAX_ITEMS)
            for key in _PARTITION:
                integer(counts[key], 0, total)
            integer(counts["within_24h"], 0, counts["fresh"])
            if (sum(counts[key] for key in _PARTITION) != total
                    or (counts["conflict"] == 1 and not counts["invalid"])
                    or (counts["duplicate"] and not (counts["fresh"] or counts["stale"]))
                    or attempts != 1 or finished is None
                    or not started <= _time(row["captured_at"]) <= finished
                    or (status, error) != _summary_status(counts)):
                raise Invalid("receipt_counts")
        elif (row["captured_at"] is not None or (status, error, attempts) not in {
                ("FAILED", "transport_failed", 1), ("FAILED", "invalid_response", 1),
                ("UNKNOWN", "transport_unknown", 1), ("UNKNOWN", "gate_rejected", 0),
                ("UNKNOWN", "interrupted", 0), ("UNKNOWN", "interrupted", 1),
                ("UNKNOWN", "clock_rollback", 0), ("UNKNOWN", "clock_rollback", 1)}):
            raise Invalid("receipt_failure")
        return value
    except (Invalid, TypeError, ValueError, OverflowError):
        raise Invalid("invalid_primary_news_receipt") from None


def _tag(name):
    return name.rsplit("}", 1)[-1].lower()


def _board_value(key, value):
    value = value.strip()
    identity = _BOARD if key not in _RIGHTS else rf"(?:(?:Copyright|\(c\)|\u00a9) (?:[0-9]{{4}} )?)?{_BOARD}"
    return value in _BOARD_URLS or re.fullmatch(identity, value) is not None


def _board_only(node):
    # Inspect attribution separately from release fields; never inspect descriptions.
    stack = [node]
    while stack:
        part = stack.pop()
        key = _tag(part.tag)
        markers = [(_tag(name), value) for name, value in part.attrib.items()]
        if any(name in _ATTRIBUTION and not _board_value(name, value)
               for name, value in markers):
            return False
        if key in _ATTRIBUTION:
            label = (part.text or "").strip()
            if (len(part) or (not label and not markers)
                    or (label and not _board_value(key, label))
                    or any(name not in {"url", "href", "resource"} or value not in _BOARD_URLS
                           for name, value in markers)):
                return False
        stack.extend(child for child in part if _tag(child.tag) not in
                     {"item", "channel", "description", "encoded", "summary"})
    return True


def _item_fields(item):
    fields = []
    for name in ("title", "link", "pubDate"):
        matches = [child for child in item if _tag(child.tag) == name.lower()]
        if len(matches) != 1 or matches[0].tag != name or len(matches[0]):
            raise Invalid("ambiguous_item")
        if name == "link" and any(_tag(key) in {"href", "url"} for key in matches[0].attrib):
            raise Invalid("ambiguous_item")
        fields.append(matches[0].text or "")
    title, link, date = fields
    text(title, "title", 2000)
    if any(c in title for c in "\r\n\t"):
        raise Invalid("invalid_title")
    match = _DATE.fullmatch(date.strip())
    if match is None:
        raise Invalid("invalid_date")
    published = parsedate_to_datetime(match[0])
    if published.tzinfo is None or (
            match[1] and match[1] != ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")[published.weekday()]):
        raise Invalid("ambiguous_date")
    return title, link, published


def _item_locators(item):
    for field in item:
        if _tag(field.tag) == "link":
            values = [field.text or ""]
            values.extend(value for name, value in field.attrib.items() if _tag(name) in {"href", "url"})
            yield from (value for value in values if _RELEASE.fullmatch(value))


def _parse_counts(body, captured):
    value = sources._safe_text(body)
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)", value, re.I):
        raise Invalid("forbidden_xml")
    encoding = re.search(r"""<\?xml[^>]*encoding\s*=\s*['"]([^'"]+)""", value, re.I)
    if encoding and encoding[1].lower() not in {"utf-8", "utf8", "us-ascii"}:
        raise Invalid("forbidden_encoding")
    root = ET.fromstring(value)
    if root.tag != "rss" or root.get("version", "2.0") != "2.0" or len(root) != 1 or root[0].tag != "channel":
        raise Invalid("invalid_rss")
    channel = root[0]
    items = channel.findall("item")
    links = channel.findall("link")
    if (len(items) > _MAX_ITEMS or sum(1 for _ in root.iter()) > 5000
            or sum(_tag(node.tag) == "item" for node in root.iter()) != len(items)
            or len(links) > 1 or (links and (len(links[0]) or links[0].text not in _CHANNEL_URLS))
            or not _board_only(root) or not _board_only(channel)):
        raise Invalid("invalid_channel")

    counts = dict.fromkeys(_PARTITION, 0)
    counts.update(total=len(items), within_24h=0)
    groups = {}
    tainted = set()
    for item in items:
        try:
            title, link, published = _item_fields(item)
        except (Invalid, ValueError, OverflowError):
            counts["invalid"] += 1
            tainted.update(_item_locators(item))
            continue
        if _RELEASE.fullmatch(link) is None:
            counts["out_of_scope"] += 1
            continue
        groups.setdefault(link, []).append((title, published, _board_only(item)))

    # Resolve conflicts before making any freshness decision for this locator.
    for link, rows in groups.items():
        _, published, board = rows[0]
        if link in tainted or not all(row == rows[0] for row in rows):
            counts["conflict"] += len(rows)
        elif not board:
            counts["attribution"] += len(rows)
        elif published > captured:
            counts["future"] += len(rows)
        else:
            age = captured - published
            counts["fresh" if age <= timedelta(seconds=_WINDOW) else "stale"] += 1
            counts["within_24h"] += int(age <= timedelta(days=1))
            counts["duplicate"] += len(rows) - 1
    return counts


def _pinned_source():
    contents = {}
    for name, expected in _PINNED.items():
        value = (CHECKOUT / name).read_text(encoding="utf-8")
        if digest(value.encode("utf-8")) != expected:
            raise Invalid("probe_inputs_changed")
        contents[name] = value
    catalog = tomllib.loads(contents["config/sources.toml"])
    return sources.Source.from_dict(next(row for row in catalog["sources"] if row["source_id"] == "fed_press"))


def _windows_profile():
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    security = ctypes.WinDLL("advapi32", use_last_error=True)
    profiles = ctypes.WinDLL("userenv", use_last_error=True)
    process = kernel.GetCurrentProcess
    process.argtypes, process.restype = (), wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes, close.restype = (wintypes.HANDLE,), wintypes.BOOL
    open_token = security.OpenProcessToken
    open_token.argtypes = (wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE))
    open_token.restype = wintypes.BOOL
    profile = profiles.GetUserProfileDirectoryW
    profile.argtypes = (wintypes.HANDLE, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
    profile.restype = wintypes.BOOL

    token = wintypes.HANDLE()
    if not open_token(process(), 0x0008, ctypes.byref(token)):  # TOKEN_QUERY
        raise Invalid("journal_account_unavailable")
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buffer))
        if not profile(token, buffer, ctypes.byref(size)) or not 1 <= size.value <= len(buffer):
            raise Invalid("journal_account_unavailable")
        return buffer.value
    finally:
        if not close(token):
            raise Invalid("journal_account_unavailable")


def _path():
    if os.name == "nt":
        location = _windows_profile()
        parent = ("AppData", "Local", "a-share-market-intelligence")
    elif os.name == "posix":
        import pwd

        location = pwd.getpwuid(os.getuid()).pw_dir
        parent = (".a-share-market-intelligence",)
    else:
        raise Invalid("journal_account_unavailable")
    if type(location) is not str or not location or "\0" in location:
        raise Invalid("invalid_journal_account_path")
    home = Path(location)
    if not home.is_absolute() or home == Path(home.anchor) or ".." in home.parts:
        raise Invalid("invalid_journal_account_path")
    if os.name == "nt" and (re.fullmatch(r"[A-Za-z]:", home.drive) is None
                            or any(":" in part or part.endswith((" ", ".")) for part in home.parts[1:])):
        raise Invalid("invalid_journal_account_path")
    return home.joinpath(*parent, _OPERATION + ".jsonl")


def _fetch_summary(source, row):
    row["dispatch_attempts"] = 1
    try:
        response = sources.fetch(source)
    except sources.TransportError as exc:
        if exc.outcome == "failed":
            return "FAILED", "transport_failed"
        return "UNKNOWN", "transport_unknown"
    except Exception:
        return "UNKNOWN", "transport_unknown"
    captured = utc_now()
    if captured < _time(row["started_at"]):
        return "UNKNOWN", "clock_rollback"
    try:
        if type(response) is not sources.Response or type(response.content_type) is not str:
            raise Invalid("invalid_response")
        response.validate()
        counts = _parse_counts(response.body, captured)
    except (Invalid, ET.ParseError, ValueError, OverflowError):
        return "FAILED", "invalid_response"
    row.update(captured_at=stamp(captured), counts=counts)
    return _summary_status(counts)


def _approval(value):
    if type(value) is not tuple or len(value) != 2:
        raise Invalid("invalid_probe_gate_binding")
    for item in value:
        fingerprint(item)
    return value


def _observe(authorize):
    """Only the governance caller supplies the trusted staged gate callback."""
    try:
        approval = _approval(authorize())
        snapshot, chain = approval
        receipt = pending(snapshot)
        source = _pinned_source()
        source.authorize()
    except (Exception, KeyboardInterrupt, SystemExit):
        raise Invalid("probe_not_authorized") from None

    started = utc_now()
    row = {"operation_id": _OPERATION, "started_at": stamp(started), "finished_at": None,
           "captured_at": None, "freshness_seconds": _WINDOW, "dispatch_attempts": 0,
           "counts": None, "error": None}
    try:
        path = external_path(_path())
        # Exclusive creation consumes even an empty/partial marker. Never read it.
        with _open_journal(path) as handle:
            _attached(handle, path)
            _append(handle, {"kind": "reservation", "operation_id": _OPERATION,
                             "snapshot_id": snapshot, "approval_chain_sha256": chain,
                             "started_at": row["started_at"]})
            _sync_namespace(path)
            try:
                try:
                    current = _approval(authorize())
                except Exception:
                    current = None
                if current != approval:
                    status, error = "UNKNOWN", "gate_rejected"
                elif utc_now() < started:
                    status, error = "UNKNOWN", "clock_rollback"
                else:
                    _attached(handle, path)
                    status, error = _fetch_summary(source, row)
            except (KeyboardInterrupt, SystemExit):
                status, error = "UNKNOWN", "interrupted"
                row.update(captured_at=None, counts=None)

            finished = utc_now()
            if finished < _time(row["captured_at"] or row["started_at"]) or error == "clock_rollback":
                status, error = "UNKNOWN", "clock_rollback"
                row.update(captured_at=None, counts=None)
            else:
                row["finished_at"] = stamp(finished)
            row["error"] = error
            receipt.update(status=status, observation=row)
            validate_receipt(receipt, snapshot, terminal=True)
            _attached(handle, path)
            _append(handle, {"kind": "final", "receipt": receipt})
            _attached(handle, path)
    except FileExistsError:
        raise Invalid("probe_already_consumed") from None
    except (Exception, KeyboardInterrupt, SystemExit):
        raise Invalid("probe_journal_unavailable") from None
    return receipt
