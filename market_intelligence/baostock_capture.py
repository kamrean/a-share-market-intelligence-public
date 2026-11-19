"""One released session; market inputs exist only during in-process verification."""

from contextlib import redirect_stderr, redirect_stdout
from datetime import date, timedelta, timezone
import csv
import io
import json
from pathlib import Path
import re
import sqlite3
import sys

from . import daily_market as daily
from . import preparation
from .models import Invalid, UNIVERSE, canonical, digest, exact, fingerprint, instant, integer, stamp, utc_now
from .source_policy import PURPOSE, RIGHTS
from .store import Busy, CHECKOUT, Store, external_path

ROOT = CHECKOUT
SOURCE = "baostock_standard_free"
TASK = "p4b-baostock-one-session"
INTENT = "p4b-baostock-one-session:"
VERSION = "p4b-ephemeral-capture-v1"
SDK_HASH = "0bf71c6069ab5890ff3596632f9c3f8f1fbc6bfcac582c2f9d6a5c11ab2cfaf8"
SYMBOLS = ("sh.000001", "sz.399001")
SHANGHAI = timezone(timedelta(hours=8))
TTL = 60
MAX_RECEIPT = 32768
LIMITATIONS = ["unauthenticated_unencrypted_tcp", "publication_time_unknown", "finality_unknown",
               "provider_calendar_not_independently_certified", "metadata_only_no_later_replay",
               "no_secure_ram_erasure_or_os_paging_claim", "not_live_research_or_mvp"]
STEPS = (("login", None, "01"), ("query_trade_dates", None, "34"),
         ("query_stock_basic", SYMBOLS[0], "46"), ("query_stock_basic", SYMBOLS[1], "46"),
         ("query_history_k_data_plus", SYMBOLS[0], "96"),
         ("query_history_k_data_plus", SYMBOLS[1], "96"), ("logout", None, "03"))
TRANSPORT_REASONS = frozenset({"complete", "unsupported_platform", "sdk_integrity", "sdk_context",
    "concurrent_use", "dns", "deadline", "clock_rollback", "connection", "send", "recv", "eof",
    "wire_limit", "frame_limit", "decoded_limit", "compression", "protocol", "pagination",
    "provider_error", "callback_rejected", "interrupted", "cleanup", "internal_error"})
REASONS = {"OBSERVED": "verified_two_indices", "PARTIAL": "partial_input", "EMPTY": "empty_input"}
FAILURE_REASONS = {"transport_failed", "computation_failed", "cleanup_failed"}
UNKNOWN_REASONS = {"transport_unknown", "interrupted", "lease_lost", "prior_attempt_incomplete"}
META_KEYS = {"index", "method", "symbol", "start_date", "end_date", "first_seen_at", "completed_at",
             "wire_bytes", "wire_sha256", "decoded_bytes", "response_type", "client_version",
             "row_count", "page_number", "page_size"}


def _require(condition):
    if not condition:
        raise Invalid("invalid_baostock_state")


def _time(value):
    result = instant(value)
    _require(stamp(result) == value)
    return result


def _day(value):
    _require(type(value) is str and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is not None)
    return date.fromisoformat(value)


def _id(value):
    _require(type(value) is str and re.fullmatch(r"[0-9a-f]{32}", value) is not None)


def _symbols(value):
    _require(type(value) is list and value == [s for s in SYMBOLS if s in value])


def _coverage():
    return {"covered": 0, "expected": 5, "missing": list(UNIVERSE)}


