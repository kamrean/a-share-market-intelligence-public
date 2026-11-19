"""Two separately released phases; durable quotas and sanitized results only."""

from contextlib import redirect_stderr, redirect_stdout
from datetime import timedelta
from pathlib import Path
import sqlite3
import sys
import uuid

from . import baostock_capture as capture
from . import baostock_transport as transport
from .intelligence import decode_input
from .models import Invalid, canonical, digest, exact, fingerprint, integer, stamp, utc_now
from .store import LEASE_SECONDS, Lease, external_path

ROOT = Path(__file__).resolve().parents[1]
PHASES = {"diagnostic": 1, "verification": 7}
TASK_PREFIX = "p4d-baostock-"
MAX_RECEIPT = 32768
CLEANUP = ("socket_closed", "bindings_restored", "transport_payloads_disposed",
           "capture_payloads_disposed", "payload_tables_empty")
DIAGNOSTIC_TRANSPORT = {
    "status", "reason", "started_at", "finished_at", "requests_sent", "wire_bytes",
    "decoded_bytes", "wire_sha256", "validated_responses", *CLEANUP[:3],
}


def _require(condition):
    if not condition:
        raise Invalid("invalid_recovery_state")


def validate_diagnostic(value):
    exact(value, transport.empty_diagnostic())
    integer(value["schema_version"], 1, 1)
    integer(value["request_index"], 0, 1)
    _require(value["method"] in (None, "login"))
    _require((value["request_index"] == 0) == (value["method"] is None))
    _require(value["first_failure_point"] is None or value["first_failure_point"] in transport.PROTOCOL_POINTS)
    integer(value["frame_bytes"], 0, transport.MAX_FRAME)
    integer(value["header_bytes"], 0, min(21, value["frame_bytes"]))
    for key in ("header_structure", "declared_length_syntax", "crc_syntax", "crc_match",
                "length_bytes_match", "length_characters_match"):
        _require(value[key] in {"pass", "fail", "not_reached"})
    _require(value["utf8"] in {"pass", "fail", "not_reached", "indeterminate"})
    _require(value["version"] in {"expected", "other", "unavailable"})
    _require(value["response_type"] in {"expected", "exception_04", "other", "unavailable"})
    _require(value["terminator"] in {"not_seen", "at_end", "nonterminal"})
    if value["declared_length"] is not None:
        integer(value["declared_length"], 0, 9999999999)
    _require((value["declared_length_syntax"] == "pass") == (value["declared_length"] is not None))
    for key in ("checksum_characters", "body_bytes", "body_characters"):
        if value[key] is not None:
            integer(value[key], 0, transport.MAX_FRAME)
    if value["crc_match"] != "not_reached":
        _require(value["crc_syntax"] == "pass" and value["terminator"] == "at_end")
    for field, count in (("length_bytes_match", value["body_bytes"]),
                         ("length_characters_match", value["body_characters"])):
        expected = ("not_reached" if count is None or value["declared_length"] is None
                    else "pass" if count == value["declared_length"] else "fail")
        _require(value[field] == expected)
    return value


def _diagnostic_transport(value):
    exact(value, DIAGNOSTIC_TRANSPORT)
    _require(value["status"] in {"COMPLETE", "FAILED"} and value["reason"] in transport.REASONS)
    _require((value["status"] == "COMPLETE") == (value["reason"] == "complete"))
    _require(capture._time(value["started_at"]) <= capture._time(value["finished_at"]))
    for key, limit in (("requests_sent", 1), ("validated_responses", 1),
                       ("wire_bytes", transport.MAX_WIRE), ("decoded_bytes", transport.MAX_DECODED)):
        integer(value[key], 0, limit)
    _require(value["validated_responses"] <= value["requests_sent"])
    fingerprint(value["wire_sha256"])
    for key in CLEANUP[:3]:
        _require(type(value[key]) is bool)
    if value["status"] == "COMPLETE":
        _require(value["requests_sent"] == value["validated_responses"] == 1
                 and value["wire_bytes"] > 0 and value["decoded_bytes"] > 0
                 and all(value[k] for k in CLEANUP[:3]))
    return value


