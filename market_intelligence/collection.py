"""P4S's fixed, independently released, one-attempt Fed observation.

Only this module's CLI is a production entrypoint. Test doubles replace module
boundaries; there are deliberately no store, transport or authority arguments.
"""

import argparse
from datetime import timedelta
from http import HTTPStatus
import json
from pathlib import Path
import re

from . import runtime, sources
from .gate import Calendar
from .models import (Capture, Evidence, Invalid, UNIVERSE, canonical, digest,
                     exact, fingerprint, instant, integer, stamp, utc_now,
                     validate_summary)
from .store import Busy, CHECKOUT, Store, external_path

ROOT = CHECKOUT


class _MetadataStore(Store):
    """Keep inherited summary and cleanup lookups from loading retained bodies."""

    def capture(self, capture_id):
        fingerprint(capture_id)
        row = self.db.execute(
            "SELECT capture_id,source_id,content_hash,metadata FROM captures WHERE capture_id=?",
            (capture_id,)).fetchone()
        if row is None:
            raise Invalid("unknown capture")
        capture = Capture.from_dict(json.loads(row["metadata"]))
        if (capture.version_id != capture_id or capture.source_id != row["source_id"]
                or capture.content_hash != row["content_hash"]):
            raise Invalid("stored capture hash mismatch")
        return capture

    def records(self, ids=None, now=None):
        now = now or utc_now()
        if ids is None:
            ids = [row[0] for row in self.db.execute("SELECT evidence_id FROM evidence ORDER BY evidence_id")]
        available, unavailable = [], []
        for identity in ids:
            fingerprint(identity)
            row = self.db.execute("SELECT capture_id FROM evidence WHERE evidence_id=?", (identity,)).fetchone()
            if row is None:
                raise Invalid("unknown evidence reference")
            capture = self.capture(row[0])
            if (capture.retention == "restricted"
                    or (capture.expires_at and instant(capture.expires_at) <= now)
                    or self.db.execute("SELECT 1 FROM tombstones WHERE capture_id=?", (capture.version_id,)).fetchone()):
                unavailable.append({"evidence_id": identity, "reason": "expired_or_restricted"})
            else:
                records, missing = super().records([identity], now)
                available.extend(records)
                unavailable.extend(missing)
        return available, unavailable


TASK = "p4s-fed-press-one-shot"
MODE = "live_observation"
INTENT = "p4s-fed-press-one-shot:"
PRE_DISPATCH_ERRORS = frozenset({
    "explicit_allow_network_required", "probe_gate_blocked", "snapshot_changed",
    "source_not_authorized", "prior_run_without_attempt", "reservation_failed",
})
FAILED_TRANSPORT = frozenset({
    "response_size", "content_type", "empty_dns", "private_dns",
    "content_encoding", "content_length", "transfer_encoding",
    *("http_status_" + str(int(code)) for code in HTTPStatus if code != 200),
})
UNKNOWN_TRANSPORT = frozenset({
    "dns_timeout", "truncated_response", "transport_unknown",
    "invalid_transport_response",
})
OBSERVATION_ERRORS = FAILED_TRANSPORT | UNKNOWN_TRANSPORT | frozenset({
    "parse_failed", "empty_or_all_rejected_feed", "partial_parse",
    "news_gate_blocked", "prior_attempt_incomplete",
})
LOCAL_ERRORS = PRE_DISPATCH_ERRORS | frozenset({
    "invalid_cli_arguments", "invalid_probe_record", "invalid_local_state",
    "local_operation_failed", "collection_interrupted", "probe_in_progress",
})
NEWS_REASONS = frozenset({"insufficient_coverage", "stale evidence", "conflicting evidence"})


def _require(condition):
    if not condition:
        raise Invalid("invalid_probe_record")


def _time(value):
    parsed = instant(value)
    _require(stamp(parsed) == value and parsed <= utc_now())
    return parsed


def _identifier(value):
    _require(type(value) is str and re.fullmatch(r"[0-9a-f]{32}", value) is not None)