def _transport_record(value):
    exact(value, {"status", "reason", "started_at", "finished_at", "window_start", "window_end",
                  "requests_sent", "wire_bytes", "decoded_bytes", "wire_sha256", "responses",
                  "socket_closed", "bindings_restored", "transport_payloads_disposed"})
    _require(value["status"] in {"COMPLETE", "FAILED"} and value["reason"] in TRANSPORT_REASONS)
    _require((value["status"] == "COMPLETE") == (value["reason"] == "complete"))
    started, finished = _time(value["started_at"]), _time(value["finished_at"])
    _require(started <= finished)
    integer(value["requests_sent"], 0, 7)
    integer(value["wire_bytes"], 0, 512000)
    integer(value["decoded_bytes"], 0, 1048576)
    fingerprint(value["wire_sha256"])
    for key in ("socket_closed", "bindings_restored", "transport_payloads_disposed"):
        _require(type(value[key]) is bool)
    if value["window_start"] is None:
        _require(value["window_end"] is None and not value["responses"] and value["requests_sent"] == 0)
    else:
        end = started.astimezone(SHANGHAI).date() - timedelta(days=1)
        _require(_day(value["window_end"]) == end and _day(value["window_start"]) == end - timedelta(days=13))
    responses = value["responses"]
    _require(type(responses) is list and len(responses) <= value["requests_sent"])
    last, wire, decoded = started, 0, 0
    for index, row in enumerate(responses, 1):
        exact(row, META_KEYS)
        integer(row["index"], index, index)
        method, symbol, response_type = STEPS[index - 1]
        _require((row["method"], row["symbol"], row["response_type"]) == (method, symbol, response_type))
        _require(row["client_version"] == "00.9.40")
        first, complete = _time(row["first_seen_at"]), _time(row["completed_at"])
        _require(last <= first <= complete <= finished)
        last = complete
        integer(row["wire_bytes"], 1, 128000)
        integer(row["decoded_bytes"], 0, 1048576)
        fingerprint(row["wire_sha256"])
        integer(row["row_count"], 0, 14)
        data = index not in (1, 7)
        _require(row["page_number"] == (1 if data else None)
                 and row["page_size"] == (2000 if data else None))
        if data:
            integer(row["page_number"], 1, 1)
            integer(row["page_size"], 2000, 2000)
        else:
            _require(row["row_count"] == 0)
        window = index in (2, 5, 6)
        _require(row["start_date"] == (value["window_start"] if window else None)
                 and row["end_date"] == (value["window_end"] if window else None))
        wire += row["wire_bytes"]
        decoded += row["decoded_bytes"]
    _require(wire <= value["wire_bytes"] and decoded <= value["decoded_bytes"])
    if value["status"] == "COMPLETE":
        _require(len(responses) == value["requests_sent"] == 7 and value["window_start"] is not None)
        _require(all(value[k] for k in ("socket_closed", "bindings_restored", "transport_payloads_disposed")))
        _require([r["row_count"] for r in responses[1:4]] == [14, 1, 1])
    return value


def not_run_receipt(snapshot_id):
    return validate_observation_receipt({"schema_version": 1, "package_id": "P4B", "snapshot_id": snapshot_id,
        "status": "NOT_RUN", "reason": "awaiting_release", "observation": None}, snapshot_id)


