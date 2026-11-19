"""P4T current-snapshot governance; historical approvals never authorize requests."""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import tomllib


ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("_p4t_legacy_helpers", Path(__file__).with_name("governance.py"))
g = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(g)
Block, require, exact, keys = g.Block, g.require, g.exact, g.keys
BASELINE = "3cceca6e1534484b9001db16644a98b7b068897b"
BRANCH = "stage1-tencent-index-replacement"
CONTRACT_PATH = "contracts/p4t_tencent_daily_index.toml"
HISTORY_PATH = "governance/p4t-history.json"
TEMPLATE_PATH = "templates/p4t_builder_prompt.md"
PROMPT_PATH = "docs/reviews/p4t-builder-prompt.md"
AUDIT_AGENT = "01a1f31e-108d-7603-959f-604c54e690ea"
PLANNING_SNAPSHOT = "6fe0c4d216d99592c07cfc6fc3ee485e7aa0065e6f976ab6a247f967ce35ff5f"
FROZEN_INPUT_HASHES = {
    "AGENTS.md": "b618a63a790612fa48ccc08a998259a475b13715b1d1652b8d12e7699dea76c7",
    "BLUEPRINT.md": "168111643ac2de093adba8ca852434a2e5fc161fb8e1e6e0e1b801ebd23d1138",
    "README.md": "dfe246e487a98f303eceae43c39eae88a7d04ed1f06b7cffc09d5576eac9cfab",
    "START_CONSTRUCTION_PROMPT.md": "e23acaf0b163ec2b909ddb699f2b2ba239f9d4afa286f8984cead31e6bcaedca",
    "config/tencent_daily.toml": "c87f25d3375039e81f820bf465c7e12c6b35df1d2537dfed3068866c8b0a5cf4",
    CONTRACT_PATH: "6ec603ea320a44024f22ee8e2926b8fd5dd6803bc0d1dded2c34e5b37e2a1635",
    "docs/CONSTRUCTION_LOG.md": "2c3d7b122a7dd44bbab853f0643969e18b81590e245df7b8408c6a7e709c5be1",
    "docs/TENCENT_DAILY_SOURCE.md": "588a560b3ad13734dfb30a5d3b815c08f173f079c0ceab1f21eeb3db088957a6",
    "docs/evidence/p4t-source-audit.json": "8f5e890cfd1eaf118f97e75724106c0d41d9258ea448a69f077a21e7505f56cb",
    "docs/evidence/p4t-user-authorization.json": "5a4b556310f68810d078aaf9a9b1cd040c6e00ec161da2c753f056609c08a6a3",
    "docs/reviews/p4b-merge.json": "87295b9435c27f076be1f38b49f0ff709cb8e40861bf07e5627bab69f19c3b36",
    PROMPT_PATH: "8548d31a421fcee53b428927bbec9aae1f12abf7e80b96e9e5b267c7f12494b1",
    HISTORY_PATH: "5b28adfb6b28e4b9f7b4548661673ba176cf27e4a73847bb2290f04f6b80faa1",
    "governance/policy.toml": "ee5a9e986a85620c758e091443aee614090fe7845b7afee910ca2110ea372748",
    "operations/p4d-final-connection/disposition.json": "e577cd0e6f3f80764b7524c67700b1a6ff298f72079245ee42ce00b711afb99f",
    "project-state/state.json": "045a86027628ca9e2f491798e26c8219b103f51d347fb57d52d7e7441843f45d",
    TEMPLATE_PATH: "8548d31a421fcee53b428927bbec9aae1f12abf7e80b96e9e5b267c7f12494b1",
}
MUTABLE_PATHS = (
    "AGENTS.md", "START_CONSTRUCTION_PROMPT.md", "README.md", "docs/CONSTRUCTION_LOG.md",
    "governance/policy.toml", "project-state/state.json", ".github/workflows/governance.yml",
    "tools/governance.py", "tests/governance/test_governance.py",
)
ENTRY_PATHS = ("docs/reviews/p4t-entrypoint-review.json", "docs/reviews/p4t-prompt-audit.json")
ORIGINAL_ATTESTATIONS = (
    "docs/reviews/p4t-bundle.json", "docs/reviews/p4t-validation.json",
    "docs/reviews/p4t-review.json", "docs/reviews/p4t-pre-probe-release.json",
    "docs/evidence/p4t-market-observation.json", "docs/reviews/p4t-release.json",
)
CURRENT_ATTESTATIONS = (*ORIGINAL_ATTESTATIONS[:3], ORIGINAL_ATTESTATIONS[5])
REPAIR_CONTRACT_PATH = "contracts/p4t_post_probe_test_repair.toml"
REPAIR_MANIFEST_PATH = "docs/evidence/p4t-post-probe-test-repair.json"
REPAIR_ID = "post_probe_windows_path_r1"
ORIGINAL_SNAPSHOT = "9b3f1d2c71431bcd3c794657f8bd9b8276ba38802010276e3513f6f610da774b"
ORIGINAL_COMMIT = "ec362d84c7c3fa0a8d142ac83a8531a20bbe12e0"
REPAIR_REVIEWER = "01a1f35d-d5fb-7101-8f7d-3dfc8c1f9c3e"
REPAIR_PLANNING_SNAPSHOT = "cf626fb377eb07a30ed2f56e66c2cf51007c5b7831fdc471280dd75e9e90bda9"
SUPPLEMENT_HASHES = {
    REPAIR_CONTRACT_PATH: "85c1ee2343d9761a37166cbfe5557647a17884f15b71ac8959cbedb39ce35464",
    REPAIR_MANIFEST_PATH: "d40c2d1f941d99e7f133cdcb9141dbbb4265332a00362dcb335f45332ef07ce5",
}
REPAIR_INPUT_HASHES = {**FROZEN_INPUT_HASHES, **SUPPLEMENT_HASHES}
PRODUCT_TEST_PATH = "tests/product/test_tencent_capture.py"
ARCHIVED_PROTECTED_PATHS = ("tools/governance.py", "tools/governance_p4t.py",
                            "tests/governance/test_governance_p4t.py", PRODUCT_TEST_PATH, *ENTRY_PATHS)
