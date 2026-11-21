"""Offline P4CORE scope, local attestation and historical closure tests."""

from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

import p4d_fixture
import p4t_merged_fixture


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("p4core_governance_tests", ROOT / "tools/governance_core.py")
c = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(c)
g = c.g


def json_text(value):
    return json.dumps(value, sort_keys=True) + "\n"


def authored_receipts(digest, builders):
    common = {"schema_version": 1, "package": "P4CORE", "snapshot_id": digest}
    validation = json_text({
        **common, "role": "builder_validation", "verdict": "PASS", "live_requests": 0,
        "results": {
            "commands": [{"command": name, "exit_code": 0} for name in c.VALIDATION_COMMANDS],
            "counts": {"product_collected": 947, "product_passed": 946,
                       "product_windows_symlink_skip": 1, "core_governance": 12,
                       "historical_mvp_governance": 13, "governance_collected": 263},
            "builder_agents": builders,
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
    return {c.VALIDATION: validation, c.REVIEW: review, c.RELEASE: release}


class PlanningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.planning = {name: g.read_text(ROOT, name) for name in (*c.PLANNING_PATHS, c.ENTRY)}

    def test_pinned_contract_and_entry(self):
        contract, state = c.validate_planning(self.planning)
        self.assertEqual((contract["base"], contract["branch"]), (c.BASE, c.BRANCH))
        self.assertEqual(tuple(contract["receipt_paths"]), c.RECEIPTS)
        self.assertEqual(state["dependency"]["merge_commit"], c.BASE)
        self.assertEqual(state["live_market_coverage"], "0/3_core")

    def test_every_planning_input_is_frozen(self):
        for name in c.PLANNING_PATHS:
            changed = {**self.planning, name: self.planning[name] + "\n"}
            entry = g.parse_json(changed[c.ENTRY])
            entry["planning_snapshot"] = g.snapshot_for_paths(changed, c.PLANNING_PATHS)[0]
            changed[c.ENTRY] = json_text(entry)
            with self.subTest(path=name), self.assertRaisesRegex(c.Block, "frozen planning snapshot"):
                c.validate_planning(changed)

    def test_entry_receipt_requires_both_passes_and_exact_scope(self):
        entry = g.parse_json(self.planning[c.ENTRY])
        for key, value in (("entry_verdict", "BLOCK"), ("prompt_verdict", "BLOCK"),
                           ("agent_id", "coordinator"), ("read_only", 1), ("scope", "live"),
                           ("findings", ["unresolved"]), ("new_field", False)):
            changed = {**self.planning, c.ENTRY: json_text({**entry, key: value})}
            with self.subTest(field=key), self.assertRaisesRegex(c.Block, "entry and prompt receipt"):
                c.validate_planning(changed)

    def test_p4d_fixture_materializes_the_pr14_blueprint(self):
        name = "BLUEPRINT.md"
        expected = g.git_raw(ROOT, "show", f"{p4t_merged_fixture.MERGED_P4T}:{name}")
        self.assertEqual((p4d_fixture.view() / name).read_bytes(), expected)

    def test_workflow_retains_pinned_jobs_suites_and_historical_refusals(self):
        path = ".github/workflows/governance.yml"
        historical = g.git(ROOT, "show", f"{c.BASE}:{path}")
        expected = historical.replace(
            "      - name: Scope and canonical consistency\n",
            "      - name: Smoke test the offline A-share core preflight\n"
            "        run: python -B -m market_intelligence.mvp_core preflight\n"
            "      - name: Scope and canonical consistency\n",
        ).replace("tools/governance_mvp.py", "tools/governance_core.py")
        self.assertEqual(g.read_text(ROOT, path), expected)


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.digest = "b" * 64
        self.builders = [str(uuid4()) for _ in range(2)]
        self.receipts = authored_receipts(self.digest, self.builders)
        self.results = g.parse_json(self.receipts[c.VALIDATION])["results"]

    def validate(self, receipts):
        c.validate_receipts({"builder_agents": ["coordinator"]}, receipts, self.digest)

    def test_receipt_chain_is_local_and_uses_normalized_hashes(self):
        self.validate(self.receipts)
        self.validate({name: text.replace("\n", "\r\n") for name, text in self.receipts.items()})
        for path in (c.VALIDATION, c.REVIEW):
            with self.subTest(path=path), self.assertRaisesRegex(c.Block, "sha256"):
                self.validate({**self.receipts, path: self.receipts[path] + "\n"})
        for path, key, value in (
            (c.VALIDATION, "live_requests", 1), (c.VALIDATION, "live_requests", False),
            (c.VALIDATION, "package", "P4MVP"), (c.REVIEW, "snapshot_id", "a" * 64),
            (c.REVIEW, "findings", ["BLOCK"]), (c.REVIEW, "read_only", 1),
            (c.RELEASE, "remote_ci", "PASS"), (c.RELEASE, "human_merge_required", False),
            (c.RELEASE, "runtime_allowed", False),
        ):
            receipt = g.parse_json(self.receipts[path])
            with self.subTest(field=key), self.assertRaises(c.Block):
                self.validate({**self.receipts, path: json_text({**receipt, key: value})})

    def test_validation_requires_each_exact_successful_command(self):
        commands = self.results["commands"]
        c.validate_results({**self.results, "commands": list(reversed(commands))})
        for index, entry in enumerate(commands):
            with self.subTest(missing=entry["command"]), self.assertRaisesRegex(c.Block, "command names"):
                c.validate_results({**self.results, "commands": commands[:index] + commands[index + 1:]})
        for invalid in (1, False):
            with self.subTest(exit_code=invalid), self.assertRaisesRegex(c.Block, "exit code"):
                c.validate_results({**self.results, "commands": [
                    {**commands[0], "exit_code": invalid}, *commands[1:]]})
        with self.assertRaisesRegex(c.Block, "command names"):
            c.validate_results({**self.results, "commands": [*commands, commands[0]]})

    def test_validation_requires_exact_suite_floors_and_accounting(self):
        counts = self.results["counts"]
        self.assertEqual(c.VALIDATION_COUNT_MINIMA,
                         {**counts, "product_windows_symlink_skip": 0})
        c.validate_results({**self.results, "counts": {**counts, "product_windows_symlink_skip": 0,
                                                      "product_passed": counts["product_collected"]}})
        for name, minimum in c.VALIDATION_COUNT_MINIMA.items():
            missing = {key: value for key, value in counts.items() if key != name}
            with self.subTest(missing=name), self.assertRaisesRegex(c.Block, "validation counts"):
                c.validate_results({**self.results, "counts": missing})
            for invalid in (True, minimum - 1):
                with self.subTest(field=name, value=invalid), self.assertRaisesRegex(c.Block, name):
                    c.validate_results({**self.results, "counts": {**counts, name: invalid}})
        for changes, error in (({"extra_suite": 1}, "validation counts"),
                               ({"product_windows_symlink_skip": 0}, "accounting"),
                               ({"product_windows_symlink_skip": 2}, "at most one")):
            with self.subTest(changes=changes), self.assertRaisesRegex(c.Block, error):
                c.validate_results({**self.results, "counts": {**counts, **changes}})

    def test_validation_requires_two_distinct_native_builders(self):
        one, two = self.builders
        for actors in ([one], [one, two, str(uuid4())], [one, one], [one, "coordinator"],
                       [one, "00000000-0000-0000-0000-000000000000"]):
            with self.subTest(actors=actors), self.assertRaisesRegex(c.Block, "validation"):
                c.validate_results({**self.results, "builder_agents": actors})
        with self.assertRaisesRegex(c.Block, "independent"):
            self.validate(authored_receipts(self.digest, [one, c.AUDITOR]))

    def test_reviewer_and_release_checker_are_independent(self):
        review = g.parse_json(self.receipts[c.REVIEW])
        release = g.parse_json(self.receipts[c.RELEASE])
        for actor in (*self.builders, c.AUDITOR.upper(), "CoOrDiNaToR"):
            changed_review = json_text({**review, "agent_id": actor})
            changed_release = json_text({**release, "review_sha256": g.sha256(changed_review)})
            with self.subTest(review=actor), self.assertRaisesRegex(c.Block, "independent"):
                self.validate({**self.receipts, c.REVIEW: changed_review, c.RELEASE: changed_release})
            with self.subTest(release=actor), self.assertRaisesRegex(c.Block, "independent"):
                self.validate({**self.receipts, c.RELEASE: json_text({**release, "agent_id": actor})})
        with self.assertRaisesRegex(c.Block, "independent"):
            self.validate({**self.receipts, c.RELEASE: json_text({**release, "agent_id": review["agent_id"]})})

    def test_runtime_commands_refuse_before_scope_or_repository_access(self):
        for command in c.CLOSED_COMMANDS:
            for flags in ([], ["--ci"]):
                output, error = io.StringIO(), io.StringIO()
                with self.subTest(command=command, flags=flags), redirect_stdout(output), redirect_stderr(error), \
                        patch.object(c, "check", side_effect=AssertionError("scope check")), \
                        patch.object(g, "git_raw", side_effect=AssertionError("Git access")), \
                        patch.object(g, "read_text", side_effect=AssertionError("file read")):
                    self.assertEqual(c.main([command, *flags], root=ROOT), 1)
                self.assertEqual(output.getvalue(), "")
                self.assertIn("--ci is not runtime authorization", error.getvalue())


class CandidateTests(unittest.TestCase):
    def test_local_and_ci_checks_bind_scope_history_and_receipts(self):
        planning = {name: g.read_text(ROOT, name) for name in (*c.PLANNING_PATHS, c.ENTRY)}
        contract, _ = c.validate_planning(planning)
        with tempfile.TemporaryDirectory(prefix="p4core-candidate-") as directory:
            root = Path(directory)
            g.git(root, "init", "--initial-branch=" + c.BRANCH)
            g.git(root, "config", "core.autocrlf", "true")
            g.git(root, "-c", "protocol.file.allow=always", "fetch", "--no-tags", str(ROOT), c.BASE)
            g.git(root, "checkout", "-B", c.BRANCH, c.BASE)

            def write(name, text):
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(text.encode("utf-8"))

            for name, text in planning.items():
                write(name, text)
            for name in set(contract["new_paths"]) - set(c.RECEIPTS) - planning.keys():
                write(name, "Authored candidate fixture.\n")
            g.git(root, "add", "--", ".")
            approved = c.snapshot(root)
            self.assertEqual(approved, c.snapshot(root, ci=True))
            self.assertEqual(set(approved["protected_file_hashes"]),
                             (c.baseline_entries(root).keys() | set(contract["new_paths"])) - set(c.RECEIPTS))
            with self.assertRaisesRegex(c.Block, "missing required file"):
                c.gate(root, ci=True)

            for name, text in authored_receipts(approved["snapshot_id"], [str(uuid4()), str(uuid4())]).items():
                write(name, text.replace("\n", "\r\n"))
            g.git(root, "add", "--", *c.RECEIPTS)
            self.assertEqual(c.snapshot(root), approved)
            self.assertEqual(c.gate(root), "LOCAL_READY_FOR_PR")
            g.git(root, "symbolic-ref", "HEAD", "refs/heads/wrong-branch")
            g.git(root, "update-ref", "HEAD", c.BASE)
            with self.assertRaisesRegex(c.Block, "Git branch"):
                c.gate(root)
            self.assertEqual(c.gate(root, ci=True), "LOCAL_READY_FOR_PR")

            protected = "docs/P4CORE_ACCEPTANCE.md"
            write(protected, "Changed candidate fixture.\n")
            with self.assertRaisesRegex(c.Block, "index/worktree mismatch"):
                c.gate(root, ci=True)
            g.git(root, "add", "--", protected)
            self.assertNotEqual(c.snapshot(root, ci=True)["snapshot_id"], approved["snapshot_id"])
            with self.assertRaisesRegex(c.Block, "snapshot_id"):
                c.gate(root, ci=True)
            write(protected, "Authored candidate fixture.\n")
            g.git(root, "add", "--", protected)

            historical = "docs/reviews/p4mvp-review.json"
            original = g.read_text(root, historical)
            write(historical, original.replace("\n", "\r\n"))
            with self.assertRaisesRegex(c.Block, "immutable merged bytes changed"):
                c.gate(root, ci=True)
            write(historical, original + "\n")
            g.git(root, "add", "--", historical)
            with self.assertRaisesRegex(c.Block, "Git patch changes an immutable merged path"):
                c.gate(root, ci=True)
            write(historical, original)
            g.git(root, "add", "--", historical)

            write("unexpected.txt", "Out of scope.\n")
            with self.assertRaisesRegex(c.Block, "out-of-scope"):
                c.gate(root, ci=True)


if __name__ == "__main__":
    unittest.main()