def _validate_observation(value):
    validate_summary(value)
    _identifier(value["run_id"])
    _require(value["mode"] == MODE and value["source_id"] == "fed_press")
    started, finished = _time(value["started_at"]), _time(value["finished_at"])
    requests, quality, parsed = value["requests"], value["quality"], value["parse"]
    for key in ("attempted", "success", "failed", "unknown"):
        integer(requests[key], 0, 1)
    integer(requests["attempted"], 1, 1)
    integer(requests["reserved_bytes"], sources.MAX_BYTES, sources.MAX_BYTES)
    if requests["unknown"]:
        _require(requests["observed_bytes"] is None)
    elif requests["failed"]:
        integer(requests["observed_bytes"], 0, 0)
    else:
        integer(requests["observed_bytes"], 0, sources.MAX_BYTES)
    for key in ("request_sample_size", "parse_sample_size", "parse_success_count", "nonempty_parse_count"):
        integer(quality[key], 0, 1)
    for key in ("item_denominator", "accepted_records"):
        integer(quality[key], 0, 1000)
    for key in ("total", "accepted", "rejected"):
        integer(parsed[key], 0, 1000)
    _require(set(parsed["reasons"]) <= {"invalid_or_duplicate_item"})
    _require(len(value["capture_ids"]) <= 1)
    count = len(value["capture_ids"])
    _require(quality["parse_sample_size"] == count and count <= requests["success"])
    _require(quality["nonempty_parse_count"] == int(parsed["accepted"] > 0))
    if not quality["parse_success_count"]:
        _require(parsed == {"total": 0, "accepted": 0, "rejected": 0, "reasons": {}})
    for row in value["observations"]:
        _require(row["source_id"] == "fed_press" and row["locator"] == sources.FED_URL
                 and row["parser_version"] == "rss-v1" and row["retention"] == "short_term_raw"
                 and row["source_timestamp"] is None)
        retrieved, seen = _time(row["retrieved_at"]), _time(row["first_seen_at"])
        _require(started <= retrieved <= finished and seen == retrieved)
        expiry = instant(row["expires_at"])
        _require(stamp(expiry) == row["expires_at"] and expiry - retrieved == timedelta(days=1))
    coverage = value["live_market_coverage"]
    exact(coverage, {"covered", "expected", "missing"})
    integer(coverage["covered"], 0, 0)
    integer(coverage["expected"], 5, 5)
    gate = value["gate"]
    _require(gate["status"] == "BLOCK" and gate["confidence_ceiling"] == "none")
    market, news = gate["domains"]["market"], gate["domains"]["news"]
    _require(market == {"status": "BLOCK", "covered": 0, "expected": 5,
                        "missing": list(UNIVERSE), "reasons": ["insufficient_coverage"],
                        "confidence_ceiling": "unavailable"})
    _require(news["reasons"] == sorted(set(news["reasons"]))
             and set(news["reasons"]) <= NEWS_REASONS)
    _require(news["status"] == ("BLOCK" if news["reasons"] else "PASS"))
    _require(("insufficient_coverage" in news["reasons"]) == (news["covered"] == 0))
    _require(not news["covered"] or parsed["accepted"] > 0)
    errors = value["error_codes"]
    _require(all(type(code) is str and code in OBSERVATION_ERRORS for code in errors))
    _require(len(errors) == len(set(errors)))
    incomplete = errors == ["prior_attempt_incomplete"]
    if incomplete:
        _require(value["outcome"] == "unknown" and value["source_state"] == "degraded"
                 and quality["parse_success_count"] == 0 and news["covered"] == 0)
    elif requests["success"]:
        _require(count == 1)
        expected_errors = []
        if not quality["parse_success_count"]:
            expected_errors.append("parse_failed")
        elif not parsed["accepted"]:
            expected_errors.append("empty_or_all_rejected_feed")
        else:
            if parsed["rejected"]:
                expected_errors.append("partial_parse")
            if news["status"] != "PASS":
                expected_errors.append("news_gate_blocked")
        _require(errors == expected_errors)
        _require(value["outcome"] == ("observed" if parsed["accepted"] else "failed"))
        _require(value["source_state"] == ("degraded" if errors else "live_verified"))
    else:
        _require(not count and not quality["parse_success_count"] and news["covered"] == 0)
        allowed = FAILED_TRANSPORT if requests["failed"] else UNKNOWN_TRANSPORT
        _require(len(errors) == 1 and errors[0] in allowed)
        _require(value["outcome"] == ("failed" if requests["failed"] else "unknown")
                 and value["source_state"] == "degraded")
    return value