def validate_observation_receipt(value, snapshot_id):
    """Deterministic closed-schema validation: no SDK, clock, store or file access."""
    try:
        fingerprint(snapshot_id)
        exact(value, {"schema_version", "package_id", "snapshot_id", "status", "reason", "observation"})
        integer(value["schema_version"], 1, 1)
        _require(value["package_id"] == "P4B" and value["snapshot_id"] == snapshot_id)
        status, reason = value["status"], value["reason"]
        if status == "NOT_RUN":
            _require(value["observation"] is None and reason == "awaiting_release")
            return value
        _require((status in REASONS and reason == REASONS[status])
                 or (status == "FAILED" and reason in FAILURE_REASONS)
                 or (status == "UNKNOWN" and reason in UNKNOWN_REASONS))
        o = value["observation"]
        exact(o, {"source_id", "implementation_version", "sdk_version", "sdk_wheel_sha256", "run_id",
                  "attempt_id", "started_at", "finished_at", "transport", "batch_possession_at", "input",
                  "composition", "cleanup", "live_market_coverage", "new_live_research_runs", "limitations"})
        _require(o["source_id"] == SOURCE and o["implementation_version"] == VERSION
                 and o["sdk_version"] == "0.9.4" and o["sdk_wheel_sha256"] == SDK_HASH)
        _id(o["run_id"])
        _id(o["attempt_id"])
        start = _time(o["started_at"])
        finish = _time(o["finished_at"]) if o["finished_at"] is not None else None
        _require(finish is None or start <= finish)
        t = o["transport"]
        if t is not None:
            _transport_record(t)
            _require(start <= _time(t["started_at"]) and finish is not None and _time(t["finished_at"]) <= finish)
        inp = o["input"]
        exact(inp, {"observed_symbols", "validated_symbols", "session_start", "session_end",
                    "latest_expected_session", "latest_session_symbols", "daily_rows_received",
                    "accepted_rows", "rejected_rows", "rejection_counts", "observed_input_coverage"})
        for key in ("observed_symbols", "validated_symbols", "latest_session_symbols"):
            _symbols(inp[key])
        _require(set(inp["latest_session_symbols"]) <= set(inp["validated_symbols"]) <= set(inp["observed_symbols"]))
        exact(inp["observed_input_coverage"], {"covered", "expected"})
        integer(inp["observed_input_coverage"]["covered"], 0, 2)
        integer(inp["observed_input_coverage"]["expected"], 5, 5)
        _require(inp["observed_input_coverage"]["covered"] == len(inp["validated_symbols"]))
        if inp["session_start"] is None:
            _require(inp["session_end"] is None and not inp["validated_symbols"])
        else:
            _require(t is not None and inp["validated_symbols"]
                     and _day(t["window_start"]) <= _day(inp["session_start"]) <= _day(inp["session_end"]) <= _day(t["window_end"]))
        if inp["latest_expected_session"] is not None:
            _require(t is not None and _day(t["window_start"]) <= _day(inp["latest_expected_session"]) <= _day(t["window_end"]))
        if t is None:
            _require(inp["daily_rows_received"] is None and not inp["observed_symbols"] and not inp["validated_symbols"])
        else:
            rows = [r for r in t["responses"] if r["method"] == "query_history_k_data_plus"]
            integer(inp["daily_rows_received"], 0, 28)
            _require(inp["daily_rows_received"] == sum(r["row_count"] for r in rows))
            _require(inp["observed_symbols"] == [s for s in SYMBOLS if any(r["symbol"] == s and r["row_count"] for r in rows)])
        c = o["composition"]
        exact(c, {"status", "verified", "bundle_sha256", "normalized_sha256", "declaration_sha256",
                  "methods_sha256", "entities_sha256", "policy_sha256", "completed_at", "expires_at"})
        _require(c["status"] in {"NOT_RUN", "VERIFIED", "FAILED"} and type(c["verified"]) is bool)
        hashes = ("bundle_sha256", "normalized_sha256", "declaration_sha256", "methods_sha256", "entities_sha256", "policy_sha256")
        if c["status"] == "VERIFIED":
            _require(c["verified"] is True and status in REASONS and t is not None and t["status"] == "COMPLETE")
            for key in hashes:
                fingerprint(c[key])
            required = [r for r in t["responses"] if r["index"] not in (1, 7)]
            _require(o["batch_possession_at"] == required[-1]["completed_at"])
            batch = _time(o["batch_possession_at"])
            done, expiry = _time(c["completed_at"]), _time(c["expires_at"])
            _require(batch <= _time(t["finished_at"]) <= done <= finish and done < expiry)
            _require(expiry == _time(required[0]["first_seen_at"]) + timedelta(seconds=TTL))
            for key in ("accepted_rows", "rejected_rows"):
                integer(inp[key], 0, 28)
            _require(inp["accepted_rows"] + inp["rejected_rows"] == inp["daily_rows_received"])
            _require(type(inp["rejection_counts"]) is dict and not inp["rejection_counts"] and inp["rejected_rows"] == 0)
            count = len(inp["latest_session_symbols"])
            expected = "OBSERVED" if count == 2 else "PARTIAL" if inp["accepted_rows"] else "EMPTY"
            _require(status == expected)
        else:
            _require(not c["verified"] and all(c[k] is None for k in hashes)
                     and c["completed_at"] is None and c["expires_at"] is None
                     and o["batch_possession_at"] is None and status in {"FAILED", "UNKNOWN"})
            _require(inp["accepted_rows"] is None and inp["rejected_rows"] is None
                     and inp["rejection_counts"] is None and not inp["latest_session_symbols"])
            if c["status"] == "FAILED":
                _require(t is not None and t["status"] == "COMPLETE" and reason == "computation_failed")
        if t is not None and t["status"] == "FAILED":
            _require(status == "FAILED" and reason == "transport_failed" and c["status"] == "NOT_RUN")
        cleanup = o["cleanup"]
        exact(cleanup, {"socket_closed", "bindings_restored", "transport_payloads_disposed",
                        "capture_payloads_disposed", "payload_tables_empty", "checked_at"})
        for key in ("socket_closed", "bindings_restored", "transport_payloads_disposed"):
            _require(cleanup[key] is None if t is None else type(cleanup[key]) is bool and cleanup[key] == t[key])
        for key in ("capture_payloads_disposed", "payload_tables_empty"):
            _require(cleanup[key] is None or type(cleanup[key]) is bool)
        if cleanup["checked_at"] is not None:
            _require(finish is not None and start <= _time(cleanup["checked_at"]) <= finish)
        if status in REASONS:
            _require(all(cleanup[k] is True for k in cleanup if k != "checked_at") and cleanup["checked_at"] is not None)
        if finish is None:
            _require(status == "UNKNOWN" and t is None and cleanup["checked_at"] is None)
        exact(o["live_market_coverage"], {"covered", "expected", "missing"})
        integer(o["live_market_coverage"]["covered"], 0, 0)
        integer(o["live_market_coverage"]["expected"], 5, 5)
        _require(o["live_market_coverage"] == _coverage() and type(o["new_live_research_runs"]) is int
                 and o["new_live_research_runs"] == 0 and o["limitations"] == LIMITATIONS)
        _require(len(canonical(value).encode()) <= MAX_RECEIPT)
    except Exception:
        raise Invalid("invalid_observation_receipt") from None
    return value


def _path():
    return external_path(Path.home() / ".a-share-market-intelligence" / "p4b-baostock-test.sqlite")


class _ReadOnlyJournal(preparation._ReadOnlyStore):
    def _authorizer(self, action, table, column, database, trigger):
        if action == sqlite3.SQLITE_READ and table in {"captures", "evidence", "bundles", "tombstones"} and column:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK


class _Journal(Store):
    def __init__(self, path):
        super().__init__(path)
        self.db.set_authorizer(self._authorizer)

    @staticmethod
    def _authorizer(action, table, column, database, trigger):
        if (action in {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE}
                and table in {"captures", "evidence", "bundles", "tombstones"}):
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK


