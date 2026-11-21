"""Offline repair scope, immutable probe history and fresh release regressions."""

from contextlib import redirect_stderr
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from uuid import UUID


ROOT = Path(__file__).resolve().parents[2]
TOOL = "tools/governance_news_ci_repair.py"
SPEC = importlib.util.spec_from_file_location("p4fed_ci_repair_tests", ROOT / TOOL)
repair = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(repair)
n, g = repair.n, repair.g


def json_text(record):
    return json.dumps(record, sort_keys=True) + "\n"


def blob_id(data):
    return hashlib.sha1(f"blob {len(data)}\0".encode("ascii") + data,
                        usedforsecurity=False).hexdigest()


class RepairScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baseline = n.baseline_entries(ROOT)
        cls.planning = {name: g.read_text(ROOT, name) for name in
                        (repair.CONTRACT, repair.ENTRY, *n.PLANNING_PATHS, n.ENTRY)}
        cls.plan = repair._validate_plan(cls.planning)
        cls.original_paths = cls.baseline.keys() | set(n.validate_planning(cls.planning)["additions"])
        cls.protected = tuple(sorted(
            (cls.original_paths | set(cls.plan["additions"])) - set(repair.RECEIPTS)))
        cls.contents = {name: (ROOT / name).read_bytes().decode("utf-8") for name in cls.protected}

    def seed(self, root):
        for name, value in self.contents.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(value.encode("utf-8"))

    def test_reconstructs_pinned_snapshot_without_loading_historical_code(self):
        original = repair._reconstruct(self.contents, self.original_paths)
        digest, _ = g.snapshot_for_paths(original, self.original_paths - set(n.RECEIPTS))
        self.assertEqual(digest, self.plan["original_snapshot"])
        self.assertEqual(set(original), self.original_paths)
        with (n._probe_module(ROOT) as probe,
              patch.object(probe, "validate_receipt", wraps=probe.validate_receipt) as validate,
              patch.object(probe, "_observe", side_effect=AssertionError("dispatch attempted")) as observe,
              patch.object(probe, "_path", side_effect=AssertionError("journal accessed")) as journal,
              patch.object(g, "git_raw", side_effect=AssertionError("historical Git object requested"))):
            repair._validate_history(ROOT, self.contents, self.original_paths, self.plan)
        validate.assert_called_once_with(g.parse_json(self.contents[n.OBSERVATION]), digest, terminal=True)
        observe.assert_not_called()
        journal.assert_not_called()
        current = repair.make_snapshot((self.contents, self.protected, []))
        self.assertNotEqual(current["snapshot_id"], digest)
        self.assertEqual(set(current["protected_paths"]), set(self.contents))
        self.assertTrue(set(n.RECEIPTS) <= self.contents.keys())

    def test_each_historical_receipt_is_pinned_to_its_bytes(self):
        for name in n.RECEIPTS:
            with (self.subTest(name=name), self.assertRaisesRegex(n.Block, "historical receipt SHA-256"),
                  patch.object(n, "_probe_module", side_effect=AssertionError("probe loaded too early"))):
                repair._validate_history(ROOT, {**self.contents, name: self.contents[name] + "\n"},
                                         self.original_paths, self.plan)

    def test_refuses_missing_or_repeated_lines_and_unrelated_original_edits(self):
        for name, changes in repair.REPAIR_LINES.items():
            for before, after in changes:
                for tampered in (self.contents[name].replace(after, before), self.contents[name] + after):
                    with self.subTest(name=name, line=after), self.assertRaisesRegex(n.Block, "exact repair line"):
                        repair._reconstruct({**self.contents, name: tampered}, self.original_paths)
        for name in (*repair.REPAIR_LINES, n.PRODUCT, "tools/governance_news.py"):
            with (self.subTest(name=name), self.assertRaisesRegex(n.Block, "reconstructed original snapshot"),
                  patch.object(n, "_probe_module", side_effect=AssertionError("probe loaded too early"))):
                repair._validate_history(ROOT, {**self.contents, name: self.contents[name] + "\n"},
                                         self.original_paths, self.plan)

    def test_plan_and_entry_are_not_self_authorizing(self):
        with self.assertRaisesRegex(n.Block, "repair planning SHA-256"):
            repair._validate_plan({**self.planning, repair.CONTRACT: self.planning[repair.CONTRACT] + "\n"})
        entry = g.parse_json(self.planning[repair.ENTRY])
        for key, value in (("prompt_verdict", "BLOCK"), ("read_only", 1), ("new_source_requests", 1)):
            with self.subTest(key=key), self.assertRaisesRegex(n.Block, "repair entry and prompt receipt"):
                repair._validate_plan({**self.planning, repair.ENTRY: json_text({**entry, key: value})})

    def test_unrelated_untracked_path_blocks_even_in_ci(self):
        with tempfile.TemporaryDirectory(prefix="p4fed-ci-scope-") as directory:
            root = Path(directory).resolve()
            g.git(root, "init", "--initial-branch=fixture")
            g.git(root, "-c", "protocol.file.allow=always", "fetch", "--no-tags", str(ROOT), n.BASE)
            g.git(root, "checkout", "--detach", n.BASE)
            self.seed(root)
            (root / "unrelated.txt").write_bytes(b"Unrelated change.\n")
            with self.assertRaisesRegex(n.Block, "out-of-scope path: unrelated[.]txt"):
                repair.check(root, ci=True)

    def test_rejects_raw_crlf_in_worktree_or_index(self):
        with tempfile.TemporaryDirectory(prefix="p4fed-ci-bytes-") as directory:
            root = Path(directory).resolve()
            self.seed(root)
            target = root / n.REVIEW
            lf = self.contents[n.REVIEW].encode("utf-8")
            crlf = lf.replace(b"\n", b"\r\n")
            target.write_bytes(crlf)
            with (patch.object(n, "baseline_entries", return_value=self.baseline),
                  patch.object(g, "inspect_git", return_value=[]),
                  self.assertRaisesRegex(n.Block, "must use LF bytes")):
                repair.check(root)

            target.write_bytes(lf)
            entries = {name: blob_id(value.encode("utf-8")) for name, value in self.contents.items()}
            entries[n.REVIEW] = blob_id(crlf)
            # Simulate the shared inspector accepting normalized-equal contents.
            with (patch.object(n, "baseline_entries", return_value=self.baseline),
                  patch.object(g, "inspect_git", return_value=[]),
                  patch.object(g, "read_index", return_value=entries),
                  self.assertRaisesRegex(n.Block, "index/worktree bytes differ")):
                repair.check(root)


