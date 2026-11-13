"""Pure, validated P2 composition and a fixed-input synthetic stdout demo."""

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import sys
import tomllib
import uuid

from .gate import Calendar
from .market_state import (UnavailableFlowAdapter, array, evaluate_market, numeric,
                           validate_entities, validate_methods)
from .models import (Evidence, Invalid, MODES, UNIVERSE, canonical, digest, exact,
                     fingerprint, instant, integer, locator, stamp, text, utc_now)
from .news_events import evaluate_events, process_news, validate_payload

ROOT = Path(__file__).resolve().parents[1]
IMPLEMENTATION_VERSION = "p2-offline-v1"
MAX_BYTES = 512000
PARSERS = {"bar": "p2-bar-v1", "news": "rss-v1", "event": "p2-event-v1",
           "impact": "p2-event-v1", "reaction": "p2-event-v1", "event_day": "p2-event-v1"}


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Invalid("duplicate JSON field")
        result[key] = value
    return result


def decode_input(body):
    if type(body) is not bytes or len(body) > MAX_BYTES:
        raise Invalid("input byte limit")
    def invalid_constant(_):
        raise Invalid("nonfinite JSON number")
    try:
        return json.loads(body.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=invalid_constant)
    except (UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, Invalid):
            raise
        raise Invalid("invalid input JSON") from exc


@dataclass(frozen=True)
class Fact:
    source_id: str
    locator: str
    mode: str
    published_at: str | None
    retrieved_at: str
    first_seen_at: str
    expires_at: str | None
    parser_version: str
    content_hash: str
    payload: dict

    @classmethod
    def from_dict(cls, value):
        exact(value, cls.__dataclass_fields__)
        for name, limit in (("source_id", 80), ("mode", 40), ("parser_version", 80)):
            text(value[name], name, limit)
        locator(value["locator"])
        if value["mode"] not in MODES:
            raise Invalid("unknown fact mode")
        for name in ("published_at", "retrieved_at", "first_seen_at", "expires_at"):
            if value[name] is not None:
                instant(value[name])
            elif name in {"retrieved_at", "first_seen_at"}:
                raise Invalid("capture timestamp required")
        fingerprint(value["content_hash"])
        if value["content_hash"] != digest(value["payload"]):
            raise Invalid("fact content hash mismatch")
        return cls(**value)

    @property
    def fact_id(self):
        return digest(asdict(self))


def _prepare(payload, methods, entities):
    exact(payload, {"schema_version", "mode", "as_of", "calendar", "facts"})
    integer(payload["schema_version"], 1, 1)
    if payload["mode"] != "synthetic_test":
        raise Invalid("P2 accepts synthetic_test only")
    cutoff = instant(payload["as_of"])
    if cutoff > utc_now():
        raise Invalid("future as_of")
    exact(payload["calendar"], {"version", "sessions"})
    array(payload["calendar"]["sessions"], 128, "sessions")
    for session in payload["calendar"]["sessions"]:
        exact(session, {"calendar_id", "timezone", "session_id", "close_at"})
        for key in session:
            text(session[key], key, 100)
    calendar = Calendar.from_dict(payload["calendar"])
    array(payload["facts"], 1024, "facts")
    sources = {r["source_id"]: r for r in methods["sources"]}
    mapping = {r["entity_id"]: r for r in entities["entities"]}
    eligible, rejected, seen, all_facts = [], [], set(), []
    news_count, event_ids, event_count, attachment_count = 0, set(), 0, 0
    for original in payload["facts"]:
        fact = Fact.from_dict(original)
        if fact.fact_id in seen:
            raise Invalid("duplicate fact identity")
        seen.add(fact.fact_id)
        p = fact.payload
        if type(p) is not dict or "kind" not in p or type(p["kind"]) is not str:
            raise Invalid("typed fact payload required")
        kind = p["kind"]
        domain = "market" if kind == "bar" else ("news" if kind == "news" else "event")
        if kind == "bar":
            exact(p, {"kind", "symbol", "session_id", "close", "turnover"})
            text(p["symbol"], "symbol", 100)
            text(p["session_id"], "session", 10)
            if p["symbol"] not in mapping:
                raise Invalid("unknown symbol")
            numeric(p["close"], positive=True)
            if p["symbol"] in methods["constituents"] or p["turnover"] is not None:
                numeric(p["turnover"])
            session = next((s for s in calendar.sessions if s.calendar_id == mapping[p["symbol"]]["calendar_id"]
                            and s.session_id == p["session_id"]), None)
            if session is None:
                raise Invalid("unknown calendar session")
        else:
            validate_payload(p)
            if fact.published_at is None:
                raise Invalid("publication time required")
            if kind == "news":
                news_count += 1
            elif kind == "event":
                event_ids.add(p["event_id"])
                event_count += 1
            else:
                attachment_count += 1
            if kind == "reaction" and p["symbol"] not in mapping:
                raise Invalid("unknown reaction symbol")
        if news_count > 100 or len(event_ids) > 20 or event_count > 160 or attachment_count > 60:
            raise Invalid("news or event bound exceeded")
        if fact.parser_version != PARSERS[kind]:
            raise Invalid("unknown parser version")
        source = sources.get(fact.source_id)
        if source is None or not fact.locator.startswith(source["locator_prefix"]):
            raise Invalid("unknown source or inconsistent locator")
        reason = None
        if fact.mode != payload["mode"]:
            reason = "mixed_evidence_mode"
        retrieved, first_seen = instant(fact.retrieved_at), instant(fact.first_seen_at)
        published = instant(fact.published_at) if fact.published_at is not None else None
        if retrieved > cutoff or first_seen > retrieved or (published is not None and published > retrieved):
            reason = "future_or_inconsistent_possession"
        if fact.expires_at is not None:
            expiry = instant(fact.expires_at)
            if expiry <= retrieved:
                reason = "inconsistent_expiry"
            elif expiry <= cutoff:
                reason = "expired_evidence"
        scope = {"bar": "market", "event_day": "event"}.get(kind, kind)
        if scope not in source["scopes"]:
            reason = reason or "source_authority_scope"
        if kind == "bar":
            if instant(session.close_at) > min(cutoff, retrieved) or source["tier"] == "C":
                reason = reason or "future_or_unconfirmed_market_close"
            if fact.source_id != mapping[p["symbol"]]["provider"]:
                reason = reason or "series_provider_mismatch"
        elif kind == "event" and p["occurred_at"] is not None and instant(p["occurred_at"]) > cutoff:
            reason = reason or "future_actual_occurrence"
        row = asdict(fact) | {"fact_id": fact.fact_id}
        if kind == "bar":
            row["close_at"] = session.close_at
        if kind == "news" and reason is None:
            # Reuse P1's unchanged news record validation, not its two-domain gate.
            Evidence(capture_id=digest({"source": fact.source_id, "hash": fact.content_hash}),
                     source_id=fact.source_id, locator=fact.locator, domain="news", key=fact.locator,
                     tier=source["tier"], title=p["title"], value=None, published_at=fact.published_at,
                     occurred_at=None, effective_at=None, observed_at=None, retrieved_at=fact.retrieved_at,
                     first_seen_at=fact.first_seen_at, expires_at=fact.expires_at, mode=fact.mode,
                     parser_version=fact.parser_version, calendar_id=None, session_id=None, close_at=None).validate()
        all_facts.append(row)
        if reason:
            rejected.append({"fact_id": fact.fact_id, "domain": domain, "reason": reason})
        else:
            eligible.append(row)
    return eligible, rejected, all_facts


