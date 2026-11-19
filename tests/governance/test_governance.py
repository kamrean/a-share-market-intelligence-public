"""Offline P4M regression tests; all mutations and commits use temporary repositories."""

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from datetime import datetime, timedelta
import ast
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("p4m_governance", ROOT / "tools/governance.py")
g = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(g)


def preparation_fixture(snapshot_id, *, root=ROOT, prepared=False):
    """Authored schema data only; no retained payload or runtime store is read."""
    original = g.parse_json(g.read_text(root, g.SOURCE_PROBE_PATH))["observation"]
    observation = {
        "schema_version": 1, "source_id": "fed_press", "source_snapshot": g.SOURCE_SNAPSHOT,
        "source_probe_digest": g.SOURCE_PROBE_DIGEST, "source_run_id": original["run_id"],
        "source_bundle_id": original["bundle_id"], "source_mode": "live_observation",
        "processing_mode": "historical_replay", "source_state": "degraded",
        "source_gate": deepcopy(original["gate"]), "source_as_of": None,
        "processed_at": original["finished_at"], "outcome": "unavailable", "reason": "missing_store",
        "input_count": original["parse"]["accepted"], "prepared_count": 0, "eligible_count": 0,
        "excluded_count": 0, "cluster_count": 0, "parse_rejected": original["parse"]["rejected"],
        "reason_counts": dict(original["parse"]["reasons"]), "provenance": [], "clusters": [],
        "prepared_digest": None, "network_requests": 0, "new_live_runs": 0,
        "live_market_coverage": deepcopy(original["live_market_coverage"]),
        "unavailable_domains": ["entity", "event", "flow", "market", "sector"],
    }
    if prepared:
        capture = original["observations"][0]
        count = observation["input_count"]
        observation.update(outcome="prepared", reason="local_preparation",
                           source_as_of=original["finished_at"], prepared_count=count,
                           excluded_count=count, reason_counts={"stale_news": count},
                           prepared_digest=g.sha256("authored private fixture content"))
        observation["provenance"] = sorted([{
            "evidence_id": g.sha256(f"fixture-evidence-{index}"),
            "capture_id": capture["capture_id"], "fact_id": g.sha256(f"fixture-fact-{index}"),
            "capture_hash": capture["content_hash"],
            "fact_payload_hash": g.sha256(f"fixture-payload-{index}"),
            "source_mode": "live_observation",
            "published_at": (datetime.fromisoformat(original["started_at"]) - timedelta(days=32)).isoformat(),
            **{key: capture[key] for key in ("retrieved_at", "first_seen_at", "expires_at")},
            "disposition": "excluded", "reason": "stale_news",
        } for index in range(count)], key=lambda row: row["evidence_id"])
    return {"schema_version": 1, "package_id": "P4E", "snapshot_id": snapshot_id,
            "status": "RECORDED", "processed_at": observation["processed_at"],
            "observation": observation, "reason": "local_preparation"}



def nbs_fixture(snapshot_id, *, status="FAILED"):
    """Authored terminal diagnostics, never a source observation or runtime access."""
    from market_intelligence import nbs
    from market_intelligence.models import digest
    unknown = status == "UNKNOWN"
    observation = {
        "source_id": "nbs_latest", "run_id": "1" * 32, "attempt_id": "2" * 32,
        "started_at": "2020-01-01T00:00:00Z", "finished_at": "2020-01-01T00:00:01Z",
        "request_outcome": "unknown" if unknown else "failed",
        "reserved_bytes": 512000, "observed_bytes": None if unknown else 0,
        "capture": None, "parse": None, "records": [], "source_as_of": None,
        "source_policy_hash": digest(nbs._policy()), "manifest_hash": None,
        "summary_hash": "3" * 64, "source_gate_id": "4" * 64,
        "composition": nbs._composition(), "reason": "transport_unknown" if unknown else "http_status",
        "limitations": list(nbs.LIMITATIONS), "benchmark_coverage": nbs._coverage(),
        "new_live_research_runs": 0,
    }
    return {"schema_version": 1, "package_id": "P4N", "snapshot_id": snapshot_id,
            "status": status, "observed_at": observation["finished_at"],
            "observation": observation, "reason": "source_observation"}


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

    def test_git_environment_routing_and_replacements_remain_disabled(self):
        completed = subprocess.CompletedProcess([], 0, stdout=b"fixture", stderr=b"")
        with patch.dict(os.environ, {"GIT_DIR": "foreign", "GIT_WORK_TREE": "foreign",
                                     "GIT_INDEX_FILE": "foreign", "GIT_CONFIG_COUNT": "1",
                                     "GIT_CONFIG_KEY_0": "core.fsmonitor",
                                     "GIT_CONFIG_VALUE_0": "untrusted"}), \
                patch.object(subprocess, "run", return_value=completed) as run:
            self.assertEqual(g.git_raw(ROOT, "status", "--porcelain"), b"fixture")
        args, options = run.call_args
        self.assertEqual(args[0][:5], ["git", "-c", "core.fsmonitor=false", "-C", str(ROOT)])
        self.assertEqual({key: value for key, value in options["env"].items()
                          if key.startswith("GIT_")},
                         {"GIT_OPTIONAL_LOCKS": "0", "GIT_NO_REPLACE_OBJECTS": "1"})


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
        self.assertEqual(g.BASELINE, "93f6fe87c723dba32225a4895788d8edeef0b539")
        self.assertEqual(g.BRANCH, "stage3-daily-market-evidence")
        self.assertNotIn("codex", g.BRANCH)
        g.validate_contract(self.contract)
        g.exact(self.policy, g.POLICY, "policy")
        g.validate_state(self.state)
        self.assertIs(self.state["github"]["remote_creation_authorized"], False)
        self.assertEqual(self.state["stage_registry"][0]["status"], "complete")

    def test_p4_scope_keeps_historical_packages_immutable(self):
        self.assertEqual((len(g.ALLOWED), len(g.IMMUTABLE), len(g.PROTECTED)), (200, 176, 196))
        self.assertEqual(tuple(self.contract["allowed_paths"]), g.ALLOWED)
        self.assertEqual(tuple(self.contract["immutable_paths"]), g.IMMUTABLE)
        self.assertEqual(tuple(self.contract["attestation_paths"]), g.CURRENT_ATTESTATIONS)
        self.assertEqual(set(g.ALLOWED) - set(g.PROTECTED), set(g.CURRENT_ATTESTATIONS))
        self.assertEqual(g.CURRENT_ATTESTATIONS, (
            "docs/reviews/p4m-bundle.json", "docs/reviews/p4m-validation.json",
            "docs/reviews/p4m-review.json", "docs/reviews/p4m-release.json"))
        for package in ("p0", "p1", "p2", "p3", "p4", "p4l", "p4c", "p4s", "p4e", "p4i", "p4n"):
            for receipt in ("builder-prompt.md", "bundle.json", "prompt-audit.json",
                            "release.json", "review.json", "validation.json"):
                name = f"docs/reviews/{package}-{receipt}"
                self.assertIn(name, g.IMMUTABLE)
                self.assertIn(name, g.PROTECTED)
                self.assertNotIn(name, g.CURRENT_ATTESTATIONS)
        for name in ("docs/reviews/p0-merge.json", "docs/reviews/p1-merge.json",
                     "docs/reviews/p2-merge.json",
                     "tests/product/test_p1.py", "tests/product/test_p2.py",
                     "tests/product/test_p3.py", "contracts/p3_research.toml",
                     "templates/p3_builder_prompt.md", "docs/evidence/p3-demo.json",
                     "contracts/p2_intelligence.toml", "templates/p2_builder_prompt.md",
                     "docs/evidence/p2-demo.json", "config/p4_publication.toml",
                     "contracts/p4_publication.toml", "docs/P4_METHODS.md",
                     "docs/reviews/p4-entrypoint-review.json", "templates/p4_builder_prompt.md",
                     "market_intelligence/delivery.py", "market_intelligence/ledger.py",
                     "tests/product/test_p4.py", "contracts/p4_live_integration_entry.toml",
                     "templates/p4l_builder_prompt.md", "docs/LIVE_INTEGRATION_PLAN.md",
                     "docs/reviews/p4l-entrypoint-review.json"):
            self.assertIn(name, g.IMMUTABLE)
        self.assertIn("docs/reviews/p3-merge.json", g.PROTECTED)
        self.assertIn("docs/reviews/p3-merge.json", g.IMMUTABLE)
        self.assertIn("docs/reviews/p4-merge.json", g.PROTECTED)
        self.assertIn("docs/reviews/p4-merge.json", g.IMMUTABLE)
        self.assertIn("docs/reviews/p4l-merge.json", g.PROTECTED)
        self.assertIn("docs/reviews/p4l-merge.json", g.IMMUTABLE)
        self.assertIn("docs/reviews/p4c-merge.json", g.PROTECTED)
        self.assertIn("docs/reviews/p4c-merge.json", g.IMMUTABLE)
        self.assertIn("docs/reviews/p4s-merge.json", g.PROTECTED)
        self.assertIn("docs/reviews/p4s-merge.json", g.IMMUTABLE)
        self.assertIn("docs/reviews/p4e-merge.json", g.PROTECTED)
        self.assertIn("docs/reviews/p4e-merge.json", g.IMMUTABLE)
        self.assertIn("docs/reviews/p4i-merge.json", g.PROTECTED)
        self.assertIn("docs/reviews/p4i-merge.json", g.IMMUTABLE)
        self.assertNotIn("docs/reviews/p4n-merge.json", g.IMMUTABLE)

    def test_p4_merge_identity_and_ci_are_exact(self):
        self.assertEqual(g.dependency(), {
            "package_id": "P4N",
            "pr_url": "https://github.com/kamrean/a-share-market-intelligence-public/pull/11",
            "merge_commit": "93f6fe87c723dba32225a4895788d8edeef0b539",
            "merged_at": "2026-11-18T14:45:50Z",
            "tested_head": "d35743371a12ae9c0f6e6d7be937fdac23408b2a",
            "pr_ci_run": 44322275119,
            "merge_ci_run": 44327850126,
            "ci_status": "PASS",
            "merge_status": "merged"
        })
        expected = g.prior_merge_evidence()
        actual = g.parse_json(g.read_text(ROOT, "docs/reviews/p4n-merge.json"))
        g.exact(actual, expected, "prior merge evidence")

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
        for active_package in ("P2", "P3", "P4", "P4L", "P5"):
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
        for status, runtime in (
            ("contract_prepared_unreleased", "daily_market_planned_model_deferred"),
            ("building", "daily_market_planned_model_deferred"),
            ("implemented_unreleased", "daily_market_evidence_model_deferred"),
        ):
            state = deepcopy(self.state)
            state["package_status"] = status
            state["live_integration"]["runtime_implementation"] = runtime
            g.validate_state(state)
            self.assertEqual(state["stage_registry"][7]["status"], "engineering_merged_live_gaps")
            self.assertEqual(state["stage_registry"][8]["status"], "offline_subset_merged_live_gaps")
            for stage in (5, 6, 7, 8):
                for bad in ("complete", "planned", "implemented_unreleased", "in_progress",
                            "offline_subset_implemented_unreleased", "offline_subset_in_progress"):
                    changed = deepcopy(state)
                    changed["stage_registry"][stage]["status"] = bad
                    with self.subTest(status=status, stage=stage, bad=bad), self.assertRaises(g.Block):
                        g.validate_state(changed)

    def test_p4_paths_and_next_gate_are_exact(self):
        self.assertEqual(g.CONTRACT_PATH, "contracts/p4m_daily_market_evidence.toml")
        self.assertEqual(g.TEMPLATE_PATH, "templates/p4m_builder_prompt.md")
        self.assertEqual(g.PROMPT_PATH, "docs/reviews/p4m-builder-prompt.md")
        self.assertEqual(g.NEXT_GATE,
                         "Human merge after exact-head CI and independent remote release; resolve anonymous-only "
                         "source acquisition and real temporal/provenance evidence, then separately audited "
                         "model/report/private-delivery integration. "
                         "No inherited network or VPS permission.")
        self.assertEqual(self.state["next_gate"], g.NEXT_GATE)
        for field, bad in (("active_contract", "contracts/p3_research.toml"),
                           ("baseline", "e61f1cbfc41118deb13df421b927ace3d01c150e"),
                           ("branch", "stage5-6-research-foundation")):
            with self.subTest(field=field), self.assertRaises(g.Block):
                g.validate_state({**self.state, field: bad})
        for field, bad in (("package_id", "P3"), ("stage_ids", [5, 6]),
                           ("attestation_paths", [name.replace("p4m-", "p4-") for name in g.CURRENT_ATTESTATIONS])):
            with self.subTest(field=field), self.assertRaises(g.Block):
                g.validate_contract({**self.contract, field: bad})

    def test_p4_samples_are_required_protected_snapshot_inputs(self):
        contents = dict.fromkeys(g.PROTECTED, "offline snapshot fixture\n")
        original = g.snapshot(contents)[0]
        for name in ("docs/evidence/p4-demo.json", "docs/evidence/p4-report.txt"):
            self.assertIn(name, self.contract["allowed_paths"])
            self.assertIn(name, g.PROTECTED)
            self.assertNotIn(name, g.CURRENT_ATTESTATIONS)
            self.assertIn(name, g.IMMUTABLE)
            self.assertNotEqual(original, g.snapshot({**contents, name: "changed sample\n"})[0])
            with patch.object(Path, "is_file", return_value=False):
                with self.assertRaisesRegex(g.Block, "missing required file"):
                    g.read_text(ROOT, name)

    def test_all_p4_historical_artifacts_cannot_be_removed_from_immutable_scope(self):
        artifacts = {
            name for name in g.ALLOWED
            if name.startswith(("market_intelligence/", "config/", "tests/product/", "tests/fixtures/",
                                "docs/reviews/p4-"))
        } - {"market_intelligence/daily_market.py", "market_intelligence/intelligence.py",
             "tests/product/test_daily_market.py"}
        artifacts.update(("contracts/p4_publication.toml", "templates/p4_builder_prompt.md",
                          "docs/P4_METHODS.md", "docs/evidence/p4-demo.json",
                          "docs/evidence/p4-report.txt", "requirements.lock",
                          "contracts/p4_live_integration_entry.toml", "templates/p4l_builder_prompt.md",
                          "docs/LIVE_INTEGRATION_PLAN.md"))
        artifacts.update(name for name in g.ALLOWED
                         if name.startswith("docs/reviews/p4l-") and
                         name != "docs/reviews/p4l-merge.json")
        for name in sorted(artifacts):
            self.assertIn(name, g.IMMUTABLE)
            contract = deepcopy(self.contract)
            contract["immutable_paths"].remove(name)
            with self.subTest(path=name), self.assertRaisesRegex(g.Block, "immutable_paths"):
                g.validate_contract(contract)

    def test_p4_actual_prerequisites_bind_current_inputs(self):
        for role, key, names in (
            ("blueprint_reviewer", "blueprint_review", g.AUDIT_INPUTS),
            ("prompt_auditor", "prompt_audit", g.AUDIT_INPUTS),
        ):
            receipt = g.parse_json(g.read_text(ROOT, g.CURRENT_RECEIPT_PATHS[key]))
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
        historical = tomllib.loads(g.read_text(ROOT, "contracts/p1_sources_evidence.toml"))["repair_exception"]
        for value in (2, True, "unlimited"):
            contract, policy = deepcopy(self.contract), deepcopy(self.policy)
            contract["max_repair_cycles"] = value
            policy["max_repair_cycles"] = value
            with self.assertRaises(g.Block):
                g.validate_contract(contract)
            with self.assertRaises(g.Block):
                g.exact(policy, g.POLICY, "policy")
        for value in ({}, {"additional_cycles": 1}, historical, g.REPAIR_EXCEPTION):
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

    def test_only_named_p4c_product_and_test_extensions_are_mutable(self):
        product_paths = {name for name in g.ALLOWED
                         if name.startswith(("market_intelligence/", "tests/product/"))}
        self.assertEqual(product_paths - set(g.IMMUTABLE), {
            "market_intelligence/daily_market.py", "market_intelligence/intelligence.py",
            "tests/product/test_daily_market.py",
        })
        for name in product_paths:
            self.assertIn(name, g.PROTECTED)
            self.assertNotIn(name, g.CURRENT_ATTESTATIONS)
        for package in ("p1", "p2", "p3", "p4", "scenario", "workflow", "collection", "preparation", "real_evidence"):
            self.assertIn(f"tests/product/test_{package}.py", g.IMMUTABLE)
        self.assertIn(g.PREREQUISITE_PATH, g.IMMUTABLE)
        self.assertEqual(g.FROZEN_INPUT_HASHES[g.PREREQUISITE_PATH], g.PREREQUISITE_HASH)

    def test_current_required_reads_and_contract_next_gate_are_pinned(self):
        self.assertEqual(self.contract["required_reads"], ["AGENTS.md","BLUEPRINT.md","START_CONSTRUCTION_PROMPT.md","contracts/p4m_daily_market_evidence.toml","governance/policy.toml","project-state/state.json","docs/DAILY_MARKET_EVIDENCE.md","docs/reviews/p4n-merge.json","README.md","docs/CONSTRUCTION_LOG.md","docs/REAL_EVIDENCE_COMPOSITION.md","docs/FREE_SOURCE_COLLECTION.md","docs/NBS_EVIDENCE_INGESTION.md","docs/evidence/live-integration-prerequisites.json","docs/evidence/p4s-source-probe.json","docs/evidence/p4e-preparation.json","docs/evidence/p4n-nbs-observation.json","docs/reviews/p4n-review.json","docs/reviews/p4n-pre-probe-release.json","docs/reviews/p4n-release.json"])
        for field, bad in (("required_reads", self.contract["required_reads"][:-1]),
                           ("next_gate", "start_model_integration"),
                           ("next_gate", g.NEXT_GATE + "; dispatch")):
            with self.subTest(field=field), self.assertRaises(g.Block):
                g.validate_contract({**self.contract, field: bad})

    def test_ci_retains_action_pins_platforms_permissions_and_timeout(self):
        workflow = ".github/workflows/governance.yml"
        previous = g.normalize_text(g.git(ROOT, "show", f"{g.BASELINE}:{workflow}"))
        expected = previous.replace(
            "# P4N bindings retain all offline regressions; CI never runs a product request.",
            "# P4M bindings retain all offline regressions; CI never runs a product request.",
        ).replace("66be9c9705c5a9739f2beb5a3a9287f21703018d", g.BASELINE)
        self.assertEqual(g.read_text(ROOT, workflow), expected)

    def test_frozen_inputs_match_actual_independent_zero_finding_audits(self):
        approved = {}
        for key in ("blueprint_review", "prompt_audit"):
            receipt = g.parse_json(g.read_text(ROOT, g.CURRENT_RECEIPT_PATHS[key]))
            self.assertEqual(receipt["findings"], [])
            self.assertEqual(receipt["verdict"], "PASS")
            self.assertIs(receipt["independent"], True)
            self.assertEqual(receipt["agent_id"], "01a1ef26-ac47-7d02-9fcb-8c3aa5403dad")
            self.assertEqual(receipt["repair_cycles"], 0)
            approved.update(receipt["files"])
        self.assertEqual(g.FROZEN_INPUT_HASHES, approved)
        self.assertEqual(set(approved), set(g.AUDIT_INPUTS) |
                         {"BLUEPRINT.md", "START_CONSTRUCTION_PROMPT.md"})
        for name, digest in approved.items():
            with self.subTest(path=name):
                self.assertEqual(g.sha256(g.read_text(ROOT, name)), digest)

    def test_p4i_exact_dependency_fields_are_required_in_contract_and_state(self):
        for field, value in g.dependency().items():
            for target, validator in ((self.contract, g.validate_contract), (self.state, g.validate_state)):
                for bad in (None, True, "forged"):
                    changed = deepcopy(target)
                    changed["dependency"][field] = bad
                    with self.subTest(field=field, bad=bad), self.assertRaises(g.Block):
                        validator(changed)
                changed = deepcopy(target)
                del changed["dependency"][field]
                with self.assertRaises(g.Block):
                    validator(changed)
        self.assertEqual(len(g.FROZEN_INPUT_HASHES), 20)

    def test_p4i_historical_receipts_cannot_become_current_exemptions(self):
        for name in (g.PREPARATION_PATH, g.SOURCE_PROBE_PATH,
                     *(path for path in g.IMMUTABLE if path.startswith("docs/reviews/"))):
            self.assertIn(name, g.PROTECTED)
            for field, value in (("attestation_paths", [*g.CURRENT_ATTESTATIONS, name]),
                                 ("immutable_paths", [path for path in g.IMMUTABLE if path != name])):
                with self.subTest(path=name, field=field), self.assertRaises(g.Block):
                    g.validate_contract({**self.contract, field: value})

    def test_p4i_audit_schemas_are_closed(self):
        contents = {name: g.read_text(ROOT, name) for name in g.FROZEN_INPUT_HASHES}
        for role, key, names in (
            ("blueprint_reviewer", "blueprint_review", g.AUDIT_INPUTS),
            ("prompt_auditor", "prompt_audit", g.AUDIT_INPUTS),
        ):
            receipt = g.parse_json(g.read_text(ROOT, g.CURRENT_RECEIPT_PATHS[key]))
            for field in receipt:
                changed = {name: value for name, value in receipt.items() if name != field}
                with self.subTest(role=role, missing=field), self.assertRaises(g.Block):
                    g.validate_prerequisite(changed, role, names, contents)
            with self.assertRaisesRegex(g.Block, "unknown fields"):
                g.validate_prerequisite({**receipt, "permission_grant": True}, role, names, contents)