class ReleaseTests(unittest.TestCase):
    snapshot_id = "a" * 64
    actors = tuple(str(UUID(int=i)) for i in range(1, 4))

    def receipts(self, **overrides):
        common = {"schema_version": 1, "package": repair.PACKAGE, "snapshot_id": self.snapshot_id}
        observation = (ROOT / n.OBSERVATION).read_bytes().decode("utf-8")
        validation = json_text({
            **common, "role": "builder_validation", "verdict": "PASS", "offline": True,
            "agent_id": self.actors[0], "new_source_requests": 0, "short_path_repro": "PASS",
            "product_collected": 980, "product_passed": 979, "product_skipped": 1,
            "focused_probe_tests": 27, "repair_governance_tests": 5, **overrides.get("validation", {}),
        })
        review = json_text({
            **common, "role": "reviewer", "verdict": "PASS", "read_only": True,
            "agent_id": self.actors[1], "validation_sha256": g.sha256(validation),
            "findings": [], **overrides.get("review", {}),
        })
        release = json_text({
            **common, "role": "release_checker", "verdict": "LOCAL_READY_FOR_PR", "read_only": True,
            "agent_id": self.actors[2], "review_sha256": g.sha256(review),
            "original_observation_sha256": g.sha256(observation), "human_merge_required": True,
            "remote_ci": "NOT_RUN_AT_SNAPSHOT", **overrides.get("release", {}),
        })
        return {n.OBSERVATION: observation, repair.VALIDATION: validation, repair.REVIEW: review,
                repair.RELEASE: release}

    def test_gate_requires_complete_index_and_exact_receipt_chain(self):
        self.snapshot_id, _ = g.snapshot_for_paths({"tools/fixture.py": "offline\n"}, ("tools/fixture.py",))
        contents = {**self.receipts(), "tools/fixture.py": "offline\n"}
        with patch.object(repair, "check", return_value=(contents, ("tools/fixture.py",), [])) as check:
            self.assertEqual(repair.gate(ROOT, ci=True), "LOCAL_READY_FOR_PR")
        check.assert_called_once_with(ROOT, ci=True, complete_index=True)
        for path in repair.RECEIPTS:
            incomplete = {name: value for name, value in contents.items() if name != path}
            with self.subTest(path=path), self.assertRaisesRegex(n.Block, "missing required receipt"):
                repair.validate_chain(incomplete, self.snapshot_id)

    def test_blocks_stale_bindings_and_invalid_offline_attestations(self):
        for role, fields in (
            ("validation", {"new_source_requests": False}), ("validation", {"new_source_requests": 1}),
            ("validation", {"short_path_repro": "FAIL"}), ("validation", {"product_collected": 979}),
            ("validation", {"product_passed": 980}), ("validation", {"product_skipped": 2}),
            ("validation", {"focused_probe_tests": 26}), ("validation", {"repair_governance_tests": 4}),
            ("review", {"snapshot_id": "b" * 64}), ("review", {"validation_sha256": "b" * 64}),
            ("review", {"findings": ["unresolved"]}), ("release", {"review_sha256": "b" * 64}),
            ("release", {"original_observation_sha256": "b" * 64}),
            ("release", {"human_merge_required": False}), ("release", {"allow_network": True}),
        ):
            with self.subTest(role=role, fields=fields), self.assertRaises(n.Block):
                repair.validate_chain(self.receipts(**{role: fields}), self.snapshot_id)

    def test_native_roles_must_be_independent(self):
        for index, role in enumerate(("validation", "review", "release")):
            for actor in (n.AUDITOR, *self.actors[:index], "coordinator"):
                with (self.subTest(role=role, actor=actor),
                      self.assertRaisesRegex(n.Block, "independent|native agent")):
                    repair.validate_chain(self.receipts(**{role: {"agent_id": actor}}), self.snapshot_id)

    def test_cli_has_no_dispatch_and_original_ci_refusal_is_unchanged(self):
        for args in (("observe", "--ci"), ("preprobe", "--ci"), ("gate", "--allow-network")):
            with (self.subTest(args=args), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error,
                  patch.object(repair, "check", side_effect=AssertionError("offline checks reached"))):
                repair.main(args)
            self.assertEqual(error.exception.code, 2)
        result = subprocess.run([sys.executable, "-B", str(ROOT / "tools/governance_news.py"),
                                 "observe", "--ci", "--allow-network"],
                                cwd=ROOT, capture_output=True, text=True, check=False)
        self.assertEqual((result.returncode, result.stdout), (1, ""))
        self.assertIn(n.CI_REFUSAL, result.stderr)


if __name__ == "__main__":
    unittest.main()