def validate_probe_record(value, snapshot_id):
    """Validate the exact public receipt; never accept arbitrary diagnostic text."""
    try:
        fingerprint(snapshot_id)
        exact(value, {"schema_version", "package_id", "snapshot_id", "status",
                      "observed_at", "observation", "reason"})
        integer(value["schema_version"], 1, 1)
        _require(value["package_id"] == "P4S" and value["snapshot_id"] == snapshot_id)
        if value["status"] == "NOT_RUN":
            _require(value["observed_at"] is None and value["observation"] is None
                     and value["reason"] == "awaiting_release")
        elif value["status"] == "BLOCKED_NO_REQUEST":
            _time(value["observed_at"])
            _require(value["observation"] is None and type(value["reason"]) is str
                     and value["reason"] in PRE_DISPATCH_ERRORS)
        elif value["status"] == "RECORDED":
            _time(value["observed_at"])
            _validate_observation(value["observation"])
            _require(value["observed_at"] == value["observation"]["finished_at"]
                     and value["reason"] == "source_observation")
        else:
            raise Invalid("invalid_probe_record")
        canonical(value)
    except Exception:
        raise Invalid("invalid_probe_record") from None
    return value


def _record(snapshot, summary=None, reason=None):
    status = "RECORDED" if summary is not None else ("BLOCKED_NO_REQUEST" if reason else "NOT_RUN")
    value = {"schema_version": 1, "package_id": "P4S", "snapshot_id": snapshot,
             "status": status, "observed_at": summary["finished_at"] if summary else (
                 stamp(utc_now()) if reason else None), "observation": summary,
             "reason": "source_observation" if summary else (reason or "awaiting_release")}
    return validate_probe_record(value, snapshot)


def _authorize():
    # Governance imports our receipt validator lazily as well.
    try:
        from tools.governance import probe_gate
        snapshot = probe_gate(ROOT)
        fingerprint(snapshot)
        return snapshot
    except Exception:
        raise Invalid("probe_gate_blocked") from None


def _path():
    return external_path(Path.home() / ".a-share-market-intelligence" / "p4s-fed-probe.sqlite")


def _bind(store, lease, snapshot):
    with store.transaction():
        store._owned(lease, utc_now())
        events = store.db.execute("SELECT kind FROM events WHERE run_id=?", (lease.run_id,)).fetchall()
        _require([row[0] for row in events] == ["started"])
        store._event(lease.run_id, utc_now(), "p4s_intent", {"snapshot_id": snapshot, "intent": INTENT + snapshot})