class PrerequisiteManifestTests(unittest.TestCase):
    def setUp(self):
        # These unit tests stop before Git/product inspection. Keep actual frozen
        # governance inputs, with inert snapshot data for unrelated product files.
        self.contents = dict.fromkeys(g.PROTECTED, "offline snapshot fixture\n")
        names = {*g.FROZEN_INPUT_HASHES, "project-state/state.json", "docs/reviews/p4i-merge.json",
                 g.CURRENT_RECEIPT_PATHS["blueprint_review"], g.CURRENT_RECEIPT_PATHS["prompt_audit"]}
        self.contents.update({name: g.read_text(ROOT, name) for name in names})
        self.original = self.contents[g.PREREQUISITE_PATH]
        self.register = g.parse_json(self.original)

    def assert_register_blocked_after_rehash(self, register):
        altered = register if isinstance(register, str) else json.dumps(register, indent=2) + "\n"
        contents = {**self.contents, g.PREREQUISITE_PATH: altered}
        name = g.CURRENT_RECEIPT_PATHS["prompt_audit"]
        audit = g.parse_json(contents[name])
        # Forged hashes exist only in memory, never in the actual audit receipt.
        audit["files"] = {path: g.sha256(contents[path]) for path in g.AUDIT_INPUTS}
        contents[name] = json.dumps(audit)
        with self.assertRaisesRegex(g.Block, "files frozen input"):
            g.validate_prerequisite(audit, "prompt_auditor", g.AUDIT_INPUTS, contents)
        self.assertNotEqual(g.snapshot(contents)[0], g.snapshot(self.contents)[0])
        with patch.object(g, "read_text", side_effect=lambda _root, path: contents[path]), \
                patch.object(g, "inspect_git", return_value=[]) as inspect:
            with self.assertRaisesRegex(g.Block, "prerequisite register|duplicate field"):
                g.check(ROOT)
            inspect.assert_not_called()

    def test_exact_frozen_register_and_lf_normalization(self):
        self.assertEqual(g.PREREQUISITE_HASH,
                         "55fd89e11f1b771c40805ef58eed2b8dedbdbb7686cedf6f3b183526aaa3f2e3")
        self.assertEqual(g.validate_live_prerequisites(self.original), self.register)
        self.assertEqual(g.validate_live_prerequisites(self.original.replace("\n", "\r\n")),
                         self.register)
        self.assertEqual(self.register["benchmark_universe"],
                         ["000001.SH", "399001.SZ", "399006.SZ", "KOSPI", "SP500"])
        self.assertEqual(self.register["live_verified_benchmarks"], 0)
        self.assertEqual([row["id"] for row in self.register["required_inputs"]],
                         ["mainland_sources", "korean_source", "us_source", "primary_news",
                          "model", "private_telegram", "engineering", "acceptance"])

    def test_current_audit_inputs_are_exact_and_snapshot_protected(self):
        self.assertEqual(g.AUDIT_INPUTS, tuple(["AGENTS.md","BLUEPRINT.md","START_CONSTRUCTION_PROMPT.md","README.md","contracts/p4m_daily_market_evidence.toml","governance/policy.toml","docs/DAILY_MARKET_EVIDENCE.md","templates/p4m_builder_prompt.md","docs/reviews/p4m-builder-prompt.md","docs/reviews/p4n-merge.json","docs/REAL_EVIDENCE_COMPOSITION.md","docs/FREE_SOURCE_COLLECTION.md","docs/NBS_EVIDENCE_INGESTION.md","docs/evidence/live-integration-prerequisites.json","docs/evidence/p4s-source-probe.json","docs/evidence/p4e-preparation.json","docs/evidence/p4n-nbs-observation.json","docs/reviews/p4n-review.json","docs/reviews/p4n-pre-probe-release.json","docs/reviews/p4n-release.json"]))
        self.assertTrue(set(g.AUDIT_INPUTS).issubset(g.PROTECTED))
        audit = g.parse_json(self.contents[g.CURRENT_RECEIPT_PATHS["prompt_audit"]])
        for name in g.AUDIT_INPUTS:
            changed = {**self.contents, name: self.contents[name] + "changed\n"}
            with self.subTest(path=name), self.assertRaisesRegex(g.Block, "files"):
                g.validate_prerequisite(audit, "prompt_auditor", g.AUDIT_INPUTS, changed)

    def test_missing_duplicate_reordered_or_substituted_benchmarks_fail_rehashed_audit(self):
        symbols = self.register["benchmark_universe"]
        for bad in (symbols[:-1], symbols + [symbols[0]], list(reversed(symbols)),
                    symbols[:-1] + ["ETF_PROXY"], symbols[:-1] + [symbols[0]], []):
            with self.subTest(symbols=bad):
                self.assert_register_blocked_after_rehash({**self.register, "benchmark_universe": bad})

    def test_rights_scope_cannot_change_even_with_rehashed_audit(self):
        rights = self.register["rights_scope"]
        for bad in (rights[:-1], rights + [rights[0]], list(reversed(rights)),
                    rights + ["all_uses_authorized"], []):
            with self.subTest(rights=bad):
                self.assert_register_blocked_after_rehash({**self.register, "rights_scope": bad})

    def test_all_eight_requirement_rows_remain_exact_after_rehashed_audit(self):
        rows = self.register["required_inputs"]
        for bad in (rows[:-1], rows + [rows[0]], list(reversed(rows)),
                    rows[:-1] + [rows[0]], []):
            self.assert_register_blocked_after_rehash({**self.register, "required_inputs": bad})
        for index, row in enumerate(rows):
            for key in row:
                with self.subTest(requirement=row["id"], missing=key):
                    changed = deepcopy(self.register)
                    del changed["required_inputs"][index][key]
                    self.assert_register_blocked_after_rehash(changed)
            for key, bad in (("status", "ready"), ("permission_grant", True),
                             ("account_status", "verified")):
                with self.subTest(requirement=row["id"], field=key):
                    changed = deepcopy(self.register)
                    changed["required_inputs"][index][key] = bad
                    self.assert_register_blocked_after_rehash(changed)

    def test_model_destination_and_acceptance_flags_fail_rehashed_audit(self):
        changes = {
            "mainland_sources": [("candidate_personal_key_required", True),
                                 ("unverified_symbol", "verified"), ("documented_symbols", [])],
            "model": [("provider", "configured"), ("budget_approved", True)],
            "private_telegram": [("destination_selected", True), ("test_release", "PASS")],
            "engineering": [("runtime_implementation", "implemented")],
            "acceptance": [("completed_dated_runs", 3), ("required_dated_runs", 0),
                           ("required_claim_sample", "none"), ("human_sample_status", "PASS")],
        }
        for index, row in enumerate(self.register["required_inputs"]):
            for key, bad in changes.get(row["id"], []):
                with self.subTest(requirement=row["id"], field=key):
                    changed = deepcopy(self.register)
                    changed["required_inputs"][index][key] = bad
                    self.assert_register_blocked_after_rehash(changed)

    def test_core_modes_permissions_and_live_count_cannot_expand(self):
        for key, bad in (
            ("schema_version", True), ("package_id", "P4"), ("observed_on", "2026-11-14"),
            ("basis", "live_observation"), ("basis", "historical_replay"),
            ("product_probes_performed", True), ("credentials_inspected", True),
            ("permission_grant", True), ("permission_grant", 0), ("readiness", "PASS"),
            ("live_verified_benchmarks", 5), ("live_verified_benchmarks", False),
            ("selected_providers", ["configured_provider"]), ("next_action", "dispatch"),
            ("provider_key_available", True), ("runtime_mode", "live_observation"),
        ):
            with self.subTest(field=key, bad=bad):
                self.assert_register_blocked_after_rehash({**self.register, key: bad})
        for key in self.register:
            changed = deepcopy(self.register)
            del changed[key]
            with self.subTest(missing=key):
                self.assert_register_blocked_after_rehash(changed)
        duplicate = self.original.replace('"permission_grant": false',
                                          '"permission_grant": false, "permission_grant": true')
        self.assert_register_blocked_after_rehash(duplicate)

    def test_user_deferral_and_private_confirmation_are_not_verification(self):
        rows = {row["id"]: row for row in self.register["required_inputs"]}
        self.assertEqual(rows["model"], {
            "id": "model", "status": "deferred_by_user_zero_paid_budget",
            "account_status": "not_established",
        })
        self.assertEqual(rows["private_telegram"], {
            "id": "private_telegram",
            "status": "user_confirmed_own_private_chat_credentials_and_release_unverified",
            "account_status": "bot_exists_user_reported",
        })
        self.assertEqual(rows["acceptance"]["completed_dated_runs"], 0)
        self.assertEqual(rows["acceptance"]["human_sample_status"], "not_assessed")
        self.assertEqual((self.register["live_verified_benchmarks"],
                          len(self.register["benchmark_universe"])), (0, 5))

    def test_full_manifest_schema_is_pinned_independently_of_hash(self):
        def mutations(value, path=()):
            yield path, None
            if type(value) is dict:
                yield path, {**value, "unapproved": False}
                for key, item in value.items():
                    yield path, {name: data for name, data in value.items() if name != key}
                    yield from mutations(item, (*path, key))
            elif type(value) is list:
                yield path, value[:-1] if value else ["unapproved"]
                for index, item in enumerate(value):
                    yield from mutations(item, (*path, index))
            elif type(value) is bool:
                yield path, int(value)
                yield path, not value
            elif type(value) is int:
                yield path, value + 1
                yield path, bool(value)
            else:
                yield path, value + " changed"

        for path, bad in mutations(self.register):
            changed = deepcopy(self.register)
            if path:
                target = changed
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = bad
            else:
                changed = bad
            text = json.dumps(changed, indent=2) + "\n"
            # Isolate schema validation from the independently tested content pin.
            with self.subTest(path=path, bad=bad), \
                    patch.object(g, "PREREQUISITE_HASH", g.sha256(text)), \
                    self.assertRaisesRegex(g.Block, "prerequisite register"):
                g.validate_live_prerequisites(text)

    def test_all_changed_frozen_inputs_fail_even_when_receipt_hashes_are_recomputed(self):
        for role, key, names in (
            ("blueprint_reviewer", "blueprint_review", g.AUDIT_INPUTS),
            ("prompt_auditor", "prompt_audit", g.AUDIT_INPUTS),
        ):
            for name in names:
                changed = {**self.contents, name: self.contents[name] + "\n"}
                receipt = g.parse_json(changed[g.CURRENT_RECEIPT_PATHS[key]])
                receipt["files"] = {path: g.sha256(changed[path]) for path in names}
                changed[g.CURRENT_RECEIPT_PATHS[key]] = json.dumps(receipt)
                with self.subTest(role=role, path=name):
                    with self.assertRaisesRegex(g.Block, "files frozen input"):
                        g.validate_prerequisite(receipt, role, names, changed)
                    with patch.object(g, "read_text", side_effect=lambda _root, path: changed[path]), \
                            patch.object(g, "inspect_git") as inspect:
                        with self.assertRaises(g.Block):
                            g.check(ROOT)
                        inspect.assert_not_called()

    def test_historical_probe_is_immutable_and_pinned_by_bytes_and_canonical_digest(self):
        original = self.contents[g.SOURCE_PROBE_PATH]
        record = g.validate_historical_probe(original)
        self.assertEqual(g.validate_historical_probe(original.replace("\n", "\r\n")), record)
        self.assertIn(g.SOURCE_PROBE_PATH, g.IMMUTABLE)
        self.assertIn(g.SOURCE_PROBE_PATH, g.PROTECTED)
        self.assertNotIn(g.SOURCE_PROBE_PATH, g.CURRENT_ATTESTATIONS)
        self.assertEqual(g.probe_digest(record), g.SOURCE_PROBE_DIGEST)
        self.assertNotEqual(g.probe_digest(record["observation"]), g.SOURCE_PROBE_DIGEST)
        g.validate_source_probe(record, g.SOURCE_SNAPSHOT)
        changed = deepcopy(record)
        changed["observation"]["requests"]["attempted"] = 2
        text = json.dumps(changed)
        with self.assertRaisesRegex(g.Block, "frozen content"):
            g.validate_historical_probe(text)
        with patch.object(g, "SOURCE_PROBE_HASH", g.sha256(text)), \
                self.assertRaisesRegex(g.Block, "canonical digest"):
            g.validate_historical_probe(text)
        contents = {**self.contents, g.SOURCE_PROBE_PATH: text}
        audit = g.parse_json(contents[g.CURRENT_RECEIPT_PATHS["prompt_audit"]])
        audit["files"][g.SOURCE_PROBE_PATH] = g.sha256(text)
        with self.assertRaisesRegex(g.Block, "files frozen input"):
            g.validate_prerequisite(audit, "prompt_auditor", g.AUDIT_INPUTS, contents)
        contents[g.CURRENT_RECEIPT_PATHS["prompt_audit"]] = json.dumps(audit)
        with patch.object(g, "read_text", side_effect=lambda _root, path: contents[path]), \
                patch.object(g, "inspect_git") as inspect, \
                self.assertRaisesRegex(g.Block, "historical source probe"):
            g.check(ROOT)
        inspect.assert_not_called()


    def test_historical_preparation_is_frozen_not_a_fifth_attestation(self):
        original = self.contents[g.PREPARATION_PATH]
        record = g.validate_historical_preparation(original)
        self.assertEqual(g.validate_historical_preparation(original.replace("\n", "\r\n")), record)
        self.assertIn(g.PREPARATION_PATH, g.IMMUTABLE)
        self.assertIn(g.PREPARATION_PATH, g.PROTECTED)
        self.assertNotIn(g.PREPARATION_PATH, g.CURRENT_ATTESTATIONS)
        g.validate_preparation_record(record, g.PREPARATION_SNAPSHOT)
        for changed in (original + "\n", json.dumps({**record, "status": "NOT_RUN"})):
            with self.assertRaisesRegex(g.Block, "historical preparation frozen content"):
                g.validate_historical_preparation(changed)
            contents = {**self.contents, g.PREPARATION_PATH: changed}
            audit = g.parse_json(contents[g.CURRENT_RECEIPT_PATHS["prompt_audit"]])
            audit["files"][g.PREPARATION_PATH] = g.sha256(changed)
            with self.assertRaisesRegex(g.Block, "files frozen input"):
                g.validate_prerequisite(audit, "prompt_auditor", g.AUDIT_INPUTS, contents)
            with patch.object(g, "read_text", side_effect=lambda _root, path: contents[path]), \
                    patch.object(g, "inspect_git") as inspect, \
                    self.assertRaisesRegex(g.Block, "historical preparation frozen content"):
                g.check(ROOT)
            inspect.assert_not_called()

    def test_p4i_actual_audit_agent_cannot_be_substituted(self):
        for key in ("blueprint_review", "prompt_audit"):
            path = g.CURRENT_RECEIPT_PATHS[key]
            receipt = g.parse_json(self.contents[path])
            changed = {**self.contents, path: json.dumps({**receipt, "agent_id": "other-independent"})}
            with patch.object(g, "read_text", side_effect=lambda _root, name: changed[name]), \
                    patch.object(g, "inspect_git") as inspect, \
                    self.assertRaisesRegex(g.Block, "approved independent audit agent"):
                g.check(ROOT)
            inspect.assert_not_called()


class LiveIntegrationLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.state = g.parse_json(g.read_text(ROOT, "project-state/state.json"))

    def test_only_matching_governance_entry_states_are_valid(self):
        for package, runtime in (("building", "daily_market_planned_model_deferred"),
                                 ("implemented_unreleased", "daily_market_evidence_model_deferred")):
            state = deepcopy(self.state)
            state["package_status"] = package
            state["live_integration"]["runtime_implementation"] = runtime
            g.validate_state(state)
            state["live_integration"]["runtime_implementation"] = (
                "daily_market_evidence_model_deferred" if package == "building"
                else "daily_market_planned_model_deferred")
            with self.assertRaisesRegex(g.Block, "runtime_implementation"):
                g.validate_state(state)

    def test_no_premature_entry_runtime_or_milestone_completion(self):
        for bad in ("implemented", "contract_prepared", "complete", "live_ready", "PASS",
                    "merged", True, None):
            with self.subTest(package_status=bad), self.assertRaises(g.Block):
                g.validate_state({**self.state, "package_status": bad})
            for key in ("entry_status", "runtime_implementation", "readiness"):
                state = deepcopy(self.state)
                state["live_integration"][key] = bad
                with self.subTest(field=key, bad=bad), self.assertRaises(g.Block):
                    g.validate_state(state)
        for key in self.state["milestones"]:
            for bad in ("complete", "implemented", "in_progress", "authorized"):
                state = deepcopy(self.state)
                state["milestones"][key] = bad
                with self.subTest(milestone=key, bad=bad), self.assertRaises(g.Block):
                    g.validate_state(state)

    def test_live_state_schema_and_permissions_are_exact(self):
        for key in self.state:
            state = deepcopy(self.state)
            del state[key]
            with self.subTest(missing=key), self.assertRaises(g.Block):
                g.validate_state(state)
        for key in self.state["live_integration"]:
            for operation in ("missing", "wrong_type"):
                state = deepcopy(self.state)
                if operation == "missing":
                    del state["live_integration"][key]
                else:
                    state["live_integration"][key] = None
                with self.subTest(field=key, operation=operation), self.assertRaises(g.Block):
                    g.validate_state(state)
        for key, bad in (("permissions_enabled", True), ("permissions_enabled", 0),
                         ("prerequisite_register", "docs/evidence/p1-source-probe.json"),
                         ("runtime_implementation", "in_progress"), ("extra", False),
                         *((name, True) for name in g.PERMISSIONS)):
            state = deepcopy(self.state)
            state["live_integration"][key] = bad
            with self.subTest(field=key, bad=bad), self.assertRaises(g.Block):
                g.validate_state(state)
        with self.assertRaises(g.Block):
            g.validate_state({**self.state, "permissions": {"external_data_access_allowed": True}})

    def test_historical_stage7_stage8_do_not_track_current_building(self):
        for package, runtime in (("building", "daily_market_planned_model_deferred"),
                                 ("implemented_unreleased", "daily_market_evidence_model_deferred")):
            state = deepcopy(self.state)
            state["package_status"] = package
            state["live_integration"]["runtime_implementation"] = runtime
            g.validate_state(state)
            for stage, expected in ((7, "engineering_merged_live_gaps"),
                                    (8, "offline_subset_merged_live_gaps")):
                self.assertEqual(state["stage_registry"][stage]["status"], expected)
                for bad in ("in_progress", "implemented_unreleased", "contract_prepared_unreleased",
                            "offline_subset_in_progress", "offline_subset_implemented_unreleased", "complete"):
                    changed = deepcopy(state)
                    changed["stage_registry"][stage]["status"] = bad
                    with self.subTest(package=package, stage=stage, bad=bad), self.assertRaises(g.Block):
                        g.validate_state(changed)

    def test_cli_success_is_explicitly_governance_only_and_live_blocked(self):
        for args in (("check",), ("check", "--ci"), ("gate",), ("gate", "--ci")):
            output = io.StringIO()
            with patch.object(g, "check"), patch.object(g, "gate", return_value="LOCAL_READY_FOR_PR"), \
                    redirect_stdout(output):
                self.assertEqual(g.main(list(args)), 0)
            self.assertIn("Daily-market offline governance only; model/live-readiness-blocked: BLOCKED_EXTERNAL_PREREQUISITES",
                          output.getvalue())
            self.assertTrue(output.getvalue().startswith("PASS (" if args[0] == "check"
                                                        else "LOCAL_READY_FOR_PR ("))

    def test_both_offline_states_keep_human_merged_entry_and_closed_live_permissions(self):
        for package, runtime in (("building", "daily_market_planned_model_deferred"),
                                 ("implemented_unreleased", "daily_market_evidence_model_deferred")):
            state = deepcopy(self.state)
            state["package_status"] = package
            state["live_integration"]["runtime_implementation"] = runtime
            g.validate_state(state)
            self.assertEqual(state["live_integration"], {
                "entry_status": "human_merged", "runtime_implementation": runtime,
                "readiness": "BLOCKED_EXTERNAL_PREREQUISITES",
                "prerequisite_register": g.PREREQUISITE_PATH, "permissions_enabled": False,
            })
            for entry in ("in_progress", "contract_prepared_unreleased", "implemented_unreleased"):
                changed = deepcopy(state)
                changed["live_integration"]["entry_status"] = entry
                with self.subTest(package=package, entry=entry), self.assertRaises(g.Block):
                    g.validate_state(changed)


