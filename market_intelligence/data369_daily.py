"""Bounded 369 daily archive request/response adapter, without live dispatch."""

from dataclasses import dataclass
from datetime import date, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
from types import MappingProxyType
from urllib.parse import urlencode

from .models import Invalid, instant


ENDPOINT = "https://api.369.top/api/a/stock/day_kline"
SYMBOLS = MappingProxyType({
    "000001.SH": "sh000001",
    "399001.SZ": "sz399001",
    "399006.SZ": "sz399006",
})
SHANGHAI = timezone(timedelta(hours=8))
ARCHIVE_CANDIDATE_TIME = time(16)
SUCCESS_CODE = 200
MAX_ROWS = 4
MAX_BODY_BYTES = 8192
MAX_JSON_DEPTH = 32


@dataclass(frozen=True)
class ArchiveDiagnostic:
    """No prices, provider messages, response bodies or live evidence."""

    status: str
    accepted_sessions: int = 0
    duplicate_rows: int = 0
    conflicting_rows: int = 0
    rejected_rows: int = 0
    missing_sessions: tuple[str, ...] = ()


def _date(value):
    if type(value) is not str or len(value) != 10:
        raise Invalid("invalid_session")
    try:
        result = date.fromisoformat(value)
    except ValueError:
        raise Invalid("invalid_session") from None
    if result.isoformat() != value:
        raise Invalid("invalid_session")
    return result


def _window(symbol, sessions, as_of):
    if type(symbol) is not str or symbol not in SYMBOLS:
        raise Invalid("unsupported_symbol")
    if type(sessions) is not tuple or len(sessions) != 2:
        raise Invalid("invalid_window")
    previous, latest = map(_date, sessions)
    if not timedelta(days=1) <= latest - previous <= timedelta(days=14):
        raise Invalid("invalid_window")
    try:
        now = instant(as_of).astimezone(SHANGHAI)
    except OverflowError:
        raise Invalid("invalid_as_of") from None
    if not timedelta(0) <= now.date() - latest <= timedelta(days=14):
        raise Invalid("invalid_window")
    if latest == now.date() and now.time() < ARCHIVE_CANDIDATE_TIME:
        raise Invalid("before_archive_candidate_time")


def request_url(symbol, sessions, as_of):
    """Plan a narrow GET; sessions must come from a trusted exchange calendar."""
    _window(symbol, sessions, as_of)
    previous, latest = sessions
    query = urlencode({
        "code": SYMBOLS[symbol], "start_date": previous, "end_date": latest,
        "limit": MAX_ROWS, "fields": "date,close",
    })
    # No adjust parameter uses the documented unadjusted default.
    return f"{ENDPOINT}?{query}"


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_key")
        result[key] = value
    return result


def _decimal(token):
    if len(token) > 48:
        raise ValueError("oversize_number")
    return Decimal(token)


def _nonfinite(_token):
    raise ValueError("nonfinite_json")


def _check_json_depth(text):
    # Bound structural nesting; the decoder still validates the JSON grammar.
    depth = 0
    quoted = escaped = False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > MAX_JSON_DEPTH:
                raise ValueError("excessive_json_depth")
        elif char in "]}":
            depth -= 1
            if depth < 0:
                raise ValueError("unbalanced_json")


def inspect_archive(body, symbol, sessions, as_of):
    """Check an injected archive response against its exact requested window.

    Date freshness is relative to the caller's expected session, not proof of
    publication/finality. The provider body is never returned or persisted.
    """
    _window(symbol, sessions, as_of)
    if type(body) is not bytes or not 0 < len(body) <= MAX_BODY_BYTES:
        return ArchiveDiagnostic("invalid_body", missing_sessions=sessions)
    try:
        text = body.decode("utf-8")
        _check_json_depth(text)
        envelope = json.loads(text, object_pairs_hook=_unique_pairs,
                              parse_float=_decimal, parse_constant=_nonfinite)
    except (ValueError, InvalidOperation, RecursionError):
        return ArchiveDiagnostic("invalid_json", missing_sessions=sessions)
    if (type(envelope) is not dict or type(envelope.get("code")) is not int
            or envelope["code"] != SUCCESS_CODE or type(envelope.get("data")) is not dict):
        return ArchiveDiagnostic("invalid_envelope", missing_sessions=sessions)
    data = envelope["data"]
    if data.get("code") != SYMBOLS[symbol]:
        return ArchiveDiagnostic("wrong_symbol", missing_sessions=sessions)
    if data.get("adjust") != "none":
        return ArchiveDiagnostic("wrong_adjustment", missing_sessions=sessions)
    rows = data.get("rows")
    if type(rows) is not list or len(rows) > MAX_ROWS:
        return ArchiveDiagnostic("invalid_rows", missing_sessions=sessions)

    dated = {day: [] for day in sessions}
    rejected = 0
    for row in rows:
        if type(row) is not dict or type(row.get("date")) is not str or row["date"] not in dated:
            rejected += 1
            continue
        value = row.get("close")
        if (set(row) - {"date", "close", "code"} or row.get("code", SYMBOLS[symbol]) != SYMBOLS[symbol]
                or type(value) not in (int, Decimal) or not 0 < value <= 1_000_000_000):
            dated[row["date"]].append(None)
        else:
            dated[row["date"]].append(value)

    accepted, duplicates, conflicts = set(), 0, 0
    for day, values in dated.items():
        if not values:
            continue
        if None in values:
            rejected += len(values)
        elif len(set(values)) != 1:
            conflicts += len(values)
        else:
            accepted.add(day)
            duplicates += len(values) - 1

    missing = tuple(day for day in sessions if day not in accepted)
    if rejected or conflicts:
        status = "rejected_window"
    elif not accepted:
        status = "empty_window"
    elif sessions[-1] not in accepted:
        status = "stale_window"
    elif missing:
        status = "missing_previous_close"
    else:
        status = "complete_window"
    return ArchiveDiagnostic(status, len(accepted), duplicates, conflicts, rejected, missing)