REPAIR_MUTABLE_PATHS = (PRODUCT_TEST_PATH, *ARCHIVED_PROTECTED_PATHS[:3],
                        *ENTRY_PATHS, *CURRENT_ATTESTATIONS)
PRE_RELEASE = "LOCAL_READY_FOR_TENCENT_DIAGNOSTIC"
FINAL_RELEASE = "LOCAL_READY_FOR_PR"
APPROVAL_VALUES = {"limited_technical_probe_exception_accepted": True,
                   "legal_entitlement_verified": False, "normal_source_operations_enabled": False}
REPAIR_APPROVAL_VALUES = {
    **APPROVAL_VALUES, "repair_id": REPAIR_ID, "approval_scope": "offline_post_probe_test_repair",
    "required_total_repair_cycles": 2, "new_source_requests": 0,
    "original_probe_snapshot": ORIGINAL_SNAPSHOT,
    "repair_manifest_digest": SUPPLEMENT_HASHES[REPAIR_MANIFEST_PATH],
}
HISTORY_KEYS = (
    "schema_version", "package_id", "purpose", "baseline", "checkpoint_commit", "p4d_checkpoint_commit",
    "checkpoint_git_objects_required", "hash_algorithm", "baseline_path_count", "checkpoint_path_count",
    "baseline_file_hashes_digest", "checkpoint_file_hashes_digest", "checkpoint_content_snapshot_id",
    "p4d_file_hashes_digest", "p4d_content_snapshot_id", "retained_delta", "archive_limits",
    "checkpoint_inputs", "p4d_inputs", "checkpoint_prefixes", "p4d_prefixes", "reconstruction",
)
HISTORY_DIGESTS = {
    "baseline_file_hashes_digest": "42d6ab17ef754183eccf1b2e29bb11281d97608c4e3d6f853057f072f92ba749",
    "checkpoint_file_hashes_digest": "9ddd29ca4b7620d727a93799136e7e6d9e10b1f8dc94cf380f70a51e4808cf7c",
    "checkpoint_content_snapshot_id": "87e61a497ce04c98009d03fb77699833379a7f52bde9d70d0e8bcb21c5497357",
    "p4d_file_hashes_digest": "d1900e534cac463e8d2a41988c0cf7efd20b7564906f8c08250ceb2dd8372533",
    "p4d_content_snapshot_id": "1a16502971ac05b373b03b758e8b88fffdefebf17dfcce22b683d952a7298fcb",
}
ARCHIVE_FILE_BYTES = 1048576
ARCHIVE_TOTAL_BYTES = 2097152


