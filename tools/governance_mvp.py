"""P4MVP offline candidate scope, snapshot and local release checks."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tomllib
import uuid


ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "_p4mvp_governance_helpers", Path(__file__).with_name("governance.py"))
g = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(g)
Block, require, exact = g.Block, g.require, g.exact

PACKAGE = "P4MVP"
BASE = "652d5c6effcf22dc1f38b2d2a3c5ea86c20291ec"
BRANCH = "stage7-offline-mvp-readiness"
CONTRACT = "contracts/p4mvp_offline_readiness.toml"
POLICY = "governance/policy.toml"
STATE = "project-state/state.json"
ENTRY = "docs/reviews/p4mvp-entry.json"
VALIDATION, REVIEW, RELEASE = (
    "docs/reviews/p4mvp-validation.json",
    "docs/reviews/p4mvp-review.json",
    "docs/reviews/p4mvp-release.json",
)
RECEIPTS = (REVIEW, VALIDATION, RELEASE)
PLANNING_PATHS = ("AGENTS.md", "START_CONSTRUCTION_PROMPT.md", "BLUEPRINT.md",
                  CONTRACT, POLICY, STATE)
PLANNING_SNAPSHOT = "6281e4c5e963b0184345bca3764074826185f3c2db50f215459abcb9b126ade1"
AUDITOR = "01a1fa26-ba2b-7242-a748-a82cc1b7537e"
CLOSED_PROBE = {
    "contract": CONTRACT, "mode": "closed", "upstream_rights": "unknown",
    "normal_source_operations_enabled": False, "historical_gates_closed": True,
    "ci_network_gate_allowed": False,
}
CLOSED_COMMANDS = (
    "runtime-gate", "historical-gate", "tencent-gate", "diagnostic-gate",
    "verification-gate", "probe-gate", "preparation-gate", "nbs-gate", "market-gate",
)
VALIDATION_COMMANDS = (
    "python -B -m pip check",
    "python -B -m market_intelligence.mvp demo --json",
    "python -B -m unittest discover -s tests/product -p test_*.py -q",
    "python -B -m unittest discover -s tests/governance -p test_governance_mvp.py",
    "python -B -m unittest discover -s tests/governance -p test_governance_p4t.py",
    "python -B -m unittest discover -s tests/product -p test_tencent_*.py (socket-denied focused suite)",
    "python -B tools/governance_mvp.py check",
    "python -B tools/governance_mvp.py snapshot",
    "offline runtime/legacy gate CI-denial assertions",
    f"git diff --cached --check {BASE}",
)
# Floors from offline suite discovery at the repair, with one eligible Windows
# symlink skip. The receipt retains the actual run counts, not these substitutes.
VALIDATION_COUNT_MINIMA = {
    "product_collected": 936,
    "product_passed": 935,
    "product_windows_symlink_skip": 0,
    "focused_tencent": 74,
    "focused_demo_workflow_scenario": 84,
    "p4mvp_governance": 13,
    "historical_p4t_governance": 41,
    "ci_denial_assertions": 4,
}


def validate_planning(contents):
    digest, _ = g.snapshot_for_paths(contents, PLANNING_PATHS)
    exact(digest, PLANNING_SNAPSHOT, "frozen planning snapshot")
    contract = tomllib.loads(contents[CONTRACT])
    for name, expected in {
        "schema_version": 1, "package": PACKAGE, "branch": BRANCH, "base": BASE,
        "tier": "D", "allow_live_requests": False, "human_merge_required": True,
        "auto_merge_allowed": False, "planning_paths": list(PLANNING_PATHS),
        "receipt_paths": list(RECEIPTS),
    }.items():
        exact(contract[name], expected, f"contract.{name}")
    exact(g.parse_json(contents[ENTRY]), {
        "schema_version": 1, "package": PACKAGE, "role": "entry_and_prompt_auditor",
        "agent_id": AUDITOR, "read_only": True, "entry_verdict": "PASS",
        "prompt_verdict": "PASS", "scope": "offline_candidate_only",
        "planning_snapshot": digest, "findings": [],
        "initial_block": "Clarified one consolidated review repair cycle and labeled the prior P4T entry historical/closed.",
    }, "entry and prompt receipt")
    policy = tomllib.loads(contents[POLICY])
    state = g.parse_json(contents[STATE])
    exact(policy["permissions"], g.PERMISSIONS, "policy.permissions")
    exact(policy["repository"], g.REPOSITORY, "policy.repository")
    exact(policy["limited_source_probe"], CLOSED_PROBE, "policy.limited_source_probe")
    exact(state["limited_source_probe"], CLOSED_PROBE, "state.limited_source_probe")
    exact(state["live_integration"]["permissions_enabled"], False, "state live permissions")
    exact(state["historical_checkpoints"]["old_allowances_reopened"], False, "old allowances")
    # The audited planning digest also pins the entire stage registry, lifecycle
    # state, exact path lists and policy; no receipt may modify that authority.
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
    blobs = g.read_blobs(root, (merged[name] for name in immutable))
    immutable_hashes = {name: g.sha256(blobs[merged[name]]) for name in immutable}
    changed = g.inspect_git(root, ci=ci, contents=contents, complete_index=complete_index,
                            baseline=BASE, branch=BRANCH, allowed=allowed,
                            immutable_hashes=immutable_hashes)
    require(not immutable.intersection(changed), "Git patch changes an immutable merged path")
    for name in sorted(immutable):
        # The pinned tree uses LF (including receipt blobs); do not accept edits
        # that disappear from the normalized snapshot or Git's path diff.
        require(g.safe_path(root, name).read_bytes() == blobs[merged[name]].encode("utf-8"),
                f"immutable merged bytes changed: {name}")
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
    """Check the receipt's required coverage and count accounting without running it."""
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
    passed = counts["product_passed"]
    exact(counts["product_collected"], passed + skipped, "validation product count accounting")
    require(counts["focused_tencent"] + counts["focused_demo_workflow_scenario"] <= passed,
            "validation counts: focused suites exceed product passes")
    builders = results["builder_agents"]
    g.string_list(builders, "validation builder agents")
    exact(len(builders), 3, "validation builder agent count")
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


def runtime_gate(command):
    raise Block(f"{command}: historical and runtime capabilities are closed; "
                "--ci is not runtime authorization")


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
            print("PASS (P4MVP offline scope and planning)")
        elif args.command == "snapshot":
            print(json.dumps(snapshot(root, ci=args.ci), indent=2, sort_keys=True))
        elif args.command == "gate":
            print(gate(root, ci=args.ci))
        else:
            runtime_gate(args.command)
        return 0
    except (ValueError, OSError, RuntimeError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(f"BLOCK: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