class ProbeAttestationTests(unittest.TestCase):
    """Synthetic in-memory approvals, never real reviewer/release receipts."""

    def setUp(self):
        self.contract = tomllib.loads(g.read_text(ROOT, g.CONTRACT_PATH))
        self.state = g.parse_json(g.read_text(ROOT, "project-state/state.json"))
        self.state["package_status"] = "implemented_unreleased"
        self.state["live_integration"]["runtime_implementation"] = (
            "daily_market_evidence_model_deferred")
        self.contents = dict.fromkeys(g.PROTECTED, "offline snapshot fixture\n")
        for name in (*g.FROZEN_INPUT_HASHES, g.CURRENT_RECEIPT_PATHS["blueprint_review"],
                     g.CURRENT_RECEIPT_PATHS["prompt_audit"]):
            self.contents[name] = g.read_text(ROOT, name)
        self.checked = (self.contract, self.state, self.contents, [])
        self.current = g.make_bundle(self.checked)
        self.snapshot = self.current["snapshot_id"]
        common = {"schema_version": 1, "snapshot_id": self.snapshot}
        self.receipts = [self.current, {
            **common, "role": "builder_validation", "verdict": "PASS",
            "commands": [{"command": command, "exit_code": 0} for command in g.VALIDATION_COMMANDS],
            "python_version": "3.12.10", "remote_ci": "NOT_RUN_AT_SNAPSHOT",
        }, {
            **common, "role": "reviewer", "verdict": "PASS", "agent_id": "fixture-reviewer",
            "read_only": True, "findings": [], "repair_cycles": 0,
        }, {
            **common, "role": "release_checker", "verdict": g.LOCAL_RELEASE,
            "agent_id": "fixture-release", "read_only": True,
            "remote_ci": "NOT_RUN_AT_SNAPSHOT", "pr_url": None, "merge_status": "not_merged",
            "vps_sync": "not_requested", "next_gate": g.NEXT_GATE,
        }]
        self.pre_release = {**self.receipts[3], "verdict": g.PRE_PROBE_RELEASE}
        self.observation = nbs_fixture(self.snapshot)
        self.historical = {
            **common, "package_id": "P4E", "status": "NOT_RUN", "processed_at": None,
            "observation": None, "reason": "awaiting_release",
        }
        self.sync()

    def sync(self):
        self.contents.update({name: json.dumps(value)
                              for name, value in zip(g.CURRENT_ATTESTATIONS, self.receipts)})

    def validate(self):
        self.sync()
        g.validate_current_attestations(self.checked, self.current)

    def terminal(self, *, recorded=False):
        self.historical = preparation_fixture(self.snapshot, prepared=recorded)
        self.sync()

    def test_exact_typed_limited_probe_does_not_expand_general_permissions(self):
        historical = tomllib.loads(g.read_text(ROOT, "contracts/p4e_evidence_preparation.toml"))
        expected = historical["local_preparation"]
        self.assertNotIn("local_preparation", self.contract)
        self.assertNotIn("local_preparation", g.POLICY)
        self.assertNotIn("limited_probe", self.contract)
        for field in ("local_preparation", "limited_probe"):
            for value in (expected, {}, True, "LOCAL_READY_FOR_LIMITED_PROBE"):
                with self.subTest(field=field, value=value), self.assertRaises(g.Block):
                    g.validate_contract({**self.contract, field: value})
                with self.assertRaises(g.Block):
                    g.exact({**g.POLICY, field: value}, g.POLICY, "policy")
        for key, value in expected.items():
            bad_values = [None, str(value), not value] if type(value) is not str else [None, 1, value + "/extra"]
            if type(value) is int:
                bad_values += [bool(value), value + 1, -1]
            if type(value) is bool:
                bad_values += [int(value)]
            for bad in bad_values:
                with self.subTest(key=key, bad=bad), self.assertRaises(g.Block):
                    g.validate_contract({**self.contract, "local_preparation": {**expected, key: bad}})
            with self.assertRaises(g.Block):
                g.validate_contract({**self.contract, "local_preparation":
                                     {name: item for name, item in expected.items() if name != key}})

    def test_pending_probe_can_release_without_circular_terminal_observation(self):
        # A pending historical fixture is not current NBS authority.
        self.validate()
        g.validate_preparation_record(self.historical, self.snapshot)
        with patch.object(g, "check", return_value=self.checked) as check:
            with self.assertRaisesRegex(g.Block, "preparation capability is closed"):
                g.preparation_gate(ROOT)
            check.assert_not_called()
        self.assertRegex(self.snapshot, r"^[0-9a-f]{64}$")

    def test_probe_gate_preserves_check_and_complete_index_failures(self):
        for message in ("index/worktree mismatch", "missing commit candidate files", "Git branch",
                        "immutable historical input changed", "possible secret", "link/junction forbidden"):
            with patch.object(g, "check", side_effect=g.Block(message)) as check, \
                    patch.object(g, "validate_current_attestations") as validation:
                with self.subTest(message=message), self.assertRaisesRegex(g.Block, message):
                    g.gate(ROOT)
                check.assert_called_once_with(ROOT, ci=False, complete_index=True)
                validation.assert_not_called()
                check.reset_mock()
                for ci in (False, True):
                    with self.assertRaisesRegex(g.Block, "preparation capability is closed"):
                        g.preparation_gate(ROOT, ci=ci)
                check.assert_not_called()

    def test_probe_gate_has_no_ci_argument_and_cli_rejects_ci(self):
        # The historical case now asserts unconditional rejection with or without CI.
        for flags in ([], ["--ci"]):
            with redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()) as error:
                self.assertEqual(g.main(["preparation-gate", *flags], root=ROOT), 1)
            self.assertEqual(output.getvalue(), "")
            self.assertIn("preparation capability is closed", error.getvalue())
            with self.assertRaisesRegex(g.Block, "preparation capability is closed"):
                g.preparation_gate(ROOT, ci=bool(flags))

    def test_final_gate_requires_terminal_observation(self):
        # Historical terminal observations remain auditable but are not current attestations.
        for recorded in (False, True):
            self.terminal(recorded=recorded)
            g.validate_preparation_record(self.historical, self.snapshot)
            self.validate()
        with patch.object(g, "check", return_value=self.checked) as check, \
                patch.object(g, "validate_preparation_record", side_effect=AssertionError("old phase")):
            self.assertEqual(g.gate(ROOT), g.LOCAL_RELEASE)
            check.assert_called_once_with(ROOT, ci=False, complete_index=True)

    def test_terminal_probe_never_reauthorizes_dispatch(self):
        for recorded in (False, True):
            self.terminal(recorded=recorded)
            g.validate_preparation_record(self.historical, self.snapshot)
            for gate in (g.probe_gate, g.preparation_gate, g.nbs_gate):
                for ci in (False, True):
                    with self.assertRaisesRegex(g.Block, "capability is closed"):
                        gate(ROOT, ci=ci)

    def test_prerelease_requires_null_digest_and_final_requires_canonical_digest(self):
        self.terminal()
        from market_intelligence.models import digest
        self.assertEqual(g.preparation_digest(self.historical), digest(self.historical))
        self.assertEqual(g.preparation_digest({"text": "\uac00"}), digest({"text": "\uac00"}))
        original = deepcopy(self.receipts[3])
        for bad in (None, "", "0" * 64, False, {}, g.preparation_digest(self.historical)):
            self.receipts[3] = {**original, "preparation_digest": bad}
            with self.subTest(value=bad), self.assertRaisesRegex(g.Block, "unknown fields"):
                self.validate()
        self.receipts[3] = original
        self.validate()

    def test_preapproval_is_exact_and_same_agent_is_required_at_final_release(self):
        original = deepcopy(self.receipts[3])
        preapproval = {"verdict": "LOCAL_READY_FOR_LOCAL_PREPARATION", "snapshot_id": self.snapshot,
                      "agent_id": original["agent_id"]}
        variants = [preapproval, None, {}]
        for key, bad in (("verdict", "PASS"), ("snapshot_id", "0" * 64),
                         ("agent_id", "different-release"), ("agent_id", 1),
                         ("agent_id", None), ("allow_network", True)):
            variants.append({**preapproval, key: bad})
        variants.extend({k: v for k, v in preapproval.items() if k != key} for key in preapproval)
        for value in variants:
            self.receipts[3] = {**original, "pre_preparation": value}
            with self.subTest(value=value), self.assertRaisesRegex(g.Block, "unknown fields"):
                self.validate()
        self.receipts[3] = {**original, "agent_id": self.receipts[2]["agent_id"]}
        with self.assertRaisesRegex(g.Block, "separate independent release"):
            self.validate()

    def test_independent_roles_are_strict_strings_in_both_phases(self):
        for recorded in (False, True):
            self.terminal(recorded=recorded)
            for index in (2, 3):
                original = deepcopy(self.receipts[index])
                for bad in (None, 1, True, {}, "", " ", " fixture-agent", "fixture-agent ",
                            *self.state["builder_agents"]):
                    self.receipts[index] = {**original, "agent_id": bad}
                    with self.subTest(recorded=recorded, index=index, bad=bad), self.assertRaises(g.Block):
                        self.validate()
                self.receipts[index] = original
            self.receipts[3]["agent_id"] = self.receipts[2]["agent_id"]
            with self.assertRaisesRegex(g.Block, "separate independent release"):
                self.validate()
            self.receipts[3]["agent_id"] = "fixture-release"

    def test_missing_or_arbitrary_receipts_cannot_authorize_probe(self):
        self.sync()
        for name in g.CURRENT_ATTESTATIONS:
            original = self.contents[name]
            for invalid in (None, '"PASS"', '{}'):
                if invalid is None:
                    del self.contents[name]
                else:
                    self.contents[name] = invalid
                with self.subTest(name=name, invalid=invalid), self.assertRaises(g.Block):
                    g.validate_current_attestations(self.checked, self.current)
                self.contents[name] = original

    def test_stale_snapshots_fail_in_every_probe_approval(self):
        for index in range(4):
            original = deepcopy(self.receipts[index])
            self.receipts[index]["snapshot_id"] = "0" * 64
            with self.subTest(index=index), self.assertRaises(g.Block):
                self.validate()
            self.receipts[index] = original
        self.contents["README.md"] += "changed protected decision\n"
        changed = g.make_bundle(self.checked)
        self.sync()
        with self.assertRaisesRegex(g.Block, "bundle receipt"):
            g.validate_current_attestations(self.checked, changed)

    def test_probe_schema_rejects_unknown_fields_duplicates_and_diagnostic_strings(self):
        original = deepcopy(self.historical)
        for key, bad in (("schema_version", True), ("status", "PASS"), ("observation", {}),
                         ("reason", "arbitrary receipt authorizes request"), ("path", "private"),
                         ("package_id", "P4C"), ("processed_at", "2026-01-01T00:00:00Z")):
            with self.subTest(key=key), self.assertRaises(g.Block):
                g.validate_preparation_record({**original, key: bad}, self.snapshot)
        duplicate = json.dumps(original).replace(
            '"status": "NOT_RUN"', '"status": "NOT_RUN", "status": "RECORDED"')
        with self.assertRaisesRegex(g.Block, "duplicate field"):
            g.parse_json(duplicate)

    def test_forged_terminal_record_cannot_pass_even_with_matching_digest(self):
        self.terminal(recorded=True)
        original = deepcopy(self.historical)
        for key, bad in (("live_market_coverage", {"covered": 5, "expected": 5, "missing": []}),
                         ("source_mode", "synthetic_test"), ("source_id", "baostock"),
                         ("reason_counts", {"private exception text": 1}), ("title", "news payload"),
                         ("network_requests", 1), ("new_live_runs", 1)):
            changed = deepcopy(original)
            changed["observation"][key] = bad
            self.assertNotEqual(g.preparation_digest(changed), g.preparation_digest(original))
            with self.subTest(key=key), self.assertRaisesRegex(g.Block, "preparation"):
                g.validate_preparation_record(changed, self.snapshot)

    def test_validation_is_lazy_and_loads_the_requested_checkout(self):
        # This isolated fixture deliberately differs from the real preparation module.
        with tempfile.TemporaryDirectory(prefix="p4s-validator-") as directory:
            root = Path(directory)
            package = root / "market_intelligence"
            package.mkdir()
            (package / "__init__.py").write_text("", encoding="utf-8")
            (package / "preparation.py").write_text(
                "def validate_preparation_record(value, snapshot_id, *, root):\n"
                "    raise ValueError('different checkout validator')\n", encoding="utf-8")
            with self.assertRaisesRegex(g.Block, "preparation"):
                g.validate_preparation_record(self.historical, self.snapshot, root=root)
        g.validate_preparation_record(self.historical, self.snapshot)
        self.assertFalse(any(name.startswith("_p4e_preparation_validation_") for name in sys.modules))

    def test_direct_script_context_does_not_need_pythonpath_for_lazy_validator(self):
        # run_path uses the actual script entrypoint path; keep only tools on the
        # project's import path and disable environment/user site injection.
        code = (
            "import json,runpy,sys; from pathlib import Path; "
            "script=Path(sys.argv[1]).resolve(); "
            "sys.path[:]=[str(script.parent)]+[p for p in sys.path if p and "
            "Path(p).resolve()!=script.parent.parent]; "
            "g=runpy.run_path(str(script)); "
            "g['validate_preparation_record'](json.loads(sys.argv[2]),sys.argv[3]); print('PASS')"
        )
        result = subprocess.run([sys.executable, "-I", "-B", "-c", code,
                                 str(ROOT / "tools/governance.py"), json.dumps(self.historical), self.snapshot],
                                capture_output=True, text=True, check=False)
        self.assertEqual((result.returncode, result.stdout.strip(), result.stderr), (0, "PASS", ""))

    def test_old_probe_gate_is_closed_without_even_consulting_approvals(self):
        for root in (ROOT, Path("nonexistent-checkout")):
            with patch.object(g, "check", return_value=self.checked) as check, \
                    patch.object(g, "validate_current_attestations") as validate:
                for ci in (False, True):
                    with self.assertRaisesRegex(g.Block, "historical P4S probe capability is closed"):
                        g.probe_gate(root, ci=ci)
                check.assert_not_called()
                validate.assert_not_called()
        for flags in ([], ["--ci"]):
            with redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()) as error:
                self.assertEqual(g.main(["probe-gate", *flags]), 1)
            self.assertEqual(output.getvalue(), "")
            self.assertIn("no network request authorized", error.getvalue())

    def test_planned_or_building_state_never_authorizes_preparation_or_final_release(self):
        for status in ("contract_prepared_unreleased", "building"):
            self.state["package_status"] = status
            self.state["live_integration"]["runtime_implementation"] = (
                "daily_market_planned_model_deferred")
            g.validate_state(self.state)
            with self.subTest(status=status), self.assertRaisesRegex(g.Block, "still building"):
                self.validate()
            with self.assertRaisesRegex(g.Block, "preparation capability is closed"):
                g.preparation_gate(ROOT)

    def test_legacy_probe_release_fields_cannot_authorize_local_preparation(self):
        original = deepcopy(self.receipts[3])
        for field, bad in (("verdict", "LOCAL_READY_FOR_LIMITED_PROBE"),
                           ("verdict", "LOCAL_READY_FOR_LOCAL_PREPARATION"),
                           ("pre_probe", {}), ("pre_preparation", {}),
                           ("probe_digest", None), ("preparation_digest", None),
                           ("allow_network", True)):
            self.receipts[3] = {**original, field: bad}
            with self.subTest(field=field, value=bad), self.assertRaises(g.Block):
                self.validate()
        self.receipts[3] = original

    def test_recorded_result_is_strict_even_with_recomputed_release_digest(self):
        self.terminal()
        original = deepcopy(self.historical)
        g.validate_preparation_record(original, self.snapshot)
        for field, bad in (("outcome", "PASS"), ("reason", "private path or exception"),
                           ("processing_mode", "live_observation"), ("source_snapshot", "0" * 64),
                           ("source_probe_digest", "0" * 64), ("source_run_id", "0" * 32),
                           ("source_bundle_id", "0" * 64), ("network_requests", False),
                           ("input_count", True), ("prepared_count", 101),
                           ("provenance", [{}] * 101), ("clusters", [{}] * 101),
                           ("prepared_digest", "0" * 64), ("unavailable_domains", []),
                           ("source_gate", {"status": "PASS"}), ("title", "private payload"),
                           ("processed_at", "9999-01-01T00:00:00Z")):
            changed = deepcopy(original)
            changed["observation"][field] = bad
            self.assertNotEqual(g.preparation_digest(changed), g.preparation_digest(original))
            with self.subTest(field=field), self.assertRaisesRegex(g.Block, "preparation"):
                g.validate_preparation_record(changed, self.snapshot)
        for field, bad in (("status", "BLOCKED_NO_REQUEST"), ("processed_at", None),
                           ("observation", None), ("reason", "source_observation")):
            changed = {**original, field: bad}
            self.assertNotEqual(g.preparation_digest(changed), g.preparation_digest(original))
            with self.subTest(field=field), self.assertRaises(g.Block):
                g.validate_preparation_record(changed, self.snapshot)

    def test_preparation_validator_uses_explicit_root_and_ignores_foreign_import_cache(self):
        with tempfile.TemporaryDirectory(prefix="p4e-validator-") as directory:
            root = Path(directory).resolve()
            package = root / "market_intelligence"
            package.mkdir()
            (package / "__init__.py").write_text("", encoding="utf-8")
            validator = package / "preparation.py"
            validator.write_text(
                "from pathlib import Path\n"
                "def validate_preparation_record(value, snapshot_id, *, root):\n"
                "    assert root == Path(__file__).resolve().parents[1]\n"
                "    return value\n", encoding="utf-8")
            with patch.dict(sys.modules, {"market_intelligence.preparation": object()}), \
                    patch.dict(os.environ, {"PYTHONPATH": "foreign-invalid-checkout"}):
                g.validate_preparation_record(self.historical, self.snapshot, root=root)
            validator.write_text(
                "def validate_preparation_record(value, snapshot_id, *, root):\n"
                "    return dict(value, unapproved=True)\n", encoding="utf-8")
            with self.assertRaisesRegex(g.Block, "preparation"):
                g.validate_preparation_record(self.historical, self.snapshot, root=root)
            namespace = "_p4e_preparation_validation_" + g.sha256(str(root))
            with patch.dict(sys.modules, {namespace: object()}), \
                    self.assertRaisesRegex(g.Block, "recursive validation"):
                g.validate_preparation_record(self.historical, self.snapshot, root=root)
        self.assertFalse(any(name.startswith("_p4e_preparation_validation_") for name in sys.modules))

    def test_preparation_receipt_validation_never_reads_a_store_or_authorizes_operations(self):
        self.terminal()
        original = g.parse_json(g.read_text(ROOT, g.SOURCE_PROBE_PATH))
        with patch("sqlite3.connect", side_effect=AssertionError("unexpected store read")), \
                patch("socket.socket", side_effect=AssertionError("unexpected network")), \
                patch.object(g, "preparation_gate", side_effect=AssertionError("unexpected authorization")):
            g.validate_preparation_record(self.historical, self.snapshot)
            g.validate_source_probe(original, g.SOURCE_SNAPSHOT)
            self.validate()


    def test_p4i_release_has_only_standard_closed_fields(self):
        original = deepcopy(self.receipts[3])
        self.assertEqual(set(original), {
            "schema_version", "snapshot_id", "role", "verdict", "agent_id", "read_only",
            "remote_ci", "pr_url", "merge_status", "vps_sync", "next_gate",
        })
        for field in original:
            self.receipts[3] = {key: value for key, value in original.items() if key != field}
            with self.subTest(missing=field), self.assertRaises(g.Block):
                self.validate()
        for field, bad in (("schema_version", True), ("role", "reviewer"), ("read_only", 1),
                           ("verdict", "PASS"), ("remote_ci", "PASS"), ("pr_url", ""),
                           ("merge_status", "merged"), ("vps_sync", "done"),
                           ("next_gate", "runtime_ready"), ("permission_grant", True)):
            self.receipts[3] = {**original, field: bad}
            with self.subTest(field=field), self.assertRaises(g.Block):
                self.validate()
        self.receipts[3] = original
        self.validate()

    def test_p4i_both_closed_gates_do_not_read_files_or_dispatch(self):
        with patch.object(g, "read_text", side_effect=AssertionError("file read")), \
                patch.object(g, "check", side_effect=AssertionError("check")), \
                patch.object(g, "validate_current_attestations", side_effect=AssertionError("approval")), \
                patch("socket.socket", side_effect=AssertionError("network")), \
                patch("socket.getaddrinfo", side_effect=AssertionError("DNS")), \
                patch("sqlite3.connect", side_effect=AssertionError("store")):
            for gate in (g.probe_gate, g.preparation_gate):
                for root in (ROOT, Path("absent-repository")):
                    for ci in (False, True):
                        with self.subTest(gate=gate.__name__, ci=ci), self.assertRaisesRegex(
                                g.Block, "P4M: historical .* capability is closed"):
                            gate(root, ci=ci)

    def test_p4i_four_attestations_do_not_invoke_historical_validators(self):
        self.assertEqual(len(self.receipts), 4)
        with patch.object(g, "validate_source_probe", side_effect=AssertionError("old probe")), \
                patch.object(g, "validate_preparation_record", side_effect=AssertionError("old preparation")):
            self.validate()
        for name in g.CURRENT_ATTESTATIONS:
            contents = {key: value for key, value in self.contents.items() if key != name}
            with self.subTest(missing=name), self.assertRaisesRegex(g.Block, "missing required receipt"):
                g.validate_current_attestations((self.contract, self.state, contents, []), self.current)

    def historical_checked(self):
        record = preparation_fixture(self.snapshot, prepared=True)
        current = {"schema_version": 1, "package_id": "P4E", "snapshot_id": self.snapshot,
                   "changed_paths": []}
        release = {**self.receipts[3], "preparation_digest": g.preparation_digest(record),
                   "pre_preparation": {"verdict": g.PRE_PREPARATION_RELEASE,
                                       "snapshot_id": self.snapshot, "agent_id": "fixture-release"}}
        review = {**self.receipts[2], "agent_id": "fixture-reviewer", "repair_cycles": 0}
        contents = dict(zip(g.ATTESTATIONS, map(json.dumps,
                        (current, self.receipts[1], review, release, record))))
        contents[g.RECEIPT_PATHS["prompt_audit"]] = json.dumps({"repair_cycles": 0})
        checked = ({"package_id": "P4E", "required_validation": g.VALIDATION_COMMANDS,
                    "next_gate": release["next_gate"]},
                   {"active_package": "P4E", "package_status": "implemented_unreleased",
                    "builder_agents": ["coordinator"]}, contents, [])
        return checked, current

    def test_historical_public_bindings_never_expand_current_exemptions(self):
        self.assertEqual(g.ATTESTATIONS, (
            "docs/reviews/p4e-bundle.json", "docs/reviews/p4e-validation.json",
            "docs/reviews/p4e-review.json", "docs/reviews/p4e-release.json",
            "docs/evidence/p4e-preparation.json"))
        self.assertEqual(len(g.CURRENT_ATTESTATIONS), 4)
        self.assertTrue(set(g.ATTESTATIONS).issubset(g.IMMUTABLE))
        self.assertTrue(set(g.ATTESTATIONS).issubset(g.PROTECTED))
        self.assertFalse(set(g.ATTESTATIONS) & set(g.CURRENT_ATTESTATIONS))
        self.assertEqual(self.current["attestation_paths"], list(g.CURRENT_ATTESTATIONS))
        self.assertEqual(self.contract["attestation_paths"], list(g.CURRENT_ATTESTATIONS))
        self.assertEqual(self.state["receipts"], g.CURRENT_RECEIPT_PATHS)

    def test_historical_public_validator_is_terminal_pure_and_fail_closed(self):
        checked, current = self.historical_checked()
        with patch.object(g, "check", side_effect=AssertionError("current check")), \
                patch.object(g, "gate", side_effect=AssertionError("current gate")), \
                patch.object(g, "preparation_gate", side_effect=AssertionError("operation")), \
                patch("sqlite3.connect", side_effect=AssertionError("store")), \
                patch("socket.socket", side_effect=AssertionError("network")):
            self.assertIsNone(g.validate_attestations(checked, current))
            original = json.loads(checked[2][g.PREPARATION_PATH])
            altered = deepcopy(original)
            altered["observation"]["new_live_runs"] = 1
            release = json.loads(checked[2][g.ATTESTATIONS[3]])
            contents = {**checked[2], g.PREPARATION_PATH: json.dumps(altered),
                        g.ATTESTATIONS[3]: json.dumps({
                            **release, "preparation_digest": g.preparation_digest(altered)})}
            with self.assertRaisesRegex(g.Block, "preparation"):
                g.validate_attestations((*checked[:2], contents, []), current)
            pending = {**original, "status": "NOT_RUN", "processed_at": None,
                       "observation": None, "reason": "awaiting_release"}
            contents[g.PREPARATION_PATH] = json.dumps(pending)
            with self.assertRaisesRegex(g.Block, "terminal preparation"):
                g.validate_attestations((*checked[:2], contents, []), current)
            with self.assertRaises(TypeError):
                g.validate_attestations(checked, current, pre_preparation=True)

    def test_historical_public_validator_cannot_accept_declared_p4i(self):
        checked, current = self.historical_checked()
        for index, key in ((0, "package_id"), (1, "active_package")):
            changed = list(deepcopy(checked))
            changed[index][key] = "P4I"
            with self.assertRaisesRegex(g.Block, "not current P4I approval"):
                g.validate_attestations(tuple(changed), current)
        with self.assertRaisesRegex(g.Block, "not current P4I approval"):
            g.validate_attestations(checked, {**current, "package_id": "P4I"})

    def test_active_gate_never_routes_legacy_schema_to_historical_helper(self):
        checked, _current = self.historical_checked()
        self.contents[g.CURRENT_RECEIPT_PATHS["release"]] = checked[2][g.ATTESTATIONS[3]]
        with patch.object(g, "check", return_value=self.checked), \
                patch.object(g, "validate_attestations", return_value=None) as historical:
            for ci in (False, True):
                with self.assertRaisesRegex(g.Block, "unknown fields"):
                    g.gate(ROOT, ci=ci)
            historical.assert_not_called()
        self.sync()
        with patch.object(g, "check", return_value=self.checked), \
                patch.object(g, "validate_attestations", side_effect=AssertionError("legacy route")):
            self.assertEqual(g.gate(ROOT), g.LOCAL_RELEASE)


