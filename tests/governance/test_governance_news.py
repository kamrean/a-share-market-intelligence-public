"""Offline P4FED scope, independent receipts and CLI refusals."""

from contextlib import nullcontext, redirect_stderr, redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import py_compile
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[2]
TOOL = "tools/governance_news.py"
SPEC = importlib.util.spec_from_file_location("p4fed_governance_tests", ROOT / TOOL)
news = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(news)
g = news.g


def json_text(value):
    return json.dumps(value, sort_keys=True) + "\n"


def terminal_failure(pending):
    return {**pending, "status": "FAILED", "observation": {
        "operation_id": news.OPERATION_ID, "started_at": "2026-11-21T00:00:00Z",
        "finished_at": "2026-11-21T00:00:01Z", "captured_at": None, "freshness_seconds": 2678400,
        "dispatch_attempts": 1, "counts": None, "error": "transport_failed",
    }}


def authored_receipts(snapshot_id, pending):
    shared = {"schema_version": 1, "package": "P4FED", "snapshot_id": snapshot_id}
    validation = json_text({
        **shared, "role": "builder_validation", "verdict": "PASS", "offline": True,
        "product_collected": 953, "product_passed": 952, "product_skipped": 1,
        "focused_product": 5, "focused_governance": 5, "historical_core": 12,
        "agents": [str(uuid4()), str(uuid4())],
    })
    review = json_text({
        **shared, "role": "reviewer", "verdict": "PASS", "read_only": True,
        "agent_id": str(uuid4()), "validation_sha256": g.sha256(validation), "findings": [],
    })
    checker = str(uuid4())
    preprobe = json_text({
        **shared, "role": "release_checker", "verdict": "PREPROBE_READY", "read_only": True,
        "agent_id": checker, "review_sha256": g.sha256(review), "max_attempts": 1,
    })
    observation = json_text(terminal_failure(pending))
    release = json_text({
        **shared, "role": "release_checker", "verdict": "LOCAL_READY_FOR_PR", "read_only": True,
        "agent_id": checker, "preprobe_sha256": g.sha256(preprobe),
        "observation_sha256": g.sha256(observation), "human_merge_required": True,
        "remote_ci": "NOT_RUN_AT_SNAPSHOT",
    })
    return {news.VALIDATION: validation, news.REVIEW: review, news.PRE_RELEASE: preprobe,
            news.OBSERVATION: observation, news.RELEASE: release}


def isolated_env():
    return {key: value for key, value in os.environ.items()
            if key.upper() not in ("CI", "GITHUB_ACTIONS", "PYTHONPATH")}


class PlanningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.planning = {name: g.read_text(ROOT, name) for name in (*news.PLANNING_PATHS, news.ENTRY)}

    def test_pinned_contract_and_entry(self):
        contract = news.validate_planning(self.planning)
        self.assertEqual((contract["base"], contract["branch"]), (news.BASE, news.BRANCH))
        self.assertEqual(tuple(contract["attestations"]), news.RECEIPTS)

    def test_changed_plan_and_inexact_entry_fail_closed(self):
        for name in news.PLANNING_PATHS:
            with self.subTest(path=name), self.assertRaisesRegex(news.Block, "frozen planning snapshot"):
                news.validate_planning({**self.planning, name: self.planning[name] + "\n"})
        entry = g.parse_json(self.planning[news.ENTRY])
        for key, value in (("entry_verdict", "BLOCK"), ("prompt_verdict", "BLOCK"), ("read_only", 1),
                           ("agent_id", str(uuid4())), ("live", True), ("findings", ["unresolved"])):
            with self.subTest(field=key), self.assertRaisesRegex(news.Block, "entry and prompt receipt"):
                news.validate_planning({**self.planning, news.ENTRY: json_text({**entry, key: value})})


