"""P4FED staged scope, one-shot authorization and offline release checks."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tomllib
import uuid


CI_REFUSAL = "--ci is not runtime authorization"


def _in_ci(ci):
    return ci or "CI" in os.environ or "GITHUB_ACTIONS" in os.environ


def _parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("check", "snapshot", "preprobe", "observe", "gate"):
        command = commands.add_parser(name, allow_abbrev=False)
        command.add_argument("--ci", action="store_true", help="use offline CI checks only")
        if name == "observe":
            command.add_argument("--allow-network", action="store_true")
    return parser.parse_args(argv)


def _refuse_runtime(args):
    if args.command in ("observe", "preprobe") and _in_ci(args.ci):
        print(f"BLOCK: {CI_REFUSAL}", file=sys.stderr)
    elif args.command == "observe" and not args.allow_network:
        print("BLOCK", file=sys.stderr)
    else:
        return False
    return True


if __name__ == "__main__":
    # Deny runtime requests before importing even the local governance helpers.
    _cli_args = _parse_args(None)
    if _refuse_runtime(_cli_args):
        raise SystemExit(1)


ROOT = Path(__file__).parent.parent.absolute()


@contextmanager
def _source_imports():
    # Point the cache prefix at a regular file: reviewed source, never cached code.
    prefix, dont_write = sys.pycache_prefix, sys.dont_write_bytecode
    sys.pycache_prefix, sys.dont_write_bytecode = str(Path(__file__).absolute()), True
    try:
        yield
    finally:
        sys.pycache_prefix, sys.dont_write_bytecode = prefix, dont_write


try:
    with _source_imports():
        _spec = importlib.util.spec_from_file_location(
            "_p4fed_governance_helpers", Path(__file__).with_name("governance_core.py"))
        c = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(c)
except (Exception, KeyboardInterrupt, SystemExit):
    if __name__ == "__main__" and _cli_args.command == "observe":
        print("BLOCK", file=sys.stderr)
        raise SystemExit(1) from None
    raise


g = c.g
Block, require, exact = g.Block, g.require, g.exact

PACKAGE = "P4FED"
BASE = "39f89d78987cca0f34d80dea8072451100b44ce5"
BRANCH = "stage7-live-integration"
CONTRACT = "contracts/p4fed_primary_news_verification.toml"
POLICY, STATE = c.POLICY, c.STATE
ENTRY = "docs/reviews/p4fed-entry.json"
VALIDATION = "docs/reviews/p4fed-validation.json"
REVIEW = "docs/reviews/p4fed-review.json"
PRE_RELEASE = "docs/reviews/p4fed-preprobe-release.json"
RELEASE = "docs/reviews/p4fed-release.json"
OBSERVATION = "docs/evidence/p4fed-observation.json"
RECEIPTS = (VALIDATION, REVIEW, PRE_RELEASE, RELEASE, OBSERVATION)
PRODUCT = "market_intelligence/primary_news_probe.py"
PLANNING_PATHS = ("AGENTS.md", "START_CONSTRUCTION_PROMPT.md", "BLUEPRINT.md",
                  CONTRACT, "docs/P4_LIVE_SOURCE_READINESS.md", POLICY, STATE)
PLANNING_SNAPSHOT = "b9df5602487eb0d625a41118c5b9cd813da205b84c71cd81c81ab72f923667d9"
AUDITOR = "01a1fe00-7864-7f51-9100-ff7220944d47"
CLOSED_PROBE = {**c.CLOSED_PROBE, "contract": CONTRACT}
OPERATION_ID = "p4fed-board-rss-20261121-v1"
PREPROBE_READY = "PREPROBE_READY"
COUNT_MINIMA = {"product_collected": 953, "product_passed": 952, "product_skipped": 0,
                "focused_product": 5, "focused_governance": 5, "historical_core": 12}


def _expect_fields(record, expected, label):
    for key, value in expected.items():
        exact(record.get(key), value, f"{label}.{key}")


def validate_planning(contents):
    digest, _ = g.snapshot_for_paths(contents, PLANNING_PATHS)
    exact(digest, PLANNING_SNAPSHOT, "frozen planning snapshot")
    contract = tomllib.loads(contents[CONTRACT])
    _expect_fields(contract, {
        "schema_version": 1, "package": PACKAGE, "base": BASE, "branch": BRANCH,
        "tier": "D", "human_merge_required": True, "auto_merge_allowed": False,
        "repair_limit": 1, "planning": list(PLANNING_PATHS), "attestations": list(RECEIPTS),
    }, "contract")
    _expect_fields(contract["operation"], {
        "id": OPERATION_ID, "source": "fed_press", "method": "GET",
        "endpoint": "https://www.federalreserve.gov/feeds/press_all.xml", "max_attempts": 1,
        "retry": False, "redirect": False, "paid": False, "requires_registration": False,
        "requires_key": False, "persist_payload": False,
    }, "contract.operation")
    exact(g.parse_json(contents[ENTRY]), {
        "schema_version": 1, "package": PACKAGE, "role": "entry_and_prompt_auditor",
        "agent_id": AUDITOR, "read_only": True, "entry_verdict": "PASS",
        "prompt_verdict": "PASS", "planning_snapshot": digest, "findings": [],
    }, "entry and prompt receipt")
    policy, state = tomllib.loads(contents[POLICY]), g.parse_json(contents[STATE])
    _expect_fields(policy, {
        "active_package": PACKAGE, "merge_tier": "D", "human_merge_required": True,
        "auto_merge_allowed": False, "direct_merge_allowed": False,
        "admin_bypass_allowed": False, "remote_creation_authorized": False,
        "permissions": g.PERMISSIONS, "repository": g.REPOSITORY,
        "limited_source_probe": CLOSED_PROBE,
        "primary_news_verification": {
            "operation_id": OPERATION_ID, "max_attempts": 1,
            "requires_exact_preprobe_release": True, "allows_normal_collection": False,
            "sanitized_result_path": OBSERVATION,
        },
    }, "policy")
    _expect_fields(state, {
        "active_package": PACKAGE, "active_contract": CONTRACT, "baseline": BASE,
        "branch": BRANCH, "builder_agents": ["coordinator"], "limited_source_probe": CLOSED_PROBE,
        "primary_news_verification": {
            "operation_id": OPERATION_ID, "status_source": OBSERVATION,
            "max_attempts": 1, "grants_general_runtime": False,
        },
    }, "state")
    exact(state["live_integration"]["permissions_enabled"], False, "state live permissions")
    exact(state["historical_checkpoints"]["old_allowances_reopened"], False, "old allowances")
    return contract


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
    contents, blobs = {}, {}

    def read(name):
        path = g.safe_path(root, name)
        require(path.is_file(), f"missing required file: {name}")
        data = path.read_bytes()
        text = data.decode("utf-8")
        require("\r" not in text, f"candidate file must use LF bytes: {name}")
        contents[name] = text
        g.scan_secrets(name, text)
        blobs[name] = hashlib.sha1(f"blob {len(data)}\0".encode("ascii") + data,
                                   usedforsecurity=False).hexdigest()

    for name in (*PLANNING_PATHS, ENTRY):
        read(name)
    contract = validate_planning(contents)
    baseline = baseline_entries(root)
    g.string_list(contract["mutable"], "contract mutable paths")
    g.string_list(contract["additions"], "contract additions")
    mutable, additions = set(contract["mutable"]), set(contract["additions"])
    require(mutable <= baseline.keys() and not additions & baseline.keys()
            and {*RECEIPTS, ENTRY} <= additions, "contract paths differ from the merged tree")
    allowed = tuple(sorted(baseline.keys() | additions))
    protected = tuple(name for name in allowed if name not in RECEIPTS)
    complete = ci or complete_index
    for name in allowed:
        if name in RECEIPTS and not complete and not g.safe_path(root, name).exists():
            continue
        if name not in contents:
            read(name)

    changed = g.inspect_git(root, ci=ci, contents=contents, complete_index=complete,
                            baseline=BASE, branch=BRANCH, allowed=allowed, immutable_hashes={})
    immutable = baseline.keys() - mutable
    require(not immutable.intersection(changed), "Git patch changes an immutable merged path")
    for name in sorted(immutable):
        exact(blobs[name], baseline[name], f"immutable merged bytes changed: {name}")
    # The shared inspector compares LF-normalized text. Dispatch also pins raw bytes
    # and refuses present additions or attestations that Git would not commit.
    entries = g.read_index(root, allowed=allowed)
    exact(sorted(entries), sorted(blobs), "index: expected every present candidate file staged")
    for name, blob in blobs.items():
        exact(entries[name], blob, f"index/worktree bytes differ: {name}")
    return contents, protected, changed


def make_snapshot(checked):
    contents, protected, changed = checked
    snapshot_id, hashes = g.snapshot_for_paths(contents, protected)
    return {
        "schema_version": 1, "package": PACKAGE, "base": BASE, "branch": BRANCH,
        "snapshot_id": snapshot_id, "planning_snapshot": PLANNING_SNAPSHOT,
        "hash_algorithm": g.ALGORITHM, "protected_paths": list(protected),
        "protected_file_hashes": hashes,
        "changed_paths": sorted(set(changed) - set(RECEIPTS)), "receipt_paths": list(RECEIPTS),
    }


def snapshot(root=ROOT, *, ci=False):
    return make_snapshot(check(root, ci=ci))


def _parse_receipt(contents, path):
    require(path in contents, f"missing required receipt: {path}")
    return g.parse_json(contents[path])


def _receipt(contents, path, snapshot_id, role, verdict, *, variable=(), **expected):
    receipt = _parse_receipt(contents, path)
    fields = {"schema_version": 1, "package": PACKAGE, "snapshot_id": snapshot_id,
              "role": role, "verdict": verdict, **expected}
    g.keys(receipt, (*fields, *variable), role)
    _expect_fields(receipt, fields, role)
    return receipt


def _receipt_hash(contents, path):
    return g.sha256(g.normalize_text(contents[path]))


def _agent_id(value):
    g.nonempty(value, "native agent ID")
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        raise Block("invalid native agent ID") from None
    require(parsed.int != 0 and str(parsed) == value, "invalid native agent ID")
    return value


def validate_chain(contents, snapshot_id):
    validation = _receipt(contents, VALIDATION, snapshot_id, "builder_validation", "PASS",
                          offline=True, variable=(*COUNT_MINIMA, "agents"))
    for name, minimum in COUNT_MINIMA.items():
        count = validation[name]
        require(type(count) is int and count >= minimum, f"validation: {name} must be >= {minimum}")
    require(validation["product_skipped"] <= 1, "validation: at most one product skip")
    exact(validation["product_collected"], validation["product_passed"] + validation["product_skipped"],
          "validation product count accounting")
    builders = validation["agents"]
    g.string_list(builders, "validation builders")
    exact(len(builders), 2, "validation builder count")
    review = _receipt(contents, REVIEW, snapshot_id, "reviewer", "PASS", read_only=True,
                      validation_sha256=_receipt_hash(contents, VALIDATION), findings=[],
                      variable=("agent_id",))
    release = _receipt(contents, PRE_RELEASE, snapshot_id, "release_checker", PREPROBE_READY,
                       read_only=True, review_sha256=_receipt_hash(contents, REVIEW), max_attempts=1,
                       variable=("agent_id",))
    actors = [AUDITOR, *(_agent_id(actor) for actor in (*builders, review["agent_id"], release["agent_id"]))]
    require(len(set(actors)) == len(actors), "entry, builders, reviewer and release must be independent")
    return release["agent_id"]


@contextmanager
def _probe_module(root):
    root = Path(root).resolve()
    package_path = g.safe_path(root, "market_intelligence/__init__.py")
    module_path = g.safe_path(root, PRODUCT)
    namespace = "_p4fed_probe_" + g.sha256(str(root))
    module_name = namespace + ".primary_news_probe"
    if module_name in sys.modules:
        # _observe calls back into preprobe using the same reviewed package.
        module = sys.modules[module_name]
        exact(Path(module.__file__).resolve(), module_path, "current probe module")
        yield module
        return
    spec = importlib.util.spec_from_file_location(
        namespace, package_path, submodule_search_locations=[str(package_path.parent)])
    require(spec is not None and spec.loader is not None, "probe package unavailable")
    package = importlib.util.module_from_spec(spec)
    sys.modules[namespace] = package
    try:
        with _source_imports():
            spec.loader.exec_module(package)
            module = importlib.import_module(module_name)
        exact(Path(module.__file__).resolve(), module_path, "current probe module")
        yield module
    finally:
        for name in tuple(sys.modules):
            if name == namespace or name.startswith(namespace + "."):
                del sys.modules[name]


def preprobe(root=ROOT, *, ci=False):
    """Return the protected snapshot and exact staged approval chain digests."""
    require(not _in_ci(ci), CI_REFUSAL)
    exact(g.git(root, "rev-parse", "--verify", "HEAD").strip(), BASE, "original base HEAD")
    checked = check(root)
    contents, _, _ = checked
    snapshot_id = make_snapshot(checked)["snapshot_id"]
    require(RELEASE not in contents, "final release already exists")
    validate_chain(contents, snapshot_id)
    observation = _parse_receipt(contents, OBSERVATION)
    with _probe_module(root) as probe:
        exact(observation, probe.pending(snapshot_id), "pending observation")
    approval_chain_sha256, _ = g.snapshot_for_paths(contents, (VALIDATION, REVIEW, PRE_RELEASE, OBSERVATION))
    return snapshot_id, approval_chain_sha256


def observe(root=ROOT, *, ci=False, allow_network=False):
    require(not _in_ci(ci), CI_REFUSAL)
    exact(allow_network, True, "observe requires --allow-network")
    initial = preprobe(root)
    snapshot_id, _ = initial
    first_recheck = True

    def authorize():
        nonlocal first_recheck
        current = preprobe(root)
        if first_recheck:
            exact(current, initial, "startup preprobe approval")
            first_recheck = False
        return current

    with _probe_module(root) as probe:
        receipt = probe._observe(authorize)
        return probe.validate_receipt(receipt, snapshot_id, terminal=True)


def gate(root=ROOT, *, ci=False):
    checked = check(root, ci=ci, complete_index=True)
    contents, _, _ = checked
    snapshot_id = make_snapshot(checked)["snapshot_id"]
    agent_id = validate_chain(contents, snapshot_id)
    _receipt(contents, RELEASE, snapshot_id, "release_checker", g.LOCAL_RELEASE, read_only=True,
             agent_id=agent_id, preprobe_sha256=_receipt_hash(contents, PRE_RELEASE),
             observation_sha256=_receipt_hash(contents, OBSERVATION),
             human_merge_required=True, remote_ci="NOT_RUN_AT_SNAPSHOT")
    with _probe_module(root) as probe:
        probe.validate_receipt(_parse_receipt(contents, OBSERVATION), snapshot_id, terminal=True)
    return g.LOCAL_RELEASE


def main(argv=None, *, root=ROOT):
    args = _parse_args(argv)
    return 1 if _refuse_runtime(args) else _run(args, root)


def _run(args, root):
    if args.command == "observe":
        try:
            receipt = observe(root, allow_network=args.allow_network)
            print(json.dumps(receipt, sort_keys=True, allow_nan=False))
            return 0
        except (Exception, KeyboardInterrupt, SystemExit):
            print("BLOCK", file=sys.stderr)
            return 1
    try:
        if args.command == "check":
            check(root, ci=args.ci)
            print("PASS (P4FED scope and planning)")
        elif args.command == "snapshot":
            print(json.dumps(snapshot(root, ci=args.ci), indent=2, sort_keys=True))
        elif args.command == "preprobe":
            preprobe(root)
            print(PREPROBE_READY)
        else:
            print(gate(root, ci=args.ci))
        return 0
    except (ValueError, OSError, RuntimeError, KeyError, TypeError, ImportError,
            subprocess.SubprocessError) as error:
        print(f"BLOCK: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(_run(_cli_args, ROOT))