def not_run_receipt(snapshot_id, phase):
    return validate_recovery_receipt({"schema_version": 1, "package_id": "P4D", "phase": phase,
        "snapshot_id": snapshot_id, "status": "NOT_RUN", "reason": "awaiting_release",
        "observation": None}, snapshot_id, phase)


def validate_recovery_receipt(value, snapshot_id, phase):
    """Pure validation, including the unchanged P4B composition schema reuse."""
    try:
        fingerprint(snapshot_id)
        _require(type(phase) is str and phase in PHASES)
        exact(value, {"schema_version", "package_id", "phase", "snapshot_id", "status", "reason", "observation"})
        integer(value["schema_version"], 1, 1)
        _require(value["package_id"] == "P4D" and value["phase"] == phase and value["snapshot_id"] == snapshot_id)
        if value["status"] == "NOT_RUN":
            _require(value["observation"] is None and value["reason"] == "awaiting_release")
        elif phase == "verification":
            # Reuse a pure schema validator, not P4B authority or mutable globals.
            legacy = {k: v for k, v in value.items() if k != "phase"}
            legacy["package_id"] = "P4B"
            capture.validate_observation_receipt(legacy, snapshot_id)
            if value["observation"]["transport"] is not None:
                integer(value["observation"]["transport"]["requests_sent"], 0, 7)
        else:
            _require((value["status"], value["reason"]) in {
                ("RECORDED", "diagnostic_recorded"), ("UNKNOWN", "transport_unknown")})
            o = value["observation"]
            exact(o, {"source_id", "sdk_version", "run_id", "attempt_id", "started_at", "finished_at",
                      "transport", "protocol", "cleanup"})
            _require(o["source_id"] == capture.SOURCE and o["sdk_version"] == "0.9.4")
            capture._id(o["run_id"])
            capture._id(o["attempt_id"])
            start = capture._time(o["started_at"])
            finish = capture._time(o["finished_at"]) if o["finished_at"] is not None else None
            _require(finish is None or start <= finish)
            t = o["transport"]
            exact(o["cleanup"], {*CLEANUP, "checked_at"})
            for key in CLEANUP:
                _require(o["cleanup"][key] is None or type(o["cleanup"][key]) is bool)
            _require(o["cleanup"]["checked_at"] == o["finished_at"])
            if t is None:
                _require(value["status"] == "UNKNOWN" and o["protocol"] is None
                         and all(o["cleanup"][k] is None for k in CLEANUP[:3]))
            else:
                _diagnostic_transport(t)
                validate_diagnostic(o["protocol"])
                _require(value["status"] == "RECORDED" and finish is not None
                         and start <= capture._time(t["started_at"]) <= capture._time(t["finished_at"]) <= finish)
                _require(o["protocol"]["frame_bytes"] <= t["wire_bytes"])
                _require((t["status"] == "COMPLETE") == (o["protocol"]["first_failure_point"] is None))
                for key in CLEANUP[:3]:
                    _require(o["cleanup"][key] == t[key])
        _require(len(canonical(value).encode()) <= MAX_RECEIPT)
    except Exception:
        raise Invalid("invalid_recovery_receipt") from None
    return value


def _path():
    return external_path(Path.home() / ".a-share-market-intelligence" / "p4d-baostock-recovery.sqlite")


def _rows(store, table, limit, columns):
    _require(store.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] <= limit)
    for column, maximum in columns.items():
        _require(not store.db.execute(f"SELECT 1 FROM {table} WHERE length({column})>?", (maximum,)).fetchone())
    return [dict(row) for row in store.db.execute(f"SELECT * FROM {table} ORDER BY rowid")]