def _empty_payloads(store):
    for table in ("captures", "evidence", "bundles", "tombstones"):
        _require(store.db.execute("SELECT count(*) FROM " + table).fetchone()[0] == 0)


def _bounded_rows(store, table, columns):
    limit = 16 if table == "events" else 1
    _require(store.db.execute("SELECT count(*) FROM " + table).fetchone()[0] <= limit)
    for column, maximum in columns.items():
        _require(not store.db.execute("SELECT 1 FROM " + table + " WHERE length(" + column + ")>? LIMIT 1", (maximum,)).fetchone())
    return [dict(row) for row in store.db.execute("SELECT * FROM " + table + (" ORDER BY event_id" if table == "events" else ""))]


def _inspect(store, snapshot_id=None):
    _empty_payloads(store)
    runs = _bounded_rows(store, "runs", {"run_id": 32, "task_key": 160, "intent": 256, "summary": 0})
    attempts = _bounded_rows(store, "attempts", {"attempt_id": 32, "reason": 100})
    events = _bounded_rows(store, "events", {"kind": 32, "detail": MAX_RECEIPT + 128})
    lease = dict(store.db.execute("SELECT * FROM lease WHERE singleton=1").fetchone())
    _require(store.db.execute("SELECT count(*) FROM lease").fetchone()[0] == 1)
    if not runs:
        _require(not attempts and not events and lease == {"singleton": 1, "run_id": None, "owner": None,
                                                          "generation": 0, "expires_at": None})
        return None, None, None
    run = runs[0]
    _id(run["run_id"])
    _require(run["task_key"] == TASK and run["mode"] == "live_observation" and run["summary"] is None
             and run["status"] in {"running", "interrupted", "complete"} and run["intent"].startswith(INTENT))
    snapshot = run["intent"][len(INTENT):]
    fingerprint(snapshot)
    _require(snapshot_id is None or snapshot_id == snapshot)
    started = _time(run["started_at"])
    if run["finished_at"] is not None:
        _require(started <= _time(run["finished_at"]))
    integer(lease["generation"], 1, 100)
    if lease["owner"] is not None:
        _id(lease["owner"])
        _require(lease["run_id"] == run["run_id"] and lease["expires_at"] is not None)
        _time(lease["expires_at"])
    else:
        _require(lease["run_id"] is None and lease["expires_at"] is None)
    attempt = attempts[0] if attempts else None
    if attempt:
        _id(attempt["attempt_id"])
        _require(attempt["run_id"] == run["run_id"] and attempt["reserved_bytes"] == 512000
                 and attempt["outcome"] in {"reserved", "success", "failed", "unknown"})
        reserved = _time(attempt["reserved_at"])
        _require(started <= reserved and attempt["day"] == stamp(reserved)[:10])
        _require(attempt["reason"] in {None, "response_received", "transport_failed", "transport_unknown",
                 "interrupted", "released_pending", "lease_expired"})
        if attempt["observed_bytes"] is not None:
            integer(attempt["observed_bytes"], 0, 512000)
        if attempt["outcome"] in {"reserved", "unknown"}:
            _require(attempt["observed_bytes"] is None)
    result, last, kinds = None, started, []
    for event in events:
        _require(event["run_id"] == run["run_id"] and last <= _time(event["at"]))
        last = _time(event["at"])
        detail = json.loads(event["detail"])
        kind = event["kind"]
        kinds.append(kind)
        if kind in {"started", "released"}:
            exact(detail, {"generation"})
            integer(detail["generation"], 1, lease["generation"])
        elif kind == "p4b_intent":
            _require(detail == {"snapshot_id": snapshot, "intent": run["intent"]})
        elif kind == "reserved":
            _require(attempt is not None and detail == {"attempt_id": attempt["attempt_id"], "bytes": 512000})
            _require(event["at"] == attempt["reserved_at"])
        elif kind == "settled":
            _require(attempt is not None and detail == {"attempt_id": attempt["attempt_id"], "outcome": attempt["outcome"]})
        elif kind == "p4b_result":
            exact(detail, {"receipt", "receipt_hash"})
            result = validate_observation_receipt(detail["receipt"], snapshot)
            _require(attempt is not None and digest(result) == detail["receipt_hash"])
            o = result["observation"]
            _require(o is not None and o["run_id"] == run["run_id"] and o["attempt_id"] == attempt["attempt_id"]
                     and o["started_at"] == run["started_at"] and o["finished_at"] == run["finished_at"] == event["at"])
            t = o["transport"]
            expected = "unknown" if t is None else "success" if t["status"] == "COMPLETE" else "failed"
            _require(attempt["outcome"] == expected and attempt["observed_bytes"] == (t["wire_bytes"] if t else None))
        else:
            raise Invalid("invalid_journal_event")
    _require(kinds[:1] == ["started"] and len(kinds) == len(set(kinds)))
    if "p4b_intent" in kinds:
        _require(kinds[1] == "p4b_intent")
    if attempt:
        _require(kinds[:3] == ["started", "p4b_intent", "reserved"])
    else:
        _require("reserved" not in kinds and "settled" not in kinds and result is None)
    if result is not None:
        _require(kinds == ["started", "p4b_intent", "reserved", "settled", "p4b_result"]
                 and run["status"] == "complete" and lease["owner"] is None)
    else:
        _require(run["status"] != "complete" and run["finished_at"] is None)
    return run, attempt, result


