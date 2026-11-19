"""P4D exact-snapshot governance; two separately released checkpoints (Python 3.12)."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import re
import string
import subprocess
import sys
import tomllib


ROOT = Path(__file__).resolve().parents[1]
BASELINE = "3cceca6e1534484b9001db16644a98b7b068897b"
BRANCH = "stage1-baostock-protocol-recovery"
ALGORITHM = "sha256-path-and-lf-normalized-utf8-v1"
CONTRACT_PATH = "contracts/p4d_protocol_recovery.toml"
TEMPLATE_PATH = "templates/p4d_builder_prompt.md"
PROMPT_PATH = "docs/reviews/p4d-builder-prompt.md"
PREREQUISITE_PATH = "docs/evidence/live-integration-prerequisites.json"
PREREQUISITE_HASH = "55fd89e11f1b771c40805ef58eed2b8dedbdbb7686cedf6f3b183526aaa3f2e3"
SOURCE_PROBE_PATH = "docs/evidence/p4s-source-probe.json"
SOURCE_PROBE_HASH = "d6169de44000cafe648fb87bc300de498f593bbfae11b24dd992e4c37d258af1"
SOURCE_PROBE_DIGEST = "9d7225b06ff7ecc0631c94866e9fad6d932e062f00c7bd8017203282cbf279b4"
SOURCE_SNAPSHOT = "5f3ddda6f07ed608b59bf57c3e58add7b5caf7904613d9a4d3efa2c7afdb3c42"
PREPARATION_PATH = "docs/evidence/p4e-preparation.json"
PREPARATION_HASH = "609501a5e1a085dd060637a53048df802b69e4ffe80a0acb4a3155ba3542f5a8"
PREPARATION_SNAPSHOT = "2acc872a09ae032b2ceb1cb7b031cfb562672e3e4ac0f7a2adc14de8aa877dfe"
AUDIT_AGENT = "01a1f31e-108d-7603-959f-604c54e690ea"
MARKET_ATTESTATIONS = (
    "docs/reviews/p4b-bundle.json",
    "docs/reviews/p4b-validation.json",
    "docs/reviews/p4b-review.json",
    "docs/reviews/p4b-pre-probe-release.json",
    "docs/reviews/p4b-release.json",
    "docs/evidence/p4b-market-observation.json",
)
# Historical NBS records are audit inputs, never current exemptions or capabilities.
NBS_ATTESTATIONS = tuple([
    "docs/reviews/p4n-bundle.json",
    "docs/reviews/p4n-validation.json",
    "docs/reviews/p4n-review.json",
    "docs/reviews/p4n-pre-probe-release.json",
    "docs/reviews/p4n-release.json",
    "docs/evidence/p4n-nbs-observation.json"
])
# Historical public bindings support pure P4E audits, never current exemptions.
ATTESTATIONS = (
    "docs/reviews/p4e-bundle.json", "docs/reviews/p4e-validation.json",
    "docs/reviews/p4e-review.json", "docs/reviews/p4e-release.json",
    "docs/evidence/p4e-preparation.json",
)
BASELINE_ALLOWED = (
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
    "config/p4b_sdk_manifest.json",
    "config/sources.toml",
    "contracts/p0_stage0.toml",
    "contracts/p1_sources_evidence.toml",
    "contracts/p2_intelligence.toml",
    "contracts/p3_research.toml",
    "contracts/p4_live_integration_entry.toml",
    "contracts/p4_publication.toml",
    "contracts/p4b_baostock_live_evidence.toml",
    "contracts/p4c_offline_workflow.toml",
    "contracts/p4e_evidence_preparation.toml",
    "contracts/p4i_real_evidence_composition.toml",
    "contracts/p4m_daily_market_evidence.toml",
    "contracts/p4n_nbs_ingestion.toml",
    "contracts/p4s_free_sources.toml",
    "docs/BAOSTOCK_LIVE_EVIDENCE.md",
    "docs/CONSTRUCTION_LOG.md",
    "docs/DAILY_MARKET_EVIDENCE.md",
    "docs/EVIDENCE_PREPARATION.md",
    "docs/FREE_SOURCE_COLLECTION.md",
    "docs/LEGACY_REUSE_MANIFEST.md",
    "docs/LIVE_INTEGRATION_PLAN.md",
    "docs/NBS_EVIDENCE_INGESTION.md",
    "docs/OFFLINE_WORKFLOW_PLAN.md",
    "docs/P1_DESIGN.md",
    "docs/P2_METHODS.md",
    "docs/P3_METHODS.md",
    "docs/P4_METHODS.md",
    "docs/PUBLICATION_PROVENANCE.json",
    "docs/REAL_EVIDENCE_COMPOSITION.md",
    "docs/SOURCE_MATRIX.md",
    "docs/evidence/live-integration-prerequisites.json",
    "docs/evidence/p1-source-probe.json",
    "docs/evidence/p2-demo.json",
    "docs/evidence/p3-demo.json",
    "docs/evidence/p4-demo.json",
    "docs/evidence/p4-report.txt",
    "docs/evidence/p4b-market-observation.json",
    "docs/evidence/p4b-user-authorization.json",
    "docs/evidence/p4e-preparation.json",
    "docs/evidence/p4n-nbs-observation.json",
    "docs/evidence/p4s-source-probe.json",
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
    "docs/reviews/p4b-builder-prompt.md",
    "docs/reviews/p4b-bundle.json",
    "docs/reviews/p4b-entrypoint-review.json",
    "docs/reviews/p4b-pre-probe-release.json",
    "docs/reviews/p4b-prompt-audit.json",
    "docs/reviews/p4b-release.json",
    "docs/reviews/p4b-review.json",
    "docs/reviews/p4b-validation.json",
    "docs/reviews/p4c-builder-prompt.md",
    "docs/reviews/p4c-bundle.json",
    "docs/reviews/p4c-entrypoint-review.json",
    "docs/reviews/p4c-merge.json",
    "docs/reviews/p4c-prompt-audit.json",
    "docs/reviews/p4c-release.json",
    "docs/reviews/p4c-review.json",
    "docs/reviews/p4c-validation.json",
    "docs/reviews/p4e-builder-prompt.md",
    "docs/reviews/p4e-bundle.json",
    "docs/reviews/p4e-entrypoint-review.json",
    "docs/reviews/p4e-merge.json",
    "docs/reviews/p4e-prompt-audit.json",
    "docs/reviews/p4e-release.json",
    "docs/reviews/p4e-review.json",
    "docs/reviews/p4e-validation.json",
    "docs/reviews/p4i-builder-prompt.md",
    "docs/reviews/p4i-bundle.json",
    "docs/reviews/p4i-entrypoint-review.json",
    "docs/reviews/p4i-merge.json",
    "docs/reviews/p4i-prompt-audit.json",
    "docs/reviews/p4i-release.json",
    "docs/reviews/p4i-review.json",
    "docs/reviews/p4i-validation.json",
    "docs/reviews/p4l-builder-prompt.md",
    "docs/reviews/p4l-bundle.json",
    "docs/reviews/p4l-entrypoint-review.json",
    "docs/reviews/p4l-merge.json",
    "docs/reviews/p4l-prompt-audit.json",
    "docs/reviews/p4l-release.json",
    "docs/reviews/p4l-review.json",
    "docs/reviews/p4l-validation.json",
    "docs/reviews/p4m-builder-prompt.md",
    "docs/reviews/p4m-bundle.json",
    "docs/reviews/p4m-entrypoint-review.json",
    "docs/reviews/p4m-merge.json",
    "docs/reviews/p4m-prompt-audit.json",
    "docs/reviews/p4m-release.json",
    "docs/reviews/p4m-review.json",
    "docs/reviews/p4m-validation.json",
    "docs/reviews/p4n-builder-prompt.md",
    "docs/reviews/p4n-bundle.json",
    "docs/reviews/p4n-entrypoint-review.json",
    "docs/reviews/p4n-merge.json",
    "docs/reviews/p4n-pre-probe-release.json",
    "docs/reviews/p4n-prompt-audit.json",
    "docs/reviews/p4n-release.json",
    "docs/reviews/p4n-review.json",
    "docs/reviews/p4n-validation.json",
    "docs/reviews/p4s-builder-prompt.md",
    "docs/reviews/p4s-bundle.json",
    "docs/reviews/p4s-entrypoint-review.json",
    "docs/reviews/p4s-merge.json",
    "docs/reviews/p4s-prompt-audit.json",
    "docs/reviews/p4s-release.json",
    "docs/reviews/p4s-review.json",
    "docs/reviews/p4s-validation.json",
    "governance/policy.toml",
    "market_intelligence/__init__.py",
    "market_intelligence/__main__.py",
    "market_intelligence/baostock_capture.py",
    "market_intelligence/baostock_transport.py",
    "market_intelligence/collection.py",
    "market_intelligence/cost_guard.py",
    "market_intelligence/daily_market.py",
    "market_intelligence/delivery.py",
    "market_intelligence/evaluation.py",
    "market_intelligence/gate.py",
    "market_intelligence/intelligence.py",
    "market_intelligence/ledger.py",
    "market_intelligence/market_state.py",
    "market_intelligence/models.py",
    "market_intelligence/nbs.py",
    "market_intelligence/news_events.py",
    "market_intelligence/preparation.py",
    "market_intelligence/publication.py",
    "market_intelligence/research.py",
    "market_intelligence/runtime.py",
    "market_intelligence/scenario.py",
    "market_intelligence/source_policy.py",
    "market_intelligence/sources.py",
    "market_intelligence/store.py",
    "market_intelligence/workflow.py",
    "project-state/state.json",
    "requirements-baostock.lock",
    "requirements.lock",
    "templates/builder_prompt.md",
    "templates/p1_builder_prompt.md",
    "templates/p2_builder_prompt.md",
    "templates/p3_builder_prompt.md",
    "templates/p4_builder_prompt.md",
    "templates/p4b_builder_prompt.md",
    "templates/p4c_builder_prompt.md",
    "templates/p4e_builder_prompt.md",
    "templates/p4i_builder_prompt.md",
    "templates/p4l_builder_prompt.md",
    "templates/p4m_builder_prompt.md",
    "templates/p4n_builder_prompt.md",
    "templates/p4s_builder_prompt.md",
    "tests/fixtures/p1_feed.xml",
    "tests/fixtures/p1_market.csv",
    "tests/fixtures/p2_input.json",
    "tests/fixtures/p3_responses.json",
    "tests/governance/test_governance.py",
    "tests/product/test_baostock_capture.py",
    "tests/product/test_baostock_transport.py",
    "tests/product/test_collection.py",
    "tests/product/test_daily_market.py",
    "tests/product/test_nbs.py",
    "tests/product/test_p1.py",
    "tests/product/test_p2.py",
    "tests/product/test_p3.py",
    "tests/product/test_p4.py",
    "tests/product/test_preparation.py",
    "tests/product/test_real_evidence.py",
    "tests/product/test_scenario.py",
    "tests/product/test_workflow.py",
    "tools/governance.py",
)
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
MARKET_VALIDATION_COMMANDS = [
    "python -m pip check",
    "python -m unittest discover -s tests/product -p test_baostock_transport.py -v",
    "python -m unittest discover -s tests/product -p test_baostock_capture.py -v",
    "python -m unittest discover -s tests/governance -p test_*.py -v",
    "python -m unittest discover -s tests/product -p test_*.py -v",
    "python tools/governance.py check",
    "python tools/governance.py prompt --verify",
    "python tools/governance.py bundle",
    "git diff --check",
]
MARKET_REQUIRED_READS = [
    "AGENTS.md",
    "BLUEPRINT.md",
    "START_CONSTRUCTION_PROMPT.md",
    "contracts/p4b_baostock_live_evidence.toml",
    "governance/policy.toml",
    "project-state/state.json",
    "docs/BAOSTOCK_LIVE_EVIDENCE.md",
    "docs/evidence/p4b-user-authorization.json",
    "requirements-baostock.lock",
    "config/p4b_sdk_manifest.json",
    "docs/reviews/p4m-merge.json",
    "README.md",
    "docs/CONSTRUCTION_LOG.md",
    "contracts/p4m_daily_market_evidence.toml",
    "docs/DAILY_MARKET_EVIDENCE.md",
    "docs/REAL_EVIDENCE_COMPOSITION.md",
    "docs/FREE_SOURCE_COLLECTION.md",
    "docs/NBS_EVIDENCE_INGESTION.md",
    "docs/evidence/live-integration-prerequisites.json",
    "docs/evidence/p4s-source-probe.json",
    "docs/evidence/p4e-preparation.json",
    "docs/evidence/p4n-nbs-observation.json",
    "docs/reviews/p4m-bundle.json",
    "docs/reviews/p4m-validation.json",
    "docs/reviews/p4m-review.json",
    "docs/reviews/p4m-release.json",
]
MARKET_AUDIT_INPUTS = (
    "AGENTS.md",
    "BLUEPRINT.md",
    "START_CONSTRUCTION_PROMPT.md",
    "contracts/p4b_baostock_live_evidence.toml",
    "governance/policy.toml",
    "docs/BAOSTOCK_LIVE_EVIDENCE.md",
    "docs/evidence/p4b-user-authorization.json",
    "requirements-baostock.lock",
    "config/p4b_sdk_manifest.json",
    "docs/reviews/p4m-merge.json",
    "README.md",
    "contracts/p4m_daily_market_evidence.toml",
    "docs/DAILY_MARKET_EVIDENCE.md",
    "docs/REAL_EVIDENCE_COMPOSITION.md",
    "docs/FREE_SOURCE_COLLECTION.md",
    "docs/NBS_EVIDENCE_INGESTION.md",
    "docs/evidence/live-integration-prerequisites.json",
    "docs/evidence/p4s-source-probe.json",
    "docs/evidence/p4e-preparation.json",
    "docs/evidence/p4n-nbs-observation.json",
    "docs/reviews/p4m-bundle.json",
    "docs/reviews/p4m-validation.json",
    "docs/reviews/p4m-review.json",
    "docs/reviews/p4m-release.json",
    "templates/p4b_builder_prompt.md",
    "docs/reviews/p4b-builder-prompt.md",
)
# Actual independently approved bytes, never hashes trusted from a mutable receipt.
MARKET_FROZEN_INPUT_HASHES = {
    "AGENTS.md": "d14de09f5007bf518b1d85ff55c18155117caa01582db0618871316b81b64a79",
    "BLUEPRINT.md": "168111643ac2de093adba8ca852434a2e5fc161fb8e1e6e0e1b801ebd23d1138",
    "START_CONSTRUCTION_PROMPT.md": "1daaa1a124a897eb71df07547e1b805cb5b2cac5917fe9e15a999b7d713f41d2",
    "contracts/p4b_baostock_live_evidence.toml": "dc427afac2115f50246c2f50296fc9f777e7c8046b60b3c28434d13ea71d23c5",
    "governance/policy.toml": "09750e0d673aa0cf5688975b5850c88bee5d963b8e4e84fb8cea6167aa798520",
    "docs/BAOSTOCK_LIVE_EVIDENCE.md": "a91976c4c2f8bb2fe02fbca2dee6ff2a82d09b2c8060ee4bc1bec12dc769ac00",
    "docs/evidence/p4b-user-authorization.json": "e335bd5f6f788dc8862ca7bcc509ee1cdb6b38f3460627f8af07312ee0ec28c1",
    "requirements-baostock.lock": "cd6dc4771410af97e5dee2cee786936ba154aecc9d52194d9d3918a057d0e07a",
    "config/p4b_sdk_manifest.json": "f4fa91e3cb84ca72e5ea106d131dba0550682c3c1f1e9cc28b2e7016ba15ea7f",
    "docs/reviews/p4m-merge.json": "8eb19eae843bf994a135f048cf70e289b3e06fc35d0b8719d32caf323da5ba92",
    "README.md": "814b8152fafce3b197ec87ea3436b5145edbcf6813dc9857c17b58a3a453d028",
    "contracts/p4m_daily_market_evidence.toml": "2a25c3d6fda001487a7785996d6fc635188b8d0e3e769645952d842de5ec42e2",
    "docs/DAILY_MARKET_EVIDENCE.md": "d93bdbcd806b15063a70f56d454cb6fa39d5e4c8325a9538e1662ac03361660f",
    "docs/REAL_EVIDENCE_COMPOSITION.md": "5ea32e3f7afec88796dbc017c283b5f595c777fdfebe8d3dbe2d88c6f2c07a31",
    "docs/FREE_SOURCE_COLLECTION.md": "76e2c6cfead182462d11a949f0cfec58ffad1b5bc66337921cc84074434780e5",
    "docs/NBS_EVIDENCE_INGESTION.md": "4556b010d1e0ea1e7879b297e92d904bc9b6c8dbbd47047561c28d10de082727",
    "docs/evidence/live-integration-prerequisites.json": "55fd89e11f1b771c40805ef58eed2b8dedbdbb7686cedf6f3b183526aaa3f2e3",
    "docs/evidence/p4s-source-probe.json": "d6169de44000cafe648fb87bc300de498f593bbfae11b24dd992e4c37d258af1",
    "docs/evidence/p4e-preparation.json": "609501a5e1a085dd060637a53048df802b69e4ffe80a0acb4a3155ba3542f5a8",
    "docs/evidence/p4n-nbs-observation.json": "d02bf2a4213874ff8057df2fdc8ec065fb700ca6cd2500970dd4feb589dd92a1",
    "docs/reviews/p4m-bundle.json": "d10529a55caa83eab4fc430848bfee7b48c7a6f9e44bd0c0c1c2c49f58cafabe",
    "docs/reviews/p4m-validation.json": "51f588d307cea379b5919b4737c7660b00b4a2f1c2d8537caec97ce33d465bda",
    "docs/reviews/p4m-review.json": "35ee63f03d166ac360678c26b69947077c14072b7fece9dd152261db291b2e3d",
    "docs/reviews/p4m-release.json": "2b641a59bdfd4c356998c69e579d335648cfbdcaf2a9dc88c517601e8187ab07",
    "templates/p4b_builder_prompt.md": "21e4cf74d382c99f1d264025ed8fbeb20b47f059fcc4b629c9af54ffa525b1d5",
    "docs/reviews/p4b-builder-prompt.md": "d9f35ac355fe7a22c1cef19daa5d4302b5fa7610fd58dd91b781b10328b6da2f",
}


def dependency():
    return {
        "package_id": "P4B",
        "pr_url": "https://github.com/kamrean/a-share-market-intelligence-public/pull/13",
        "merge_commit": BASELINE,
        "merged_at": "2026-11-19T08:16:45Z",
        "tested_head": "7454205f5a8737ea60aafe3469c96211659e8d66",
        "pr_ci_run": 44481051951,
        "merge_ci_run": 44486260610,
        "ci_status": "PASS",
        "merge_status": "merged",
    }


LIMITED_INGESTION = {
    "source_id": "nbs_latest", "endpoint": "https://www.stats.gov.cn/sj/zxfb/rss.xml",
    "method": "GET", "maximum_requests": 1, "retries": 0,
    "maximum_response_bytes": 512000, "deadline_seconds": 10, "maximum_items": 100,
    "normalized_retention_seconds": 86400, "input_store": "p4n-nbs-ingestion.sqlite",
    "raw_payload_persistence": False, "article_or_attachment_fetch": False,
    "requires_key": False, "requires_registration": False, "paid": False,
    "requires_user_opt_in": True, "requires_independent_review": True,
    "requires_pre_probe_release": True, "source_as_of": "actual_capture_time",
    "unknown_authorship": "reject_without_title_retention", "unknown_publication_timezone": "reject",
    "authorization_basis": "User explicitly authorized this one NBS RSS read-only test after "
                           "Reviewer and Release Checker; anonymous sources only. No model/Telegram/trading/VPS.",
}

REPAIR_EXCEPTION = {
    "id": "p4n-channel-rights-r1", "authorized_by": "user",
    "authorization": "User explicitly authorized one additional bounded R1 repair after the P4N channel-rights BLOCK.",
    "additional_cycles": 1, "maximum_total": 2,
    "blocked_snapshot": "2ff1ca0f9a5e2dc97e81372a2c556707810fc03e7528ba90bb2a1566cee4d06d",
    "blocked_review_digest": "8b57fc068fba730d89c706911a96b1cb5721ca47fe619ee23a6264f1362e23c2",
    "reviewer_agent": "01a1eec4-2066-76f1-9ba5-7dad5674a563",
    "finding": "P1: unknown namespaced or nested channel-level rights restrictions are ignored.",
    "product_paths": ["market_intelligence/nbs.py", "tests/product/test_nbs.py"],
    "scope": "Reject ambiguous channel-level rights without retaining titles; add regression tests. "
             "No operational permission, data-source, file allowlist or merge-policy expansion.",
}

NBS_POLICY = {
    "schema_version": 1, "active_package": "P4N", "python_version": "3.12",
    "merge_tier": "D", "human_merge_required": True, "auto_merge_allowed": False,
    "direct_merge_allowed": False, "admin_bypass_allowed": False,
    "remote_creation_authorized": False, "remote_ci_status": "NOT_RUN_AT_SNAPSHOT",
    "repository": REPOSITORY, "max_repair_cycles": 1,
    "reviewer_must_be_independent": True, "construction_subagents_allowed": True,
    "permissions": PERMISSIONS, "limited_ingestion": LIMITED_INGESTION,
    "repair_exception": REPAIR_EXCEPTION,
}
LIMITED_MARKET_INGESTION = {
    "source_id": "baostock_standard_free",
    "endpoint": "tcp://public-api.baostock.com:10030",
    "tls": False,
    "peer_authenticated": False,
    "sdk_version": "0.9.4",
    "sdk_wheel_sha256": "0bf71c6069ab5890ff3596632f9c3f8f1fbc6bfcac582c2f9d6a5c11ab2cfaf8",
    "dependency_lock": "requirements-baostock.lock",
    "sdk_manifest": "config/p4b_sdk_manifest.json",
    "user_authorization": "docs/evidence/p4b-user-authorization.json",
    "symbols": [
        "sh.000001",
        "sz.399001",
    ],
    "benchmark_universe": [
        "000001.SH",
        "399001.SZ",
        "399006.SZ",
        "KOSPI",
        "SP500",
    ],
    "allowed_operations": [
        "login",
        "query_trade_dates",
        "query_stock_basic",
        "query_history_k_data_plus",
        "logout",
    ],
    "planned_requests": 7,
    "maximum_requests": 12,
    "maximum_sessions": 1,
    "maximum_connections": 1,
    "retries": 0,
    "deadline_seconds": 20,
    "deadline_includes_dns": True,
    "maximum_wire_bytes": 512000,
    "maximum_frame_bytes": 128000,
    "maximum_decoded_bytes": 1048576,
    "window_natural_days": 14,
    "window_end": "prior_Asia_Shanghai_calendar_day",
    "automatic_pagination": False,
    "reconnect_allowed": False,
    "endpoint_override_allowed": False,
    "raw_payload_persistence": False,
    "normalized_payload_persistence": False,
    "derived_payload_persistence": False,
    "payload_exports_allowed": False,
    "payload_lifetime": "bounded_process_memory_only",
    "journal_home_relative": ".a-share-market-intelligence/p4b-baostock-test.sqlite",
    "journal_payload_tables_empty": True,
    "journal_reset_allowed": False,
    "allowance_consumed_on": "durable_session_reservation_before_any_dispatch",
    "retained_results": "closed_schema_sanitized_metadata_only",
    "requires_key": False,
    "requires_registration": False,
    "paid": False,
    "requires_user_opt_in": True,
    "requires_independent_review": True,
    "requires_pre_probe_release": True,
    "requires_exact_snapshot_market_gate": True,
    "market_gate_accepts_ci": False,
    "authorization_basis": "Current protected user authorization permits this one zero-paid anonymous session only after independent Reviewer and pre-probe Release; results only, all market payloads and intermediates RAM-only.",
}

MARKET_POLICY = {
    "schema_version": 1, "active_package": "P4B", "python_version": "3.12",
    "merge_tier": "D", "human_merge_required": True, "auto_merge_allowed": False,
    "direct_merge_allowed": False, "admin_bypass_allowed": False,
    "remote_creation_authorized": False, "remote_ci_status": "NOT_RUN_AT_SNAPSHOT",
    "repository": REPOSITORY, "max_repair_cycles": 1,
    "reviewer_must_be_independent": True, "construction_subagents_allowed": True,
    "permissions": PERMISSIONS, "limited_market_ingestion": LIMITED_MARKET_INGESTION,
}
NBS_RECEIPT_PATHS = {
    "blueprint_review": "docs/reviews/p4n-entrypoint-review.json",
    "prompt_audit": "docs/reviews/p4n-prompt-audit.json", "builder_prompt": "docs/reviews/p4n-builder-prompt.md",
    **dict(zip(("bundle", "validation", "reviewer", "pre_probe_release", "release", "observation"),
               NBS_ATTESTATIONS)),
}
MARKET_RECEIPT_PATHS = {
    "blueprint_review": "docs/reviews/p4b-entrypoint-review.json",
    "prompt_audit": "docs/reviews/p4b-prompt-audit.json", "builder_prompt": "docs/reviews/p4b-builder-prompt.md",
    **dict(zip(("bundle", "validation", "reviewer", "pre_probe_release", "release", "observation"),
               MARKET_ATTESTATIONS)),
}
RECEIPT_PATHS = {
    "blueprint_review": "docs/reviews/p4e-entrypoint-review.json",
    "prompt_audit": "docs/reviews/p4e-prompt-audit.json",
    "builder_prompt": "docs/reviews/p4e-builder-prompt.md",
    **dict(zip(("bundle", "validation", "reviewer", "release", "preparation"), ATTESTATIONS)),
}
NBS_NEXT_GATE = ("Human merge after exact-head CI and independent remote release; retain anonymous-only "
                 "five-index gaps, then separately audited model/report/private-delivery integration. "
                 "No inherited network or VPS permission.")
MARKET_NEXT_GATE = "Human merge after exact-head CI and independent remote release; resolve remaining anonymous sources and genuine model/report/private-delivery acceptance. The one-shot market allowance is consumed after any reserved attempt and never authorizes retries, scheduling or VPS."
LOCAL_RELEASE = "LOCAL_READY_FOR_PR"
NBS_PRE_PROBE_RELEASE = "LOCAL_READY_FOR_NBS_PROBE"
PRE_PROBE_RELEASE = "LOCAL_READY_FOR_BAOSTOCK_PROBE"
# Historical receipt vocabulary only; no pre-preparation validator or capability.
PRE_PREPARATION_RELEASE = "LOCAL_READY_FOR_LOCAL_PREPARATION"
GOVERNANCE_ONLY = ("Bounded market governance only; model/live-readiness-blocked: "
                   "BLOCKED_EXTERNAL_PREREQUISITES")


# P4D scope is pinned in code, never granted by a mutable contract.
MUTABLE_PATHS = (
    "AGENTS.md", "START_CONSTRUCTION_PROMPT.md", "README.md",
    "docs/CONSTRUCTION_LOG.md", "governance/policy.toml", "project-state/state.json",
    ".github/workflows/governance.yml", "tools/governance.py",
    "tests/governance/test_governance.py", "market_intelligence/baostock_transport.py",
    "tests/product/test_baostock_transport.py",
)
NEW_PATHS = (
    "contracts/p4d_protocol_recovery.toml", "docs/BAOSTOCK_PROTOCOL_RECOVERY.md",
    "docs/evidence/p4d-user-authorization.json", "docs/reviews/p4b-merge.json",
    "templates/p4d_builder_prompt.md", "docs/reviews/p4d-builder-prompt.md",
    "docs/reviews/p4d-entrypoint-review.json", "docs/reviews/p4d-prompt-audit.json",
    "market_intelligence/baostock_recovery.py", "tests/product/test_baostock_recovery.py",
    "docs/reviews/p4d-diagnostic-bundle.json", "docs/reviews/p4d-diagnostic-validation.json",
    "docs/reviews/p4d-diagnostic-review.json", "docs/reviews/p4d-diagnostic-release.json",
    "docs/evidence/p4d-diagnostic.json", "docs/evidence/p4d-repair-basis.json",
    "docs/reviews/p4d-bundle.json", "docs/reviews/p4d-validation.json",
    "docs/reviews/p4d-review.json", "docs/reviews/p4d-pre-probe-release.json",
    "docs/evidence/p4d-market-observation.json", "docs/reviews/p4d-release.json",
)
DIAGNOSTIC_ATTESTATIONS = NEW_PATHS[10:15]
REPAIR_BASIS_PATH = NEW_PATHS[15]
VERIFICATION_ATTESTATIONS = NEW_PATHS[16:]
# Compatibility name denotes all non-decision artifacts during phase A.
CURRENT_ATTESTATIONS = (*DIAGNOSTIC_ATTESTATIONS, REPAIR_BASIS_PATH,
                        *VERIFICATION_ATTESTATIONS)
ALLOWED = tuple(sorted((*BASELINE_ALLOWED, *NEW_PATHS)))
IMMUTABLE = tuple(sorted(set(BASELINE_ALLOWED) - set(MUTABLE_PATHS)))
PROTECTED = tuple(sorted(set(ALLOWED) - set(CURRENT_ATTESTATIONS)))
CURRENT_RECEIPT_PATHS = {
    "blueprint_review": "docs/reviews/p4d-entrypoint-review.json",
    "prompt_audit": "docs/reviews/p4d-prompt-audit.json", "builder_prompt": PROMPT_PATH,
    **dict(zip(("diagnostic_bundle", "diagnostic_validation", "diagnostic_review",
                "diagnostic_release", "diagnostic"), DIAGNOSTIC_ATTESTATIONS)),
    "repair_basis": REPAIR_BASIS_PATH,
    **dict(zip(("bundle", "validation", "reviewer", "pre_probe_release",
                "observation", "release"), VERIFICATION_ATTESTATIONS)),
}
LIMITED_PROTOCOL_RECOVERY = {
    "user_authorization": "docs/evidence/p4d-user-authorization.json",
    "source_id": "baostock_standard_free", "endpoint": "tcp://public-api.baostock.com:10030",
    "sdk_version": "0.9.4", "dependency_lock": "requirements-baostock.lock",
    "sdk_manifest": "config/p4b_sdk_manifest.json", "tls": False, "peer_authenticated": False,
    "maximum_sessions": 2, "maximum_total_requests": 9, "diagnostic_requests": 1,
    "verification_requests": 7, "maximum_connections_per_session": 1, "retries": 0,
    "deadline_seconds": 20, "maximum_wire_bytes": 512000, "maximum_frame_bytes": 128000,
    "maximum_decoded_bytes": 1048576, "symbols": ["sh.000001", "sz.399001"],
    "window_natural_days": 14, "window_end": "prior_Asia_Shanghai_calendar_day",
    "requires_key": False, "requires_registration": False, "paid": False,
    "payload_lifetime": "bounded_process_memory_only",
    "retained_results": "closed_schema_sanitized_metadata_only",
    "journal_home_relative": ".a-share-market-intelligence/p4d-baostock-recovery.sqlite",
    "journal_reset_allowed": False, "old_journal_mutation_allowed": False,
    "allowance_consumed_on": "durable_phase_reservation_before_any_dispatch",
    "unknown_consumes_allowance": True, "unused_capacity_authorizes_extra_requests": False,
    "phase_transition_automatic": False, "ci_network_gate_allowed": False,
}
POLICY = {key: value for key, value in MARKET_POLICY.items()
          if key != "limited_market_ingestion"}
POLICY.update(active_package="P4D", limited_protocol_recovery=LIMITED_PROTOCOL_RECOVERY)
NEXT_GATE = ("AWAITING_HUMAN_MERGE after truthful terminal results, independent final Release "
             "and exact-head CI; any inconclusive diagnosis or failed verification remains an "
             "explicit source gap. No new session, model, Telegram, schedule or VPS authority.")
PHASE_RELEASE = {
    "diagnostic": "LOCAL_READY_FOR_PROTOCOL_DIAGNOSTIC",
    "verification": "LOCAL_READY_FOR_RECOVERY_VERIFICATION",
}
REQUIRED_READS = [
    "AGENTS.md", "START_CONSTRUCTION_PROMPT.md", "BLUEPRINT.md",
    "project-state/state.json", "governance/policy.toml", CONTRACT_PATH,
    "docs/BAOSTOCK_PROTOCOL_RECOVERY.md", "docs/evidence/p4d-user-authorization.json",
    "docs/reviews/p4b-merge.json", "docs/evidence/p4b-market-observation.json",
    "docs/reviews/p4b-review.json", "docs/reviews/p4b-release.json",
    "requirements-baostock.lock", "config/p4b_sdk_manifest.json",
]
VALIDATION_COMMANDS = [
    "python -m pip check",
    "python -m unittest discover -s tests/product -p test_baostock_transport.py -v",
    "python -m unittest discover -s tests/product -p test_baostock_recovery.py -v",
    "python -m unittest discover -s tests/governance -p test_*.py -v",
    "python -m unittest discover -s tests/product -p test_*.py -v",
    "python tools/governance.py check", "python tools/governance.py prompt --verify",
    "python tools/governance.py bundle",
    "git diff --check 3cceca6e1534484b9001db16644a98b7b068897b",
]
CONTRACT_DIGEST = "8443d82d3870eef117bdcd1f9ceafcfb35f449bbc38a0f864a73259babf23d86"
FROZEN_INPUT_HASHES = {
    "AGENTS.md": "1bc93a9feb32e3d8d497220b17f46c47de785ee55f5d1aa5617ec4926f511fd9",
    "START_CONSTRUCTION_PROMPT.md": "fd2b04e1dd5298bb7084a752ee379183e352a3edf438560c73e406c4a9b5227b",
    "BLUEPRINT.md": "168111643ac2de093adba8ca852434a2e5fc161fb8e1e6e0e1b801ebd23d1138",
    "governance/policy.toml": "df40f9f59d3b7a9ac157e729ec0fd36d263fd5f4af54f8e35dd5dc3e366d14fb",
    "contracts/p4d_protocol_recovery.toml": "45474e00bc5aba39eb9c70489f858d2dd0859a59d7961669ce81723a86498f8b",
    "docs/BAOSTOCK_PROTOCOL_RECOVERY.md": "7576d826f996d153c4562cfc144fcff915722deea1e111b9bb421f3995f5f7ec",
    "docs/evidence/p4d-user-authorization.json": "7c0a027b9c293b76a7f7f67c14a6d17039c7cacdb5121535b4f4db8e2d8d5872",
    "docs/reviews/p4b-merge.json": "87295b9435c27f076be1f38b49f0ff709cb8e40861bf07e5627bab69f19c3b36",
    "docs/evidence/p4b-market-observation.json": "2d4d9ff1890e9df3de2203c349ee62ccac642a5cada2ba6eb164580e53d492ac",
    "docs/reviews/p4b-review.json": "ac4942e976d778ed6df89e982d2703df6558932c5b611a82eb4a402e4b93c9f1",
    "docs/reviews/p4b-release.json": "a4c261fe4b0fd5fd43a5c52223b1b84ece10c2ff4880edcd6d9abcd81dfade2d",
    "requirements-baostock.lock": "cd6dc4771410af97e5dee2cee786936ba154aecc9d52194d9d3918a057d0e07a",
    "config/p4b_sdk_manifest.json": "f4fa91e3cb84ca72e5ea106d131dba0550682c3c1f1e9cc28b2e7016ba15ea7f",
    "templates/p4d_builder_prompt.md": "4d81745eed28fb425ece68e5602fe098883e57cb031bc31bf18da148dad25875",
    "docs/reviews/p4d-builder-prompt.md": "0d0f39d9ced0bfa18294ff5e71f51895f48d9c822a2c02e495e0025eeb7a9202",
}
AUDIT_INPUTS = tuple(FROZEN_INPUT_HASHES)


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
        "schema_version": 1, "package_id": "P4D", "stage_ids": [1, 2, 3, 4],
        "branch": BRANCH, "baseline": BASELINE, "merge_tier": "D",
        "human_merge_required": True, "auto_merge_allowed": False,
        "max_repair_cycles": 1, "blueprint_review_required": True,
        "prompt_audit_required": True, "independent_review_required": True,
        "snapshot_algorithm": ALGORITHM, "mutable_paths": list(MUTABLE_PATHS),
        "new_paths": list(NEW_PATHS), "required_reads": REQUIRED_READS,
        "required_validation": VALIDATION_COMMANDS, "permissions": PERMISSIONS,
        "next_gate": NEXT_GATE, "limited_protocol_recovery": LIMITED_PROTOCOL_RECOVERY,
    }
    fields = ("title", "objective", "immutable_rule", "diagnostic_validation", "rollback",
              "review_flow", "implementation")
    keys(contract, (*pinned, *fields), "contract")
    for key, value in pinned.items():
        exact(contract[key], value, f"contract.{key}")
    exact(probe_digest(contract), CONTRACT_DIGEST, "contract approved content")
    return contract

def validate_state(state):
    pinned = {
"schema_version": 1, "active_package": "P4D", "active_contract": CONTRACT_PATH,
        "branch": BRANCH, "baseline": BASELINE,
        "milestones": {"research_mvp": "not_implemented",
                       "forward_observation": "not_started", "vps_release": "not_authorized"},
        "source_readiness": "BLOCKED_MARKET_SOURCES", "dependency": dependency(),
        "github": {"remote_creation_authorized": False, "owner": "kamrean",
                   "repository": "a-share-market-intelligence-public",
                   "visibility": "public", "remote_ci": "NOT_RUN_AT_SNAPSHOT",
                   "merge_status": "not_merged"},
        "receipts": CURRENT_RECEIPT_PATHS,
"next_gate": NEXT_GATE, "limited_protocol_recovery": LIMITED_PROTOCOL_RECOVERY,
    }
    keys(state, (*pinned, "package_status", "builder_agents", "stage_registry", "last_updated",
"live_integration", "phase"),
         "state")
    for key, value in pinned.items():
        exact(state[key], value, f"state.{key}")
    require(type(state["package_status"]) is str and state["package_status"] in
            ("awaiting_implementation", "building", "implemented_unreleased"),
            "state: premature/unknown package status")
    require(type(state["phase"]) is str and state["phase"] in PHASE_RELEASE,
            "state: invalid recovery phase")
    runtime_status = ("protocol_recovery_implemented_model_deferred"
                      if state["package_status"] == "implemented_unreleased"
                      else "protocol_recovery_planned_model_deferred")
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
    marker = "{{CONTRACT_SHA256}}"
    require(template.count(marker) == 1, "builder template: missing contract substitution")
    result = template.replace(marker, FROZEN_INPUT_HASHES[CONTRACT_PATH])
    require("{{" not in result and "}}" not in result, "builder template: invalid substitution")
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
    fields = ("schema_version", "role", "verdict", "read_only", "independent", "agent_id",
              "files", "findings", "repair_cycles")
    keys(receipt, (*fields, "baseline") if role == "blueprint_reviewer" else fields, role)
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
        require(type(cycles) is int and 0 <= cycles <= 1, f"{role}: P4B repair bound")


def validate_historical_probe(text):
    text = normalize_text(text)
    exact(sha256(text), SOURCE_PROBE_HASH, "historical source probe frozen content")
    value = parse_json(text)
    exact(probe_digest(value), SOURCE_PROBE_DIGEST, "historical source probe canonical digest")
    exact(value["snapshot_id"], SOURCE_SNAPSHOT, "historical source probe snapshot")
    exact(value["status"], "RECORDED", "historical source probe status")
    return value


def validate_historical_preparation(text):
    text = normalize_text(text)
    exact(sha256(text), PREPARATION_HASH, "historical preparation frozen content")
    value = parse_json(text)
    exact(value["snapshot_id"], PREPARATION_SNAPSHOT, "historical preparation snapshot")
    exact(value["status"], "RECORDED", "historical preparation status")
    return value


def prior_merge_evidence(root=ROOT):
    text = read_text(root, "docs/reviews/p4b-merge.json")
    exact(sha256(text), FROZEN_INPUT_HASHES["docs/reviews/p4b-merge.json"],
          "prior merge evidence frozen content")
    return parse_json(text)


def phase_attestations(phase):
    require(type(phase) is str and phase in PHASE_RELEASE, "invalid recovery phase")
    return CURRENT_ATTESTATIONS if phase == "diagnostic" else VERIFICATION_ATTESTATIONS


def protected_paths(state):
    validate_state(state)
    return tuple(sorted(set(ALLOWED) - set(phase_attestations(state["phase"]))))

def check(root=ROOT, *, ci=False, complete_index=False):
    root = Path(root).resolve()
    state = parse_json(read_text(root, "project-state/state.json"))
    contents = {name: read_text(root, name) for name in protected_paths(state)}
    contract = validate_contract(tomllib.loads(contents[CONTRACT_PATH]))
    exact(tomllib.loads(contents["governance/policy.toml"]), POLICY, "policy")
    state = parse_json(contents["project-state/state.json"])
    validate_state(state)
    validate_live_prerequisites(contents[PREREQUISITE_PATH])
    validate_historical_probe(contents[SOURCE_PROBE_PATH])
    validate_historical_preparation(contents[PREPARATION_PATH])
    prior_merge_evidence(root)
    exact(contents[PROMPT_PATH], render_prompt(contract, contents[TEMPLATE_PATH]), "builder prompt")
    validate_prerequisite(parse_json(contents[CURRENT_RECEIPT_PATHS["blueprint_review"]]),
                          "blueprint_reviewer", AUDIT_INPUTS,
                          contents)
    validate_prerequisite(parse_json(contents[CURRENT_RECEIPT_PATHS["prompt_audit"]]), "prompt_auditor",
                          AUDIT_INPUTS, contents)
    entrypoint_cycles = parse_json(contents[CURRENT_RECEIPT_PATHS["blueprint_review"]])["repair_cycles"]
    audit_cycles = parse_json(contents[CURRENT_RECEIPT_PATHS["prompt_audit"]])["repair_cycles"]
    require(entrypoint_cycles <= audit_cycles, "prompt_auditor: P4B repair bound or reset")
    for role in ("blueprint_review", "prompt_audit"):
        agent = parse_json(contents[CURRENT_RECEIPT_PATHS[role]])["agent_id"]
        require(agent == agent.strip() and agent not in state["builder_agents"],
                f"{role}: builder self-review")
        exact(agent, AUDIT_AGENT, f"{role}: approved independent audit agent")
    for name in phase_attestations(state["phase"]):
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
    state = parse_json(contents["project-state/state.json"])
    for name in protected_paths(state):
        content = contents[name].encode("utf-8")
        hashes[name] = hashlib.sha256(content).hexdigest()
        # Length framing makes even embedded NULs unambiguous; Git HEAD is not an input.
        aggregate.update(name.encode("utf-8") + b"\0" + str(len(content)).encode("ascii") +
                         b"\0" + content + b"\0")
    return aggregate.hexdigest(), hashes


def make_bundle(checked):
    contract, state, contents, changed = checked
    validate_state(state)
    snapshot_id, hashes = snapshot(contents)
    return {"schema_version": 1, "package_id": "P4D", "phase": state["phase"],
            "baseline": contract["baseline"],
            "branch": contract["branch"], "snapshot_id": snapshot_id, "hash_algorithm": ALGORITHM,
            "protected_file_hashes": hashes, "changed_paths": changed,
            "attestation_paths": list(phase_attestations(state["phase"])), "scope_result": "PASS"}


def bundle(root=ROOT, *, ci=False):
    return make_bundle(check(root, ci=ci))


def probe_digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=True, allow_nan=False))


def preparation_digest(value):
    return probe_digest(value)


def validate_source_probe(value, snapshot_id, *, root=ROOT):
    # Load relative imports from the inspected checkout, not cwd/PYTHONPATH or
    # an already cached package belonging to a different temporary repository.
    root = Path(root).resolve()
    package_path = safe_path(root, "market_intelligence/__init__.py")
    safe_path(root, "market_intelligence/collection.py")
    namespace = "_p4s_probe_validation_" + sha256(str(root))
    require(not any(name == namespace or name.startswith(namespace + ".")
                    for name in sys.modules), "source probe: recursive validation")
    spec = importlib.util.spec_from_file_location(
        namespace, package_path, submodule_search_locations=[str(package_path.parent)])
    require(spec is not None and spec.loader is not None, "source probe: package unavailable")
    package = importlib.util.module_from_spec(spec)
    sys.modules[namespace] = package
    try:
        spec.loader.exec_module(package)
        collection = importlib.import_module(namespace + ".collection")
        exact(Path(collection.__file__).resolve(),
              (root / "market_intelligence/collection.py").resolve(), "source probe module")
        validated = collection.validate_probe_record(value, snapshot_id)
        exact(validated, value, "source probe validation result")
    except (ValueError, ImportError, AttributeError) as error:
        raise Block("source probe: invalid or unavailable sanitized record validator") from error
    finally:
        for name in tuple(sys.modules):
            if name == namespace or name.startswith(namespace + "."):
                del sys.modules[name]


def validate_preparation_record(value, snapshot_id, *, root=ROOT):
    # Isolate imports by checkout so neither PYTHONPATH nor a cached foreign
    # product package can supply the approval validator.
    root = Path(root).resolve()
    package_path = safe_path(root, "market_intelligence/__init__.py")
    module_path = safe_path(root, "market_intelligence/preparation.py")
    namespace = "_p4e_preparation_validation_" + sha256(str(root))
    require(not any(name == namespace or name.startswith(namespace + ".")
                    for name in sys.modules), "preparation: recursive validation")
    spec = importlib.util.spec_from_file_location(
        namespace, package_path, submodule_search_locations=[str(package_path.parent)])
    require(spec is not None and spec.loader is not None, "preparation: package unavailable")
    package = importlib.util.module_from_spec(spec)
    sys.modules[namespace] = package
    try:
        spec.loader.exec_module(package)
        preparation = importlib.import_module(namespace + ".preparation")
        exact(Path(preparation.__file__).resolve(), module_path.resolve(), "preparation module")
        validated = preparation.validate_preparation_record(value, snapshot_id, root=root)
        exact(validated, value, "preparation validation result")
    except (ValueError, ImportError, AttributeError) as error:
        raise Block("preparation: invalid or unavailable sanitized record validator") from error
    finally:
        for name in tuple(sys.modules):
            if name == namespace or name.startswith(namespace + "."):
                del sys.modules[name]


def _validate_review_attestations(checked, current, *, paths, audit_path, next_gate,
                                  release_fields=(), release_verdict=LOCAL_RELEASE, p4n_repair=False):
    contract, state, contents, _changed = checked
    require(state["package_status"] == "implemented_unreleased", "gate: state is still building")
    receipts = []
    for name in paths:
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
    audit_cycles = parse_json(contents[audit_path])["repair_cycles"]
    if p4n_repair:
        # This exact historical P4N exception cannot affect active P4B review.
        exact(contract.get("package_id"), "P4N", "repair exception contract package")
        exact(state.get("active_package"), "P4N", "repair exception state package")
        exact(current.get("package_id"), "P4N", "repair exception bundle package")
        for label, binding in (("contract", contract), ("state", state)):
            exact(binding.get("repair_exception"), REPAIR_EXCEPTION, f"{label}.repair_exception")
        require("governance/policy.toml" in contents, "repair exception: missing policy")
        exact(tomllib.loads(contents["governance/policy.toml"]), NBS_POLICY, "repair exception policy")
        require(type(audit_cycles) is int and 0 <= audit_cycles <= 1,
                "review receipt: planning repair bound or reset")
        exact(cycles, 2, "review receipt: repair bound or reset")
        exact(review["agent_id"], REPAIR_EXCEPTION["reviewer_agent"],
              "review receipt: same approved repair Reviewer")
    else:
        require(type(audit_cycles) is int and type(cycles) is int and 0 <= audit_cycles <= cycles <= 1,
                "review receipt: repair bound or reset")
    keys(release, (*common, "role", "verdict", "agent_id", "read_only", "remote_ci", "pr_url",
                   "merge_status", "vps_sync", "next_gate", *release_fields), "release receipt")
    for key, value in {**common, "role": "release_checker", "read_only": True,
                       "verdict": release_verdict,
                       "remote_ci": "NOT_RUN_AT_SNAPSHOT",
                       "pr_url": None, "merge_status": "not_merged", "vps_sync": "not_requested",
                       "next_gate": next_gate}.items():
        exact(release[key], value, f"release receipt.{key}")
    for role, receipt in (("reviewer", review), ("release_checker", release)):
        nonempty(receipt["agent_id"], f"{role}.agent_id")
        require(receipt["agent_id"] == receipt["agent_id"].strip() and
                receipt["agent_id"] not in state["builder_agents"], f"{role}: builder self-review")
    require(release["agent_id"] != review["agent_id"],
            "release_checker: needs a separate independent release agent")
    # Identity strings bind records, not authenticity. Actual independence needs external audit.
    return release


def validate_nbs_observation(value, snapshot_id, *, root=ROOT):
    # Match the preparation validator's isolated checkout import, with no store reads.
    root = Path(root).resolve()
    package_path = safe_path(root, "market_intelligence/__init__.py")
    module_path = safe_path(root, "market_intelligence/nbs.py")
    namespace = "_p4n_observation_validation_" + sha256(str(root))
    require(not any(name == namespace or name.startswith(namespace + ".")
                    for name in sys.modules), "NBS observation: recursive validation")
    spec = importlib.util.spec_from_file_location(
        namespace, package_path, submodule_search_locations=[str(package_path.parent)])
    require(spec is not None and spec.loader is not None, "NBS observation: package unavailable")
    package = importlib.util.module_from_spec(spec)
    sys.modules[namespace] = package
    try:
        spec.loader.exec_module(package)
        nbs = importlib.import_module(namespace + ".nbs")
        exact(Path(nbs.__file__).resolve(), module_path.resolve(), "NBS observation module")
        validated = nbs.validate_observation_record(value, snapshot_id)
        exact(validated, value, "NBS observation validation result")
    except (ValueError, ImportError, AttributeError) as error:
        raise Block("NBS observation: invalid or unavailable sanitized record validator") from error
    finally:
        for name in tuple(sys.modules):
            if name == namespace or name.startswith(namespace + "."):
                del sys.modules[name]


def not_run_release(snapshot_id):
    """Inert complete-index placeholder, never an approval or agent assertion."""
    return {"schema_version": 1, "package_id": "P4N", "snapshot_id": snapshot_id,
            "role": "release_checker", "verdict": "NOT_RUN",
            "reason": "awaiting_terminal_observation_and_independent_release"}


def _validate_market_bindings(checked, current):
    contract, state, contents, _changed = checked
    exact(contract["package_id"], "P4B", "current contract package")
    exact(state["active_package"], "P4B", "current state package")
    exact(current["package_id"], "P4B", "current bundle package")
    for label, binding in (("contract", contract), ("state", state)):
        require("repair_exception" not in binding and "limited_ingestion" not in binding,
                "P4B: historical ingestion or repair exception cannot be inherited")
        exact(binding.get("limited_market_ingestion"), LIMITED_MARKET_INGESTION,
              f"{label}.limited_market_ingestion")
    require("governance/policy.toml" in contents, "current approval: missing policy")
    exact(tomllib.loads(contents["governance/policy.toml"]), MARKET_POLICY, "current approval policy")
    for name in MARKET_ATTESTATIONS:
        require(name in contents, f"missing required receipt: {name}")


def _market_validation_call(root, function, *args):
    # Anchor pure validation to the inspected checkout, not a cached foreign package.
    root = Path(root).resolve()
    package_path = safe_path(root, "market_intelligence/__init__.py")
    module_path = safe_path(root, "market_intelligence/baostock_capture.py")
    namespace = "_p4b_market_validation_" + sha256(str(root))
    require(not any(name == namespace or name.startswith(namespace + ".")
                    for name in sys.modules), "market: recursive validation")
    spec = importlib.util.spec_from_file_location(
        namespace, package_path, submodule_search_locations=[str(package_path.parent)])
    require(spec is not None and spec.loader is not None, "market: package unavailable")
    package = importlib.util.module_from_spec(spec)
    sys.modules[namespace] = package
    try:
        spec.loader.exec_module(package)
        capture = importlib.import_module(namespace + ".baostock_capture")
        exact(Path(capture.__file__).resolve(), module_path.resolve(), "market validation module")
        return getattr(capture, function)(*args)
    except (ValueError, ImportError, AttributeError, OSError, TypeError, RuntimeError):
        raise Block("market: invalid record or unavailable safe journal validator") from None
    finally:
        for name in tuple(sys.modules):
            if name == namespace or name.startswith(namespace + "."):
                del sys.modules[name]


def validate_market_observation(value, snapshot_id, *, root=ROOT):
    validated = _market_validation_call(root, "validate_observation_receipt", value, snapshot_id)
    require(validated is value, "market: observation validator changed the result")


def assert_market_journal_unconsumed(snapshot_id, *, root=ROOT):
    raise Block("P4D: historical market capability is closed; old journal access forbidden")


def not_run_market_release(snapshot_id):
    """An inert pending-result record, never a Release Checker assertion."""
    return {"schema_version": 1, "package_id": "P4B", "snapshot_id": snapshot_id,
            "role": "release_checker", "verdict": "NOT_RUN",
            "reason": "awaiting_terminal_observation_and_independent_release"}


def validate_market_prerequisites(checked, current, *, root=ROOT):
    _validate_market_bindings(checked, current)
    contents = checked[2]
    _validate_review_attestations(
        checked, current, paths=MARKET_ATTESTATIONS[:4],
        audit_path=MARKET_RECEIPT_PATHS["prompt_audit"], next_gate=MARKET_NEXT_GATE,
        release_verdict=PRE_PROBE_RELEASE)
    observation = parse_json(contents[MARKET_RECEIPT_PATHS["observation"]])
    validate_market_observation(observation, current["snapshot_id"], root=root)
    exact(observation["status"], "NOT_RUN", "market gate: allowance already observed")
    exact(parse_json(contents[MARKET_RECEIPT_PATHS["release"]]),
          not_run_market_release(current["snapshot_id"]), "pending final release")


def validate_historical_market_attestations(checked, current, *, root=ROOT):
    _validate_market_bindings(checked, current)
    contents = checked[2]
    pre_release = _validate_review_attestations(
        checked, current, paths=MARKET_ATTESTATIONS[:4],
        audit_path=MARKET_RECEIPT_PATHS["prompt_audit"], next_gate=MARKET_NEXT_GATE,
        release_verdict=PRE_PROBE_RELEASE)
    observation = parse_json(contents[MARKET_RECEIPT_PATHS["observation"]])
    validate_market_observation(observation, current["snapshot_id"], root=root)
    require(observation["status"] != "NOT_RUN", "final gate: terminal market observation required")
    release = _validate_review_attestations(
        checked, current,
        paths=(*MARKET_ATTESTATIONS[:3], MARKET_RECEIPT_PATHS["release"]),
        audit_path=MARKET_RECEIPT_PATHS["prompt_audit"], next_gate=MARKET_NEXT_GATE,
        release_fields=("pre_probe_release_digest", "observation_digest"))
    exact(release["pre_probe_release_digest"], probe_digest(pre_release),
          "release receipt.pre_probe_release_digest")
    exact(release["observation_digest"], probe_digest(observation),
          "release receipt.observation_digest")
    # Capture owns the closed observation schema; final cleanup checks follow its contract.
    require_market_cleanup(observation)


def require_market_cleanup(observation):
    require(observation["observation"]["finished_at"] is not None,
            "final gate: actual terminal completion required")
    cleanup = observation["observation"]["cleanup"]
    for field in ("socket_closed", "bindings_restored", "transport_payloads_disposed",
                  "capture_payloads_disposed", "payload_tables_empty"):
        exact(cleanup[field], True, f"final gate: cleanup.{field}")
    require(cleanup["checked_at"] is not None, "final gate: actual cleanup check required")


def validate_historical_nbs_attestations(checked, current, *, root=ROOT, pre_probe=False):
    """Pure P4N receipt auditing only; never a capability or P4B approval."""
    contract, state, contents, _changed = checked
    exact(contract["package_id"], "P4N", "historical NBS contract package")
    exact(state["active_package"], "P4N", "historical NBS state package")
    exact(current["package_id"], "P4N", "historical NBS bundle package")
    for label, binding in (("historical contract", contract), ("historical state", state)):
        exact(binding.get("limited_ingestion"), LIMITED_INGESTION, f"{label}.limited_ingestion")
    for name in NBS_ATTESTATIONS:
        require(name in contents, f"missing required receipt: {name}")
    _validate_review_attestations(
        checked, current, paths=NBS_ATTESTATIONS[:4],
        audit_path=NBS_RECEIPT_PATHS["prompt_audit"], next_gate=NBS_NEXT_GATE,
        release_verdict=NBS_PRE_PROBE_RELEASE, p4n_repair=True)
    observation = parse_json(contents[NBS_RECEIPT_PATHS["observation"]])
    validate_nbs_observation(observation, current["snapshot_id"], root=root)
    require(observation["status"] in ("NOT_RUN", "RECORDED", "FAILED", "UNKNOWN"),
            "NBS observation: unknown status")
    release = parse_json(contents[NBS_RECEIPT_PATHS["release"]])
    if pre_probe and type(release) is dict and release.get("verdict") == "NOT_RUN":
        exact(release, not_run_release(current["snapshot_id"]), "pending final release")
        return
    require(observation["status"] in ("RECORDED", "FAILED", "UNKNOWN"),
            "final gate: terminal NBS observation required")
    _validate_review_attestations(
        checked, current, paths=(*NBS_ATTESTATIONS[:3], NBS_RECEIPT_PATHS["release"]),
        audit_path=NBS_RECEIPT_PATHS["prompt_audit"], next_gate=NBS_NEXT_GATE, p4n_repair=True)


def validate_attestations(checked, current, *, root=ROOT):
    """Pure terminal P4E audit compatibility; never a P4I approval or preapproval."""
    contract, state, contents, _changed = checked
    require(all(package in (None, "P4E") for package in
                (contract.get("package_id"), state.get("active_package"), current.get("package_id"))),
            "historical preparation audit: P4E records only, not current P4I approval")
    require("repair_exception" not in contract and "repair_exception" not in state,
            "historical preparation audit: repair exception cannot be inherited")
    release = _validate_review_attestations(
        checked, current, paths=ATTESTATIONS[:4], audit_path=RECEIPT_PATHS["prompt_audit"],
        next_gate=contract.get("next_gate", NEXT_GATE),
        release_fields=("pre_preparation", "preparation_digest"))
    exact(release["pre_preparation"], {
        "verdict": PRE_PREPARATION_RELEASE, "snapshot_id": current["snapshot_id"],
        "agent_id": release["agent_id"],
    }, "historical release receipt.pre_preparation")
    require(PREPARATION_PATH in contents, "missing historical preparation receipt")
    preparation = parse_json(contents[PREPARATION_PATH])
    validate_preparation_record(preparation, current["snapshot_id"], root=root)
    exact(preparation["status"], "RECORDED", "historical terminal preparation required")
    exact(release["preparation_digest"], preparation_digest(preparation),
          "historical release receipt.preparation_digest")



def _recovery_validation_call(root, function, *args):
    root = Path(root).resolve()
    package_path = safe_path(root, "market_intelligence/__init__.py")
    module_path = safe_path(root, "market_intelligence/baostock_recovery.py")
    namespace = "_p4d_recovery_validation_" + sha256(str(root))
    require(not any(name == namespace or name.startswith(namespace + ".")
                    for name in sys.modules), "recovery: recursive validation")
    spec = importlib.util.spec_from_file_location(
        namespace, package_path, submodule_search_locations=[str(package_path.parent)])
    require(spec is not None and spec.loader is not None, "recovery: package unavailable")
    package = importlib.util.module_from_spec(spec)
    sys.modules[namespace] = package
    try:
        spec.loader.exec_module(package)
        recovery = importlib.import_module(namespace + ".baostock_recovery")
        exact(Path(recovery.__file__).resolve(), module_path.resolve(), "recovery module")
        return getattr(recovery, function)(*args)
    except (ValueError, ImportError, AttributeError, OSError, TypeError, RuntimeError):
        raise Block("recovery: invalid record or unavailable safe phase validator") from None
    finally:
        for name in tuple(sys.modules):
            if name == namespace or name.startswith(namespace + "."):
                del sys.modules[name]


def validate_recovery_receipt(value, snapshot_id, phase, *, root=ROOT):
    validated = _recovery_validation_call(
        root, "validate_recovery_receipt", value, snapshot_id, phase)
    require(validated is value, "recovery: validator changed the result")


def not_run_recovery_release(snapshot_id):
    return {"schema_version": 1, "package_id": "P4D", "phase": "verification",
            "snapshot_id": snapshot_id, "role": "release_checker", "verdict": "NOT_RUN",
            "reason": "awaiting_terminal_observation_and_independent_release"}


def _recovery_bindings(checked, current, phase):
    contract, state, contents, _changed = checked
    validate_contract(contract)
    validate_state(state)
    exact(state["phase"], phase, "recovery gate phase")
    exact(tomllib.loads(contents["governance/policy.toml"]), POLICY, "recovery policy")
    exact(parse_json(contents["project-state/state.json"]), state, "recovery state content")
    expected = make_bundle(checked)
    exact(current, expected, "recovery current bundle")
    for role in ("blueprint_review", "prompt_audit"):
        receipt = parse_json(contents[CURRENT_RECEIPT_PATHS[role]])
        validate_prerequisite(receipt, "blueprint_reviewer" if role == "blueprint_review"
                              else "prompt_auditor", AUDIT_INPUTS, contents)
        require(receipt["agent_id"] not in state["builder_agents"], "entry: builder self-review")
        exact(receipt["agent_id"], AUDIT_AGENT, "entry: independent audit agent")
    entry = parse_json(contents[CURRENT_RECEIPT_PATHS["blueprint_review"]])
    audit = parse_json(contents[CURRENT_RECEIPT_PATHS["prompt_audit"]])
    require(entry["repair_cycles"] <= audit["repair_cycles"], "entry: repair bound or reset")
    needed = DIAGNOSTIC_ATTESTATIONS if phase == "diagnostic" else CURRENT_ATTESTATIONS
    for name in needed:
        require(name in contents, f"missing required receipt: {name}")


def _phase_review(checked, current, phase):
    paths = DIAGNOSTIC_ATTESTATIONS[:4] if phase == "diagnostic" else VERIFICATION_ATTESTATIONS[:4]
    return _validate_review_attestations(
        checked, current, paths=paths, audit_path=CURRENT_RECEIPT_PATHS["prompt_audit"],
        next_gate=NEXT_GATE, release_verdict=PHASE_RELEASE[phase])


def _terminal_receipt(checked, current, phase, *, root):
    name = CURRENT_RECEIPT_PATHS["diagnostic" if phase == "diagnostic" else "observation"]
    value = parse_json(checked[2][name])
    validate_recovery_receipt(value, current["snapshot_id"], phase, root=root)
    require(value["status"] != "NOT_RUN", "final gate: actual terminal recovery result required")
    require_market_cleanup(value)
    if phase == "diagnostic":
        exact(value["status"], "RECORDED", "diagnostic: inconclusive result cannot authorize verification")
    return value


CHECKPOINT_FILE_BYTES = 1024 * 1024
CHECKPOINT_TOTAL_BYTES = 2 * 1024 * 1024


def _checkpoint_contents(contents, basis):
    """Rebuild reviewed phase-A text without importing it or relying on Git history."""
    commit = basis["checkpoint_commit"]
    require(commit is None or (type(commit) is str and re.fullmatch(r"[0-9a-f]{40}", commit)),
            "repair basis: checkpoint commit must be null or full provenance hash")
    artifact_hashes = basis["checkpoint_artifact_hashes"]
    keys(artifact_hashes, DIAGNOSTIC_ATTESTATIONS, "repair basis.checkpoint_artifact_hashes")
    for name in DIAGNOSTIC_ATTESTATIONS:
        exact(sha256(contents[name]), artifact_hashes[name], f"frozen diagnostic artifact: {name}")
    saved = parse_json(contents[DIAGNOSTIC_ATTESTATIONS[0]])
    require(type(saved) is dict, "repair basis: diagnostic bundle required")
    hashes = saved.get("protected_file_hashes")
    keys(hashes, PROTECTED, "repair basis: checkpoint protected hashes")
    for name, digest in hashes.items():
        require(type(digest) is str and re.fullmatch(r"[0-9a-f]{64}", digest),
                f"repair basis: invalid checkpoint hash: {name}")
    changed = {name for name in PROTECTED if sha256(contents[name]) != hashes[name]}
    archived = basis["checkpoint_inputs"]
    keys(archived, changed, "repair basis: checkpoint inputs")
    total = 0
    for name, text in archived.items():
        require(type(text) is str and normalize_text(text) == text,
                f"repair basis: checkpoint input must be LF text: {name}")
        size = len(text.encode("utf-8"))
        total += size
        require(size <= CHECKPOINT_FILE_BYTES, "repair basis: checkpoint input exceeds per-file limit")
        require(total <= CHECKPOINT_TOTAL_BYTES, "repair basis: checkpoint inputs exceed total limit")
        scan_secrets(f"checkpoint:{name}", text)
    frozen = {**contents, **archived}
    for name in PROTECTED:
        exact(sha256(frozen[name]), hashes[name], f"repair basis: checkpoint input hash: {name}")
    for name in IMMUTABLE:
        exact(frozen[name], contents[name], f"repair basis: immutable historical input: {name}")
    return frozen


def validate_repair_basis(checked, current, *, root=ROOT):
    """Verify a self-contained phase-A snapshot, including after squash merge."""
    contents = checked[2]
    basis = parse_json(contents[REPAIR_BASIS_PATH])
    fields = ("schema_version", "package_id", "status", "diagnostic_snapshot_id",
              "diagnostic_digest", "checkpoint_commit", "checkpoint_artifact_hashes",
              "checkpoint_inputs", "rationale")
    keys(basis, fields, "repair basis")
    for key, value in {"schema_version": 1, "package_id": "P4D", "status": "SUPPORTED"}.items():
        exact(basis[key], value, f"repair basis.{key}")
    frozen = _checkpoint_contents(contents, basis)
    old_state = parse_json(frozen["project-state/state.json"])
    old_contract = tomllib.loads(frozen[CONTRACT_PATH])
    old_checked = (old_contract, old_state, frozen, [])
    old_bundle = make_bundle(old_checked)
    _recovery_bindings(old_checked, old_bundle, "diagnostic")
    _phase_review(old_checked, old_bundle, "diagnostic")
    diagnostic = _terminal_receipt(old_checked, old_bundle, "diagnostic", root=root)
    exact(basis["diagnostic_snapshot_id"], old_bundle["snapshot_id"],
          "repair basis.diagnostic_snapshot_id")
    exact(basis["diagnostic_digest"], probe_digest(diagnostic), "repair basis.diagnostic_digest")
    require(current["snapshot_id"] != old_bundle["snapshot_id"], "fresh verification snapshot required")
    old_review = parse_json(frozen[CURRENT_RECEIPT_PATHS["diagnostic_review"]])
    for role in ("blueprint_review", "prompt_audit"):
        exact(contents[CURRENT_RECEIPT_PATHS[role]], frozen[CURRENT_RECEIPT_PATHS[role]],
              "frozen entry receipt")
    current_review = parse_json(contents[CURRENT_RECEIPT_PATHS["reviewer"]])
    require(type(current_review.get("repair_cycles")) is int
            and old_review["repair_cycles"] <= current_review["repair_cycles"] <= 1,
            "verification review: global repair bound or reset")
    # Only the supported transport repair can change existing product behavior.
    repair_paths = {"market_intelligence/baostock_transport.py", "tests/product/test_baostock_transport.py"}
    for name in ALLOWED:
        if name.startswith(("market_intelligence/", "tests/product/")) and name not in repair_paths:
            exact(contents[name], frozen[name], f"repair basis: unrelated product change: {name}")
    rationale = basis["rationale"]
    require(type(rationale) is list and 1 <= len(rationale) <= 16, "repair basis: rationale required")
    located = set()
    for item in rationale:
        keys(item, ("path", "line_start", "line_end", "before_sha256", "after_sha256",
                    "explanation"), "repair rationale")
        path = item["path"]
        require(type(path) is str and path in repair_paths, "repair rationale: unsupported path")
        require(type(item["line_start"]) is int and type(item["line_end"]) is int
                and 1 <= item["line_start"] <= item["line_end"] <= len(contents[path].splitlines()),
                "repair rationale: invalid source location")
        exact(item["before_sha256"], sha256(frozen[path]), "repair rationale.before_sha256")
        exact(item["after_sha256"], sha256(contents[path]), "repair rationale.after_sha256")
        require(item["before_sha256"] != item["after_sha256"], "repair rationale: no actual change")
        nonempty(item["explanation"], "repair rationale.explanation")
        require(len(item["explanation"]) <= 2000, "repair rationale: explanation too long")
        scan_secrets("repair rationale", item["explanation"])
        located.add(path)
    changed = {name for name in repair_paths if contents[name] != frozen[name]}
    exact(located, changed, "repair rationale: changed paths")
    require("market_intelligence/baostock_transport.py" in changed, "supported transport repair required")
    return diagnostic, basis


def validate_recovery_prerequisites(checked, current, phase, *, root=ROOT):
    _recovery_bindings(checked, current, phase)
    _phase_review(checked, current, phase)
    diagnostic_digest = None
    if phase == "verification":
        diagnostic, _basis = validate_repair_basis(checked, current, root=root)
        diagnostic_digest = probe_digest(diagnostic)
        exact(parse_json(checked[2][CURRENT_RECEIPT_PATHS["release"]]),
              not_run_recovery_release(current["snapshot_id"]), "pending final recovery release")
    name = CURRENT_RECEIPT_PATHS["diagnostic" if phase == "diagnostic" else "observation"]
    record = parse_json(checked[2][name])
    validate_recovery_receipt(record, current["snapshot_id"], phase, root=root)
    exact(record["status"], "NOT_RUN", "recovery gate: phase already observed")
    return diagnostic_digest


def validate_current_attestations(checked, current, *, root=ROOT):
    _recovery_bindings(checked, current, "verification")
    pre_release = _phase_review(checked, current, "verification")
    diagnostic, basis = validate_repair_basis(checked, current, root=root)
    observation = _terminal_receipt(checked, current, "verification", root=root)
    release = _validate_review_attestations(
        checked, current, paths=(*VERIFICATION_ATTESTATIONS[:3], CURRENT_RECEIPT_PATHS["release"]),
        audit_path=CURRENT_RECEIPT_PATHS["prompt_audit"], next_gate=NEXT_GATE,
        release_fields=("diagnostic_digest", "repair_basis_digest",
                        "pre_probe_release_digest", "observation_digest"))
    for field, value in (("diagnostic_digest", diagnostic), ("repair_basis_digest", basis),
                         ("pre_probe_release_digest", pre_release), ("observation_digest", observation)):
        exact(release[field], probe_digest(value), f"final release.{field}")


def recovery_gate(root=ROOT, phase="diagnostic", *, ci=False):
    exact(ci, False, "recovery-gate: --ci is not runtime authorization")
    phase_attestations(phase)
    checked = check(root, complete_index=True)
    current = make_bundle(checked)
    digest = validate_recovery_prerequisites(checked, current, phase, root=root)
    result = _recovery_validation_call(root, "assert_phase_available", phase,
                                       current["snapshot_id"], digest)
    exact(result, None, "recovery: phase availability return")
    return {"package_id": "P4D", "phase": phase, "snapshot_id": current["snapshot_id"],
            "verdict": PHASE_RELEASE[phase], "diagnostic_digest": digest}


def gate(root=ROOT, *, ci=False):
    checked = check(root, ci=ci, complete_index=True)
    validate_current_attestations(checked, make_bundle(checked), root=root)
    return LOCAL_RELEASE


def market_gate(root=ROOT, *, ci=False):
    raise Block("P4D: historical market capability is closed; --ci is not runtime authorization")


def nbs_gate(root=ROOT, *, ci=False):
    raise Block("P4D: historical NBS ingestion capability is closed; no network request authorized")


def probe_gate(root=ROOT, *, ci=False):
    raise Block("P4D: historical P4S probe capability is closed; no network request authorized")


def preparation_gate(root=ROOT, *, ci=False):
    raise Block("P4D: historical P4E preparation capability is closed; no runtime-store access authorized")


def main(argv=None, *, root=ROOT):
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("check", "prompt", "bundle", "gate"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--ci", action="store_true",
                               help="skip local branch identity only; retain baseline/scope/permission checks")
        if command == "prompt":
            subparser.add_argument("--verify", action="store_true")
    for command in ("probe-gate", "preparation-gate", "nbs-gate", "market-gate",
                    "diagnostic-gate", "verification-gate"):
        subparsers.add_parser(command).add_argument(
            "--ci", action="store_true", help="capability remains closed even in CI")
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
        elif args.command == "probe-gate":
            print(probe_gate(root, ci=args.ci))
        elif args.command == "preparation-gate":
            print(preparation_gate(root, ci=args.ci))
        elif args.command in ("diagnostic-gate", "verification-gate"):
            print(json.dumps(recovery_gate(root, args.command.split("-")[0], ci=args.ci), sort_keys=True))
        elif args.command == "market-gate":
            print(json.dumps(market_gate(root, ci=args.ci), sort_keys=True))
        elif args.command == "nbs-gate":
            print(nbs_gate(root, ci=args.ci))
        else:
            print(f"{gate(root, ci=args.ci)} ({GOVERNANCE_ONLY})")
        return 0
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"BLOCK: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