class NbsAttestationTests(unittest.TestCase):
    """Pure authored approvals, never a real release or product operation."""

    def setUp(self):
        self.contract = tomllib.loads(g.read_text(ROOT, "contracts/p4n_nbs_ingestion.toml"))
        self.state = g.parse_json(g.read_text(ROOT, "project-state/state.json"))
        self.state["package_status"] = "implemented_unreleased"
        self.state["active_package"] = "P4N"
        self.state["limited_ingestion"] = deepcopy(g.LIMITED_INGESTION)
        self.state["repair_exception"] = deepcopy(g.REPAIR_EXCEPTION)
        self.state["live_integration"]["runtime_implementation"] = (
            "nbs_ingestion_model_deferred")
        self.contents = dict.fromkeys(g.PROTECTED, "offline snapshot fixture\n")
        for name in (*g.FROZEN_INPUT_HASHES, g.NBS_RECEIPT_PATHS["blueprint_review"],
                     g.NBS_RECEIPT_PATHS["prompt_audit"]):
            self.contents[name] = g.read_text(ROOT, name)
        self.contents["governance/policy.toml"] = g.git(ROOT, "show", f"{g.BASELINE}:governance/policy.toml")
        self.checked = (self.contract, self.state, self.contents, [])
        self.current = {**g.make_bundle(self.checked), "package_id": "P4N",
                        "attestation_paths": list(g.NBS_ATTESTATIONS)}
        self.snapshot = self.current["snapshot_id"]
        common = {"schema_version": 1, "snapshot_id": self.snapshot}
        self.receipts = [self.current, {
            **common, "role": "builder_validation", "verdict": "PASS",
            "commands": [{"command": command, "exit_code": 0} for command in g.VALIDATION_COMMANDS],
            "python_version": "3.12.10", "remote_ci": "NOT_RUN_AT_SNAPSHOT",
        }, {
            **common, "role": "reviewer", "verdict": "PASS", "agent_id": g.REPAIR_EXCEPTION["reviewer_agent"],
            "read_only": True, "findings": [], "repair_cycles": 2,
        }, {
            **common, "role": "release_checker", "verdict": g.LOCAL_RELEASE,
            "agent_id": "fixture-release", "read_only": True,
            "remote_ci": "NOT_RUN_AT_SNAPSHOT", "pr_url": None, "merge_status": "not_merged",
            "vps_sync": "not_requested", "next_gate": g.NBS_NEXT_GATE,
        }]
        self.pre_release = {**self.receipts[3], "verdict": g.PRE_PROBE_RELEASE}
        self.observation = nbs_fixture(self.snapshot)
        self.historical = {
            **common, "package_id": "P4E", "status": "NOT_RUN", "processed_at": None,
            "observation": None, "reason": "awaiting_release",
        }
        self.sync()

    def sync(self):
        records = [*self.receipts[:3], self.pre_release, self.receipts[3], self.observation]
        self.contents.update({name: json.dumps(value)
                              for name, value in zip(g.NBS_ATTESTATIONS, records)})


    def validate(self, *, pre_probe=False):
        self.sync()
        g.validate_historical_nbs_attestations(self.checked, self.current, pre_probe=pre_probe)

    def pending(self):
        from market_intelligence.nbs import not_run_record
        self.observation = not_run_record(self.snapshot)
        self.receipts[3] = g.not_run_release(self.snapshot)
        self.sync()

    def test_nbs_limited_ingestion_is_exact_in_contract_policy_and_state(self):
        for field, expected in g.LIMITED_INGESTION.items():
            bad_values = [None, {}, [], str(expected)] if type(expected) is not str else [None, 1, expected + "x"]
            if type(expected) is bool:
                bad_values += [not expected, int(expected)]
            if type(expected) is int:
                bad_values += [bool(expected), expected + 1, -1]
            for bad in bad_values:
                changed = {**g.LIMITED_INGESTION, field: bad}
                for index in (0, 1):
                    checked = list(deepcopy(self.checked))
                    checked[index]["limited_ingestion"] = changed
                    with self.subTest(binding=index, field=field, bad=bad), self.assertRaisesRegex(
                            g.Block, "limited_ingestion"):
                        g.validate_historical_nbs_attestations(tuple(checked), self.current)
                for value, validate in ((self.contract, g.validate_contract),
                                         (self.state, g.validate_state),
                                         (g.POLICY, lambda p: g.exact(p, g.POLICY, "policy"))):
                    with self.subTest(field=field, bad=bad), self.assertRaises(g.Block):
                        validate({**value, "limited_ingestion": changed})
        for changed in ({}, {**g.LIMITED_INGESTION, "permission_grant": True}):
            with self.assertRaises(g.Block):
                g.validate_contract({**self.contract, "limited_ingestion": changed})
        self.assertEqual(self.contract["merge_tier"], "D")
        self.assertTrue(all(not v for k, v in g.PERMISSIONS.items() if k != "research_only"))

    def test_nbs_pending_final_release_is_inert_and_complete(self):
        self.pending()
        self.assertNotIn("agent_id", self.receipts[3])
        self.assertEqual(set(self.contents) & set(g.NBS_ATTESTATIONS), set(g.NBS_ATTESTATIONS))
        self.validate(pre_probe=True)
        with self.assertRaisesRegex(g.Block, "terminal NBS observation"):
            self.validate()
        with patch.object(g, "check", side_effect=AssertionError("no current gate")):
            for ci in (False, True):
                with self.assertRaisesRegex(g.Block, "capability is closed"):
                    g.nbs_gate(ROOT, ci=ci)
        original = deepcopy(self.receipts[3])
        for key, value in (("agent_id", "fixture-release"), ("verdict", "PASS"),
                           ("snapshot_id", "0" * 64), ("schema_version", True),
                           ("reason", "approved"), ("permission_grant", True)):
            self.receipts[3] = {**original, key: value}
            with self.subTest(field=key), self.assertRaises(g.Block):
                self.validate(pre_probe=True)
        for key in original:
            self.receipts[3] = {k: v for k, v in original.items() if k != key}
            with self.subTest(missing=key), self.assertRaises(g.Block):
                self.validate(pre_probe=True)

    def test_nbs_no_final_approval_before_terminal_observation(self):
        final = deepcopy(self.receipts[3])
        self.pending()
        self.receipts[3] = final
        for pre_probe in (False, True):
            with self.assertRaisesRegex(g.Block, "terminal NBS observation"):
                self.validate(pre_probe=pre_probe)

    def test_nbs_failure_and_unknown_are_terminal_without_success_claims(self):
        for status in ("FAILED", "UNKNOWN"):
            self.observation = nbs_fixture(self.snapshot, status=status)
            self.validate()
            self.validate(pre_probe=True)
            self.assertEqual(self.observation["observation"]["benchmark_coverage"]["covered"], 0)
            final = self.receipts[3]
            self.receipts[3] = g.not_run_release(self.snapshot)
            self.validate(pre_probe=True)
            with self.assertRaises(g.Block):
                self.validate()
            self.receipts[3] = final

    def test_nbs_zero_eligible_metadata_is_terminal_not_source_success(self):
        from market_intelligence import nbs
        from market_intelligence.models import digest
        o = self.observation["observation"]
        o.update(request_outcome="success", observed_bytes=100, reason="empty_or_all_rejected_feed",
                 source_as_of=o["finished_at"], parse={"total": 0, "accepted": 0, "rejected": 0, "reasons": {}})
        o["capture"] = {"capture_id": "5" * 64, "content_hash": "6" * 64,
                        "response_hash": "7" * 64, "retrieved_at": o["finished_at"],
                        "first_seen_at": o["finished_at"], "expires_at": "2020-01-02T00:00:01Z",
                        "parser_version": "rss-v1", "retention": "metadata_only"}
        o["manifest_hash"] = digest(nbs._manifest(o))
        self.validate()
        self.assertEqual(self.observation["status"], "FAILED")

    def test_nbs_pre_and_final_release_roles_are_independent(self):
        for key in ("pre_probe_release", "release"):
            original = deepcopy(self.pre_release if key == "pre_probe_release" else self.receipts[3])
            for bad in (None, True, 1, {}, "", " ", " fixture-release", "fixture-release ",
                        self.receipts[2]["agent_id"], *self.state["builder_agents"]):
                changed = {**original, "agent_id": bad}
                if key == "pre_probe_release":
                    self.pre_release = changed
                else:
                    self.receipts[3] = changed
                for phase in (False, True):
                    with self.subTest(role=key, value=bad, phase=phase), self.assertRaises(g.Block):
                        self.validate(pre_probe=phase)
            if key == "pre_probe_release":
                self.pre_release = original
            else:
                self.receipts[3] = original
        self.validate()
        self.receipts[3]["agent_id"] = "another-independent-final-release"
        self.validate()

    def test_nbs_pre_release_schema_snapshot_verdict_are_exact(self):
        original = deepcopy(self.pre_release)
        cases = [{k: v for k, v in original.items() if k != key} for key in original]
        cases += [{**original, key: bad} for key, bad in (
            ("snapshot_id", "0" * 64), ("schema_version", True), ("role", "reviewer"),
            ("verdict", g.LOCAL_RELEASE), ("verdict", "NOT_RUN"), ("verdict", "PASS"),
            ("read_only", 1), ("remote_ci", "PASS"), ("pr_url", "https://example.invalid/pr/1"),
            ("merge_status", "merged"), ("vps_sync", "done"), ("next_gate", "dispatch"),
            ("allow_network", True))]
        for changed in cases:
            self.pre_release = changed
            for phase in (False, True):
                with self.subTest(receipt=changed, phase=phase), self.assertRaises(g.Block):
                    self.validate(pre_probe=phase)

    def test_nbs_all_six_receipts_are_required_before_authorization(self):
        self.pending()
        for name in g.NBS_ATTESTATIONS:
            original = self.contents[name]
            for value in (None, '{}', '"PASS"'):
                if value is None:
                    del self.contents[name]
                else:
                    self.contents[name] = value
                with self.subTest(name=name, value=value), self.assertRaises(g.Block):
                    g.validate_historical_nbs_attestations(self.checked, self.current, pre_probe=True)
                self.contents[name] = original

    def test_nbs_observation_status_snapshot_and_payload_fail_closed(self):
        original = deepcopy(self.observation)
        for key, bad in (("snapshot_id", "0" * 64), ("status", "PASS"), ("status", "BLOCK"),
                         ("status", "success"), ("status", True), ("status", None),
                         ("status", []), ("observation", {}), ("title", "private payload"),
                         ("permission_grant", True), ("package_id", "P4I")):
            self.observation = {**original, key: bad}
            for phase in (False, True):
                with self.subTest(key=key, bad=bad), self.assertRaises(g.Block):
                    self.validate(pre_probe=phase)
        duplicate = json.dumps(original).replace('"status": "FAILED"',
                                                '"status": "FAILED", "status": "RECORDED"')
        with self.assertRaisesRegex(g.Block, "duplicate field"):
            g.parse_json(duplicate)

    def test_nbs_gate_has_no_ci_bypass_in_python_or_cli(self):
        self.pending()
        with patch.object(g, "check", side_effect=AssertionError("closed gate reads")), \
                patch.object(g, "validate_historical_nbs_attestations", side_effect=AssertionError("approval")):
            for flags in ([], ["--ci"]):
                with self.assertRaisesRegex(g.Block, "capability is closed"):
                    g.nbs_gate(ROOT, ci=bool(flags))
                with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
                    self.assertEqual(g.main(["nbs-gate", *flags], root=ROOT), 1)
                self.assertEqual(out.getvalue(), "")
                self.assertIn("capability is closed", err.getvalue())

    def test_nbs_gate_preserves_complete_index_and_check_failures(self):
        for message in ("index/worktree mismatch", "missing commit candidate files", "Git branch",
                        "immutable historical input changed", "possible secret", "link/junction forbidden"):
            with patch.object(g, "check", side_effect=g.Block(message)) as check, \
                    patch.object(g, "validate_historical_nbs_attestations") as attestations:
                for ci in (False, True):
                    with self.assertRaisesRegex(g.Block, "capability is closed"):
                        g.nbs_gate(ROOT, ci=ci)
                check.assert_not_called()
                attestations.assert_not_called()

    def test_nbs_pending_still_requires_offline_validation_review_and_repair_bound(self):
        self.pending()
        for index, key, bad in ((1, "verdict", "NOT_RUN"), (1, "commands", []),
                                (1, "remote_ci", "PASS"), (2, "verdict", "BLOCK"),
                                (2, "agent_id", self.state["builder_agents"][0]),
                                (2, "repair_cycles", 1), (2, "repair_cycles", 3), (2, "repair_cycles", True)):
            original = deepcopy(self.receipts[index])
            self.receipts[index][key] = bad
            with self.subTest(index=index, key=key), self.assertRaises(g.Block):
                self.validate(pre_probe=True)
            self.receipts[index] = original
        for status in ("building", "contract_prepared_unreleased"):
            self.state["package_status"] = status
            with self.assertRaisesRegex(g.Block, "still building"):
                self.validate(pre_probe=True)
        self.state["package_status"] = "implemented_unreleased"
        path = g.NBS_RECEIPT_PATHS["prompt_audit"]
        audit = json.loads(self.contents[path])
        self.contents[path] = json.dumps({**audit, "repair_cycles": 1})
        self.receipts[2]["repair_cycles"] = 0
        with self.assertRaisesRegex(g.Block, "repair bound or reset"):
            self.validate(pre_probe=True)
        self.receipts[2]["repair_cycles"] = 1
        with self.assertRaisesRegex(g.Block, "repair bound or reset"):
            self.validate(pre_probe=True)
        self.receipts[2]["repair_cycles"] = 2
        self.validate(pre_probe=True)

    def test_nbs_validator_is_pure_and_rejects_foreign_cached_modules(self):
        import types
        foreign = types.ModuleType("market_intelligence.nbs")
        foreign.validate_observation_record = lambda value, snapshot: value
        with patch.dict(sys.modules, {"market_intelligence.nbs": foreign}), \
                patch("socket.socket", side_effect=AssertionError("network")), \
                patch("socket.getaddrinfo", side_effect=AssertionError("DNS")), \
                patch("sqlite3.connect", side_effect=AssertionError("store")), \
                patch.object(Path, "home", side_effect=AssertionError("user store")):
            g.validate_nbs_observation(self.observation, self.snapshot)
            with self.assertRaises(g.Block):
                g.validate_nbs_observation({**self.observation, "status": "PASS"}, self.snapshot)
        self.assertFalse(any(name.startswith("_p4n_observation_validation_") for name in sys.modules))

    def test_nbs_validator_loads_explicit_checkout_and_cleans_namespace(self):
        with tempfile.TemporaryDirectory(prefix="p4n-validator-") as directory:
            root = Path(directory)
            package = root / "market_intelligence"
            package.mkdir()
            (package / "__init__.py").write_text("", encoding="utf-8")
            module = package / "nbs.py"
            module.write_text("def validate_observation_record(value, snapshot_id):\n"
                              "    raise ValueError('foreign checkout private detail')\n", encoding="utf-8")
            with self.assertRaisesRegex(g.Block, "invalid or unavailable") as error:
                g.validate_nbs_observation(self.observation, self.snapshot, root=root)
            self.assertNotIn("private detail", str(error.exception))
            namespace = "_p4n_observation_validation_" + g.sha256(str(root.resolve()))
            with patch.dict(sys.modules, {namespace: object()}), self.assertRaisesRegex(g.Block, "recursive"):
                g.validate_nbs_observation(self.observation, self.snapshot, root=root)
        g.validate_nbs_observation(self.observation, self.snapshot)
        self.assertFalse(any(name.startswith("_p4n_observation_validation_") for name in sys.modules))