def _inspect(store):
    """Reconcile durable relationships before reads, recovery or any reservation."""
    try:
        runs = store.db.execute("SELECT run_id FROM runs").fetchall()
        _require(len(runs) <= 1)
        if not runs:
            for table in ("attempts", "events", "captures", "evidence", "tombstones", "bundles"):
                _require(store.db.execute("SELECT count(*) FROM " + table).fetchone()[0] == 0)
            lease = store.db.execute("SELECT * FROM lease").fetchall()
            _require(len(lease) == 1 and lease[0]["owner"] is None and lease[0]["generation"] == 0)
            return None
        run = store.run(runs[0][0])
        _identifier(run["run_id"])
        _require(run["task_key"] == TASK and run["mode"] == MODE
                 and type(run["intent"]) is str and run["intent"].startswith(INTENT))
        snapshot = run["intent"][len(INTENT):]
        fingerprint(snapshot)
        started = _time(run["started_at"])
        attempts = run["attempts"]
        _require(len(attempts) <= 1 and store.db.execute("SELECT count(*) FROM attempts").fetchone()[0] == len(attempts))
        events = [dict(row) for row in store.db.execute("SELECT * FROM events ORDER BY event_id")]
        _require(all(row["run_id"] == run["run_id"] for row in events))
        bindings = [row for row in events if row["kind"] == "p4s_intent"]
        _require(len(bindings) == 1 and json.loads(bindings[0]["detail"]) == {
            "snapshot_id": snapshot, "intent": run["intent"]})
        _require(all(row["kind"] in {"started", "p4s_intent", "reserved", "settled", "completed",
                                    "resumed", "released", "interrupted"} for row in events))
        reserved = [row for row in events if row["kind"] == "reserved"]
        settled = [row for row in events if row["kind"] == "settled"]
        _require(len(reserved) == len(attempts) and len(settled) <= len(attempts))
        for attempt in attempts:
            _identifier(attempt["attempt_id"])
            _require(started <= _time(attempt["reserved_at"]) and attempt["day"] == attempt["reserved_at"][:10])
            _require(json.loads(reserved[0]["detail"]) == {
                "attempt_id": attempt["attempt_id"], "bytes": sources.MAX_BYTES})
            _require(reserved[0]["at"] == attempt["reserved_at"])
            if attempt["outcome"] in {"reserved", "unknown"}:
                _require(attempt["observed_bytes"] is None)
            elif attempt["outcome"] == "failed":
                integer(attempt["observed_bytes"], 0, 0)
            else:
                integer(attempt["observed_bytes"], 0, sources.MAX_BYTES)
            if settled:
                _require(json.loads(settled[0]["detail"]) == {
                    "attempt_id": attempt["attempt_id"], "outcome": attempt["outcome"]})
            else:
                _require(attempt["outcome"] in {"reserved", "unknown"})
            if attempt["outcome"] == "success":
                _require(attempt["reason"] == "response_received")
            elif attempt["outcome"] == "failed":
                _require(attempt["reason"] in FAILED_TRANSPORT)
            elif attempt["outcome"] == "unknown":
                _require(attempt["reason"] in UNKNOWN_TRANSPORT | {
                    "released_pending", "lease_expired", "collection_interrupted"})
            else:
                _require(attempt["reason"] is None)
        captures = [store.capture(row[0]) for row in store.db.execute("SELECT capture_id FROM captures")]
        _require(len(captures) <= 1 and (not captures or attempts[0]["outcome"] == "success"))
        for capture in captures:
            _require(capture.source_id == "fed_press" and capture.locator == sources.FED_URL
                     and capture.mode == MODE and capture.parser_version == "rss-v1"
                     and capture.retention == "short_term_raw" and capture.source_timestamp is None
                     and capture.request_id == attempts[0]["attempt_id"])
            _require(started <= _time(capture.retrieved_at)
                     and capture.first_seen_at == capture.retrieved_at
                     and instant(capture.expires_at) - instant(capture.retrieved_at) == timedelta(days=1))
            tombstone = store.db.execute("SELECT * FROM tombstones WHERE capture_id=?", (capture.version_id,)).fetchone()
            if tombstone:
                absent = store.db.execute("SELECT payload IS NULL FROM captures WHERE capture_id=?", (capture.version_id,)).fetchone()[0]
                _require(absent == 1 and tombstone["reason"] == "expired"
                         and tombstone["content_hash"] == capture.content_hash
                         and tombstone["parser_version"] == "rss-v1"
                         and _time(tombstone["at"]) >= instant(capture.expires_at))
                _require(store.db.execute("SELECT count(*) FROM evidence").fetchone()[0] == 0)
            elif instant(capture.expires_at) > utc_now():
                payload = store.db.execute("SELECT payload FROM captures WHERE capture_id=?", (capture.version_id,)).fetchone()[0]
                _require(type(payload) is bytes and len(payload) == attempts[0]["observed_bytes"]
                         and digest(payload) == capture.content_hash)
            _require(capture.response_hash == digest({"status": 200, "content_type": capture.content_type,
                                                      "content_hash": capture.content_hash}))
        _require(store.db.execute("PRAGMA foreign_key_check").fetchone() is None)
        for row in store.db.execute("SELECT evidence_id,capture_id FROM evidence"):
            fingerprint(row["evidence_id"])
            _require(len(captures) == 1 and row["capture_id"] == captures[0].version_id)
            if instant(captures[0].expires_at) <= utc_now():
                continue
            value = store.db.execute("SELECT record FROM evidence WHERE evidence_id=?", (row["evidence_id"],)).fetchone()[0]
            record = Evidence.from_dict(json.loads(value))
            _require(len(captures) == 1 and row["capture_id"] == captures[0].version_id
                     and row["evidence_id"] == record.evidence_id and record.domain == "news")
            store._binding(record, captures[0])
        if run["status"] == "complete":
            summary = run["summary"]
            _record(snapshot, summary)
            _require(summary["run_id"] == run["run_id"] and summary["started_at"] == run["started_at"]
                     and _time(run["finished_at"]) >= _time(summary["finished_at"]))
            _require(len(attempts) == 1 and attempts[0]["outcome"] != "reserved")
            expected = {"attempted": 1, "success": int(attempts[0]["outcome"] == "success"),
                        "failed": int(attempts[0]["outcome"] == "failed"),
                        "unknown": int(attempts[0]["outcome"] == "unknown"),
                        "reserved_bytes": sources.MAX_BYTES, "observed_bytes": attempts[0]["observed_bytes"]}
            _require(summary["requests"] == expected
                     and summary["capture_ids"] == [c.version_id for c in captures])
            if summary["error_codes"] != ["prior_attempt_incomplete"] and attempts[0]["outcome"] != "success":
                _require(summary["error_codes"] == [attempts[0]["reason"]])
            for observation, capture in zip(summary["observations"], captures):
                _require(observation == {key: capture.version_id if key == "capture_id" else getattr(capture, key)
                                         for key in observation})
            bundle = store.bundle(summary["bundle_id"])["bundle"]
            _require(summary["gate"] == {key: bundle["gate"][key] for key in summary["gate"]})
            _require(bundle["gate"]["mode"] == MODE
                     and instant(summary["started_at"]) <= instant(bundle["gate"]["as_of"]) <= instant(summary["finished_at"]))
            if (captures and instant(captures[0].expires_at) > utc_now()
                    and not store.db.execute("SELECT 1 FROM tombstones").fetchone()):
                try:
                    parsed = store.retained_parse(captures[0].version_id)
                except Invalid:
                    if instant(captures[0].expires_at) > utc_now():
                        _require(summary["quality"]["parse_success_count"] == 0)
                else:
                    records = [json.loads(row[0]) for row in store.db.execute("SELECT record FROM evidence")]
                    _require(sorted(canonical(row) for row in records) == sorted(canonical(row.to_dict()) for row in parsed.records))
                    if summary["error_codes"] != ["prior_attempt_incomplete"]:
                        _require(summary["quality"]["parse_success_count"] == 1 and summary["parse"] == parsed.counts)
                        from .gate import evaluate
                        actual_gate = evaluate(parsed.records, runtime.profile_for(MODE),
                                               Calendar("unavailable-live-calendars-v1", ()),
                                               bundle["gate"]["as_of"], instant(bundle["gate"]["as_of"]))
                        _require(actual_gate == bundle["gate"])
        else:
            _require(run["summary"] is None and run["finished_at"] is None
                     and not any(row["kind"] == "completed" for row in events))
        return run
    except Exception:
        raise Invalid("invalid_local_state") from None


