"""Explicit local workflows. Real retrieval is opt-in, never scheduled."""

import argparse
from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
import sqlite3
import sys
import tomllib
import uuid

from .gate import Calendar, Profile, Session, evaluate, load_profile
from .models import (Capture, Invalid, UNIVERSE, digest, stamp, utc_now)
from .sources import (FED_URL, Response, TransportError, fetch, load_catalog, parse)
from .store import BudgetDenied, Busy, CHECKOUT, Store

ROOT = CHECKOUT


def policy_allows(root=ROOT):
    """Re-read the active permission and exact audited inputs before dispatch."""
    with (root / "governance/policy.toml").open("rb") as handle:
        policy = tomllib.load(handle)
    permissions = policy.get("permissions", {})
    if (policy.get("active_package") != "P1"
            or permissions.get("external_data_access_allowed") is not True
            or permissions.get("research_only") is not True):
        raise Invalid("active_policy_denies_network")
    forbidden = {"scheduled_collection_allowed", "llm_research_allowed", "telegram_delivery_allowed",
                 "vps_deployment_allowed", "vps_mutation_allowed", "broker_allowed",
                 "order_execution_allowed", "portfolio_control_allowed"}
    if any(permissions.get(key) is not False for key in forbidden):
        raise Invalid("unexpected_policy_capability")
    audit = json.loads((root / "docs/reviews/p1-prompt-audit.json").read_text(encoding="utf-8"))
    if (audit.get("verdict") != "PASS" or audit.get("independent") is not True
            or audit.get("read_only") is not True or audit.get("role") != "prompt_auditor"):
        raise Invalid("prompt_audit_not_passed")
    expected = {"contracts/p1_sources_evidence.toml", "governance/policy.toml",
                "templates/p1_builder_prompt.md", "docs/reviews/p1-builder-prompt.md", "AGENTS.md", "docs/P1_DESIGN.md"}
    if set(audit.get("files", {})) != expected:
        raise Invalid("incomplete_audit_binding")
    for path, checksum in audit["files"].items():
        content = (root / path).read_text(encoding="utf-8").replace("\r\n", "\n")
        if digest(content.encode("utf-8")) != checksum:
            raise Invalid("audited_input_changed")


def synthetic_calendar():
    return Calendar("synthetic-explicit-sessions-2020-v1", (
        Session("SSE", "Asia/Shanghai", "2020-01-02", "2020-01-02T15:00:00+08:00"),
        Session("SZSE", "Asia/Shanghai", "2020-01-02", "2020-01-02T15:00:00+08:00"),
        Session("KRX", "Asia/Seoul", "2020-01-02", "2020-01-02T15:30:00+09:00"),
        Session("US_CASH", "America/New_York", "2020-01-02", "2020-01-02T16:00:00-05:00"),
    )).validate()


def profile_for(mode, synthetic=False):
    profile = load_profile(ROOT / "config/p1_profile.toml")
    if synthetic:
        # A deliberately historical fixture window, not a production freshness rule.
        return replace(profile, mode=mode, version="synthetic-fixture-profile-v1",
                       rules=tuple(replace(r, max_age_seconds=315576000) for r in profile.rules))
    return replace(profile, mode=mode)


def _summary(store, lease, mode, source_id, started, outcome, source_state, parsed,
             capture_ids, bundle_id, gate, errors):
    attempts = store.attempts(lease.run_id)
    unknown = sum(a["outcome"] in {"reserved", "unknown"} for a in attempts)
    return {
        "schema_version": 1, "run_id": lease.run_id, "mode": mode,
        "started_at": started, "finished_at": stamp(utc_now()), "outcome": outcome,
        "source_id": source_id, "source_state": source_state,
        "requests": {"attempted": len(attempts),
                     "success": sum(a["outcome"] == "success" for a in attempts),
                     "failed": sum(a["outcome"] == "failed" for a in attempts),
                     "unknown": unknown,
                     "reserved_bytes": sum(a["reserved_bytes"] for a in attempts),
                     "observed_bytes": None if any(a["observed_bytes"] is None for a in attempts)
                     else sum(a["observed_bytes"] for a in attempts)},
        "quality": {"request_sample_size": len(attempts),
                    "parse_sample_size": len(capture_ids),
                    "parse_success_count": len(capture_ids) if parsed is not None else 0,
                    "nonempty_parse_count": len(capture_ids) if parsed is not None and parsed.records else 0,
                    "item_denominator": parsed.total if parsed else 0,
                    "accepted_records": len(parsed.records) if parsed else 0,
                    "correction_rate": None, "publication_delay_seconds": None,
                    "long_term_reliability": None},
        "parse": parsed.counts if parsed else {"total": 0, "accepted": 0, "rejected": 0, "reasons": {}},
        "capture_ids": capture_ids, "bundle_id": bundle_id,
        "observations": [{"capture_id": identity, **{name: getattr(store.capture(identity), name)
                           for name in ("source_id", "locator", "retrieved_at", "first_seen_at", "source_timestamp",
                                        "content_hash", "response_hash", "parser_version", "retention", "expires_at")}}
                         for identity in capture_ids],
        "gate": {"status": gate["status"], "domains": gate["domains"], "confidence_ceiling": gate["confidence_ceiling"]},
        "live_market_coverage": {"covered": 0, "expected": 5, "missing": list(UNIVERSE)},
        "readiness": "BLOCKED_MARKET_SOURCES", "error_codes": errors,
    }