def read_text(root, name):
    if name in (HISTORY_PATH, REPAIR_MANIFEST_PATH):
        require(g.safe_path(root, name).stat().st_size <= 3 * ARCHIVE_TOTAL_BYTES,
                "history: oversized manifest")
    return g.read_text(root, name)


def frozen_text(name, text):
    exact(g.sha256(text), FROZEN_INPUT_HASHES[name], f"frozen planning input: {name}")
    return text


def validate_contract(text):
    contract = tomllib.loads(frozen_text(CONTRACT_PATH, text))
    exact(contract["package_id"], "P4T", "contract package")
    exact(contract["baseline"], BASELINE, "contract baseline")
    exact(contract["branch"], BRANCH, "contract branch")
    exact(contract["mutable_paths"], list(MUTABLE_PATHS), "contract mutable scope")
    g.string_list(contract["new_paths"], "contract new scope")
    exact(len(contract["new_paths"]), 23, "contract new scope count")
    require(not set(contract["new_paths"]) & set(MUTABLE_PATHS), "overlapping scope")
    exact(contract["permissions"], g.PERMISSIONS, "closed general permissions")
    probe = contract["limited_source_probe"]
    exact(probe["upstream_rights"], "unknown", "upstream rights")
    exact(probe["legal_entitlement_verified"], False, "upstream entitlement")
    exact(probe["normal_source_operations_enabled"], False, "normal source operations")
    return contract


def baseline_contents(root=ROOT):
    entries = {}
    for record in g.git_paths(g.git(root, "ls-tree", "-r", "-z", BASELINE)):
        metadata, separator, name = record.partition("\t")
        fields = metadata.split()
        require(separator and len(fields) == 3 and fields[0] in ("100644", "100755")
                and fields[1] == "blob" and name not in entries, "history: malformed baseline tree")
        g.safe_path(root, name)
        entries[name] = fields[2]
    exact(sorted(entries), sorted(g.BASELINE_ALLOWED), "history: merged baseline paths")
    blobs = g.read_blobs(root, entries.values())
    return {name: blobs[oid] for name, oid in sorted(entries.items())}


def file_hashes(contents):
    return {name: g.sha256(text) for name, text in sorted(contents.items())}


def _prefix(text, value):
    keys(value, ("utf8_bytes", "sha256"), "history prefix")
    require(type(value["utf8_bytes"]) is int and 0 < value["utf8_bytes"] <= ARCHIVE_FILE_BYTES,
            "history: prefix byte bound")
    encoded = text.encode("utf-8")[:value["utf8_bytes"]]
    exact(len(encoded), value["utf8_bytes"], "history: missing log prefix")
    result = encoded.decode("utf-8")
    exact(g.sha256(result), value["sha256"], "history: changed log prefix")
    return result


