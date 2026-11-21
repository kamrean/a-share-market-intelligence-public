"""Offline P4CORE engineering evaluation and blocked live-readiness preflight."""

import argparse
from pathlib import Path
import sys
import tomllib

from . import gate
from .models import MARKETS, Evidence, Invalid, canonical, digest, instant, stamp, utc_now


_PROFILE_PATH = Path(__file__).resolve().parents[1] / "config" / "mvp_core.toml"
_PROFILE_HASH = "7ce1d2121f13dec0029c76670071a25636bc6e14914d353879a4a77d98aea0bd"
_MISSING = object()


def load_profile():
    with _PROFILE_PATH.open("rb") as handle:
        profile = tomllib.load(handle)
    if digest(profile) != _PROFILE_HASH:
        raise Invalid("P4CORE profile does not match the v1 pin")
    return profile


def _validate_calendar(calendar, allowed_ids):
    if (type(calendar) is not gate.Calendar or type(calendar.sessions) is not tuple
            or any(type(session) is not gate.Session for session in calendar.sessions)):
        raise Invalid("explicit scoped calendar required")
    try:
        calendar.validate()
    except (TypeError, OverflowError) as exc:
        raise Invalid("invalid scoped calendar") from exc
    if any(session.calendar_id not in allowed_ids for session in calendar.sessions):
        raise Invalid("calendar session outside assessment scope")


def _optional_slot(key, block, market_rules, mode, cutoff, now):
    ids = []
    if block is _MISSING:
        reasons = ["insufficient_coverage"]
    elif type(block) is not dict or set(block) != {"calendar", "records"}:
        reasons = ["invalid_optional_envelope"]
    elif type(block["records"]) not in (list, tuple):
        reasons = ["invalid_optional_records"]
    else:
        calendar, records = block["calendar"], block["records"]
        try:
            _validate_calendar(calendar, {MARKETS[key][0]})
        except Invalid:
            reasons = ["invalid_optional_calendar"]
        else:
            # Check membership, never use a row's claims to select its destination.
            def matches(record):
                if isinstance(record, Evidence):
                    return (record.domain, record.key) == ("market", key)
                return type(record) is dict and (record.get("domain"), record.get("key")) == ("market", key)

            if not all(map(matches, records)):
                reasons = ["optional_record_outside_slot"]
            else:
                result, ids, rejected = gate._assess(
                    records, market_rules, mode, calendar, cutoff, now, (key,))
                reasons = sorted(set(result["market"]["reasons"]) | {row["reason"] for row in rejected})
    available = not reasons
    return {"status": "PASS" if available else "DEGRADED",
            "availability": "available" if available else "unavailable",
            "confidence_ceiling": "supported" if available else "unavailable",
            "reasons": reasons, "accepted_ids": ids if available else []}


def evaluate(records, calendar, as_of, now=None, *, optional=None):
    """Assess synthetic core inputs and optional {symbol: {records, calendar}} envelopes."""
    profile = load_profile()
    rules = tuple(gate.Rule.from_dict(row) for row in profile["rules"])
    market_rules = tuple(rule for rule in rules if rule.domain == "market")
    _validate_calendar(calendar, {MARKETS[key][0] for key in profile["required_market"]})
    now = now or utc_now()
    cutoff = instant(as_of)
    if cutoff > now:
        raise Invalid("future as_of")

    if optional is None:
        optional = {}
    if type(optional) is not dict or not optional.keys() <= set(profile["optional_market"]):
        raise Invalid("optional envelopes must be keyed by configured optional symbols")

    domains, accepted, rejected = gate._assess(
        records, rules, profile["mode"], calendar, cutoff, now, profile["required_market"],
        (profile["required_news_tier"],))
    context = {
        key: _optional_slot(key, optional.get(key, _MISSING), market_rules, profile["mode"], cutoff, now)
        for key in profile["optional_market"]
    }
    status = "BLOCK" if any(row["status"] == "BLOCK" for row in domains.values()) else (
        "DEGRADED" if any(row["status"] == "DEGRADED" for row in context.values()) else "PASS")
    return {
        "schema_version": profile["schema_version"], "profile_version": profile["version"],
        "method_version": profile["method_version"], "profile_hash": _PROFILE_HASH,
        "mode": profile["mode"], "as_of": stamp(cutoff), "calendar_hash": digest(calendar.to_dict()),
        "status": status, "evidence_scope": "synthetic_engineering_only",
        "confidence_ceiling": {"BLOCK": "none", "DEGRADED": "limited", "PASS": "supported"}[status],
        "domains": domains, "accepted_ids": accepted, "rejected": rejected, "optional": context,
    }


def preflight():
    profile = load_profile()
    required = profile["required_market"]
    # No live evidence input exists in this version; a synthetic PASS cannot affect this report.
    return {
        "schema_version": profile["schema_version"], "profile_version": profile["version"],
        "profile_hash": _PROFILE_HASH, "status": "BLOCK", "evidence_scope": "offline_preflight",
        "confidence_ceiling": "none",
        "live_market_coverage": {"covered": 0, "expected": len(required), "missing": required},
        "blockers": ["mainland_operation_rights_unverified", "no_approved_fresh_mainland_capture",
                     "no_eligible_live_primary_news_evidence"],
        "optional": {key: {"status": "DEGRADED", "availability": "unavailable",
                           "confidence_ceiling": "unavailable"}
                     for key in profile["optional_market"]},
        "source_requirements": profile["source_requirements"],
        "news_candidate": profile["news_candidate"],
        "requests_attempted": 0, "permissions_enabled": [], "permissions_granted": [],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("command", choices=("preflight",))
    parser.parse_args(argv)
    try:
        print(canonical(preflight()))
        return 0
    except (OSError, ValueError):
        print("Offline preflight blocked: invalid or unreadable profile.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