def _freeze(store, lease, records, mode, calendar, synthetic=False):
    profile = profile_for(mode, synthetic)
    result = evaluate(records, profile, calendar, stamp(utc_now()))
    return result, store.freeze(result, lease=lease)


def demo(store, task_key):
    lease, run = store.begin(task_key, "synthetic_test", intent="demo-v1")
    if lease is None:
        return run["summary"]
    try:
        records, captures, totals = [], [], []
        for filename, parser, media in (("p1_feed.xml", "rss-v1", "application/rss+xml"),
                                        ("p1_market.csv", "market-csv-v1", "text/csv")):
            body = (ROOT / "tests/fixtures" / filename).read_bytes()
            now = stamp(utc_now())
            source_id = "synthetic_" + parser
            capture = Capture(source_id, "https://example.invalid/synthetic/" + filename,
                              now, store.first_seen(source_id, digest(body), now), None, digest(body),
                              digest({"synthetic_fixture": filename, "content_hash": digest(body)}), media,
                              parser, "reprocessable_raw", "Independently authored SYNTHETIC fixture, no real prices or disclosures",
                              None, "synthetic_test", "fixture-" + lease.run_id)
            parsed = parse(body, capture)
            captures.append(store.put_capture(capture, body, parsed.records, lease=lease))
            records.extend(parsed.records)
            totals.append(parsed)
        from .sources import Parsed
        parsed = Parsed(tuple(records), sum(p.total for p in totals), {
            reason: sum(p.rejections.get(reason, 0) for p in totals)
            for reason in {k for p in totals for k in p.rejections}})
        gate, bundle = _freeze(store, lease, records, "synthetic_test", synthetic_calendar(), synthetic=True)
        summary = _summary(store, lease, "synthetic_test", "synthetic_fixtures", run["started_at"],
                           "synthetic_demo", "implemented_unverified", parsed, captures, bundle, gate, [])
        store.finish(lease, summary)
        return summary
    except BaseException:
        store.release(lease)
        raise