def _inspect(store):
    capture._empty_payloads(store)
    runs = _rows(store, "runs", 2, {"run_id": 32, "task_key": 160, "intent": 256, "summary": 0})
    attempts = _rows(store, "attempts", 2, {"attempt_id": 32, "reason": 100})
    events = _rows(store, "events", 12, {"kind": 32, "detail": MAX_RECEIPT + 128})
    leases = _rows(store, "lease", 1, {"owner": 32})
    _require(len(leases) == 1)
    lease = leases[0]
    integer(lease["generation"], 0, 2)
    _require(lease["singleton"] == 1)
    if lease["owner"] is None:
        _require(lease["run_id"] is None and lease["expires_at"] is None)
    else:
        capture._id(lease["owner"])
        capture._id(lease["run_id"])
        capture._time(lease["expires_at"])
    _require(len(attempts) == len(runs) == lease["generation"])
    found, seen_events = {}, set()
    for number, run in enumerate(runs, 1):
        phase = run["task_key"].removeprefix(TASK_PREFIX)
        _require(phase in PHASES and run["task_key"] == TASK_PREFIX + phase and phase not in found)
        _require(phase == ("diagnostic" if number == 1 else "verification"))
        capture._id(run["run_id"])
        prefix = TASK_PREFIX + phase + ":"
        _require(run["intent"].startswith(prefix))
        snapshot = run["intent"][len(prefix):]
        fingerprint(snapshot)
        _require(run["mode"] == "live_observation" and run["summary"] is None
                 and run["status"] in {"running", "interrupted", "complete"})
        start = capture._time(run["started_at"])
        if number == 2:
            prior = found["diagnostic"][2]
            _require(prior is not None and capture._time(prior["observation"]["finished_at"]) <= start)
        candidates = [a for a in attempts if a["run_id"] == run["run_id"]]
        _require(len(candidates) == 1)
        attempt = candidates[0]
        capture._id(attempt["attempt_id"])
        reserved = capture._time(attempt["reserved_at"])
        _require(start == reserved and attempt["day"] == stamp(reserved)[:10]
                 and attempt["reserved_bytes"] == transport.MAX_WIRE
                 and attempt["outcome"] in {"reserved", "success", "failed", "unknown"})
        _require(attempt["reason"] in {None, "response_received", "transport_failed", "transport_unknown", "released_pending"})
        if attempt["observed_bytes"] is not None:
            integer(attempt["observed_bytes"], 0, transport.MAX_WIRE)
        if attempt["outcome"] in {"reserved", "unknown"}:
            _require(attempt["observed_bytes"] is None)
        result, last, kinds = None, start, []
        for event in [e for e in events if e["run_id"] == run["run_id"]]:
            seen_events.add(event["event_id"])
            _require(last <= capture._time(event["at"]))
            last = capture._time(event["at"])
            detail = decode_input(event["detail"].encode())
            kind = event["kind"]
            kinds.append(kind)
            if kind in {"started", "released"}:
                exact(detail, {"generation"})
                integer(detail["generation"], number, number)
                _require(detail == {"generation": number})
                if kind == "started":
                    _require(event["at"] == run["started_at"])
            elif kind == "p4d_intent":
                exact(detail, {"phase", "snapshot_id", "reserved_requests"})
                integer(detail["reserved_requests"], PHASES[phase], PHASES[phase])
                _require(detail == {"phase": phase, "snapshot_id": snapshot, "reserved_requests": PHASES[phase]})
            elif kind == "reserved":
                exact(detail, {"attempt_id", "bytes"})
                integer(detail["bytes"], transport.MAX_WIRE, transport.MAX_WIRE)
                _require(detail == {"attempt_id": attempt["attempt_id"], "bytes": transport.MAX_WIRE}
                         and event["at"] == attempt["reserved_at"])
            elif kind == "settled":
                _require(detail == {"attempt_id": attempt["attempt_id"], "outcome": attempt["outcome"]})
            elif kind == "p4d_result":
                exact(detail, {"receipt", "receipt_hash"})
                result = validate_recovery_receipt(detail["receipt"], snapshot, phase)
                _require(result["status"] != "NOT_RUN" and digest(result) == detail["receipt_hash"])
                o = result["observation"]
                _require(o["run_id"] == run["run_id"] and o["attempt_id"] == attempt["attempt_id"]
                         and o["started_at"] == run["started_at"]
                         and o["finished_at"] == run["finished_at"] == event["at"])
                t = o["transport"]
                outcome = "unknown" if t is None else "success" if t["status"] == "COMPLETE" else "failed"
                _require(attempt["outcome"] == outcome and attempt["observed_bytes"] == (t["wire_bytes"] if t else None))
            else:
                raise Invalid("invalid_recovery_event")
        _require(kinds[:3] == ["started", "p4d_intent", "reserved"])
        if result is not None:
            _require(kinds == ["started", "p4d_intent", "reserved", "settled", "p4d_result"]
                     and run["status"] == "complete" and lease["run_id"] != run["run_id"])
        else:
            _require(kinds in (["started", "p4d_intent", "reserved"],
                              ["started", "p4d_intent", "reserved", "released"])
                     and run["status"] != "complete" and run["finished_at"] is None)
        found[phase] = (run, attempt, result)
    _require(len(seen_events) == len(events) and sum(PHASES[p] for p in found) <= 9)
    if not runs:
        _require(not events and lease["owner"] is None)
    if lease["owner"] is not None:
        _require(lease["run_id"] == runs[-1]["run_id"] and runs[-1]["status"] == "running")
    return found


