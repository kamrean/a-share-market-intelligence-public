"""Transactional local evidence, conservative reservations and fenced leases."""

from contextlib import closing, contextmanager
from dataclasses import dataclass, replace
from datetime import timedelta
import json
from pathlib import Path
import sqlite3
import uuid

from .models import (Capture, Evidence, Invalid, MODES, canonical, digest, exact,
                     fingerprint, instant, integer, stamp, text, utc_now, validate_summary)
from .sources import MAX_BYTES, parse

CHECKOUT = Path(__file__).resolve().parents[1]
STORE_LIMIT = 10 * 1024 * 1024
DATABASE_LIMIT = 1200 * 4096  # Rollback journal plus DB remain below 10 MiB.
LEASE_SECONDS = 30
APPLICATION_ID = 0x50314531
SCHEMA = """
CREATE TABLE runs (
    run_id TEXT PRIMARY KEY, task_key TEXT UNIQUE NOT NULL,
    mode TEXT NOT NULL, intent TEXT NOT NULL, status TEXT NOT NULL, started_at TEXT NOT NULL,
    finished_at TEXT, summary TEXT);
CREATE TABLE lease (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1), run_id TEXT,
    owner TEXT, generation INTEGER NOT NULL, expires_at TEXT);
INSERT INTO lease VALUES (1,NULL,NULL,0,NULL);
CREATE TABLE attempts (
    attempt_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id),
    reserved_at TEXT NOT NULL, day TEXT NOT NULL, outcome TEXT NOT NULL,
    reserved_bytes INTEGER NOT NULL, observed_bytes INTEGER, reason TEXT);
CREATE TABLE events (
    event_id INTEGER PRIMARY KEY, run_id TEXT NOT NULL, at TEXT NOT NULL,
    kind TEXT NOT NULL, detail TEXT NOT NULL);
CREATE TABLE captures (
    capture_id TEXT PRIMARY KEY, source_id TEXT NOT NULL,
    content_hash TEXT NOT NULL, metadata TEXT NOT NULL, payload BLOB);
CREATE TABLE evidence (
    evidence_id TEXT PRIMARY KEY, capture_id TEXT NOT NULL REFERENCES captures(capture_id),
    record TEXT NOT NULL);
CREATE TABLE tombstones (
    capture_id TEXT PRIMARY KEY REFERENCES captures(capture_id), at TEXT NOT NULL,
    reason TEXT NOT NULL, content_hash TEXT NOT NULL, parser_version TEXT NOT NULL);
CREATE TABLE bundles (bundle_id TEXT PRIMARY KEY, record TEXT NOT NULL);
PRAGMA user_version=1;
PRAGMA application_id=1345406257;
"""


def _schema_signature(connection):
    return connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall()


def _existing_store(path):
    if not path.exists() or path.stat().st_size == 0:
        return False
    sidecars = [Path(str(path) + suffix) for suffix in ("-journal", "-wal", "-shm")]
    if sum(p.stat().st_size for p in [path, *sidecars] if p.exists()) > STORE_LIMIT:
        raise Invalid("store size limit")
    try:
        # Inspect without any PRAGMA that changes the user's database.
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as existing:
            if (existing.execute("PRAGMA user_version").fetchone()[0] != 1
                    or existing.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
                    or existing.execute("PRAGMA journal_mode").fetchone()[0] != "delete"):
                raise Invalid("unrelated or unsupported existing database")
            with closing(sqlite3.connect(":memory:")) as reference:
                reference.executescript(SCHEMA)
                if _schema_signature(existing) != _schema_signature(reference):
                    raise Invalid("existing store schema mismatch")
            if existing.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise Invalid("existing store integrity failure")
    except sqlite3.Error as exc:
        raise Invalid("existing database cannot be safely opened") from exc
    return True


class Busy(Invalid):
    pass


class BudgetDenied(Invalid):
    pass


