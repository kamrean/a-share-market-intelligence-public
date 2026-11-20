"""Offline P4MVP scope, approval-chain and checkout regressions."""

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

import p4t_merged_fixture


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("p4mvp_governance_tests", ROOT / "tools/governance_mvp.py")
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)
g = m.g


def json_text(value):
    return json.dumps(value, sort_keys=True) + "\n"


def authored_receipts(snapshot_id):
    common = {"schema_version": 1, "package": "P4MVP", "snapshot_id": snapshot_id}
    validation = json_text({
        **common, "role": "builder_validation", "verdict": "PASS", "live_requests": 0,
        "results": {
            "commands": [{"command": name, "exit_code": 0} for name in m.VALIDATION_COMMANDS],
            "counts": {**m.VALIDATION_COUNT_MINIMA, "product_windows_symlink_skip": 1},
            "builder_agents": [str(uuid4()) for _ in range(3)],
        },
    })
    review = json_text({
        **common, "role": "reviewer", "verdict": "PASS", "agent_id": str(uuid4()),
        "read_only": True, "findings": [], "validation_sha256": g.sha256(validation),
    })
    release = json_text({
        **common, "role": "release_checker", "verdict": "LOCAL_READY_FOR_PR",
        "agent_id": str(uuid4()), "read_only": True, "review_sha256": g.sha256(review),
        "remote_ci": "NOT_RUN_AT_SNAPSHOT", "human_merge_required": True,
    })
    return {m.VALIDATION: validation, m.REVIEW: review, m.RELEASE: release}


class PlanningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.planning = {name: g.read_text(ROOT, name) for name in (*m.PLANNING_PATHS, m.ENTRY)}

    def test_recorded_entry_snapshot_and_contract_match(self):
        contract, state = m.validate_planning(self.planning)
        self.assertEqual((contract["base"], contract["branch"]), (m.BASE, m.BRANCH))
        self.assertEqual(state["stage_registry"][-1]["status"], "deferred")
        self.assertEqual(tuple(contract["receipt_paths"]), m.RECEIPTS)

    def test_every_planning_input_is_frozen_even_with_rewritten_receipt(self):
        for name in m.PLANNING_PATHS:
            changed = {**self.planning, name: self.planning[name] + "\n"}
            entry = g.parse_json(self.planning[m.ENTRY])
            entry["planning_snapshot"] = g.snapshot_for_paths(changed, m.PLANNING_PATHS)[0]
            changed[m.ENTRY] = json_text(entry)
            with self.subTest(path=name), self.assertRaisesRegex(m.Block, "frozen planning snapshot"):
                m.validate_planning(changed)

    def test_entry_role_scope_and_both_verdicts_are_required(self):
        entry = g.parse_json(self.planning[m.ENTRY])
        for key, value in (("entry_verdict", "BLOCK"), ("prompt_verdict", "BLOCK"),
                           ("scope", "runtime"), ("read_only", 1), ("agent_id", "coordinator"),
                           ("planning_snapshot", "a" * 64), ("plan_snapshot", m.PLANNING_SNAPSHOT)):
            with self.subTest(field=key), self.assertRaisesRegex(m.Block, "entry and prompt receipt"):
                m.validate_planning({**self.planning, m.ENTRY: json_text({**entry, key: value})})

    def test_workflow_changes_only_active_checks_and_demo_smoke(self):
        path = ".github/workflows/governance.yml"
        old = p4t_merged_fixture.merged_contents(ROOT, g)[path]
        current = g.read_text(ROOT, path)
        expected = old.replace(
            "# P4T retains locked setup and historical suites; no source requests in CI.",
            "# Retain the locked setup and historical suites; no source requests in CI.",
        ).replace(
            "      - name: Scope and canonical consistency\n",
            "      - name: Smoke test the offline MVP demo\n"
            "        run: python -B -m market_intelligence.mvp demo --json\n"
            "      - name: Scope and canonical consistency\n",
        ).replace(
            "tools/governance_p4t.py check --ci", "tools/governance_mvp.py check --ci",
        ).replace(
            "tools/governance_p4t.py prompt --verify", "tools/governance_mvp.py snapshot --ci",
        ).replace(
            "tools/governance_p4t.py gate --ci", "tools/governance_mvp.py gate --ci",
        )
        self.assertEqual(current, expected)


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.digest = "b" * 64
        self.receipts = authored_receipts(self.digest)
        self.state = {"builder_agents": ["coordinator"]}

    def validate(self, receipts):
        m.validate_receipts(self.state, receipts, self.digest)

    def test_receipt_chain_uses_lf_normalized_bytes(self):
        self.validate(self.receipts)
        self.validate({name: text.replace("\n", "\r\n") for name, text in self.receipts.items()})
        for path in (m.VALIDATION, m.REVIEW):
            with self.subTest(path=path), self.assertRaisesRegex(m.Block, "sha256"):
                self.validate({**self.receipts, path: self.receipts[path] + "\n"})

    def test_roles_cannot_cross_snapshot_or_claim_live_or_remote_results(self):
        for path, key, value in (
            (m.VALIDATION, "live_requests", 1), (m.VALIDATION, "live_requests", False),
            (m.VALIDATION, "schema_version", True), (m.VALIDATION, "package", "P4T"),
            (m.REVIEW, "snapshot_id", "a" * 64), (m.REVIEW, "findings", ["BLOCK"]),
            (m.REVIEW, "read_only", 1), (m.REVIEW, "role", "builder_validation"),
            (m.RELEASE, "remote_ci", "PASS"), (m.RELEASE, "human_merge_required", False),
            (m.RELEASE, "runtime_allowed", False),
        ):
            changed = {**g.parse_json(self.receipts[path]), key: value}
            with self.subTest(field=key), self.assertRaises(m.Block):
                self.validate({**self.receipts, path: json_text(changed)})

    def test_validation_requires_distinct_native_builders(self):
        receipt = g.parse_json(self.receipts[m.VALIDATION])
        builders = receipt["results"]["builder_agents"]
        for value in (builders[:2], [builders[0]] * 3, ["bogus", *builders[1:]]):
            changed = deepcopy(receipt)
            changed["results"]["builder_agents"] = value
            with self.subTest(builders=value), self.assertRaisesRegex(m.Block, "validation"):
                self.validate({**self.receipts, m.VALIDATION: json_text(changed)})

    def test_validation_requires_each_exact_successful_command(self):
        results = g.parse_json(self.receipts[m.VALIDATION])["results"]
        commands = results["commands"]
        m.validate_results({**results, "commands": list(reversed(commands))})
        for index, command in enumerate(commands):
            remaining = commands[:index] + commands[index + 1:]
            with self.subTest(missing=command["command"]), self.assertRaisesRegex(m.Block, "command names"):
                m.validate_results({**results, "commands": remaining})
        for replacement in (commands[1], {"command": commands[0]["command"] + " ", "exit_code": 0},
                            {**commands[0], "exit_code": False}, {**commands[0], "exit_code": 1}):
            with self.subTest(replacement=replacement), self.assertRaisesRegex(m.Block, "validation"):
                m.validate_results({**results, "commands": [replacement, *commands[1:]]})
        with self.assertRaisesRegex(m.Block, "command names"):
            m.validate_results({**results, "commands": [*commands, commands[0]]})

    def test_validation_requires_each_exact_suite_count(self):
        results = g.parse_json(self.receipts[m.VALIDATION])["results"]
        for name in results["counts"]:
            counts = dict(results["counts"])
            del counts[name]
            with self.subTest(missing=name), self.assertRaisesRegex(m.Block, "validation counts"):
                m.validate_results({**results, "counts": counts})
        with self.assertRaisesRegex(m.Block, "validation counts"):
            m.validate_results({**results, "counts": {**results["counts"], "extra_suite": 99}})

    def test_validation_enforces_suite_minima_and_product_accounting(self):
        results = g.parse_json(self.receipts[m.VALIDATION])["results"]
        counts = results["counts"]
        m.validate_results({**results, "counts": {**counts, "product_windows_symlink_skip": 0,
                                                 "product_passed": counts["product_collected"]}})
        for name, minimum in m.VALIDATION_COUNT_MINIMA.items():
            for invalid in (minimum - 1, True):
                with self.subTest(suite=name, count=invalid), self.assertRaisesRegex(m.Block, name):
                    m.validate_results({**results, "counts": {**counts, name: invalid}})
        with self.assertRaisesRegex(m.Block, "product count accounting"):
            m.validate_results({**results, "counts": {**counts, "product_windows_symlink_skip": 0}})
        with self.assertRaisesRegex(m.Block, "at most one Windows symlink skip"):
            m.validate_results({**results, "counts": {**counts, "product_windows_symlink_skip": 2,
                                                     "product_collected": counts["product_passed"] + 2}})
        with self.assertRaisesRegex(m.Block, "focused suites exceed product passes"):
            m.validate_results({**results, "counts": {**counts, "focused_tencent": counts["product_passed"]}})

    def test_builder_coordinator_and_auditor_cannot_review_or_release(self):
        builders = g.parse_json(self.receipts[m.VALIDATION])["results"]["builder_agents"]
        review = g.parse_json(self.receipts[m.REVIEW])
        release = g.parse_json(self.receipts[m.RELEASE])
        for actor in (*builders, "CoOrDiNaToR", m.AUDITOR):
            authored_review = json_text({**review, "agent_id": actor})
            authored_release = json_text({**release, "review_sha256": g.sha256(authored_review)})
            with self.subTest(role="review", actor=actor), self.assertRaisesRegex(m.Block, "independent"):
                self.validate({**self.receipts, m.REVIEW: authored_review, m.RELEASE: authored_release})
            with self.subTest(role="release", actor=actor), self.assertRaisesRegex(m.Block, "independent"):
                self.validate({**self.receipts, m.RELEASE: json_text({**release, "agent_id": actor})})
        with self.assertRaisesRegex(m.Block, "independent"):
            self.validate({**self.receipts, m.RELEASE: json_text({**release, "agent_id": review["agent_id"]})})

    def test_runtime_commands_block_before_access_with_or_without_ci(self):
        for command in m.CLOSED_COMMANDS:
            for flags in ([], ["--ci"]):
                output, error = io.StringIO(), io.StringIO()
                with self.subTest(command=command, flags=flags), redirect_stdout(output), redirect_stderr(error), \
                        patch.object(m, "check", side_effect=AssertionError("scope check")), \
                        patch.object(g, "git_raw", side_effect=AssertionError("Git access")), \
                        patch.object(g, "read_text", side_effect=AssertionError("file read")):
                    self.assertEqual(m.main([command, *flags], root=ROOT), 1)
                self.assertEqual(output.getvalue(), "")
                self.assertIn("--ci is not runtime authorization", error.getvalue())