def _release(store, lease):
    current = store.db.execute("SELECT * FROM lease WHERE singleton=1").fetchone()
    if (current["owner"], current["generation"], current["run_id"]) != (lease.owner, lease.generation, lease.run_id):
        return
    try:
        store.release(lease)
    except Busy:
        # Expiry must not prevent cleanup; fence again inside the transaction.
        with store.transaction():
            current = store.db.execute("SELECT * FROM lease WHERE singleton=1").fetchone()
            if (current["owner"], current["generation"], current["run_id"]) != (lease.owner, lease.generation, lease.run_id):
                return
            store.db.execute("UPDATE attempts SET outcome='unknown',reason='collection_interrupted' WHERE run_id=? AND outcome='reserved'", (lease.run_id,))
            store.db.execute("UPDATE runs SET status='interrupted' WHERE run_id=? AND status!='complete'", (lease.run_id,))
            store._event(lease.run_id, utc_now(), "released", {"generation": lease.generation})
            store.db.execute("UPDATE lease SET owner=NULL,expires_at=NULL,run_id=NULL WHERE singleton=1")


def _finish(store, lease, run, snapshot, parsed=None, captures=(), outcome="unknown", errors=()):
    gate, bundle = runtime._freeze(store, lease, parsed.records if parsed else (), MODE,
                                   Calendar("unavailable-live-calendars-v1", ()))
    errors = list(errors)
    if parsed and parsed.records:
        if parsed.rejections:
            errors.append("partial_parse")
        if gate["domains"]["news"]["status"] != "PASS":
            errors.append("news_gate_blocked")
    state = "live_verified" if outcome == "observed" and not errors else "degraded"
    summary = runtime._summary(store, lease, MODE, "fed_press", run["started_at"],
                               outcome, state, parsed, list(captures), bundle, gate, errors)
    result = _record(snapshot, summary)
    store.finish(lease, summary)
    _inspect(store)
    return result


