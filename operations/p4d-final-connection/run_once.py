"""Explicit final operator allowance; never an old P4D runtime capability."""

from contextlib import closing, redirect_stderr, redirect_stdout
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools import governance as g
from market_intelligence import baostock_capture as capture
from market_intelligence import baostock_recovery as recovery
from market_intelligence import baostock_transport as transport
from market_intelligence.models import canonical, stamp, utc_now
from market_intelligence.store import external_path

BASELINE = "5365ce7f9524e361758c45246f8a20a9ff61bde2"
OPERATION = "p4d-final-connection-20261119"
PREFIX = "operations/p4d-final-connection/"
NAMES = ("contract.json", "run_once.py", "test_run_once.py", "entry-review.json",
         "validation.json", "review.json", "release.json", "result.json", "disposition.json")
MUTABLE = {"AGENTS.md", "docs/CONSTRUCTION_LOG.md"}
ATTESTATIONS = {PREFIX + n for n in NAMES[4:]}
ENTRY_HASHES = {
    "AGENTS.md": "b34dd740b258a3fcee569de850af48519b999e2b8969af13c77b5325136173ba",
    PREFIX + "contract.json": "10af97521ece4b0ae30839c002d62e131a4823180fda96074d1d11650c95f9d7",
    "docs/CONSTRUCTION_LOG.md": "2061137e0efd6787525ec94ff4841d165e626db4292a89e9e5271a0850febf42",
}
COMMANDS = [
    "python -m unittest discover -s operations/p4d-final-connection -p test_run_once.py -v",
    "python -m pip check", "python operations/p4d-final-connection/run_once.py check",
    "git diff --check",
]
OLD_JOURNALS = ("p4b-baostock-test.sqlite", "p4d-baostock-recovery.sqlite")


def require(condition):
    if not condition:
        raise ValueError("operator_precondition_blocked")


def check(root=ROOT):
    """New named scope, not a waiver or invocation of the blocked old gate."""
    root = Path(root).resolve()
    require(Path(g.git(root, "rev-parse", "--show-toplevel").strip()).resolve() == root)
    require(g.git(root, "symbolic-ref", "--short", "HEAD").strip() == g.BRANCH)
    g.git(root, "merge-base", "--is-ancestor", BASELINE, "HEAD")
    base_entries = {}
    for record in g.git_paths(g.git(root, "ls-tree", "-r", "-z", BASELINE)):
        meta, name = record.split("\t", 1)
        mode, kind, oid = meta.split()
        require(kind == "blob" and mode in {"100644", "100755"})
        base_entries[name] = oid
    require(set(base_entries) == set(g.ALLOWED))
    allowed = set(base_entries) | {PREFIX + n for n in NAMES}
    contents = {}
    for name in sorted(allowed):
        path = g.safe_path(root, name)
        require(path.is_file() and path.stat().st_size <= 1024 * 1024)
        contents[name] = g.read_text(root, name)
        g.scan_secrets(name, contents[name])
    originals = g.read_blobs(root, base_entries.values())
    for name, oid in base_entries.items():
        if name not in MUTABLE:
            require(contents[name] == originals[oid])
    require(contents["docs/CONSTRUCTION_LOG.md"].startswith(originals[base_entries["docs/CONSTRUCTION_LOG.md"]]))
    for name, expected in ENTRY_HASHES.items():
        require(g.sha256(contents[name]) == expected)
    entry = g.parse_json(contents[PREFIX + "entry-review.json"])
    g.exact(entry, {"schema_version": 1, "entrypoint": "PASS", "prompt_auditor": "PASS",
                   "agent_id": "01a1f31e-108d-7603-959f-604c54e690ea",
                   "read_only": True, "findings": [], "files": ENTRY_HASHES}, "entry")
    entries = {}
    for record in g.git_paths(g.git(root, "ls-files", "--stage", "-z")):
        meta, name = record.split("\t", 1)
        mode, oid, stage = meta.split()
        require(name in allowed and name not in entries and stage == "0" and mode in {"100644", "100755"})
        entries[name] = oid
    require(set(entries) == allowed)
    staged = g.read_blobs(root, entries.values())
    for name, oid in entries.items():
        require(staged[oid] == contents[name])
    candidate = set(g.git_paths(g.git(root, "diff", "--cached", "--ita-invisible-in-index",
                                     "--name-only", "-z", "HEAD")))
    head = set(g.git_paths(g.git(root, "ls-tree", "-r", "--name-only", "-z", "HEAD")))
    require(head <= allowed and allowed <= head | candidate)
    # Inspect even ignored files and empty linked directories, not just Git's index.
    directories = [root]
    while directories:
        for path in directories.pop().iterdir():
            if path.parent == root and path.name == ".git":
                continue
            name = path.relative_to(root).as_posix()
            g.safe_path(root, name)
            if path.is_dir():
                directories.append(path)
            else:
                require(name in allowed or g.harmless_cache(name))
    hashes = {name: g.sha256(text) for name, text in contents.items() if name not in ATTESTATIONS}
    snapshot = hashlib.sha256(canonical(hashes).encode()).hexdigest()
    return {"snapshot_id": snapshot, "hashes": hashes, "contents": contents}