class CandidateTests(unittest.TestCase):
    def test_local_and_ci_checks_share_scope_and_receipt_guards(self):
        planning = {name: g.read_text(ROOT, name) for name in (*m.PLANNING_PATHS, m.ENTRY)}
        contract, _ = m.validate_planning(planning)
        with tempfile.TemporaryDirectory(prefix="p4mvp-candidate-") as directory:
            root = Path(directory)
            g.git(root, "init", "--initial-branch=" + m.BRANCH)
            g.git(root, "config", "core.autocrlf", "true")
            g.git(root, "-c", "protocol.file.allow=always", "fetch", "--no-tags", str(ROOT), m.BASE)
            g.git(root, "checkout", "-B", m.BRANCH, m.BASE)

            def write(name, text):
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(text.encode("utf-8"))

            for name, text in planning.items():
                write(name, text)
            for name in set(contract["new_paths"]) - set(m.RECEIPTS) - planning.keys():
                write(name, "Authored candidate fixture.\n")
            g.git(root, "add", "--", ".")
            approved = m.snapshot(root)
            self.assertEqual(approved, m.snapshot(root, ci=True))
            self.assertEqual(set(approved["protected_file_hashes"]),
                             (m.baseline_entries(root).keys() | set(contract["new_paths"])) - set(m.RECEIPTS))
            with self.assertRaisesRegex(m.Block, "missing required file"):
                m.gate(root, ci=True)

            for name, text in authored_receipts(approved["snapshot_id"]).items():
                write(name, text.replace("\n", "\r\n"))
            g.git(root, "add", "--", *m.RECEIPTS)
            self.assertEqual(m.snapshot(root), approved)
            self.assertEqual(m.gate(root), g.LOCAL_RELEASE)
            g.git(root, "symbolic-ref", "HEAD", "refs/heads/wrong-branch")
            g.git(root, "update-ref", "HEAD", m.BASE)
            with self.assertRaisesRegex(m.Block, "Git branch"):
                m.gate(root)
            self.assertEqual(m.gate(root, ci=True), g.LOCAL_RELEASE)

            protected = "docs/P4MVP_READINESS.md"
            original = g.read_text(root, protected)
            write(protected, original + "\n")
            with self.assertRaisesRegex(m.Block, "index/worktree mismatch"):
                m.gate(root, ci=True)
            g.git(root, "add", "--", protected)
            self.assertNotEqual(m.snapshot(root, ci=True)["snapshot_id"], approved["snapshot_id"])
            with self.assertRaisesRegex(m.Block, "snapshot_id"):
                m.gate(root, ci=True)
            write(protected, original)
            g.git(root, "add", "--", protected)

            history = "docs/reviews/p4t-review.json"
            historical_bytes = (root / history).read_bytes()
            self.assertNotIn(b"\r", historical_bytes)
            original = historical_bytes.decode("utf-8")
            write(history, original.replace("\n", "\r\n"))
            with self.assertRaisesRegex(m.Block, "immutable merged bytes changed"):
                m.gate(root, ci=True)
            write(history, original)

            write(m.POLICY, planning[m.POLICY].replace("external_data_access_allowed = false",
                                                      "external_data_access_allowed = true"))
            with self.assertRaisesRegex(m.Block, "frozen planning snapshot"):
                m.gate(root, ci=True)
            write(m.POLICY, planning[m.POLICY])
            write("unexpected.txt", "Out of scope.\n")
            with self.assertRaisesRegex(m.Block, "out-of-scope"):
                m.gate(root, ci=True)


if __name__ == "__main__":
    unittest.main()
