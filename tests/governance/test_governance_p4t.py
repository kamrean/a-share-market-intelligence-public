"""P4T offline proof, approval and candidate regressions; no live journal or source."""

import ast
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import p4d_fixture


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("p4t_governance_tests", ROOT / "tools/governance_p4t.py")
p = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(p)
g = p.g


def records(checked, status="NOT_RUN"):
    contract, _state, _contents, _changed = checked
    bundle = p.make_bundle(checked)
    common = {"schema_version": 1, "snapshot_id": bundle["snapshot_id"]}
    validation = {**common, "role": "builder_validation", "verdict": "PASS",
                  "commands": [{"command": c, "exit_code": 0} for c in contract["required_validation"]],
                  "python_version": "3.12.10", "remote_ci": "NOT_RUN_AT_SNAPSHOT"}
    review = {**common, "role": "reviewer", "verdict": "PASS", "agent_id": "authored-reviewer",
              "read_only": True, "findings": [], "repair_cycles": 0, **p.APPROVAL_VALUES}
    pre = {**common, "role": "release_checker", "verdict": p.PRE_RELEASE,
           "agent_id": "authored-release", "read_only": True, "remote_ci": "NOT_RUN_AT_SNAPSHOT",
           "pr_url": None, "merge_status": "not_merged", "vps_sync": "not_requested",
           "next_gate": contract["next_gate"], **p.APPROVAL_VALUES}
    observation = {**common, "package_id": "P4T", "status": "NOT_RUN", "observation": None}
    if status != "NOT_RUN":
        observation.update(status="RECORDED", observation={
            "outcome": "OBSERVED" if status == "RECORDED" else status, "dispatch_attempts": 1,
            "payloads_persisted": False, "payload_references_discarded": True, "connections_closed": True,
            "upstream_rights": "unknown", "normal_composition": "BLOCKED_SOURCE_RIGHTS",
            "live_verified_benchmark_count": 0})
    final = p.not_run_release(bundle["snapshot_id"])
    if status != "NOT_RUN":
        final = {**pre, "verdict": p.FINAL_RELEASE, "pre_probe_release_digest": g.probe_digest(pre),
                 "observation_digest": g.probe_digest(observation)}
    return dict(zip(p.CURRENT_ATTESTATIONS, (bundle, validation, review, pre, observation, final)))


def receipt_stub(_root, function, *args):
    if function == "not_run_receipt":
        return {"schema_version": 1, "snapshot_id": args[0], "package_id": "P4T",
                "status": "NOT_RUN", "observation": None}
    if function == "validate_observation_receipt":
        g.exact(args[0]["snapshot_id"], args[1], "fixture receipt snapshot")
        return args[0]
    raise AssertionError("Only pure receipt functions may be called")


class TencentGovernanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract, cls.state, planning = p.planning_inputs(ROOT)
        cls.proof = g.parse_json(p.read_text(ROOT, p.HISTORY_PATH))
        cls.baseline = p.baseline_contents(ROOT)
        cls.retained, cls.historical = p.historical_contents(ROOT)
        cls.allowed, cls.protected = p.scope(cls.contract, cls.retained)
        cls.inputs = {}
        for name in cls.allowed:
            path = ROOT / name
            cls.inputs[name] = (g.read_text(ROOT, name) if path.is_file() else "Authored offline input.\n")
        cls.inputs.update(planning)

    def setUp(self):
        self.contents = dict(self.inputs)
        self.checked = (deepcopy(self.contract), deepcopy(self.state), self.contents, [])
        self.current = p.make_bundle(self.checked)
        self.contents.update({name: json.dumps(value) for name, value in records(self.checked).items()})

    def test_actual_entry_and_seventeen_frozen_inputs_match(self):
        self.assertEqual(len(p.FROZEN_INPUT_HASHES), 17)
        self.assertEqual(g.snapshot_for_paths(self.contents, p.FROZEN_INPUT_HASHES)[0], p.PLANNING_SNAPSHOT)
        p.planning_inputs(ROOT)

    def test_planning_input_changes_cannot_rehash_their_own_authority(self):
        for name in p.FROZEN_INPUT_HASHES:
            with self.subTest(path=name), self.assertRaisesRegex(p.Block, "frozen planning"):
                p.frozen_text(name, self.contents[name] + "\n")

    def test_entry_missing_exception_wrong_agent_and_extra_field_fail(self):
        name = p.ENTRY_PATHS[0]
        original = g.parse_json(self.contents[name])
        for field, value in (("limited_technical_probe_exception_accepted", False),
                             ("agent_id", "coordinator"), ("findings", ["BLOCK"]),
                             ("repair_cycles", True), ("planning_snapshot_id", "a" * 64), ("extra", True)):
            contents = {**self.contents, name: json.dumps({**original, field: value})}
            with self.subTest(field=field), patch.object(p, "read_text", side_effect=lambda _r, n: contents[n]):
                with self.assertRaises(p.Block):
                    p.planning_inputs(ROOT)

    def test_source_rights_and_general_permissions_stay_blocked(self):
        probe = self.contract["limited_source_probe"]
        self.assertEqual(probe["upstream_rights"], "unknown")
        self.assertIs(probe["normal_source_operations_enabled"], False)
        self.assertIs(probe["legal_entitlement_verified"], False)
        self.assertEqual(self.contract["permissions"], g.PERMISSIONS)
        for key in ("upstream_rights", "normal_source_operations_enabled", "legal_entitlement_verified"):
            with self.subTest(key=key), self.assertRaises(p.Block):
                p.validate_contract(self.contents[p.CONTRACT_PATH].replace(
                    key + " = " + ('"unknown"' if key == "upstream_rights" else "false"), key + " = true"))

    def test_exact_derived_scope_without_repeated_historical_allowlist(self):
        self.assertEqual((len(self.allowed), len(self.protected)), (274, 268))
        self.assertEqual(set(self.allowed) - set(self.protected), set(p.CURRENT_ATTESTATIONS))
        self.assertEqual(len(p.MUTABLE_PATHS), 9)
        self.assertEqual(set(self.contract["new_paths"]) & set(self.retained), set())
        for name in g.CURRENT_ATTESTATIONS:
            self.assertIn(name, self.protected)

    def test_history_reconstruction_never_reads_checkpoint_objects(self):
        with patch.object(g, "git", side_effect=AssertionError("Git object access")), \
                patch.object(g, "read_blobs", side_effect=AssertionError("Git object access")):
            retained, historical = p.reconstruct_history(self.proof, self.baseline, self.inputs)
        self.assertEqual((len(retained), len(historical)), (251, 242))
        self.assertEqual(retained, self.retained)
        self.assertEqual(historical, self.historical)

    def test_history_closed_schema_and_pinned_metadata(self):
        for name in p.HISTORY_KEYS:
            bad = deepcopy(self.proof)
            del bad[name]
            with self.subTest(missing=name), self.assertRaises(p.Block):
                p.reconstruct_history(bad, self.baseline, self.inputs)
        for name, value in (("extra", True), ("schema_version", True), ("baseline_path_count", 219),
                             ("checkpoint_git_objects_required", True), ("checkpoint_commit", "f" * 40)):
            with self.subTest(field=name), self.assertRaises(p.Block):
                p.reconstruct_history({**self.proof, name: value}, self.baseline, self.inputs)

    def test_history_exact_delta_paths_counts_kinds_and_hashes(self):
        for mutation in ("missing", "unknown", "kind", "hash", "redundant"):
            bad = deepcopy(self.proof)
            delta = bad["retained_delta"]
            name = "AGENTS.md"
            if mutation == "missing":
                del delta[name]
            elif mutation == "unknown":
                delta["unknown.py"] = delta.pop(name)
            elif mutation == "kind":
                delta[name]["change"] = "added"
            elif mutation == "hash":
                delta[name]["sha256"] = "f" * 64
            else:
                delta[name]["sha256"] = g.sha256(self.baseline[name])
            with self.subTest(mutation=mutation), self.assertRaises(p.Block):
                p.reconstruct_history(bad, self.baseline, self.inputs)

    def test_history_archive_exact_keys_and_no_external_paths(self):
        for section, path in (("checkpoint_inputs", "unknown.py"), ("checkpoint_inputs", "../escape"),
                              ("p4d_inputs", "tools/governance.py"), ("p4d_prefixes", "README.md")):
            bad = deepcopy(self.proof)
            bad[section][path] = "untrusted"
            with self.subTest(section=section, path=path), self.assertRaises(p.Block):
                p.reconstruct_history(bad, self.baseline, self.inputs)
        bad = deepcopy(self.proof)
        del bad["checkpoint_inputs"]["tools/governance.py"]
        with self.assertRaises(p.Block):
            p.reconstruct_history(bad, self.baseline, self.inputs)

    def test_history_archive_tamper_non_lf_and_types(self):
        for text in (None, False, "changed", "bad\r\n"):
            bad = deepcopy(self.proof)
            bad["checkpoint_inputs"]["AGENTS.md"] = text
            with self.subTest(text=text), self.assertRaises(p.Block):
                p.reconstruct_history(bad, self.baseline, self.inputs)

    def test_history_archive_per_file_utf8_and_total_bounds(self):
        bad = deepcopy(self.proof)
        bad["checkpoint_inputs"]["AGENTS.md"] = "\u4e2d" * 350000
        with self.assertRaisesRegex(p.Block, "archive byte bound"):
            p.reconstruct_history(bad, self.baseline, self.inputs)
        bad = deepcopy(self.proof)
        for name in ("AGENTS.md", "README.md", "tools/governance.py"):
            bad["checkpoint_inputs"][name] = "x" * 800000
        with self.assertRaisesRegex(p.Block, "archive byte bound"):
            p.reconstruct_history(bad, self.baseline, self.inputs)

    def test_history_prefixes_cannot_truncate_replace_or_rehash_logs(self):
        name = "docs/CONSTRUCTION_LOG.md"
        for text in ("", self.inputs[name][1:], "changed" + self.inputs[name]):
            with self.subTest(length=len(text)), self.assertRaises(p.Block):
                p.reconstruct_history(self.proof, self.baseline, {**self.inputs, name: text})
        bad = deepcopy(self.proof)
        bad["checkpoint_prefixes"][name]["utf8_bytes"] = True
        with self.assertRaises(p.Block):
            p.reconstruct_history(bad, self.baseline, self.inputs)

    def test_history_current_immutable_bytes_and_all_aggregates_are_bound(self):
        path = "market_intelligence/baostock_transport.py"
        with self.assertRaisesRegex(p.Block, "retained file"):
            p.reconstruct_history(self.proof, self.baseline, {**self.inputs, path: self.inputs[path] + "\n"})
        for name in p.HISTORY_DIGESTS:
            with self.subTest(digest=name), self.assertRaises(p.Block):
                p.reconstruct_history({**self.proof, name: "0" * 64}, self.baseline, self.inputs)

    def test_history_python_is_never_the_executable_fixture(self):
        root = p4d_fixture.view()
        for name in self.proof["checkpoint_inputs"]:
            if name.endswith(".py"):
                self.assertEqual(g.read_text(root, name), g.read_text(ROOT, name))
        self.assertEqual(g.read_text(root, "AGENTS.md"), self.proof["p4d_inputs"]["AGENTS.md"])
        self.assertNotEqual(g.read_text(root, "tools/governance.py"),
                            self.proof["checkpoint_inputs"]["tools/governance.py"])

    def test_all_197_historical_test_method_names_remain(self):
        def names(text):
            return {c.name + "." + f.name for c in ast.parse(text).body if isinstance(c, ast.ClassDef)
                    for f in c.body if isinstance(f, ast.FunctionDef) and f.name.startswith("test_")}
        old = names(self.proof["checkpoint_inputs"]["tests/governance/test_governance.py"])
        new = names(g.read_text(ROOT, "tests/governance/test_governance.py"))
        self.assertEqual(len(old), 197)
        self.assertEqual(old, new)

    def test_current_snapshot_excludes_only_six_result_records(self):
        digest = self.current["snapshot_id"]
        for name in p.CURRENT_ATTESTATIONS:
            self.assertEqual(p.snapshot({**self.contents, name: "changed result"})[0], digest)
        for name in ("tools/governance_p4t.py", "market_intelligence/tencent_capture.py",
                     "docs/reviews/p4d-release.json", p.ENTRY_PATHS[0]):
            self.assertNotEqual(p.snapshot({**self.contents, name: self.contents[name] + "\n"})[0], digest)

    def test_current_validation_commands_roles_and_actual_snapshot_are_required(self):
        p._reviews(self.checked, self.current)
        for path, key, value in ((p.CURRENT_ATTESTATIONS[1], "commands", []),
                                 (p.CURRENT_ATTESTATIONS[1], "verdict", "NOT_RUN"),
                                 (p.CURRENT_ATTESTATIONS[2], "snapshot_id", "a" * 64),
                                 (p.CURRENT_ATTESTATIONS[2], "role", "builder_validation"),
                                 (p.CURRENT_ATTESTATIONS[3], "verdict", "LOCAL_READY_FOR_PR")):
            original = self.contents[path]
            self.contents[path] = json.dumps({**g.parse_json(original), key: value})
            with self.subTest(path=path, key=key), self.assertRaises(p.Block):
                p._reviews(self.checked, self.current)
            self.contents[path] = original

    def test_exception_acceptance_and_no_entitlement_are_explicit_in_both_roles(self):
        for path in p.CURRENT_ATTESTATIONS[2:4]:
            original = self.contents[path]
            for key, expected in p.APPROVAL_VALUES.items():
                self.contents[path] = json.dumps({**g.parse_json(original), key: not expected})
                with self.subTest(path=path, key=key), self.assertRaises(p.Block):
                    p._reviews(self.checked, self.current)
            self.contents[path] = original

    def test_current_roles_cannot_self_review_or_hide_findings(self):
        for path, key, value in ((p.CURRENT_ATTESTATIONS[2], "agent_id", "coordinator"),
                                 (p.CURRENT_ATTESTATIONS[2], "findings", ["BLOCK"]),
                                 (p.CURRENT_ATTESTATIONS[2], "repair_cycles", 2),
                                 (p.CURRENT_ATTESTATIONS[2], "repair_cycles", True),
                                 (p.CURRENT_ATTESTATIONS[3], "agent_id", "authored-reviewer")):
            original = self.contents[path]
            self.contents[path] = json.dumps({**g.parse_json(original), key: value})
            with self.subTest(path=path, key=key), self.assertRaises(p.Block):
                p._reviews(self.checked, self.current)
            self.contents[path] = original

    def test_cross_package_bundle_and_unknown_receipt_fields_fail(self):
        for path, key, value in ((p.CURRENT_ATTESTATIONS[0], "package_id", "P4D"),
                                 (p.CURRENT_ATTESTATIONS[0], "attestation_paths", list(g.CURRENT_ATTESTATIONS)),
                                 (p.CURRENT_ATTESTATIONS[2], "extra", True)):
            original = self.contents[path]
            self.contents[path] = json.dumps({**g.parse_json(original), key: value})
            with self.subTest(path=path), self.assertRaises(p.Block):
                p._reviews(self.checked, self.current)
            self.contents[path] = original

    def test_ci_probe_refuses_before_check_validator_or_journal(self):
        with patch.object(p, "check", side_effect=AssertionError("check")), \
                patch.object(p, "_receipt_call", side_effect=AssertionError("validator")):
            with self.assertRaisesRegex(p.Block, "--ci is not runtime authorization"):
                p.tencent_gate(ROOT, ci=True)
            for variable in ("CI", "GITHUB_ACTIONS"):
                with patch.dict(os.environ, {variable: ""}), self.assertRaises(p.Block):
                    p.tencent_gate(ROOT)

    def test_probe_returns_scalar_and_only_calls_pure_receipt_api_repeatedly(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(p, "check", return_value=self.checked) as check, \
                patch.object(p, "_receipt_call", side_effect=receipt_stub) as call:
            for _ in range(3):
                self.assertEqual(p.tencent_gate(ROOT), self.current["snapshot_id"])
            self.assertEqual(call.call_count, 6)
            check.assert_called_with(ROOT, complete_index=True)
            self.assertEqual({c.args[1] for c in call.call_args_list},
                             {"not_run_receipt", "validate_observation_receipt"})

    def test_probe_rejects_actual_observation_and_premature_final_release(self):
        for path in (p.CURRENT_ATTESTATIONS[4], p.CURRENT_ATTESTATIONS[5]):
            old = self.contents[path]
            self.contents[path] = json.dumps(records(self.checked, "FAILED")[path])
            with patch.dict(os.environ, {}, clear=True), patch.object(p, "check", return_value=self.checked), \
                    patch.object(p, "_receipt_call", side_effect=receipt_stub), self.assertRaises(p.Block):
                p.tencent_gate(ROOT)
            self.contents[path] = old

    def test_final_gate_requires_terminal_and_binds_actual_results(self):
        for status in ("RECORDED", "PARTIAL", "FAILED", "NOT_RUN", "UNKNOWN"):
            self.contents.update({name: json.dumps(v) for name, v in records(self.checked, status).items()})
            with self.subTest(status=status), patch.object(p, "check", return_value=self.checked), \
                    patch.object(p, "_receipt_call", side_effect=receipt_stub):
                if status in ("RECORDED", "PARTIAL", "FAILED"):
                    self.assertEqual(p.gate(ROOT, ci=True), p.FINAL_RELEASE)
                else:
                    with self.assertRaises(p.Block):
                        p.gate(ROOT, ci=True)
        self.contents.update({name: json.dumps(v) for name, v in records(self.checked, "FAILED").items()})
        for key in ("pre_probe_release_digest", "observation_digest"):
            path = p.CURRENT_ATTESTATIONS[5]
            original = self.contents[path]
            self.contents[path] = json.dumps({**g.parse_json(original), key: "f" * 64})
            with patch.object(p, "check", return_value=self.checked), \
                    patch.object(p, "_receipt_call", side_effect=receipt_stub), self.assertRaises(p.Block):
                p.gate(ROOT, ci=True)
            self.contents[path] = original

    def test_final_gate_rejects_unfinished_cleanup_and_premature_rights(self):
        self.contents.update({name: json.dumps(v) for name, v in records(self.checked, "FAILED").items()})
        path = p.CURRENT_ATTESTATIONS[4]
        original = g.parse_json(self.contents[path])
        for key, value in (("dispatch_attempts", 0), ("dispatch_attempts", True),
                           ("payloads_persisted", True), ("payload_references_discarded", False),
                           ("connections_closed", False), ("upstream_rights", "permitted"),
                           ("normal_composition", "PASS"), ("live_verified_benchmark_count", 3)):
            record = deepcopy(original)
            record["observation"][key] = value
            self.contents[path] = json.dumps(record)
            with self.subTest(key=key), patch.object(p, "check", return_value=self.checked), \
                    patch.object(p, "_receipt_call", side_effect=receipt_stub), self.assertRaises(p.Block):
                p.gate(ROOT, ci=True)

    def test_actual_product_validator_is_pure_and_checkout_bound(self):
        from market_intelligence import tencent_daily as daily
        snapshot = self.current["snapshot_id"]
        value = p._receipt_call(ROOT, "not_run_receipt", snapshot)
        self.assertEqual(value, receipt_stub(ROOT, "not_run_receipt", snapshot))
        self.assertEqual(p._receipt_call(ROOT, "validate_observation_receipt", value, snapshot), value)
        when = "2026-06-10T08:00:00Z"
        observation = {"run_id": "a" * 32, "started_at": when, "finished_at": when,
            "window": daily.plan(when), "reserved_requests": 3, "dispatch_attempts": 1,
            "allowance_consumed": True, "results": [{"symbol": "sh000001", "started_at": when,
                "finished_at": when, "status": "FAILED", "http_bytes": None, "body_bytes": None,
                "response_sha256": None, "retrieved_at": None, "parse": None,
                "replay_verified": False, "error": "http_status"}],
            "outcome": "FAILED", "local_error": None, "payloads_persisted": False,
            "payload_references_discarded": True, "connections_closed": True,
            "upstream_rights": "unknown", "normal_composition": "BLOCKED_SOURCE_RIGHTS",
            "live_verified_benchmark_count": 0}
        terminal = {**value, "status": "RECORDED", "observation": observation}
        self.assertEqual(p._receipt_call(ROOT, "validate_observation_receipt", terminal, snapshot), terminal)
        with self.assertRaises(p.Block):
            p._receipt_call(ROOT, "validate_observation_receipt", terminal, "f" * 64)
        with self.assertRaises(p.Block):
            p._receipt_call(ROOT, "observe", True)
        self.assertFalse(any(name.startswith("_p4t_receipt_validation_") for name in __import__("sys").modules))

    def test_check_uses_explicit_scope_and_never_rebinds_legacy_globals(self):
        before = (g.ALLOWED, g.BASELINE, g.BRANCH, g.IMMUTABLE)
        with patch.object(p, "read_text", side_effect=lambda _r, name: self.contents[name]), \
                patch.object(p, "historical_contents", return_value=(self.retained, self.historical)), \
                patch.object(g, "inspect_git", return_value=[]) as inspect:
            p.check(ROOT, ci=True, complete_index=True)
        self.assertEqual((g.ALLOWED, g.BASELINE, g.BRANCH, g.IMMUTABLE), before)
        self.assertEqual(inspect.call_args.kwargs["allowed"], self.allowed)
        self.assertEqual(inspect.call_args.kwargs["baseline"], p.BASELINE)
        self.assertEqual(set(inspect.call_args.kwargs["immutable_hashes"]), set(self.retained) - set(p.MUTABLE_PATHS))

    def test_current_and_legacy_operational_commands_stay_closed(self):
        names = ("market-gate", "nbs-gate", "probe-gate", "preparation-gate", "diagnostic-gate", "verification-gate")
        for command in names:
            for module in (p, g):
                with self.subTest(module=module.__name__, command=command), redirect_stdout(io.StringIO()), \
                        redirect_stderr(io.StringIO()):
                    self.assertEqual(module.main([command, "--ci"], root=ROOT), 1)
        with patch.object(g, "_recovery_validation_call", side_effect=AssertionError("journal")):
            for phase in ("diagnostic", "verification"):
                with self.assertRaises(g.Block):
                    g.recovery_gate(ROOT, phase)

    def test_cli_prompt_and_ci_refusal(self):
        for args, result in ((["prompt", "--verify"], 0), (["tencent-gate", "--ci"], 1)):
            output, error = io.StringIO(), io.StringIO()
            with redirect_stdout(output), redirect_stderr(error):
                self.assertEqual(p.main(args, root=ROOT), result)
            if result:
                self.assertEqual(output.getvalue(), "")
                self.assertIn("--ci is not runtime authorization", error.getvalue())

    def test_ci_keeps_pins_full_suites_and_no_runtime_gate(self):
        workflow = g.read_text(ROOT, ".github/workflows/governance.yml")
        old = self.proof["checkpoint_inputs"][".github/workflows/governance.yml"]
        for line in old.splitlines():
            if any(marker in line for marker in ("uses:", "os: [", "python-version:", "contents: read",
                                                 "--require-hashes", "--only-binary", "requirements-baostock.lock")):
                self.assertIn(line, workflow)
        for text in ("timeout-minutes: 120", "python tools/governance_p4t.py gate --ci",
                     "python -m unittest discover -s tests/governance -p test_*.py -v",
                     "python -m unittest discover -s tests/product -p test_*.py -v",
                     "'tencent-gate', '--ci'"):
            self.assertIn(text, workflow)


class TencentCandidateTests(unittest.TestCase):
    """One bounded local-only repository, with no unpublished checkpoint objects."""

    def test_squash_candidate_scope_index_and_history_guards(self):
        with tempfile.TemporaryDirectory(prefix="p4t-candidate-") as directory:
            root = Path(directory)
            g.git(root, "init", "--initial-branch=" + p.BRANCH)
            # A local path and the public baseline are fixed by the test, not a remote/source request.
            g.git(root, "-c", "protocol.file.allow=always", "fetch", "--no-tags", str(ROOT), p.BASELINE)
            g.git(root, "checkout", "-B", p.BRANCH, p.BASELINE)
            g.git(root, "config", "user.name", "Offline Fixture")
            g.git(root, "config", "user.email", "fixture@example.invalid")
            g.git(root, "config", "commit.gpgsign", "false")
            g.git(root, "config", "core.autocrlf", "false")
            contract, state, planning = p.planning_inputs(ROOT)
            retained, _historical = p.historical_contents(ROOT)
            allowed, _protected = p.scope(contract, retained)

            def write(name, text):
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8", newline="\n")

            for name in allowed:
                write(name, g.read_text(ROOT, name) if (ROOT / name).is_file() else "Authored offline input.\n")
            g.git(root, "add", "--", *allowed)
            checked = p.check(root)
            current = p.make_bundle(checked)
            for name, value in records(checked, "FAILED").items():
                write(name, json.dumps(value))
            g.git(root, "add", "--", *p.CURRENT_ATTESTATIONS)
            g.git(root, "commit", "-m", "Authored P4T squash candidate")
            for commit in ("4d1ec45645a25f2ccbb7a065c95a5a9d5ec77903", "5365ce7f9524e361758c45246f8a20a9ff61bde2"):
                with self.assertRaises(g.Block):
                    g.git(root, "cat-file", "-e", commit)
            g.git(root, "checkout", "--detach", "HEAD")
            self.assertEqual(p.bundle(root, ci=True)["snapshot_id"], current["snapshot_id"])
            with patch.object(p, "_receipt_call", side_effect=receipt_stub):
                self.assertEqual(p.gate(root, ci=True), p.FINAL_RELEASE)
            path = "docs/reviews/p4d-release.json"
            original = g.read_text(root, path)
            write(path, original + "\n")
            with self.assertRaisesRegex(p.Block, "history retained file"):
                p.check(root, ci=True)
            write(path, original)
            path = p.CURRENT_ATTESTATIONS[5]
            original = g.read_text(root, path)
            write(path, original + "\n")
            with self.assertRaisesRegex(p.Block, "index/worktree mismatch"):
                p.check(root, ci=True)
            write(path, original)
            g.git(root, "update-index", "--force-remove", "--", path)
            with self.assertRaisesRegex(p.Block, "missing commit candidate"):
                p.check(root, ci=True)
            g.git(root, "add", "--", path)
            write("unexpected.txt", "unexpected")
            with self.assertRaisesRegex(p.Block, "out-of-scope"):
                p.check(root, ci=True)


if __name__ == "__main__":
    unittest.main()
