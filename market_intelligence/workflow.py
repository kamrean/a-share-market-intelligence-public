"""Configurable offline operations; no source/model/Telegram client or scheduler."""

import argparse
from collections import defaultdict
from contextlib import closing, contextmanager
import os
from pathlib import Path
import re
import sqlite3
import uuid

from .cost_guard import BudgetStore, _validate_database
from .delivery import FakeTransport, deliver
from .intelligence import decode_input
from .ledger import Ledger
from .models import Invalid, canonical, digest, exact, fingerprint, instant, integer, stamp, utc_now
from . import publication, scenario
from .store import external_path

APPLICATION_ID = 0x50344331
MAX_RECORD_BYTES = 512000
MAX_STORE_BYTES = 16 * 1024 * 1024
MAX_TASKS = 32
SCHEMA = """
CREATE TABLE meta (singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    config TEXT NOT NULL, created_at TEXT NOT NULL, last_at TEXT NOT NULL);
CREATE TABLE tasks (task_key TEXT PRIMARY KEY, intent TEXT NOT NULL, case_record TEXT NOT NULL,
    owner TEXT NOT NULL, status TEXT NOT NULL, started_at TEXT NOT NULL,
    finished_at TEXT, source_record TEXT, report_id TEXT, reason TEXT NOT NULL);
PRAGMA application_id=1345602353;
PRAGMA user_version=1;
"""
REASONS = {"reserved_before_execution", "offline_report_recorded", "research_blocked",
           "offline_execution_failed"}


def _encoded(value):
    result = canonical(value)
    if len(result.encode("utf-8")) > MAX_RECORD_BYTES:
        raise Invalid("offline record size bound")
    return result


def _decode(value):
    if type(value) is not str:
        raise Invalid("invalid stored text")
    result = decode_input(value.encode("utf-8"))
    if _encoded(result) != value:
        raise Invalid("noncanonical offline record")
    return result


def _key(value):
    if type(value) is not str or re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value) is None:
        raise Invalid("invalid offline task key")
    return value


def _config(case):
    return {"research_config": case["research_config"],
            "publication_config": case["publication_config"]}


def _signature(db):
    return db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall()


def read_case(path):
    path = external_path(path)
    if not path.is_file():
        raise Invalid("case file required")
    with path.open("rb") as stream:
        body = stream.read(MAX_RECORD_BYTES + 1)
    return scenario.validate_case(decode_input(body))


def _write_new(path, body):
    path = external_path(path)
    if type(body) is not bytes or len(body) > MAX_RECORD_BYTES:
        raise Invalid("export size bound")
    path.parent.mkdir(parents=True, exist_ok=True)
    external_path(path)
    # Exclusive creation never truncates a case, database, report, or existing output.
    with path.open("xb") as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())


def export_example(path):
    case = scenario.validate_case(scenario.example_case())
    _write_new(path, (_encoded(case) + "\n").encode("utf-8"))
    return {"status": "EXPORTED_SYNTHETIC_CASE", "case_id": digest(case)}