def _available(found, phase, diagnostic_digest):
    _require(phase in PHASES and phase not in found)
    if phase == "diagnostic":
        _require(not found and diagnostic_digest is None)
    else:
        fingerprint(diagnostic_digest)
        _require(set(found) == {"diagnostic"})
        result = found["diagnostic"][2]
        _require(result is not None and result["status"] == "RECORDED" and digest(result) == diagnostic_digest)
        o = result["observation"]
        _require(all(o["cleanup"][k] is True for k in CLEANUP) and o["transport"]["requests_sent"] == 1)
        _require(o["protocol"]["first_failure_point"] in transport.PROTOCOL_POINTS
                 and not o["protocol"]["first_failure_point"].startswith("transport_")
                 and o["protocol"]["frame_bytes"] > 0)


def _read_found():
    path = _path()
    for suffix in ("-journal", "-wal", "-shm"):
        _require(not Path(str(path) + suffix).exists())
    if not path.exists():
        return {}
    with capture._ReadOnlyJournal(path) as store:
        found = _inspect(store)
        store.check_identity()
    return found


def assert_phase_available(phase, snapshot_id, diagnostic_digest=None):
    fingerprint(snapshot_id)
    try:
        _available(_read_found(), phase, diagnostic_digest)
    except Exception:
        raise Invalid("recovery_allowance_unavailable") from None


class _Journal(capture._Journal):
    def reserve_phase(self, phase, snapshot, diagnostic_digest):
        # The run, phase budget, lease and request reservation commit atomically.
        # A crash can never create a resumable unreserved phase.
        fingerprint(snapshot)
        with self.transaction():
            _available(_inspect(self), phase, diagnostic_digest)
            self._capacity(MAX_RECEIPT + 65536)
            current = dict(self.db.execute("SELECT * FROM lease WHERE singleton=1").fetchone())
            _require(current["owner"] is None)
            now = utc_now()
            lease = Lease(uuid.uuid4().hex, uuid.uuid4().hex, current["generation"] + 1)
            attempt = uuid.uuid4().hex
            task = TASK_PREFIX + phase
            self.db.execute("INSERT INTO runs VALUES (?,?,?,?,'running',?,NULL,NULL)",
                            (lease.run_id, task, "live_observation", task + ":" + snapshot, stamp(now)))
            self.db.execute("UPDATE lease SET run_id=?,owner=?,generation=?,expires_at=? WHERE singleton=1",
                            (lease.run_id, lease.owner, lease.generation, stamp(now + timedelta(seconds=LEASE_SECONDS))))
            self.db.execute("INSERT INTO attempts VALUES (?,?,?,?,'reserved',?,NULL,NULL)",
                            (attempt, lease.run_id, stamp(now), stamp(now)[:10], transport.MAX_WIRE))
            self._event(lease.run_id, now, "started", {"generation": lease.generation})
            self._event(lease.run_id, now, "p4d_intent", {"phase": phase, "snapshot_id": snapshot,
                                                       "reserved_requests": PHASES[phase]})
            self._event(lease.run_id, now, "reserved", {"attempt_id": attempt, "bytes": transport.MAX_WIRE})
        return lease, self.run(lease.run_id), attempt

    def finish_phase(self, lease, receipt):
        validate_recovery_receipt(receipt, receipt["snapshot_id"], receipt["phase"])
        o = receipt["observation"]
        now, t = capture._time(o["finished_at"]), o["transport"]
        outcome = "unknown" if t is None else "success" if t["status"] == "COMPLETE" else "failed"
        reason = {"unknown": "transport_unknown", "success": "response_received", "failed": "transport_failed"}[outcome]
        with self.transaction():
            self._owned(lease, now)
            capture._empty_payloads(self)
            self._capacity(MAX_RECEIPT + 16384)
            changed = self.db.execute("UPDATE attempts SET outcome=?,observed_bytes=?,reason=? WHERE attempt_id=? AND run_id=? AND outcome='reserved'",
                (outcome, t["wire_bytes"] if t else None, reason, o["attempt_id"], lease.run_id)).rowcount
            _require(changed == 1 and o["run_id"] == lease.run_id)
            self._event(lease.run_id, now, "settled", {"attempt_id": o["attempt_id"], "outcome": outcome})
            self._event(lease.run_id, now, "p4d_result", {"receipt": receipt, "receipt_hash": digest(receipt)})
            changed = self.db.execute("UPDATE runs SET status='complete',finished_at=?,summary=NULL WHERE run_id=? AND status='running'",
                                      (stamp(now), lease.run_id)).rowcount
            _require(changed == 1)
            self.db.execute("UPDATE lease SET owner=NULL,expires_at=NULL,run_id=NULL WHERE singleton=1 AND owner=? AND generation=?",
                            (lease.owner, lease.generation))