def build_bundle(payload, methods, entities):
    """Return content-addressed diagnostics; no network, file write, or clock in identity."""
    if len(canonical(payload).encode("utf-8")) > MAX_BYTES:
        raise Invalid("input byte limit")
    # Detach caller-owned dictionaries before validation/evaluation.
    payload = decode_input(canonical(payload).encode("utf-8"))
    methods = decode_input(canonical(methods).encode("utf-8"))
    entities = decode_input(canonical(entities).encode("utf-8"))
    validate_methods(methods)
    validate_entities(entities, methods)
    eligible, rejected, all_facts = _prepare(payload, methods, entities)
    market = evaluate_market(eligible, methods, entities, payload["calendar"], payload["as_of"])
    conflicts = set(market["conflicting_ids"])
    rejected.extend({"fact_id": identity, "domain": "market", "reason": "conflicting_market_evidence"} for identity in conflicts)
    eligible = [r for r in eligible if r["fact_id"] not in conflicts]
    news = process_news(eligible, methods, entities, payload["as_of"])
    rejected.extend(news["rejected"])
    events = evaluate_events(eligible, news["eligible"], methods, entities, payload["as_of"])
    rejected.extend(events["rejected"])
    flow = UnavailableFlowAdapter().evaluate(methods["flow"]["provider"], methods["flow"]["series_kind"], payload["as_of"]).to_dict()
    reasons = {"market": market["market_reasons"], "sector": market["sector_reasons"],
               "news": news["news_reasons"], "entity": news["entity_reasons"],
               "event": events["reasons"], "flow": ["not_integrated"]}
    for row in rejected:
        reasons[row["domain"]] = [*reasons[row["domain"]], row["reason"]]
    counts = {"market": (sum(r["status"] == "available" for r in market["benchmarks"]), 5),
              "sector": (sum(r["status"] == "available" for r in market["sectors"]), len(market["sectors"])),
              "news": (len(news["eligible"]), max(1, sum(r["payload"]["kind"] == "news" for r in all_facts))),
              "entity": (sum(m["status"] == "resolved" for r in news["items"] for m in r["entities"]), max(1, len(news["eligible"]))),
              "event": (len(events["items"]) or int(events["empty_day"]), max(1, len({r["payload"]["event_id"] for r in all_facts if r["payload"]["kind"] == "event"}))),
              "flow": (0, 1)}
    domains = {}
    for rule in methods["rules"]:
        name = rule["domain"]
        faults = list(reasons[name])
        if rule["validator"] != name + "-p2-v1":
            faults.append("missing_required_validator" if rule["required"] else "missing_validator")
        if counts[name][0] < counts[name][1]:
            faults.append("insufficient_coverage")
        if rule["validator"] != name + "-p2-v1":
            status = "BLOCK"
        else:
            status = "PASS" if not faults else ("DEGRADED" if not rule["required"] and rule["allow_degraded"] else "BLOCK")
        domains[name] = {"status": status, "covered": counts[name][0], "expected": counts[name][1],
                         "reasons": sorted(set(faults)), "confidence_ceiling": {"PASS": "supported", "DEGRADED": "limited", "BLOCK": "none"}[status]}
    status = "BLOCK" if any(r["status"] == "BLOCK" for r in domains.values()) else (
        "DEGRADED" if any(r["status"] == "DEGRADED" for r in domains.values()) else "PASS")
    used = set(market["used_refs"] + news["used_refs"] + events["used_refs"])
    rejected_ids = {r["fact_id"] for r in rejected}
    for row in all_facts:
        if row["fact_id"] not in used and row["fact_id"] not in rejected_ids:
            domain = "market" if row["payload"]["kind"] == "bar" else ("news" if row["payload"]["kind"] == "news" else "event")
            rejected.append({"fact_id": row["fact_id"], "domain": domain, "reason": "not_used_by_method"})
    rejected_ids = {r["fact_id"] for r in rejected}
    sources = {r["source_id"]: r for r in methods["sources"]}
    references = []
    if status != "BLOCK":
        for row in sorted(all_facts, key=lambda r: r["fact_id"]):
            if row["fact_id"] in used - rejected_ids:
                references.append({key: row[key] for key in ("fact_id", "source_id", "locator", "mode", "published_at",
                                  "retrieved_at", "first_seen_at", "expires_at", "content_hash", "parser_version")} |
                                  {"tier": sources[row["source_id"]]["tier"]})
    content = {"schema_version": 1, "implementation_version": IMPLEMENTATION_VERSION,
               "mode": payload["mode"], "as_of": payload["as_of"], "status": status,
               "confidence_ceiling": {"PASS": "supported", "DEGRADED": "limited", "BLOCK": "none"}[status],
               "fingerprints": {"input": digest(payload), "method": digest(methods),
                   "profile": digest({"version": methods["profile_version"], "rules": methods["rules"]}),
                   "calendar": digest(payload["calendar"]), "mapping": digest(entities),
                   "universe": digest({"id": methods["universe_id"], "benchmarks": methods["benchmark_symbols"], "constituents": methods["constituents"]})},
               "domains": domains, "results": {"market": {k: v for k, v in market.items() if k not in {"used_refs", "conflicting_ids"}},
                   "news": news["items"], "events": {"items": events["items"], "empty_day": events["empty_day"]}, "flow": flow},
               "references": references, "rejected": sorted(rejected, key=lambda r: (r["domain"], r["fact_id"], r["reason"])),
               "live_market_coverage": {"covered": 0, "expected": 5, "missing": list(UNIVERSE)},
               "readiness": "BLOCKED_MARKET_SOURCES"}
    if len(canonical(content).encode()) > 2 * MAX_BYTES:
        raise Invalid("output byte limit")
    return {"bundle_id": digest(content), "content": content}


