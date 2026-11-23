"""Apply the repository's offline staged-snapshot gates to P4KEY."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tomllib

if __package__:
    from .governance_rss_diagnostics import g, n
else:
    from governance_rss_diagnostics import g, n


ROOT = Path(__file__).resolve().parents[1]
BASE = "12ee5864d0dece50d0a049d26186403fd0a5d892"
BRANCH = "stage1-369-index-readiness"
PACKAGE = "P4KEY"
CONTRACT = "contracts/p4key_369_index_readiness.toml"
PLANNING_SHA = "907ae11b4eb24ebd03c5899937921021fac6383bd17d7096e6e1d6d6ecec7672"
WORKFLOW = ".github/workflows/governance.yml"
ENTRY, VALIDATION, REVIEW, RELEASE = tuple(
    f"docs/reviews/p4key-{kind}.json" for kind in ("entry", "validation", "review", "release"))
RECEIPTS = (VALIDATION, REVIEW, RELEASE)
COMMANDS = ("check", "snapshot", "gate")
exact, require = g.exact, g.require


def _baseline(root):
    files = {}
    for row in g.git_paths(g.git(root, "ls-tree", "-r", "-z", BASE)):
        meta, separator, path = row.partition("\t")
        fields = meta.split()
        require(separator and len(fields) == 3 and fields[:2] == ["100644", "blob"]
                and path not in files, "invalid PR18 baseline tree")
        files[path] = fields[2]
    require(files, "empty PR18 baseline tree")
    return files


def _planning(contents):
    exact(g.sha256(contents[CONTRACT]), PLANNING_SHA, "audited contract")
    plan = tomllib.loads(contents[CONTRACT])
    for field, value in {"base": BASE, "branch": BRANCH, "package": PACKAGE,
                         "human_merge_required": True, "real_requests_authorized": 0,
                         "attestations": list(RECEIPTS)}.items():
        exact(plan[field], value, f"contract: {field}")
    entry = g.parse_json(contents[ENTRY])
    exact(entry, {"schema_version": 1, "package": PACKAGE,
                  "role": "entry_and_prompt_auditor",
                  "agent_id": "01a203d2-b496-7833-93f7-b0cda9b4e41a",
                  "verdict": "PASS", "planning_sha256": PLANNING_SHA,
                  "runtime_authority": False}, "entry and prompt receipt")
    return plan, entry


def _workflow(original, candidate):
    for command in COMMANDS:
        old = f"        run: python tools/governance_rss_diagnostics.py {command} --ci\n"
        new = f"        run: python tools/governance_data369.py {command} --ci\n"
        exact(original.count(old), 1, "baseline offline command occurrence")
        original = original.replace(old, new)
    exact(candidate, original, "workflow: only three offline command switches allowed")


def check(root=ROOT, *, ci=False, released=False):
    root = Path(root).resolve()
    contents = {}
    blobs = {}

    def read(name):
        data = g.safe_path(root, name).read_bytes()
        require(b"\r" not in data, f"expected LF bytes: {name}")
        contents[name] = data.decode("utf-8")
        blobs[name] = hashlib.sha1(f"blob {len(data)}\0".encode() + data,
                                   usedforsecurity=False).hexdigest()

    for name in (CONTRACT, ENTRY):
        read(name)
    plan, _ = _planning(contents)
    baseline = _baseline(root)
    mutable, additions = set(plan["mutable"]), set(plan["additions"])
    require(mutable <= baseline.keys() and not additions & baseline.keys(),
            "allowlist differs from PR18 tree")
    allowed = baseline.keys() | additions
    protected = allowed - set(RECEIPTS)
    for name in sorted(allowed - contents.keys()):
        if not released and name in RECEIPTS and not g.safe_path(root, name).exists():
            continue
        read(name)
    changed = g.inspect_git(root, ci=ci, contents=contents, complete_index=released,
                            baseline=BASE, branch=BRANCH, allowed=allowed, immutable_hashes={})
    require(set(changed) <= mutable | additions, "change outside P4KEY contract")
    exact(g.read_index(root, allowed=allowed), blobs, "raw worktree and staged bytes")
    for row in g.git_paths(g.git(root, "ls-files", "--stage", "-z")):
        require(row.startswith("100644 "), "unexpected index file mode")
    for name in sorted(baseline.keys() - mutable):
        exact(blobs[name], baseline[name], f"immutable PR18 path: {name}")
    _workflow(g.git_raw(root, "cat-file", "blob", baseline[WORKFLOW]).decode("utf-8"),
              contents[WORKFLOW])
    digest, hashes = g.snapshot_for_paths(contents, sorted(protected))
    return contents, {
        "package": PACKAGE, "base": BASE, "branch": BRANCH, "snapshot_id": digest,
        "planning_sha256": PLANNING_SHA,
        "protected_file_hashes": hashes,
        "paths": sorted(set(changed) - set(RECEIPTS)),
    }


def validate_receipts(contents, snapshot_id):
    _, entry = _planning(contents)

    def receipt(path, role, verdict, variable=(), **details):
        record = g.parse_json(contents[path])
        expected = {"schema_version": 1, "package": PACKAGE, "snapshot_id": snapshot_id,
                    "role": role, "verdict": verdict, **details}
        g.keys(record, (*expected, *variable), role)
        n._expect_fields(record, expected, role)
        return record

    results = receipt(VALIDATION, "builder_validation", "PASS",
                      variable=("product_total", "product_skips", "governance_total",
                                "focused_product"), agent_id="coordinator",
                      source_requests=0, live_index_count=0, dated_run_count=0)
    for field, minimum in (("product_total", 1000), ("governance_total", 1),
                           ("focused_product", 11)):
        require(type(results[field]) is int and results[field] >= minimum, f"invalid {field}")
    require(type(results["product_skips"]) is int and 0 <= results["product_skips"] <= 1,
            "unexpected product skips")
    review = receipt(REVIEW, "reviewer", "PASS", variable=("agent_id",), read_only=True,
                     findings=[], validation_sha256=g.sha256(contents[VALIDATION]))
    release = receipt(RELEASE, "release_checker", g.LOCAL_RELEASE, variable=("agent_id",),
                      read_only=True, human_merge_required=True, runtime_authority=False,
                      remote_ci="NOT_RUN_AT_SNAPSHOT", review_sha256=g.sha256(contents[REVIEW]))
    actors = [n._agent_id(record["agent_id"]) for record in (entry, review, release)]
    require(len(set(actors)) == len(actors), "entry, review and release must be independent")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("command", choices=COMMANDS)
    parser.add_argument("--ci", action="store_true")
    args = parser.parse_args(argv)
    try:
        contents, snapshot = check(ci=args.ci, released=args.command == "gate")
        if args.command == "gate":
            validate_receipts(contents, snapshot["snapshot_id"])
            print(g.LOCAL_RELEASE)
        elif args.command == "snapshot":
            print(json.dumps(snapshot, sort_keys=True, indent=2))
        else:
            print("PASS (P4KEY offline scope; no runtime authority)")
    except (ValueError, OSError, RuntimeError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print(f"BLOCK: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