def external_path(path):
    path = Path(path)
    if not path.is_absolute():
        raise Invalid("absolute external store path required")
    for part in (path, *path.parents):
        if part.is_symlink() or part.is_junction():
            raise Invalid("linked store paths forbidden")
    for suffix in ("-journal", "-wal", "-shm"):
        sidecar = Path(str(path) + suffix)
        if sidecar.is_symlink() or sidecar.is_junction() or (sidecar.exists() and sidecar.stat().st_nlink > 1):
            raise Invalid("linked SQLite sidecar forbidden")
    resolved = path.resolve()
    if resolved == CHECKOUT or CHECKOUT in resolved.parents:
        raise Invalid("runtime stores must be outside checkout")
    if any((parent / ".git").exists() for parent in resolved.parents):
        raise Invalid("runtime stores must be outside every Git checkout")
    if path.exists() and not path.is_file():
        raise Invalid("store must be a regular file")
    if path.exists() and path.stat().st_nlink > 1:
        raise Invalid("hard-linked store forbidden")
    return resolved


@dataclass(frozen=True)
class Lease:
    run_id: str
    owner: str
    generation: int


class Store:
    def __init__(self, path):
        self.path = external_path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and self.path.stat().st_size > DATABASE_LIMIT:
            raise Invalid("store size limit")
        exists = _existing_store(self.path)
        self.db = sqlite3.connect(self.path, timeout=2, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=DELETE")
        self.db.execute("PRAGMA secure_delete=ON")
        self.db.execute("PRAGMA synchronous=FULL")
        if self.db.execute("PRAGMA page_size").fetchone()[0] != 4096:
            self.close()
            raise Invalid("unsupported SQLite page size")
        self.db.execute("PRAGMA max_page_count=1200")
        if not exists:
            self.db.executescript(SCHEMA)

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise

    def _capacity(self, extra=0):
        total = sum(p.stat().st_size for p in (self.path, Path(str(self.path) + "-journal")) if p.exists())
        pages = self.db.execute("PRAGMA page_count").fetchone()[0]
        free = self.db.execute("PRAGMA freelist_count").fetchone()[0]
        if total > STORE_LIMIT or (pages - free) * 4096 + extra > DATABASE_LIMIT:
            raise BudgetDenied("storage_limit")

    def _event(self, run_id, now, kind, detail):
        self.db.execute("INSERT INTO events(run_id,at,kind,detail) VALUES (?,?,?,?)",
                        (run_id, stamp(now), kind, canonical(detail)))

    def _owned(self, lease, now):
        row = self.db.execute("SELECT * FROM lease WHERE singleton=1").fetchone()
        if (row["run_id"] != lease.run_id or row["owner"] != lease.owner
                or row["generation"] != lease.generation or not row["expires_at"]
                or instant(row["expires_at"]) <= now):
            raise Busy("stale_or_foreign_lease")
        return row

    def begin(self, task_key, mode, now=None, intent="test-task-v1"):
        now = now or utc_now()
        text(task_key, "task key", 160)
        text(intent, "task intent", 256)
        if mode not in MODES:
            raise Invalid("invalid run mode")
        with self.transaction():
            self._capacity(65536)
            prior = self.db.execute("SELECT * FROM runs WHERE task_key=?", (task_key,)).fetchone()
            if prior and (prior["mode"] != mode or prior["intent"] != intent):
                raise Invalid("idempotency intent conflict")
            if prior and prior["status"] == "complete":
                return None, self.run(prior["run_id"])
            current = self.db.execute("SELECT * FROM lease WHERE singleton=1").fetchone()
            if current["owner"] and instant(current["expires_at"]) > now:
                raise Busy("global_concurrency_one")
            if current["owner"]:
                old = current["run_id"]
                self.db.execute("UPDATE runs SET status='interrupted' WHERE run_id=? AND status!='complete'", (old,))
                self.db.execute("UPDATE attempts SET outcome='unknown',reason='lease_expired' WHERE run_id=? AND outcome='reserved'", (old,))
                self._event(old, now, "interrupted", {"reason": "lease_expired", "generation": current["generation"]})
            run_id = prior["run_id"] if prior else uuid.uuid4().hex
            if not prior:
                self.db.execute("INSERT INTO runs VALUES (?,?,?,?,'running',?,NULL,NULL)",
                                (run_id, task_key, mode, intent, stamp(now)))
            else:
                self.db.execute("UPDATE runs SET status='running' WHERE run_id=?", (run_id,))
            lease = Lease(run_id, uuid.uuid4().hex, current["generation"] + 1)
            self.db.execute("UPDATE lease SET run_id=?,owner=?,generation=?,expires_at=? WHERE singleton=1",
                            (run_id, lease.owner, lease.generation, stamp(now + timedelta(seconds=LEASE_SECONDS))))
            self._event(run_id, now, "started" if not prior else "resumed", {"generation": lease.generation})
            return lease, self.run(run_id)

    def renew(self, lease, now=None):
        now = now or utc_now()
        with self.transaction():
            self._owned(lease, now)
            self.db.execute("UPDATE lease SET expires_at=? WHERE singleton=1 AND owner=? AND generation=?",
                            (stamp(now + timedelta(seconds=LEASE_SECONDS)), lease.owner, lease.generation))

    def release(self, lease, now=None):
        now = now or utc_now()
        with self.transaction():
            self._owned(lease, now)
            self.db.execute("UPDATE attempts SET outcome='unknown',reason='released_pending' WHERE run_id=? AND outcome='reserved'", (lease.run_id,))
            self.db.execute("UPDATE runs SET status='interrupted' WHERE run_id=? AND status!='complete'", (lease.run_id,))
            self._event(lease.run_id, now, "released", {"generation": lease.generation})
            self.db.execute("UPDATE lease SET owner=NULL,expires_at=NULL,run_id=NULL WHERE singleton=1 AND owner=? AND generation=?",
                            (lease.owner, lease.generation))

    def reserve(self, lease, now=None):
        now = now or utc_now()
        with self.transaction():
            self._owned(lease, now)
            self._capacity(3 * MAX_BYTES + 262144)
            if self.db.execute("SELECT 1 FROM attempts WHERE outcome IN ('unknown','reserved') LIMIT 1").fetchone():
                raise BudgetDenied("unknown_usage_halts_dispatch")
            per_run = self.db.execute("SELECT count(*) FROM attempts WHERE run_id=?", (lease.run_id,)).fetchone()[0]
            day = stamp(now)[:10]
            per_day = self.db.execute("SELECT count(*) FROM attempts WHERE day=?", (day,)).fetchone()[0]
            if per_run >= 2 or per_day >= 12:
                raise BudgetDenied("request_budget_exhausted")
            attempt_id = uuid.uuid4().hex
            self.db.execute("INSERT INTO attempts VALUES (?,?,?,?,'reserved',?,NULL,NULL)",
                            (attempt_id, lease.run_id, stamp(now), day, MAX_BYTES))
            self._event(lease.run_id, now, "reserved", {"attempt_id": attempt_id, "bytes": MAX_BYTES})
            return attempt_id

    def settle(self, lease, attempt_id, outcome, observed_bytes, reason, now=None):
        now = now or utc_now()
        if outcome not in {"success", "failed", "unknown"}:
            raise Invalid("unknown attempt outcome")
        if observed_bytes is not None:
            integer(observed_bytes, 0, MAX_BYTES)
        if outcome == "unknown" and observed_bytes is not None:
            raise Invalid("unknown outcome must retain unknown usage")
        text(reason, "attempt reason", 100)
        with self.transaction():
            self._owned(lease, now)
            count = self.db.execute("UPDATE attempts SET outcome=?,observed_bytes=?,reason=? WHERE attempt_id=? AND run_id=? AND outcome='reserved'",
                                    (outcome, observed_bytes, reason, attempt_id, lease.run_id)).rowcount
            if count != 1:
                raise Invalid("attempt already settled or foreign")
            self._event(lease.run_id, now, "settled", {"attempt_id": attempt_id, "outcome": outcome})

    def attempts(self, run_id):
        rows = [dict(r) for r in self.db.execute("SELECT * FROM attempts WHERE run_id=? ORDER BY reserved_at,attempt_id", (run_id,))]
        for row in rows:
            if row["outcome"] not in {"reserved", "success", "failed", "unknown"} or row["reserved_bytes"] != MAX_BYTES:
                raise Invalid("invalid stored attempt")
            instant(row["reserved_at"])
            if row["observed_bytes"] is not None:
                integer(row["observed_bytes"], 0, MAX_BYTES)
        return rows

    def run(self, run_id):
        row = self.db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise Invalid("unknown run")
        result = dict(row)
        if result["mode"] not in MODES or result["status"] not in {"running", "interrupted", "complete"}:
            raise Invalid("invalid stored run")
        instant(result["started_at"])
        if result["finished_at"]:
            instant(result["finished_at"])
        result["summary"] = validate_summary(json.loads(result["summary"])) if result["summary"] else None
        if result["summary"]:
            receipts = self.db.execute("SELECT detail FROM events WHERE run_id=? AND kind='completed'", (run_id,)).fetchall()
            if len(receipts) != 1 or json.loads(receipts[0][0]) != {"summary_hash": digest(result["summary"])}:
                raise Invalid("stored summary integrity failure")
        result["attempts"] = self.attempts(run_id)
        return result

    def finish(self, lease, summary, now=None):
        now = now or utc_now()
        validate_summary(summary)
        if summary["run_id"] != lease.run_id or summary["mode"] != self.run(lease.run_id)["mode"]:
            raise Invalid("summary run binding mismatch")
        encoded = canonical(summary)
        if len(encoded) > 100000:
            raise Invalid("summary too large")
        with self.transaction():
            self._owned(lease, now)
            if self.db.execute("SELECT 1 FROM attempts WHERE run_id=? AND outcome='reserved'", (lease.run_id,)).fetchone():
                raise Invalid("cannot finish pending request")
            self._capacity(len(encoded) + 16384)
            self.db.execute("UPDATE runs SET status='complete',finished_at=?,summary=? WHERE run_id=? AND status='running'",
                            (stamp(now), encoded, lease.run_id))
            self._event(lease.run_id, now, "completed", {"summary_hash": digest(summary)})
            self.db.execute("UPDATE lease SET owner=NULL,expires_at=NULL,run_id=NULL WHERE singleton=1 AND owner=? AND generation=?",
                            (lease.owner, lease.generation))

    @staticmethod
    def _binding(record, capture):
        record.validate()
        if record.capture_id != capture.version_id:
            raise Invalid("capture binding mismatch")
        for name in ("source_id", "retrieved_at", "first_seen_at", "expires_at", "parser_version"):
            if getattr(record, name) != getattr(capture, name):
                raise Invalid("evidence provenance mismatch")
        if record.mode not in {capture.mode, "historical_replay"}:
            raise Invalid("evidence mode promotion")

    def put_capture(self, capture, payload=None, records=(), now=None, *, lease):
        now = now or utc_now()
        capture.validate(now)
        if capture.expires_at and instant(capture.expires_at) <= now:
            raise Invalid("expired input cannot be stored")
        if payload is not None:
            if capture.retention not in {"short_term_raw", "reprocessable_raw"}:
                raise Invalid("retention forbids payload write")
            if type(payload) is not bytes or len(payload) > MAX_BYTES or digest(payload) != capture.content_hash:
                raise Invalid("invalid payload or content hash")
        if capture.retention == "restricted" and records:
            raise Invalid("restricted normalized payload forbidden")
        for record in records:
            self._binding(record, capture)
        encoded = canonical(capture.to_dict())
        encoded_records = [(r.evidence_id, canonical(r.to_dict())) for r in records]
        if sum(len(value) for _, value in encoded_records) > 2 * MAX_BYTES:
            raise Invalid("normalized_record_size_limit")
        with self.transaction():
            self._owned(lease, now)
            self._capacity(len(payload or b"") + len(encoded) + sum(len(r[1]) for r in encoded_records) + 32768)
            prior = self.db.execute("SELECT metadata FROM captures WHERE source_id=? AND content_hash=?",
                                    (capture.source_id, capture.content_hash)).fetchall()
            earliest = min((instant(Capture.from_dict(json.loads(r[0])).first_seen_at) for r in prior), default=instant(capture.retrieved_at))
            if instant(capture.first_seen_at) != earliest:
                raise Invalid("first_seen must preserve actual earliest capture")
            row = self.db.execute("SELECT metadata,payload FROM captures WHERE capture_id=?", (capture.version_id,)).fetchone()
            if row:
                if row["metadata"] != encoded:
                    raise Invalid("capture immutable")
                if self.db.execute("SELECT 1 FROM tombstones WHERE capture_id=?", (capture.version_id,)).fetchone():
                    raise Invalid("cannot resurrect tombstoned capture")
                if payload is not None and row["payload"] != payload:
                    raise Invalid("cannot mutate stored payload")
            else:
                self.db.execute("INSERT INTO captures VALUES (?,?,?,?,?)", (capture.version_id, capture.source_id, capture.content_hash, encoded, payload))
            for identity, value in encoded_records:
                self.db.execute("INSERT OR IGNORE INTO evidence VALUES (?,?,?)", (identity, capture.version_id, value))
        return capture.version_id

    def first_seen(self, source_id, content_hash, fallback):
        rows = self.db.execute("SELECT metadata FROM captures WHERE source_id=? AND content_hash=?", (source_id, content_hash)).fetchall()
        return min((Capture.from_dict(json.loads(r[0])).first_seen_at for r in rows), key=instant, default=fallback)

    def capture(self, capture_id):
        fingerprint(capture_id)
        row = self.db.execute("SELECT * FROM captures WHERE capture_id=?", (capture_id,)).fetchone()
        if row is None:
            raise Invalid("unknown capture")
        capture = Capture.from_dict(json.loads(row["metadata"]))
        if capture.version_id != capture_id or capture.source_id != row["source_id"] or capture.content_hash != row["content_hash"]:
            raise Invalid("stored capture hash mismatch")
        return capture

    def capture_for_attempt(self, attempt_id):
        for row in self.db.execute("SELECT capture_id FROM captures"):
            capture = self.capture(row[0])
            if capture.request_id == attempt_id:
                return capture
        return None

    def retained_parse(self, capture_id, now=None):
        capture = self.capture(capture_id)
        now = now or utc_now()
        if capture.retention not in {"short_term_raw", "reprocessable_raw"} or (capture.expires_at and instant(capture.expires_at) <= now):
            raise Invalid("raw unavailable")
        if self.db.execute("SELECT 1 FROM tombstones WHERE capture_id=?", (capture_id,)).fetchone():
            raise Invalid("raw tombstoned")
        body = self.db.execute("SELECT payload FROM captures WHERE capture_id=?", (capture_id,)).fetchone()[0]
        if body is None or digest(body) != capture.content_hash:
            raise Invalid("raw hash mismatch or unavailable")
        return parse(body, capture)

    def records(self, ids=None, now=None):
        now = now or utc_now()
        if ids is None:
            ids = [r[0] for r in self.db.execute("SELECT evidence_id FROM evidence ORDER BY evidence_id")]
        result, unavailable = [], []
        for identity in ids:
            fingerprint(identity)
            row = self.db.execute("SELECT * FROM evidence WHERE evidence_id=?", (identity,)).fetchone()
            if row is None:
                raise Invalid("unknown evidence reference")
            capture = self.capture(row["capture_id"])
            if capture.retention == "restricted" or (capture.expires_at and instant(capture.expires_at) <= now) or self.db.execute("SELECT 1 FROM tombstones WHERE capture_id=?", (capture.version_id,)).fetchone():
                unavailable.append({"evidence_id": identity, "reason": "expired_or_restricted"})
                continue
            record = Evidence.from_dict(json.loads(row["record"]))
            self._binding(record, capture)
            if record.evidence_id != identity:
                raise Invalid("stored evidence hash mismatch")
            result.append(record)
        return result, unavailable

    def replay(self, capture_id, parser_version, now=None, *, lease):
        now = now or utc_now()
        capture = self.capture(capture_id)
        if parser_version != capture.parser_version:
            raise Invalid("replay parser version mismatch")
        if capture.retention not in {"short_term_raw", "reprocessable_raw"}:
            raise Invalid("metadata_auditable_only")
        if capture.expires_at and instant(capture.expires_at) <= now:
            raise Invalid("expired_unavailable")
        if self.db.execute("SELECT 1 FROM tombstones WHERE capture_id=?", (capture_id,)).fetchone():
            raise Invalid("tombstoned_unavailable")
        payload = self.db.execute("SELECT payload FROM captures WHERE capture_id=?", (capture_id,)).fetchone()[0]
        if payload is None or digest(payload) != capture.content_hash:
            raise Invalid("replay payload unavailable or hash mismatch")
        parsed = parse(payload, capture)
        replayed = tuple(replace(r, mode="historical_replay") for r in parsed.records)
        self.put_capture(capture, records=replayed, now=now, lease=lease)
        return replace(parsed, records=replayed)

    def sweep(self, now=None):
        now = now or utc_now()
        removed = []
        with self.transaction():
            active = self.db.execute("SELECT owner FROM lease WHERE singleton=1").fetchone()[0]
            if active:
                raise Busy("sweep_requires_no_active_or_unrecovered_lease")
            for row in self.db.execute("SELECT capture_id FROM captures").fetchall():
                capture = self.capture(row[0])
                reason = "restricted" if capture.retention == "restricted" else (
                    "expired" if capture.expires_at and instant(capture.expires_at) <= now else None)
                if not reason or self.db.execute("SELECT 1 FROM tombstones WHERE capture_id=?", (row[0],)).fetchone():
                    continue
                self.db.execute("INSERT INTO tombstones VALUES (?,?,?,?,?)", (row[0], stamp(now), reason, capture.content_hash, capture.parser_version))
                self.db.execute("UPDATE captures SET payload=NULL WHERE capture_id=?", (row[0],))
                self.db.execute("DELETE FROM evidence WHERE capture_id=?", (row[0],))
                removed.append(row[0])
        return removed

    def freeze(self, result, now=None, *, lease):
        from .gate import Calendar, Profile, evaluate, validate_result
        now = now or utc_now()
        validate_result(result)
        input_ids = set(result["accepted_ids"])
        if result["status"] != "BLOCK":
            # Rejections explain DEGRADED but never become usable bundle references.
            input_ids.update(row["evidence_id"] for row in result["rejected"])
        records, unavailable = self.records(sorted(input_ids), now)
        if unavailable:
            raise Invalid("unavailable evidence cannot freeze")
        profile = Profile.from_dict(result["profile"])
        calendar = Calendar.from_dict(result["calendar"])
        recomputed = evaluate(records, profile, calendar, result["as_of"], now)
        # Reject fabricated PASS, but retain stricter BLOCK results with explicit rejections.
        if result["status"] != "BLOCK" and recomputed != result:
            raise Invalid("gate result does not match durable evidence")
        exact(result, recomputed)
        refs = [{"evidence_id": r.evidence_id, "capture_id": r.capture_id,
                 "source_id": r.source_id, "content_hash": self.capture(r.capture_id).content_hash,
                 "parser_version": r.parser_version} for r in records if r.evidence_id in result["accepted_ids"]]
        bundle = {"schema_version": 1, "gate": result, "references": sorted(refs, key=lambda r: r["evidence_id"])}
        encoded = canonical(bundle)
        identity = digest(bundle)
        with self.transaction():
            self._owned(lease, now)
            self._capacity(len(encoded) + 16384)
            row = self.db.execute("SELECT record FROM bundles WHERE bundle_id=?", (identity,)).fetchone()
            if row and row[0] != encoded:
                raise Invalid("bundle immutable")
            self.db.execute("INSERT OR IGNORE INTO bundles VALUES (?,?)", (identity, encoded))
        return identity

    def bundle(self, identity, now=None):
        from .gate import validate_result
        fingerprint(identity)
        row = self.db.execute("SELECT record FROM bundles WHERE bundle_id=?", (identity,)).fetchone()
        if not row:
            raise Invalid("unknown bundle")
        result = json.loads(row[0])
        exact(result, {"schema_version", "gate", "references"})
        if digest(result) != identity:
            raise Invalid("bundle hash mismatch")
        integer(result["schema_version"], 1, 1)
        validate_result(result["gate"])
        if type(result["references"]) is not list:
            raise Invalid("invalid bundle references")
        available, unavailable = [], []
        for ref in result["references"]:
            exact(ref, {"evidence_id", "capture_id", "source_id", "content_hash", "parser_version"})
            capture = self.capture(ref["capture_id"])
            if (capture.content_hash != ref["content_hash"] or capture.source_id != ref["source_id"]
                    or capture.parser_version != ref["parser_version"]):
                raise Invalid("bundle reference mismatch")
            if self.db.execute("SELECT 1 FROM tombstones WHERE capture_id=?", (ref["capture_id"],)).fetchone():
                unavailable.append({"evidence_id": ref["evidence_id"], "reason": "tombstoned"})
            else:
                records, missing = self.records([ref["evidence_id"]], now)
                available.extend(r.evidence_id for r in records)
                unavailable.extend(missing)
        if sorted(r["evidence_id"] for r in result["references"]) != result["gate"]["accepted_ids"]:
            raise Invalid("bundle references disagree with gate")
        return {"bundle": result, "currently_available_ids": available, "unavailable": unavailable,
                "usable_now": not unavailable and result["gate"]["status"] in {"PASS", "DEGRADED"}}
