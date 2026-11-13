"""Append-only reports and conservative durable fake-delivery reservations."""

from collections import defaultdict
from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass
from datetime import timedelta
import os
from pathlib import Path
import re
import sqlite3
import uuid

from .intelligence import decode_input
from .models import Invalid, canonical, digest, exact, fingerprint, instant, integer, stamp, utc_now
from .publication import load_config, validate_config, verify_report
from .store import external_path

APPLICATION_ID = 0x50344C31
SCHEMA = """
CREATE TABLE meta (singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    config TEXT NOT NULL, last_at TEXT NOT NULL);
CREATE TABLE reports (sequence INTEGER PRIMARY KEY, report_id TEXT UNIQUE NOT NULL,
    record TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE chunks (chunk_id TEXT PRIMARY KEY, report_id TEXT NOT NULL REFERENCES reports(report_id),
    ordinal INTEGER NOT NULL, record TEXT NOT NULL, UNIQUE(report_id, ordinal));
CREATE TABLE reservations (attempt_id INTEGER PRIMARY KEY,
    chunk_id TEXT UNIQUE NOT NULL REFERENCES chunks(chunk_id), record TEXT NOT NULL);
CREATE TABLE outcomes (attempt_id INTEGER PRIMARY KEY REFERENCES reservations(attempt_id), record TEXT NOT NULL);
PRAGMA application_id=1345604657;
PRAGMA user_version=1;
"""
for _table in ("reports", "chunks", "reservations", "outcomes"):
    for _operation in ("UPDATE", "DELETE"):
        SCHEMA += ("CREATE TRIGGER " + _table + "_no_" + _operation.lower() + " BEFORE "
                   + _operation + " ON " + _table
                   + " BEGIN SELECT RAISE(ABORT, 'append_only'); END;\n")


class DeliveryBlocked(Invalid):
    pass


REFUSALS = frozenset({"storage_limit", "clock_out_of_bounds", "clock_rollback",
                     "persistent_run_budget", "persistent_day_budget", "persistent_rate_limit",
                     "store_has_unknown_pending_or_failed_send", "attempt_storage_limit"})


def report_status(rows, refusal=None):
    if refusal is not None and refusal not in REFUSALS:
        raise Invalid("unrecognized dispatch refusal")
    for row in rows:
        if row["status"] in ("unknown", "failed"):
            outcome = row["outcome"]
            return row["status"], outcome["reason"] if outcome else "interrupted_or_inflight_unknown"
    if all(row["status"] == "sent" for row in rows):
        return "completed", "all_chunks_fake_sent"
    return ("blocked", refusal) if refusal else ("queued", "not_dispatched")


@dataclass(frozen=True)
class Reservation:
    attempt_id: int
    chunk_id: str
    report_id: str
    run_id: str
    owner: str
    sink: str
    reserved_at: str
    day: str
    chars: int


@dataclass(frozen=True)
class Outcome:
    attempt_id: int
    status: str
    reason: str
    finished_at: str
    acknowledgement: dict | None


def run_identity(report_id, sink):
    return digest({"report_id": report_id, "sink": sink, "mode": "synthetic_test"})


def validate_ack(ack, reservation):
    exact(ack, {"label", "reservation_id", "chunk_id", "report_id", "sink", "status", "received_chars", "not_sent"})
    if (ack["label"] != "SIMULATED ACK ONLY" or ack["reservation_id"] != digest(reservation)
            or ack["chunk_id"] != reservation["chunk_id"] or ack["report_id"] != reservation["report_id"]
            or ack["sink"] != reservation["sink"] or ack["status"] not in ("sent", "failed")):
        raise Invalid("unbound fake acknowledgement")
    expected = reservation["chars"] if ack["status"] == "sent" else 0
    integer(ack["received_chars"], expected, expected)
    if ack["not_sent"] is not (ack["status"] == "failed"):
        raise Invalid("ambiguous fake acknowledgement")
    return ack["status"]


def _record(encoded):
    if type(encoded) is not str or len(encoded) > 500000:
        raise Invalid("record byte bound")
    value = decode_input(encoded.encode())
    if canonical(value) != encoded:
        raise Invalid("noncanonical stored record")
    return value