def assert_journal_unconsumed(snapshot_id):
    """Read-only availability check, never authority to dispatch or reset quota."""
    try:
        fingerprint(snapshot_id)
        path = _path()
        for suffix in ("-journal", "-wal", "-shm"):
            _require(not Path(str(path) + suffix).exists())
        if not path.exists():
            return None
        with _ReadOnlyJournal(path) as store:
            run, attempt, _ = _inspect(store, snapshot_id)
            _require(run is None and attempt is None)
        store.check_identity()
    except Exception:
        raise Invalid("journal_unavailable") from None
    return None


def _snapshot():
    from tools import governance
    return governance.snapshot({p: governance.read_text(ROOT, p) for p in governance.PROTECTED})[0]


def _authorize():
    try:
        from tools.governance import market_gate
        result = market_gate(ROOT)
        _require(result["package_id"] == "P4B" and result["verdict"] == "LOCAL_READY_FOR_BAOSTOCK_PROBE")
        fingerprint(result["snapshot_id"])
        return result["snapshot_id"]
    except Exception:
        raise Invalid("market_gate_blocked") from None


class _Memory:
    def __init__(self):
        self.calendar = {}
        self.identities = set()
        self.bars = {}
        self.window = None
        self.last_time = None
        self.index = 0
        self.disposed = False

    def accept(self, view):
        try:
            from .baostock_transport import ResponseView
            _require(type(view) is ResponseView and not self.disposed)
            integer(view.index, self.index + 1, self.index + 1)
            method, symbol, response_type = STEPS[view.index - 1]
            _require((view.method, view.symbol, view.response_type) == (method, symbol, response_type))
            _require(view.user_id == "anonymous" and view.client_version == "00.9.40")
            first, completed = _time(view.first_seen_at), _time(view.completed_at)
            _require(first <= completed and (self.last_time is None or self.last_time <= first))
            _require(type(view.fields) is tuple and type(view.data) is tuple and len(view.data) <= 14)
            for row in view.data:
                _require(type(row) is tuple and len(row) == len(view.fields))
                _require(all(type(x) is str and len(x) <= 128 and not any(ord(c) < 32 for c in x) for x in row))
            if view.index in (1, 7):
                _require(not view.fields and not view.data and view.page_number is None and view.page_size is None)
            else:
                integer(view.page_number, 1, 1)
                integer(view.page_size, 2000, 2000)
            if method == "query_trade_dates":
                _require(view.fields == ("calendar_date", "is_trading_day") and len(view.data) == 14)
                start, end = _day(view.start_date), _day(view.end_date)
                _require(end - start == timedelta(days=13) and end < first.astimezone(SHANGHAI).date())
                calendar = {}
                for day, flag in view.data:
                    _require(day not in calendar and start <= _day(day) <= end and flag in {"0", "1"})
                    calendar[day] = flag
                _require(len(calendar) == 14)
                self.calendar = calendar
                self.window = (view.start_date, view.end_date)
            elif method == "query_stock_basic":
                _require(view.fields == ("code", "code_name", "ipoDate", "outDate", "type", "status"))
                _require(len(view.data) == 1 and view.data[0][0] == symbol and view.data[0][4:] == ("2", "1"))
                _require(symbol not in self.identities)
                self.identities.add(symbol)
            elif method == "query_history_k_data_plus":
                _require(view.fields == daily.HEADER and symbol in self.identities
                         and (view.start_date, view.end_date) == self.window
                         and view.frequency == "d" and view.adjustflag == "3" and symbol not in self.bars)
                seen = set()
                for row in view.data:
                    _require(row[1] == symbol and row[0] not in seen and self.calendar.get(row[0]) == "1")
                    _require(instant(row[0] + "T15:00:00+08:00") < first)
                    daily._values(row)
                    seen.add(row[0])
                self.bars[symbol] = view.data
            if method != "query_history_k_data_plus":
                _require(view.frequency is None and view.adjustflag is None)
            if method not in {"query_trade_dates", "query_history_k_data_plus"}:
                _require(view.start_date is None and view.end_date is None)
            self.index = view.index
            self.last_time = completed
        except Exception:
            raise Invalid("response_rejected") from None
        return None

    def metadata(self, transport):
        dates = [row[0] for rows in self.bars.values() for row in rows]
        valid = [s for s in SYMBOLS if self.bars.get(s)]
        responses = transport["responses"] if transport else []
        received = [r for r in responses if r["method"] == "query_history_k_data_plus"]
        return {"observed_symbols": [s for s in SYMBOLS if any(r["symbol"] == s and r["row_count"] for r in received)],
            "validated_symbols": valid if transport else [],
            "session_start": min(dates) if dates and transport else None,
            "session_end": max(dates) if dates and transport else None,
            "latest_expected_session": max((d for d, flag in self.calendar.items() if flag == "1"), default=None) if transport else None,
            "latest_session_symbols": [], "daily_rows_received": sum(r["row_count"] for r in received) if transport else None,
            "accepted_rows": None, "rejected_rows": None, "rejection_counts": None,
            "observed_input_coverage": {"covered": len(valid) if transport else 0, "expected": 5}}

    def dispose(self):
        self.calendar.clear()
        self.identities.clear()
        self.bars.clear()
        self.window = None
        self.last_time = None
        self.disposed = not self.calendar and not self.identities and not self.bars and self.window is None