def verify_bundle(bundle, payload, methods, entities):
    exact(bundle, {"bundle_id", "content"})
    fingerprint(bundle["bundle_id"])
    if len(canonical(bundle).encode()) > 2 * MAX_BYTES:
        raise Invalid("bundle byte limit")
    if digest(bundle["content"]) != bundle["bundle_id"]:
        raise Invalid("bundle hash mismatch")
    if canonical(bundle) != canonical(build_bundle(payload, methods, entities)):
        raise Invalid("bundle recomputation mismatch")
    return True


def load_inputs():
    values = []
    for relative in ("tests/fixtures/p2_input.json", "config/p2_methods.toml", "config/p2_entities.toml"):
        path = ROOT / relative
        if any(p.is_symlink() or p.is_junction() for p in (path, *path.parents)):
            raise Invalid("linked demo asset")
        with path.open("rb") as handle:
            body = handle.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            raise Invalid("demo asset byte limit")
        values.append(decode_input(body) if path.suffix == ".json" else tomllib.loads(body.decode("utf-8")))
    return tuple(values)


def demo():
    payload, methods, entities = load_inputs()
    bundle = build_bundle(payload, methods, entities)
    verify_bundle(bundle, payload, methods, entities)
    return {"label": "SYNTHETIC TEST ONLY - NOT LIVE RESEARCH", "run_id": uuid.uuid4().hex,
            "generated_at": stamp(utc_now()), "bundle": bundle}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv != ["demo"]:
        print('{"reason":"invalid_command","status":"ERROR"}')
        return 2
    try:
        print(canonical(demo()))
        return 0
    except (Invalid, OSError, ValueError, RecursionError):
        print('{"reason":"synthetic_demo_failed","status":"ERROR"}')
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