def reconstruct_history(proof, baseline, current):
    """Validate data only; neither checkpoint objects nor archived code are loaded."""
    keys(proof, HISTORY_KEYS, "history")
    for name, expected in {"schema_version": 1, "package_id": "P4T", "baseline": BASELINE,
                           "checkpoint_commit": "4d1ec45645a25f2ccbb7a065c95a5a9d5ec77903",
                           "p4d_checkpoint_commit": "5365ce7f9524e361758c45246f8a20a9ff61bde2",
                           "checkpoint_git_objects_required": False, "hash_algorithm": g.ALGORITHM,
                           "baseline_path_count": 220, "checkpoint_path_count": 251,
                           **HISTORY_DIGESTS}.items():
        exact(proof[name], expected, f"history.{name}")
    g.nonempty(proof["purpose"], "history purpose")
    keys(proof["reconstruction"], ("checkpoint", "p4d", "limitations"), "history reconstruction")
    for value in proof["reconstruction"].values():
        g.nonempty(value, "history reconstruction text")
    exact(proof["archive_limits"], {"per_file_utf8_bytes": ARCHIVE_FILE_BYTES,
                                    "total_utf8_bytes": ARCHIVE_TOTAL_BYTES}, "history archive limits")
    exact(sorted(baseline), sorted(g.BASELINE_ALLOWED), "history baseline paths")
    expected = file_hashes(baseline)
    exact(g.probe_digest(expected), HISTORY_DIGESTS["baseline_file_hashes_digest"], "history baseline digest")
    delta = proof["retained_delta"]
    require(type(delta) is dict and len(delta) == 42, "history: exactly42 retained deltas required")
    modified = 0
    for name, value in delta.items():
        g.safe_path(ROOT, name)
        keys(value, ("change", "sha256"), "history delta")
        exact(value["change"], "modified" if name in baseline else "added", "history delta kind")
        modified += name in baseline
        require(type(value["sha256"]) is str and re.fullmatch(r"[0-9a-f]{64}", value["sha256"]),
                "history: invalid file hash")
        require(expected.get(name) != value["sha256"], "history: redundant delta")
        expected[name] = value["sha256"]
    exact(modified, 11, "history modified count")
    exact(len(expected), 251, "history retained path count")
    exact(g.probe_digest(dict(sorted(expected.items()))), HISTORY_DIGESTS["checkpoint_file_hashes_digest"],
          "history retained path/hash set")
    keys(proof["checkpoint_inputs"], set(MUTABLE_PATHS) - {"docs/CONSTRUCTION_LOG.md"},
         "history checkpoint inputs")
    keys(proof["p4d_inputs"], ("AGENTS.md",), "history P4D inputs")
    total = 0
    for section in ("checkpoint_inputs", "p4d_inputs"):
        for name, text in proof[section].items():
            require(type(text) is str and "\r" not in text, "history: archived input must be LF text")
            size = len(text.encode("utf-8"))
            total += size
            require(size <= ARCHIVE_FILE_BYTES and total <= ARCHIVE_TOTAL_BYTES,
                    "history: archive byte bound")
            g.scan_secrets("history:" + name, text)
    for section in ("checkpoint_prefixes", "p4d_prefixes"):
        keys(proof[section], ("docs/CONSTRUCTION_LOG.md",), "history log prefix paths")
    retained = {}
    for name, digest in expected.items():
        if name in proof["checkpoint_inputs"]:
            text = proof["checkpoint_inputs"][name]
        elif name in proof["checkpoint_prefixes"]:
            text = _prefix(current[name], proof["checkpoint_prefixes"][name])
        else:
            require(name in current, f"history: missing retained input: {name}")
            text = current[name]
        exact(g.sha256(text), digest, f"history retained file: {name}")
        retained[name] = text
    exact(g.snapshot_for_paths(retained, retained)[0], HISTORY_DIGESTS["checkpoint_content_snapshot_id"],
          "history retained aggregate")
    historical = {name: retained[name] for name in g.ALLOWED}
    historical.update(proof["p4d_inputs"])
    for name, prefix in proof["p4d_prefixes"].items():
        historical[name] = _prefix(current[name], prefix)
    exact(len(historical), 242, "history P4D path count")
    exact(g.probe_digest(file_hashes(historical)), HISTORY_DIGESTS["p4d_file_hashes_digest"],
          "history P4D file map")
    exact(g.snapshot_for_paths(historical, historical)[0], HISTORY_DIGESTS["p4d_content_snapshot_id"],
          "history P4D aggregate")
    exact(g.sha256(historical["AGENTS.md"]), g.FROZEN_INPUT_HASHES["AGENTS.md"], "history original P4D entry")
    return retained, historical


def historical_contents(root=ROOT):
    text = frozen_text(HISTORY_PATH, read_text(root, HISTORY_PATH))
    proof = g.parse_json(text)
    baseline = baseline_contents(root)
    names = set(baseline) | set(proof["retained_delta"])
    current = {name: read_text(root, name) for name in names}
    return reconstruct_history(proof, baseline, current)


