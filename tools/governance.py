"""Read-only P4C offline-engineering checks; model/live readiness blocked (Python 3.12)."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import string
import subprocess
import sys
import tomllib


ROOT = Path(__file__).resolve().parents[1]
BASELINE = "8ebfa0994436c05234336105c424cbd823b2af0b"
BRANCH = "stage7-offline-workflow"
ALGORITHM = "sha256-path-and-lf-normalized-utf8-v1"
CONTRACT_PATH = "contracts/p4c_offline_workflow.toml"
TEMPLATE_PATH = "templates/p4c_builder_prompt.md"
PROMPT_PATH = "docs/reviews/p4c-builder-prompt.md"
PREREQUISITE_PATH = "docs/evidence/live-integration-prerequisites.json"
PREREQUISITE_HASH = "55fd89e11f1b771c40805ef58eed2b8dedbdbb7686cedf6f3b183526aaa3f2e3"
ATTESTATIONS = tuple([
    "docs/reviews/p4c-bundle.json",
    "docs/reviews/p4c-validation.json",
    "docs/reviews/p4c-review.json",
    "docs/reviews/p4c-release.json"
])
ALLOWED = tuple([
    ".gitattributes",
    ".github/workflows/governance.yml",
    ".gitignore",
    "AGENTS.md",
    "BLUEPRINT.md",
    "README.md",
    "START_CONSTRUCTION_PROMPT.md",
    "config/p1_profile.toml",
    "config/p2_entities.toml",
    "config/p2_methods.toml",
    "config/p3_research.toml",
    "config/p4_publication.toml",
    "config/sources.toml",
    "contracts/p0_stage0.toml",
    "contracts/p1_sources_evidence.toml",
    "contracts/p2_intelligence.toml",
    "contracts/p3_research.toml",
    "contracts/p4_live_integration_entry.toml",
    "contracts/p4_publication.toml",
    "contracts/p4c_offline_workflow.toml",
    "docs/CONSTRUCTION_LOG.md",
    "docs/LEGACY_REUSE_MANIFEST.md",
    "docs/LIVE_INTEGRATION_PLAN.md",
    "docs/OFFLINE_WORKFLOW_PLAN.md",
    "docs/P1_DESIGN.md",
    "docs/P2_METHODS.md",
    "docs/P3_METHODS.md",
    "docs/P4_METHODS.md",
    "docs/PUBLICATION_PROVENANCE.json",
    "docs/SOURCE_MATRIX.md",
    "docs/evidence/live-integration-prerequisites.json",
    "docs/evidence/p1-source-probe.json",
    "docs/evidence/p2-demo.json",
    "docs/evidence/p3-demo.json",
    "docs/evidence/p4-demo.json",
    "docs/evidence/p4-report.txt",
    "docs/reviews/blueprint-review.json",
    "docs/reviews/p0-builder-prompt.md",
    "docs/reviews/p0-bundle.json",
    "docs/reviews/p0-merge.json",
    "docs/reviews/p0-prompt-audit.json",
    "docs/reviews/p0-release.json",
    "docs/reviews/p0-review.json",
    "docs/reviews/p0-validation.json",
    "docs/reviews/p1-builder-prompt.md",
    "docs/reviews/p1-bundle.json",
    "docs/reviews/p1-entrypoint-review.json",
    "docs/reviews/p1-merge.json",
    "docs/reviews/p1-prompt-audit.json",
    "docs/reviews/p1-release.json",
    "docs/reviews/p1-review.json",
    "docs/reviews/p1-validation.json",
    "docs/reviews/p2-builder-prompt.md",
    "docs/reviews/p2-bundle.json",
    "docs/reviews/p2-entrypoint-review.json",
    "docs/reviews/p2-merge.json",
    "docs/reviews/p2-prompt-audit.json",
    "docs/reviews/p2-release.json",
    "docs/reviews/p2-review.json",
    "docs/reviews/p2-validation.json",
    "docs/reviews/p3-builder-prompt.md",
    "docs/reviews/p3-bundle.json",
    "docs/reviews/p3-entrypoint-review.json",
    "docs/reviews/p3-merge.json",
    "docs/reviews/p3-prompt-audit.json",
    "docs/reviews/p3-release.json",
    "docs/reviews/p3-review.json",
    "docs/reviews/p3-validation.json",
    "docs/reviews/p4-builder-prompt.md",
    "docs/reviews/p4-bundle.json",
    "docs/reviews/p4-entrypoint-review.json",
    "docs/reviews/p4-merge.json",
    "docs/reviews/p4-prompt-audit.json",
    "docs/reviews/p4-release.json",
    "docs/reviews/p4-review.json",
    "docs/reviews/p4-validation.json",
    "docs/reviews/p4c-builder-prompt.md",
    "docs/reviews/p4c-bundle.json",
    "docs/reviews/p4c-entrypoint-review.json",
    "docs/reviews/p4c-prompt-audit.json",
    "docs/reviews/p4c-release.json",
    "docs/reviews/p4c-review.json",
    "docs/reviews/p4c-validation.json",
    "docs/reviews/p4l-builder-prompt.md",
    "docs/reviews/p4l-bundle.json",
    "docs/reviews/p4l-entrypoint-review.json",
    "docs/reviews/p4l-merge.json",
    "docs/reviews/p4l-prompt-audit.json",
    "docs/reviews/p4l-release.json",
    "docs/reviews/p4l-review.json",
    "docs/reviews/p4l-validation.json",
    "governance/policy.toml",
    "market_intelligence/__init__.py",
    "market_intelligence/__main__.py",
    "market_intelligence/cost_guard.py",
    "market_intelligence/delivery.py",
    "market_intelligence/evaluation.py",
    "market_intelligence/gate.py",
    "market_intelligence/intelligence.py",
    "market_intelligence/ledger.py",
    "market_intelligence/market_state.py",
    "market_intelligence/models.py",
    "market_intelligence/news_events.py",
    "market_intelligence/publication.py",
    "market_intelligence/research.py",
    "market_intelligence/runtime.py",
    "market_intelligence/scenario.py",
    "market_intelligence/sources.py",
    "market_intelligence/store.py",
    "market_intelligence/workflow.py",
    "project-state/state.json",
    "requirements.lock",
    "templates/builder_prompt.md",
    "templates/p1_builder_prompt.md",
    "templates/p2_builder_prompt.md",
    "templates/p3_builder_prompt.md",
    "templates/p4_builder_prompt.md",
    "templates/p4c_builder_prompt.md",
    "templates/p4l_builder_prompt.md",
    "tests/fixtures/p1_feed.xml",
    "tests/fixtures/p1_market.csv",
    "tests/fixtures/p2_input.json",
    "tests/fixtures/p3_responses.json",
    "tests/governance/test_governance.py",
    "tests/product/test_p1.py",
    "tests/product/test_p2.py",
    "tests/product/test_p3.py",
    "tests/product/test_p4.py",
    "tests/product/test_scenario.py",
    "tests/product/test_workflow.py",
    "tools/governance.py"
])
IMMUTABLE = tuple([
    ".gitattributes",
    ".gitignore",
    "config/p1_profile.toml",
    "config/p2_entities.toml",
    "config/p2_methods.toml",
    "config/p3_research.toml",
    "config/p4_publication.toml",
    "config/sources.toml",
    "contracts/p0_stage0.toml",
    "contracts/p1_sources_evidence.toml",
    "contracts/p2_intelligence.toml",
    "contracts/p3_research.toml",
    "contracts/p4_live_integration_entry.toml",
    "contracts/p4_publication.toml",
    "docs/LEGACY_REUSE_MANIFEST.md",
    "docs/LIVE_INTEGRATION_PLAN.md",
    "docs/P1_DESIGN.md",
    "docs/P2_METHODS.md",
    "docs/P3_METHODS.md",
    "docs/P4_METHODS.md",
    "docs/PUBLICATION_PROVENANCE.json",
    "docs/SOURCE_MATRIX.md",
    "docs/evidence/p1-source-probe.json",
    "docs/evidence/p2-demo.json",
    "docs/evidence/p3-demo.json",
    "docs/evidence/p4-demo.json",
    "docs/evidence/p4-report.txt",
    "docs/reviews/blueprint-review.json",
    "docs/reviews/p0-builder-prompt.md",
    "docs/reviews/p0-bundle.json",
    "docs/reviews/p0-merge.json",
    "docs/reviews/p0-prompt-audit.json",
    "docs/reviews/p0-release.json",
    "docs/reviews/p0-review.json",
    "docs/reviews/p0-validation.json",
    "docs/reviews/p1-builder-prompt.md",
    "docs/reviews/p1-bundle.json",
    "docs/reviews/p1-entrypoint-review.json",
    "docs/reviews/p1-merge.json",
    "docs/reviews/p1-prompt-audit.json",
    "docs/reviews/p1-release.json",
    "docs/reviews/p1-review.json",
    "docs/reviews/p1-validation.json",
    "docs/reviews/p2-builder-prompt.md",
    "docs/reviews/p2-bundle.json",
    "docs/reviews/p2-entrypoint-review.json",
    "docs/reviews/p2-merge.json",
    "docs/reviews/p2-prompt-audit.json",
    "docs/reviews/p2-release.json",
    "docs/reviews/p2-review.json",
    "docs/reviews/p2-validation.json",
    "docs/reviews/p3-builder-prompt.md",
    "docs/reviews/p3-bundle.json",
    "docs/reviews/p3-entrypoint-review.json",
    "docs/reviews/p3-merge.json",
    "docs/reviews/p3-prompt-audit.json",
    "docs/reviews/p3-release.json",
    "docs/reviews/p3-review.json",
    "docs/reviews/p3-validation.json",
    "docs/reviews/p4-builder-prompt.md",
    "docs/reviews/p4-bundle.json",
    "docs/reviews/p4-entrypoint-review.json",
    "docs/reviews/p4-merge.json",
    "docs/reviews/p4-prompt-audit.json",
    "docs/reviews/p4-release.json",
    "docs/reviews/p4-review.json",
    "docs/reviews/p4-validation.json",
    "docs/reviews/p4l-builder-prompt.md",
    "docs/reviews/p4l-bundle.json",
    "docs/reviews/p4l-entrypoint-review.json",
    "docs/reviews/p4l-prompt-audit.json",
    "docs/reviews/p4l-release.json",
    "docs/reviews/p4l-review.json",
    "docs/reviews/p4l-validation.json",
    "market_intelligence/__init__.py",
    "market_intelligence/__main__.py",
    "market_intelligence/cost_guard.py",
    "market_intelligence/delivery.py",
    "market_intelligence/evaluation.py",
    "market_intelligence/gate.py",
    "market_intelligence/intelligence.py",
    "market_intelligence/ledger.py",
    "market_intelligence/market_state.py",
    "market_intelligence/models.py",
    "market_intelligence/news_events.py",
    "market_intelligence/research.py",
    "market_intelligence/runtime.py",
    "market_intelligence/sources.py",
    "market_intelligence/store.py",
    "requirements.lock",
    "templates/builder_prompt.md",
    "templates/p1_builder_prompt.md",
    "templates/p2_builder_prompt.md",
    "templates/p3_builder_prompt.md",
    "templates/p4_builder_prompt.md",
    "templates/p4l_builder_prompt.md",
    "tests/fixtures/p1_feed.xml",
    "tests/fixtures/p1_market.csv",
    "tests/fixtures/p2_input.json",
    "tests/fixtures/p3_responses.json",
    "tests/product/test_p1.py",
    "tests/product/test_p2.py",
    "tests/product/test_p3.py",
    "tests/product/test_p4.py"
])
PROTECTED = tuple(sorted(set(ALLOWED) - set(ATTESTATIONS)))
PERMISSIONS = {
    "research_only": True,
    "external_data_access_allowed": False,
    "scheduled_collection_allowed": False,
    "llm_research_allowed": False,
    "telegram_delivery_allowed": False,
    "vps_deployment_allowed": False,
    "vps_mutation_allowed": False,
    "broker_allowed": False,
    "order_execution_allowed": False,
    "portfolio_control_allowed": False
}
REPOSITORY = {
    "owner": "kamrean",
    "name": "a-share-market-intelligence-public",
    "visibility": "public"
}
VALIDATION_COMMANDS = [
    "python -m unittest discover -s tests/governance -p test_*.py -v",
    "python -m unittest discover -s tests/product -p test_*.py -v",
    "python tools/governance.py check",
    "python tools/governance.py prompt --verify",
    "python tools/governance.py bundle",
    "git diff --check"
]
AUDIT_INPUTS = (CONTRACT_PATH, "governance/policy.toml", TEMPLATE_PATH, PROMPT_PATH,
                "AGENTS.md", "docs/OFFLINE_WORKFLOW_PLAN.md", PREREQUISITE_PATH)
# Approved independent audit inputs, not hashes supplied by a mutable receipt.
FROZEN_INPUT_HASHES = {
    "BLUEPRINT.md": "ba305530fb632c054e33b53a173733568c533600150ea166071f5341dbd7b9ac",
    "START_CONSTRUCTION_PROMPT.md": "2d3798c2b8f0a446207f8961775bf5935d491f495858a137848e943e5b2fe650",
    CONTRACT_PATH: "885faf19039fa359e7095f6f759269c75f853ff962a8e1196761335b529b0f69",
    "governance/policy.toml": "2dab2dae4b2183b0ae2ae24eb479af04a07d6359a4b1b80d7b622dee98b11db8",
    TEMPLATE_PATH: "12553ccbd7ad4679ea76b28df4932447b45ae2e3b933b61e1d9facf65235a6f1",
    PROMPT_PATH: "dd32fbfe53bae2a85320a01f54c640b7fefe71d0a9ef5fc35d6ef0c95e04878d",
    "AGENTS.md": "a50002fe1f3ae1a247951f3ab16de8a614ff5f14b36657a608ad6514f054d299",
    "docs/OFFLINE_WORKFLOW_PLAN.md": "9dd2b6607825e4419f0a5fb2a504fbdbfa7956c2cda37ec8bd74e01cf07b6f70",
    PREREQUISITE_PATH: PREREQUISITE_HASH,
}


def dependency():
    return {
        "package_id": "P4L",
        "pr_url": "https://github.com/kamrean/a-share-market-intelligence-public/pull/6",
        "merge_commit": BASELINE,
        "merged_at": "2026-11-13T15:39:08Z",
        "tested_head": "02fe397651e17f3485a7d282b861cc6007fbce49",
        "pr_ci_run": 43450010910,
        "merge_ci_run": 43454745770,
        "ci_status": "PASS",
        "merge_status": "merged"
    }


POLICY = {
    "schema_version": 1, "active_package": "P4C", "python_version": "3.12",
    "merge_tier": "D", "human_merge_required": True, "auto_merge_allowed": False,
    "direct_merge_allowed": False, "admin_bypass_allowed": False,
    "remote_creation_authorized": False, "remote_ci_status": "NOT_RUN_AT_SNAPSHOT",
    "repository": REPOSITORY, "max_repair_cycles": 1,
    "reviewer_must_be_independent": True, "construction_subagents_allowed": True,
    "permissions": PERMISSIONS,
}
RECEIPT_PATHS = {
    "blueprint_review": "docs/reviews/p4c-entrypoint-review.json",
    "prompt_audit": "docs/reviews/p4c-prompt-audit.json", "builder_prompt": PROMPT_PATH,
    **dict(zip(("bundle", "validation", "reviewer", "release"), ATTESTATIONS)),
}
NEXT_GATE = ("exact_head_ci_and_independent_release_then_human_merge; "
             "select and audit final free model/source/private delivery integration; "
             "no dispatch or VPS")
LOCAL_RELEASE = "LOCAL_READY_FOR_PR"
GOVERNANCE_ONLY = ("offline-engineering only; model/live-readiness-blocked: "
                   "BLOCKED_EXTERNAL_PREREQUISITES")


class Block(ValueError):
    """An unmet local prerequisite, never an authorization to repair scope."""


def require(condition, message):
    if not condition:
        raise Block(message)


def keys(value, expected, label):
    require(type(value) is dict, f"{label}: expected an object")
    missing, unknown = set(expected) - value.keys(), value.keys() - set(expected)
    require(not missing and not unknown,
            f"{label}: missing fields {sorted(missing)}; unknown fields {sorted(unknown)}")


def exact(value, expected, label):
    """Equality with exact types: False, 0 and null are never interchangeable."""
    require(type(value) is type(expected), f"{label}: wrong type")
    if type(expected) is dict:
        keys(value, expected, label)
        for key, item in expected.items():
            exact(value[key], item, f"{label}.{key}")
    elif type(expected) is list:
        require(len(value) == len(expected), f"{label}: wrong length")
        for index, item in enumerate(expected):
            exact(value[index], item, f"{label}[{index}]")
    else:
        require(value == expected, f"{label}: unexpected value")


def nonempty(value, label):
    require(type(value) is str and bool(value.strip()), f"{label}: expected nonempty text")


def string_list(value, label):
    require(type(value) is list and bool(value), f"{label}: expected nonempty list")
    for item in value:
        nonempty(item, label)
    require(len(value) == len(set(value)), f"{label}: duplicate entries")


def validate_contract(contract):
    pinned = {
        "schema_version": 1, "package_id": "P4C", "stage_ids": [1, 2, 3, 4, 5, 6, 7, 8],
        "branch": BRANCH, "baseline": BASELINE, "merge_tier": "D",
        "human_merge_required": True, "auto_merge_allowed": False,
        "max_repair_cycles": 1, "blueprint_review_required": True,
        "prompt_audit_required": True, "independent_review_required": True,
        "snapshot_algorithm": ALGORITHM, "allowed_paths": list(ALLOWED),
        "immutable_paths": list(IMMUTABLE), "attestation_paths": list(ATTESTATIONS),
        "forbidden_prefixes": ["apps/", "src/", "deploy/", "data/", "runtime/", "secrets/"],
        "required_reads": ["BLUEPRINT.md", "START_CONSTRUCTION_PROMPT.md", CONTRACT_PATH,
                           "governance/policy.toml", "project-state/state.json",
                           "docs/reviews/p4l-merge.json", "docs/OFFLINE_WORKFLOW_PLAN.md",
                           PREREQUISITE_PATH],
        "required_validation": VALIDATION_COMMANDS, "permissions": PERMISSIONS,
        "repository": REPOSITORY, "dependency": dependency(), "next_gate": NEXT_GATE,
    }
    text_fields = ("title", "objective", "bootstrap_prompt", "rollback")
    list_fields = ("deliverables", "negative_tests", "receipt_rules")
    keys(contract, (*pinned, *text_fields, *list_fields), "contract")
    for key, value in pinned.items():
        exact(contract[key], value, f"contract.{key}")
    for key in text_fields:
        nonempty(contract[key], f"contract.{key}")
    for key in list_fields:
        string_list(contract[key], f"contract.{key}")
    return contract


def validate_state(state):
    pinned = {
        "schema_version": 1, "active_package": "P4C", "active_contract": CONTRACT_PATH,
        "branch": BRANCH, "baseline": BASELINE,
        "milestones": {"research_mvp": "not_implemented",
                       "forward_observation": "not_started", "vps_release": "not_authorized"},
        "source_readiness": "BLOCKED_MARKET_SOURCES", "dependency": dependency(),
        "github": {"remote_creation_authorized": False, "owner": "kamrean",
                   "repository": "a-share-market-intelligence-public",
                   "visibility": "public", "remote_ci": "NOT_RUN_AT_SNAPSHOT",
                   "merge_status": "not_merged"},
        "receipts": RECEIPT_PATHS,
        "next_gate": NEXT_GATE,
    }
    keys(state, (*pinned, "package_status", "builder_agents", "stage_registry", "last_updated",
                 "live_integration"),
         "state")
    for key, value in pinned.items():
        exact(state[key], value, f"state.{key}")
    require(type(state["package_status"]) is str and state["package_status"] in
            ("building", "implemented_unreleased"), "state: premature/unknown package status")
    runtime_status = ("offline_workflow_building_live_deferred"
                      if state["package_status"] == "building"
                      else "offline_core_implemented_live_deferred")
    exact(state["live_integration"], {
        "entry_status": "human_merged", "runtime_implementation": runtime_status,
        "readiness": "BLOCKED_EXTERNAL_PREREQUISITES",
        "prerequisite_register": PREREQUISITE_PATH, "permissions_enabled": False,
    }, "state.live_integration")
    string_list(state["builder_agents"], "state.builder_agents")
    require(all(agent == agent.strip() for agent in state["builder_agents"]),
            "state.builder_agents: surrounding whitespace")
    packages = ("P0", "P1", "P1", "P2", "P2", "P3", "P3", "P4", "P4/P5", "P5")
    expected = [{"stage": i, "package": package,
                 "status": "complete" if i in (0, 2) else "engineering_merged_source_gaps" if i == 1 else
                           "engineering_merged_live_gaps" if i in (3, 4, 5, 6, 7) else
                           "deferred" if i == 9 else "offline_subset_merged_live_gaps"}
                for i, package in enumerate(packages)]
    exact(state["stage_registry"], expected, "state.stage_registry")
    require(type(state["last_updated"]) is str and
            re.fullmatch(r"\d{4}-\d{2}-\d{2}", state["last_updated"]), "state: invalid date")


def safe_path(root, name):
    require(type(name) is str and bool(name), "unsafe empty path")
    require(not any(ord(char) < 32 for char in name) and "\\" not in name and ":" not in name,
            "unsafe path characters")
    parts = name.split("/")
    require(all(part not in ("", ".", "..") and not part.endswith((".", " ")) for part in parts),
            "unsafe path components")
    root = Path(root).resolve()
    path = root
    for part in parts:
        path /= part
        require(not path.is_symlink() and not path.is_junction(), f"link/junction forbidden: {name}")
    require(path.resolve().is_relative_to(root), f"path escapes repository: {name}")
    if path.is_file():
        require(path.stat().st_nlink == 1, f"hardlink forbidden: {name}")
    return path


def normalize_text(text):
    return text.replace("\r\n", "\n").replace("\r", "\n")


def read_text(root, name):
    path = safe_path(root, name)
    require(path.is_file(), f"missing required file: {name}")
    return normalize_text(path.read_bytes().decode("utf-8"))


def parse_json(text):
    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "JSON: duplicate field")
            result[key] = value
        return result

    def invalid_constant(_value):
        raise Block("JSON: non-finite number")

    return json.loads(text, object_pairs_hook=object_pairs, parse_constant=invalid_constant)


def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def validate_live_prerequisites(text):
    text = normalize_text(text)
    register = parse_json(text)
    # Pin approved bytes and the complete typed schema; a rehashed audit cannot
    # promote source/model/private-delivery readiness or thaw individual rows.
    exact(sha256(text), PREREQUISITE_HASH, "prerequisite register frozen content")
    pinned = {
        "schema_version": 1, "package_id": "P4C", "observed_on": "2026-11-13",
        "basis": "primary_documentation_repository_and_user_declared_scope",
        "product_probes_performed": False, "credentials_inspected": False,
        "readiness": "BLOCKED_EXTERNAL_PREREQUISITES",
        "benchmark_universe": ["000001.SH", "399001.SZ", "399006.SZ", "KOSPI", "SP500"],
        "live_verified_benchmarks": 0, "selected_providers": [],
        "rights_scope": ["automated_retrieval", "raw_retention", "normalized_retention",
                         "derived_analysis", "external_model_input", "private_delivery",
                         "public_redistribution"],
        "required_inputs": [
            {
                "id": "mainland_sources",
                "targets": ["000001.SH", "399001.SZ", "399006.SZ"],
                "status": "free_local_candidate_available_live_and_external_processing_unverified",
                "account_status": "not_established",
                "source_refs": [
                    "https://www.sse.com.cn/home/legal/",
                    "https://www.cnindex.com.cn/zh_services/data_licensing/index.html?act_menu=4&act_service=3",
                    "https://quantapi.51ifind.com/gwstatic/static/ds_web/quantapi-web/help-center/faq.html",
                    "https://www.baostock.com/mainContent?file=home.md",
                    "https://www.baostock.com/mainContent?file=indexData.md",
                    "https://www.baostock.com/disclaimer",
                ],
                "candidate": "baostock_standard_free", "candidate_personal_key_required": False,
                "documented_symbols": ["sh.000001", "sz.399001"], "unverified_symbol": "sz.399006",
            },
            {
                "id": "korean_source", "targets": ["KOSPI"],
                "status": "rights_and_entitlement_unresolved", "account_status": "not_established",
                "source_refs": ["https://www.data.go.kr/data/15094807/openapi.do",
                                "https://www.kogl.or.kr/info/licenseType4.do"],
            },
            {
                "id": "us_source", "targets": ["SP500"],
                "status": "compatible_provider_unresolved", "account_status": "not_established",
                "source_refs": ["https://fred.stlouisfed.org/series/SP500",
                                "https://fred.stlouisfed.org/legal/"],
            },
            {
                "id": "primary_news",
                "status": "historical_fed_observation_current_permission_disabled",
                "a_share_source_status": "candidate_only",
                "source_refs": ["docs/evidence/p1-source-probe.json",
                                "https://www.sse.com.cn/aboutus/mediacenter/hotandd/",
                                "https://www.sse.com.cn/home/legal/"],
            },
            {
                "id": "model", "status": "deferred_by_user_zero_paid_budget",
                "account_status": "not_established",
            },
            {
                "id": "private_telegram",
                "status": "user_confirmed_own_private_chat_credentials_and_release_unverified",
                "account_status": "bot_exists_user_reported",
            },
            {
                "id": "engineering",
                "status": "configurable_offline_workflow_in_scope_live_adapters_deferred",
                "source_refs": ["market_intelligence/intelligence.py",
                                "market_intelligence/research.py",
                                "market_intelligence/publication.py"],
            },
            {
                "id": "acceptance",
                "status": "genuine_runs_human_sampling_and_private_delivery_not_completed",
                "required_dated_runs": 3, "completed_dated_runs": 0,
                "required_claim_sample": "at_least_20_or_all_if_fewer",
                "human_sample_status": "not_assessed",
            },
        ],
        "permission_grant": False,
        "next_action": "Finish configurable offline workflow first; final source/model/private "
                       "delivery integration requires audited concrete scope and independent "
                       "release. No external dispatch.",
    }
    exact(register, pinned, "prerequisite register")
    return register


def render_prompt(contract, template):
    validate_contract(contract)
    try:
        result = string.Template(template).substitute(
            contract_json=json.dumps(contract, indent=2, sort_keys=True))
    except (KeyError, ValueError) as error:
        raise Block("builder template: invalid substitution") from error
    require("$contract_json" in template or "${contract_json}" in template,
            "builder template: missing contract substitution")
    return result.rstrip("\n") + "\n"


def git_raw(root, *args, input_bytes=None):
    environment = os.environ.copy()
    # Inherited Git routing must not turn an injected root into a different repo.
    for name in tuple(environment):
        if name.startswith("GIT_"):
            environment.pop(name)
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    environment["GIT_NO_REPLACE_OBJECTS"] = "1"
    result = subprocess.run(
        ["git", "-c", "core.fsmonitor=false", "-C", str(root), *args],
        input=input_bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=environment, check=False)
    require(result.returncode == 0, f"Git {' '.join(args[:2])}: failed ({result.returncode})")
    return result.stdout


def git(root, *args):
    return git_raw(root, *args).decode("utf-8")


def read_blobs(root, object_ids):
    object_ids = sorted(set(object_ids))
    require(all(type(oid) is str and re.fullmatch(r"[0-9a-f]{40}", oid)
                for oid in object_ids), "index: invalid batch blob ID")
    if not object_ids:
        return {}
    raw = git_raw(root, "cat-file", "--batch",
                  input_bytes=("\n".join(object_ids) + "\n").encode("ascii"))
    blobs, offset = {}, 0
    # Git frames each payload with its byte length, not a line count. This also
    # preserves multibyte UTF-8 and embedded newlines without a process per file.
    for oid in object_ids:
        newline = raw.find(b"\n", offset)
        require(newline >= offset, "index: truncated batch header")
        header = raw[offset:newline].split(b" ")
        require(len(header) == 3 and header[:2] == [oid.encode("ascii"), b"blob"]
                and header[2].isdigit(), "index: malformed batch header")
        start = newline + 1
        end = start + int(header[2])
        require(end < len(raw) and raw[end:end + 1] == b"\n",
                "index: truncated batch payload")
        blobs[oid] = normalize_text(raw[start:end].decode("utf-8"))
        offset = end + 1
    require(offset == len(raw), "index: trailing batch output")
    return blobs


def git_paths(output):
    require(not output or output.endswith("\0"), "Git returned malformed path list")
    return output[:-1].split("\0") if output else []


def harmless_cache(name):
    known = {
        (Path(path).parent / "__pycache__" / Path(path).stem).as_posix()
        for path in ALLOWED if path.endswith(".py")
    }
    match = re.fullmatch(r"(.+)\.cpython-312(?:\.opt-[12])?\.pyc", name)
    return bool(match and match.group(1) in known)


def read_index(root):
    entries = {}
    for record in git_paths(git(root, "ls-files", "--stage", "-z")):
        metadata, separator, name = record.partition("\t")
        fields = metadata.split()
        require(separator and len(fields) == 3, "Git returned malformed index entry")
        mode, oid, stage = fields
        safe_path(root, name)
        require(name in ALLOWED, f"out-of-scope path: {name}")
        require(stage == "0" and name not in entries, f"index: unresolved entry: {name}")
        require(mode in ("100644", "100755"), f"index: non-regular file: {name}")
        require(re.fullmatch(r"[0-9a-f]{40}", oid), f"index: invalid blob ID: {name}")
        entries[name] = oid
    return entries


def inspect_git(root, *, ci=False, contents=None, complete_index=False):
    root = Path(root).resolve()
    require(Path(git(root, "rev-parse", "--show-toplevel").strip()).resolve() == root,
            "Git root differs from requested root")
    exact(git(root, "rev-parse", "--verify", f"{BASELINE}^{{commit}}").strip(), BASELINE,
          "Git baseline")
    git(root, "merge-base", "--is-ancestor", BASELINE, "HEAD")
    if not ci:
        exact(git(root, "symbolic-ref", "--quiet", "--short", "HEAD").strip(), BRANCH,
              "Git branch")
    diff_flags = ("--no-ext-diff", "--no-textconv", "--no-renames", "--name-only", "-z")
    committed = git_paths(git(root, "diff", *diff_flags, BASELINE, "HEAD", "--"))
    staged = git_paths(git(root, "diff", *diff_flags, "--cached", "--ita-invisible-in-index", "HEAD", "--"))
    removed = git_paths(git(root, "diff", *diff_flags, "--cached", "--ita-invisible-in-index",
                           "--diff-filter=D", "HEAD", "--"))
    working = git_paths(git(root, "diff", *diff_flags, "--"))
    entries = read_index(root)
    tracked = list(entries)
    head = git_paths(git(root, "ls-tree", "-r", "--name-only", "-z", "HEAD", "--"))
    untracked = git_paths(git(root, "ls-files", "--others", "--exclude-standard", "-z"))
    ignored = git_paths(git(root, "ls-files", "--others", "--ignored", "--exclude-standard", "-z"))
    for name in set(committed + staged + working + tracked + head + untracked + ignored):
        safe_path(root, name)
        if name in ignored and harmless_cache(name):
            continue
        require(name in ALLOWED, f"out-of-scope path: {name}")
    required = set(ALLOWED) if complete_index else set(head)
    missing = (required - entries.keys()) | set(removed)
    require(not missing, f"index: missing commit candidate files: {', '.join(sorted(missing))}")
    indexed_blobs = read_blobs(root, entries.values())
    for name, oid in sorted(entries.items()):
        # Intent-to-add entries appear in ls-files but are omitted from a commit.
        require(name in head or name in staged, f"index: intent-to-add entry: {name}")
        indexed = indexed_blobs[oid]
        scan_secrets(f"index:{name}", indexed)
        if contents is None:
            working_text = read_text(root, name)
        else:
            require(name in contents, f"index: missing working input: {name}")
            working_text = contents[name]
        require(indexed == working_text, f"index/worktree mismatch: {name}; stage the final working content")
    # Historical evidence is not just in scope: its exact baseline content is immutable.
    baseline_entries = {}
    for record in git_paths(git(root, "ls-tree", "-r", "-z", BASELINE, "--", *IMMUTABLE)):
        metadata, separator, name = record.partition("\t")
        fields = metadata.split()
        require(separator and len(fields) == 3 and fields[1] == "blob",
                "immutable baseline: malformed entry")
        baseline_entries[name] = fields[2]
    exact(sorted(baseline_entries), sorted(IMMUTABLE), "immutable baseline paths")
    baseline_blobs = read_blobs(root, baseline_entries.values())
    for name, oid in baseline_entries.items():
        require(read_text(root, name) == baseline_blobs[oid],
                f"immutable historical input changed: {name}")
    # Git can omit an empty directory junction. Inspect links before descending,
    # excluding only Git's own metadata, which is not a construction input.
    directories = [root]
    while directories:
        directory = directories.pop()
        for path in directory.iterdir():
            if directory == root and path.name == ".git":
                continue
            safe_path(root, path.relative_to(root).as_posix())
            if path.is_dir():
                directories.append(path)
    return sorted(set(committed + staged + working + untracked +
                      [name for name in ignored if not harmless_cache(name)]))


SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{24,}\b"),
    re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{30,}\b"),
    re.compile(r"(?i)\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|"
               r"client[_-]?secret|telegram[_-]?bot[_-]?token)[\"']?\s*[:=]\s*"
               r"[\"']?([A-Za-z0-9_./+=-]{12,})[\"']?(?=\s|[,;}]|$)"),
)


def scan_secrets(name, text):
    for pattern in SECRET_PATTERNS:
        require(pattern.search(text) is None, f"possible secret in {name}")


def validate_prerequisite(receipt, role, names, contents):
    require(type(receipt) is dict, f"{role}: expected receipt object")
    for key, value in {"schema_version": 1, "role": role, "verdict": "PASS",
                       "read_only": True, "independent": True}.items():
        exact(receipt.get(key), value, f"{role}.{key}")
    nonempty(receipt.get("agent_id"), f"{role}.agent_id")
    expected = {name: sha256(contents[name]) for name in names}
    exact(receipt.get("files"), expected, f"{role}.files")
    for name, digest in expected.items():
        require(name in FROZEN_INPUT_HASHES, f"{role}.files: unapproved input: {name}")
        exact(digest, FROZEN_INPUT_HASHES[name], f"{role}.files frozen input: {name}")
    exact(receipt.get("findings"), [], f"{role}.findings")
    if role == "blueprint_reviewer":
        exact(receipt.get("baseline"), BASELINE, f"{role}.baseline")
        cycles = receipt.get("repair_cycles")
        require(type(cycles) is int and 0 <= cycles <= 1, f"{role}: repair bound")
    else:
        cycles = receipt.get("repair_cycles")
        require(type(cycles) is int and 0 <= cycles <= 1, f"{role}: P4C repair bound")


def check(root=ROOT, *, ci=False, complete_index=False):
    root = Path(root).resolve()
    contents = {name: read_text(root, name) for name in PROTECTED}
    contract = validate_contract(tomllib.loads(contents[CONTRACT_PATH]))
    exact(tomllib.loads(contents["governance/policy.toml"]), POLICY, "policy")
    state = parse_json(contents["project-state/state.json"])
    validate_state(state)
    validate_live_prerequisites(contents[PREREQUISITE_PATH])
    prior = parse_json(contents["docs/reviews/p4l-merge.json"])
    expected_prior = {
        "schema_version": 1, "role": "historical_merge_observation",
        "observed_on": "2026-11-13", **dependency(),
        "prior_snapshot": "26e8c277ff4f33cb020aebdaedfa7845bcc31cb890912691fae171d38347929d",
        "prior_gate_check": "PASS on merged main using --ci; frozen local P4L receipt remains historical",
        "authority": "Evidence of prior human merge, not authorization for later runtime scopes.",
    }
    exact(prior, expected_prior, "prior merge evidence")
    exact(contents[PROMPT_PATH], render_prompt(contract, contents[TEMPLATE_PATH]), "builder prompt")
    validate_prerequisite(parse_json(contents[RECEIPT_PATHS["blueprint_review"]]),
                          "blueprint_reviewer", ("BLUEPRINT.md", "START_CONSTRUCTION_PROMPT.md"),
                          contents)
    validate_prerequisite(parse_json(contents[RECEIPT_PATHS["prompt_audit"]]), "prompt_auditor",
                          AUDIT_INPUTS, contents)
    entrypoint_cycles = parse_json(contents[RECEIPT_PATHS["blueprint_review"]])["repair_cycles"]
    audit_cycles = parse_json(contents[RECEIPT_PATHS["prompt_audit"]])["repair_cycles"]
    require(entrypoint_cycles <= audit_cycles, "prompt_auditor: P4C repair bound or reset")
    for role in ("blueprint_review", "prompt_audit"):
        agent = parse_json(contents[RECEIPT_PATHS[role]])["agent_id"]
        require(agent == agent.strip() and agent not in state["builder_agents"],
                f"{role}: builder self-review")
    for name in ATTESTATIONS:
        path = safe_path(root, name)
        if path.exists():
            contents[name] = read_text(root, name)
    for name, text in contents.items():
        scan_secrets(name, text)
    return contract, state, contents, inspect_git(root, ci=ci, contents=contents,
                                                 complete_index=complete_index)


def snapshot(contents):
    aggregate = hashlib.sha256()
    hashes = {}
    for name in PROTECTED:
        content = contents[name].encode("utf-8")
        hashes[name] = hashlib.sha256(content).hexdigest()
        # Length framing makes even embedded NULs unambiguous; Git HEAD is not an input.
        aggregate.update(name.encode("utf-8") + b"\0" + str(len(content)).encode("ascii") +
                         b"\0" + content + b"\0")
    return aggregate.hexdigest(), hashes


def make_bundle(checked):
    contract, _state, contents, changed = checked
    snapshot_id, hashes = snapshot(contents)
    return {"schema_version": 1, "package_id": "P4C", "baseline": contract["baseline"],
            "branch": contract["branch"], "snapshot_id": snapshot_id, "hash_algorithm": ALGORITHM,
            "protected_file_hashes": hashes, "changed_paths": changed,
            "attestation_paths": list(ATTESTATIONS), "scope_result": "PASS"}


def bundle(root=ROOT, *, ci=False):
    return make_bundle(check(root, ci=ci))


def validate_attestations(checked, current):
    contract, state, contents, _changed = checked
    require(state["package_status"] == "implemented_unreleased", "gate: state is still building")
    receipts = []
    for name in ATTESTATIONS:
        require(name in contents, f"missing required receipt: {name}")
        receipts.append(parse_json(contents[name]))
    saved, validation, review, release = receipts
    keys(saved, current, "bundle receipt")
    # Paths may move from untracked to committed after review; neither status nor HEAD is approval.
    for key, value in current.items():
        if key != "changed_paths":
            exact(saved[key], value, f"bundle receipt.{key}")
    require(type(saved["changed_paths"]) is list, "bundle receipt.changed_paths: expected list")
    require(all(type(name) is str and name in ALLOWED for name in saved["changed_paths"]),
            "bundle receipt.changed_paths: out of scope")
    exact(saved["changed_paths"], sorted(set(saved["changed_paths"])), "bundle receipt.changed_paths")
    common = {"schema_version": 1, "snapshot_id": current["snapshot_id"]}
    exact(validation, {**common, "role": "builder_validation", "verdict": "PASS",
                       "commands": [{"command": command, "exit_code": 0}
                                    for command in contract["required_validation"]],
                       "python_version": "3.12.10", "remote_ci": "NOT_RUN_AT_SNAPSHOT"},
          "validation receipt")
    keys(review, (*common, "role", "verdict", "agent_id", "read_only", "findings", "repair_cycles"),
         "review receipt")
    for key, value in {**common, "role": "reviewer", "verdict": "PASS", "read_only": True,
                       "findings": []}.items():
        exact(review[key], value, f"review receipt.{key}")
    cycles = review["repair_cycles"]
    audit_cycles = parse_json(contents[RECEIPT_PATHS["prompt_audit"]])["repair_cycles"]
    require(type(cycles) is int and audit_cycles <= cycles <= 1, "review receipt: P4C repair bound or reset")
    keys(release, (*common, "role", "verdict", "agent_id", "read_only", "remote_ci", "pr_url",
                   "merge_status", "vps_sync", "next_gate"), "release receipt")
    for key, value in {**common, "role": "release_checker", "read_only": True,
                       "verdict": LOCAL_RELEASE, "remote_ci": "NOT_RUN_AT_SNAPSHOT",
                       "pr_url": None, "merge_status": "not_merged", "vps_sync": "not_requested",
                       "next_gate": NEXT_GATE}.items():
        exact(release[key], value, f"release receipt.{key}")
    for role, receipt in (("reviewer", review), ("release_checker", release)):
        nonempty(receipt["agent_id"], f"{role}.agent_id")
        require(receipt["agent_id"] == receipt["agent_id"].strip() and
                receipt["agent_id"] not in state["builder_agents"], f"{role}: builder self-review")
    require(release["agent_id"] != review["agent_id"],
            "release_checker: Tier D needs a separate independent release agent")
    # Identity strings bind records, not authenticity. Actual independence needs external audit.


def gate(root=ROOT, *, ci=False):
    checked = check(root, ci=ci, complete_index=True)
    validate_attestations(checked, make_bundle(checked))
    return LOCAL_RELEASE


def main(argv=None, *, root=ROOT):
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("check", "prompt", "bundle", "gate"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--ci", action="store_true",
                               help="skip local branch identity only; retain baseline/scope/permission checks")
        if command == "prompt":
            subparser.add_argument("--verify", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "prompt":
            contract = validate_contract(tomllib.loads(read_text(root, CONTRACT_PATH)))
            rendered = render_prompt(contract, read_text(root, TEMPLATE_PATH))
            if args.verify:
                exact(read_text(root, PROMPT_PATH), rendered, "builder prompt")
                print(f"PASS ({GOVERNANCE_ONLY})")
            else:
                print(rendered, end="")
        elif args.command == "check":
            check(root, ci=args.ci)
            ci_note = "; CI skips local branch identity only" if args.ci else ""
            print(f"PASS ({GOVERNANCE_ONLY}{ci_note})")
        elif args.command == "bundle":
            print(json.dumps(bundle(root, ci=args.ci), indent=2, sort_keys=True))
        else:
            print(f"{gate(root, ci=args.ci)} ({GOVERNANCE_ONLY})")
        return 0
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"BLOCK: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