class ChainTests(unittest.TestCase):
    def setUp(self):
        self.digest = "a" * 64
        with news._probe_module(ROOT) as probe:
            self.receipts = authored_receipts(self.digest, probe.pending(self.digest))

    def change(self, path, **fields):
        contents = dict(self.receipts)
        contents[path] = json_text({**g.parse_json(contents[path]), **fields})
        return contents

    def test_exact_receipt_and_normalized_chain_hashes(self):
        checker = news.validate_chain(self.receipts, self.digest)
        self.assertEqual(checker, g.parse_json(self.receipts[news.RELEASE])["agent_id"])
        self.assertEqual(checker, news.validate_chain(
            {name: text.replace("\n", "\r\n") for name, text in self.receipts.items()}, self.digest))
        for path in (news.VALIDATION, news.REVIEW):
            with self.subTest(path=path), self.assertRaisesRegex(news.Block, "sha256"):
                news.validate_chain({**self.receipts, path: self.receipts[path] + "\n"}, self.digest)
        for path, fields in (
            (news.VALIDATION, {"offline": 1}), (news.VALIDATION, {"product_collected": 952}),
            (news.VALIDATION, {"product_passed": 953}), (news.VALIDATION, {"product_skipped": 2}),
            (news.VALIDATION, {"focused_product": 4}), (news.VALIDATION, {"focused_governance": True}),
            (news.VALIDATION, {"historical_core": 11}), (news.REVIEW, {"findings": ["BLOCK"]}),
            (news.REVIEW, {"snapshot_id": "b" * 64}), (news.PRE_RELEASE, {"max_attempts": True}),
            (news.PRE_RELEASE, {"request_authorized": True}),
        ):
            with self.subTest(path=path, fields=fields), self.assertRaises(news.Block):
                news.validate_chain(self.change(path, **fields), self.digest)

    def test_roles_are_independent_and_native(self):
        builders = g.parse_json(self.receipts[news.VALIDATION])["agents"]
        reviewer = g.parse_json(self.receipts[news.REVIEW])["agent_id"]
        with self.assertRaisesRegex(news.Block, "duplicate"):
            news.validate_chain(self.change(news.VALIDATION, agents=[builders[0]] * 2), self.digest)
        for actor in (news.AUDITOR, *builders, reviewer, "coordinator", reviewer.upper()):
            with self.subTest(actor=actor), self.assertRaisesRegex(news.Block, "independent|native agent"):
                news.validate_chain(self.change(news.PRE_RELEASE, agent_id=actor), self.digest)