def validate_planning(contents, *, original=False):
    for name in FROZEN_INPUT_HASHES:
        frozen_text(name, contents[name])
    exact(g.snapshot_for_paths(contents, FROZEN_INPUT_HASHES)[0], PLANNING_SNAPSHOT, "planning snapshot")
    contract = validate_contract(contents[CONTRACT_PATH])
    state = g.parse_json(contents["project-state/state.json"])
    policy = tomllib.loads(contents["governance/policy.toml"])
    exact(state["active_package"], "P4T", "current state package")
    exact(state["package_status"], "active_unreleased", "current state lifecycle")
    exact(policy["permissions"], contract["permissions"], "current policy permissions")
    exact(state["limited_source_probe"], policy["limited_source_probe"], "current probe policy")
    exact(state["limited_source_probe"]["upstream_rights"], "unknown", "current upstream rights")
    exact(state["limited_source_probe"]["normal_source_operations_enabled"], False, "current source rights")
    exact(contents[PROMPT_PATH], contents[TEMPLATE_PATH], "deterministic prompt")
    common = {"schema_version": 1, "package_id": "P4T", "verdict": "PASS", "agent_id": AUDIT_AGENT,
              "read_only": True, "independent": True, "repair_cycles": 0, "findings": [],
              "baseline": BASELINE, "planning_snapshot_id": PLANNING_SNAPSHOT,
              "files": FROZEN_INPUT_HASHES, "limited_technical_probe_exception_accepted": True,
              "approval_scope": "implementation_only"}
    if not original:
        for name, digest in SUPPLEMENT_HASHES.items():
            exact(g.sha256(contents[name]), digest, "frozen supplement: " + name)
        exact(g.snapshot_for_paths(contents, REPAIR_INPUT_HASHES)[0], REPAIR_PLANNING_SNAPSHOT,
              "repair planning snapshot")
        del common["limited_technical_probe_exception_accepted"]
        common.update(repair_id=REPAIR_ID, planning_snapshot_id=REPAIR_PLANNING_SNAPSHOT,
                      files=REPAIR_INPUT_HASHES, approval_scope="offline_post_probe_test_repair",
                      required_total_repair_cycles=2, new_source_requests=0)
    for name, role in zip(ENTRY_PATHS, ("blueprint_reviewer", "prompt_auditor")):
        exact(g.parse_json(contents[name]), {**common, "role": role}, f"current entry receipt: {role}")
        require(AUDIT_AGENT not in state["builder_agents"], "entry: builder self-review")
    return contract, state


def planning_inputs(root=ROOT):
    contents = {name: read_text(root, name) for name in (*REPAIR_INPUT_HASHES, *ENTRY_PATHS)}
    contract, state = validate_planning(contents)
    return contract, state, contents


def scope(contract, retained, *, original=False):
    allowed = tuple(sorted(set(retained) | set(contract["new_paths"])))
    require(not set(retained) & set(contract["new_paths"]), "new path already historical")
    exact(len(allowed), 274, "current path count")
    if original:
        return allowed, tuple(name for name in allowed if name not in ORIGINAL_ATTESTATIONS)
    require(not set(allowed) & set(SUPPLEMENT_HASHES), "supplement path already historical")
    allowed = tuple(sorted((*allowed, *SUPPLEMENT_HASHES)))
    return allowed, tuple(name for name in allowed if name not in CURRENT_ATTESTATIONS)


def _repair_archives(proof):
    keys(proof, ("schema_version", "package_id", "repair_id", "purpose", "authorization", "provenance",
                 "allowed_product_test_patch", "original_receipts", "original_protected_inputs", "limits",
                 "execution_policy"), "repair proof")
    keys(proof["authorization"], ("source", "question_summary", "answer", "previous_cycles",
                                 "additional_cycles", "required_total_cycles"), "repair authorization")
    keys(proof["provenance"], ("commit", "pr_url", "git_object_required", "snapshot_id", "ci_run",
                              "ubuntu", "windows", "failed_test", "failure"), "repair provenance")
    exact(proof["limits"], {"per_file_utf8_bytes": ARCHIVE_FILE_BYTES,
                            "total_utf8_bytes": ARCHIVE_TOTAL_BYTES}, "repair archive limits")
    exact(proof["execution_policy"], {
        "archived_python_executable": False, "new_source_requests": 0,
        "original_observation_must_remain_byte_identical": True,
        "original_pre_probe_release_must_remain_byte_identical": True,
        "original_snapshot_must_not_be_rebound": True, "quota_reset_allowed": False,
        "third_repair_cycle_allowed": False}, "repair execution policy")
    archives = {}
    total = 0
    for section, names in (("original_receipts", ORIGINAL_ATTESTATIONS),
                           ("original_protected_inputs", ARCHIVED_PROTECTED_PATHS)):
        keys(proof[section], names, "repair archive paths")
        for name, value in proof[section].items():
            keys(value, ("text", "sha256"), "repair archived input")
            text = value["text"]
            require(type(text) is str and "\r" not in text, "repair: archive must be LF text")
            size = len(text.encode("utf-8"))
            total += size
            require(size <= ARCHIVE_FILE_BYTES and total <= ARCHIVE_TOTAL_BYTES, "repair archive byte bound")
            exact(g.sha256(text), value["sha256"], "repair archived file hash: " + name)
            g.scan_secrets("repair archive:" + name, text)
            archives[name] = text
    return archives