def _recover(store, run):
    snapshot = run["intent"][len(INTENT):]
    if not run["attempts"]:
        return _record(snapshot, reason="prior_run_without_attempt")
    lease, run = store.begin(TASK, MODE, intent=run["intent"])
    if lease is None:
        return _record(snapshot, run["summary"])
    try:
        captures = [row[0] for row in store.db.execute("SELECT capture_id FROM captures")]
        return _finish(store, lease, run, snapshot, captures=captures,
                       errors=["prior_attempt_incomplete"])
    finally:
        _release(store, lease)


def _collect(store, snapshot):
    lease, run = store.begin(TASK, MODE, intent=INTENT + snapshot)
    if lease is None:
        _inspect(store)
        return _record(snapshot, run["summary"])
    try:
        # Another process may have finished between the first inspection and begin.
        if not store.db.execute("SELECT 1 FROM events WHERE kind='p4s_intent'").fetchone():
            _bind(store, lease, snapshot)
        _inspect(store)
        if run["attempts"]:
            captures = [row[0] for row in store.db.execute("SELECT capture_id FROM captures")]
            return _finish(store, lease, run, snapshot, captures=captures,
                           errors=["prior_attempt_incomplete"])
        try:
            source = sources.load_catalog(ROOT / "config/sources.toml")["fed_press"]
            source.authorize()
        except Exception:
            return _record(snapshot, reason="source_not_authorized")
        try:
            current = _authorize()
        except Invalid:
            return _record(snapshot, reason="probe_gate_blocked")
        if current != snapshot:
            return _record(snapshot, reason="snapshot_changed")
        # Governance can take time; renew before reserving, never after expiry.
        store.renew(lease)
        _require(store.db.execute("SELECT count(*) FROM attempts").fetchone()[0] == 0)
        attempt = store.reserve(lease)
        try:
            response = sources.fetch(source)
            if type(response) is not sources.Response:
                raise sources.TransportError("invalid_transport_response", "unknown")
            response.validate()
        except Exception as exc:
            code, outcome = "transport_unknown", "unknown"
            if isinstance(exc, sources.TransportError):
                candidate = str(exc)
                if candidate in FAILED_TRANSPORT and exc.outcome == "failed":
                    code, outcome = candidate, "failed"
                elif candidate in UNKNOWN_TRANSPORT and exc.outcome == "unknown":
                    code = candidate
            store.settle(lease, attempt, outcome, 0 if outcome == "failed" else None, code)
            return _finish(store, lease, run, snapshot, outcome=outcome, errors=[code])
        store.settle(lease, attempt, "success", len(response.body), "response_received")
        now = utc_now()
        captured = stamp(now)
        capture = Capture("fed_press", sources.FED_URL, captured,
                          store.first_seen("fed_press", digest(response.body), captured), None,
                          digest(response.body), response.response_hash, response.content_type,
                          "rss-v1", source.retention, source.retention_basis,
                          stamp(now + timedelta(days=1)), MODE, attempt)
        try:
            parsed = sources.parse(response.body, capture)
        except Invalid:
            identity = store.put_capture(capture, response.body, lease=lease)
            return _finish(store, lease, run, snapshot, captures=[identity],
                           outcome="failed", errors=["parse_failed"])
        identity = store.put_capture(capture, response.body, parsed.records, lease=lease)
        return _finish(store, lease, run, snapshot, parsed, [identity],
                       "observed" if parsed.records else "failed",
                       [] if parsed.records else ["empty_or_all_rejected_feed"])
    finally:
        _release(store, lease)