def _policy():
    return {"schema_version": 1, "version": "p4b-ephemeral-rights-v1", "sources": [{
        "source_id": SOURCE, "synthetic": False, "purpose": PURPOSE, "origin": "https://www.baostock.com",
        "locator_prefix": daily.ENGINE_PREFIX,
        "retention": {"class": "normalized_only", "max_age_seconds": TTL, "basis_refs": [daily.DOCUMENT]},
        "rights": {name: {"state": "permitted" if name in {"normalized_retention", "derived_analysis"} else "denied",
                          "basis_refs": [daily.DOCUMENT]} for name in RIGHTS}}]}


def _methods():
    return {"schema_version": 2, "version": "p4b-daily-method-v1", "profile_version": "p4b-required-gaps-v1",
        "universe_id": "p4b-fixed-five-benchmarks", "benchmark_symbols": list(UNIVERSE), "constituents": [],
        "lookback_sessions": 1, "news_half_life_seconds": 86400, "news_ttl_seconds": 86400,
        "approach_seconds": 86400, "confirmation_grace_seconds": 21600, "event_ttl_seconds": 604800,
        "flow": {"provider": "unconfigured", "series_kind": "provider_estimate", "enabled": False},
        "sources": [{"source_id": SOURCE, "tier": "B", "locator_prefix": daily.ENGINE_PREFIX,
                     "scopes": ["market"], "event_ids": [], "synthetic": False}],
        "rules": [{"domain": d, "validator": d + "-p2-v1", "required": d != "flow", "allow_degraded": d == "flow"}
                  for d in ("market", "sector", "news", "entity", "event", "flow")]}


def _entities():
    return {"schema_version": 2, "version": "p4b-index-mapping-v1", "mapping_refs": [daily.ENGINE_LOCATOR],
        "entities": [{"entity_id": symbol, "provider_symbol": code, "aliases": [symbol], "sector_id": "benchmark",
            "calendar_id": "SSE" if code.startswith("sh.") else "SZSE", "currency": "CNY", "unit": "index_points",
            "provider": SOURCE, "publication_delay_seconds": None} for code, symbol in daily.SYMBOLS.items()]}


def _not_computed(status="NOT_RUN"):
    return {"status": status, "verified": False, "bundle_sha256": None, "normalized_sha256": None,
        "declaration_sha256": None, "methods_sha256": None, "entities_sha256": None, "policy_sha256": None,
        "completed_at": None, "expires_at": None}


