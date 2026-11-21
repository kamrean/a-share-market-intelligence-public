"""Offline P4FEDCI scope, historical evidence and local release checks."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tomllib


ROOT = Path(__file__).parent.parent.absolute()

# Read the current, adjacent governance source, never a cached or archived copy.
_prefix, _dont_write = sys.pycache_prefix, sys.dont_write_bytecode
sys.pycache_prefix, sys.dont_write_bytecode = str(Path(__file__).absolute()), True
try:
    _spec = importlib.util.spec_from_file_location(
        "_p4fed_ci_original", Path(__file__).with_name("governance_news.py"))
    n = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(n)
finally:
    sys.pycache_prefix, sys.dont_write_bytecode = _prefix, _dont_write

g = n.g
require, exact = g.require, g.exact

PACKAGE = "P4FEDCI"
CONTRACT = "contracts/p4fed_post_probe_ci_repair.toml"
PLANNING_SHA256 = "5fac625ba2ace6cbc632a906e2c25b1d6096bc232719ebe6e1e041e4e360b4eb"
ENTRY = "docs/reviews/p4fed-ci-entry.json"
VALIDATION = "docs/reviews/p4fed-ci-validation.json"
REVIEW = "docs/reviews/p4fed-ci-review.json"
RELEASE = "docs/reviews/p4fed-ci-release.json"
RECEIPTS = (VALIDATION, REVIEW, RELEASE)
COMMANDS = ("check", "snapshot", "gate")
REPAIR_LINES = {
    ".github/workflows/governance.yml": tuple(
        (f"        run: python tools/governance_news.py {command} --ci\n",
         f"        run: python tools/governance_news_ci_repair.py {command} --ci\n")
        for command in COMMANDS
    ),
    "tests/product/test_primary_news_probe.py": (
        ("            self.assertEqual(path, self.path)\n",
         "            self.assertEqual(path, self.path.resolve(strict=True))\n"),
    ),
}
COUNT_MINIMA = {"product_collected": 980, "product_passed": 979, "product_skipped": 0,
                "focused_probe_tests": 27, "repair_governance_tests": 5}


def _validate_plan(contents):
    exact(g.sha256(contents[CONTRACT]), PLANNING_SHA256, "repair planning SHA-256")
    exact(g.parse_json(contents[ENTRY]), {
        "schema_version": 1, "package": PACKAGE, "role": "entry_and_prompt_auditor",
        "agent_id": n.AUDITOR, "read_only": True, "entry_verdict": "PASS",
        "prompt_verdict": "PASS", "planning_sha256": PLANNING_SHA256, "findings": [],
    }, "repair entry and prompt receipt")
    return tomllib.loads(contents[CONTRACT])


def _reconstruct(contents, original_paths):
    original = {name: contents[name] for name in original_paths}
    for name, changes in REPAIR_LINES.items():
        lines = original[name].splitlines(keepends=True)
        for before, after in changes:
            exact(lines.count(after), 1, f"exact repair line in {name}")
            lines[lines.index(after)] = before
        original[name] = "".join(lines)
    return original


def _validate_history(root, contents, original_paths, plan):
    original = _reconstruct(contents, original_paths)
    original_snapshot, _ = g.snapshot_for_paths(original, set(original_paths) - set(n.RECEIPTS))
    exact(original_snapshot, plan["original_snapshot"], "reconstructed original snapshot")
    hashes = plan["immutable_receipt_hashes"]
    for name in n.RECEIPTS:
        exact(g.sha256(original[name]), hashes[name], f"historical receipt SHA-256: {name}")
    actor = n.validate_chain(original, original_snapshot)
    n._receipt(original, n.RELEASE, original_snapshot, "release_checker", g.LOCAL_RELEASE,
               read_only=True, agent_id=actor, preprobe_sha256=hashes[n.PRE_RELEASE],
               observation_sha256=hashes[n.OBSERVATION], human_merge_required=True,
               remote_ci="NOT_RUN_AT_SNAPSHOT")
    observation = n._parse_receipt(original, n.OBSERVATION)
    with n._probe_module(root) as probe:
        probe.validate_receipt(observation, original_snapshot, terminal=True)
    exact(observation["status"], "FAILED", "original observation status")
    n._expect_fields(observation["observation"], {
        "error": "invalid_response", "dispatch_attempts": 1, "counts": None,
    }, "original observation")


def check(root=ROOT, *, ci=False, complete_index=False):
    root = Path(root).resolve()
    contents, blobs = {}, {}

    def read(name):
        path = g.safe_path(root, name)
        require(path.is_file(), f"missing required file: {name}")
        data = path.read_bytes()
        value = data.decode("utf-8")
        require("\r" not in value, f"candidate file must use LF bytes: {name}")
        g.scan_secrets(name, value)
        contents[name] = value
        blobs[name] = hashlib.sha1(f"blob {len(data)}\0".encode("ascii") + data,
                                   usedforsecurity=False).hexdigest()

    for name in (CONTRACT, ENTRY, *n.PLANNING_PATHS, n.ENTRY):
        read(name)
    plan = _validate_plan(contents)
    original_contract = n.validate_planning(contents)
    baseline = n.baseline_entries(root)
    mutable, additions = set(original_contract["mutable"]), set(original_contract["additions"])
    original_paths = baseline.keys() | additions
    supplement = set(plan["additions"])
    require(mutable <= baseline.keys() and not additions & baseline.keys()
            and {*n.RECEIPTS, n.ENTRY} <= additions
            and not supplement & original_paths, "contract paths differ from the merged tree")
    allowed = tuple(sorted(original_paths | supplement))
    protected = tuple(name for name in allowed if name not in RECEIPTS)
    for name in allowed:
        if name in RECEIPTS and not complete_index and not g.safe_path(root, name).exists():
            continue
        if name not in contents:
            read(name)

    changed = g.inspect_git(root, ci=ci, contents=contents, complete_index=complete_index,
                            baseline=n.BASE, branch=n.BRANCH, allowed=allowed, immutable_hashes={})
    changed = [name for name in changed if not g.harmless_cache(name, allowed=allowed)]
    immutable = baseline.keys() - mutable
    require(not immutable.intersection(changed), "Git patch changes an immutable merged path")
    for name in sorted(immutable):
        exact(blobs[name], baseline[name], f"immutable merged bytes changed: {name}")

    # The shared inspector compares normalized text; the candidate requires exact LF blobs.
    entries = g.read_index(root, allowed=allowed)
    exact(sorted(entries), sorted(blobs), "index: expected every present candidate file staged")
    for name, blob in blobs.items():
        exact(entries[name], blob, f"index/worktree bytes differ: {name}")
    # PR16 and the original PR17 tree use non-executable regular files throughout.
    for row in g.git_paths(g.git(root, "ls-files", "--stage", "-z")):
        require(row.startswith("100644 "), "index: unexpected file mode")

    _validate_history(root, contents, original_paths, plan)
    return contents, protected, changed


def make_snapshot(checked):
    contents, protected, changed = checked
    snapshot_id, hashes = g.snapshot_for_paths(contents, protected)
    return {
        "schema_version": 1, "package": PACKAGE, "base": n.BASE, "branch": n.BRANCH,
        "snapshot_id": snapshot_id, "planning_sha256": PLANNING_SHA256,
        "original_snapshot": tomllib.loads(contents[CONTRACT])["original_snapshot"],
        "original_observation_sha256": g.sha256(contents[n.OBSERVATION]),
        "hash_algorithm": g.ALGORITHM, "protected_paths": list(protected),
        "protected_file_hashes": hashes,
        "changed_paths": sorted(set(changed) - set(RECEIPTS)), "receipt_paths": list(RECEIPTS),
    }


def snapshot(root=ROOT, *, ci=False):
    return make_snapshot(check(root, ci=ci))


def validate_chain(contents, snapshot_id):
    def receipt(path, role, verdict, *, variable=(), **expected):
        record = n._parse_receipt(contents, path)
        fields = {"schema_version": 1, "package": PACKAGE, "snapshot_id": snapshot_id,
                  "role": role, "verdict": verdict, **expected}
        g.keys(record, (*fields, "agent_id", *variable), role)
        n._expect_fields(record, fields, role)
        return record

    validation = receipt(VALIDATION, "builder_validation", "PASS", offline=True,
                         new_source_requests=0, short_path_repro="PASS", variable=COUNT_MINIMA)
    for name, minimum in COUNT_MINIMA.items():
        count = validation[name]
        require(type(count) is int and count >= minimum, f"validation: {name} must be >= {minimum}")
    require(validation["product_skipped"] <= 1, "validation: at most one product skip")
    exact(validation["product_collected"], validation["product_passed"] + validation["product_skipped"],
          "validation product count accounting")
    review = receipt(REVIEW, "reviewer", "PASS", read_only=True, findings=[],
                     validation_sha256=g.sha256(contents[VALIDATION]))
    release = receipt(RELEASE, "release_checker", g.LOCAL_RELEASE, read_only=True,
                      review_sha256=g.sha256(contents[REVIEW]),
                      original_observation_sha256=g.sha256(contents[n.OBSERVATION]),
                      human_merge_required=True, remote_ci="NOT_RUN_AT_SNAPSHOT")
    actors = (n._agent_id(n.AUDITOR), *(n._agent_id(record["agent_id"])
                                       for record in (validation, review, release)))
    require(len(set(actors)) == len(actors), "auditor, builder, reviewer and release must be independent")


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
            print("PASS (P4FEDCI scope, planning and history)")
        elif args.command == "snapshot":
            print(json.dumps(snapshot(root, ci=args.ci), indent=2, sort_keys=True))
        else:
            print(gate(root, ci=args.ci))
        return 0
    except (ValueError, OSError, RuntimeError, KeyError, TypeError, ImportError,
            subprocess.SubprocessError) as error:
        print(f"BLOCK: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
