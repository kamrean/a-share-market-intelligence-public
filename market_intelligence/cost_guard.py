"""Separate, conservative simulated-model accounting. No provider integration."""

from contextlib import closing, contextmanager
from datetime import timedelta
import os
from pathlib import Path
import sqlite3
import uuid

from .models import Invalid, canonical, digest, exact, instant, integer, stamp, text, utc_now
from .store import external_path

APPLICATION_ID = 0x50334231
SCHEMA = """
CREATE TABLE meta (singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    config TEXT NOT NULL, last_at TEXT NOT NULL);
CREATE TABLE runs (run_id TEXT PRIMARY KEY, task_key TEXT UNIQUE NOT NULL,
    intent TEXT NOT NULL, owner TEXT NOT NULL, status TEXT NOT NULL,
    started_at TEXT NOT NULL, finished_at TEXT, reason TEXT);
CREATE TABLE attempts (attempt_id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id),
    role TEXT NOT NULL, round INTEGER NOT NULL, day TEXT NOT NULL, reserved_at TEXT NOT NULL,
    input_reserved INTEGER NOT NULL, output_reserved INTEGER NOT NULL, status TEXT NOT NULL,
    input_used INTEGER, output_used INTEGER, reason TEXT NOT NULL);
PRAGMA application_id=1345536561;
PRAGMA user_version=1;
"""


def validate_config(value):
    exact(value, {"schema_version", "version", "adapter", "max_rounds", "max_claims",
                  "max_context_bytes", "max_response_bytes", "timeout_seconds", "roles", "budget"})
    integer(value["schema_version"], 1, 1)
    text(value["version"], "config version", 80)
    if value["adapter"] not in ("synthetic_fixture", "live_unavailable"):
        raise Invalid("unsupported adapter")
    for key, low, high in (("max_rounds", 1, 2), ("max_claims", 1, 24),
                           ("max_context_bytes", 1024, 65536), ("max_response_bytes", 512, 16384),
                           ("timeout_seconds", 1, 30)):
        integer(value[key], low, high)
    exact(value["roles"], {"researcher", "reviewer", "editor"})
    for role in value["roles"].values():
        exact(role, {"model", "prompt"})
        for key in role:
            text(role[key], key, 80)
    b = value["budget"]
    exact(b, {"calls_per_run", "calls_per_day", "input_per_call", "output_per_call",
              "input_per_run", "output_per_run", "input_per_day", "output_per_day",
              "max_runs", "max_store_bytes"})
    for key in b:
        integer(b[key], 1, 8 * 1024 * 1024)
    integer(b["calls_per_run"], 1, 5)
    integer(b["calls_per_day"], 1, 100)
    integer(b["max_runs"], 1, 512)
    integer(b["max_store_bytes"], 131072, 8 * 1024 * 1024)
    if (b["input_per_call"] < value["max_context_bytes"]
            or b["output_per_call"] < value["max_response_bytes"]):
        raise Invalid("reservation must conservatively cover bytes")
    return value


class BudgetDenied(Invalid):
    pass


