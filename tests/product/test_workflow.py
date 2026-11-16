"""Offline CLI and persistent-task integration regressions; all network denied."""

from contextlib import ExitStack, closing, redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from market_intelligence import scenario, workflow as w
from market_intelligence.models import Invalid, canonical, digest


def forbidden(*args, **kwargs):
    raise AssertionError("offline workflow cannot use network")


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for name in ("socket.socket", "socket.create_connection", "socket.getaddrinfo",
                     "socket.gethostbyname", "socket.gethostbyname_ex", "socket.gethostbyaddr",
                     "socket.getnameinfo"):
            self.stack.enter_context(patch(name, side_effect=forbidden))
        self.temp = self.stack.enter_context(TemporaryDirectory(prefix="p4c-workflow-"))
        self.root = Path(self.temp)
        self.directory = self.root / "workspace"
        self.case = scenario.example_case()

    def run_case(self, key="sample", case=None):
        result = w.run_case(self.case if case is None else case, self.directory, key)
        self.assertEqual(result["status"], "complete", result)
        return result

    def cli(self, args):
        output = io.StringIO()
        with redirect_stdout(output):
            code = w.main(args)
        return code, json.loads(output.getvalue())

    def test_example_roundtrip_no_overwrite(self):
        output = self.root / "case.json"
        first = w.export_example(output)
        self.assertEqual(first["case_id"], digest(w.read_case(output)))
        before = output.read_bytes()
        with self.assertRaises(FileExistsError):
            w.export_example(output)
        self.assertEqual(output.read_bytes(), before)

    def test_end_to_end_export_and_private_interface_remain_off(self):
        result = self.run_case()
        target = self.root / "report.txt"
        w.export_report(self.directory, result["report_id"], target)
        body = target.read_text(encoding="utf-8")
        for phrase in ("SYNTHETIC", "1. Sourced news", "2. Benchmarks", "3. Sectors", "0/5"):
            self.assertIn(phrase, body)
        self.assertNotIn(str(self.directory), body)
        self.assertEqual(result["live_market_coverage"], "0/5")
        self.assertEqual(result["research_mvp"], "not_implemented")

    def test_optional_stale_news_saves_reuses_and_exports_degraded_report(self):
        self.case["payload"]["facts"] = [f for f in self.case["payload"]["facts"]
                                          if f["payload"]["kind"] in {"bar", "news"}]
        self.case["methods"]["news_ttl_seconds"] = 1
        for rule in self.case["methods"]["rules"]:
            if rule["domain"] in {"news", "event", "entity"}:
                rule.update(required=False, allow_degraded=True)
        responses = self.case["responses"]["responses"]
        claims = [c for c in responses[0]["body"]["claims"] if c["domain"] == "market"]
        responses[0]["body"]["claims"] = claims
        selected = {c["claim_id"] for c in claims}
        responses[1]["body"]["verdicts"] = [v for v in responses[1]["body"]["verdicts"]
                                             if v["claim_id"] in selected]
        responses[2]["body"]["selected_ids"] = sorted(selected)
        first = self.run_case()
        self.assertTrue(self.run_case()["reused"])
        output = self.root / "degraded.txt"
        w.export_report(self.directory, first["report_id"], output)
        body = output.read_text(encoding="utf-8")
        self.assertIn("Synthetic domain news: DEGRADED;", body)
        self.assertNotIn("may warrant informational attention", body)
        with closing(sqlite3.connect(self.directory / "budget.sqlite")) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM attempts").fetchone()[0], 3)

    def test_completed_task_restart_does_not_run_again(self):
        first = self.run_case()
        with closing(sqlite3.connect(self.directory / "budget.sqlite")) as db:
            before = db.execute("SELECT count(*) FROM attempts").fetchone()[0]
        with patch.object(scenario, "execute_case", side_effect=AssertionError("must reuse")):
            repeat = self.run_case()
        self.assertEqual(repeat["report_id"], first["report_id"])
        self.assertTrue(repeat["reused"])
        with closing(sqlite3.connect(self.directory / "budget.sqlite")) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM attempts").fetchone()[0], before)

    def test_conflicting_task_does_not_mutate_prior_record(self):
        first = self.run_case()
        changed = deepcopy(self.case)
        changed["research_config"]["roles"]["editor"]["prompt"] = "changed-offline-prompt"
        with self.assertRaises(Invalid):
            w.run_case(changed, self.directory, "sample")
        self.assertEqual(w.status(self.directory)["tasks"][0]["report_id"], first["report_id"])

    def test_second_task_preserves_budget_attempt_ids(self):
        self.run_case("first")
        second = self.run_case("second")
        self.assertFalse(second["reused"])
        with closing(sqlite3.connect(self.directory / "budget.sqlite")) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM attempts").fetchone()[0], 6)
        self.assertEqual(len(w.status(self.directory)["tasks"]), 2)

    def test_changed_case_same_task_key_conflicts(self):
        self.run_case()
        changed = deepcopy(self.case)
        changed["label"] = "SYNTHETIC alternate case identity"
        with self.assertRaisesRegex(Invalid, "task identity conflict"):
            w.run_case(changed, self.directory, "sample")

    def test_known_budget_exhaustion_stays_blocked(self):
        self.case["research_config"]["budget"]["calls_per_day"] = 3
        self.run_case("first")
        second = w.run_case(self.case, self.directory, "second")
        self.assertEqual(second["status"], "blocked")
        with closing(sqlite3.connect(self.directory / "budget.sqlite")) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM attempts").fetchone()[0], 3)
        self.assertTrue(w.run_case(self.case, self.directory, "second")["reused"])

    def test_unknown_usage_prevents_further_dispatch(self):
        self.case["responses"]["responses"][0]["usage"] = None
        first = w.run_case(self.case, self.directory, "unknown")
        self.assertEqual(first["status"], "blocked")
        second = w.run_case(self.case, self.directory, "another")
        self.assertEqual(second["status"], "blocked")
        with closing(sqlite3.connect(self.directory / "budget.sqlite")) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM attempts").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT status FROM attempts").fetchone()[0], "unknown")

    def test_corrupt_budget_history_blocks_before_dispatch_without_writes(self):
        mutations = (
            "UPDATE attempts SET day='2000-01-01'",
            "UPDATE attempts SET day='2000-01-01',reserved_at='2000-01-01T00:00:00Z'",
            "UPDATE attempts SET input_reserved=1",
            "UPDATE attempts SET output_reserved=1",
            "UPDATE attempts SET input_used=input_used+1",
            "UPDATE attempts SET role='editor' WHERE attempt_id=1",
            "UPDATE attempts SET status='failed',reason='failed' WHERE attempt_id=1",
            "DELETE FROM attempts WHERE attempt_id=3",
            "DELETE FROM attempts",
            "DELETE FROM runs",
            "UPDATE runs SET started_at='2000-01-01T00:00:00Z'",
            "UPDATE runs SET intent='corrupted'",
        )
        self.case["research_config"]["budget"]["calls_per_day"] = 3
        for number, mutation in enumerate(mutations):
            with self.subTest(mutation=mutation):
                directory = self.root / ("corruption-" + str(number))
                self.assertEqual(w.run_case(self.case, directory, "first")["status"], "complete")
                with closing(sqlite3.connect(directory / "budget.sqlite")) as db, db:
                    db.execute(mutation)
                before = {p.name: p.read_bytes() for p in directory.iterdir()}
                with patch.object(scenario, "execute_case", side_effect=AssertionError("no dispatch")):
                    with self.assertRaises(Invalid):
                        w.run_case(self.case, directory, "second")
                self.assertEqual(before, {p.name: p.read_bytes() for p in directory.iterdir()})

    def test_deleted_budget_history_cannot_reset_recorded_usage(self):
        self.run_case()
        with closing(sqlite3.connect(self.directory / "budget.sqlite")) as db, db:
            db.execute("DELETE FROM attempts")
            db.execute("DELETE FROM runs")
        before = {p.name: p.read_bytes() for p in self.directory.iterdir()}
        with patch.object(scenario, "execute_case", side_effect=AssertionError("no dispatch")):
            with self.assertRaises(Invalid):
                w.run_case(self.case, self.directory, "new")
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.directory.iterdir()})

    def test_opaque_failure_blocks_new_dispatch_even_after_history_deletion(self):
        with patch.object(w.publication, "build_report", side_effect=OSError("publication interrupted")):
            failed = w.run_case(self.case, self.directory, "failed")
        self.assertEqual(failed["status"], "blocked")
        self.assertEqual(failed["reason"], "offline_execution_failed")
        with closing(sqlite3.connect(self.directory / "budget.sqlite")) as db, db:
            self.assertEqual(db.execute("SELECT count(*) FROM attempts").fetchone()[0], 3)
            db.execute("DELETE FROM attempts")
            db.execute("DELETE FROM runs")
        before = {p.name: p.read_bytes() for p in self.directory.iterdir()}
        with patch.object(scenario, "execute_case", side_effect=AssertionError("no dispatch")):
            with self.assertRaisesRegex(Invalid, "unconfirmed prior execution"):
                w.run_case(self.case, self.directory, "new")
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.directory.iterdir()})
        self.assertTrue(w.run_case(self.case, self.directory, "failed")["reused"])

    def test_unknown_budget_usage_cannot_be_rewritten_as_known(self):
        self.case["responses"]["responses"][0]["usage"] = None
        self.assertEqual(w.run_case(self.case, self.directory, "unknown")["status"], "blocked")
        with closing(sqlite3.connect(self.directory / "budget.sqlite")) as db, db:
            db.execute("UPDATE attempts SET status='success',reason='success',input_used=0,output_used=0")
        before = {p.name: p.read_bytes() for p in self.directory.iterdir()}
        with patch.object(scenario, "execute_case", side_effect=AssertionError("no dispatch")):
            with self.assertRaises(Invalid):
                w.run_case(self.case, self.directory, "new")
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.directory.iterdir()})

    def test_task_source_identity_cannot_be_transplanted(self):
        self.run_case("original")
        with closing(sqlite3.connect(self.directory / "workflow.sqlite")) as db, db:
            db.execute("UPDATE tasks SET task_key='transplanted'")
        with self.assertRaises(Invalid):
            w.status(self.directory)

    def test_status_is_read_only_and_missing_workspace_stays_missing(self):
        with self.assertRaises(Invalid):
            w.status(self.directory)
        self.assertFalse(self.directory.exists())
        self.run_case()
        before = {p.name: p.read_bytes() for p in self.directory.iterdir()}
        result = w.status(self.directory, "sample")
        self.assertIn("NOT_INSPECTED", result["delivery"])
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.directory.iterdir()})

    def test_interrupted_task_is_visible_and_not_retried(self):
        with patch.object(scenario, "execute_case", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                w.run_case(self.case, self.directory, "interrupted")
        self.assertEqual(w.status(self.directory)["tasks"][0]["status"], "reserved")
        with patch.object(scenario, "execute_case", side_effect=AssertionError("no retry")):
            result = w.run_case(self.case, self.directory, "interrupted")
        self.assertEqual(result["status"], "reserved")
        with self.assertRaises(Invalid):
            w.run_case(self.case, self.directory, "another")

    def test_failure_is_recorded_without_raw_error_text(self):
        with patch.object(scenario, "execute_case", side_effect=OSError("sensitive transport detail")):
            result = w.run_case(self.case, self.directory, "failed")
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "offline_execution_failed")
        self.assertNotIn("sensitive", canonical(w.status(self.directory)))
        with patch.object(scenario, "execute_case", side_effect=AssertionError("no retry")):
            self.assertTrue(w.run_case(self.case, self.directory, "failed")["reused"])

    def test_missing_budget_is_not_reset(self):
        self.run_case()
        (self.directory / "budget.sqlite").unlink()
        with self.assertRaises(Invalid):
            w.run_case(self.case, self.directory, "new")
        self.assertFalse((self.directory / "budget.sqlite").exists())

    def test_missing_ledger_is_not_reset(self):
        result = self.run_case()
        (self.directory / "ledger.sqlite").unlink()
        for operation in (lambda: w.run_case(self.case, self.directory, "new"),
                          lambda: w.export_report(self.directory, result["report_id"], self.root / "report.txt")):
            with self.assertRaises(Invalid):
                operation()
        self.assertFalse((self.directory / "ledger.sqlite").exists())

    def test_unsupported_model_configuration_is_blocked(self):
        self.case["research_config"]["adapter"] = "external_model"
        with self.assertRaises(Invalid):
            w.run_case(self.case, self.directory, "model")
        self.assertFalse(self.directory.exists())

    def test_paid_or_unknown_case_fields_are_rejected(self):
        for name, value in (("api_key", "not-a-real-key"), ("paid_budget", 1), ("endpoint", "https://example.invalid")):
            changed = deepcopy(self.case)
            changed[name] = value
            with self.assertRaises(Invalid):
                w.run_case(changed, self.directory, "bad")

    def test_foreign_database_and_triggers_rejected(self):
        self.directory.mkdir()
        with closing(sqlite3.connect(self.directory / "workflow.sqlite")) as db, db:
            db.execute("CREATE TABLE unrelated(value TEXT)")
        before = (self.directory / "workflow.sqlite").read_bytes()
        with self.assertRaises(Invalid):
            w.run_case(self.case, self.directory, "bad")
        self.assertEqual(before, (self.directory / "workflow.sqlite").read_bytes())

    def test_completed_receipt_tamper_is_detected(self):
        self.run_case()
        with closing(sqlite3.connect(self.directory / "workflow.sqlite")) as db, db:
            db.execute("UPDATE tasks SET report_id=?", ("0" * 64,))
        with self.assertRaises(Invalid):
            w.status(self.directory)

    def test_invalid_status_and_timestamp_are_rejected(self):
        self.run_case()
        with closing(sqlite3.connect(self.directory / "workflow.sqlite")) as db, db:
            db.execute("UPDATE tasks SET status='live_verified'")
        with self.assertRaises(Invalid):
            w.status(self.directory)

    def test_correction_links_and_cannot_branch_lineage(self):
        result = self.run_case()
        correction = w.correct_report(self.directory, result["report_id"], "Clarification of the offline scenario scope.")
        self.assertEqual(correction["supersedes"], result["report_id"])
        with self.assertRaises(Invalid):
            w.correct_report(self.directory, result["report_id"], "Clarification: authored responses are not actual model output.")
        target = self.root / "correction.txt"
        w.export_report(self.directory, correction["report_id"], target)
        self.assertIn(result["report_id"], target.read_text())

    def test_correction_cannot_insert_trade_instructions(self):
        result = self.run_case()
        with self.assertRaises(Invalid):
            w.correct_report(self.directory, result["report_id"], "buy now 100 shares")

    def test_fake_delivery_is_explicit_and_idempotent(self):
        result = self.run_case()
        receipt = w.simulate_delivery(self.directory, result["report_id"])
        self.assertEqual(receipt["label"], "SIMULATED DELIVERY ONLY")
        self.assertEqual(receipt["status"], "completed")
        self.assertEqual(w.simulate_delivery(self.directory, result["report_id"]), receipt)

    def test_ambiguous_delivery_survives_restart(self):
        result = self.run_case()
        first = w.simulate_delivery(self.directory, result["report_id"], malformed_at=1)
        self.assertEqual(first["status"], "unknown")
        repeat = w.simulate_delivery(self.directory, result["report_id"])
        self.assertEqual(repeat["status"], "unknown")
        self.assertEqual(sum(c["reservation"] is not None for c in repeat["chunks"]), 1)

    def test_export_never_overwrites_case_or_existing_output(self):
        result = self.run_case()
        target = self.root / "case.json"
        w.export_example(target)
        before = target.read_bytes()
        with self.assertRaises(FileExistsError):
            w.export_report(self.directory, result["report_id"], target)
        self.assertEqual(target.read_bytes(), before)

    def test_relative_and_checkout_paths_rejected(self):
        for directory in (Path("relative"), Path(__file__).resolve().parents[2] / "runtime-forbidden"):
            with self.assertRaises(Invalid):
                w.run_case(self.case, directory, "bad")

    def test_hardlinked_case_rejected(self):
        original, linked = self.root / "original.json", self.root / "linked.json"
        w.export_example(original)
        os.link(original, linked)
        with self.assertRaises(Invalid):
            w.read_case(linked)

    def test_hardlinked_database_rejected(self):
        self.run_case()
        os.link(self.directory / "workflow.sqlite", self.root / "alias.sqlite")
        with self.assertRaises(Invalid):
            w.status(self.directory)

    def test_linked_parent_rejected(self):
        with patch.object(Path, "is_symlink", return_value=True):
            with self.assertRaises(Invalid):
                w.run_case(self.case, self.directory, "bad")
        with patch.object(Path, "is_junction", return_value=True):
            with self.assertRaises(Invalid):
                w.run_case(self.case, self.directory, "bad")

    def test_bounded_and_duplicate_json_inputs(self):
        file = self.root / "invalid.json"
        for body in (b'{"a":1,"a":2}', b"[" * (w.MAX_RECORD_BYTES + 1)):
            file.write_bytes(body)
            with self.assertRaises(Invalid):
                w.read_case(file)

    def test_cli_complete_local_chain(self):
        casefile = str(self.root / "case.json")
        directory = str(self.directory)
        self.assertEqual(self.cli(["example", "--output", casefile])[0], 0)
        self.assertEqual(self.cli(["validate", "--input", casefile])[0], 0)
        code, result = self.cli(["run", "--input", casefile, "--workspace", directory, "--task-key", "cli"])
        self.assertEqual(code, 0, result)
        self.assertEqual(self.cli(["status", "--workspace", directory])[0], 0)
        self.assertEqual(self.cli(["export", "--workspace", directory, "--report-id", result["report_id"],
                                   "--output", str(self.root / "report.txt")])[0], 0)

    def test_cli_invalid_arguments_and_errors_do_not_echo_inputs(self):
        for args in ([], ["unknown-sensitive-command"], ["validate", "--input", "sensitive-invalid-path"]):
            code, value = self.cli(args)
            self.assertEqual(code, 2)
            self.assertEqual(value, {"status": "ERROR", "reason": "offline_operation_rejected"})

    def test_task_key_and_record_limits(self):
        for key in ("../escape", "bad key", "", "x" * 81):
            with self.assertRaises(Invalid):
                w.run_case(self.case, self.directory, key)
        self.run_case()
        with patch.object(w, "MAX_TASKS", 1), self.assertRaises(Invalid):
            w.run_case(self.case, self.directory, "next")


if __name__ == "__main__":
    unittest.main()
