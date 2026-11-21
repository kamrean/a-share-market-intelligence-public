"""Offline end-to-end demo checks using the real workflow and fake delivery."""

from contextlib import ExitStack, closing, redirect_stderr, redirect_stdout
from io import StringIO
import json
import os
import re
import sqlite3
from tempfile import gettempdir
import unittest
from unittest.mock import patch

from market_intelligence import mvp, publication, workflow
from market_intelligence.store import CHECKOUT


def _cli(*args):
    stdout, stderr = StringIO(), StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = mvp.main(args)
    return code, stdout.getvalue(), stderr.getvalue()


def _ledger_records(directory):
    uri = (directory / "ledger.sqlite").as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as db:
        return dict(db.execute("SELECT report_id, record FROM reports"))


class MVPTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        temp = gettempdir()
        stack.enter_context(patch.dict(os.environ, {"TEMP": temp, "TMP": temp}, clear=True))
        for name in ("socket", "create_connection", "getaddrinfo", "gethostbyname",
                     "gethostbyname_ex", "gethostbyaddr", "getnameinfo"):
            mock = stack.enter_context(patch("socket." + name, side_effect=AssertionError("network forbidden")))
            self.addCleanup(mock.assert_not_called)

    def assert_blocked(self, *args):
        self.assertEqual(_cli(*args), (2, "", "Offline demo blocked.\n"))

    def test_json_report_correction_delivery_and_workspace_lifecycle(self):
        initial = {}
        directories = []
        correct = workflow.correct_report
        simulate = workflow.simulate_delivery

        def capture_original(directory, report_id, reason):
            self.assertTrue(directory.is_dir())
            self.assertNotIn(CHECKOUT, directory.parents)
            self.assertFalse(any((p / ".git").exists() for p in directory.parents))
            self.assertTrue((directory / "budget.sqlite").is_file())
            directories.append(directory)
            initial.update(_ledger_records(directory))
            self.assertEqual(set(initial), {report_id})
            return correct(directory, report_id, reason)

        def capture_correction(directory, correction_id):
            records = _ledger_records(directory)
            original_id, original_record = next(iter(initial.items()))
            self.assertEqual(set(records), {original_id, correction_id})
            self.assertEqual(records[original_id], original_record)
            original = json.loads(original_record)
            correction = json.loads(records[correction_id])
            self.assertTrue(publication.verify_report(original))
            self.assertTrue(publication.verify_report(correction))
            self.assertEqual(correction["content"]["kind"], "correction")
            self.assertEqual(correction["content"]["supersedes"], original_id)
            self.assertEqual([p["body"] for p in correction["parts"]], [p["body"] for p in original["parts"]])
            return simulate(directory, correction_id)

        with (patch.object(workflow, "correct_report", side_effect=capture_original),
              patch.object(workflow, "simulate_delivery", side_effect=capture_correction)):
            code, stdout, stderr = _cli("demo", "--json")
        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(len(directories), 1)
        self.assertFalse(directories[0].exists())
        self.assertNotIn(str(directories[0].parent), stdout)
        value = json.loads(stdout)
        self.assertEqual(set(value), {"status", "label", "mode", "report", "correction", "delivery"})
        self.assertEqual(value["status"], "COMPLETE_SYNTHETIC")
        self.assertEqual(value["mode"], "synthetic_test")
        self.assertIn("NOT INVESTMENT ADVICE", value["label"])
        report, correction, delivery = value["report"], value["correction"], value["delivery"]
        self.assertEqual(report["parts"], 3)
        self.assertRegex(report["report_id"], r"^[0-9a-f]{64}$")
        self.assertRegex(correction["report_id"], r"^[0-9a-f]{64}$")
        self.assertNotEqual(report["report_id"], correction["report_id"])
        self.assertEqual(correction["supersedes"], report["report_id"])
        self.assertEqual(report["text"], publication.report_text(json.loads(initial[report["report_id"]])))
        self.assertEqual(re.findall(r"^report_id=" + report["report_id"] + r" part=([1-3])/3$",
                                    report["text"], re.MULTILINE), ["1", "2", "3"])
        self.assertIn("Live benchmark coverage 0/5", report["text"])
        self.assertIn("not truth, entailment or causal verification", report["text"])
        self.assertEqual(set(delivery), {"label", "status", "report_id", "chunks", "real_delivery"})
        self.assertEqual(delivery["label"], "SIMULATED DELIVERY ONLY")
        self.assertEqual(delivery["report_id"], correction["report_id"])
        self.assertEqual(delivery["status"], "completed")
        self.assertGreater(delivery["chunks"], 0)
        self.assertEqual(delivery["real_delivery"], "not_attempted")

    def test_report_text_except_execution_identity_is_repeatable(self):
        def report_text():
            code, stdout, stderr = _cli("demo", "--json")
            self.assertEqual((code, stderr), (0, ""))
            report = json.loads(stdout)["report"]
            return report["text"].replace("report_id=" + report["report_id"], "report_id=<run>")

        self.assertEqual(report_text(), report_text())

    def test_human_output_is_short_and_explicitly_offline(self):
        code, stdout, stderr = _cli("demo")
        self.assertEqual((code, stderr), (0, ""))
        self.assertLess(len(stdout.splitlines()), 24)
        self.assertIn("OFFLINE SYNTHETIC DEMO", stdout)
        self.assertEqual(re.findall(r"^([123])\. ", stdout, re.MULTILINE), ["1", "2", "3"])
        self.assertRegex(stdout, r"Correction [a-f0-9]{64} supersedes [a-f0-9]{64}")
        self.assertIn("Live benchmark coverage: 0/5. Semantic quality unassessed.", stdout)
        self.assertIn("SIMULATED DELIVERY ONLY: completed", stdout)

    def test_blocked_workflow_stops_and_removes_workspace(self):
        with (patch.object(publication, "build_report", side_effect=OSError(r"C:\private\fixture.txt")),
              patch.object(workflow, "run_case", wraps=workflow.run_case) as run,
              patch.object(workflow, "correct_report") as correct,
              patch.object(workflow, "export_report") as export,
              patch.object(workflow, "simulate_delivery") as deliver):
            self.assert_blocked("demo", "--json")
        run.assert_called_once()
        self.assertFalse(run.call_args.args[1].exists())
        correct.assert_not_called()
        export.assert_not_called()
        deliver.assert_not_called()

    def test_failed_fake_delivery_emits_no_report(self):
        simulate = workflow.simulate_delivery
        with patch.object(workflow, "simulate_delivery",
                          side_effect=lambda directory, identity: simulate(directory, identity, fail_at=1)) as deliver:
            self.assert_blocked("demo", "--json")
        deliver.assert_called_once()
        self.assertFalse(deliver.call_args.args[0].exists())

    def test_export_error_does_not_leak_path_or_start_delivery(self):
        with (patch.object(workflow, "export_report", side_effect=RuntimeError(r"C:\private\report.txt")) as export,
              patch.object(workflow, "simulate_delivery") as deliver):
            self.assert_blocked("demo")
        export.assert_called_once()
        self.assertFalse(export.call_args.args[0].exists())
        deliver.assert_not_called()

    def test_checkout_temp_override_is_refused_before_workspace_creation(self):
        with (patch.object(mvp, "gettempdir", return_value=str(CHECKOUT)),
              patch.object(mvp, "TemporaryDirectory") as temporary):
            self.assert_blocked("demo")
        temporary.assert_not_called()

    def test_cli_refuses_paths_and_undocumented_options(self):
        with patch.object(mvp, "_demo") as demo:
            for args in ((), ("run",), ("demo", "--workspace", r"C:\private"),
                         ("demo", "--case", r"C:\private"), ("demo", "--allow-network"),
                         ("demo", "--j")):
                with self.subTest(args=args):
                    self.assert_blocked(*args)
        demo.assert_not_called()
