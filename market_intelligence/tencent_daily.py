"""Pure Tencent daily observations; never a source-rights or research approval."""

from datetime import date, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import re
import tomllib
from urllib.parse import urlencode

from .intelligence import Fact, PARSERS, decode_input
from .models import Invalid, canonical, digest, exact, instant, stamp
from .source_policy import PURPOSE, RIGHTS, assess_source_policy, bounded_copy

ROOT = Path(__file__).resolve().parents[1]
CONFIG_HASH = "c87f25d3375039e81f820bf465c7e12c6b35df1d2537dfed3068866c8b0a5cf4"
SOURCE = "tencent_daily_index"
HOST = "proxy.finance.qq.com"
PATH = "/ifzqgtimg/appstock/app/newfqkline/get"
ENDPOINT = "https://" + HOST + PATH
SYMBOLS = {"sh000001": "000001.SH", "sz399001": "399001.SZ", "sz399006": "399006.SZ"}
MAX_BYTES = 512000
MAX_ROWS = 32
SHANGHAI = timezone(timedelta(hours=8), "Asia/Shanghai")
PARSER = "tencent-index-daily-v1"
REASONS = frozenset({"invalid_row", "invalid_date", "outside_window", "not_scheduled_session",
                     "invalid_ohlc", "conflicting_key"})
FRESHNESS = frozenset({"CURRENT", "LATEST_PRESENT_GAPS", "STALE", "EMPTY"})


def _config():
    body = (ROOT / "config/tencent_daily.toml").read_text(encoding="utf-8")
    if digest(body.encode()) != CONFIG_HASH:
        raise Invalid("config_changed")
    return tomllib.loads(body)


def _day(value):
    if type(value) is not str or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
        raise Invalid("invalid_date")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise Invalid("invalid_date") from None


def plan(as_of):
    """Scheduled sessions, not a claim about emergency closures or finality."""
    now = instant(as_of).astimezone(SHANGHAI)
    cfg = _config()["calendar"]
    if now.year != cfg["year"]:
        raise Invalid("calendar_unavailable")
    holidays = [(date.fromisoformat(a), date.fromisoformat(b)) for a, b in cfg["holiday_ranges"]]

    def is_session(day):
        return day.weekday() < 5 and not any(a <= day <= b for a, b in holidays)

    end = now.date()
    if now.timetz().replace(tzinfo=None) < time(15):
        end -= timedelta(days=1)
    while end.year == cfg["year"] and not is_session(end):
        end -= timedelta(days=1)
    start = end - timedelta(days=13)
    if start.year != cfg["year"] or end.year != cfg["year"]:
        raise Invalid("calendar_unavailable")
    sessions = [(start + timedelta(days=i)).isoformat() for i in range(14)
                if is_session(start + timedelta(days=i))]
    return {"as_of": stamp(now), "start_date": start.isoformat(), "end_date": end.isoformat(),
            "expected_sessions": sessions, "calendar_version": cfg["version"]}


def _plan(value):
    exact(value, {"as_of", "start_date", "end_date", "expected_sessions", "calendar_version"})
    if value != plan(value["as_of"]):
        raise Invalid("plan_changed")
    return value


def request_path(symbol, window):
    if symbol not in SYMBOLS:
        raise Invalid("unsupported_symbol")
    _plan(window)
    return PATH + "?" + urlencode({"_var": "kline_dayqfq", "param":
        f"{symbol},day,{window['start_date']},{window['end_date']},14,qfq"})


def source_policy():
    return {"schema_version": 1, "version": "p4t-unknown-rights-v1", "sources": [{
        "source_id": SOURCE, "synthetic": False, "purpose": PURPOSE,
        "origin": "https://" + HOST, "locator_prefix": "https://" + HOST + "/ifzqgtimg/",
        "retention": {"class": "metadata_only", "max_age_seconds": 0,
                      "basis_refs": ["https://www.tencent.com/zh-cn/statement.html"]},
        "rights": {key: {"state": "unknown", "basis_refs": []} for key in RIGHTS}}]}