class CliTests(unittest.TestCase):
    def test_ci_and_no_opt_in_refuse_before_authorization_or_import(self):
        for command in ("preprobe", "observe"):
            network = ["--allow-network"] if command == "observe" else []
            for env, flags in (({}, ["--ci"]), ({"CI": ""}, []), ({"GITHUB_ACTIONS": ""}, [])):
                out, err = io.StringIO(), io.StringIO()
                with (
                    self.subTest(command=command, env=env, flags=flags),
                    patch.dict(os.environ, env, clear=True), redirect_stdout(out), redirect_stderr(err),
                    patch.object(news, "check", side_effect=AssertionError("scope accessed")),
                    patch.object(g, "git_raw", side_effect=AssertionError("Git accessed")),
                    patch.object(news, "_probe_module", side_effect=AssertionError("product accessed")),
                ):
                    self.assertEqual(news.main([command, *flags, *network]), 1)
                self.assertEqual(out.getvalue(), "")
                self.assertIn("--ci is not runtime authorization", err.getvalue())

        out, err = io.StringIO(), io.StringIO()
        with (patch.dict(os.environ, isolated_env(), clear=True),
              patch.object(news, "preprobe", side_effect=AssertionError("authorization accessed")) as auth,
              redirect_stdout(out), redirect_stderr(err)):
            self.assertEqual(news.main(["observe"]), 1)
        auth.assert_not_called()
        self.assertEqual((out.getvalue(), err.getvalue()), ("", "BLOCK\n"))

    def test_observe_errors_are_literal_blocks(self):
        for error in (OSError("private path or response body"), KeyboardInterrupt(),
                      SystemExit("private detail")):
            out, err = io.StringIO(), io.StringIO()
            with (patch.dict(os.environ, isolated_env(), clear=True),
                  patch.object(news, "observe", side_effect=error),
                  redirect_stdout(out), redirect_stderr(err)):
                self.assertEqual(news.main(["observe", "--allow-network"]), 1)
            self.assertEqual((out.getvalue(), err.getvalue()), ("", "BLOCK\n"))

    def test_direct_ci_cli_refuses_without_even_local_helpers(self):
        with tempfile.TemporaryDirectory(prefix="p4fed-cli-") as directory:
            script = Path(directory) / "governance_news.py"
            script.write_bytes((ROOT / TOOL).read_bytes())
            result = subprocess.run([sys.executable, "-B", str(script), "observe", "--ci", "--allow-network"],
                                    cwd=directory, env=isolated_env(), capture_output=True, text=True)
        self.assertEqual((result.returncode, result.stdout), (1, ""))
        self.assertIn("--ci is not runtime authorization", result.stderr)

    def test_product_loader_reads_source_instead_of_matching_cached_bytecode(self):
        with tempfile.TemporaryDirectory(prefix="p4fed-source-") as directory:
            root = Path(directory)
            product = root / news.PRODUCT
            product.parent.mkdir()
            product.with_name("__init__.py").write_bytes(b"")
            product.write_bytes(b'ORIGIN = "cached"\n')
            cached = py_compile.compile(str(product), doraise=True,
                                        invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP)
            stamp = product.stat()
            product.write_bytes(b'ORIGIN = "source"\n')
            os.utime(product, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
            self.assertTrue(Path(cached).is_file())
            with news._probe_module(root) as probe:
                self.assertEqual(probe.ORIGIN, "source")

    def test_predecessor_ci_matrix_and_historical_refusals(self):
        workflow = g.git(ROOT, "show", f"{news.BASE}:.github/workflows/governance.yml")
        self.assertIn("  governance:\n", workflow)
        self.assertIn("os: [ubuntu-24.04, windows-2022]", workflow)
        for command in ("check", "snapshot", "gate"):
            self.assertIn(f"python tools/governance_core.py {command} --ci", workflow)
        self.assertIn("'tencent-gate', '--ci'", workflow)
        self.assertIn("for phase in ('diagnostic', 'verification'):", workflow)
        for tool, command, message in (
            (TOOL, "observe", "--ci is not runtime authorization"),
            ("tools/governance_p4t.py", "tencent-gate", "--ci is not runtime authorization"),
            ("tools/governance.py", "diagnostic-gate", "--ci is not runtime authorization"),
            ("tools/governance.py", "verification-gate", "--ci is not runtime authorization"),
            *(("tools/governance.py", name + "-gate", "closed")
              for name in ("probe", "preparation", "nbs", "market")),
        ):
            with self.subTest(command=command):
                flags = ["--allow-network"] if command == "observe" else []
                result = subprocess.run([sys.executable, "-B", tool, command, "--ci", *flags],
                                        cwd=ROOT, env=isolated_env(), capture_output=True, text=True)
                self.assertEqual((result.returncode, result.stdout), (1, ""))
                self.assertIn(message, result.stderr)


class ObserveTests(unittest.TestCase):
    def test_changed_startup_approval_is_refused_before_reservation(self):
        initial = ("a" * 64, "b" * 64)
        reissued = (initial[0], "c" * 64)
        reserve = Mock()

        def stub_observe(authorize):
            authorize()
            reserve()

        probe = SimpleNamespace(_observe=stub_observe, validate_receipt=Mock())
        with (patch.dict(os.environ, isolated_env(), clear=True),
              patch.object(news, "preprobe", side_effect=[initial, reissued]) as preprobe,
              patch.object(news, "_probe_module", return_value=nullcontext(probe))):
            with self.assertRaisesRegex(news.Block, "startup preprobe approval"):
                news.observe(ROOT, allow_network=True)
        self.assertEqual(preprobe.call_count, 2)
        reserve.assert_not_called()
        probe.validate_receipt.assert_not_called()


class CandidateTests(unittest.TestCase):
    def test_index_snapshot_and_offline_release_lifecycle(self):
        planning = {name: g.read_text(ROOT, name) for name in (*news.PLANNING_PATHS, news.ENTRY)}
        contract = news.validate_planning(planning)
        with tempfile.TemporaryDirectory(prefix="p4fed-candidate-") as directory:
            root = Path(directory)
            g.git(root, "init", "--initial-branch=" + news.BRANCH)
            g.git(root, "config", "core.autocrlf", "true")
            g.git(root, "-c", "protocol.file.allow=always", "fetch", "--no-tags", str(ROOT), news.BASE)
            g.git(root, "checkout", "-B", news.BRANCH, news.BASE)

            def write(name, text):
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(text.encode("utf-8"))

            def stage(name, text):
                write(name, text)
                g.git(root, "add", "--", name)

            protected = "tests/product/test_primary_news_probe.py"
            authored = "Authored offline candidate fixture.\n"
            additions = set(contract["additions"]) - set(news.RECEIPTS)
            for name in additions:
                if name in (news.PRODUCT, TOOL):
                    write(name, g.read_text(ROOT, name))
                else:
                    write(name, planning.get(name, authored))
            for name, text in planning.items():
                write(name, text)
            g.git(root, "add", "--", *sorted((additions | planning.keys()) - {protected}))
            with self.assertRaisesRegex(news.Block, "present candidate file staged"):
                news.snapshot(root)
            stage(protected, authored)
            approved = news.snapshot(root)
            snapshot_id = approved["snapshot_id"]
            self.assertEqual(approved["planning_snapshot"], news.PLANNING_SNAPSHOT)
            self.assertEqual(set(approved["protected_paths"]),
                             (news.baseline_entries(root).keys() | set(contract["additions"])) - set(news.RECEIPTS))
            self.assertEqual(set(approved["protected_file_hashes"]), set(approved["protected_paths"]))
            with self.assertRaisesRegex(news.Block, "missing required file"):
                news.gate(root, ci=True)

            with news._probe_module(root) as probe:
                pending = probe.pending(snapshot_id)
            receipts = authored_receipts(snapshot_id, pending)
            for name in (news.VALIDATION, news.REVIEW, news.PRE_RELEASE):
                stage(name, receipts[name])
            write(news.OBSERVATION, json_text(pending))
            with patch.dict(os.environ, isolated_env(), clear=True):
                with self.assertRaisesRegex(news.Block, "present candidate file staged"):
                    news.preprobe(root)
                stage(news.OBSERVATION, json_text(pending))
                chain_paths = (news.VALIDATION, news.REVIEW, news.PRE_RELEASE, news.OBSERVATION)
                chain_contents = {**receipts, news.OBSERVATION: json_text(pending)}
                chain_sha256, _ = g.snapshot_for_paths(chain_contents, chain_paths)
                self.assertEqual(news.preprobe(root), (snapshot_id, chain_sha256))

                pre_release = g.parse_json(receipts[news.PRE_RELEASE])
                reissued = json_text({**pre_release, "agent_id": str(uuid4())})
                reissued_sha256, _ = g.snapshot_for_paths(
                    {**chain_contents, news.PRE_RELEASE: reissued}, chain_paths)
                self.assertNotEqual(chain_sha256, reissued_sha256)
                dispatch = Mock(side_effect=AssertionError("unexpected dispatch"))
                append = Mock()
                tokens = []
                preprobe = news.preprobe

                def record_token(*args, **kwargs):
                    token = preprobe(*args, **kwargs)
                    tokens.append(token)
                    return token

                def reissue_after_reservation(_):
                    self.assertEqual(append.call_count, 1)
                    reservation = append.call_args.args[1]
                    self.assertEqual((reservation["kind"], reservation["snapshot_id"],
                                      reservation["approval_chain_sha256"]),
                                     ("reservation", snapshot_id, chain_sha256))
                    stage(news.PRE_RELEASE, reissued)

                journal = root / "unused-journal"
                with (
                    news._probe_module(root) as probe,
                    patch.object(news, "preprobe", side_effect=record_token),
                    patch.multiple(
                        probe, _pinned_source=Mock(return_value=Mock()),
                        _path=Mock(return_value=journal), external_path=Mock(return_value=journal),
                        _open_journal=Mock(return_value=nullcontext(object())), _attached=Mock(),
                        _append=append, _sync_namespace=Mock(side_effect=reissue_after_reservation),
                        _fetch_summary=dispatch,
                    ),
                ):
                    held = news.observe(root, allow_network=True)
                self.assertEqual(tokens, [(snapshot_id, chain_sha256), (snapshot_id, chain_sha256),
                                          (snapshot_id, reissued_sha256)])
                dispatch.assert_not_called()
                self.assertEqual((held["snapshot_id"], held["status"], held["observation"]["dispatch_attempts"],
                                  held["observation"]["error"]), (snapshot_id, "UNKNOWN", 0, "gate_rejected"))
                self.assertEqual(append.call_args.args[1], {"kind": "final", "receipt": held})
                self.assertFalse(journal.exists())
                stage(news.PRE_RELEASE, receipts[news.PRE_RELEASE])

                crlf = receipts[news.REVIEW].replace("\n", "\r\n")
                write(news.REVIEW, crlf)
                blob = g.git(root, "hash-object", "-w", "--no-filters", "--", news.REVIEW).strip()
                g.git(root, "update-index", "--cacheinfo", f"100644,{blob},{news.REVIEW}")
                self.assertEqual(g.git_raw(root, "show", f":{news.REVIEW}"), crlf.encode("utf-8"))
                with self.assertRaisesRegex(news.Block, "must use LF bytes"):
                    news.preprobe(root)
                write(news.REVIEW, receipts[news.REVIEW])
                with self.assertRaisesRegex(news.Block, "index/worktree bytes differ"):
                    news.preprobe(root)
                stage(news.REVIEW, receipts[news.REVIEW])
                stage(protected, authored + "Changed staged content.\n")
                self.assertNotEqual(news.snapshot(root)["snapshot_id"], snapshot_id)
                with self.assertRaisesRegex(news.Block, "snapshot_id"):
                    news.preprobe(root)
                stage(protected, authored)

                g.git(root, "branch", "-m", "wrong-branch")
                with self.assertRaisesRegex(news.Block, "Git branch"):
                    news.preprobe(root)
                g.git(root, "branch", "-m", news.BRANCH)

                stage(news.OBSERVATION, receipts[news.OBSERVATION])
                with self.assertRaisesRegex(news.Block, "pending observation"):
                    news.preprobe(root)
                with self.assertRaisesRegex(news.Block, "missing required file"):
                    news.gate(root)
                write(news.RELEASE, receipts[news.RELEASE])
                with self.assertRaisesRegex(news.Block, "present candidate file staged"):
                    news.preprobe(root)
                stage(news.RELEASE, receipts[news.RELEASE])
                with self.assertRaisesRegex(news.Block, "final release already exists"):
                    news.preprobe(root)

            final = g.parse_json(receipts[news.RELEASE])
            stage(news.RELEASE, json_text({**final, "agent_id": str(uuid4())}))
            with self.assertRaisesRegex(news.Block, "agent_id"):
                news.gate(root)
            stage(news.RELEASE, json_text({**final, "authorization": True}))
            with self.assertRaisesRegex(news.Block, "unknown fields"):
                news.gate(root)
            stage(news.RELEASE, receipts[news.RELEASE])
            stage(news.OBSERVATION, json_text(pending))
            with self.assertRaisesRegex(news.Block, "observation_sha256"):
                news.gate(root)
            stage(news.RELEASE, json_text({**final, "observation_sha256": g.sha256(json_text(pending))}))
            with self.assertRaisesRegex(ValueError, "invalid_primary_news_receipt"):
                news.gate(root)
            stage(news.OBSERVATION, receipts[news.OBSERVATION])
            stage(news.RELEASE, receipts[news.RELEASE])

            self.assertEqual(news.snapshot(root, ci=True), approved)
            result = subprocess.run([sys.executable, "-B", TOOL, "gate", "--ci"], cwd=root,
                                    env=isolated_env(), capture_output=True, text=True)
            self.assertEqual((result.returncode, result.stdout, result.stderr),
                             (0, "LOCAL_READY_FOR_PR\n", ""))

            g.git(root, "update-ref", "HEAD", g.git(root, "rev-parse", "HEAD^").strip())
            with patch.dict(os.environ, isolated_env(), clear=True):
                with self.assertRaisesRegex(news.Block, "original base HEAD"):
                    news.preprobe(root)
            g.git(root, "update-ref", "HEAD", news.BASE)

            historical = "docs/reviews/p4core-entry.json"
            stage(historical, g.read_text(root, historical) + "\n")
            with self.assertRaisesRegex(news.Block, "immutable merged path"):
                news.gate(root, ci=True)


if __name__ == "__main__":
    unittest.main()