class Workspace:
    """One pinned local workspace; interrupted tasks are not implicitly recovered."""

    def __init__(self, directory, case=None, *, read_only=False):
        self.path = external_path(Path(directory) / "workflow.sqlite")
        self.directory = self.path.parent
        self.owner = uuid.uuid4().hex
        self.read_only = read_only
        self.expected_config = _encoded(_config(case)) if case is not None else None
        exists = self.path.exists()
        self._paths()
        if not exists:
            if read_only or case is None:
                raise Invalid("offline workspace not initialized")
            if any((self.directory / name).exists() for name in ("budget.sqlite", "ledger.sqlite")):
                raise Invalid("unmanaged prior runtime stores")
            self.directory.mkdir(parents=True, exist_ok=True)
            external_path(self.path)
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        mode = "ro" if read_only else "rw"
        try:
            self.db = sqlite3.connect(self.path.as_uri() + "?mode=" + mode,
                                      uri=True, isolation_level=None, timeout=2)
            if not exists:
                now = stamp(utc_now())
                self.db.executescript("BEGIN IMMEDIATE;\n" + SCHEMA)
                self.db.execute("INSERT INTO meta VALUES (1,?,?,?)", (self.expected_config, now, now))
                self.db.execute("COMMIT")
            self.config = self._validate()
            self.file_identity = self._identity()
            if not read_only:
                self.db.execute("PRAGMA synchronous=FULL")
                self.db.execute("PRAGMA max_page_count=" + str(MAX_STORE_BYTES // 4096))
        except (Invalid, sqlite3.Error, OSError, ValueError, TypeError):
            if hasattr(self, "db"):
                self.db.close()
            raise Invalid("offline workspace unavailable or incompatible") from None

    def _identity(self):
        stat = self.path.stat()
        return stat.st_dev, stat.st_ino

    def _paths(self):
        for name in ("workflow.sqlite", "budget.sqlite", "ledger.sqlite"):
            external_path(self.directory / name)
        if self.path.exists():
            if hasattr(self, "file_identity") and self._identity() != self.file_identity:
                raise Invalid("workspace database replaced")
            total = sum(path.stat().st_size for path in
                        (self.path, *(Path(str(self.path) + s) for s in ("-journal", "-wal", "-shm")))
                        if path.exists())
            if total > MAX_STORE_BYTES:
                raise Invalid("workspace storage bound")

    def _validate(self):
        self._paths()
        if (self.db.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
                or self.db.execute("PRAGMA user_version").fetchone()[0] != 1
                or self.db.execute("PRAGMA journal_mode").fetchone()[0] != "delete"
                or self.db.execute("PRAGMA quick_check").fetchall() != [("ok",)]):
            raise Invalid("invalid workspace database")
        with closing(sqlite3.connect(":memory:")) as expected:
            expected.executescript(SCHEMA)
            if _signature(self.db) != _signature(expected):
                raise Invalid("workspace schema mismatch")
        meta = self.db.execute("SELECT singleton,config,created_at,last_at FROM meta").fetchall()
        if len(meta) != 1 or meta[0][0] != 1:
            raise Invalid("invalid workspace metadata")
        config = _decode(meta[0][1])
        exact(config, {"research_config", "publication_config"})
        from .cost_guard import validate_config
        validate_config(config["research_config"])
        publication.validate_config(config["publication_config"])
        if self.expected_config is not None and meta[0][1] != self.expected_config:
            raise Invalid("workspace configuration changed")
        start, end = instant(meta[0][2]), instant(meta[0][3])
        if not start <= end <= utc_now():
            raise Invalid("workspace time inconsistency")
        rows = self.db.execute("SELECT * FROM tasks ORDER BY started_at,task_key").fetchall()
        if len(rows) > MAX_TASKS:
            raise Invalid("task storage bound")
        for row in rows:
            key, intent, case_record, owner, status, started, finished, source, report_id, reason = row
            _key(key)
            fingerprint(intent)
            case = _decode(case_record)
            if digest(case) != intent or _config(case) != config:
                raise Invalid("task input or configuration mismatch")
            if (type(owner) is not str or re.fullmatch(r"[0-9a-f]{32}", owner) is None
                    or status not in ("reserved", "complete", "blocked") or reason not in REASONS
                    or not start <= instant(started) <= end):
                raise Invalid("invalid stored task")
            if status == "reserved":
                if finished is not None or source is not None or report_id is not None or reason != "reserved_before_execution":
                    raise Invalid("invalid reservation")
            else:
                if finished is None or not instant(started) <= instant(finished) <= end:
                    raise Invalid("invalid task completion time")
                if source is not None:
                    _decode(source)
                if status == "complete":
                    fingerprint(report_id)
                    if source is None or reason != "offline_report_recorded":
                        raise Invalid("incomplete report receipt")
                elif report_id is not None or reason not in ("research_blocked", "offline_execution_failed"):
                    raise Invalid("invalid blocked task")
        return config

    @contextmanager
    def transaction(self):
        if self.read_only:
            raise Invalid("read-only workspace")
        self._paths()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self._validate()
            now = stamp(utc_now())
            last = self.db.execute("SELECT last_at FROM meta").fetchone()[0]
            if instant(now) < instant(last):
                raise Invalid("workspace clock rollback")
            if self.db.execute("PRAGMA page_count").fetchone()[0] * 4096 + 2 * MAX_RECORD_BYTES > MAX_STORE_BYTES:
                raise Invalid("workspace storage bound")
            yield now
            self.db.execute("UPDATE meta SET last_at=?", (now,))
            self.db.execute("COMMIT")
        except BaseException:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise

    def task(self, key):
        _key(key)
        row = self.db.execute("SELECT * FROM tasks WHERE task_key=?", (key,)).fetchone()
        if row is None:
            raise Invalid("unknown task")
        names = ("task_key", "intent", "case_record", "owner", "status", "started_at",
                 "finished_at", "source_record", "report_id", "reason")
        return dict(zip(names, row))

    def reserve(self, key, case):
        _key(key)
        encoded, identity = _encoded(case), digest(case)
        with self.transaction() as now:
            prior = self.db.execute("SELECT intent FROM tasks WHERE task_key=?", (key,)).fetchone()
            if prior:
                if prior[0] != identity:
                    raise Invalid("task identity conflict")
                return self.task(key)
            if self.db.execute("SELECT 1 FROM tasks WHERE status='reserved'").fetchone():
                raise Invalid("interrupted or active offline task")
            if self.db.execute("SELECT count(*) FROM tasks").fetchone()[0] >= MAX_TASKS:
                raise Invalid("task count bound")
            self.db.execute("INSERT INTO tasks VALUES (?,?,?,?,'reserved',?,NULL,NULL,NULL,?)",
                            (key, identity, encoded, self.owner, now, "reserved_before_execution"))
        return None

    def finish(self, key, status, source=None, report_id=None, reason="offline_execution_failed"):
        if status not in ("complete", "blocked"):
            raise Invalid("invalid final status")
        encoded = _encoded(source) if source is not None else None
        with self.transaction() as now:
            prior = self.task(key)
            if prior["owner"] != self.owner or prior["status"] != "reserved":
                raise Invalid("inactive or foreign task")
            self.db.execute("UPDATE tasks SET status=?,finished_at=?,source_record=?,report_id=?,reason=? WHERE task_key=?",
                            (status, now, encoded, report_id, reason, key))

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def _summary(task, *, reused=False):
    return {"label": "OFFLINE SYNTHETIC WORKFLOW; NO MODEL OR TELEGRAM CALL",
            "task_key": task["task_key"], "case_id": task["intent"], "status": task["status"],
            "report_id": task["report_id"], "reason": task["reason"], "reused": reused,
            "started_at": task["started_at"], "finished_at": task["finished_at"],
            "live_market_coverage": "0/5", "research_mvp": "not_implemented"}


def _read_report(ledger, report_id):
    fingerprint(report_id)
    ledger.snapshot(report_id)  # Validates the complete ledger and append-only lineage.
    row = ledger.db.execute("SELECT record FROM reports WHERE report_id=?", (report_id,)).fetchone()
    if row is None:
        raise Invalid("unknown report")
    report = _decode(row[0])
    publication.verify_report(report, ledger.config)
    return report


def _verify_task(task, config):
    if task["status"] != "complete":
        return
    source = scenario.verify_execution(_decode(task["source_record"]))
    if (digest(source["case"]) != task["intent"]
            or source["task_key"] != task["task_key"]
            or not instant(task["started_at"]) <= instant(source["started_at"])
            <= instant(source["generated_at"]) <= instant(task["finished_at"])):
        raise Invalid("task execution binding mismatch")
    report = publication.build_report(source, config["publication_config"])
    if report["report_id"] != task["report_id"]:
        raise Invalid("task report binding mismatch")


def _budget_preflight(workspace):
    """Cross-check persistent usage before opening any new dispatch-capable store."""
    path = external_path(workspace.directory / "budget.sqlite")
    tasks = {r[0]: workspace.task(r[0]) for r in workspace.db.execute("SELECT task_key FROM tasks")}
    if not path.is_file():
        if tasks:
            raise Invalid("recorded budget store missing; no implicit reset")
        return
    config = workspace.config["research_config"]
    limits = config["budget"]
    size = sum(p.stat().st_size for p in (path, *(Path(str(path) + s) for s in ("-journal", "-wal", "-shm"))) if p.exists())
    if size > limits["max_store_bytes"]:
        raise Invalid("budget storage bound")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("BEGIN")
        _validate_database(db, canonical(config))
        last = instant(db.execute("SELECT last_at FROM meta").fetchone()[0])
        if last > utc_now():
            raise Invalid("budget clock inconsistency")
        db.row_factory = sqlite3.Row
        runs = [dict(r) for r in db.execute("SELECT * FROM runs ORDER BY started_at,run_id")]
        attempts = [dict(r) for r in db.execute("SELECT * FROM attempts ORDER BY attempt_id")]
    if len(runs) > limits["max_runs"] or len(attempts) > limits["max_runs"] * limits["calls_per_run"]:
        raise Invalid("budget history bound")
    by_id, by_task = {}, {}
    from .intelligence import build_bundle
    for run in runs:
        task = tasks.get(run["task_key"])
        if task is None or run["task_key"] in by_task:
            raise Invalid("unbound budget run")
        for identity in (run["run_id"], run["owner"]):
            if type(identity) is not str or re.fullmatch(r"[0-9a-f]{32}", identity) is None:
                raise Invalid("invalid budget run identity")
        case = scenario.validate_case(_decode(task["case_record"]))
        bundle = build_bundle(case["payload"], case["methods"], case["entities"])
        if run["intent"] != digest({"bundle": bundle["bundle_id"], "config": config}):
            raise Invalid("budget intent mismatch")
        start = instant(run["started_at"])
        if stamp(start) != run["started_at"] or not instant(task["started_at"]) <= start <= last:
            raise Invalid("budget run chronology mismatch")
        if run["status"] not in ("active", "complete", "blocked"):
            raise Invalid("unknown budget run state")
        if run["status"] == "active":
            if run["finished_at"] is not None or run["reason"] is not None:
                raise Invalid("invalid active budget run")
        else:
            finish = instant(run["finished_at"])
            if (stamp(finish) != run["finished_at"] or not start <= finish <= last
                    or type(run["reason"]) is not str or not 1 <= len(run["reason"]) <= 100):
                raise Invalid("invalid finished budget run")
            if task["finished_at"] is not None and finish > instant(task["finished_at"]):
                raise Invalid("budget finished after task receipt")
        by_id[run["run_id"]] = run
        by_task[run["task_key"]] = run
    per_run, per_day, records = defaultdict(lambda: [0, 0, 0]), defaultdict(lambda: [0, 0, 0]), defaultdict(list)
    previous = None
    for number, attempt in enumerate(attempts, 1):
        integer(attempt["attempt_id"], number, number)
        run = by_id.get(attempt["run_id"])
        if run is None:
            raise Invalid("orphan budget attempt")
        when = instant(attempt["reserved_at"])
        finish = instant(run["finished_at"]) if run["finished_at"] else last
        if (stamp(when) != attempt["reserved_at"] or attempt["day"] != stamp(when)[:10]
                or not instant(run["started_at"]) <= when <= finish
                or (previous is not None and when < previous)):
            raise Invalid("budget day or reservation chronology mismatch")
        previous = when
        if attempt["role"] not in ("researcher", "reviewer", "editor"):
            raise Invalid("invalid budget role")
        integer(attempt["round"], 1, config["max_rounds"])
        integer(attempt["input_reserved"], limits["input_per_call"], limits["input_per_call"])
        integer(attempt["output_reserved"], limits["output_per_call"], limits["output_per_call"])
        outcome = attempt["status"]
        if outcome in ("success", "failed"):
            integer(attempt["input_used"], 0, attempt["input_reserved"])
            integer(attempt["output_used"], 0, attempt["output_reserved"])
            if attempt["reason"] != outcome:
                raise Invalid("budget settlement reason mismatch")
        elif outcome in ("pending", "unknown"):
            reasons = ({"reserved_before_dispatch"} if outcome == "pending" else
                       {s + "_usage_unconfirmed" for s in ("success", "failed", "timeout", "exception")})
            if attempt["input_used"] is not None or attempt["output_used"] is not None or attempt["reason"] not in reasons:
                raise Invalid("unknown usage was altered")
        else:
            raise Invalid("invalid budget settlement")
        if run["status"] == "complete" and outcome != "success":
            raise Invalid("completed run has unconfirmed usage")
        if run["status"] != "active" and outcome == "pending":
            raise Invalid("finished run has pending usage")
        for counter in (per_run[run["run_id"]], per_day[attempt["day"]]):
            counter[0] += 1
            counter[1] += attempt["input_reserved"]
            counter[2] += attempt["output_reserved"]
        records[run["run_id"]].append(attempt)
    for scope, counters in (("run", per_run), ("day", per_day)):
        for totals in counters.values():
            if any(total > limits[kind + "_per_" + scope] for total, kind in zip(totals, ("calls", "input", "output"))):
                raise Invalid("persisted budget exceeds limits")
    for key, task in tasks.items():
        run = by_task.get(key)
        if task["status"] == "complete":
            _verify_task(task, workspace.config)
            if run is None or run["status"] != "complete":
                raise Invalid("completed task lost its budget run")
        if task["source_record"] is None:
            continue
        source = _decode(task["source_record"])
        if (source.get("case_id") != task["intent"] or source.get("task_key") != key
                or source.get("execution_digest") != digest({k: v for k, v in source.items() if k != "execution_digest"})):
            raise Invalid("budget source receipt mismatch")
        research_result = source["research"]
        accounting = research_result["accounting"]
        if accounting is None:
            if run is not None or research_result["run_id"] is not None:
                raise Invalid("missing budget accounting")
            continue
        if run is None or research_result["run_id"] != run["run_id"]:
            raise Invalid("lost budget run receipt")
        if (accounting["config_id"] != digest(config) or accounting["status"] != run["status"]
                or accounting["started_at"] != run["started_at"] or accounting["finished_at"] != run["finished_at"]
                or canonical(accounting["attempts"]) != canonical(records[run["run_id"]])):
            raise Invalid("budget history differs from execution receipt")


def run_case(case, directory, task_key):
    case = scenario.validate_case(case)
    _key(task_key)
    with Workspace(directory, case) as workspace:
        _budget_preflight(workspace)
        # An opaque failure cannot prove unused budget, even if its rows vanished.
        if (not workspace.db.execute("SELECT 1 FROM tasks WHERE task_key=?", (task_key,)).fetchone()
                and workspace.db.execute("SELECT 1 FROM tasks WHERE status='blocked' AND source_record IS NULL").fetchone()):
            raise Invalid("unconfirmed prior execution; new task forbidden")
        if (workspace.db.execute("SELECT 1 FROM tasks WHERE status='complete'").fetchone()
                and not (workspace.directory / "ledger.sqlite").is_file()):
            raise Invalid("recorded report store missing; no implicit reset")
        prior = workspace.reserve(task_key, case)
        if prior is not None:
            _verify_task(prior, workspace.config)
            if prior["status"] == "complete":
                if not (workspace.directory / "ledger.sqlite").is_file():
                    raise Invalid("recorded ledger missing")
                with Ledger(workspace.directory / "ledger.sqlite", case["publication_config"]) as ledger:
                    _read_report(ledger, prior["report_id"])
            return _summary(prior, reused=True)
        try:
            with BudgetStore(workspace.directory / "budget.sqlite", case["research_config"]) as budget:
                source = scenario.execute_case(case, budget, task_key)
            if source["research"]["status"] != "COMPLETE_SYNTHETIC":
                workspace.finish(task_key, "blocked", source, reason="research_blocked")
            else:
                scenario.verify_execution(source)
                report = publication.build_report(source, case["publication_config"])
                with Ledger(workspace.directory / "ledger.sqlite", case["publication_config"]) as ledger:
                    ledger.append(report)
                workspace.finish(task_key, "complete", source, report["report_id"], "offline_report_recorded")
        except (Invalid, OSError, sqlite3.Error, ValueError, TypeError, KeyError, RecursionError):
            # Preserve the reservation if durable failure recording itself cannot complete.
            workspace.finish(task_key, "blocked", reason="offline_execution_failed")
        return _summary(workspace.task(task_key))


def status(directory, task_key=None):
    with Workspace(directory, read_only=True) as workspace:
        keys = [task_key] if task_key is not None else [r[0] for r in workspace.db.execute(
            "SELECT task_key FROM tasks ORDER BY started_at,task_key")]
        tasks = []
        for key in keys:
            task = workspace.task(key)
            _verify_task(task, workspace.config)
            tasks.append(_summary(task))
        return {"status": "RECORDED_OFFLINE_TASKS", "tasks": tasks,
                "delivery": "NOT_INSPECTED; task completion is not a delivery receipt",
                "ledger_health": "NOT_INSPECTED; export verifies the current ledger"}


def _open_existing(directory):
    workspace = Workspace(directory, read_only=True)
    try:
        if not (workspace.directory / "ledger.sqlite").is_file():
            raise Invalid("publication ledger missing")
        return workspace
    except BaseException:
        workspace.close()
        raise


def export_report(directory, report_id, output):
    with _open_existing(directory) as workspace:
        with Ledger(workspace.directory / "ledger.sqlite", workspace.config["publication_config"]) as ledger:
            report = _read_report(ledger, report_id)
            body = publication.report_text(report, ledger.config).encode("utf-8")
        _write_new(output, body)
    return {"status": "EXPORTED_LOCAL_REPORT", "report_id": report_id}


def correct_report(directory, report_id, reason):
    with _open_existing(directory) as workspace:
        with Ledger(workspace.directory / "ledger.sqlite", workspace.config["publication_config"]) as ledger:
            original = _read_report(ledger, report_id)
            correction = publication.build_report(original["content"]["source"], ledger.config,
                                                   supersedes=report_id, reason=reason)
            ledger.append(correction)
    return {"status": "RECORDED_LOCAL_CORRECTION", "report_id": correction["report_id"],
            "supersedes": report_id, "real_delivery": "not_attempted"}


def simulate_delivery(directory, report_id, *, fail_at=None, malformed_at=None):
    with _open_existing(directory) as workspace:
        with Ledger(workspace.directory / "ledger.sqlite", workspace.config["publication_config"]) as ledger:
            _read_report(ledger, report_id)
            return deliver(ledger, report_id, FakeTransport(fail_at=fail_at, malformed_at=malformed_at))


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Invalid("invalid offline command")


def main(argv=None):
    try:
        parser = _Parser(description=__doc__)
        commands = parser.add_subparsers(dest="command", required=True)
        commands.add_parser("example").add_argument("--output", required=True)
        commands.add_parser("validate").add_argument("--input", required=True)
        run = commands.add_parser("run")
        run.add_argument("--input", required=True)
        run.add_argument("--workspace", required=True)
        run.add_argument("--task-key", required=True)
        view = commands.add_parser("status")
        view.add_argument("--workspace", required=True)
        view.add_argument("--task-key")
        for command in ("export", "correct", "simulate-delivery"):
            item = commands.add_parser(command)
            item.add_argument("--workspace", required=True)
            item.add_argument("--report-id", required=True)
            if command == "export":
                item.add_argument("--output", required=True)
            if command == "correct":
                item.add_argument("--reason", required=True)
        args = parser.parse_args(argv)
        if args.command == "example":
            result = export_example(args.output)
        elif args.command == "validate":
            case = read_case(args.input)
            from .intelligence import build_bundle
            bundle = build_bundle(case["payload"], case["methods"], case["entities"])
            result = {"status": "VALID_SYNTHETIC_CASE", "case_id": digest(case),
                      "evidence_gate": bundle["content"]["status"], "model": "not_called"}
        elif args.command == "run":
            result = run_case(read_case(args.input), args.workspace, args.task_key)
        elif args.command == "status":
            result = status(args.workspace, args.task_key)
        elif args.command == "export":
            result = export_report(args.workspace, args.report_id, args.output)
        elif args.command == "correct":
            result = correct_report(args.workspace, args.report_id, args.reason)
        else:
            result = simulate_delivery(args.workspace, args.report_id)
        print(canonical(result))
        if result["status"] in ("blocked", "reserved", "unknown", "failed") or result.get("evidence_gate") == "BLOCK":
            return 2
        return 0
    except (Invalid, OSError, sqlite3.Error, ValueError, TypeError, KeyError, RecursionError):
        print('{"status":"ERROR","reason":"offline_operation_rejected"}')
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