class RepairExceptionTests(unittest.TestCase):
    """The named P4N exception is exact, non-resettable and non-inheritable."""

    setUp = NbsAttestationTests.setUp
    sync = NbsAttestationTests.sync
    validate = NbsAttestationTests.validate
    historical_checked = ProbeAttestationTests.historical_checked

    def test_exception_is_pinned_to_actual_block_and_same_reviewer(self):
        expected = {
            "id": "p4n-channel-rights-r1", "authorized_by": "user",
            "authorization": "User explicitly authorized one additional bounded R1 repair after the P4N channel-rights BLOCK.",
            "additional_cycles": 1, "maximum_total": 2,
            "blocked_snapshot": "2ff1ca0f9a5e2dc97e81372a2c556707810fc03e7528ba90bb2a1566cee4d06d",
            "blocked_review_digest": "8b57fc068fba730d89c706911a96b1cb5721ca47fe619ee23a6264f1362e23c2",
            "reviewer_agent": "01a1eec4-2066-76f1-9ba5-7dad5674a563",
            "finding": "P1: unknown namespaced or nested channel-level rights restrictions are ignored.",
            "product_paths": ["market_intelligence/nbs.py", "tests/product/test_nbs.py"],
            "scope": "Reject ambiguous channel-level rights without retaining titles; add regression tests. "
                     "No operational permission, data-source, file allowlist or merge-policy expansion.",
        }
        self.assertEqual(g.REPAIR_EXCEPTION, expected)
        for value in (self.contract, self.state, g.NBS_POLICY):
            self.assertEqual(value["repair_exception"], expected)
        self.assertEqual((self.contract["max_repair_cycles"], g.NBS_POLICY["max_repair_cycles"]), (1, 1))
        for pre_probe in (False, True):
            self.validate(pre_probe=pre_probe)

    def test_exception_missing_or_inherited_objects_are_rejected_everywhere(self):
        historical = tomllib.loads(g.read_text(ROOT, "contracts/p1_sources_evidence.toml"))["repair_exception"]
        for original, validate in ((self.contract, lambda value: g.exact(value, self.contract, "historical contract")), (self.state, lambda value: g.exact(value, self.state, "historical state")),
                                    (g.NBS_POLICY, lambda value: g.exact(value, g.NBS_POLICY, "policy"))):
            with self.assertRaises(g.Block):
                validate({key: value for key, value in original.items() if key != "repair_exception"})
            for bad in (None, True, 2, "authorized", [], {}, {"additional_cycles": 1}, historical):
                with self.subTest(bad=bad), self.assertRaises(g.Block):
                    validate({**original, "repair_exception": bad})

    def test_exception_every_field_type_value_and_extra_field_is_closed(self):
        expected = g.REPAIR_EXCEPTION
        variants = [{**expected, "further_repairs_allowed": True}]
        for key, value in expected.items():
            variants.append({name: item for name, item in expected.items() if name != key})
            bad_values = [None, True, {}, []]
            if type(value) is int:
                bad_values += [value - 1, value + 1, float(value), str(value)]
            elif type(value) is list:
                bad_values += [list(reversed(value)), value + ["market_intelligence/source_policy.py"],
                               value[:-1], [*value, value[0]]]
            else:
                bad_values += [value + " changed", ""]
            variants.extend({**expected, key: bad} for bad in bad_values)
        for exception in variants:
            for original, validate in ((self.contract, lambda value: g.exact(value, self.contract, "historical contract")), (self.state, lambda value: g.exact(value, self.state, "historical state")),
                                        (g.NBS_POLICY, lambda value: g.exact(value, g.NBS_POLICY, "policy"))):
                with self.subTest(exception=exception), self.assertRaises(g.Block):
                    validate({**original, "repair_exception": exception})

    def test_current_gates_do_not_fall_back_when_any_exception_binding_is_absent(self):
        for pre_probe in (False, True):
            for index in (0, 1):
                for bad in (None, {**g.REPAIR_EXCEPTION, "maximum_total": 3}):
                    changed = list(deepcopy(self.checked))
                    if bad is None:
                        del changed[index]["repair_exception"]
                    else:
                        changed[index]["repair_exception"] = bad
                    with self.subTest(binding=index, bad=bad), self.assertRaisesRegex(g.Block, "repair_exception"):
                        g.validate_historical_nbs_attestations(tuple(changed), self.current, pre_probe=pre_probe)
            for policy in ({key: value for key, value in g.NBS_POLICY.items() if key != "repair_exception"},
                           {**g.NBS_POLICY, "repair_exception": {**g.REPAIR_EXCEPTION, "additional_cycles": 2}}):
                with patch.object(g.tomllib, "loads", return_value=policy), self.assertRaises(g.Block):
                    g.validate_historical_nbs_attestations(self.checked, self.current, pre_probe=pre_probe)
            contents = {key: value for key, value in self.contents.items() if key != "governance/policy.toml"}
            with self.assertRaisesRegex(g.Block, "missing policy"):
                g.validate_historical_nbs_attestations((*self.checked[:2], contents, []), self.current,
                                                pre_probe=pre_probe)

    def test_current_review_exactly_two_rejects_reset_third_repair_and_wrong_types(self):
        for pre_probe in (False, True):
            for bad in (-1, 0, 1, 3, 4, True, False, None, "2", 2.0, {}, []):
                self.receipts[2]["repair_cycles"] = bad
                with self.subTest(pre_probe=pre_probe, bad=bad), self.assertRaisesRegex(g.Block, "repair bound or reset"):
                    self.validate(pre_probe=pre_probe)
            self.receipts[2]["repair_cycles"] = 2
            self.validate(pre_probe=pre_probe)

    def test_current_review_cannot_substitute_another_independent_agent(self):
        expected = g.REPAIR_EXCEPTION["reviewer_agent"]
        for bad in (None, True, 1, "", expected + " ", " " + expected, g.AUDIT_AGENT,
                    "other-independent-reviewer", self.receipts[3]["agent_id"], *self.state["builder_agents"]):
            self.receipts[2]["agent_id"] = bad
            for pre_probe in (False, True):
                with self.subTest(pre_probe=pre_probe, agent=bad), self.assertRaisesRegex(g.Block, "same approved repair Reviewer"):
                    self.validate(pre_probe=pre_probe)
        self.receipts[2]["agent_id"] = expected
        self.validate()

    def test_historical_defaults_cannot_inherit_current_exception_or_two_cycles(self):
        checked, current = self.historical_checked()
        path = g.RECEIPT_PATHS["reviewer"]
        original = json.loads(checked[2][path])
        for cycles in (0, 1):
            checked[2][path] = json.dumps({**original, "repair_cycles": cycles})
            g.validate_attestations(checked, current)
        for cycles in (2, 3, True, "1", -1):
            checked[2][path] = json.dumps({**original, "repair_cycles": cycles})
            with self.subTest(cycles=cycles), self.assertRaisesRegex(g.Block, "repair bound or reset"):
                g.validate_attestations(checked, current)
        checked[2][path] = json.dumps(original)
        for index in (0, 1):
            changed = list(deepcopy(checked))
            changed[index]["repair_exception"] = deepcopy(g.REPAIR_EXCEPTION)
            with self.assertRaisesRegex(g.Block, "cannot be inherited"):
                g.validate_attestations(tuple(changed), current)
        with self.assertRaisesRegex(g.Block, "repair exception contract package"):
            g._validate_review_attestations(
                checked, current, paths=g.ATTESTATIONS[:4], audit_path=g.RECEIPT_PATHS["prompt_audit"],
                next_gate=g.NEXT_GATE, release_fields=("pre_preparation", "preparation_digest"), p4n_repair=True)

    def test_current_exception_never_raises_planning_audit_default(self):
        path = g.NBS_RECEIPT_PATHS["prompt_audit"]
        original = json.loads(self.contents[path])
        for cycles in (2, 3, True, -1, "0", None):
            self.contents[path] = json.dumps({**original, "repair_cycles": cycles})
            for pre_probe in (False, True):
                with self.subTest(cycles=cycles), self.assertRaisesRegex(g.Block, "planning repair bound or reset"):
                    self.validate(pre_probe=pre_probe)
        self.contents[path] = json.dumps(original)
        self.validate()


class DailyMarketGovernanceTests(unittest.TestCase):
    """P4M in-memory regression fixtures are not real review or release evidence."""

    setUp = ProbeAttestationTests.setUp
    sync = ProbeAttestationTests.sync
    validate = ProbeAttestationTests.validate

    def test_p4m_scope_and_four_current_receipts_are_exact(self):
        self.assertEqual((len(g.ALLOWED), len(g.IMMUTABLE), len(g.PROTECTED)), (200, 176, 196))
        self.assertEqual(g.CURRENT_ATTESTATIONS, (
            "docs/reviews/p4m-bundle.json", "docs/reviews/p4m-validation.json",
            "docs/reviews/p4m-review.json", "docs/reviews/p4m-release.json"))
        self.assertNotIn("observation", g.CURRENT_RECEIPT_PATHS)
        self.assertNotIn("pre_probe_release", g.CURRENT_RECEIPT_PATHS)
        self.validate()

    def test_both_actual_audits_bind_all_twenty_frozen_references(self):
        self.assertEqual(len(g.AUDIT_INPUTS), 20)
        for role, key in (("blueprint_reviewer", "blueprint_review"), ("prompt_auditor", "prompt_audit")):
            receipt = g.parse_json(g.read_text(ROOT, g.CURRENT_RECEIPT_PATHS[key]))
            self.assertEqual(set(receipt["files"]), set(g.AUDIT_INPUTS))
            self.assertEqual(receipt["agent_id"], g.AUDIT_AGENT)
            g.validate_prerequisite(receipt, role, g.AUDIT_INPUTS, self.contents)
            for name in g.AUDIT_INPUTS:
                changed = deepcopy(receipt)
                del changed["files"][name]
                with self.subTest(role=role, missing=name), self.assertRaises(g.Block):
                    g.validate_prerequisite(changed, role, g.AUDIT_INPUTS, self.contents)

    def test_historical_nbs_six_receipts_are_all_immutable_protected_inputs(self):
        self.assertEqual(len(g.NBS_ATTESTATIONS), 6)
        for name in g.NBS_ATTESTATIONS:
            self.assertIn(name, g.IMMUTABLE)
            self.assertIn(name, g.PROTECTED)
            self.assertNotIn(name, g.CURRENT_ATTESTATIONS)
        for name in ("contracts/p4n_nbs_ingestion.toml", "templates/p4n_builder_prompt.md",
                     "docs/reviews/p4n-builder-prompt.md", "docs/reviews/p4n-prompt-audit.json",
                     "docs/reviews/p4n-entrypoint-review.json", "docs/NBS_EVIDENCE_INGESTION.md",
                     "market_intelligence/nbs.py", "market_intelligence/source_policy.py",
                     "tests/product/test_nbs.py"):
            self.assertIn(name, g.IMMUTABLE)

    def test_current_release_never_calls_historical_or_observation_validators(self):
        with patch.object(g, "validate_historical_nbs_attestations", side_effect=AssertionError("NBS")), \
                patch.object(g, "validate_nbs_observation", side_effect=AssertionError("observation")), \
                patch.object(g, "validate_attestations", side_effect=AssertionError("P4E")), \
                patch.object(g, "validate_preparation_record", side_effect=AssertionError("preparation")):
            self.validate()
            with patch.object(g, "check", return_value=self.checked):
                self.assertEqual(g.gate(ROOT), g.LOCAL_RELEASE)

    def test_current_review_has_one_total_repair_without_historical_exception(self):
        for cycles in (0, 1):
            self.receipts[2]["repair_cycles"] = cycles
            self.validate()
        for cycles in (2, 3, -1, True, False, None, "1", 1.0):
            self.receipts[2]["repair_cycles"] = cycles
            with self.subTest(cycles=cycles), self.assertRaisesRegex(g.Block, "repair bound or reset"):
                self.validate()
        path = g.CURRENT_RECEIPT_PATHS["prompt_audit"]
        audit = json.loads(self.contents[path])
        self.contents[path] = json.dumps({**audit, "repair_cycles": 1})
        self.receipts[2]["repair_cycles"] = 0
        with self.assertRaisesRegex(g.Block, "repair bound or reset"):
            self.validate()
        self.receipts[2]["repair_cycles"] = 1
        self.validate()

    def test_current_bindings_reject_even_exact_old_ingestion_and_exception(self):
        for field, value in (("limited_ingestion", g.LIMITED_INGESTION),
                             ("repair_exception", g.REPAIR_EXCEPTION)):
            with self.assertRaises(g.Block):
                g.validate_contract({**self.contract, field: value})
            with self.assertRaises(g.Block):
                g.validate_state({**self.state, field: value})
            with self.assertRaises(g.Block):
                g.exact({**g.POLICY, field: value}, g.POLICY, "policy")
            for index in (0, 1):
                changed = list(deepcopy(self.checked))
                changed[index][field] = deepcopy(value)
                with self.assertRaisesRegex(g.Block, "cannot be inherited"):
                    g.validate_current_attestations(tuple(changed), self.current)

    def test_historical_package_cannot_approve_current_snapshot(self):
        for index, field in ((0, "package_id"), (1, "active_package")):
            changed = list(deepcopy(self.checked))
            changed[index][field] = "P4N"
            with self.assertRaisesRegex(g.Block, "current .* package"):
                g.validate_current_attestations(tuple(changed), self.current)
        with self.assertRaisesRegex(g.Block, "current bundle package"):
            g.validate_current_attestations(self.checked, {**self.current, "package_id": "P4N"})
        with self.assertRaises(TypeError):
            g.validate_current_attestations(self.checked, self.current, pre_probe=True)

    def test_old_gates_always_block_without_any_file_store_or_network_access(self):
        with patch.object(g, "read_text", side_effect=AssertionError("read")), \
                patch.object(g, "check", side_effect=AssertionError("check")), \
                patch.object(g, "validate_current_attestations", side_effect=AssertionError("approval")), \
                patch.object(g, "validate_historical_nbs_attestations", side_effect=AssertionError("history")), \
                patch("socket.socket", side_effect=AssertionError("network")), \
                patch("socket.getaddrinfo", side_effect=AssertionError("DNS")), \
                patch("sqlite3.connect", side_effect=AssertionError("store")):
            for gate in (g.nbs_gate, g.probe_gate, g.preparation_gate):
                for root in (ROOT, Path("absent-checkout")):
                    for ci in (False, True):
                        with self.subTest(gate=gate.__name__, root=root, ci=ci), self.assertRaisesRegex(
                                g.Block, "capability is closed"):
                            gate(root, ci=ci)

    def test_current_readiness_waits_for_actual_implementation_and_receipts(self):
        for status in ("contract_prepared_unreleased", "building"):
            self.state["package_status"] = status
            self.state["live_integration"]["runtime_implementation"] = "daily_market_planned_model_deferred"
            g.validate_state(self.state)
            with self.assertRaisesRegex(g.Block, "still building"):
                self.validate()
        self.state["package_status"] = "implemented_unreleased"
        self.state["live_integration"]["runtime_implementation"] = "daily_market_evidence_model_deferred"
        g.validate_state(self.state)
        for name in g.CURRENT_ATTESTATIONS:
            contents = {key: value for key, value in self.contents.items() if key != name}
            with self.assertRaisesRegex(g.Block, "missing required receipt"):
                g.validate_current_attestations((*self.checked[:2], contents, []), self.current)

    def test_historical_failed_observation_is_not_relabelled_or_reauthorized(self):
        name = "docs/evidence/p4n-nbs-observation.json"
        text = g.read_text(ROOT, name)
        self.assertEqual(g.sha256(text), g.FROZEN_INPUT_HASHES[name])
        value = g.parse_json(text)
        self.assertEqual(value["status"], "FAILED")
        self.assertEqual(value["observation"]["reason"], "content_length")
        self.assertEqual(value["observation"]["observed_bytes"], 0)
        self.assertIsNone(value["observation"]["capture"])
        self.assertIsNone(value["observation"]["parse"])
        self.assertEqual(value["snapshot_id"], g.prior_merge_evidence()["prior_snapshot"])

    def test_p4m_private_input_and_time_limits_remain_frozen_contract_requirements(self):
        text = g.read_text(ROOT, g.CONTRACT_PATH)
        for term in ("512000 bytes", "1024 data rows", "10 columns", "128 characters",
                     "published_at=null", "calendar_completeness_verified=false",
                     "current_market_freshness=unverified", "input coverage2/5",
                     "live verification remains0/5", "documentation_endpoint_not_acquisition_endpoint"):
            self.assertIn(term, text)
        self.assertEqual(g.sha256(text), g.FROZEN_INPUT_HASHES[g.CONTRACT_PATH])


class GovernanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_contract = (ROOT / g.CONTRACT_PATH).read_text(encoding="utf-8")
        cls.contract = tomllib.loads(cls.original_contract)
        cls.source_inputs = {
            name: ("# Authored temporary-repository fixture only.\n"
                   if name in ("market_intelligence/daily_market.py", "tests/product/test_daily_market.py")
                   and not (ROOT / name).is_file() else (ROOT / name).read_text(encoding="utf-8"))
            for name in g.PROTECTED
        }

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="p4c-governance-")
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
        # Only the new P4N merge observation points at this temporary baseline.
        # Historical contracts, products and receipts retain their bytes.
        prior = json.loads(self.source_inputs["docs/reviews/p4n-merge.json"])
        self.write_json("docs/reviews/p4n-merge.json", {**prior, "merge_commit": baseline,
                                                       "merge_ci_head": baseline})
        self.state = {
            "schema_version": 1, "active_package": "P4M", "active_contract": g.CONTRACT_PATH,
            "branch": g.BRANCH, "baseline": baseline, "package_status": "implemented_unreleased",
            "builder_agents": ["coordinator", "implementation-builder"],
            "stage_registry": [
                {"stage": i, "package": package,
                 "status": "complete" if i in (0, 2) else "engineering_merged_source_gaps" if i == 1 else
                           "engineering_merged_live_gaps" if i in (3, 4, 5, 6, 7)
                           else "deferred" if i == 9 else "offline_subset_merged_live_gaps"}
                for i, package in enumerate(("P0", "P1", "P1", "P2", "P2", "P3", "P3", "P4", "P4/P5", "P5"))
            ],
            "milestones": {"research_mvp": "not_implemented", "forward_observation": "not_started",
                           "vps_release": "not_authorized"},
            "source_readiness": "BLOCKED_MARKET_SOURCES", "dependency": g.dependency(),
            "github": {"remote_creation_authorized": False, "owner": "kamrean",
                       "repository": "a-share-market-intelligence-public",
                       "visibility": "public", "remote_ci": "NOT_RUN_AT_SNAPSHOT", "merge_status": "not_merged"},
            "receipts": g.CURRENT_RECEIPT_PATHS,
            "next_gate": g.NEXT_GATE,
            "last_updated": "2026-11-13",
            "live_integration": {
                "entry_status": "human_merged",
                "runtime_implementation": "daily_market_evidence_model_deferred",
                "readiness": "BLOCKED_EXTERNAL_PREREQUISITES",
                "prerequisite_register": g.PREREQUISITE_PATH, "permissions_enabled": False,
            },
        }
        self.write_json("project-state/state.json", self.state)
        contract = tomllib.loads(g.read_text(self.root, g.CONTRACT_PATH))
        self.write(g.PROMPT_PATH, g.render_prompt(contract, self.source_inputs[g.TEMPLATE_PATH]))
        # The synthetic repository has its own baseline and rendered prompt.
        # Bind those test-only bytes once; later mutations must still fail.
        patch.object(g, "FROZEN_INPUT_HASHES", {
            name: g.sha256(g.read_text(self.root, name)) for name in g.FROZEN_INPUT_HASHES
        }).start()
        for role, receipt_name, names in (
            ("blueprint_reviewer", "blueprint_review", g.AUDIT_INPUTS),
            ("prompt_auditor", "prompt_audit", g.AUDIT_INPUTS),
        ):
            # Rebind copies of actual audits only inside the temporary fixture.
            # These altered hashes are test data, not new real-world approval.
            receipt = json.loads(self.source_inputs[g.CURRENT_RECEIPT_PATHS[receipt_name]])
            receipt["files"] = {name: g.sha256(g.read_text(self.root, name)) for name in names}
            if role == "blueprint_reviewer":
                receipt["baseline"] = baseline
            self.write_json(g.CURRENT_RECEIPT_PATHS[receipt_name], receipt)

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
        self.write_json(g.CURRENT_ATTESTATIONS[0], current)
        common = {"schema_version": 1, "snapshot_id": current["snapshot_id"]}
        self.write_json(g.CURRENT_ATTESTATIONS[1], {
            **common, "role": "builder_validation", "verdict": "PASS",
            "commands": [{"command": command, "exit_code": 0} for command in g.VALIDATION_COMMANDS],
            "python_version": "3.12.10", "remote_ci": "NOT_RUN_AT_SNAPSHOT",
        })
        self.write_json(g.CURRENT_ATTESTATIONS[2], {
            **common, "role": "reviewer", "verdict": "PASS", "agent_id": "independent-reviewer",
            "read_only": True, "findings": [], "repair_cycles": 0,
        })
        self.write_json(g.CURRENT_RECEIPT_PATHS["release"], {
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
        self.assertEqual(len(g.ALLOWED), 200)
        self.assertEqual(len(g.PROTECTED), 196)
        self.assertEqual(len(g.IMMUTABLE), 176)
        self.assertIn("docs/reviews/p0-review.json", g.PROTECTED)
        self.assertIn("docs/reviews/p0-review.json", g.IMMUTABLE)
        self.assertIn("docs/PUBLICATION_PROVENANCE.json", g.PROTECTED)
        for field, bad in (("allowed_paths", list(g.ALLOWED) + ["src/product.py"]),
                           ("attestation_paths", ["docs/"]), ("baseline", "0" * 40),
                           ("immutable_paths", list(g.IMMUTABLE[:-1])),
                           ("attestation_paths", [name.replace("p4m-", "p4-") for name in g.CURRENT_ATTESTATIONS]),
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
        self.state["live_integration"]["runtime_implementation"] = "daily_market_planned_model_deferred"
        self.write_json("project-state/state.json", self.state)
        g.check(self.root)
        self.attest()
        with self.assertRaisesRegex(g.Block, "still building"):
            g.gate(self.root)
        self.state["package_status"] = "contract_prepared_unreleased"
        g.validate_state(self.state)
        for bad in ("contract_prepared", "implemented", "complete", "merged", None, True):
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
        self.assertEqual(self.cli("check"), (0, f"PASS ({g.GOVERNANCE_ONLY})\n", ""))
        current = g.bundle(self.root)
        self.assertEqual(current["scope_result"], "PASS")
        self.assertEqual(set(current["protected_file_hashes"]), set(g.PROTECTED))
        self.assertIn(g.CURRENT_RECEIPT_PATHS["blueprint_review"], current["protected_file_hashes"])
        self.assertIn(g.CURRENT_RECEIPT_PATHS["prompt_audit"], current["protected_file_hashes"])
        with self.assertRaises(g.Block):
            g.gate(self.root)

    def test_prompt_exact_newline_and_verify(self):
        status, rendered, error = self.cli("prompt")
        self.assertEqual((status, error), (0, ""))
        self.assertTrue(rendered.endswith("\n"))
        self.assertFalse(rendered.endswith("\n\n"))
        self.assertEqual(rendered, g.read_text(self.root, g.PROMPT_PATH))
        self.assertEqual(self.cli("prompt", "--verify"), (0, f"PASS ({g.GOVERNANCE_ONLY})\n", ""))
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
        for name in (g.CURRENT_RECEIPT_PATHS["blueprint_review"], g.CURRENT_RECEIPT_PATHS["prompt_audit"]):
            original = self.load_json(name)
            bad_hashes = dict(original["files"])
            bad_hashes[next(iter(bad_hashes))] = "0" * 64
            for field, value in (("files", {}), ("files", bad_hashes),
                                 ("files", {**original["files"], "docs/CONSTRUCTION_LOG.md": "0" * 64}),
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
        for name in g.CURRENT_ATTESTATIONS:
            self.assertEqual(original, g.snapshot({**contents, name: "unhashed receipt"})[0])

    def test_factual_price_and_volume_are_allowed(self):
        self.write("docs/CONSTRUCTION_LOG.md", "Historical market price and traded volume are factual research data.\n")
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
        for name in g.CURRENT_ATTESTATIONS:
            self.write_json(name, {"arbitrary": "non-secret pending receipt"})
        self.assertEqual(before, g.bundle(self.root)["snapshot_id"])
        for name in g.CURRENT_ATTESTATIONS:
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
                     "docs//secret", "./docs/CONSTRUCTION_LOG.md", "docs/CONSTRUCTION_LOG.md.", "docs/CONSTRUCTION_LOG.md ", "bad\npath"):
            with self.subTest(path=name), self.assertRaises(g.Block):
                g.safe_path(self.root, name)

    def test_symlink_escape_and_junction_are_rejected(self):
        with patch.object(Path, "is_symlink", return_value=True):
            with self.assertRaisesRegex(g.Block, "link/junction"):
                g.safe_path(self.root, "docs/CONSTRUCTION_LOG.md")
        with patch.object(Path, "is_junction", return_value=True):
            with self.assertRaisesRegex(g.Block, "link/junction"):
                g.safe_path(self.root, "docs/CONSTRUCTION_LOG.md")
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
        self.assertEqual(self.cli("gate"), (0, f"LOCAL_READY_FOR_PR ({g.GOVERNANCE_ONLY})\n", ""))
        self.run_git("add", ".")
        self.run_git("commit", "-m", "temporary reviewed package")
        self.assertEqual(g.bundle(self.root)["snapshot_id"], before["snapshot_id"])
        self.assertEqual(g.gate(self.root), "LOCAL_READY_FOR_PR")
        self.run_git("checkout", "--detach", "HEAD")
        self.assertEqual(self.cli("gate", "--ci"), (0, f"LOCAL_READY_FOR_PR ({g.GOVERNANCE_ONLY})\n", ""))

    def assert_candidate_blocked(self, message):
        for command in ("check", "bundle", "gate"):
            for flags in ((), ("--ci",)):
                with self.subTest(command=command, flags=flags):
                    status, out, error = self.cli(command, *flags)
                    self.assertEqual((status, out), (1, ""))
                    self.assertIn(message, error)
                    self.assertNotIn("Traceback", error)
        for command in ("nbs-gate", "probe-gate", "preparation-gate"):
            for flags in ((), ("--ci",)):
                status, out, error = self.cli(command, *flags)
                self.assertEqual((status, out), (1, ""))
                self.assertIn("capability is closed", error)
                self.assertNotIn("Traceback", error)

    def test_staged_only_content_blocks_check_bundle_and_gate(self):
        approved = self.attest()
        text = g.read_text(self.root, "docs/CONSTRUCTION_LOG.md")
        self.write("docs/CONSTRUCTION_LOG.md", text + "Unapproved staged decision.\n")
        self.run_git("add", "docs/CONSTRUCTION_LOG.md")
        self.write("docs/CONSTRUCTION_LOG.md", text)
        contents = {name: g.read_text(self.root, name) for name in g.PROTECTED}
        self.assertEqual(g.snapshot(contents)[0], approved["snapshot_id"])
        self.assert_candidate_blocked("index/worktree mismatch: docs/CONSTRUCTION_LOG.md")

    def test_staged_only_synthetic_secret_is_scanned(self):
        self.attest()
        text = g.read_text(self.root, "docs/CONSTRUCTION_LOG.md")
        synthetic = "ghp_" + "A" * 36
        self.write("docs/CONSTRUCTION_LOG.md", text + synthetic + "\n")
        self.run_git("add", "docs/CONSTRUCTION_LOG.md")
        self.write("docs/CONSTRUCTION_LOG.md", text)
        self.assert_candidate_blocked("possible secret in index:docs/CONSTRUCTION_LOG.md")
        self.assertNotIn(synthetic, self.cli("gate")[2])

    def test_replacement_ref_cannot_substitute_approved_index_content(self):
        self.attest()
        approved = g.read_text(self.root, "docs/CONSTRUCTION_LOG.md")
        good_blob = self.run_git("rev-parse", ":docs/CONSTRUCTION_LOG.md").strip()
        altered = approved + "Unapproved staged decision.\n"
        self.write("docs/CONSTRUCTION_LOG.md", altered)
        self.run_git("add", "docs/CONSTRUCTION_LOG.md")
        bad_blob = self.run_git("rev-parse", ":docs/CONSTRUCTION_LOG.md").strip()
        self.write("docs/CONSTRUCTION_LOG.md", approved)
        self.run_git("replace", bad_blob, good_blob)
        self.assertEqual(self.run_git("rev-parse", "refs/replace/" + bad_blob).strip(), good_blob)
        self.assertEqual(self.run_git("cat-file", "blob", bad_blob), altered)
        self.assert_candidate_blocked("index/worktree mismatch: docs/CONSTRUCTION_LOG.md")

    def test_committed_mismatch_cannot_hide_behind_approved_worktree(self):
        self.attest()
        approved = g.read_text(self.root, "docs/CONSTRUCTION_LOG.md")
        altered = approved + "Unapproved committed decision.\n"
        self.write("docs/CONSTRUCTION_LOG.md", altered)
        self.run_git("add", "docs/CONSTRUCTION_LOG.md")
        self.write("docs/CONSTRUCTION_LOG.md", approved)
        self.run_git("commit", "-m", "temporary mismatched candidate")
        self.assertEqual(self.run_git("show", "HEAD:docs/CONSTRUCTION_LOG.md"), altered)
        self.assertEqual(g.read_text(self.root, "docs/CONSTRUCTION_LOG.md"), approved)
        self.assert_candidate_blocked("index/worktree mismatch: docs/CONSTRUCTION_LOG.md")

    def test_index_flags_cannot_mask_content_divergence(self):
        self.attest()
        approved = g.read_text(self.root, "docs/CONSTRUCTION_LOG.md")
        self.write("docs/CONSTRUCTION_LOG.md", approved + "Unapproved staged decision.\n")
        self.run_git("add", "docs/CONSTRUCTION_LOG.md")
        self.write("docs/CONSTRUCTION_LOG.md", approved)
        for flag in ("--assume-unchanged", "--skip-worktree"):
            self.run_git("update-index", flag, "docs/CONSTRUCTION_LOG.md")
            self.assert_candidate_blocked("index/worktree mismatch: docs/CONSTRUCTION_LOG.md")
            self.run_git("update-index", "--no-" + flag[2:], "docs/CONSTRUCTION_LOG.md")

    def test_receipt_index_and_worktree_divergence_are_rejected(self):
        self.attest()
        for name in g.CURRENT_ATTESTATIONS:
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
        for name in ("docs/CONSTRUCTION_LOG.md", g.CURRENT_RECEIPT_PATHS["release"]):
            self.run_git("update-index", "--force-remove", "--", name)
            self.assertEqual(self.cli("check")[0], 0)
            self.assertEqual(g.bundle(self.root)["snapshot_id"], approved["snapshot_id"])
            self.assertEqual(self.cli("gate")[0], 1)
            self.run_git("add", name)
        self.assertEqual(g.gate(self.root), "LOCAL_READY_FOR_PR")

    def test_staged_deletion_of_head_input_or_receipt_is_rejected(self):
        self.attest()
        self.run_git("commit", "-m", "temporary complete package")
        for name in ("docs/CONSTRUCTION_LOG.md", g.CURRENT_ATTESTATIONS[2]):
            self.run_git("update-index", "--force-remove", "--", name)
            self.assertTrue((self.root / name).is_file())
            self.assert_candidate_blocked("missing commit candidate files")
            self.run_git("add", name)

    def test_index_file_types_and_invalid_utf8_are_rejected(self):
        self.attest()
        approved = g.read_text(self.root, "docs/CONSTRUCTION_LOG.md")
        blob = self.run_git("rev-parse", ":docs/CONSTRUCTION_LOG.md").strip()
        head = self.run_git("rev-parse", "HEAD").strip()
        for mode, oid in (("120000", blob), ("160000", head)):
            self.run_git("update-index", "--cacheinfo", f"{mode},{oid},docs/CONSTRUCTION_LOG.md")
            self.assert_candidate_blocked("index: non-regular file: docs/CONSTRUCTION_LOG.md")
            self.run_git("update-index", "--cacheinfo", f"100644,{blob},docs/CONSTRUCTION_LOG.md")
        (self.root / "docs/CONSTRUCTION_LOG.md").write_bytes(b"\xff")
        self.run_git("add", "docs/CONSTRUCTION_LOG.md")
        self.write("docs/CONSTRUCTION_LOG.md", approved)
        self.assert_candidate_blocked("utf-8")

    def test_intent_to_add_is_not_a_complete_commit_candidate(self):
        self.write("docs/CONSTRUCTION_LOG.md", "")
        self.attest()
        self.run_git("update-index", "--force-remove", "--", "docs/CONSTRUCTION_LOG.md")
        self.run_git("add", "--intent-to-add", "docs/CONSTRUCTION_LOG.md")
        self.assertIn("docs/CONSTRUCTION_LOG.md", self.run_git("ls-files", "--stage"))
        self.assert_candidate_blocked("index: intent-to-add entry: docs/CONSTRUCTION_LOG.md")

    def test_matching_staging_and_commit_keep_snapshot_with_mixed_newlines(self):
        approved = self.attest()
        for name in g.ALLOWED:
            self.write(name, g.read_text(self.root, name).replace("\n", "\r\n"))
        self.assertEqual(g.bundle(self.root)["snapshot_id"], approved["snapshot_id"])
        self.assertEqual(g.gate(self.root), "LOCAL_READY_FOR_PR")
        self.run_git("add", ".")
        raw_crlf = self.run_git("hash-object", "-w", "--no-filters", "docs/CONSTRUCTION_LOG.md").strip()
        self.run_git("update-index", "--cacheinfo", f"100644,{raw_crlf},docs/CONSTRUCTION_LOG.md")
        self.write("docs/CONSTRUCTION_LOG.md", g.read_text(self.root, "docs/CONSTRUCTION_LOG.md"))
        self.assertIn("\r\n", self.run_git("cat-file", "blob", raw_crlf))
        self.assertEqual(g.gate(self.root), "LOCAL_READY_FOR_PR")
        self.run_git("commit", "-m", "temporary normalized matching package")
        self.assertEqual(g.bundle(self.root)["snapshot_id"], approved["snapshot_id"])
        self.assertEqual(g.gate(self.root), "LOCAL_READY_FOR_PR")
        self.run_git("checkout", "--detach", "HEAD")
        self.assertEqual(self.cli("gate", "--ci"), (0, f"LOCAL_READY_FOR_PR ({g.GOVERNANCE_ONLY})\n", ""))

    def test_stale_snapshot_and_hash_receipts(self):
        self.attest()
        self.write("docs/CONSTRUCTION_LOG.md", "decision changed after review\n")
        self.run_git("add", "docs/CONSTRUCTION_LOG.md")
        with self.assertRaisesRegex(g.Block, "bundle receipt"):
            g.gate(self.root)
        self.attest()
        saved = self.load_json(g.CURRENT_ATTESTATIONS[0])
        saved["protected_file_hashes"]["docs/CONSTRUCTION_LOG.md"] = "0" * 64
        self.write_json(g.CURRENT_ATTESTATIONS[0], saved)
        self.run_git("add", g.CURRENT_ATTESTATIONS[0])
        with self.assertRaisesRegex(g.Block, "protected_file_hashes"):
            g.gate(self.root)

    def test_missing_block_wrong_and_self_review_receipts(self):
        self.attest()
        checked = g.check(self.root)
        current = g.make_bundle(checked)
        for name in g.CURRENT_ATTESTATIONS:
            with self.subTest(missing=name):
                contents = dict(checked[2])
                del contents[name]
                with self.assertRaises(g.Block):
                    g.validate_current_attestations((*checked[:2], contents, checked[3]), current)
        mutations = {
            g.CURRENT_ATTESTATIONS[1]: [("verdict", "BLOCK"), ("remote_ci", "PASS"),
                                ("remote_ci", "NOT_RUN_NO_REMOTE"), ("commands", []),
                                ("python_version", "3.12"), ("snapshot_id", "0" * 64)],
            g.CURRENT_ATTESTATIONS[2]: [("verdict", "BLOCK"), ("agent_id", "implementation-builder"),
                                ("read_only", "true"), ("repair_cycles", 2),
                                ("repair_cycles", 3), ("repair_cycles", True),
                                ("repair_cycles", -1), ("repair_cycles", "0"), ("findings", ["unresolved"]),
                                ("snapshot_id", "0" * 64), ("permissions", g.PERMISSIONS)],
            g.CURRENT_RECEIPT_PATHS["release"]: [("verdict", "PASS"), ("remote_ci", "PASS"),
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
                        g.validate_current_attestations((*checked[:2], contents, checked[3]), current)

    def test_validation_commands_exact_and_exit_codes_not_boolean(self):
        self.attest()
        checked = g.check(self.root)
        current = g.make_bundle(checked)
        original = json.loads(checked[2][g.CURRENT_ATTESTATIONS[1]])
        for bad in (1, False, "0", None):
            value = deepcopy(original)
            value["commands"][0]["exit_code"] = bad
            contents = {**checked[2], g.CURRENT_ATTESTATIONS[1]: json.dumps(value)}
            with self.assertRaises(g.Block):
                g.validate_current_attestations((*checked[:2], contents, checked[3]), current)
        value = deepcopy(original)
        value["commands"].reverse()
        contents = {**checked[2], g.CURRENT_ATTESTATIONS[1]: json.dumps(value)}
        with self.assertRaises(g.Block):
            g.validate_current_attestations((*checked[:2], contents, checked[3]), current)

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
        (self.root / "docs/CONSTRUCTION_LOG.md").write_bytes(b"\xff")
        self.assertEqual(self.cli("check")[0], 1)
        self.write("docs/CONSTRUCTION_LOG.md", "restored\n")
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
        prior_contract = tomllib.loads(g.read_text(self.root, "contracts/p4_publication.toml"))
        self.assertEqual(prior_contract["package_id"], "P4")
        self.assertEqual(prior_contract["baseline"], "9976a304c23be444093eaeaaf1277a2c846aed4d")
        self.assertEqual(self.load_json("docs/reviews/p4-merge.json")["package_id"], "P4")

    def test_prior_merge_evidence_cannot_be_forged(self):
        name = "docs/reviews/p4n-merge.json"
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
            name = g.CURRENT_RECEIPT_PATHS[key]
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
        name = g.CURRENT_RECEIPT_PATHS["blueprint_review"]
        self.write_json(name, {**self.load_json(name), "repair_cycles": 1})
        with self.assertRaisesRegex(g.Block, "repair bound or reset"):
            g.check(self.root)
        name = g.CURRENT_RECEIPT_PATHS["prompt_audit"]
        self.write_json(name, {**self.load_json(name), "repair_cycles": 1})
        self.attest()
        review = self.load_json(g.CURRENT_ATTESTATIONS[2])
        self.write_json(g.CURRENT_ATTESTATIONS[2], {**review, "repair_cycles": 0})
        self.run_git("add", g.CURRENT_ATTESTATIONS[2])
        with self.assertRaisesRegex(g.Block, "repair bound or reset"):
            g.gate(self.root)
        self.write_json(g.CURRENT_ATTESTATIONS[2], {**review, "repair_cycles": 1})
        self.run_git("add", ".")
        self.assertEqual(g.gate(self.root), g.LOCAL_RELEASE)
        self.write_json(g.CURRENT_ATTESTATIONS[2], {**review, "repair_cycles": 2})
        self.run_git("add", g.CURRENT_ATTESTATIONS[2])
        with self.assertRaisesRegex(g.Block, "repair bound or reset"):
            g.gate(self.root)

    def test_consumed_prompt_repair_cannot_reset_at_final_review(self):
        name = g.CURRENT_RECEIPT_PATHS["prompt_audit"]
        self.write_json(name, {**self.load_json(name), "repair_cycles": 1})
        self.attest()
        review = self.load_json(g.CURRENT_ATTESTATIONS[2])
        self.write_json(g.CURRENT_ATTESTATIONS[2], {**review, "repair_cycles": 0})
        self.run_git("add", g.CURRENT_ATTESTATIONS[2])
        with self.assertRaisesRegex(g.Block, "repair bound or reset"):
            g.gate(self.root)
        self.write_json(g.CURRENT_ATTESTATIONS[2], {**review, "repair_cycles": 1})
        self.run_git("add", ".")
        self.assertEqual(g.gate(self.root), g.LOCAL_RELEASE)
        self.write_json(g.CURRENT_ATTESTATIONS[2], {**review, "repair_cycles": 2})
        self.run_git("add", g.CURRENT_ATTESTATIONS[2])
        with self.assertRaisesRegex(g.Block, "repair bound or reset"):
            g.gate(self.root)

    def test_only_registered_product_bytecode_is_ignored(self):
        self.write("market_intelligence/__pycache__/runtime.cpython-312.pyc", "synthetic cache")
        g.check(self.root)
        self.write("market_intelligence/__pycache__/runtimeXcpython-312.pyc", "not a cache name")
        with self.assertRaisesRegex(g.Block, "out-of-scope"):
            g.check(self.root)

    def test_hardlinked_protected_files_and_attestations_are_rejected(self):
        self.attest()
        with tempfile.TemporaryDirectory(prefix="p4c-hardlink-") as external:
            alias = Path(external) / "alias"
            for name in ("docs/CONSTRUCTION_LOG.md", *g.CURRENT_ATTESTATIONS):
                with self.subTest(path=name):
                    os.link(self.root / name, alias)
                    try:
                        with self.assertRaisesRegex(g.Block, "hardlink forbidden"):
                            g.read_text(self.root, name)
                        self.assert_candidate_blocked("hardlink forbidden")
                    finally:
                        alias.unlink()

    def test_new_offline_implementation_changes_stale_approval_without_thawing_history(self):
        approved = self.attest()
        for name in ("market_intelligence/daily_market.py", "market_intelligence/intelligence.py",
                     "tests/product/test_daily_market.py"):
            original = g.read_text(self.root, name)
            with self.subTest(path=name):
                self.write(name, original + "\n# Changed offline implementation.\n")
                self.run_git("add", name)
                current = g.bundle(self.root)
                self.assertEqual(current["scope_result"], "PASS")
                self.assertNotEqual(current["snapshot_id"], approved["snapshot_id"])
                with self.assertRaisesRegex(g.Block, "bundle receipt"):
                    g.gate(self.root)
            self.write(name, original)
            self.run_git("add", name)
        self.assertEqual(g.gate(self.root), g.LOCAL_RELEASE)

    def test_bundle_package_and_final_receipt_schema_are_p4c_only(self):
        current = self.attest()
        self.assertEqual(current["package_id"], "P4M")
        self.assertEqual(self.state["package_status"], "implemented_unreleased")
        checked = g.check(self.root)
        for package in ("P4", "P4L", "P4I", "P5", None):
            saved = {**current, "package_id": package}
            contents = {**checked[2], g.CURRENT_ATTESTATIONS[0]: json.dumps(saved)}
            with self.subTest(package=package), self.assertRaisesRegex(g.Block, "package_id"):
                g.validate_current_attestations((*checked[:2], contents, checked[3]), current)
        for name in g.CURRENT_ATTESTATIONS[1:]:
            receipt = json.loads(checked[2][name])
            contents = {**checked[2], name: json.dumps({**receipt, "permissions_enabled": True})}
            with self.subTest(path=name), self.assertRaises(g.Block):
                g.validate_current_attestations((*checked[:2], contents, checked[3]), current)

    def test_entrypoint_pass_cannot_hide_findings(self):
        name = g.CURRENT_RECEIPT_PATHS["blueprint_review"]
        self.write_json(name, {**self.load_json(name), "findings": ["unresolved"]})
        with self.assertRaisesRegex(g.Block, "findings"):
            g.check(self.root)

    def test_real_script_probe_and_final_gates_without_pythonpath(self):
        # Bind the subprocess checker to this synthetic repository's approved
        # inputs, just as setUp binds the in-process checker. Real files stay untouched.
        checker = self.source_inputs["tools/governance.py"]
        bindings = {"BASELINE": g.BASELINE, "FROZEN_INPUT_HASHES": g.FROZEN_INPUT_HASHES}
        for node in ast.parse(checker).body:
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
                name = node.targets[0].id
                if name in bindings:
                    checker = checker.replace(ast.get_source_segment(
                        self.source_inputs["tools/governance.py"], node),
                        name + " = " + repr(bindings[name]), 1)
        self.write("tools/governance.py", checker)
        current = self.attest()
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith("GIT_") and key != "PYTHONPATH"}

        def invoke(*args):
            return subprocess.run([sys.executable, "-I", "-B", "tools/governance.py", *args],
                                  cwd=self.root, env=environment, capture_output=True,
                                  text=True, check=False)

        result = invoke("gate")
        self.assertEqual((result.returncode, result.stdout, result.stderr),
                         (0, f"{g.LOCAL_RELEASE} ({g.GOVERNANCE_ONLY})\n", ""))
        for command in ("probe-gate", "preparation-gate", "nbs-gate"):
            for flags in ((), ("--ci",)):
                result = invoke(command, *flags)
                self.assertEqual((result.returncode, result.stdout), (1, ""))
                self.assertIn("capability is closed", result.stderr)
                self.assertNotIn("Traceback", result.stderr)
        self.write_json(g.CURRENT_RECEIPT_PATHS["release"], g.not_run_release(current["snapshot_id"]))
        self.run_git("add", *g.CURRENT_ATTESTATIONS)
        self.assertEqual(invoke("gate").returncode, 1)
        self.assertEqual(invoke("nbs-gate").returncode, 1)
        self.run_git("rm", "--cached", g.CURRENT_RECEIPT_PATHS["release"])
        result = invoke("gate")
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing commit candidate files", result.stderr)
        self.attest()
        release = self.load_json(g.CURRENT_RECEIPT_PATHS["release"])
        release.update(verdict="LOCAL_READY_FOR_LOCAL_PREPARATION", preparation_digest=None)
        self.write_json(g.CURRENT_RECEIPT_PATHS["release"], release)
        self.run_git("add", *g.CURRENT_ATTESTATIONS)
        self.assertEqual(invoke("gate").returncode, 1)


if __name__ == "__main__":
    unittest.main()
