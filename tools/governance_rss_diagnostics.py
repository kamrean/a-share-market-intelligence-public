"""Check staged P4DIAG scope, snapshot and release evidence offline."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tomllib


ROOT = Path(__file__).resolve().parents[1]

# Load the adjacent working source without reading or writing a bytecode cache.
_prefix, _dont_write = sys.pycache_prefix, sys.dont_write_bytecode
sys.pycache_prefix, sys.dont_write_bytecode = str(Path(__file__).absolute()), True
try:
    _spec = importlib.util.spec_from_file_location(
        "_p4diag_ci_helpers", Path(__file__).with_name("governance_news_ci_repair.py"))
    _repair = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_repair)
finally:
    sys.pycache_prefix, sys.dont_write_bytecode = _prefix, _dont_write

g, n = _repair.g, _repair.n
require, exact = g.require, g.exact

PACKAGE = "P4DIAG"
BASE = "7a036af0e540ba1526b20330b18779e2cc71d421"
BRANCH = "stage7-rss-offline-diagnostics"
AUDITOR = "01a1fe00-7864-7f51-9100-ff7220944d47"
PLANNING_SNAPSHOT = "54c37ced0dfe9006a214930b1d33cf19277c1ba8f1b4a480593a27944c0969b0"
ORIGINAL_OBSERVATION_SHA256 = "5aa27a64f69fa9895a7de229eb94ec470aceecb9e868552bdf64aec52b25367b"
CONTRACT = "contracts/p4diag_offline_rss.toml"
PLANNING = (CONTRACT, "docs/P4DIAG_OFFLINE_RSS.md")
ENTRY = "docs/reviews/p4diag-entry.json"
VALIDATION = "docs/reviews/p4diag-validation.json"
REVIEW = "docs/reviews/p4diag-review.json"
RELEASE = "docs/reviews/p4diag-release.json"
RECEIPTS = (VALIDATION, REVIEW, RELEASE)
WORKFLOW = ".github/workflows/governance.yml"
COMMANDS = ("check", "snapshot", "gate")
COUNT_MINIMA = {"product_collected": 990, "product_passed": 0, "product_skipped": 0,
                "focused_diagnostics": 10, "focused_governance": 5, "historical_p4fedci": 10}


def _validate_plan(contents):
    digest, _ = g.snapshot_for_paths(contents, PLANNING)
    exact(digest, PLANNING_SNAPSHOT, "frozen planning snapshot")
    exact(g.parse_json(contents[ENTRY]), {
        "schema_version": 1, "package": PACKAGE, "role": "entry_and_prompt_auditor",
        "agent_id": AUDITOR, "read_only": True, "entry_verdict": "PASS",
        "prompt_verdict": "PASS", "planning_snapshot": digest, "findings": [],
    }, "entry and prompt receipt")
    return tomllib.loads(contents[CONTRACT])


def baseline_entries(root):
    entries = {}
    for record in g.git_paths(g.git(root, "ls-tree", "-r", "-z", BASE)):
        metadata, separator, name = record.partition("\t")
        fields = metadata.split()
        require(separator and len(fields) == 3 and fields[:2] == ["100644", "blob"]
                and name not in entries, "invalid PR17 merged tree entry")
        entries[name] = fields[2]
    require(entries, "empty PR17 merged tree")
    return entries


def _validate_workflow(root, baseline_blob, candidate):
    lines = g.git_raw(root, "cat-file", "blob", baseline_blob).decode("utf-8").splitlines(keepends=True)
    for command in COMMANDS:
        before = f"        run: python tools/governance_news_ci_repair.py {command} --ci\n"
        exact(lines.count(before), 1, "PR17 offline workflow command count")
        lines[lines.index(before)] = f"        run: python tools/governance_rss_diagnostics.py {command} --ci\n"
    exact(candidate, "".join(lines), "workflow: only the three offline command switches are allowed")


def check(root=ROOT, *, ci=False, complete_index=False):
    root = Path(root).resolve()
    contents, blobs = {}, {}

    def read(name):
        path = g.safe_path(root, name)
        require(path.is_file(), f"missing required file: {name}")
        require(os.name == "nt" or not path.stat().st_mode & stat.S_IXUSR,
                f"worktree: unexpected executable file: {name}")
        data = path.read_bytes()
        value = data.decode("utf-8")
        require(b"\r" not in data, f"candidate file must use LF bytes: {name}")
        g.scan_secrets(name, value)
        contents[name] = value
        blobs[name] = hashlib.sha1(f"blob {len(data)}\0".encode("ascii") + data,
                                   usedforsecurity=False).hexdigest()

    for name in (*PLANNING, ENTRY):
        read(name)
    plan = _validate_plan(contents)
    baseline = baseline_entries(root)
    mutable, additions = set(plan["mutable"]), set(plan["additions"])
    require(mutable <= baseline.keys() and not additions & baseline.keys(),
            "contract paths differ from the merged tree")
    exact(plan["attestations"], list(RECEIPTS), "contract attestations")
    allowed = baseline.keys() | additions
    protected = tuple(sorted(allowed - set(RECEIPTS)))
    for name in sorted(allowed - contents.keys()):
        if name in RECEIPTS and not complete_index and not g.safe_path(root, name).exists():
            continue
        read(name)

    changed = g.inspect_git(root, ci=ci, contents=contents, complete_index=complete_index,
                            baseline=BASE, branch=BRANCH, allowed=allowed, immutable_hashes={})
    require(set(changed) <= mutable | additions, "Git patch changes a path outside the contract scope")
    entries = g.read_index(root, allowed=allowed)
    exact(sorted(entries), sorted(blobs), "index: expected every present candidate file staged")
    # The shared inspector normalizes text; raw Git blob IDs bind exact UTF-8 LF bytes.
    for name, blob in blobs.items():
        exact(entries[name], blob, f"index/worktree bytes differ: {name}")
    for name in sorted(baseline.keys() - mutable):
        exact(blobs[name], baseline[name], f"immutable PR17 bytes changed: {name}")
    for row in g.git_paths(g.git(root, "ls-files", "--stage", "-z")):
        require(row.startswith("100644 "), "index: unexpected file mode")

    exact(g.sha256(contents[n.OBSERVATION]), ORIGINAL_OBSERVATION_SHA256, "original observation SHA-256")
    _validate_workflow(root, baseline[WORKFLOW], contents[WORKFLOW])
    staged = sorted(name for name in protected if entries[name] != baseline.get(name))
    return contents, protected, staged


def snapshot(root=ROOT, *, ci=False):
    contents, protected, staged = check(root, ci=ci)
    snapshot_id, hashes = g.snapshot_for_paths(contents, protected)
    return {
        "schema_version": 1, "package": PACKAGE, "base": BASE, "branch": BRANCH,
        "snapshot_id": snapshot_id, "planning_snapshot": PLANNING_SNAPSHOT,
        "original_observation_sha256": ORIGINAL_OBSERVATION_SHA256,
        "hash_algorithm": g.ALGORITHM, "protected_paths": list(protected),
        "protected_file_hashes": hashes, "staged_paths": staged, "receipt_paths": list(RECEIPTS),
    }


def validate_chain(contents, snapshot_id):
    def receipt(path, role, verdict, *, variable, **expected):
        record = n._parse_receipt(contents, path)
        fields = {"schema_version": 1, "package": PACKAGE, "snapshot_id": snapshot_id,
                  "role": role, "verdict": verdict, **expected}
        g.keys(record, (*fields, *variable), role)
        n._expect_fields(record, fields, role)
        return record

    validation = receipt(VALIDATION, "builder_validation", "PASS", offline=True,
                         new_source_requests=0, variable=("agents", *COUNT_MINIMA))
    for name, minimum in COUNT_MINIMA.items():
        count = validation[name]
        require(type(count) is int and count >= minimum, f"validation: {name} must be >= {minimum}")
    require(validation["product_skipped"] <= 1, "validation: at most one product skip")
    exact(validation["product_collected"], validation["product_passed"] + validation["product_skipped"],
          "validation product count accounting")
    builders = validation["agents"]
    require(type(builders) is list and len(builders) == 2, "validation: expected two builder agents")
    review = receipt(REVIEW, "reviewer", "PASS", variable=("agent_id",), read_only=True, findings=[],
                     validation_sha256=g.sha256(contents[VALIDATION]))
    release = receipt(RELEASE, "release_checker", g.LOCAL_RELEASE, variable=("agent_id",), read_only=True,
                      review_sha256=g.sha256(contents[REVIEW]),
                      original_observation_sha256=ORIGINAL_OBSERVATION_SHA256,
                      human_merge_required=True, remote_ci="NOT_RUN_AT_SNAPSHOT")
    actors = tuple(map(n._agent_id, (AUDITOR, *builders, review["agent_id"], release["agent_id"])))
    require(len(set(actors)) == len(actors), "auditor, builders, reviewer and release must be independent")


def gate(root=ROOT, *, ci=False):
    contents, protected, _ = check(root, ci=ci, complete_index=True)
    snapshot_id, _ = g.snapshot_for_paths(contents, protected)
    validate_chain(contents, snapshot_id)
    return g.LOCAL_RELEASE


def main(argv=None, *, root=ROOT):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in COMMANDS:
        command = commands.add_parser(name, allow_abbrev=False)
        command.add_argument("--ci", action="store_true", help="skip only the local branch check")
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            check(root, ci=args.ci)
            print("PASS (P4DIAG offline scope, planning and history)")
        elif args.command == "snapshot":
            print(json.dumps(snapshot(root, ci=args.ci), indent=2, sort_keys=True))
        else:
            print(gate(root, ci=args.ci))
        return 0
    except (ValueError, OSError, RuntimeError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(f"BLOCK: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