def _snapshot():
    from tools import governance as g
    contents = {name: g.read_text(ROOT, name) for name in g.PROTECTED}
    # Phase-dependent protection may include the immutable phase-A attestations.
    for name in g.CURRENT_ATTESTATIONS:
        if g.safe_path(ROOT, name).exists():
            contents[name] = g.read_text(ROOT, name)
    return g.snapshot(contents)[0]


def _authorize(phase):
    from tools import governance as g
    try:
        result = g.recovery_gate(ROOT, phase=phase)
        expected = "LOCAL_READY_FOR_PROTOCOL_DIAGNOSTIC" if phase == "diagnostic" else "LOCAL_READY_FOR_RECOVERY_VERIFICATION"
        _require(result["package_id"] == "P4D" and result["phase"] == phase and result["verdict"] == expected)
        fingerprint(result["snapshot_id"])
        return result["snapshot_id"], result["diagnostic_digest"]
    except Exception:
        raise Invalid("recovery_gate_blocked") from None


def _diagnostic_receipt(snapshot, run, attempt, t, protocol, disposed, checked):
    cleanup = {k: t[k] if t else None for k in CLEANUP[:3]}
    cleanup.update(capture_payloads_disposed=disposed, payload_tables_empty=True if checked else None, checked_at=checked)
    return {"schema_version": 1, "package_id": "P4D", "phase": "diagnostic", "snapshot_id": snapshot,
        "status": "RECORDED" if t else "UNKNOWN", "reason": "diagnostic_recorded" if t else "transport_unknown",
        "observation": {"source_id": capture.SOURCE, "sdk_version": "0.9.4", "run_id": run["run_id"],
                        "attempt_id": attempt, "started_at": run["started_at"], "finished_at": checked,
                        "transport": t, "protocol": protocol, "cleanup": cleanup}}


def _verification_receipt(snapshot, run, attempt, t, inp, computation, batch, disposed, checked, status, reason):
    value = capture._make_receipt(snapshot, run, attempt, t, inp, computation, batch, disposed, checked, status, reason)
    return {**value, "package_id": "P4D", "phase": "verification"}