def _compose(memory, transport, run_id, attempt_id):
    body = bundle = content = declaration = methods = entities = policy = buffer = None
    try:
        _require(transport["status"] == "COMPLETE" and memory.index == 7)
        required = [r for r in transport["responses"] if r["index"] not in (1, 7)]
        batch = required[-1]["completed_at"]
        expiry = stamp(_time(required[0]["first_seen_at"]) + timedelta(seconds=TTL))
        _require(_time(transport["finished_at"]) <= utc_now() < _time(expiry))
        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(daily.HEADER)
        for symbol in SYMBOLS:
            writer.writerows(memory.bars[symbol])
        body = buffer.getvalue().encode("utf-8")
        buffer.close()
        buffer = writer = None
        _require(len(body) <= daily.MAX_BYTES)
        custody = {"source_id": SOURCE, "run_id": run_id, "request_id": attempt_id,
            "first_seen_at": batch, "retrieved_at": batch, "observed_at": batch, "published_at": None, "expires_at": expiry}
        declaration = {"schema_version": 1, "version": daily.INPUT_VERSION, "parser_version": daily.PARSER_VERSION,
            "source_mode": "live_observation", "source_as_of": batch,
            "calendar": {"version": "p4b-provider-calendar-ordinary-close-v1", "sessions": [
                {"calendar_id": cal, "timezone": "Asia/Shanghai", "session_id": day,
                 "close_at": day + "T15:00:00+08:00"}
                for day, flag in sorted(memory.calendar.items()) if flag == "1" for cal in ("SSE", "SZSE")]},
            "custody": custody, "hashes": {"normalized_csv_sha256": digest(body), "original_response_sha256": transport["wire_sha256"]},
            "transport": {k: custody[k] for k in ("source_id", "run_id", "request_id")} |
                         {"locator": daily.TRANSPORT, "protocol": "tcp", "tls": False, "peer_authenticated": False},
            "documentation_reference": {"source_id": SOURCE, "locator": daily.DOCUMENT, "locator_role": daily.DOC_ROLE},
            "engine_locator": {"source_id": SOURCE, "locator": daily.ENGINE_LOCATOR,
                               "locator_prefix": daily.ENGINE_PREFIX, "locator_role": daily.ENGINE_ROLE}}
        methods, entities, policy = _methods(), _entities(), _policy()
        bundle = daily.build_daily_replay_bundle(body, declaration, methods, entities, policy)
        _require(daily.verify_daily_replay_bundle(bundle, body, declaration, methods, entities, policy) is True)
        content = bundle["content"]
        _require(content["live_market_coverage"] == _coverage() and content["new_live_runs"] == 0
                 and not content["acquisition_verified"] and not content["finality_verified"]
                 and not any(content["operations_enabled"].values()))
        parsed = content["parse"]
        _require(parsed["rejected"] == 0 and not parsed["reasons"])
        available = {r["symbol"] for r in content["results"]["market"]["benchmarks"] if r["status"] == "available"}
        latest = [s for s in SYMBOLS if daily.SYMBOLS[s] in available]
        completed = utc_now()
        _require(_time(content["processing"]["completed_at"]) <= completed < _time(expiry))
        computation = {"status": "VERIFIED", "verified": True, "bundle_sha256": bundle["bundle_id"],
            "normalized_sha256": digest(body), "declaration_sha256": digest(declaration), "methods_sha256": digest(methods),
            "entities_sha256": digest(entities), "policy_sha256": digest(policy), "completed_at": stamp(completed), "expires_at": expiry}
        return computation, batch, {"accepted_rows": parsed["accepted"], "rejected_rows": parsed["rejected"],
                                   "rejection_counts": dict(parsed["reasons"]), "latest_session_symbols": latest}
    finally:
        if buffer is not None:
            buffer.close()
        body = bundle = content = declaration = methods = entities = policy = buffer = None


def _make_receipt(snapshot, run, attempt, transport, inp, computation, batch, disposed, checked_at, status, reason):
    cleanup = {k: transport[k] if transport else None for k in
               ("socket_closed", "bindings_restored", "transport_payloads_disposed")}
    cleanup.update(capture_payloads_disposed=disposed, payload_tables_empty=True if checked_at else None, checked_at=checked_at)
    return validate_observation_receipt({"schema_version": 1, "package_id": "P4B", "snapshot_id": snapshot,
        "status": status, "reason": reason, "observation": {
            "source_id": SOURCE, "implementation_version": VERSION, "sdk_version": "0.9.4", "sdk_wheel_sha256": SDK_HASH,
            "run_id": run["run_id"], "attempt_id": attempt, "started_at": run["started_at"], "finished_at": checked_at,
            "transport": transport, "batch_possession_at": batch, "input": inp, "composition": computation, "cleanup": cleanup,
            "live_market_coverage": _coverage(), "new_live_research_runs": 0, "limitations": list(LIMITATIONS)}}, snapshot)


class _Discard:
    def write(self, value):
        return len(value)

    def flush(self):
        pass


def _dispatch(memory):
    from .baostock_transport import TransportReceipt, run_once
    with redirect_stdout(_Discard()), redirect_stderr(_Discard()):
        receipt = run_once(accept_response=memory.accept)
    _require(type(receipt) is TransportReceipt)
    return _transport_record(receipt.to_dict())


def _finish(store, lease, receipt):
    validate_observation_receipt(receipt, receipt["snapshot_id"])
    o = receipt["observation"]
    now = _time(o["finished_at"])
    with store.transaction():
        store._owned(lease, now)
        _empty_payloads(store)
        _require(not store.db.execute("SELECT 1 FROM attempts WHERE outcome='reserved'").fetchone())
        _require(not store.db.execute("SELECT 1 FROM events WHERE kind='p4b_result'").fetchone())
        store._capacity(MAX_RECEIPT + 16384)
        store._event(lease.run_id, now, "p4b_result", {"receipt": receipt, "receipt_hash": digest(receipt)})
        changed = store.db.execute("UPDATE runs SET status='complete',finished_at=?,summary=NULL WHERE run_id=? AND status='running'",
                                   (stamp(now), lease.run_id)).rowcount
        _require(changed == 1)
        store.db.execute("UPDATE lease SET owner=NULL,expires_at=NULL,run_id=NULL WHERE singleton=1 AND owner=? AND generation=?",
                         (lease.owner, lease.generation))


