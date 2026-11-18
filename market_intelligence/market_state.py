"""Bounded synthetic market methods; no collector or persistent state."""

from dataclasses import asdict, dataclass
import math

from .gate import Calendar
from .models import (Invalid, MARKETS, UNIVERSE, exact, instant, integer, locator, text)
from .source_policy import https_location, identifier, validate_source_policy

DOMAINS = frozenset({"market", "sector", "flow", "news", "event", "entity"})
FLOW_KINDS = frozenset({"provider_estimate", "exchange_statistic", "turnover_proxy"})


def array(value, limit, name, minimum=0):
    if type(value) is not list or not minimum <= len(value) <= limit:
        raise Invalid("invalid " + name + " array")
    return value


def numeric(value, *, positive=False):
    if (type(value) not in (int, float) or value < 0 or value > 1e15
            or not math.isfinite(value) or (positive and value < 1e-12)):
        raise Invalid("invalid bounded number")
    return value


def unique_strings(value, limit, name, minimum=0, width=100):
    array(value, limit, name, minimum)
    for item in value:
        text(item, name, width)
    if len(set(value)) != len(value):
        raise Invalid("duplicate " + name)
    return value


def validate_methods(data):
    return _validate_methods(data)


def _validate_methods(data, *, real=False, source_policy=None):
    exact(data, {"schema_version", "version", "profile_version", "universe_id",
                 "benchmark_symbols", "constituents", "lookback_sessions",
                 "news_half_life_seconds", "news_ttl_seconds", "approach_seconds",
                 "confirmation_grace_seconds", "event_ttl_seconds", "flow", "sources", "rules"})
    integer(data["schema_version"], 2 if real else 1, 2 if real else 1)
    for key in ("version", "profile_version", "universe_id"):
        text(data[key], key, 100)
    if data["benchmark_symbols"] != list(UNIVERSE):
        raise Invalid("fixed benchmark universe changed")
    unique_strings(data["constituents"], 27, "constituents", 0 if real else 1)
    if set(data["constituents"]) & set(UNIVERSE):
        raise Invalid("benchmark is not a constituent")
    integer(data["lookback_sessions"], 1, 30)
    for key in ("news_half_life_seconds", "news_ttl_seconds", "approach_seconds",
                "confirmation_grace_seconds", "event_ttl_seconds"):
        integer(data[key], 1, 31536000)
    exact(data["flow"], {"provider", "series_kind", "enabled"})
    text(data["flow"]["provider"], "provider", 80)
    text(data["flow"]["series_kind"], "series_kind", 80)
    if data["flow"]["series_kind"] not in FLOW_KINDS or data["flow"]["enabled"] is not False:
        raise Invalid("only unavailable flow is implemented")
    array(data["sources"], 8, "sources", 0 if real else 1)
    policies = ({s["source_id"]: s for s in validate_source_policy(source_policy)["sources"]}
                if real else {})
    ids = set()
    for source in data["sources"]:
        exact(source, {"source_id", "tier", "locator_prefix", "scopes", "event_ids", "synthetic"})
        text(source["source_id"], "source_id", 80)
        text(source["tier"], "tier", 1)
        locator(source["locator_prefix"])
        if source["source_id"] in ids or source["tier"] not in {"A", "B", "C"}:
            raise Invalid("invalid source registry identity or tier")
        if real:
            identifier(source["source_id"], "source", 80)
            https_location(source["locator_prefix"], prefix=True)
            policy = policies.get(source["source_id"])
            if (source["synthetic"] is not False or policy is None
                    or source["locator_prefix"] != policy["locator_prefix"]):
                raise Invalid("real source policy binding mismatch")
        elif (source["synthetic"] is not True
                or not source["locator_prefix"].startswith("https://example.invalid/")
                or not source["locator_prefix"].endswith("/")):
            raise Invalid("invalid synthetic source registry")
        ids.add(source["source_id"])
        unique_strings(source["scopes"], 5, "scopes", 1)
        if not set(source["scopes"]) <= {"market", "news", "event", "impact", "reaction"}:
            raise Invalid("unknown authority scope")
        unique_strings(source["event_ids"], 20, "event authority")
    if real and ids != set(policies):
        raise Invalid("method and policy source identities disagree")
    array(data["rules"], 6, "rules", 6)
    domains = set()
    for rule in data["rules"]:
        exact(rule, {"domain", "validator", "required", "allow_degraded"})
        text(rule["domain"], "domain", 20)
        if rule["domain"] not in DOMAINS or rule["domain"] in domains:
            raise Invalid("unknown or duplicate domain rule")
        domains.add(rule["domain"])
        text(rule["validator"], "validator", 100)
        if type(rule["required"]) is not bool or type(rule["allow_degraded"]) is not bool:
            raise Invalid("boolean domain rule required")
        if rule["required"] and rule["allow_degraded"]:
            raise Invalid("required domain cannot permit degradation")
    return data