def composition_decision():
    """Use the existing policy engine; a technical exception cannot pass it."""
    decision = assess_source_policy(source_policy(), SOURCE, "local_composition", PURPOSE)
    if decision["state"] != "unknown" or decision["operation_enabled"] is not False:
        raise Invalid("source_policy_changed")
    return decision


def _number(value):
    if (type(value) is not str or len(value) > 40
            or re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value) is None):
        raise Invalid("invalid_ohlc")
    number = Decimal(value)
    if not Decimal("1e-12") <= number <= Decimal("1e15"):
        raise Invalid("invalid_ohlc")
    return number


def _rows(body, symbol):
    if type(body) is not bytes or len(body) > MAX_BYTES:
        raise Invalid("response_size")
    body = body.strip()
    if body.startswith(b"kline_dayqfq="):
        body = body[len(b"kline_dayqfq="):]
        if body.endswith(b";"):
            body = body[:-1]
    try:
        value = bounded_copy(decode_input(body), MAX_BYTES)
        if (type(value) is not dict or type(value.get("code")) is not int or value["code"] != 0
                or type(value.get("data")) is not dict
                or type(value["data"].get(symbol)) is not dict):
            raise Invalid("response_identity")
        item = value["data"][symbol]
        populated = [name for name in ("day", "qfqday") if item.get(name)]
        present = [name for name in ("day", "qfqday") if name in item]
        if len(populated) > 1 or (not populated and len(present) != 1):
            raise Invalid("adjustment_ambiguous")
        adjustment = (populated or present)[0]
        rows = item[adjustment]
        if type(rows) is not list or len(rows) > MAX_ROWS:
            raise Invalid("row_limit")
        return rows, adjustment
    except (Invalid, KeyError, TypeError, ValueError, RecursionError):
        raise Invalid("response_schema") from None


def parse(body, symbol, window, observed_at):
    """Build ephemeral core Fact shapes and closed, non-price diagnostics."""
    if symbol not in SYMBOLS:
        raise Invalid("unsupported_symbol")
    _plan(window)
    seen = instant(observed_at)
    if seen < instant(window["as_of"]) or seen.astimezone(SHANGHAI).year != 2026:
        raise Invalid("observation_time")
    rows, adjustment = _rows(body, symbol)
    groups, reasons, returned = {}, {}, []

    def reject(reason, count=1):
        reasons[reason] = reasons.get(reason, 0) + count

    for row in rows:
        if type(row) is not list or not row:
            reject("invalid_row")
            continue
        try:
            day = _day(row[0]).isoformat()
        except Invalid:
            reject("invalid_date")
            continue
        returned.append(day)
        if not window["start_date"] <= day <= window["end_date"]:
            reject("outside_window")
            continue
        if day not in window["expected_sessions"]:
            reject("not_scheduled_session")
            continue
        reason, close = None, None
        if (not 6 <= len(row) <= 20
                or any(type(v) not in (str, int, float, bool, type(None)) for v in row)
                or any(type(v) is str and len(v) > 256 for v in row)):
            reason = "invalid_row"
        else:
            try:
                opening, close, high, low = (_number(v) for v in row[1:5])
                if not low <= min(opening, close) <= max(opening, close) <= high:
                    raise Invalid("invalid_ohlc")
            except Invalid:
                reason = "invalid_ohlc"
        groups.setdefault(day, []).append((digest(row), float(close) if close is not None else None, reason))

    facts, duplicate_rows, conflicts = [], 0, 0
    for day, values in sorted(groups.items()):
        if len({value[0] for value in values}) != 1:
            conflicts += len(values)
            reject("conflicting_key", len(values))
            continue
        if values[0][2]:
            reject(values[0][2], len(values))
            continue
        duplicate_rows += len(values) - 1
        payload = {"kind": "bar", "symbol": SYMBOLS[symbol], "session_id": day,
                   "close": values[0][1], "turnover": None}
        # No retention grant: this shape expires at possession and cannot enter research.
        fact = {"source_id": SOURCE, "locator": ENDPOINT, "mode": "live_observation",
                "published_at": None, "retrieved_at": stamp(seen), "first_seen_at": stamp(seen),
                "expires_at": stamp(seen), "parser_version": PARSERS["bar"],
                "content_hash": digest(payload), "payload": payload}
        Fact.from_dict(fact)
        facts.append(fact)
    accepted_dates = [f["payload"]["session_id"] for f in facts]
    missing = sorted(set(window["expected_sessions"]) - set(accepted_dates))
    latest = max(accepted_dates, default=None)
    freshness = ("EMPTY" if not latest else "STALE" if latest != window["end_date"] else
                 "LATEST_PRESENT_GAPS" if missing else "CURRENT")
    decision = composition_decision()
    summary = {"symbol": symbol, "benchmark_id": SYMBOLS[symbol], "parser_version": PARSER,
               "adjustment": adjustment, "total": len(rows), "accepted": len(facts),
               "duplicate_rows": duplicate_rows, "conflicting_rows": conflicts,
               "rejected": sum(reasons.values()), "reasons": dict(sorted(reasons.items())),
               "latest_returned_session": max(returned, default=None),
               "latest_accepted_session": latest, "expected_latest_session": window["end_date"],
               "missing_sessions": missing, "freshness": freshness,
               "response_identity_bound": True, "publication_time": None, "finality": "unknown",
               "unit_dependent_metrics": "unavailable", "source_policy_state": decision["state"],
               "normal_composition": "BLOCKED_SOURCE_RIGHTS"}
    return {"facts": facts, "summary": summary, "replay_id": digest({
        "facts": facts, "summary": summary, "window": window, "adjustment": adjustment})}


