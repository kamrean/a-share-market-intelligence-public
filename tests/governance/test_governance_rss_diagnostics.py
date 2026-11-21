"""Offline P4DIAG scope and independent receipt regressions."""

from contextlib import contextmanager, redirect_stderr
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import UUID


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "p4diag_tests", ROOT / "tools/governance_rss_diagnostics.py")
diag = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(diag)
g, n = diag.g, diag.n


def json_text(record):
    return json.dumps(record, sort_keys=True) + "\n"


def blob_id(data):
    return hashlib.sha1(f"blob {len(data)}\0".encode("ascii") + data,
                        usedforsecurity=False).hexdigest()


class ScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baseline = diag.baseline_entries(ROOT)
        blobs = g.read_blobs(ROOT, cls.baseline.values())
        cls.contents = {name: blobs[oid] for name, oid in cls.baseline.items()}
        cls.contents.update({name: (ROOT / name).read_bytes().decode("utf-8")
                             for name in (*diag.PLANNING, diag.ENTRY)})
        plan = diag._validate_plan(cls.contents)
        for name in set(plan["additions"]) - set(diag.RECEIPTS):
            cls.contents.setdefault(name, "Offline fixture.\n")
        cls.contents[diag.WORKFLOW] = cls.contents[diag.WORKFLOW].replace(
            "tools/governance_news_ci_repair.py ", "tools/governance_rss_diagnostics.py ")

    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory(prefix="p4diag-"))
        self.root = Path(directory).resolve()
        self.entries = {}
        self.modes = {}
        for name, text in self.contents.items():
            self.put(name, text)

    def put(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        data = text.encode("utf-8")
        path.write_bytes(data)
        self.entries[name] = blob_id(data)

    @contextmanager
    def fixture_index(self, *changed):
        git_raw = g.git_raw

        def read_git(root, *args, **kwargs):
            self.assertEqual(root, self.root)
            if args == ("ls-tree", "-r", "-z", diag.BASE) or args[:2] == ("cat-file", "blob"):
                return git_raw(ROOT, *args, **kwargs)
            self.assertEqual(args, ("ls-files", "--stage", "-z"))
            return "".join(f"{self.modes.get(name, '100644')} {oid} 0\t{name}\0"
                           for name, oid in self.entries.items()).encode("utf-8")

        with (patch.object(g, "git_raw", side_effect=read_git),
              patch.object(g, "inspect_git", return_value=list(changed)) as inspect):
            yield inspect

    def test_snapshot_binds_staged_bytes_and_actual_archived_receipts_without_dispatch(self):
        with (self.fixture_index(diag.WORKFLOW) as inspect,
              patch.object(n, "_probe_module", side_effect=AssertionError("legacy probe loaded")),
              patch.object(n, "observe", side_effect=AssertionError("source dispatch attempted"))):
            snapshot = diag.snapshot(self.root, ci=True)
        self.assertTrue(inspect.call_args.kwargs["ci"])
        self.assertEqual(inspect.call_args.kwargs["baseline"], diag.BASE)
        self.assertEqual(inspect.call_args.kwargs["branch"], diag.BRANCH)
        self.assertEqual(snapshot["planning_snapshot"], diag.PLANNING_SNAPSHOT)
        self.assertEqual(snapshot["staged_paths"], sorted({diag.WORKFLOW, *(
            set(self.contents) - self.baseline.keys())}))
        self.assertEqual(set(snapshot["protected_paths"]), self.contents.keys())
        self.assertFalse(set(diag.RECEIPTS) & set(snapshot["protected_paths"]))
        for name in (n.ENTRY, *n.RECEIPTS, diag._repair.ENTRY, *diag._repair.RECEIPTS):
            with self.subTest(path=name):
                raw = g.git_raw(ROOT, "cat-file", "blob", self.baseline[name])
                self.assertEqual(snapshot["protected_file_hashes"][name], hashlib.sha256(raw).hexdigest())
        observation = g.parse_json(self.contents[n.OBSERVATION])
        self.assertEqual(snapshot["original_observation_sha256"], diag.ORIGINAL_OBSERVATION_SHA256)
        self.assertEqual(observation["status"], "FAILED")
        self.assertEqual(observation["observation"]["error"], "invalid_response")
        self.assertEqual(observation["observation"]["dispatch_attempts"], 1)

    def test_staging_an_edited_archived_receipt_does_not_authorize_it(self):
        with self.fixture_index():
            for name in (n.OBSERVATION, n.REVIEW, diag._repair.RELEASE):
                self.put(name, self.contents[name] + "\n")
                with self.subTest(path=name), self.assertRaisesRegex(g.Block, "immutable PR17 bytes"):
                    diag.check(self.root, ci=True)
                self.put(name, self.contents[name])

    def test_unknown_paths_and_workflow_permission_changes_block_in_ci(self):
        with self.fixture_index("unexpected.txt"), self.assertRaisesRegex(g.Block, "contract scope"):
            diag.check(self.root, ci=True)
        self.put(diag.WORKFLOW, self.contents[diag.WORKFLOW].replace("contents: read", "contents: write"))
        with self.fixture_index(diag.WORKFLOW), self.assertRaisesRegex(g.Block, "three offline command"):
            diag.check(self.root, ci=True)

    def test_raw_index_worktree_bytes_and_modes_are_exact(self):
        name = diag.WORKFLOW
        raw = self.contents[name].encode("utf-8")
        crlf = raw.replace(b"\n", b"\r\n")
        with self.fixture_index(name):
            self.entries[name] = blob_id(crlf)
            with self.assertRaisesRegex(g.Block, "index/worktree bytes differ"):
                diag.check(self.root)
            (self.root / name).write_bytes(crlf)
            with self.assertRaisesRegex(g.Block, "must use LF bytes"):
                diag.check(self.root)
            (self.root / name).write_bytes(b"\xff")
            with self.assertRaises(UnicodeDecodeError):
                diag.check(self.root)
            self.put(name, self.contents[name])
            self.modes[name] = "100755"
            with self.assertRaisesRegex(g.Block, "unexpected file mode"):
                diag.check(self.root)

    def test_plan_and_auditor_cannot_self_authorize(self):
        tampered = {**self.contents, diag.CONTRACT: self.contents[diag.CONTRACT] + "\n"}
        receipt = g.parse_json(tampered[diag.ENTRY])
        receipt["planning_snapshot"], _ = g.snapshot_for_paths(tampered, diag.PLANNING)
        tampered[diag.ENTRY] = json_text(receipt)
        with self.assertRaisesRegex(g.Block, "frozen planning snapshot"):
            diag._validate_plan(tampered)
        receipt = g.parse_json(self.contents[diag.ENTRY])
        for fields in ({"read_only": 1}, {"prompt_verdict": "BLOCK"}, {"agent_id": str(UUID(int=1))},
                       {"runtime_permissions_changed": True}):
            with self.subTest(fields=fields), self.assertRaisesRegex(g.Block, "entry and prompt receipt"):
                diag._validate_plan({**self.contents, diag.ENTRY: json_text({**receipt, **fields})})

    def test_optional_receipts_must_be_staged_when_present_and_required_for_gate(self):
        path = self.root / diag.VALIDATION
        with self.fixture_index():
            with self.assertRaisesRegex(g.Block, "missing required file"):
                diag.gate(self.root)
            path.write_bytes(b"{}\n")
            with self.assertRaisesRegex(g.Block, "every present candidate file staged"):
                diag.snapshot(self.root)


class ReleaseTests(unittest.TestCase):
    protected = {"tools/fixture.py": "Offline fixture.\n"}
    snapshot_id, _ = g.snapshot_for_paths(protected, protected)
    builders = [str(UUID(int=1)), str(UUID(int=2))]
    reviewer, releaser = str(UUID(int=3)), str(UUID(int=4))

    def receipts(self, *, validation=None, review=None, release=None):
        common = {"schema_version": 1, "package": "P4DIAG", "snapshot_id": self.snapshot_id}
        validation_text = json_text({
            **common, "role": "builder_validation", "verdict": "PASS", "offline": True,
            "new_source_requests": 0, "agents": self.builders,
            "product_collected": 990, "product_passed": 989, "product_skipped": 1,
            "focused_diagnostics": 10, "focused_governance": 5, "historical_p4fedci": 10,
            **(validation or {}),
        })
        review_text = json_text({
            **common, "role": "reviewer", "verdict": "PASS", "read_only": True,
            "agent_id": self.reviewer, "findings": [], "validation_sha256": g.sha256(validation_text),
            **(review or {}),
        })
        release_text = json_text({
            **common, "role": "release_checker", "verdict": "LOCAL_READY_FOR_PR", "read_only": True,
            "agent_id": self.releaser, "review_sha256": g.sha256(review_text),
            "original_observation_sha256": diag.ORIGINAL_OBSERVATION_SHA256,
            "human_merge_required": True, "remote_ci": "NOT_RUN_AT_SNAPSHOT", **(release or {}),
        })
        return {diag.VALIDATION: validation_text, diag.REVIEW: review_text, diag.RELEASE: release_text}

    def test_gate_checks_complete_index_and_bound_receipt_chain(self):
        contents = {**self.protected, **self.receipts()}
        checked = contents, tuple(self.protected), list(self.protected)
        with patch.object(diag, "check", return_value=checked) as check:
            self.assertEqual(diag.gate(ROOT, ci=True), "LOCAL_READY_FOR_PR")
        check.assert_called_once_with(ROOT, ci=True, complete_index=True)
        for path in diag.RECEIPTS:
            missing = {name: text for name, text in contents.items() if name != path}
            with self.subTest(path=path), self.assertRaisesRegex(g.Block, "missing required receipt"):
                diag.validate_chain(missing, self.snapshot_id)

    def test_counts_and_offline_claims_are_exact(self):
        for fields in (
            {"new_source_requests": 1}, {"new_source_requests": False}, {"offline": 1},
            {"product_collected": 989, "product_passed": 988}, {"product_passed": 990},
            {"product_skipped": 2, "product_passed": 988}, {"focused_diagnostics": 9},
            {"focused_governance": 4}, {"historical_p4fedci": 9}, {"allow_network": True},
        ):
            with self.subTest(fields=fields), self.assertRaises(g.Block):
                diag.validate_chain(self.receipts(validation=fields), self.snapshot_id)

    def test_hash_links_snapshot_and_human_merge_cannot_be_substituted(self):
        for path in (diag.VALIDATION, diag.REVIEW):
            contents = self.receipts()
            contents[path] += "\n"
            with self.subTest(path=path), self.assertRaisesRegex(g.Block, "sha256"):
                diag.validate_chain(contents, self.snapshot_id)
        for role, fields in (
            ("review", {"snapshot_id": "a" * 64}), ("review", {"findings": ["unresolved"]}),
            ("release", {"original_observation_sha256": "b" * 64}),
            ("release", {"human_merge_required": False}), ("release", {"remote_ci": "PASS"}),
        ):
            with self.subTest(role=role, fields=fields), self.assertRaises(g.Block):
                diag.validate_chain(self.receipts(**{role: fields}), self.snapshot_id)

    def test_all_five_native_actors_are_independent(self):
        for role, fields in (
            ("validation", {"agents": self.builders[:1]}),
            ("validation", {"agents": [self.builders[0]] * 2}),
            ("validation", {"agents": [self.builders[0], diag.AUDITOR]}),
            ("review", {"agent_id": self.builders[1]}), ("review", {"agent_id": "coordinator"}),
            ("release", {"agent_id": self.reviewer}), ("release", {"agent_id": diag.AUDITOR}),
        ):
            with (self.subTest(role=role, fields=fields),
                  self.assertRaisesRegex(g.Block, "two builder agents|independent|native agent")):
                diag.validate_chain(self.receipts(**{role: fields}), self.snapshot_id)

    def test_cli_does_not_offer_dispatch_or_extra_input_options(self):
        for args in (("observe", "--ci"), ("preprobe",), ("gate", "--allow-network"),
                     ("snapshot", "--root", "."), ("check", "--c")):
            with (self.subTest(args=args), redirect_stderr(io.StringIO()),
                  self.assertRaises(SystemExit) as error,
                  patch.object(diag, "check", side_effect=AssertionError("scope check reached"))):
                diag.main(args)
            self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