def validate_entities(data, methods):
    return _validate_entities(data, methods)


def _validate_entities(data, methods, *, real=False):
    exact(data, {"schema_version", "version", "mapping_refs", "entities"})
    integer(data["schema_version"], 2 if real else 1, 2 if real else 1)
    text(data["version"], "mapping version", 100)
    unique_strings(data["mapping_refs"], 8, "mapping references", 0 if real else 1, 2048)
    for ref in data["mapping_refs"]:
        locator(ref)
        if real:
            https_location(ref)
    array(data["entities"], 32, "entities", 0 if real else 1)
    if real and data["entities"] and not data["mapping_refs"]:
        raise Invalid("configured mapping requires source references")
    ids = set()
    sources = {s["source_id"] for s in methods["sources"]}
    for row in data["entities"]:
        exact(row, {"entity_id", "aliases", "sector_id", "calendar_id", "currency", "unit", "provider"}
              | ({"provider_symbol", "publication_delay_seconds"} if real else set()))
        for key in ("entity_id", "sector_id", "calendar_id", "currency", "unit", "provider"):
            text(row[key], key, 100)
        unique_strings(row["aliases"], 8, "aliases", 1, 200)
        if (row["entity_id"] in ids or row["calendar_id"] not in {x[0] for x in MARKETS.values()}
                or row["provider"] not in sources):
            raise Invalid("invalid entity identity or provider")
        if row["entity_id"] in MARKETS and row["calendar_id"] != MARKETS[row["entity_id"]][0]:
            raise Invalid("benchmark calendar mismatch")
        if row["entity_id"] in MARKETS and row["sector_id"] != "benchmark":
            raise Invalid("benchmark sector mismatch")
        if real:
            identifier(row["provider_symbol"], "provider symbol")
            if row["publication_delay_seconds"] is not None:
                integer(row["publication_delay_seconds"], 0, 31536000)
            currency = {"SSE": "CNY", "SZSE": "CNY", "KRX": "KRW", "US_CASH": "USD"}[row["calendar_id"]]
            unit = "index_points" if row["entity_id"] in MARKETS else "currency_units"
            if row["currency"] != currency or row["unit"] != unit:
                raise Invalid("inconsistent currency or unit")
            provider = next(s for s in methods["sources"] if s["source_id"] == row["provider"])
            if "market" not in provider["scopes"]:
                raise Invalid("mapped provider lacks market authority scope")
        ids.add(row["entity_id"])
    expected = set(UNIVERSE) | set(methods["constituents"])
    if (real and not ids <= expected) or (not real and ids != expected):
        raise Invalid("mapping does not match declared universe")
    constituents = [r for r in data["entities"] if r["entity_id"] in methods["constituents"]]
    combinations = len({(r["currency"], r["unit"]) for r in constituents})
    if combinations > 1 or (not real and combinations != 1):
        raise Invalid("cross-currency or incompatible turnover universe")
    if any(r["sector_id"] == "benchmark" for r in constituents):
        raise Invalid("constituent requires a sector")
    return data


@dataclass(frozen=True)
class FlowResult:
    status: str
    provider: str
    series_kind: str
    value: None
    unit: None
    as_of: str
    reason: str

    def to_dict(self):
        return asdict(self)