def reconstruct_probe(contents):
    """Archived text is hashed and parsed as data, never imported or executed."""
    contract, state = validate_planning(contents)
    supplement = tomllib.loads(contents[REPAIR_CONTRACT_PATH])
    exact(supplement["mutable_paths"], list(REPAIR_MUTABLE_PATHS), "repair mutable paths")
    exact(supplement["new_paths"], list(SUPPLEMENT_HASHES), "repair new paths")
    exact(supplement["current_result_paths"], list(CURRENT_ATTESTATIONS), "repair current results")
    exact(supplement["required_validation"], contract["required_validation"], "repair validation commands")
    exact(supplement["permissions"], g.PERMISSIONS, "repair permissions")
    exact(supplement["review_flow"]["builder_agents"], state["builder_agents"], "repair builders")
    proof = g.parse_json(contents[REPAIR_MANIFEST_PATH])
    archives = _repair_archives(proof)
    history = g.parse_json(contents[HISTORY_PATH])
    retained = set(g.BASELINE_ALLOWED) | set(history["retained_delta"])
    allowed, protected = scope(contract, retained, original=True)
    original = {name: archives[name] if name in archives else contents[name] for name in allowed}
    digest, hashes = g.snapshot_for_paths(original, protected)
    exact(len(hashes), 268, "original probe protected count")
    exact(digest, ORIGINAL_SNAPSHOT, "original probe aggregate")
    saved = g.parse_json(original[ORIGINAL_ATTESTATIONS[0]])
    expected = {"schema_version": 1, "package_id": "P4T", "baseline": BASELINE, "branch": BRANCH,
                "snapshot_id": digest, "hash_algorithm": g.ALGORITHM, "protected_file_hashes": hashes,
                "changed_paths": saved.get("changed_paths"), "attestation_paths": list(ORIGINAL_ATTESTATIONS),
                "scope_result": "PASS"}
    exact(saved, expected, "original probe bundle")
    validate_planning(original, original=True)
    original_checked = (contract, state, original, [])
    pre = _original_reviews(original_checked, expected)
    release = _original_reviews(original_checked, expected, final=True)
    review = g.parse_json(original[ORIGINAL_ATTESTATIONS[2]])
    exact(review["repair_cycles"], 1, "original consumed repair count")
    exact(review["agent_id"], REPAIR_REVIEWER, "original independent reviewer")
    observation = g.parse_json(original[ORIGINAL_ATTESTATIONS[4]])
    _terminal(observation)
    for field, value in (("pre_probe_release_digest", pre), ("observation_digest", observation)):
        exact(release[field], g.probe_digest(value), "original release." + field)
    for name in set(allowed) - set(REPAIR_MUTABLE_PATHS):
        exact(contents[name], original[name], "post-probe immutable input: " + name)
    change = proof["allowed_product_test_patch"]
    exact(change, {"path": PRODUCT_TEST_PATH, "before": "            self.assertEqual(path, self.path)\n",
                   "after": "            self.assertEqual(path, self.path.resolve())\n", "occurrences": 1,
                   "other_product_or_runtime_changes_allowed": False}, "repair exact test patch")
    exact(original[PRODUCT_TEST_PATH].count(change["before"]), 1, "repair original assertion count")
    exact(contents[PRODUCT_TEST_PATH], original[PRODUCT_TEST_PATH].replace(change["before"], change["after"], 1),
          "repair only permitted product test change")
    return original