def probe(store, task_key, allow_network=False, source_id="fed_press", transport=None, policy_root=ROOT):
    simulated = transport is not None
    mode = "synthetic_test" if simulated else "live_observation"
    lease, run = store.begin(task_key, mode, intent="probe-v1:" + source_id + ":" + str(allow_network))
    if lease is None:
        return run["summary"]
    parsed, captures, errors = None, [], []
    state, outcome = "implemented_unverified", "not_attempted"
    try:
        try:
            if allow_network is not True:
                raise Invalid("explicit_allow_network_required")
            policy_allows(policy_root)
            catalog = load_catalog(ROOT / "config/sources.toml")
            if source_id not in catalog:
                raise Invalid("unknown_source")
            source = catalog[source_id]
            state = source.state
            source.authorize()
        except (Invalid, OSError, ValueError) as exc:
            errors.append(str(exc) if isinstance(exc, Invalid) else "policy_or_catalog_unavailable")
        else:
            successes = [a for a in run["attempts"] if a["outcome"] == "success"]
            if successes:
                capture = store.capture_for_attempt(successes[-1]["attempt_id"])
                if capture:
                    captures.append(capture.version_id)
                    try:
                        parsed = store.retained_parse(capture.version_id)
                        outcome = "simulated_probe" if simulated else "observed"
                        state = "implemented_unverified" if simulated else "live_verified"
                        if not parsed.records or parsed.rejections:
                            state = "degraded"
                        if not parsed.records:
                            outcome = "failed"
                    except Invalid:
                        errors.append("retained_capture_unavailable")
                        outcome, state = "unknown", "degraded"
                else:
                    errors.append("successful_response_not_durably_captured")
                    outcome, state = "unknown", "degraded"
            for _ in range(0 if successes else 2):
                try:
                    policy_allows(policy_root)
                    source.authorize()
                    attempt = store.reserve(lease)
                except (Invalid, OSError, ValueError) as exc:
                    errors.append(str(exc) if isinstance(exc, Invalid) else "policy_unavailable")
                    break
                try:
                    response = (transport or fetch)(source)
                    if not isinstance(response, Response):
                        raise TransportError("invalid_transport_response", "unknown")
                    response.validate()
                except Exception as exc:
                    result = exc.outcome if isinstance(exc, TransportError) else "unknown"
                    reason = str(exc) if isinstance(exc, TransportError) else "transport_unknown"
                    store.settle(lease, attempt, result, None if result == "unknown" else 0, reason)
                    errors.append(reason)
                    outcome, state = result, "degraded"
                    if result == "unknown":
                        break
                    continue
                store.settle(lease, attempt, "success", len(response.body), "response_received")
                now = utc_now()
                captured = stamp(now)
                source_name = "synthetic_fed_transport" if simulated else source.source_id
                capture = Capture(source_name, FED_URL, captured,
                                  store.first_seen(source_name, digest(response.body), captured), None,
                                  digest(response.body), response.response_hash, response.content_type,
                                  "rss-v1", source.retention, source.retention_basis,
                                  stamp(now + timedelta(seconds=source.retention_seconds)), mode, attempt)
                try:
                    parsed = parse(response.body, capture)
                    captures.append(store.put_capture(capture, response.body, parsed.records, lease=lease))
                    if not parsed.records:
                        errors.append("empty_or_all_rejected_feed")
                        outcome, state = "failed", "degraded"
                    else:
                        outcome = "simulated_probe" if simulated else "observed"
                        state = "implemented_unverified" if simulated else "live_verified"
                        if parsed.rejections:
                            state = "degraded"
                except Invalid:
                    # Preserve the permitted raw observation even when parsing failed.
                    captures.append(store.put_capture(capture, response.body, lease=lease))
                    parsed = None
                    errors.append("parse_failed")
                    outcome, state = "failed", "degraded"
                break
        gate, bundle = _freeze(store, lease, parsed.records if parsed else (), mode, Calendar("unavailable-live-calendars-v1", ()))
        summary = _summary(store, lease, mode, source_id, run["started_at"], outcome, state,
                           parsed, captures, bundle, gate, errors)
        store.finish(lease, summary)
        return summary
    except BaseException:
        store.release(lease)
        raise


def replay(store, task_key, capture_id, parser_version):
    lease, run = store.begin(task_key, "historical_replay", intent="replay-v1:" + capture_id + ":" + parser_version)
    if lease is None:
        return run["summary"]
    try:
        parsed = store.replay(capture_id, parser_version, lease=lease)
        capture = store.capture(capture_id)
        synthetic = capture.mode == "synthetic_test"
        calendar = synthetic_calendar() if synthetic else Calendar("unavailable-live-calendars-v1", ())
        gate, bundle = _freeze(store, lease, parsed.records, "historical_replay", calendar, synthetic)
        summary = _summary(store, lease, "historical_replay", capture.source_id, run["started_at"],
                           "replayed", "implemented_unverified", parsed, [capture_id], bundle, gate, [])
        store.finish(lease, summary)
        return summary
    except BaseException:
        store.release(lease)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m market_intelligence")
    parser.add_argument("command", choices=("demo", "probe", "replay", "sweep"))
    parser.add_argument("--store", required=True, help="absolute SQLite path outside all checkouts")
    parser.add_argument("--task-key", default=None)
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--source", default="fed_press")
    parser.add_argument("--capture-id")
    parser.add_argument("--parser-version")
    args = parser.parse_args(argv)
    try:
        if args.command != "probe" and args.allow_network:
            raise Invalid("network flag valid only for probe")
        if args.command == "replay" and (not args.capture_id or not args.parser_version):
            raise Invalid("replay requires capture ID and exact parser version")
        key = args.task_key or args.command + "-" + uuid.uuid4().hex
        with Store(args.store) as store:
            if args.command == "demo":
                result = demo(store, key)
            elif args.command == "probe":
                result = probe(store, key, args.allow_network, args.source)
            elif args.command == "replay":
                result = replay(store, key, args.capture_id, args.parser_version)
            else:
                result = {"schema_version": 1, "command": "sweep", "observed_at": stamp(utc_now()),
                          "removed_capture_ids": store.sweep()}
        print(json.dumps(result, sort_keys=True, indent=2, allow_nan=False))
        return 0 if args.command == "sweep" or result["outcome"] in {"synthetic_demo", "observed", "simulated_probe", "replayed"} else 2
    except (Invalid, OSError, sqlite3.Error, ValueError) as exc:
        # Do not expose private paths, raw input or arbitrary exception content.
        reason = str(exc) if isinstance(exc, Invalid) else "local_operation_failed"
        print(json.dumps({"status": "ERROR", "reason": reason}, sort_keys=True))
        return 2