class UnavailableFlowAdapter:
    def evaluate(self, provider, series_kind, as_of):
        text(provider, "provider", 80)
        instant(as_of)
        if series_kind not in FLOW_KINDS:
            raise Invalid("unknown flow series kind")
        return FlowResult("unavailable", provider, series_kind, None, None, as_of, "not_integrated")


def rounded(value):
    return round(value, 10)


def evaluate_market(facts, methods, entities, calendar, as_of):
    return _evaluate_market(facts, methods, entities, calendar, as_of)


def _evaluate_market(facts, methods, entities, calendar, as_of, *, real=False, source_policy=None, guard=None):
    """Evaluate already eligible facts against the complete declared session grid."""
    _validate_methods(methods, real=real, source_policy=source_policy)
    _validate_entities(entities, methods, real=real)
    calendar = Calendar.from_dict(calendar)
    cutoff = instant(as_of)
    mapping = {r["entity_id"]: r for r in entities["entities"]}
    # Shared validation may consume the remaining retention window.
    if guard is not None:
        guard()
    bars, conflicts, used = {}, [], set()
    constituent_conflict = False
    for fact in facts:
        payload = fact["payload"]
        if payload["kind"] != "bar":
            continue
        key = (payload["symbol"], payload["session_id"])
        bars.setdefault(key, []).append(fact)
    selected = {}
    for key, group in sorted(bars.items()):
        if len({(r["payload"]["close"], r["payload"]["turnover"]) for r in group}) != 1:
            conflicts.extend(r["fact_id"] for r in group)
            constituent_conflict = constituent_conflict or key[0] in methods["constituents"]
        else:
            selected[key] = group
    lookback = methods["lookback_sessions"]
    windows = {}
    for symbol, info in sorted(mapping.items()):
        sessions = sorted((s for s in calendar.sessions if s.calendar_id == info["calendar_id"]
                           and instant(s.close_at) <= cutoff), key=lambda s: instant(s.close_at))
        windows[symbol] = sessions

    def sample(symbol, session):
        group = selected.get((symbol, session.session_id), [])
        used.update(r["fact_id"] for r in group)
        return group[0]["payload"] if group else None

    def window(symbol, count):
        sessions = windows.get(symbol, [])[-count:]
        rows = [sample(symbol, s) for s in sessions]
        return sessions, rows, len(sessions) == count and all(r is not None for r in rows)

    benchmarks = []
    for symbol in UNIVERSE:
        sessions, rows, complete = window(symbol, 1)
        benchmarks.append({"symbol": symbol, "value": rows[0]["close"] if complete else None,
                           "session_id": sessions[0].session_id if sessions else None,
                           "status": "available" if complete else "unavailable"})
    latest, returns, long_returns, prior_returns, sector_members = {}, {}, {}, {}, {}
    for symbol in sorted(methods["constituents"]):
        if symbol not in mapping:
            continue
        info = mapping[symbol]
        sector_members.setdefault(info["sector_id"], []).append(symbol)
        sessions, rows, complete = window(symbol, 2)
        if complete:
            latest[symbol] = (sessions[-1], rows[-1])
            returns[symbol] = rows[-1]["close"] / rows[-2]["close"] - 1
        _, rows, complete = window(symbol, 2 * lookback + 1)
        if complete:
            long_returns[symbol] = rows[-1]["close"] / rows[lookback]["close"] - 1
            prior_returns[symbol] = rows[lookback]["close"] / rows[0]["close"] - 1
    expected = len(methods["constituents"])
    compatible = len({instant(v[0].close_at) for v in latest.values()}) <= 1
    full = expected > 0 and len(returns) == expected and compatible
    counts = {"advancing": sum(v > 0 for v in returns.values()),
              "declining": sum(v < 0 for v in returns.values()),
              "unchanged": sum(v == 0 for v in returns.values())}
    breadth = {"status": "available" if full else "insufficient_history", **counts,
               "eligible": len(returns), "expected": expected,
               "ratios": {k: rounded(v / expected) for k, v in counts.items()} if full else None}
    turnover = sum(v[1]["turnover"] for v in latest.values()) if full else None
    historical_totals = []
    for offset in range(1, lookback + 1):
        amounts, closes = [], []
        for symbol in sorted(methods["constituents"]):
            sessions = windows.get(symbol, [])
            if len(sessions) <= offset:
                break
            row = sample(symbol, sessions[-1 - offset])
            if row is None:
                break
            amounts.append(row["turnover"])
            closes.append(instant(sessions[-1 - offset].close_at))
        if len(amounts) == expected and len(set(closes)) == 1:
            historical_totals.append(sum(amounts))
    baseline = sum(historical_totals) / lookback if len(historical_totals) == lookback else None
    relative = rounded(turnover / baseline) if turnover is not None and baseline not in (None, 0) else None
    first = mapping.get(methods["constituents"][0], {}) if expected else {}
    turnover_result = {"value": turnover, "currency": first.get("currency"),
                       "unit": first.get("unit"), "relative": relative,
                       "reason": "insufficient_history" if turnover is None or baseline is None else (
                           "zero_baseline" if baseline == 0 else None)}
    rotation_complete = (full and len(long_returns) == expected
                         and len({tuple(instant(s.close_at) for s in windows[k][-2 * lookback - 1:])
                                  for k in methods["constituents"]}) == 1)
    strength, previous = {}, {}
    if rotation_complete:
        for sector, members in sorted(sector_members.items()):
            strength[sector] = sum(long_returns[k] for k in members) / len(members)
            previous[sector] = sum(prior_returns[k] for k in members) / len(members)
    ranks = {k: n + 1 for n, k in enumerate(sorted(strength, key=lambda k: (-strength[k], k)))}
    old_ranks = {k: n + 1 for n, k in enumerate(sorted(previous, key=lambda k: (-previous[k], k)))}
    sectors = []
    for sector, members in sorted(sector_members.items()):
        sector_turnover = sum(latest[k][1]["turnover"] for k in members) if full else None
        sectors.append({"sector_id": sector, "eligible": sum(k in long_returns for k in members),
                        "expected": len(members), "equal_weight_return": rounded(strength[sector]) if rotation_complete else None,
                        "rank": ranks.get(sector), "rank_change": old_ranks[sector] - ranks[sector] if rotation_complete else None,
                        "turnover_share": rounded(sector_turnover / turnover) if turnover not in (None, 0) else None,
                        "turnover_share_reason": "zero_total_turnover" if turnover == 0 else ("insufficient_history" if turnover is None else None),
                        "status": "available" if rotation_complete else "insufficient_history"})
    market_reasons = ([] if all(r["status"] == "available" for r in benchmarks) else ["missing_benchmarks"])
    if not full or turnover_result["reason"]:
        market_reasons.append(turnover_result["reason"] or "incompatible_sessions")
    if conflicts:
        market_reasons.append("conflicting_market_evidence")
    sector_reasons = ([] if rotation_complete and not constituent_conflict else
                      ["insufficient_history" if not constituent_conflict else "conflicting_market_evidence"])
    if real:
        if not methods["constituents"]:
            market_reasons.append("unconfigured_constituents")
            sector_reasons.append("unconfigured_constituents")
        if set(mapping) != set(UNIVERSE) | set(methods["constituents"]):
            market_reasons.append("unconfigured_mapping")
        if not set(methods["constituents"]) <= set(mapping):
            sector_reasons.append("unconfigured_mapping")
        if any(not windows.get(s) for s in (*UNIVERSE, *methods["constituents"])):
            market_reasons.append("unavailable_calendar")
    return {"benchmarks": benchmarks, "breadth": breadth, "turnover": turnover_result,
            "sectors": sectors, "market_reasons": sorted(set(market_reasons)),
            "sector_reasons": sorted(set(sector_reasons)),
            "conflicting_ids": sorted(conflicts), "used_refs": sorted(used - set(conflicts))}
