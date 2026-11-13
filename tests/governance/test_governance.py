"""Offline P4 regression tests; all mutations and commits use temporary repositories."""

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import tomllib
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("p4_governance", ROOT / "tools/governance.py")
g = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(g)


class BatchBlobTests(unittest.TestCase):
    def test_empty_batch_does_not_start_git(self):
        with patch.object(g, "git_raw") as command:
            self.assertEqual(g.read_blobs(ROOT, []), {})
            command.assert_not_called()

    def test_batch_byte_lengths_deduplication_and_unicode(self):
        first, second = "a" * 40, "b" * 40
        text = "line\r\n\uac00\u4e2d\n"
        body = text.encode("utf-8")
        raw = (f"{first} blob {len(body)}\n".encode() + body + b"\n" +
               f"{second} blob 0\n\n".encode())
        with patch.object(g, "git_raw", return_value=raw) as command:
            self.assertEqual(g.read_blobs(ROOT, [second, first, first]),
                             {first: text.replace("\r\n", "\n"), second: ""})
            command.assert_called_once_with(ROOT, "cat-file", "--batch",
                                            input_bytes=f"{first}\n{second}\n".encode())

    def test_malformed_batch_is_rejected(self):
        oid = "a" * 40
        cases = [b"", f"{oid} missing\n".encode(),
                 f"{oid} tree 0\n\n".encode(),
                 f"{'b' * 40} blob 0\n\n".encode(),
                 f"{oid} blob -1\n\n".encode(),
                 f"{oid} blob 4\nx\n".encode(),
                 f"{oid} blob 0\n\nextra".encode(),
                 f"{oid} blob 1\n".encode() + b"\xff\n"]
        for raw in cases:
            with self.subTest(raw=raw), patch.object(g, "git_raw", return_value=raw):
                with self.assertRaises(ValueError):
                    g.read_blobs(ROOT, [oid])
        with self.assertRaises(g.Block):
            g.read_blobs(ROOT, ["bad\nrequest"])