def _collect(store, phase, snapshot, diagnostic_digest):
    lease, run, attempt = store.reserve_phase(phase, snapshot, diagnostic_digest)
    memory, t, protocol = capture._Memory(), None, None
    computation, batch, status, reason = capture._not_computed(), None, "UNKNOWN", "transport_unknown"
    try:
        _require(_snapshot() == snapshot)
        store.renew(lease)
        try:
            with redirect_stdout(capture._Discard()), redirect_stderr(capture._Discard()):
                result, protocol = transport.run_recovery(phase=phase, accept_response=memory.accept)
            _require(type(result) is transport.TransportReceipt)
            t = result.to_dict()
            if phase == "diagnostic":
                t = {**{k: v for k, v in t.items() if k in DIAGNOSTIC_TRANSPORT},
                     "validated_responses": len(t["responses"])}
                _diagnostic_transport(t)
                validate_diagnostic(protocol)
            else:
                capture._transport_record(t)
                if t["status"] == "COMPLETE":
                    store.renew(lease)
                    try:
                        with redirect_stdout(capture._Discard()), redirect_stderr(capture._Discard()):
                            computation, batch, parsed = capture._compose(memory, t, run["run_id"], attempt)
                        status = "OBSERVED" if len(parsed["latest_session_symbols"]) == 2 else "PARTIAL" if parsed["accepted_rows"] else "EMPTY"
                        reason = capture.REASONS[status]
                    except (Exception, KeyboardInterrupt):
                        computation, batch = capture._not_computed("FAILED"), None
                        status, reason = "FAILED", "computation_failed"
                else:
                    status, reason = "FAILED", "transport_failed"
        except (Exception, KeyboardInterrupt, SystemExit):
            t, protocol = None, None
            status, reason = "UNKNOWN", "transport_unknown"
            computation, batch = capture._not_computed(), None
        finally:
            inp = memory.metadata(t if phase == "verification" else None)
            if computation["status"] == "VERIFIED":
                inp.update(parsed)
            memory.dispose()
        if computation["status"] == "VERIFIED" and not capture._time(computation["completed_at"]) <= utc_now() < capture._time(computation["expires_at"]):
            computation, batch = capture._not_computed("FAILED"), None
            inp.update(accepted_rows=None, rejected_rows=None, rejection_counts=None, latest_session_symbols=[])
            status, reason = "FAILED", "computation_failed"
        store.renew(lease)
        capture._empty_payloads(store)
        checked = stamp(utc_now())
        if phase == "diagnostic":
            receipt = _diagnostic_receipt(snapshot, run, attempt, t, protocol, memory.disposed, checked)
        else:
            receipt = _verification_receipt(snapshot, run, attempt, t, inp, computation, batch, memory.disposed, checked, status, reason)
        store.finish_phase(lease, receipt)
    finally:
        memory.dispose()
        try:
            if store.run(run["run_id"])["status"] != "complete":
                store.release(lease)
        except (Invalid, sqlite3.Error):
            pass


def status(phase):
    _require(phase in PHASES)
    found = _read_found()
    if phase not in found:
        return not_run_receipt(_snapshot(), phase)
    run, attempt, receipt = found[phase]
    if receipt is None:
        snapshot = run["intent"].split(":", 1)[1]
        if phase == "diagnostic":
            receipt = _diagnostic_receipt(snapshot, run, attempt["attempt_id"], None, None, None, None)
        else:
            receipt = _verification_receipt(snapshot, run, attempt["attempt_id"], None, capture._Memory().metadata(None),
                        capture._not_computed(), None, None, None, "UNKNOWN", "prior_attempt_incomplete")
    return validate_recovery_receipt(receipt, receipt["snapshot_id"], phase)


def observe(phase, allow_network=False):
    if allow_network is not True:
        raise Invalid("explicit_allow_network_required")
    _require(phase in PHASES)
    snapshot, diagnostic_digest = _authorize(phase)
    assert_phase_available(phase, snapshot, diagnostic_digest)
    with _Journal(_path()) as store:
        _collect(store, phase, snapshot, diagnostic_digest)
    return status(phase)


def main(argv=None):
    try:
        args = list(sys.argv[1:] if argv is None else argv)
        if len(args) == 3 and args[0] == "observe" and args[2] == "--allow-network":
            result = observe(args[1], True)
        elif len(args) == 2 and args[0] == "status":
            result = status(args[1])
        else:
            raise Invalid("invalid_cli_arguments")
        print(canonical(result))
        return 0
    except (Exception, KeyboardInterrupt):
        print(canonical({"status": "ERROR", "reason": "recovery_operation_blocked"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
