"""P4CORE offline candidate scope, snapshot and local release checks."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tomllib
import uuid


ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "_p4core_governance_helpers", Path(__file__).with_name("governance_mvp.py"))
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)
g = m.g
Block, require, exact = g.Block, g.require, g.exact

PACKAGE = "P4CORE"
BASE = "37a73f7ff3c9d3f7fd4600cc460430c035c8213a"
BRANCH = "stage1-a-share-core-acceptance"
CONTRACT = "contracts/p4core_a_share_acceptance.toml"
POLICY, STATE = m.POLICY, m.STATE
ENTRY = "docs/reviews/p4core-entry.json"
VALIDATION = "docs/reviews/p4core-validation.json"
REVIEW = "docs/reviews/p4core-review.json"
RELEASE = "docs/reviews/p4core-release.json"
RECEIPTS = (VALIDATION, REVIEW, RELEASE)
PLANNING_PATHS = ("AGENTS.md", "START_CONSTRUCTION_PROMPT.md", "BLUEPRINT.md",
                  CONTRACT, POLICY, STATE)
PLANNING_SNAPSHOT = "fcbd7076504667edba23522d5d2572f55b751ee7fda953749ed7c5136c2bdcd7"
AUDITOR = "01a1fcef-cdae-7252-ae28-a768205e8f9c"
CLOSED_PROBE = {**m.CLOSED_PROBE, "contract": CONTRACT}
CLOSED_COMMANDS = m.CLOSED_COMMANDS
VALIDATION_COMMANDS = (
    "product suite", "P4CORE governance", "historical P4MVP governance",
    "full governance suite", "offline preflight", "scope check", "patch whitespace",
)
# PR15's 936 product tests plus 11 core tests, with at most one Windows skip.
VALIDATION_COUNT_MINIMA = {
    "product_collected": 947, "product_passed": 946, "product_windows_symlink_skip": 0,
    "core_governance": 12, "historical_mvp_governance": 13, "governance_collected": 263,
}


def validate_planning(contents):
    digest, _ = g.snapshot_for_paths(contents, PLANNING_PATHS)
    exact(digest, PLANNING_SNAPSHOT, "frozen planning snapshot")
    contract = tomllib.loads(contents[CONTRACT])
    for name, expected in {
        "schema_version": 1, "package": PACKAGE, "branch": BRANCH, "base": BASE,
        "tier": "D", "allow_live_requests": False, "human_merge_required": True,
        "auto_merge_allowed": False, "repair_limit": 1,
        "planning_paths": list(PLANNING_PATHS), "receipt_paths": list(RECEIPTS),
    }.items():
        exact(contract[name], expected, f"contract.{name}")
    exact(g.parse_json(contents[ENTRY]), {
        "schema_version": 1, "package": PACKAGE, "role": "entry_and_prompt_auditor",
        "agent_id": AUDITOR, "read_only": True, "entry_verdict": "PASS",
        "prompt_verdict": "PASS", "scope": "offline_core_only",
        "planning_snapshot": digest, "findings": [],
    }, "entry and prompt receipt")
    policy = tomllib.loads(contents[POLICY])
    state = g.parse_json(contents[STATE])
    exact(policy["permissions"], g.PERMISSIONS, "policy.permissions")
    exact(policy["repository"], g.REPOSITORY, "policy.repository")
    exact(policy["limited_source_probe"], CLOSED_PROBE, "policy.limited_source_probe")
    exact(state["limited_source_probe"], CLOSED_PROBE, "state.limited_source_probe")
    exact(state["live_integration"]["permissions_enabled"], False, "state live permissions")
    exact(state["historical_checkpoints"]["old_allowances_reopened"], False, "old allowances")
    exact(state["builder_agents"], ["coordinator"], "state coordinator")
    return contract, state


def baseline_entries(root):
    entries = {}
    for record in g.git_paths(g.git(root, "ls-tree", "-r", "-z", BASE)):
        metadata, separator, name = record.partition("\t")
        fields = metadata.split()
        require(separator and len(fields) == 3 and fields[0] in ("100644", "100755")
                and fields[1] == "blob" and name not in entries, "invalid merged tree entry")
        entries[name] = fields[2]
    require(entries, "empty merged tree")
    return entries


def check(root=ROOT, *, ci=False, complete_index=False):
    root = Path(root).resolve()
    contents = {name: g.read_text(root, name) for name in (*PLANNING_PATHS, ENTRY)}
    contract, state = validate_planning(contents)
    merged = baseline_entries(root)
    mutable, new = set(contract["mutable_paths"]), set(contract["new_paths"])
    require(mutable <= merged.keys() and not new & merged.keys() and set(RECEIPTS) <= new,
            "contract paths differ from the merged tree")
    allowed = tuple(sorted(merged.keys() | new))
    protected = tuple(name for name in allowed if name not in RECEIPTS)
    for name in allowed:
        path = g.safe_path(root, name)
        if name in RECEIPTS and not path.exists() and not complete_index:
            continue
        if name not in contents:
            contents[name] = g.read_text(root, name)
        g.scan_secrets(name, contents[name])
    immutable = merged.keys() - mutable
    changed = g.inspect_git(root, ci=ci, contents=contents, complete_index=complete_index,
                            baseline=BASE, branch=BRANCH, allowed=allowed, immutable_hashes={})
    require(not immutable.intersection(changed), "Git patch changes an immutable merged path")
    for name in sorted(immutable):
        # Use the pinned Git blob ID without applying newline or attribute filters.
        data = g.safe_path(root, name).read_bytes()
        blob_id = hashlib.sha1(f"blob {len(data)}\0".encode("ascii") + data,
                               usedforsecurity=False).hexdigest()
        require(blob_id == merged[name], f"immutable merged bytes changed: {name}")
    return state, contents, protected, changed


def make_snapshot(checked):
    _state, contents, protected, changed = checked
    snapshot_id, hashes = g.snapshot_for_paths(contents, protected)
    return {
        "schema_version": 1, "package": PACKAGE, "base": BASE, "branch": BRANCH,
        "snapshot_id": snapshot_id, "planning_snapshot": PLANNING_SNAPSHOT,
        "hash_algorithm": g.ALGORITHM, "protected_file_hashes": hashes,
        "changed_paths": sorted(set(changed) - set(RECEIPTS)), "receipt_paths": list(RECEIPTS),
    }


def snapshot(root=ROOT, *, ci=False):
    return make_snapshot(check(root, ci=ci))


def validate_results(results):
    """Check receipt coverage and count accounting without running its claims."""
    g.keys(results, ("commands", "counts", "builder_agents"), "validation.results")
    commands, counts = results["commands"], results["counts"]
    require(type(commands) is list, "validation: commands must be a list")
    names = []
    for command in commands:
        g.keys(command, ("command", "exit_code"), "validation command")
        g.nonempty(command["command"], "validation command text")
        exact(command["exit_code"], 0, "validation command exit code")
        names.append(command["command"])
    exact(sorted(names), sorted(VALIDATION_COMMANDS), "validation required command names")
    g.keys(counts, VALIDATION_COUNT_MINIMA, "validation counts")
    for suite, minimum in VALIDATION_COUNT_MINIMA.items():
        count = counts[suite]
        require(type(count) is int and count >= minimum,
                f"validation counts: {suite} must be an integer >= {minimum}")
    skipped = counts["product_windows_symlink_skip"]
    require(skipped <= 1, "validation counts: at most one Windows symlink skip")
    exact(counts["product_collected"], counts["product_passed"] + skipped,
          "validation product count accounting")
    builders = results["builder_agents"]
    g.string_list(builders, "validation builder agents")
    exact(len(builders), 2, "validation builder agent count")
    for actor in builders:
        try:
            parsed = uuid.UUID(actor)
        except ValueError:
            raise Block("validation: invalid native builder agent ID") from None
        require(parsed.int != 0 and str(parsed) == actor, "validation: invalid native builder agent ID")
    return builders


def validate_receipts(state, contents, snapshot_id):
    def record(path, role, verdict, **expected):
        receipt = g.parse_json(contents[path])
        fields = {"schema_version": 1, "package": PACKAGE, "role": role,
                  "verdict": verdict, "snapshot_id": snapshot_id, **expected}
        variable = "results" if path == VALIDATION else "agent_id"
        g.keys(receipt, (*fields, variable), role)
        for key, value in fields.items():
            exact(receipt[key], value, f"{role}.{key}")
        return receipt[variable]

    builders = validate_results(record(VALIDATION, "builder_validation", "PASS", live_requests=0))
    review_agent = record(REVIEW, "reviewer", "PASS", read_only=True, findings=[],
                          validation_sha256=g.sha256(g.normalize_text(contents[VALIDATION])))
    release_agent = record(RELEASE, "release_checker", g.LOCAL_RELEASE, read_only=True,
                           review_sha256=g.sha256(g.normalize_text(contents[REVIEW])),
                           remote_ci="NOT_RUN_AT_SNAPSHOT", human_merge_required=True)
    actors = {actor.casefold() for actor in (*state["builder_agents"], AUDITOR)}
    require(actors.isdisjoint(builders), "builder and entry roles must be independent")
    actors.update(builders)
    for actor in (review_agent, release_agent):
        g.nonempty(actor, "receipt agent ID")
        require(actor == actor.strip() and actor.casefold() not in actors,
                "receipt roles must be independent")
        actors.add(actor.casefold())


def gate(root=ROOT, *, ci=False):
    checked = check(root, ci=ci, complete_index=True)
    state, contents, _protected, _changed = checked
    validate_receipts(state, contents, make_snapshot(checked)["snapshot_id"])
    return g.LOCAL_RELEASE


def main(argv=None, *, root=ROOT):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("check", "snapshot", "gate", *CLOSED_COMMANDS):
        commands.add_parser(name).add_argument(
            "--ci", action="store_true", help="skip only the local branch identity check")
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            check(root, ci=args.ci)
            print("PASS (P4CORE offline scope and planning)")
        elif args.command == "snapshot":
            print(json.dumps(snapshot(root, ci=args.ci), indent=2, sort_keys=True))
        elif args.command == "gate":
            print(gate(root, ci=args.ci))
        else:
            m.runtime_gate(args.command)
        return 0
    except (ValueError, OSError, RuntimeError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(f"BLOCK: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