def _existing(store):
    run = _inspect(store)
    if run is None:
        return None
    if run["status"] == "complete":
        return _record(run["intent"][len(INTENT):], run["summary"])
    return _recover(store, run)


def probe(allow_network=False):
    """Fixed production capability. Any existing attempt precludes dispatch."""
    path = _path()
    if path.exists():
        if path.stat().st_size == 0:
            raise Invalid("invalid_local_state")
        with _MetadataStore(path) as store:
            result = _existing(store)
            if result is not None:
                return result
    if allow_network is not True:
        raise Invalid("explicit_allow_network_required")
    snapshot = _authorize()
    with _MetadataStore(path) as store:
        result = _existing(store)
        return result if result is not None else _collect(store, snapshot)


def status():
    path = _path()
    if path.exists():
        if path.stat().st_size == 0:
            raise Invalid("invalid_local_state")
        with _MetadataStore(path) as store:
            result = _existing(store)
            if result is not None:
                return result
    return _record(_authorize())


def sweep():
    path = _path()
    if path.exists():
        if path.stat().st_size == 0:
            raise Invalid("invalid_local_state")
        with _MetadataStore(path) as store:
            result = _existing(store)
            store.sweep()
            _inspect(store)
            if result is not None:
                return result
    return _record(_authorize())


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Invalid("invalid_cli_arguments")


def main(argv=None):
    parser = _Parser(prog="python -m market_intelligence.collection", allow_abbrev=False)
    parser.add_argument("command", choices=("probe", "status", "sweep"))
    parser.add_argument("--allow-network", action="store_true")
    try:
        args = parser.parse_args(argv)
        if args.allow_network and args.command != "probe":
            raise Invalid("invalid_cli_arguments")
        result = probe(args.allow_network) if args.command == "probe" else (status() if args.command == "status" else sweep())
        validate_probe_record(result, result["snapshot_id"])
        print(json.dumps(result, sort_keys=True, indent=2, allow_nan=False))
        return 0 if result["status"] == "RECORDED" and result["observation"]["source_state"] == "live_verified" else 2
    except (Exception, KeyboardInterrupt) as exc:
        reason = "collection_interrupted" if isinstance(exc, KeyboardInterrupt) else (
            "probe_in_progress" if isinstance(exc, Busy) else "local_operation_failed")
        if isinstance(exc, Invalid) and str(exc) in LOCAL_ERRORS:
            reason = str(exc)
        print(json.dumps({"status": "ERROR", "reason": reason}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