def check(root=ROOT, *, ci=False, complete_index=False):
    root = Path(root).resolve()
    contract, state, contents = planning_inputs(root)
    retained, _historical = historical_contents(root)
    allowed, protected = scope(contract, retained)
    contents.update({name: read_text(root, name) for name in protected if name not in contents})
    for name in CURRENT_ATTESTATIONS:
        if g.safe_path(root, name).exists():
            contents[name] = read_text(root, name)
    for name, text in contents.items():
        g.scan_secrets(name, text)
    original = reconstruct_probe(contents)
    for name in ORIGINAL_ATTESTATIONS[3:5]:
        exact(g.safe_path(root, name).read_bytes(), original[name].encode("utf-8"),
              "original runtime receipt bytes: " + name)
    _receipt_call(root, "validate_observation_receipt", g.parse_json(original[ORIGINAL_ATTESTATIONS[4]]),
                  ORIGINAL_SNAPSHOT)
    immutable = {name: g.sha256(text) for name, text in original.items() if name not in REPAIR_MUTABLE_PATHS}
    changed = g.inspect_git(root, ci=ci, contents=contents, complete_index=complete_index,
                            baseline=BASELINE, branch=BRANCH, allowed=allowed, immutable_hashes=immutable)
    return contract, state, contents, changed


def snapshot(contents):
    contract = validate_contract(contents[CONTRACT_PATH])
    proof = g.parse_json(frozen_text(HISTORY_PATH, contents[HISTORY_PATH]))
    retained = set(g.BASELINE_ALLOWED) | set(proof["retained_delta"])
    _allowed, protected = scope(contract, retained)
    return g.snapshot_for_paths(contents, protected)


def make_bundle(checked):
    contract, _state, contents, changed = checked
    digest, hashes = snapshot(contents)
    return {"schema_version": 1, "package_id": "P4T", "baseline": BASELINE, "branch": BRANCH,
            "snapshot_id": digest, "hash_algorithm": g.ALGORITHM, "protected_file_hashes": hashes,
            "changed_paths": changed, "attestation_paths": list(CURRENT_ATTESTATIONS), "scope_result": "PASS"}


def bundle(root=ROOT, *, ci=False):
    return make_bundle(check(root, ci=ci))


def render_prompt(root=ROOT):
    planning_inputs(root)
    return frozen_text(TEMPLATE_PATH, read_text(root, TEMPLATE_PATH))


def _receipt_call(root, function, *args):
    require(function in ("validate_observation_receipt", "not_run_receipt"), "pure receipt API only")
    root = Path(root).resolve()
    package_path = g.safe_path(root, "market_intelligence/__init__.py")
    module_path = g.safe_path(root, "market_intelligence/tencent_capture.py")
    namespace = "_p4t_receipt_validation_" + g.sha256(str(root))
    require(not any(n == namespace or n.startswith(namespace + ".") for n in sys.modules),
            "recursive receipt validation")
    spec = importlib.util.spec_from_file_location(namespace, package_path,
                                                 submodule_search_locations=[str(package_path.parent)])
    require(spec is not None and spec.loader is not None, "receipt package unavailable")
    package = importlib.util.module_from_spec(spec)
    sys.modules[namespace] = package
    try:
        spec.loader.exec_module(package)
        module = importlib.import_module(namespace + ".tencent_capture")
        exact(Path(module.__file__).resolve(), module_path.resolve(), "current receipt validator")
        return getattr(module, function)(*args)
    except (ValueError, ImportError, AttributeError, OSError, TypeError, RuntimeError):
        raise Block("invalid observation or unavailable pure receipt validator") from None
    finally:
        for name in tuple(sys.modules):
            if name == namespace or name.startswith(namespace + "."):
                del sys.modules[name]


def _original_reviews(checked, current, *, final=False):
    contract, _state, contents, _changed = checked
    allowed = set(current["protected_file_hashes"]) | set(ORIGINAL_ATTESTATIONS)
    return g._validate_review_attestations(
        checked, current, paths=(*ORIGINAL_ATTESTATIONS[:3], ORIGINAL_ATTESTATIONS[5 if final else 3]),
        audit_path=ENTRY_PATHS[1], next_gate=contract["next_gate"],
        release_fields=("pre_probe_release_digest", "observation_digest") if final else (),
        release_verdict=FINAL_RELEASE if final else PRE_RELEASE, allowed_paths=allowed,
        package_status="active_unreleased", approval_values=APPROVAL_VALUES)