def _signature(db):
    return [tuple(r) for r in db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name")]


def _check_lineage(report, previous):
    content = report["content"]
    target = content["supersedes"]
    if target is None:
        return
    if target not in previous or target == report["report_id"]:
        raise Invalid("dangling or cyclic correction")
    if any(r["content"]["supersedes"] == target for r in previous.values()):
        raise Invalid("correction target is not current leaf")
    old = previous[target]["content"]
    if any(content[k] != old[k] for k in ("dataset_id", "mode", "as_of")):
        raise Invalid("correction dataset mismatch")


class Ledger:
    """One store is the quota boundary. Pending work has no automatic recovery."""

    def __init__(self, path, config=None, clock=utc_now):
        self.config_json = canonical(validate_config(load_config() if config is None else config))
        self.config = _record(self.config_json)
        self.clock, self.owner = clock, uuid.uuid4().hex
        self.path = external_path(path)
        self._size()
        exists = self.path.exists()
        try:
            if exists:
                with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as db:
                    self._validate(db)
            else:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                external_path(self.path)
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(fd)
            self.file_identity = self._file_identity()
            self.db = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=2, isolation_level=None)
            if not exists:
                self.db.executescript("BEGIN IMMEDIATE;\n" + SCHEMA)
                self.db.execute("INSERT INTO meta VALUES (1,?,?)", (self.config_json, self._now()))
                self.db.execute("COMMIT")
            self._validate(self.db)
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("PRAGMA max_page_count=" + str(self.config["max_store_bytes"] // 8192))
        except (Invalid, sqlite3.Error, OSError, ValueError, TypeError, KeyError) as exc:
            if hasattr(self, "db"):
                self.db.close()
            raise Invalid("publication ledger unavailable or incompatible") from exc

    def _file_identity(self):
        stat = self.path.stat()
        return stat.st_dev, stat.st_ino

    def _size(self):
        external_path(self.path)
        if hasattr(self, "file_identity") and self._file_identity() != self.file_identity:
            raise Invalid("ledger file replaced")
        total = sum(p.stat().st_size for p in (self.path, *(Path(str(self.path) + suffix)
                    for suffix in ("-journal", "-wal", "-shm"))) if p.exists())
        if total > self.config["max_store_bytes"]:
            raise DeliveryBlocked("storage_limit")

    def _now(self):
        now = stamp(self.clock())
        if not instant("2000-01-01T00:00:00Z") <= instant(now) <= utc_now() + timedelta(seconds=5):
            raise DeliveryBlocked("clock_out_of_bounds")
        return now

    def _validate(self, db):
        if (db.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
                or db.execute("PRAGMA user_version").fetchone()[0] != 1
                or db.execute("PRAGMA journal_mode").fetchone()[0] != "delete"
                or db.execute("PRAGMA page_size").fetchone()[0] != 4096):
            raise Invalid("not a P4 ledger")
        with closing(sqlite3.connect(":memory:")) as expected:
            expected.executescript(SCHEMA)
            if _signature(db) != _signature(expected):
                raise Invalid("ledger schema mismatch")
        if (db.execute("PRAGMA quick_check").fetchall() != [("ok",)]
                or db.execute("PRAGMA foreign_key_check").fetchall()):
            raise Invalid("ledger integrity failure")
        meta = db.execute("SELECT singleton,config,last_at FROM meta").fetchall()
        if len(meta) != 1 or meta[0][:2] != (1, self.config_json):
            raise Invalid("ledger configuration mismatch")
        last = instant(meta[0][2])
        if stamp(last) != meta[0][2]:
            raise Invalid("noncanonical clock")
        reports, chunks, created = {}, {}, {}
        rows = db.execute("SELECT sequence,report_id,record,created_at FROM reports ORDER BY sequence").fetchall()
        if len(rows) > self.config["max_reports"]:
            raise Invalid("report count bound")
        previous_created = None
        for sequence, identity, encoded, when in rows:
            integer(sequence, len(reports) + 1, len(reports) + 1)
            report = _record(encoded)
            verify_report(report, self.config)
            if identity != report["report_id"]:
                raise Invalid("report identity mismatch")
            _check_lineage(report, reports)
            at = instant(when)
            if (stamp(at) != when or not instant(report["content"]["source"]["generated_at"]) <= at <= last
                    or (previous_created is not None and at < previous_created)):
                raise Invalid("invalid ledger creation time")
            previous_created = at
            reports[identity], created[identity] = report, at
            chunks.update({c["chunk_id"]: c for c in report["chunks"]})
        stored_chunks = db.execute("SELECT chunk_id,report_id,ordinal,record FROM chunks").fetchall()
        if len(stored_chunks) != len(chunks):
            raise Invalid("incomplete chunk store")
        for identity, report_id, ordinal, encoded in stored_chunks:
            value = _record(encoded)
            if (identity not in chunks or canonical(value) != canonical(chunks[identity])
                    or report_id != value["report_id"] or type(ordinal) is not int or ordinal != value["ordinal"]):
                raise Invalid("chunk binding mismatch")
        outcomes = {}
        for attempt_id, encoded in db.execute("SELECT attempt_id,record FROM outcomes"):
            value = _record(encoded)
            exact(value, Outcome.__dataclass_fields__)
            integer(value["attempt_id"], attempt_id, attempt_id)
            outcomes[attempt_id] = value
        reservations = []
        counters = defaultdict(lambda: [0, 0])
        next_chunk = defaultdict(lambda: 1)
        previous_finish = None
        blocked = False
        for attempt_id, chunk_id, encoded in db.execute("SELECT attempt_id,chunk_id,record FROM reservations ORDER BY attempt_id"):
            if blocked:
                raise Invalid("dispatch after unresolved or failed send")
            integer(attempt_id, len(reservations) + 1, len(reservations) + 1)
            value = _record(encoded)
            exact(value, Reservation.__dataclass_fields__)
            if chunk_id not in chunks:
                raise Invalid("unknown reserved chunk")
            chunk = chunks[chunk_id]
            report_id = chunk["report_id"]
            at = instant(value["reserved_at"])
            integer(value["attempt_id"], attempt_id, attempt_id)
            integer(value["chars"], len(chunk["text"]), len(chunk["text"]))
            if (value["chunk_id"] != chunk_id or value["report_id"] != report_id
                    or value["run_id"] != run_identity(report_id, self.config["sink"])
                    or value["sink"] != self.config["sink"]
                    or type(value["owner"]) is not str or re.fullmatch(r"[0-9a-f]{32}", value["owner"]) is None
                    or value["day"] != stamp(at)[:10] or value["reserved_at"] != stamp(at)
                    or not created[report_id] <= at <= last
                    or (previous_finish is not None and at < previous_finish)
                    or chunk["ordinal"] != next_chunk[report_id]):
                raise Invalid("invalid reservation binding or order")
            self._quota(value, reservations, counters)
            reservations.append(value)
            next_chunk[report_id] += 1
            outcome = outcomes.get(attempt_id)
            if outcome is None:
                blocked = True
                continue
            finish = instant(outcome["finished_at"])
            if not at <= finish <= last or stamp(finish) != outcome["finished_at"]:
                raise Invalid("invalid settlement clock")
            previous_finish = finish
            if outcome["status"] in ("sent", "failed"):
                status = validate_ack(outcome["acknowledgement"], value)
                if (status != outcome["status"] or outcome["reason"] != "bound_fake_" + status
                        or (finish - at).total_seconds() > self.config["timeout_seconds"]):
                    raise Invalid("invalid settled fake receipt")
            elif (outcome["status"] != "unknown" or outcome["acknowledgement"] is not None
                  or outcome["reason"] not in ("adapter_exception", "timeout", "invalid_ack")):
                raise Invalid("invalid unknown settlement")
            blocked = outcome["status"] != "sent"
        if len(reservations) > self.config["max_attempts"] or set(outcomes) - {r["attempt_id"] for r in reservations}:
            raise Invalid("attempt count or orphan outcome")
        return reports, reservations, outcomes

    def _quota(self, value, prior, counters):
        for scope, key in (("run", value["run_id"]), ("day", value["day"])):
            total = counters[(scope, key)]
            total[0] += 1
            total[1] += value["chars"]
            if total[0] > self.config["attempts_per_" + scope] or total[1] > self.config["chars_per_" + scope]:
                raise DeliveryBlocked("persistent_" + scope + "_budget")
        at = instant(value["reserved_at"])
        recent = sum(0 <= (at - instant(r["reserved_at"])).total_seconds() < self.config["rate_seconds"] for r in prior)
        if recent >= self.config["rate_calls"]:
            raise DeliveryBlocked("persistent_rate_limit")

    @contextmanager
    def transaction(self):
        self._size()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            state = self._validate(self.db)
            now = self._now()
            if instant(now) < instant(self.db.execute("SELECT last_at FROM meta").fetchone()[0]):
                raise DeliveryBlocked("clock_rollback")
            self._capacity(32768)
            yield now, state
            self.db.execute("UPDATE meta SET last_at=?", (now,))
            self.db.execute("COMMIT")
        except BaseException:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise

    def _capacity(self, extra):
        pages = self.db.execute("PRAGMA page_count").fetchone()[0]
        if pages * 4096 + extra > self.config["max_store_bytes"] // 2:
            raise DeliveryBlocked("storage_limit")

    def append(self, report):
        encoded = canonical(report)
        report = _record(encoded)
        verify_report(report, self.config)
        with self.transaction() as (now, state):
            reports = state[0]
            identity = report["report_id"]
            if identity in reports:
                if canonical(reports[identity]) != encoded:
                    raise Invalid("conflicting report identity")
                return identity
            if len(reports) >= self.config["max_reports"]:
                raise DeliveryBlocked("report_count_limit")
            if instant(now) < instant(report["content"]["source"]["generated_at"]):
                raise Invalid("report created before source execution")
            _check_lineage(report, reports)
            self._capacity(len(encoded) * 2 + sum(len(canonical(c)) * 2 for c in report["chunks"]) + 65536)
            self.db.execute("INSERT INTO reports VALUES (?,?,?,?)", (len(reports) + 1, identity, encoded, now))
            self.db.executemany("INSERT INTO chunks VALUES (?,?,?,?)",
                                [(c["chunk_id"], identity, c["ordinal"], canonical(c)) for c in report["chunks"]])
        return identity

    def reserve(self, report_id):
        fingerprint(report_id)
        with self.transaction() as (now, state):
            reports, prior, outcomes = state
            if report_id not in reports:
                raise Invalid("report must be recorded before dispatch")
            if any(r["attempt_id"] not in outcomes or outcomes[r["attempt_id"]]["status"] != "sent" for r in prior):
                raise DeliveryBlocked("store_has_unknown_pending_or_failed_send")
            done = {r["chunk_id"] for r in prior}
            chunk = next((c for c in reports[report_id]["chunks"] if c["chunk_id"] not in done), None)
            if chunk is None:
                return None
            if len(prior) >= self.config["max_attempts"]:
                raise DeliveryBlocked("attempt_storage_limit")
            reservation = asdict(Reservation(len(prior) + 1, chunk["chunk_id"], report_id,
                                run_identity(report_id, self.config["sink"]), self.owner,
                                self.config["sink"], now, now[:10], len(chunk["text"])))
            counters = defaultdict(lambda: [0, 0])
            for row in prior:
                for scope, key in (("run", row["run_id"]), ("day", row["day"])):
                    counters[(scope, key)][0] += 1
                    counters[(scope, key)][1] += row["chars"]
            self._quota(reservation, prior, counters)
            self.db.execute("INSERT INTO reservations VALUES (?,?,?)",
                            (reservation["attempt_id"], chunk["chunk_id"], canonical(reservation)))
            return {"reservation": reservation, "chunk": chunk}

    def settle(self, request, ack=None, reason="invalid_ack"):
        exact(request, {"reservation", "chunk"})
        value = request["reservation"]
        exact(value, Reservation.__dataclass_fields__)
        integer(value["attempt_id"], 1, self.config["max_attempts"])
        if reason not in ("invalid_ack", "adapter_exception", "timeout"):
            raise Invalid("invalid settlement reason")
        with self.transaction() as (now, state):
            reports, prior, outcomes = state
            found = next((r for r in prior if r["attempt_id"] == value["attempt_id"]), None)
            if found is None or canonical(found) != canonical(value) or value["owner"] != self.owner or value["attempt_id"] in outcomes:
                raise Invalid("foreign or settled reservation")
            expected = next(c for c in reports[value["report_id"]]["chunks"] if c["chunk_id"] == value["chunk_id"])
            if canonical(request["chunk"]) != canonical(expected):
                raise Invalid("changed reserved chunk")
            status, acknowledgement = "unknown", None
            if reason == "invalid_ack":
                try:
                    status = validate_ack(ack, value)
                    acknowledgement = _record(canonical(ack))
                    reason = "bound_fake_" + status
                except (Invalid, TypeError, ValueError):
                    status = "unknown"
            if (instant(now) - instant(value["reserved_at"])).total_seconds() > self.config["timeout_seconds"]:
                status, acknowledgement, reason = "unknown", None, "timeout"
            outcome = asdict(Outcome(value["attempt_id"], status, reason, now, acknowledgement))
            self.db.execute("INSERT INTO outcomes VALUES (?,?)", (value["attempt_id"], canonical(outcome)))
            return status

    def snapshot(self, report_id, refusal=None):
        fingerprint(report_id)
        self._size()
        self.db.execute("BEGIN")
        try:
            reports, prior, outcomes = self._validate(self.db)
            if report_id not in reports:
                raise Invalid("unknown report")
            attempts = {r["chunk_id"]: r for r in prior}
            rows = []
            for chunk in reports[report_id]["chunks"]:
                reservation = attempts.get(chunk["chunk_id"])
                outcome = outcomes.get(reservation["attempt_id"]) if reservation else None
                rows.append({"chunk_id": chunk["chunk_id"], "ordinal": chunk["ordinal"], "chars": len(chunk["text"]),
                             "status": outcome["status"] if outcome else ("unknown" if reservation else "queued"),
                             "reservation": reservation, "outcome": outcome})
            if refusal is None and any(r["attempt_id"] not in outcomes or outcomes[r["attempt_id"]]["status"] != "sent" for r in prior):
                refusal = "store_has_unknown_pending_or_failed_send"
            status, reason = report_status(rows, refusal)
            return {"label": "SIMULATED DELIVERY ONLY", "report_id": report_id,
                    "run_id": run_identity(report_id, self.config["sink"]), "sink": self.config["sink"],
                    "status": status, "reason": reason, "chunks": rows}
        finally:
            self.db.execute("ROLLBACK")

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