def validate_summary(value, window):
    """Only this fixed shape may leave process memory, never Fact payloads."""
    from .models import integer
    keys = {"symbol", "benchmark_id", "parser_version", "adjustment", "total", "accepted",
            "duplicate_rows", "conflicting_rows", "rejected", "reasons", "latest_returned_session",
            "latest_accepted_session", "expected_latest_session", "missing_sessions", "freshness",
            "response_identity_bound", "publication_time", "finality", "unit_dependent_metrics",
            "source_policy_state", "normal_composition"}
    exact(value, keys)
    _plan(window)
    if (value["symbol"] not in SYMBOLS or value["benchmark_id"] != SYMBOLS[value["symbol"]]
            or value["parser_version"] != PARSER or value["adjustment"] not in {"day", "qfqday"}
            or value["response_identity_bound"] is not True or value["publication_time"] is not None
            or value["finality"] != "unknown" or value["unit_dependent_metrics"] != "unavailable"
            or value["source_policy_state"] != "unknown"
            or value["normal_composition"] != "BLOCKED_SOURCE_RIGHTS"):
        raise Invalid("invalid_summary")
    for key in ("total", "accepted", "duplicate_rows", "conflicting_rows", "rejected"):
        integer(value[key], 0, MAX_ROWS)
    if (value["accepted"] + value["duplicate_rows"] + value["rejected"] != value["total"]
            or type(value["reasons"]) is not dict or not set(value["reasons"]) <= REASONS):
        raise Invalid("invalid_summary")
    for count in value["reasons"].values():
        integer(count, 1, MAX_ROWS)
    if (sum(value["reasons"].values()) != value["rejected"]
            or value["conflicting_rows"] != value["reasons"].get("conflicting_key", 0)):
        raise Invalid("invalid_summary")
    missing = value["missing_sessions"]
    if (type(missing) is not list or missing != sorted(set(missing))
            or not set(missing) <= set(window["expected_sessions"])
            or value["accepted"] != len(window["expected_sessions"]) - len(missing)):
        raise Invalid("invalid_summary")
    present = sorted(set(window["expected_sessions"]) - set(missing))
    latest = present[-1] if present else None
    returned = value["latest_returned_session"]
    if returned is not None:
        _day(returned)
    expected = ("EMPTY" if not latest else "STALE" if latest != window["end_date"] else
                "LATEST_PRESENT_GAPS" if missing else "CURRENT")
    if (value["latest_accepted_session"] != latest or value["expected_latest_session"] != window["end_date"]
            or value["freshness"] != expected or (latest and (returned is None or returned < latest))):
        raise Invalid("invalid_summary")
    canonical(value)
    return value