class RepositoryBindingTests(unittest.TestCase):
    def setUp(self):
        self.contract = tomllib.loads((ROOT / g.CONTRACT_PATH).read_text(encoding="utf-8"))
        self.state = json.loads((ROOT / "project-state/state.json").read_text(encoding="utf-8"))
        self.policy = tomllib.loads((ROOT / "governance/policy.toml").read_text(encoding="utf-8"))

    def test_public_repository_and_merged_dependency_are_consistent(self):
        expected = {"owner": "kamrean", "name": "a-share-market-intelligence-public",
                    "visibility": "public"}
        self.assertEqual(self.contract["repository"], expected)
        self.assertEqual(self.policy["repository"], expected)
        self.assertEqual(self.contract["dependency"], g.dependency())
        self.assertEqual(self.state["dependency"], g.dependency())
        self.assertEqual(g.BASELINE, "9976a304c23be444093eaeaaf1277a2c846aed4d")
        self.assertEqual(g.BRANCH, "stage7-report-ledger")
        self.assertNotIn("codex", g.BRANCH)
        g.validate_contract(self.contract)
        g.exact(self.policy, g.POLICY, "policy")
        g.validate_state(self.state)
        self.assertIs(self.state["github"]["remote_creation_authorized"], False)
        self.assertEqual(self.state["stage_registry"][0]["status"], "complete")

    def test_p4_scope_keeps_historical_packages_immutable(self):
        self.assertEqual((len(g.ALLOWED), len(g.IMMUTABLE), len(g.PROTECTED)), (104, 77, 100))
        self.assertEqual(tuple(self.contract["allowed_paths"]), g.ALLOWED)
        self.assertEqual(tuple(self.contract["immutable_paths"]), g.IMMUTABLE)
        self.assertEqual(tuple(self.contract["attestation_paths"]), g.ATTESTATIONS)
        self.assertEqual(set(g.ALLOWED) - set(g.PROTECTED), set(g.ATTESTATIONS))
        self.assertEqual(g.ATTESTATIONS, (
            "docs/reviews/p4-bundle.json", "docs/reviews/p4-validation.json",
            "docs/reviews/p4-review.json", "docs/reviews/p4-release.json"))
        for package in ("p0", "p1", "p2", "p3"):
            for receipt in ("builder-prompt.md", "bundle.json", "prompt-audit.json",
                            "release.json", "review.json", "validation.json"):
                name = f"docs/reviews/{package}-{receipt}"
                self.assertIn(name, g.IMMUTABLE)
                self.assertIn(name, g.PROTECTED)
                self.assertNotIn(name, g.ATTESTATIONS)
        for name in ("docs/reviews/p0-merge.json", "docs/reviews/p1-merge.json",
                     "docs/reviews/p2-merge.json",
                     "tests/product/test_p1.py", "tests/product/test_p2.py",
                     "tests/product/test_p3.py", "contracts/p3_research.toml",
                     "templates/p3_builder_prompt.md", "docs/evidence/p3-demo.json",
                     "contracts/p2_intelligence.toml", "templates/p2_builder_prompt.md",
                     "docs/evidence/p2-demo.json"):
            self.assertIn(name, g.IMMUTABLE)
        self.assertIn("docs/reviews/p3-merge.json", g.PROTECTED)
        self.assertNotIn("docs/reviews/p3-merge.json", g.IMMUTABLE)

    def test_p3_merge_identity_and_ci_are_exact(self):
        self.assertEqual(g.dependency(), {
            "package_id": "P3",
            "pr_url": "https://github.com/kamrean/a-share-market-intelligence-public/pull/4",
            "merge_commit": "9976a304c23be444093eaeaaf1277a2c846aed4d",
            "merged_at": "2026-11-13T12:46:44Z",
            "tested_head": "e56c230a4b37ace4a6d12c53bad4e7463e88ebc3",
            "pr_ci_run": 43424527859, "merge_ci_run": 43428054869,
            "ci_status": "PASS", "merge_status": "merged",
        })

    def test_stage_history_and_source_model_gaps_cannot_advance(self):
        for stage in (3, 4):
            self.assertEqual(self.state["stage_registry"][stage], {
                "stage": stage, "package": "P2", "status": "engineering_merged_live_gaps"})
        for stage in (5, 6):
            self.assertEqual(self.state["stage_registry"][stage], {
                "stage": stage, "package": "P3", "status": "engineering_merged_live_gaps"})
        for next_gate in ("exact_head_ci_and_independent_release_then_human_merge",
                          g.NEXT_GATE + "; source gaps remain; no P3 before its own contract",
                          "start_P5", "source/model gaps resolved"):
            with self.subTest(next_gate=next_gate), self.assertRaises(g.Block):
                g.validate_state({**self.state, "next_gate": next_gate})
        for active_package in ("P2", "P3", "P5"):
            with self.assertRaises(g.Block):
                g.validate_state({**self.state, "active_package": active_package})

    def test_contract_and_policy_reject_repository_drift(self):
        for key, bad in (("owner", "another-owner"), ("name", "a-share-sentinel"),
                         ("visibility", "private"), ("allow_force_push", True)):
            with self.subTest(field=key):
                contract = deepcopy(self.contract)
                contract["repository"][key] = bad
                with self.assertRaises(g.Block):
                    g.validate_contract(contract)
                policy = deepcopy(self.policy)
                policy["repository"][key] = bad
                with self.assertRaises(g.Block):
                    g.exact(policy, g.POLICY, "policy")
        for key in self.contract["repository"]:
            contract = deepcopy(self.contract)
            del contract["repository"][key]
            with self.assertRaises(g.Block):
                g.validate_contract(contract)
        for key, bad in (("merge_commit", "0" * 40), ("ci_status", "PENDING"),
                         ("merge_status", "not_merged"), ("merge_ci_run", 0),
                         ("package_id", "P1"), ("tested_head", "0" * 40),
                         ("pr_ci_run", 43363252300), ("pr_ci_run", True)):
            contract = deepcopy(self.contract)
            contract["dependency"][key] = bad
            with self.assertRaises(g.Block):
                g.validate_contract(contract)

    def test_p4_stage7_and_partial_stage8_lifecycle_binding(self):
        for status, stage7, stage8 in (
            ("building", "in_progress", "offline_subset_in_progress"),
            ("implemented_unreleased", "implemented_unreleased",
             "offline_subset_implemented_unreleased"),
        ):
            state = deepcopy(self.state)
            state["package_status"] = status
            state["stage_registry"][7]["status"] = stage7
            state["stage_registry"][8]["status"] = stage8
            g.validate_state(state)
            for stage in (5, 6, 7, 8):
                for bad in ("complete", "planned", "implemented_unreleased" if stage != 7 else
                            "offline_subset_implemented_unreleased"):
                    changed = deepcopy(state)
                    changed["stage_registry"][stage]["status"] = bad
                    with self.subTest(status=status, stage=stage, bad=bad), self.assertRaises(g.Block):
                        g.validate_state(changed)

    def test_p4_paths_and_next_gate_are_exact(self):
        self.assertEqual(g.CONTRACT_PATH, "contracts/p4_publication.toml")
        self.assertEqual(g.TEMPLATE_PATH, "templates/p4_builder_prompt.md")
        self.assertEqual(g.PROMPT_PATH, "docs/reviews/p4-builder-prompt.md")
        self.assertEqual(g.NEXT_GATE,
                         "exact_head_ci_and_independent_release_then_human_merge; "
                         "live integration and MVP acceptance require reviewed scope; no VPS")
        self.assertEqual(self.state["next_gate"], g.NEXT_GATE)
        for field, bad in (("active_contract", "contracts/p3_research.toml"),
                           ("baseline", "e61f1cbfc41118deb13df421b927ace3d01c150e"),
                           ("branch", "stage5-6-research-foundation")):
            with self.subTest(field=field), self.assertRaises(g.Block):
                g.validate_state({**self.state, field: bad})
        for field, bad in (("package_id", "P3"), ("stage_ids", [5, 6]),
                           ("attestation_paths", [name.replace("p4-", "p3-") for name in g.ATTESTATIONS])):
            with self.subTest(field=field), self.assertRaises(g.Block):
                g.validate_contract({**self.contract, field: bad})

    def test_p4_samples_are_required_protected_snapshot_inputs(self):
        contents = dict.fromkeys(g.PROTECTED, "offline snapshot fixture\n")
        original = g.snapshot(contents)[0]
        for name in ("docs/evidence/p4-demo.json", "docs/evidence/p4-report.txt"):
            self.assertIn(name, self.contract["allowed_paths"])
            self.assertIn(name, g.PROTECTED)
            self.assertNotIn(name, g.ATTESTATIONS)
            self.assertNotIn(name, g.IMMUTABLE)
            self.assertNotEqual(original, g.snapshot({**contents, name: "changed sample\n"})[0])
            with patch.object(Path, "is_file", return_value=False):
                with self.assertRaisesRegex(g.Block, "missing required file"):
                    g.read_text(ROOT, name)

    def test_p4_actual_prerequisites_bind_current_inputs(self):
        for role, key, names in (
            ("blueprint_reviewer", "blueprint_review", ("BLUEPRINT.md", "START_CONSTRUCTION_PROMPT.md")),
            ("prompt_auditor", "prompt_audit", g.AUDIT_INPUTS),
        ):
            receipt = g.parse_json(g.read_text(ROOT, g.RECEIPT_PATHS[key]))
            contents = {name: g.read_text(ROOT, name) for name in names}
            g.validate_prerequisite(receipt, role, names, contents)
            self.assertNotIn(receipt["agent_id"], self.state["builder_agents"])
            self.assertEqual(receipt["agent_id"], receipt["agent_id"].strip())
            changed = {**contents, names[-1]: contents[names[-1]] + "changed\n"}
            with self.assertRaisesRegex(g.Block, "files"):
                g.validate_prerequisite(receipt, role, names, changed)

    def test_p4_permissions_and_acceptance_gaps_remain_closed(self):
        expected = {name: name == "research_only" for name in g.PERMISSIONS}
        self.assertEqual(self.contract["permissions"], expected)
        self.assertEqual(self.policy["permissions"], expected)
        for key in ("research_mvp", "forward_observation", "vps_release"):
            state = deepcopy(self.state)
            state["milestones"][key] = "complete"
            with self.subTest(milestone=key), self.assertRaises(g.Block):
                g.validate_state(state)
        for name in expected:
            contract, policy = deepcopy(self.contract), deepcopy(self.policy)
            contract["permissions"][name] = not expected[name]
            policy["permissions"][name] = not expected[name]
            with self.subTest(permission=name), self.assertRaises(g.Block):
                g.validate_contract(contract)
            with self.assertRaises(g.Block):
                g.exact(policy, g.POLICY, "policy")

    def test_p1_repair_exception_is_not_inherited(self):
        self.assertEqual(self.contract["max_repair_cycles"], 1)
        self.assertEqual(self.policy["max_repair_cycles"], 1)
        self.assertNotIn("repair_exception", self.contract)
        self.assertNotIn("repair_exception", self.policy)
        for value in (2, True, "unlimited"):
            contract, policy = deepcopy(self.contract), deepcopy(self.policy)
            contract["max_repair_cycles"] = value
            policy["max_repair_cycles"] = value
            with self.assertRaises(g.Block):
                g.validate_contract(contract)
            with self.assertRaises(g.Block):
                g.exact(policy, g.POLICY, "policy")
        for value in ({}, {"additional_cycles": 1}):
            with self.assertRaises(g.Block):
                g.validate_contract({**self.contract, "repair_exception": value})
            with self.assertRaises(g.Block):
                g.exact({**self.policy, "repair_exception": value}, g.POLICY, "policy")

    def test_state_rejects_expanded_remote_or_source_claims(self):
        for key, bad in (
            ("remote_creation_authorized", True), ("remote_creation_authorized", 0),
            ("owner", None), ("repository", "a-share-sentinel"), ("visibility", "private"),
            ("remote_ci", "NOT_RUN_NO_REMOTE"), ("remote_ci", "PASS"),
            ("merge_status", "merged"), ("direct_push_to_main", True),
        ):
            state = deepcopy(self.state)
            state["github"][key] = bad
            with self.subTest(field=key, value=bad), self.assertRaises(g.Block):
                g.validate_state(state)
        with self.assertRaises(g.Block):
            g.validate_state({**self.state, "source_readiness": "PASS"})
        with self.assertRaises(g.Block):
            g.exact({**self.policy, "product_runtime_implemented": True}, g.POLICY, "policy")


class GovernanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_contract = (ROOT / g.CONTRACT_PATH).read_text(encoding="utf-8")
        cls.contract = tomllib.loads(cls.original_contract)
        cls.source_inputs = {
            name: (ROOT / name).read_text(encoding="utf-8")
            for name in g.PROTECTED
        }

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="p4-governance-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.run_git("init", "--initial-branch=" + g.BRANCH)
        self.run_git("config", "user.name", "Offline Fixture")
        self.run_git("config", "user.email", "fixture@example.invalid")
        self.run_git("config", "commit.gpgsign", "false")
        self.run_git("config", "core.autocrlf", "false")
        for name in g.IMMUTABLE:
            self.write(name, self.source_inputs[name])
        self.write("START_CONSTRUCTION_PROMPT.md", self.source_inputs["START_CONSTRUCTION_PROMPT.md"])
        self.run_git("add", *g.IMMUTABLE, "START_CONSTRUCTION_PROMPT.md")
        self.run_git("commit", "-m", "temporary baseline")
        baseline = self.run_git("rev-parse", "HEAD").strip()
        self.addCleanup(patch.stopall)
        patch.object(g, "BASELINE", baseline).start()
        for name in g.PROTECTED:
            self.write(name, self.source_inputs.get(name, "Offline fixture.\n"))
        self.write(g.CONTRACT_PATH, self.original_contract.replace(self.contract["baseline"], baseline))
        self.write("governance/policy.toml", self.source_inputs["governance/policy.toml"]
                   .replace(self.contract["baseline"], baseline))
        # Only the new P3 merge observation points at this temporary baseline.
        # Historical P0/P1/P2/P3 contracts, products and receipts retain their bytes.
        prior = json.loads(self.source_inputs["docs/reviews/p3-merge.json"])
        self.write_json("docs/reviews/p3-merge.json", {**prior, "merge_commit": baseline})
        self.state = {
            "schema_version": 1, "active_package": "P4", "active_contract": g.CONTRACT_PATH,
            "branch": g.BRANCH, "baseline": baseline, "package_status": "implemented_unreleased",
            "builder_agents": ["coordinator", "implementation-builder"],
            "stage_registry": [
                {"stage": i, "package": package,
                 "status": "complete" if i in (0, 2) else "engineering_merged_source_gaps" if i == 1 else
                           "engineering_merged_live_gaps" if i in (3, 4, 5, 6) else "implemented_unreleased" if i == 7
                           else "deferred" if i == 9 else "offline_subset_implemented_unreleased"}
                for i, package in enumerate(("P0", "P1", "P1", "P2", "P2", "P3", "P3", "P4", "P4/P5", "P5"))
            ],
            "milestones": {"research_mvp": "not_implemented", "forward_observation": "not_started",
                           "vps_release": "not_authorized"},
            "source_readiness": "BLOCKED_MARKET_SOURCES", "dependency": g.dependency(),
            "github": {"remote_creation_authorized": False, "owner": "kamrean",
                       "repository": "a-share-market-intelligence-public",
                       "visibility": "public", "remote_ci": "NOT_RUN_AT_SNAPSHOT", "merge_status": "not_merged"},
            "receipts": g.RECEIPT_PATHS,
            "next_gate": g.NEXT_GATE,
            "last_updated": "2026-11-13",
        }
        self.write_json("project-state/state.json", self.state)
        contract = tomllib.loads(g.read_text(self.root, g.CONTRACT_PATH))
        self.write(g.PROMPT_PATH, g.render_prompt(contract, self.source_inputs[g.TEMPLATE_PATH]))
        for role, receipt_name, names in (
            ("blueprint_reviewer", "blueprint_review", ("BLUEPRINT.md", "START_CONSTRUCTION_PROMPT.md")),
            ("prompt_auditor", "prompt_audit", g.AUDIT_INPUTS),
        ):
            # Rebind copies of actual audits only inside the temporary fixture.
            # These altered hashes are test data, not new real-world approval.
            receipt = json.loads(self.source_inputs[g.RECEIPT_PATHS[receipt_name]])
            receipt["files"] = {name: g.sha256(g.read_text(self.root, name)) for name in names}
            if role == "blueprint_reviewer":
                receipt["baseline"] = baseline
            self.write_json(g.RECEIPT_PATHS[receipt_name], receipt)

    def run_git(self, *args):
        return g.git(self.root, *args)

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))

    def write_json(self, name, value):
        self.write(name, json.dumps(value, indent=2, sort_keys=True) + "\n")

    def load_json(self, name):
        return json.loads((self.root / name).read_text(encoding="utf-8"))

    def attest(self):
        self.run_git("add", ".")
        current = g.bundle(self.root)
        self.write_json(g.ATTESTATIONS[0], current)
        common = {"schema_version": 1, "snapshot_id": current["snapshot_id"]}
        self.write_json(g.ATTESTATIONS[1], {
            **common, "role": "builder_validation", "verdict": "PASS",
            "commands": [{"command": command, "exit_code": 0} for command in g.VALIDATION_COMMANDS],
            "python_version": "3.12.10", "remote_ci": "NOT_RUN_AT_SNAPSHOT",
        })
        self.write_json(g.ATTESTATIONS[2], {
            **common, "role": "reviewer", "verdict": "PASS", "agent_id": "independent-reviewer",
            "read_only": True, "findings": [], "repair_cycles": 0,
        })
        self.write_json(g.ATTESTATIONS[3], {
            **common, "role": "release_checker", "verdict": "LOCAL_READY_FOR_PR",
            "agent_id": "independent-release-checker", "read_only": True,
            "remote_ci": "NOT_RUN_AT_SNAPSHOT", "pr_url": None, "merge_status": "not_merged",
            "vps_sync": "not_requested", "next_gate": g.NEXT_GATE,
        })
        self.run_git("add", ".")
        return current

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            status = g.main(list(args), root=self.root)
        return status, out.getvalue(), err.getvalue()

    def test_contract_missing_and_unknown_fields(self):
        for field in self.contract:
            with self.subTest(missing=field):
                value = deepcopy(self.contract)
                del value[field]
                with self.assertRaises(g.Block):
                    g.validate_contract(value)
        value = tomllib.loads(g.read_text(self.root, g.CONTRACT_PATH))
        value["future_scope"] = True
        with self.assertRaisesRegex(g.Block, "unknown fields"):
            g.validate_contract(value)

    def test_contract_wrong_types_all_fields(self):
        contract = tomllib.loads(g.read_text(self.root, g.CONTRACT_PATH))
        for field in contract:
            with self.subTest(field=field):
                value = deepcopy(contract)
                value[field] = None
                with self.assertRaises(g.Block):
                    g.validate_contract(value)
        for field, bad in (("stage_ids", [False]), ("schema_version", True), ("max_repair_cycles", True)):
            value = deepcopy(contract)
            value[field] = bad
            with self.assertRaises(g.Block):
                g.validate_contract(value)

    def test_exact_scope_and_attestations(self):
        contract = tomllib.loads(g.read_text(self.root, g.CONTRACT_PATH))
        self.assertEqual(len(g.ALLOWED), 104)
        self.assertEqual(len(g.PROTECTED), 100)
        self.assertEqual(len(g.IMMUTABLE), 77)
        self.assertIn("docs/reviews/p0-review.json", g.PROTECTED)
        self.assertIn("docs/reviews/p0-review.json", g.IMMUTABLE)
        self.assertIn("docs/PUBLICATION_PROVENANCE.json", g.PROTECTED)
        for field, bad in (("allowed_paths", list(g.ALLOWED) + ["src/product.py"]),
                           ("attestation_paths", ["docs/"]), ("baseline", "0" * 40),
                           ("immutable_paths", list(g.IMMUTABLE[:-1])),
                           ("attestation_paths", [name.replace("p4-", "p3-") for name in g.ATTESTATIONS]),
                           ("branch", "main"), ("stage_ids", [1]),
                           ("required_validation", ["echo PASS"])):
            with self.subTest(field=field):
                value = deepcopy(contract)
                value[field] = bad
                with self.assertRaises(g.Block):
                    g.validate_contract(value)

    def test_permission_types_and_expansion(self):
        contract = tomllib.loads(g.read_text(self.root, g.CONTRACT_PATH))
        for field, expected in g.PERMISSIONS.items():
            for bad in (not expected, "false", None, 0, 1):
                with self.subTest(field=field, bad=bad):
                    value = deepcopy(contract)
                    value["permissions"][field] = bad
                    with self.assertRaises(g.Block):
                        g.validate_contract(value)
        for permissions in ({}, {**g.PERMISSIONS, "new_permission": False}):
            value = deepcopy(contract)
            value["permissions"] = permissions
            with self.assertRaises(g.Block):
                g.validate_contract(value)

    def test_policy_permissions_and_unknown_fields(self):
        policy = deepcopy(g.POLICY)
        for field in g.PERMISSIONS:
            bad = deepcopy(policy)
            bad["permissions"][field] = not bad["permissions"][field]
            with self.subTest(field=field), self.assertRaises(g.Block):
                g.exact(bad, g.POLICY, "policy")
        for field, value in (("remote_creation_authorized", True), ("remote_ci_status", "PASS"),
                             ("auto_merge_allowed", "false"), ("new_permission", False)):
            with self.assertRaises(g.Block):
                g.exact({**policy, field: value}, g.POLICY, "policy")

    def test_only_two_p4_lifecycle_states(self):
        g.validate_state(self.state)
        self.state["package_status"] = "building"
        self.state["stage_registry"][7]["status"] = "in_progress"
        self.state["stage_registry"][8]["status"] = "offline_subset_in_progress"
        self.write_json("project-state/state.json", self.state)
        g.check(self.root)
        self.attest()
        with self.assertRaisesRegex(g.Block, "still building"):
            g.gate(self.root)
        for bad in ("complete", "merged", None, True):
            self.state["package_status"] = bad
            with self.assertRaises(g.Block):
                g.validate_state(self.state)

    def test_registry_milestones_and_remote_claims(self):
        mutations = [
            ("stage_registry", []), ("builder_agents", []), ("branch", "main"),
            ("baseline", "0" * 40), ("active_package", "P3"), ("extra", True),
            ("milestones", {**self.state["milestones"], "research_mvp": "complete"}),
        ]
        for field, value in mutations:
            with self.subTest(field=field), self.assertRaises(g.Block):
                g.validate_state({**self.state, field: value})
        for stage in range(10):
            value = deepcopy(self.state)
            value["stage_registry"][stage]["status"] = "in_progress" if stage in (0, 2) else "complete"
            with self.assertRaises(g.Block):
                g.validate_state(value)
        for field, bad in (("remote_creation_authorized", 0), ("remote_ci", "PASS"),
                           ("merge_status", "merged"), ("owner", "someone")):
            value = deepcopy(self.state)
            value["github"][field] = bad
            with self.assertRaises(g.Block):
                g.validate_state(value)

    def test_check_and_bundle_need_no_post_review_receipts(self):
        self.assertEqual(self.cli("check"), (0, "PASS\n", ""))
        current = g.bundle(self.root)
        self.assertEqual(current["scope_result"], "PASS")
        self.assertEqual(set(current["protected_file_hashes"]), set(g.PROTECTED))
        self.assertIn(g.RECEIPT_PATHS["blueprint_review"], current["protected_file_hashes"])
        self.assertIn(g.RECEIPT_PATHS["prompt_audit"], current["protected_file_hashes"])
        with self.assertRaises(g.Block):
            g.gate(self.root)

    def test_prompt_exact_newline_and_verify(self):
        status, rendered, error = self.cli("prompt")
        self.assertEqual((status, error), (0, ""))
        self.assertTrue(rendered.endswith("\n"))
        self.assertFalse(rendered.endswith("\n\n"))
        self.assertEqual(rendered, g.read_text(self.root, g.PROMPT_PATH))
        self.assertEqual(self.cli("prompt", "--verify"), (0, "PASS\n", ""))
        self.write(g.PROMPT_PATH, rendered + "stale\n")
        self.assertEqual(self.cli("prompt", "--verify")[0], 1)
        with self.assertRaisesRegex(g.Block, "builder prompt"):
            g.check(self.root)

    def test_prompt_invalid_template(self):
        contract = tomllib.loads(g.read_text(self.root, g.CONTRACT_PATH))
        for template in ("no substitution", "$unknown", "$contract_json $unknown", "$contract_json $"):
            with self.assertRaises(g.Block):
                g.render_prompt(contract, template)

    def test_prerequisites_wrong_hash_names_or_verdict(self):
        for name in (g.RECEIPT_PATHS["blueprint_review"], g.RECEIPT_PATHS["prompt_audit"]):
            original = self.load_json(name)
            bad_hashes = dict(original["files"])
            bad_hashes[next(iter(bad_hashes))] = "0" * 64
            for field, value in (("files", {}), ("files", bad_hashes),
                                 ("files", {**original["files"], "README.md": "0" * 64}),
                                 ("verdict", "BLOCK"), ("read_only", "true"), ("independent", False)):
                with self.subTest(name=name, field=field):
                    self.write_json(name, {**original, field: value})
                    with self.assertRaises(g.Block):
                        g.check(self.root)
            self.write_json(name, original)

    def test_deterministic_bundle_and_lf_normalization(self):
        before = self.cli("bundle")
        self.assertEqual(before, self.cli("bundle"))
        for name in g.PROTECTED:
            self.write(name, g.read_text(self.root, name).replace("\n", "\r\n"))
        self.assertEqual(before, self.cli("bundle"))

    def test_every_protected_input_changes_snapshot(self):
        contents = g.check(self.root)[2]
        original, _hashes = g.snapshot(contents)
        for name in g.PROTECTED:
            with self.subTest(path=name):
                changed = {**contents, name: contents[name] + "changed\n"}
                self.assertNotEqual(original, g.snapshot(changed)[0])
        for name in g.ATTESTATIONS:
            self.assertEqual(original, g.snapshot({**contents, name: "unhashed receipt"})[0])

    def test_factual_price_and_volume_are_allowed(self):
        self.write("README.md", "Historical market price and traded volume are factual research data.\n")
        g.check(self.root)

    def test_secret_patterns_and_regex_fixtures(self):
        for text in self.source_inputs.values():
            g.scan_secrets("source fixture", text)
        secrets = ("ghp_" + "A" * 36, "AKIA" + "A" * 16, "sk-proj-" + "A" * 35,
                   "123456789:" + "A" * 35, "api_key=" + "A" * 30,
                   "-----BEGIN " + "PRIVATE KEY-----")
        for secret in secrets:
            with self.subTest(prefix=secret[:5]), self.assertRaisesRegex(g.Block, "possible secret"):
                g.scan_secrets("receipt", secret)

    def test_attestations_excluded_but_scanned(self):
        before = g.bundle(self.root)["snapshot_id"]
        for name in g.ATTESTATIONS:
            self.write_json(name, {"arbitrary": "non-secret pending receipt"})
        self.assertEqual(before, g.bundle(self.root)["snapshot_id"])
        for name in g.ATTESTATIONS:
            self.write_json(name, {"token": "ghp_" + "A" * 36})
            with self.assertRaisesRegex(g.Block, "possible secret"):
                g.check(self.root)
            self.write_json(name, {})

    def test_untracked_ignored_and_runtime_artifacts(self):
        for name in ("extra.txt", "src/collector.py", "tests/product/test_runtime.py", ".env",
                     "runtime/capture.json", "secrets/credentials.json", "docs/reviews/extra.json"):
            with self.subTest(path=name):
                self.write(name, "private artifact\n")
                with self.assertRaisesRegex(g.Block, "out-of-scope"):
                    g.check(self.root, ci=True)
                (self.root / name).unlink()

    def test_staged_and_committed_scope(self):
        self.write("src/collector.py", "product runtime\n")
        self.run_git("add", "src/collector.py")
        with self.assertRaisesRegex(g.Block, "out-of-scope"):
            g.check(self.root)
        self.run_git("commit", "-m", "temporary out-of-scope change")
        with self.assertRaisesRegex(g.Block, "out-of-scope"):
            g.check(self.root)
        (self.root / "src/collector.py").unlink()
        with self.assertRaisesRegex(g.Block, "out-of-scope"):
            g.check(self.root)

    def test_deleted_protected_inputs(self):
        for name in g.PROTECTED:
            original = (self.root / name).read_bytes()
            (self.root / name).unlink()
            with self.subTest(path=name), self.assertRaisesRegex(g.Block, "missing required file"):
                g.check(self.root)
            (self.root / name).write_bytes(original)

    def test_path_traversal_and_windows_unsafe_paths(self):
        for name in ("../secret", "/absolute", "C:/secret", "docs\\secret", "docs/../secret",
                     "docs//secret", "./README.md", "README.md.", "README.md ", "bad\npath"):
            with self.subTest(path=name), self.assertRaises(g.Block):
                g.safe_path(self.root, name)

    def test_symlink_escape_and_junction_are_rejected(self):
        with patch.object(Path, "is_symlink", return_value=True):
            with self.assertRaisesRegex(g.Block, "link/junction"):
                g.safe_path(self.root, "README.md")
        with patch.object(Path, "is_junction", return_value=True):
            with self.assertRaisesRegex(g.Block, "link/junction"):
                g.safe_path(self.root, "README.md")
        link = self.root / "outside-link"
        try:
            link.symlink_to(self.root.parent, target_is_directory=True)
        except OSError:
            return  # Windows may disallow symlink creation; both detection branches ran above.
        self.addCleanup(lambda: link.unlink(missing_ok=True))
        with self.assertRaisesRegex(g.Block, "link/junction"):
            g.safe_path(self.root, "outside-link/file")

    def test_only_known_ignored_python_caches_are_allowed(self):
        self.write("tools/__pycache__/governance.cpython-312.pyc", "synthetic bytecode\n")
        g.check(self.root)
        self.write("tools/__pycache__/credentials.pyc", "not a compiler output for this package\n")
        with self.assertRaisesRegex(g.Block, "out-of-scope"):
            g.check(self.root)

    def test_empty_directory_junction_cannot_hide_from_git(self):
        directory = (self.root / "empty-junction").resolve()
        alias = self.root / "root-alias"
        alias.mkdir()
        roots = (self.root, self.root.resolve(), alias / "..")
        # The checker resolves its root; compare the mock to that same identity.
        # GitHub Windows TEMP may spell the same directory with a short alias.
        original = Path.is_junction
        for root in roots:
            with self.subTest(root=str(root)):
                g.inspect_git(root, ci=True)
                directory.mkdir()
                try:
                    with patch.object(Path, "is_junction",
                                      lambda path: path == directory or original(path)):
                        with self.assertRaisesRegex(g.Block, "link/junction"):
                            g.inspect_git(root, ci=True)
                finally:
                    directory.rmdir()

    @unittest.skipUnless(os.name == "nt", "Windows junction regression")
    def test_real_empty_windows_junction_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="p0-junction-target-") as target:
            outside = Path(target).resolve()
            junction = self.root.resolve() / "real-empty-junction"
            result = subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(outside)],
                capture_output=True, text=True, errors="replace", check=False)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            try:
                self.assertTrue(junction.is_junction())
                with self.assertRaisesRegex(g.Block, "link/junction"):
                    g.inspect_git(self.root, ci=True)
            finally:
                if junction.is_junction():
                    junction.rmdir()
            self.assertTrue(outside.is_dir())

    def test_branch_and_detached_ci(self):
        self.run_git("symbolic-ref", "HEAD", "refs/heads/wrong-branch")
        # Restore a valid HEAD on a wrong branch without changing the working files.
        self.run_git("update-ref", "refs/heads/wrong-branch", g.BASELINE)
        with self.assertRaisesRegex(g.Block, "Git branch"):
            g.check(self.root)
        self.assertEqual(self.cli("check", "--ci")[0], 0)
        self.run_git("checkout", "--detach", g.BASELINE)
        self.assertEqual(self.cli("check", "--ci")[0], 0)
        self.assertEqual(self.cli("check")[0], 1)
        self.state["github"]["remote_ci"] = "PASS"
        self.write_json("project-state/state.json", self.state)
        self.assertEqual(self.cli("check", "--ci")[0], 1)

    def test_missing_baseline_and_non_ancestor_fail_even_ci(self):
        with patch.object(g, "BASELINE", "0" * 40), self.assertRaises(g.Block):
            g.inspect_git(self.root, ci=True)
        other = self.run_git("commit-tree", self.run_git("rev-parse", "HEAD^{tree}").strip(),
                             "-m", "unrelated temporary root").strip()
        self.run_git("update-ref", "HEAD", other)
        with self.assertRaisesRegex(g.Block, "merge-base"):
            g.inspect_git(self.root, ci=True)

    def test_gate_and_commit_do_not_stale_snapshot(self):
        before = self.attest()
        self.assertEqual(self.cli("gate"), (0, "LOCAL_READY_FOR_PR\n", ""))
        self.run_git("add", ".")
        self.run_git("commit", "-m", "temporary reviewed package")
        self.assertEqual(g.bundle(self.root)["snapshot_id"], before["snapshot_id"])
        self.assertEqual(g.gate(self.root), "LOCAL_READY_FOR_PR")
        self.run_git("checkout", "--detach", "HEAD")
        self.assertEqual(self.cli("gate", "--ci"), (0, "LOCAL_READY_FOR_PR\n", ""))

    def assert_candidate_blocked(self, message):
        for command in ("check", "bundle", "gate"):
            for flags in ((), ("--ci",)):
                with self.subTest(command=command, flags=flags):
                    status, out, error = self.cli(command, *flags)
                    self.assertEqual((status, out), (1, ""))
                    self.assertIn(message, error)
                    self.assertNotIn("Traceback", error)

    def test_staged_only_content_blocks_check_bundle_and_gate(self):
        approved = self.attest()
        text = g.read_text(self.root, "README.md")
        self.write("README.md", text + "Unapproved staged decision.\n")
        self.run_git("add", "README.md")
        self.write("README.md", text)
        contents = {name: g.read_text(self.root, name) for name in g.PROTECTED}
        self.assertEqual(g.snapshot(contents)[0], approved["snapshot_id"])
        self.assert_candidate_blocked("index/worktree mismatch: README.md")

    def test_staged_only_synthetic_secret_is_scanned(self):
        self.attest()
        text = g.read_text(self.root, "README.md")
        synthetic = "ghp_" + "A" * 36
        self.write("README.md", text + synthetic + "\n")
        self.run_git("add", "README.md")
        self.write("README.md", text)
        self.assert_candidate_blocked("possible secret in index:README.md")
        self.assertNotIn(synthetic, self.cli("gate")[2])

    def test_replacement_ref_cannot_substitute_approved_index_content(self):
        self.attest()
        approved = g.read_text(self.root, "README.md")
        good_blob = self.run_git("rev-parse", ":README.md").strip()
        altered = approved + "Unapproved staged decision.\n"
        self.write("README.md", altered)
        self.run_git("add", "README.md")
        bad_blob = self.run_git("rev-parse", ":README.md").strip()
        self.write("README.md", approved)
        self.run_git("replace", bad_blob, good_blob)
        self.assertEqual(self.run_git("rev-parse", "refs/replace/" + bad_blob).strip(), good_blob)
        self.assertEqual(self.run_git("cat-file", "blob", bad_blob), altered)
        self.assert_candidate_blocked("index/worktree mismatch: README.md")

    def test_committed_mismatch_cannot_hide_behind_approved_worktree(self):
        self.attest()
        approved = g.read_text(self.root, "README.md")
        altered = approved + "Unapproved committed decision.\n"
        self.write("README.md", altered)
        self.run_git("add", "README.md")
        self.write("README.md", approved)
        self.run_git("commit", "-m", "temporary mismatched candidate")
        self.assertEqual(self.run_git("show", "HEAD:README.md"), altered)
        self.assertEqual(g.read_text(self.root, "README.md"), approved)
        self.assert_candidate_blocked("index/worktree mismatch: README.md")

    def test_index_flags_cannot_mask_content_divergence(self):
        self.attest()
        approved = g.read_text(self.root, "README.md")
        self.write("README.md", approved + "Unapproved staged decision.\n")
        self.run_git("add", "README.md")
        self.write("README.md", approved)
        for flag in ("--assume-unchanged", "--skip-worktree"):
            self.run_git("update-index", flag, "README.md")
            self.assert_candidate_blocked("index/worktree mismatch: README.md")
            self.run_git("update-index", "--no-" + flag[2:], "README.md")

    def test_receipt_index_and_worktree_divergence_are_rejected(self):
        self.attest()
        for name in g.ATTESTATIONS:
            approved = g.read_text(self.root, name)
            with self.subTest(path=name, side="index"):
                self.write(name, approved + "\n")
                self.run_git("add", name)
                self.write(name, approved)
                self.assert_candidate_blocked("index/worktree mismatch: " + name)
            self.run_git("add", name)
            with self.subTest(path=name, side="worktree"):
                self.write(name, approved + "\n")
                self.assert_candidate_blocked("index/worktree mismatch: " + name)
            self.write(name, approved)

    def test_gate_requires_every_allowed_file_in_commit_candidate(self):
        self.attest()
        for name in g.ALLOWED:
            with self.subTest(path=name):
                self.run_git("update-index", "--force-remove", "--", name)
                status, out, error = self.cli("gate", "--ci")
                self.assertEqual((status, out), (1, ""))
                self.assertIn("missing commit candidate files", error)
                self.assertIn(name, error)
                self.run_git("add", name)

    def test_check_and_bundle_allow_new_untracked_files_but_gate_does_not(self):
        approved = self.attest()
        for name in ("README.md", g.ATTESTATIONS[3]):
            self.run_git("update-index", "--force-remove", "--", name)
            self.assertEqual(self.cli("check")[0], 0)
            self.assertEqual(g.bundle(self.root)["snapshot_id"], approved["snapshot_id"])
            self.assertEqual(self.cli("gate")[0], 1)
            self.run_git("add", name)
        self.assertEqual(g.gate(self.root), "LOCAL_READY_FOR_PR")

    def test_staged_deletion_of_head_input_or_receipt_is_rejected(self):
        self.attest()
        self.run_git("commit", "-m", "temporary complete package")
        for name in ("README.md", g.ATTESTATIONS[2]):
            self.run_git("update-index", "--force-remove", "--", name)
            self.assertTrue((self.root / name).is_file())
            self.assert_candidate_blocked("missing commit candidate files")
            self.run_git("add", name)

    def test_index_file_types_and_invalid_utf8_are_rejected(self):
        self.attest()
        approved = g.read_text(self.root, "README.md")
        blob = self.run_git("rev-parse", ":README.md").strip()
        head = self.run_git("rev-parse", "HEAD").strip()
        for mode, oid in (("120000", blob), ("160000", head)):
            self.run_git("update-index", "--cacheinfo", f"{mode},{oid},README.md")
            self.assert_candidate_blocked("index: non-regular file: README.md")
            self.run_git("update-index", "--cacheinfo", f"100644,{blob},README.md")
        (self.root / "README.md").write_bytes(b"\xff")
        self.run_git("add", "README.md")
        self.write("README.md", approved)
        self.assert_candidate_blocked("utf-8")

    def test_intent_to_add_is_not_a_complete_commit_candidate(self):
        self.write("README.md", "")
        self.attest()
        self.run_git("update-index", "--force-remove", "--", "README.md")
        self.run_git("add", "--intent-to-add", "README.md")
        self.assertIn("README.md", self.run_git("ls-files", "--stage"))
        self.assert_candidate_blocked("index: intent-to-add entry: README.md")

    def test_matching_staging_and_commit_keep_snapshot_with_mixed_newlines(self):
        approved = self.attest()
        for name in g.ALLOWED:
            self.write(name, g.read_text(self.root, name).replace("\n", "\r\n"))
        self.assertEqual(g.bundle(self.root)["snapshot_id"], approved["snapshot_id"])
        self.assertEqual(g.gate(self.root), "LOCAL_READY_FOR_PR")
        self.run_git("add", ".")
        raw_crlf = self.run_git("hash-object", "-w", "--no-filters", "README.md").strip()
        self.run_git("update-index", "--cacheinfo", f"100644,{raw_crlf},README.md")
        self.write("README.md", g.read_text(self.root, "README.md"))
        self.assertIn("\r\n", self.run_git("cat-file", "blob", raw_crlf))
        self.assertEqual(g.gate(self.root), "LOCAL_READY_FOR_PR")
        self.run_git("commit", "-m", "temporary normalized matching package")
        self.assertEqual(g.bundle(self.root)["snapshot_id"], approved["snapshot_id"])
        self.assertEqual(g.gate(self.root), "LOCAL_READY_FOR_PR")
        self.run_git("checkout", "--detach", "HEAD")
        self.assertEqual(self.cli("gate", "--ci"), (0, "LOCAL_READY_FOR_PR\n", ""))

    def test_stale_snapshot_and_hash_receipts(self):
        self.attest()
        self.write("docs/CONSTRUCTION_LOG.md", "decision changed after review\n")
        self.run_git("add", "docs/CONSTRUCTION_LOG.md")
        with self.assertRaisesRegex(g.Block, "bundle receipt"):
            g.gate(self.root)
        self.attest()
        saved = self.load_json(g.ATTESTATIONS[0])
        saved["protected_file_hashes"]["README.md"] = "0" * 64
        self.write_json(g.ATTESTATIONS[0], saved)
        self.run_git("add", g.ATTESTATIONS[0])
        with self.assertRaisesRegex(g.Block, "protected_file_hashes"):
            g.gate(self.root)

    def test_missing_block_wrong_and_self_review_receipts(self):
        self.attest()
        checked = g.check(self.root)
        current = g.make_bundle(checked)
        for name in g.ATTESTATIONS:
            with self.subTest(missing=name):
                contents = dict(checked[2])
                del contents[name]
                with self.assertRaises(g.Block):
                    g.validate_attestations((*checked[:2], contents, checked[3]), current)
        mutations = {
            g.ATTESTATIONS[1]: [("verdict", "BLOCK"), ("remote_ci", "PASS"),
                                ("remote_ci", "NOT_RUN_NO_REMOTE"), ("commands", []),
                                ("python_version", "3.12"), ("snapshot_id", "0" * 64)],
            g.ATTESTATIONS[2]: [("verdict", "BLOCK"), ("agent_id", "implementation-builder"),
                                ("read_only", "true"), ("repair_cycles", 2), ("repair_cycles", 3), ("repair_cycles", True),
                                ("repair_cycles", -1), ("repair_cycles", "0"), ("findings", ["unresolved"]),
                                ("snapshot_id", "0" * 64), ("permissions", g.PERMISSIONS)],
            g.ATTESTATIONS[3]: [("verdict", "PASS"), ("remote_ci", "PASS"),
                                ("verdict", "LOCAL_READY_AWAITING_REMOTE"),
                                ("remote_ci", "NOT_RUN_NO_REMOTE"), ("agent_id", "coordinator"),
                                ("agent_id", "independent-reviewer"),
                                ("pr_url", "https://example.invalid/pr/1"), ("merge_status", "merged"),
                                ("vps_sync", "done"), ("read_only", 1), ("snapshot_id", "0" * 64),
                                ("next_gate", "start_P1")],
        }
        for name, cases in mutations.items():
            for field, bad in cases:
                with self.subTest(path=name, field=field, value=bad):
                    receipt = json.loads(checked[2][name])
                    receipt[field] = bad
                    contents = {**checked[2], name: json.dumps(receipt)}
                    with self.assertRaises(g.Block):
                        g.validate_attestations((*checked[:2], contents, checked[3]), current)

    def test_validation_commands_exact_and_exit_codes_not_boolean(self):
        self.attest()
        checked = g.check(self.root)
        current = g.make_bundle(checked)
        original = json.loads(checked[2][g.ATTESTATIONS[1]])
        for bad in (1, False, "0", None):
            value = deepcopy(original)
            value["commands"][0]["exit_code"] = bad
            contents = {**checked[2], g.ATTESTATIONS[1]: json.dumps(value)}
            with self.assertRaises(g.Block):
                g.validate_attestations((*checked[:2], contents, checked[3]), current)
        value = deepcopy(original)
        value["commands"].reverse()
        contents = {**checked[2], g.ATTESTATIONS[1]: json.dumps(value)}
        with self.assertRaises(g.Block):
            g.validate_attestations((*checked[:2], contents, checked[3]), current)

    def test_malformed_json_toml_unicode_and_git_errors_are_clean(self):
        for name, bad in (("project-state/state.json", "{"), (g.CONTRACT_PATH, "broken = [")):
            original = g.read_text(self.root, name)
            self.write(name, bad)
            status, out, err = self.cli("check")
            self.assertEqual((status, out), (1, ""))
            self.assertTrue(err.startswith("BLOCK:"))
            self.assertNotIn("Traceback", err)
            self.write(name, original)
        for text in ('{"a": 1, "a": 2}', '{"a": NaN}'):
            with self.assertRaises(g.Block):
                g.parse_json(text)
        (self.root / "README.md").write_bytes(b"\xff")
        self.assertEqual(self.cli("check")[0], 1)
        self.write("README.md", "restored\n")
        with patch.object(subprocess, "run", side_effect=OSError("Git unavailable")):
            self.assertEqual(self.cli("check")[0], 1)

    def test_commands_do_not_change_repository_files(self):
        self.attest()
        before = {name: (self.root / name).read_bytes() for name in g.ALLOWED}
        index_before = (self.root / ".git/index").read_bytes()
        for args in (("check",), ("check", "--ci"), ("prompt",), ("prompt", "--verify"),
                     ("bundle",), ("gate",), ("gate", "--ci")):
            self.assertEqual(self.cli(*args)[0], 0)
        self.assertEqual(before, {name: (self.root / name).read_bytes() for name in g.ALLOWED})
        self.assertEqual(index_before, (self.root / ".git/index").read_bytes())

    def test_historical_evidence_cannot_change_even_if_index_matches(self):
        for name in g.IMMUTABLE:
            original = g.read_text(self.root, name)
            self.write(name, original + "\nChanged history.\n")
            self.run_git("add", name)
            with self.subTest(path=name), self.assertRaisesRegex(g.Block, "immutable historical"):
                g.inspect_git(self.root, ci=True)
            self.write(name, original)
            self.run_git("add", name)

    def test_temporary_history_preserves_prior_package_bytes(self):
        for name in g.IMMUTABLE:
            with self.subTest(path=name):
                self.assertEqual(g.read_text(self.root, name), self.source_inputs[name])
        prior_contract = tomllib.loads(g.read_text(self.root, "contracts/p2_intelligence.toml"))
        self.assertEqual(prior_contract["package_id"], "P2")
        self.assertEqual(prior_contract["baseline"], "e7b36fb715a508c1e4ca91ac97a06b40bed2da7a")
        self.assertEqual(self.load_json("docs/reviews/p1-merge.json")["package_id"], "P1")
        self.assertEqual(self.load_json("docs/reviews/p2-merge.json")["package_id"], "P2")
        prior_contract = tomllib.loads(g.read_text(self.root, "contracts/p3_research.toml"))
        self.assertEqual(prior_contract["package_id"], "P3")
        self.assertEqual(prior_contract["baseline"], "e61f1cbfc41118deb13df421b927ace3d01c150e")
        self.assertEqual(self.load_json("docs/reviews/p3-merge.json")["package_id"], "P3")

    def test_prior_merge_evidence_cannot_be_forged(self):
        name = "docs/reviews/p3-merge.json"
        original = self.load_json(name)
        for key, bad in (("merge_commit", "0" * 40), ("ci_status", "PENDING"),
                         ("merge_status", "not_merged"), ("prior_snapshot", "0" * 64),
                         ("package_id", "P1"), ("tested_head", "0" * 40),
                         ("pr_ci_run", 0), ("merge_ci_run", 0),
                         ("prior_gate_check", "PASS")):
            self.write_json(name, {**original, key: bad})
            with self.assertRaisesRegex(g.Block, "prior merge evidence"):
                g.check(self.root)
        self.write_json(name, original)

    def test_prerequisite_agents_cannot_self_review_or_reset_repair_count(self):
        for key in ("blueprint_review", "prompt_audit"):
            name = g.RECEIPT_PATHS[key]
            original = self.load_json(name)
            self.write_json(name, {**original, "agent_id": "coordinator"})
            with self.assertRaisesRegex(g.Block, "builder self-review"):
                g.check(self.root)
            self.write_json(name, original)
            for count in (-1, 2, True, None):
                self.write_json(name, {**original, "repair_cycles": count})
                with self.assertRaisesRegex(g.Block, "repair bound"):
                    g.check(self.root)
            self.write_json(name, original)

    def test_consumed_entrypoint_repair_cannot_reset_at_prompt_audit(self):
        name = g.RECEIPT_PATHS["blueprint_review"]
        self.write_json(name, {**self.load_json(name), "repair_cycles": 1})
        with self.assertRaisesRegex(g.Block, "repair bound or reset"):
            g.check(self.root)
        name = g.RECEIPT_PATHS["prompt_audit"]
        self.write_json(name, {**self.load_json(name), "repair_cycles": 1})
        self.attest()
        with self.assertRaisesRegex(g.Block, "repair bound or reset"):
            g.gate(self.root)
        review = self.load_json(g.ATTESTATIONS[2])
        self.write_json(g.ATTESTATIONS[2], {**review, "repair_cycles": 1})
        self.run_git("add", ".")
        self.assertEqual(g.gate(self.root), g.LOCAL_RELEASE)

    def test_consumed_prompt_repair_cannot_reset_at_final_review(self):
        name = g.RECEIPT_PATHS["prompt_audit"]
        self.write_json(name, {**self.load_json(name), "repair_cycles": 1})
        self.attest()
        with self.assertRaisesRegex(g.Block, "repair bound or reset"):
            g.gate(self.root)
        review = self.load_json(g.ATTESTATIONS[2])
        self.write_json(g.ATTESTATIONS[2], {**review, "repair_cycles": 1})
        self.run_git("add", ".")
        self.assertEqual(g.gate(self.root), g.LOCAL_RELEASE)

    def test_only_registered_product_bytecode_is_ignored(self):
        self.write("market_intelligence/__pycache__/runtime.cpython-312.pyc", "synthetic cache")
        g.check(self.root)
        self.write("market_intelligence/__pycache__/runtimeXcpython-312.pyc", "not a cache name")
        with self.assertRaisesRegex(g.Block, "out-of-scope"):
            g.check(self.root)


if __name__ == "__main__":
    unittest.main()