def _collect(store, snapshot):
    lease, run = store.begin(TASK, "live_observation", intent=INTENT + snapshot)
    _require(lease is not None)
    memory, transport = _Memory(), None
    computation, batch = _not_computed(), None
    status, reason, attempt = "UNKNOWN", "transport_unknown", None
    try:
        with store.transaction():
            store._owned(lease, utc_now())
            _empty_payloads(store)
            _require(store.db.execute("SELECT count(*) FROM attempts").fetchone()[0] == 0)
            _require(store.db.execute("SELECT count(*) FROM events").fetchone()[0] == 1)
            store._event(lease.run_id, utc_now(), "p4b_intent", {"snapshot_id": snapshot, "intent": INTENT + snapshot})
        _require(_snapshot() == snapshot)
        store.renew(lease)
        # reserve commits before importing SDK or entering any DNS/socket path.
        attempt = store.reserve(lease)
        try:
            transport = _dispatch(memory)
            if transport["status"] == "COMPLETE":
                store.renew(lease)
                try:
                    with redirect_stdout(_Discard()), redirect_stderr(_Discard()):
                        computation, batch, parsed = _compose(memory, transport, run["run_id"], attempt)
                    status = "OBSERVED" if len(parsed["latest_session_symbols"]) == 2 else "PARTIAL" if parsed["accepted_rows"] else "EMPTY"
                    reason = REASONS[status]
                except (Exception, KeyboardInterrupt):
                    computation, batch = _not_computed("FAILED"), None
                    status, reason = "FAILED", "computation_failed"
            else:
                status, reason = "FAILED", "transport_failed"
        except Busy:
            raise
        except KeyboardInterrupt:
            status, reason, transport = "UNKNOWN", "interrupted", None
        except Exception:
            status, reason, transport = "UNKNOWN", "transport_unknown", None
        finally:
            inp = memory.metadata(transport)
            if computation["status"] == "VERIFIED":
                inp.update(parsed)
            memory.dispose()
        if computation["status"] == "VERIFIED" and not _time(computation["completed_at"]) <= utc_now() < _time(computation["expires_at"]):
            computation, batch = _not_computed("FAILED"), None
            inp.update(accepted_rows=None, rejected_rows=None, rejection_counts=None, latest_session_symbols=[])
            status, reason = "FAILED", "computation_failed"
        outcome = "unknown" if transport is None else "success" if transport["status"] == "COMPLETE" else "failed"
        store.settle(lease, attempt, outcome, transport["wire_bytes"] if transport else None,
                     "response_received" if outcome == "success" else "transport_failed" if outcome == "failed" else reason)
        store.renew(lease)
        _empty_payloads(store)
        checked = stamp(utc_now())
        receipt = _make_receipt(snapshot, run, attempt, transport, inp, computation, batch, memory.disposed, checked, status, reason)
        _finish(store, lease, receipt)
    finally:
        memory.dispose()
        try:
            if store.run(run["run_id"])["status"] != "complete":
                store.release(lease)
        except (Invalid, sqlite3.Error):
            pass


def _read():
    path = _path()
    for suffix in ("-journal", "-wal", "-shm"):
        _require(not Path(str(path) + suffix).exists())
    if not path.exists():
        return not_run_receipt(_snapshot())
    with _ReadOnlyJournal(path) as store:
        run, attempt, receipt = _inspect(store)
        if receipt is None:
            _require(run is not None and attempt is not None)
            memory = _Memory()
            receipt = _make_receipt(run["intent"][len(INTENT):], run, attempt["attempt_id"], None,
                memory.metadata(None), _not_computed(), None, None, None, "UNKNOWN", "prior_attempt_incomplete")
    store.check_identity()
    return receipt


def status():
    """Read sanitized journal metadata without authorization, writes or SDK load."""
    try:
        return _read()
    except Exception:
        raise Invalid("journal_unavailable") from None


def observe(allow_network=False):
    if allow_network is not True:
        raise Invalid("explicit_allow_network_required")
    snapshot = _authorize()
    assert_journal_unconsumed(snapshot)
    with _Journal(_path()) as store:
        _collect(store, snapshot)
    return status()


def main(argv=None):
    try:
        args = list(sys.argv[1:] if argv is None else argv)
        if args == ["observe", "--allow-network"]:
            result = observe(True)
        elif args == ["observe"]:
            result = observe()
        elif args == ["status"]:
            result = status()
        else:
            raise Invalid("invalid_cli_arguments")
        validate_observation_receipt(result, result["snapshot_id"])
        print(canonical(result))
        return 0 if result["status"] in REASONS else 2
    except (Exception, KeyboardInterrupt) as exc:
        allowed = {"market_gate_blocked", "explicit_allow_network_required", "invalid_cli_arguments", "journal_unavailable"}
        reason = str(exc) if type(exc) is Invalid and str(exc) in allowed else "local_operation_failed"
        print(canonical({"status": "ERROR", "reason": reason}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