def approvals(checked):
    snapshot, contents = checked["snapshot_id"], checked["contents"]
    validation = g.parse_json(contents[PREFIX + "validation.json"])
    g.exact(validation, {"snapshot_id": snapshot, "verdict": "PASS",
                        "commands": [{"command": c, "exit_code": 0} for c in COMMANDS]}, "validation")
    reviewers = []
    for name, role, verdict in (("review.json", "reviewer", "PASS"),
                                 ("release.json", "release_checker", "LOCAL_READY_FOR_SINGLE_CONNECTION")):
        value = g.parse_json(contents[PREFIX + name])
        g.keys(value, ("snapshot_id", "role", "verdict", "agent_id", "read_only", "findings", "repair_cycles"), role)
        for key, expected in {"snapshot_id": snapshot, "role": role, "verdict": verdict,
                              "read_only": True, "findings": [], "repair_cycles": 1}.items():
            g.exact(value[key], expected, key)
        require(type(value["agent_id"]) is str and value["agent_id"].strip() == value["agent_id"]
                and value["agent_id"] not in {"", "coordinator", "01a1f32f-752e-7d53-a38f-ff19b0c23586"})
        reviewers.append(value["agent_id"])
    require(reviewers[0] != reviewers[1])
    g.exact(g.parse_json(contents[PREFIX + "result.json"]), {"status": "NOT_RUN"}, "pending result")


def old_hashes():
    result = {}
    for name in OLD_JOURNALS:
        path = external_path(Path.home() / ".a-share-market-intelligence" / name)
        require(path.is_file() and path.stat().st_size <= 10 * 1024 * 1024)
        require(not any(Path(str(path) + s).exists() for s in ("-journal", "-wal", "-shm")))
        result[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def reserve(path, snapshot, before):
    path = external_path(path)
    require(path.parent.is_dir())
    # Exclusive creation survives a crash before SQLite setup: an existing file
    # is consumed forever, including zero-length or incomplete reservations.
    with path.open("xb") as stream:
        stream.flush()
        os.fsync(stream.fileno())
    with closing(sqlite3.connect(path)) as db:
        db.execute("PRAGMA synchronous=FULL")
        db.execute("CREATE TABLE records(kind TEXT PRIMARY KEY, metadata TEXT NOT NULL)")
        db.execute("INSERT INTO records VALUES ('reservation',?)", (canonical({
            "operation_id": OPERATION, "snapshot_id": snapshot, "reserved_requests": 1,
            "reserved_at": stamp(utc_now()), "old_hashes": before}),))
        db.commit()
    return quota_fingerprint(path)


def quota_fingerprint(path):
    path = external_path(path)
    require(path.is_file() and 0 < path.stat().st_size <= 65536)
    require(not any(Path(str(path) + s).exists() for s in ("-journal", "-wal", "-shm")))
    before = path.stat()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    after = path.stat()
    require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns))
    return (after.st_dev, after.st_ino, digest)


def collect(snapshot, before):
    result = {"operation_id": OPERATION, "snapshot_id": snapshot, "status": "UNKNOWN",
              "reason": "operation_unknown", "transport": None, "protocol": None,
              "old_journal_hashes_before": before, "old_journal_hashes_after": None,
              "old_journals_unchanged": None, "payload_persisted": False}
    try:
        with redirect_stdout(capture._Discard()), redirect_stderr(capture._Discard()):
            receipt, diagnostic = transport.run_recovery(phase="diagnostic", accept_response=lambda _: None)
        require(type(receipt) is transport.TransportReceipt)
        value = receipt.to_dict()
        safe = {k: v for k, v in value.items() if k in recovery.DIAGNOSTIC_TRANSPORT}
        safe["validated_responses"] = len(value["responses"])
        recovery._diagnostic_transport(safe)
        recovery.validate_diagnostic(diagnostic)
        after = old_hashes()
        unchanged = after == before
        clean = all(safe[k] is True for k in recovery.CLEANUP[:3])
        result.update(transport=safe, protocol=diagnostic, old_journal_hashes_after=after,
                      old_journals_unchanged=unchanged,
                      status="SUCCESS" if safe["status"] == "COMPLETE" and unchanged and clean else "FAILED",
                      reason=safe["reason"] if unchanged else "old_journal_changed")
    except BaseException:
        pass
    return result


def observe():
    require("CI" not in os.environ and "GITHUB_ACTIONS" not in os.environ)
    checked = check()
    approvals(checked)
    before = old_hashes()
    path = Path.home() / ".a-share-market-intelligence" / (OPERATION + ".sqlite")
    quota = reserve(path, checked["snapshot_id"], before)
    # Reservation is never refunded, including a concurrent source-tree change.
    current = check()
    require(current["snapshot_id"] == checked["snapshot_id"])
    approvals(current)
    require(quota_fingerprint(path) == quota)
    result = collect(checked["snapshot_id"], before)
    require(len(canonical(result).encode()) < 32768)
    require(quota_fingerprint(path) == quota)
    with closing(sqlite3.connect(external_path(path))) as db:
        db.execute("PRAGMA synchronous=FULL")
        db.execute("INSERT INTO records VALUES ('result',?)", (canonical(result),))
        db.commit()
    return result


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    try:
        if args == ["check"]:
            checked = check()
            print(canonical({"scope": "PASS", "snapshot_id": checked["snapshot_id"]}))
        elif args == ["observe", "--allow-network"]:
            print(canonical(observe()))
        else:
            raise ValueError("invalid_arguments")
        return 0
    except BaseException:
        print(canonical({"status": "BLOCKED_OR_UNKNOWN", "reason": "operator_operation_blocked",
                         "retry_allowed": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
