"""Offline operator tests; authored results never count as live observations."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, redirect_stdout
from copy import deepcopy
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("final_connection", Path(__file__).with_name("run_once.py"))
op = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(op)


def checked_fixture():
    snapshot = "a" * 64
    records = {
        "validation.json": {"snapshot_id": snapshot, "verdict": "PASS", "commands": [
            {"command": c, "exit_code": 0} for c in op.COMMANDS]},
        "result.json": {"status": "NOT_RUN"},
    }
    for name, role, verdict, agent in (("review.json", "reviewer", "PASS", "independent-reviewer"),
                                      ("release.json", "release_checker", "LOCAL_READY_FOR_SINGLE_CONNECTION", "independent-release")):
        records[name] = {"snapshot_id": snapshot, "role": role, "verdict": verdict,
                         "agent_id": agent, "read_only": True, "findings": [], "repair_cycles": 1}
    return {"snapshot_id": snapshot, "contents": {op.PREFIX + k: json.dumps(v) for k, v in records.items()}}


class OperatorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        (self.home / ".a-share-market-intelligence").mkdir()
        self.path = self.home / ".a-share-market-intelligence" / (op.OPERATION + ".sqlite")

    def test_approvals_and_consumed_repair_count(self):
        op.approvals(checked_fixture())
        for name, field, bad in (("review.json", "agent_id", "coordinator"),
                                  ("review.json", "repair_cycles", 0),
                                  ("review.json", "read_only", 1),
                                  ("release.json", "snapshot_id", "b" * 64),
                                  ("release.json", "agent_id", "independent-reviewer"),
                                  ("review.json", "findings", ["blocked"]),
                                  ("validation.json", "verdict", "NOT_RUN")):
            with self.subTest(name=name, field=field):
                c = checked_fixture()
                value = json.loads(c["contents"][op.PREFIX + name])
                value[field] = bad
                c["contents"][op.PREFIX + name] = json.dumps(value)
                with self.assertRaises(ValueError):
                    op.approvals(c)

    def test_existing_result_and_unknown_approval_fields_block(self):
        for name in ("review.json", "release.json", "validation.json", "result.json"):
            c = checked_fixture()
            value = json.loads(c["contents"][op.PREFIX + name])
            value["extra"] = True
            c["contents"][op.PREFIX + name] = json.dumps(value)
            with self.assertRaises(ValueError):
                op.approvals(c)

    def test_exclusive_reservation_and_partial_file_are_consumed(self):
        for initial in (b"", b"incomplete", None):
            with self.subTest(initial=initial):
                path = self.path.with_name(str(len(str(initial))) + ".sqlite")
                if initial is None:
                    op.reserve(path, "a" * 64, {})
                else:
                    path.write_bytes(initial)
                old = path.read_bytes()
                with self.assertRaises(FileExistsError):
                    op.reserve(path, "b" * 64, {})
                self.assertEqual(path.read_bytes(), old)

    def test_concurrent_reservation_has_exactly_one_winner(self):
        def attempt():
            try:
                op.reserve(self.path, "a" * 64, {})
                return True
            except FileExistsError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(lambda _: attempt(), range(2))), [False, True])

    def test_reservation_is_durable_before_dispatch_and_cannot_repeat(self):
        def collect(snapshot, before):
            with closing(sqlite3.connect(self.path)) as db:
                row = json.loads(db.execute("SELECT metadata FROM records WHERE kind='reservation'").fetchone()[0])
            self.assertEqual(row["reserved_requests"], 1)
            self.assertEqual(row["snapshot_id"], snapshot)
            return {"status": "UNKNOWN"}
        with patch.dict(os.environ, {}, clear=True), patch.object(Path, "home", return_value=self.home), \
                patch.object(op, "check", return_value=checked_fixture()), \
                patch.object(op, "old_hashes", return_value={}), patch.object(op, "collect", side_effect=collect) as call:
            self.assertEqual(op.observe(), {"status": "UNKNOWN"})
            with self.assertRaises(FileExistsError):
                op.observe()
            self.assertEqual(call.call_count, 1)

    def test_ci_rejects_before_any_check_or_store(self):
        with patch.dict(os.environ, {"CI": ""}), patch.object(op, "check") as check:
            with self.assertRaises(ValueError):
                op.observe()
            check.assert_not_called()

    def test_failed_review_never_creates_journal(self):
        c = checked_fixture()
        c["contents"][op.PREFIX + "review.json"] = '{"status":"NOT_RUN"}'
        with patch.dict(os.environ, {}, clear=True), patch.object(op, "check", return_value=c), \
                patch.object(op, "reserve") as reserve:
            with self.assertRaises(ValueError):
                op.observe()
            reserve.assert_not_called()

    def test_changed_snapshot_consumes_without_dispatch(self):
        changed = {**checked_fixture(), "snapshot_id": "b" * 64}
        with patch.dict(os.environ, {}, clear=True), patch.object(Path, "home", return_value=self.home), \
                patch.object(op, "check", side_effect=[checked_fixture(), changed]), \
                patch.object(op, "old_hashes", return_value={}), patch.object(op, "collect") as collect:
            with self.assertRaises(ValueError):
                op.observe()
            self.assertTrue(self.path.exists())
            collect.assert_not_called()

    def test_revoked_release_after_reservation_prevents_dispatch(self):
        changed = checked_fixture()
        changed["contents"][op.PREFIX + "release.json"] = '{"status":"BLOCK"}'
        with patch.dict(os.environ, {}, clear=True), patch.object(Path, "home", return_value=self.home), \
                patch.object(op, "check", side_effect=[checked_fixture(), changed]), \
                patch.object(op, "old_hashes", return_value={}), patch.object(op, "collect") as collect:
            with self.assertRaises(ValueError):
                op.observe()
            self.assertTrue(self.path.exists())
            collect.assert_not_called()

    def test_quota_modification_and_deletion_are_detected(self):
        expected = op.reserve(self.path, "a" * 64, {})
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE records SET metadata='{}'")
            db.commit()
        self.assertNotEqual(op.quota_fingerprint(self.path), expected)
        self.path.unlink()
        with self.assertRaises(ValueError):
            op.quota_fingerprint(self.path)

    def test_old_journals_are_hash_only_and_sidecars_block(self):
        for name in op.OLD_JOURNALS:
            (self.path.parent / name).write_bytes(b"authored metadata only")
        with patch.object(Path, "home", return_value=self.home):
            hashes = op.old_hashes()
            self.assertEqual(set(hashes), set(op.OLD_JOURNALS))
            self.assertTrue(all(len(h) == 64 for h in hashes.values()))
            (self.path.parent / (op.OLD_JOURNALS[0] + "-wal")).touch()
            with self.assertRaises(ValueError):
                op.old_hashes()

    def test_transport_exception_is_unknown_and_text_is_discarded(self):
        def fail(**kwargs):
            print("PRIVATE_RESPONSE")
            raise KeyboardInterrupt("PRIVATE_EXCEPTION")
        output = io.StringIO()
        with patch.object(op.transport, "run_recovery", side_effect=fail) as call, redirect_stdout(output):
            result = op.collect("a" * 64, {})
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertIsNone(result["transport"])
        self.assertNotIn("PRIVATE", json.dumps(result))
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(call.call_args.kwargs["phase"], "diagnostic")
        self.assertEqual(call.call_count, 1)

    def test_failed_transport_closed_metadata_and_changed_journal(self):
        t = op.transport.TransportReceipt("FAILED", "recv", "2026-01-01T00:00:00Z", "2026-01-01T00:00:01Z",
                                          None, None, 1, 0, 0, "a" * 64, (), True, True, True)
        d = op.transport.empty_diagnostic()
        d.update(request_index=1, method="login", first_failure_point="transport_recv")
        with patch.object(op.transport, "run_recovery", return_value=(t, d)), patch.object(op, "old_hashes", return_value={}):
            result = op.collect("a" * 64, {})
            self.assertEqual((result["status"], result["reason"]), ("FAILED", "recv"))
            self.assertEqual(result["transport"]["validated_responses"], 0)
            self.assertTrue(result["old_journals_unchanged"])
            changed = op.collect("a" * 64, {"prior": "b" * 64})
            self.assertEqual(changed["reason"], "old_journal_changed")
        d["raw_payload"] = "PRIVATE"
        with patch.object(op.transport, "run_recovery", return_value=(t, d)):
            self.assertEqual(op.collect("a" * 64, {})["status"], "UNKNOWN")

    def test_cli_has_no_retry_reset_endpoint_or_phase_option(self):
        for args in (["observe"], ["retry"], ["reset"], ["observe", "verification"], ["check", "--ci"]):
            with patch.object(op, "observe") as observe, redirect_stdout(io.StringIO()) as output:
                self.assertEqual(op.main(args), 2)
                self.assertFalse(json.loads(output.getvalue())["retry_allowed"])
                observe.assert_not_called()


class ScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.root = Path(cls.tmp.name)
        entries = {}
        for record in op.g.git_paths(op.g.git(op.ROOT, "ls-tree", "-r", "-z", op.BASELINE)):
            meta, name = record.split("\t", 1)
            entries[name] = meta.split()[2]
        blobs = op.g.read_blobs(op.ROOT, entries.values())
        for name, oid in entries.items():
            path = cls.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(blobs[oid], encoding="utf-8", newline="\n")
        op.g.git(cls.root, "init", "-b", op.g.BRANCH)
        op.g.git(cls.root, "config", "user.name", "Offline fixture")
        op.g.git(cls.root, "config", "user.email", "fixture@users.noreply.github.com")
        op.g.git(cls.root, "add", ".")
        op.g.git(cls.root, "commit", "-m", "Authored test baseline")
        cls.baseline = op.g.git(cls.root, "rev-parse", "HEAD").strip()
        for name in (*op.MUTABLE, *(op.PREFIX + n for n in op.NAMES)):
            path = cls.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(op.g.read_text(op.ROOT, name), encoding="utf-8", newline="\n")
        op.g.git(cls.root, "add", ".")

    def test_complete_candidate_passes_and_snapshot_is_deterministic(self):
        with patch.object(op, "BASELINE", self.baseline):
            a = op.check(self.root)
            b = op.check(self.root)
            self.assertEqual(a["snapshot_id"], b["snapshot_id"])
            self.assertEqual(len(a["hashes"]), len(op.g.ALLOWED) + 4)

    def test_immutable_product_change_rejected(self):
        path = self.root / "market_intelligence/baostock_transport.py"
        original = path.read_text(encoding="utf-8")
        try:
            path.write_text(original + "\n# authored mutation\n", encoding="utf-8")
            with patch.object(op, "BASELINE", self.baseline), self.assertRaises(ValueError):
                op.check(self.root)
        finally:
            path.write_text(original, encoding="utf-8", newline="\n")

    def test_unexpected_ignored_artifact_rejected(self):
        path = self.root / "unexpected.zip"
        try:
            path.write_bytes(b"authored junk")
            with patch.object(op, "BASELINE", self.baseline), self.assertRaises(ValueError):
                op.check(self.root)
        finally:
            path.unlink()

    def test_unstaged_receipt_and_staged_deletion_rejected(self):
        name = op.PREFIX + "result.json"
        path = self.root / name
        original = path.read_text(encoding="utf-8")
        try:
            path.write_text('{"status":"UNKNOWN"}', encoding="utf-8")
            with patch.object(op, "BASELINE", self.baseline), self.assertRaises(ValueError):
                op.check(self.root)
            path.write_text(original, encoding="utf-8", newline="\n")
            op.g.git(self.root, "update-index", "--force-remove", "--", name)
            with patch.object(op, "BASELINE", self.baseline), self.assertRaises(ValueError):
                op.check(self.root)
        finally:
            path.write_text(original, encoding="utf-8", newline="\n")
            op.g.git(self.root, "add", "--", name)


if __name__ == "__main__":
    unittest.main()