def _reviews(checked, current):
    contract, _state, contents, _changed = checked
    original = reconstruct_probe(contents)
    review = g.parse_json(contents[CURRENT_ATTESTATIONS[2]])
    exact(review.get("repair_cycles"), 2, "repair review: exact total cycles required")
    exact(review.get("agent_id"), REPAIR_REVIEWER, "repair review: same independent Reviewer required")
    release = g._validate_review_attestations(
        checked, current, paths=CURRENT_ATTESTATIONS, audit_path=ENTRY_PATHS[1],
        next_gate=contract["next_gate"], release_fields=("pre_probe_release_digest", "observation_digest"),
        release_verdict=FINAL_RELEASE, allowed_paths=set(current["protected_file_hashes"]) | set(CURRENT_ATTESTATIONS),
        package_status="active_unreleased", approval_values=REPAIR_APPROVAL_VALUES, repair_limit=2)
    for field, path in (("pre_probe_release_digest", ORIGINAL_ATTESTATIONS[3]),
                        ("observation_digest", ORIGINAL_ATTESTATIONS[4])):
        exact(release[field], g.probe_digest(g.parse_json(original[path])), "repair release." + field)
    return release


def not_run_release(snapshot_id):
    return {"schema_version": 1, "package_id": "P4T", "snapshot_id": snapshot_id,
            "role": "release_checker", "verdict": "NOT_RUN",
            "reason": "awaiting_terminal_observation_and_independent_release"}


def tencent_gate(root=ROOT, *, ci=False):
    raise Block("tencent-gate: consumed allowance is permanently closed; --ci is not runtime authorization")


def _terminal(observation):
    exact(observation.get("snapshot_id"), ORIGINAL_SNAPSHOT, "original observation snapshot")
    exact(observation.get("status"), "RECORDED", "actual terminal observation required")
    result = observation["observation"]
    require(result["outcome"] in ("OBSERVED", "PARTIAL", "FAILED"), "terminal outcome remains unknown")
    require(type(result["dispatch_attempts"]) is int and 1 <= result["dispatch_attempts"] <= 3,
            "actual technical attempt required")
    for key, expected in {"payloads_persisted": False, "payload_references_discarded": True,
                          "connections_closed": True, "upstream_rights": "unknown",
                          "normal_composition": "BLOCKED_SOURCE_RIGHTS", "live_verified_benchmark_count": 0}.items():
        exact(result[key], expected, "terminal observation." + key)


def gate(root=ROOT, *, ci=False):
    checked = check(root, ci=ci, complete_index=True)
    current = make_bundle(checked)
    original = reconstruct_probe(checked[2])
    _receipt_call(root, "validate_observation_receipt", g.parse_json(original[ORIGINAL_ATTESTATIONS[4]]),
                  ORIGINAL_SNAPSHOT)
    _reviews(checked, current)
    return FINAL_RELEASE


def main(argv=None, *, root=ROOT):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("check", "prompt", "bundle", "gate", "tencent-gate", "market-gate", "nbs-gate",
                 "probe-gate", "preparation-gate", "diagnostic-gate", "verification-gate"):
        command = commands.add_parser(name)
        command.add_argument("--ci", action="store_true")
        if name == "prompt":
            command.add_argument("--verify", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "prompt":
            text = render_prompt(root)
            if args.verify:
                exact(read_text(root, PROMPT_PATH), text, "deterministic prompt")
                print("PASS (P4T prompt only; no runtime authority)")
            else:
                print(text, end="")
        elif args.command == "check":
            check(root, ci=args.ci)
            print("PASS (P4T governance only; no runtime authority)")
        elif args.command == "bundle":
            print(json.dumps(bundle(root, ci=args.ci), indent=2, sort_keys=True))
        elif args.command == "gate":
            print(gate(root, ci=args.ci))
        elif args.command == "tencent-gate":
            print(tencent_gate(root, ci=args.ci))
        else:
            raise Block("P4T: historical capability is closed; no journal or source access")
        return 0
    except (ValueError, OSError, RuntimeError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(f"BLOCK: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