def _signature(db):
    return [tuple(r) for r in db.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name")]


def _validate_database(db, config):
    # Read-only checks precede any persistent pragma, DDL, or accounting write.
    if (db.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
            or db.execute("PRAGMA user_version").fetchone()[0] != 1
            or db.execute("PRAGMA journal_mode").fetchone()[0] != "delete"
            or db.execute("PRAGMA page_size").fetchone()[0] != 4096):
        raise Invalid("not a P3 budget database")
    with closing(sqlite3.connect(":memory:")) as expected:
        expected.executescript(SCHEMA)
        if _signature(db) != _signature(expected):
            raise Invalid("budget schema mismatch")
    rows = db.execute("SELECT singleton,config,last_at FROM meta").fetchall()
    if len(rows) != 1 or rows[0][0] != 1 or rows[0][1] != config:
        raise Invalid("pinned budget configuration mismatch")
    instant(rows[0][2])
    if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
        raise Invalid("budget integrity failure")


class BudgetStore:
    """One canonical store, one active owner, no implicit crash recovery/refunds."""

    def __init__(self, path, config, clock=utc_now):
        self.config_json = canonical(validate_config(config))
        self.config_id = digest(config)
        self.limits = dict(config["budget"])
        self.clock = clock
        self.owner = uuid.uuid4().hex
        self.path = external_path(path)
        self._size()
        now = self._now()
        exists = self.path.exists()
        try:
            if exists:
                with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as db:
                    _validate_database(db, self.config_json)
            else:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                # Exclusive creation prevents two initializers overwriting each other.
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(fd)
            self.db = sqlite3.connect(self.path, timeout=2, isolation_level=None)
            if not exists:
                self.db.executescript("BEGIN IMMEDIATE;\n" + SCHEMA)
                self.db.execute("INSERT INTO meta VALUES (1,?,?)", (self.config_json, now))
                self.db.execute("COMMIT")
            _validate_database(self.db, self.config_json)
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("PRAGMA max_page_count=" + str(self.limits["max_store_bytes"] // 8192))
        except (sqlite3.Error, Invalid, OSError) as exc:
            if hasattr(self, "db"):
                self.db.close()
            raise Invalid("budget store unavailable or incompatible") from exc

    def _size(self):
        external_path(self.path)
        total = sum(p.stat().st_size for p in
                    (self.path, *(Path(str(self.path) + s) for s in ("-journal", "-wal", "-shm")))
                    if p.exists())
        if total > self.limits["max_store_bytes"]:
            raise BudgetDenied("storage_limit")

    def _now(self):
        value = stamp(self.clock())
        if instant(value) < instant("2000-01-01T00:00:00Z") or instant(value) > utc_now() + timedelta(seconds=5):
            raise BudgetDenied("clock_out_of_bounds")
        return value

    @contextmanager
    def transaction(self):
        self._size()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            _validate_database(self.db, self.config_json)
            now = self._now()
            if instant(now) < instant(self.db.execute("SELECT last_at FROM meta").fetchone()[0]):
                raise BudgetDenied("clock_rollback")
            pages = self.db.execute("PRAGMA page_count").fetchone()[0]
            if pages * 4096 + 32768 > self.limits["max_store_bytes"] // 2:
                raise BudgetDenied("storage_limit")
            yield now
            self.db.execute("UPDATE meta SET last_at=?", (now,))
            self.db.execute("COMMIT")
        except BaseException:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def begin(self, task_key, intent):
        text(task_key, "task key", 160)
        text(intent, "intent", 128)
        with self.transaction() as now:
            prior = self.db.execute("SELECT * FROM runs WHERE task_key=?", (task_key,)).fetchone()
            if prior:
                if prior["intent"] != intent:
                    raise BudgetDenied("task_identity_conflict")
                raise BudgetDenied("task_already_recorded")
            if self.db.execute("SELECT 1 FROM attempts WHERE status IN ('pending','unknown')").fetchone():
                raise BudgetDenied("unknown_or_pending_usage")
            if self.db.execute("SELECT 1 FROM runs WHERE status='active'").fetchone():
                raise BudgetDenied("active_or_interrupted_run")
            if self.db.execute("SELECT count(*) FROM runs").fetchone()[0] >= self.limits["max_runs"]:
                raise BudgetDenied("run_storage_limit")
            identity = uuid.uuid4().hex
            self.db.execute("INSERT INTO runs VALUES (?,?,?,?,'active',?,NULL,NULL)",
                            (identity, task_key, intent, self.owner, now))
            return identity

    def _owned(self, run_id):
        row = self.db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None or row["owner"] != self.owner or row["status"] != "active":
            raise BudgetDenied("inactive_or_foreign_run")

    def reserve(self, run_id, role, round_number):
        if role not in ("researcher", "reviewer", "editor"):
            raise Invalid("invalid budget role")
        integer(round_number, 1, 2)
        with self.transaction() as now:
            self._owned(run_id)
            if self.db.execute("SELECT 1 FROM attempts WHERE status IN ('pending','unknown')").fetchone():
                raise BudgetDenied("unknown_or_pending_usage")
            day = now[:10]
            for scope, column, value in (("run", "run_id", run_id), ("day", "day", day)):
                counts = self.db.execute(
                    "SELECT count(*),coalesce(sum(input_reserved),0),coalesce(sum(output_reserved),0) "
                    "FROM attempts WHERE " + column + "=?", (value,)).fetchone()
                additions = (1, self.limits["input_per_call"], self.limits["output_per_call"])
                for kind, count, extra in zip(("calls", "input", "output"), counts, additions):
                    if count + extra > self.limits[kind + "_per_" + scope]:
                        raise BudgetDenied(kind + "_" + scope + "_budget_exhausted")
            cursor = self.db.execute(
                "INSERT INTO attempts VALUES (NULL,?,?,?,?,?,?,?,'pending',NULL,NULL,'reserved_before_dispatch')",
                (run_id, role, round_number, day, now, self.limits["input_per_call"], self.limits["output_per_call"]))
            return cursor.lastrowid

    def settle(self, run_id, attempt_id, usage, outcome="success"):
        if outcome not in ("success", "failed", "timeout", "exception"):
            raise Invalid("invalid attempt outcome")
        with self.transaction():
            self._owned(run_id)
            row = self.db.execute("SELECT * FROM attempts WHERE attempt_id=? AND run_id=?",
                                  (attempt_id, run_id)).fetchone()
            if row is None or row["status"] != "pending":
                raise Invalid("foreign or settled attempt")
            known = False
            try:
                exact(usage, {"input_tokens", "output_tokens"})
                integer(usage["input_tokens"], 0, row["input_reserved"])
                integer(usage["output_tokens"], 0, row["output_reserved"])
                known = outcome in ("success", "failed")
            except Invalid:
                pass
            self.db.execute("UPDATE attempts SET status=?,input_used=?,output_used=?,reason=? WHERE attempt_id=?",
                            (outcome if known else "unknown", usage["input_tokens"] if known else None,
                             usage["output_tokens"] if known else None,
                             outcome if known else outcome + "_usage_unconfirmed", attempt_id))
            # Reservations are deliberately never reduced, even with known simulated usage.
            return known

    def finish(self, run_id, status, reason):
        if status not in ("complete", "blocked"):
            raise Invalid("invalid run status")
        text(reason, "run reason", 100)
        with self.transaction() as now:
            self._owned(run_id)
            if self.db.execute("SELECT 1 FROM attempts WHERE run_id=? AND status='pending'", (run_id,)).fetchone():
                raise BudgetDenied("pending_dispatch")
            if status == "complete" and self.db.execute(
                    "SELECT 1 FROM attempts WHERE run_id=? AND status!='success'", (run_id,)).fetchone():
                raise BudgetDenied("unconfirmed_dispatch")
            self.db.execute("UPDATE runs SET status=?,finished_at=?,reason=? WHERE run_id=?",
                            (status, now, reason, run_id))

    def snapshot(self, run_id):
        self._size()
        row = self.db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise Invalid("unknown run")
        attempts = [dict(r) for r in self.db.execute(
            "SELECT * FROM attempts WHERE run_id=? ORDER BY attempt_id", (run_id,))]
        return {"label": "SIMULATED TOKEN USAGE; CONSERVATIVE RESERVATIONS; NO DOLLAR BILLING",
                "scope": "single_P3_store_only", "config_id": self.config_id,
                "status": row["status"], "started_at": row["started_at"], "finished_at": row["finished_at"],
                "calls": len(attempts), "input_reserved": sum(r["input_reserved"] for r in attempts),
                "output_reserved": sum(r["output_reserved"] for r in attempts),
                "input_used_simulated": None if any(r["input_used"] is None for r in attempts)
                else sum(r["input_used"] for r in attempts),
                "output_used_simulated": None if any(r["output_used"] is None for r in attempts)
                else sum(r["output_used"] for r in attempts), "attempts": attempts}
