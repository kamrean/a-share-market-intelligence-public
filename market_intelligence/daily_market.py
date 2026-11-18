"""Private offline daily observations, not acquisition or authenticated evidence."""

import csv
from datetime import date
from decimal import Decimal
import io
import re

from . import intelligence as engine
from .gate import Calendar
from .market_state import array, numeric
from .models import Invalid, UNIVERSE, canonical, digest, exact, fingerprint, instant, integer, stamp, text
from .source_policy import (OPERATIONS, PURPOSE, assess_source_policy, bounded_copy,
                            identifier, validate_source_policy)

MAX_BYTES = 512000
MAX_ROWS = 1024
MAX_FIELD = 128
HEADER = ("date", "code", "open", "high", "low", "close", "preclose", "volume", "amount", "pctChg")
SYMBOLS = {"sh.000001": "000001.SH", "sz.399001": "399001.SZ"}
SCHEMA = "private-observed-daily-replay"
INPUT_VERSION = "p4m-daily-input-v1"
PARSER_VERSION = "baostock-normalized-daily-v1"
IMPLEMENTATION_VERSION = "p4m-offline-daily-v1"
TRANSPORT = "tcp://public-api.baostock.com:10030"
DOCUMENT = "https://www.baostock.com/mainContent?file=indexData.md"
DOC_ROLE = "documentation_only_not_download_origin"
ENGINE_LOCATOR = "https://www.baostock.com/helpdocs/api/markdown/indexData.md"
ENGINE_PREFIX = "https://www.baostock.com/helpdocs/api/markdown/"
ENGINE_ROLE = "documentation_endpoint_not_acquisition_endpoint"


def _declaration(value, policy):
    exact(value, {"schema_version", "version", "parser_version", "source_mode", "source_as_of",
                  "calendar", "custody", "hashes", "transport", "documentation_reference", "engine_locator"})
    integer(value["schema_version"], 1, 1)
    if (value["version"] != INPUT_VERSION or value["parser_version"] != PARSER_VERSION
            or value["source_mode"] != "live_observation"):
        raise Invalid("unknown daily input version, parser or mode")
    custody = value["custody"]
    exact(custody, {"source_id", "run_id", "request_id", "first_seen_at", "retrieved_at",
                    "observed_at", "expires_at", "published_at"})
    for key in ("source_id", "run_id", "request_id"):
        identifier(custody[key], key, 80 if key == "source_id" else 100)
    if custody["published_at"] is not None:
        raise Invalid("daily publication must remain unknown")
    source_id = custody["source_id"]
    transport = value["transport"]
    exact(transport, {"source_id", "run_id", "request_id", "locator", "protocol", "tls", "peer_authenticated"})
    if (any(transport[key] != custody[key] for key in ("source_id", "run_id", "request_id"))
            or transport["locator"] != TRANSPORT or transport["protocol"] != "tcp"
            or transport["tls"] is not False or transport["peer_authenticated"] is not False):
        raise Invalid("inconsistent or unauthentic daily transport declaration")
    document = value["documentation_reference"]
    exact(document, {"source_id", "locator", "locator_role"})
    if document != {"source_id": source_id, "locator": DOCUMENT, "locator_role": DOC_ROLE}:
        raise Invalid("daily documentation binding mismatch")
    projection = value["engine_locator"]
    exact(projection, {"source_id", "locator", "locator_prefix", "locator_role"})
    if projection != {"source_id": source_id, "locator": ENGINE_LOCATOR,
                      "locator_prefix": ENGINE_PREFIX, "locator_role": ENGINE_ROLE}:
        raise Invalid("daily engine documentation binding mismatch")
    hashes = value["hashes"]
    exact(hashes, {"normalized_csv_sha256", "original_response_sha256"})
    for item in hashes.values():
        fingerprint(item)
    if len(policy["sources"]) != 1 or policy["sources"][0]["source_id"] != source_id:
        raise Invalid("daily source policy identity mismatch")
    source = policy["sources"][0]
    if (source["origin"] != "https://www.baostock.com"
            or source["locator_prefix"] != ENGINE_PREFIX):
        raise Invalid("daily policy must bind documentation namespace only")
    decision = assess_source_policy(policy, source_id, "local_composition", PURPOSE)
    if decision["state"] != "permitted":
        raise Invalid("current local composition rights not permitted")
    seen, retrieved, observed, expiry = (instant(custody[key]) for key in
                                        ("first_seen_at", "retrieved_at", "observed_at", "expires_at"))
    cutoff = instant(value["source_as_of"])
    if not seen <= retrieved <= observed <= cutoff:
        raise Invalid("inconsistent daily possession or observation time")
    if expiry <= retrieved or (expiry - seen).total_seconds() > source["retention"]["max_age_seconds"]:
        raise Invalid("inconsistent expiry or source retention duration")
    return cutoff, expiry, decision


def _configuration(declaration, methods, entities, policy, guard):
    engine._validate_methods(methods, real=True, source_policy=policy)
    guard()
    engine._validate_entities(entities, methods, real=True)
    guard()
    source_id = declaration["custody"]["source_id"]
    if len(methods["sources"]) != 1 or methods["sources"][0]["source_id"] != source_id:
        raise Invalid("daily method source identity mismatch")
    for row in entities["entities"]:
        if (row["provider"] != source_id or row["provider_symbol"] not in SYMBOLS
                or SYMBOLS[row["provider_symbol"]] != row["entity_id"]
                or row["publication_delay_seconds"] is not None):
            raise Invalid("undocumented daily mapping or publication delay")
    calendar = declaration["calendar"]
    exact(calendar, {"version", "sessions"})
    array(calendar["sessions"], 128, "sessions")
    for session in calendar["sessions"]:
        exact(session, {"calendar_id", "timezone", "session_id", "close_at"})
        for key in session:
            text(session[key], key, 100)
    result = Calendar.from_dict(calendar)
    guard()
    return result


def _csv_rows(body, guard):
    try:
        decoded = body.decode("utf-8")
        if any(ord(char) < 32 and char not in "\r\n" for char in decoded):
            raise Invalid("CSV control character")
        reader = csv.reader(io.StringIO(decoded, newline=""), strict=True)
        if next(reader, None) != list(HEADER):
            raise Invalid("exact daily CSV header required")
        rows = []
        for row in reader:
            guard()
            if len(rows) >= MAX_ROWS:
                raise Invalid("daily CSV row limit")
            if len(row) != len(HEADER) or any(len(field) > MAX_FIELD or "\n" in field or "\r" in field for field in row):
                raise Invalid("daily CSV column or field limit")
            rows.append(row)
        guard()
        return rows
    except (UnicodeError, csv.Error) as exc:
        raise Invalid("invalid UTF-8 daily CSV structure") from exc


def _values(row):
    values = {}
    for name, raw in zip(HEADER[2:], row[2:]):
        if re.fullmatch(r"-?[0-9]+(?:\.[0-9]+)?", raw) is None:
            raise Invalid("invalid decimal")
        value = Decimal(raw)
        if (value.copy_abs() > Decimal("1e15")
                or (name in {"open", "high", "low", "close", "preclose"} and value < Decimal("1e-12"))
                or (name in {"volume", "amount"} and value < 0)):
            raise Invalid("decimal outside numeric bounds")
        numeric(float(value.copy_abs()) if name == "pctChg" else float(value),
                positive=name in {"open", "high", "low", "close", "preclose"})
        if name == "pctChg" and value < -100:
            raise Invalid("invalid percentage")
        values[name] = value
    if not (values["low"] <= min(values["open"], values["close"])
            <= max(values["open"], values["close"]) <= values["high"]):
        raise Invalid("inconsistent OHLC")
    return values


def _parse(body, declaration, methods, entities, calendar, guard):
    rows = _csv_rows(body, guard)
    groups = {}
    for index, row in enumerate(rows):
        groups.setdefault((row[0], row[1]), []).append(index)
    duplicates = {}
    for indices in groups.values():
        if len(indices) > 1:
            reason = "duplicate_code_date" if len({tuple(rows[i]) for i in indices}) == 1 else "conflicting_code_date"
            duplicates.update((i, reason) for i in indices)
    mappings = {row["entity_id"]: row for row in entities["entities"]}
    sessions = {(row.calendar_id, row.session_id): row for row in calendar.sessions}
    custody = declaration["custody"]
    seen = instant(custody["first_seen_at"])
    source = methods["sources"][0]
    facts, outcomes, reasons = [], [], {}
    for index, row in enumerate(rows):
        guard()
        session_id, code = row[:2]
        reason = duplicates.get(index)
        values, session = None, None
        if reason is None and code not in SYMBOLS:
            reason = "unverified_symbol" if code == "sz.399006" else "unsupported_symbol"
        if reason is None:
            try:
                if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", session_id) is None:
                    raise ValueError("noncanonical session date")
                date.fromisoformat(session_id)
            except ValueError:
                reason = "invalid_session_date"
        if reason is None:
            try:
                values = _values(row)
            except Invalid:
                reason = "invalid_daily_values"
        if reason is None:
            mapping = mappings.get(SYMBOLS[code])
            if mapping is None:
                reason = "unconfigured_mapping"
            else:
                session = sessions.get((mapping["calendar_id"], session_id))
                if session is None:
                    reason = "unavailable_calendar"
                elif instant(session.close_at) > seen:
                    reason = "session_not_closed_at_possession"
                elif source["tier"] == "C" or "market" not in source["scopes"]:
                    reason = "source_authority_scope"
        guard()
        outcome = {"row_number": index + 1, "row_hash": digest(row), "status": "rejected" if reason else "accepted",
                   "reason": reason, "fact_id": None}
        if reason:
            reasons[reason] = reasons.get(reason, 0) + 1
        else:
            payload = {"kind": "bar", "symbol": SYMBOLS[code], "session_id": session_id,
                       "close": float(values["close"]), "turnover": None}
            fact = {"source_id": custody["source_id"], "locator": ENGINE_LOCATOR,
                    "mode": declaration["source_mode"], "published_at": None,
                    "retrieved_at": custody["retrieved_at"], "first_seen_at": custody["first_seen_at"],
                    "expires_at": custody["expires_at"], "parser_version": engine.PARSERS["bar"],
                    "content_hash": digest(payload), "payload": payload}
            engine.Fact.from_dict(fact)
            guard()
            outcome["fact_id"] = digest(fact)
            facts.append(fact)
        outcomes.append(outcome)
    guard()
    return facts, {"total": len(rows), "accepted": len(facts), "rejected": len(rows) - len(facts),
                   "reasons": reasons, "rows": outcomes,
                   "outcome": "observations_supplied" if facts else "zero_eligible_observations"}


def _inputs(csv_bytes, declaration, methods, entities, source_policy, started):
    if type(csv_bytes) is not bytes or len(csv_bytes) > MAX_BYTES:
        raise Invalid("daily CSV byte limit")
    declaration, methods, entities = (bounded_copy(value) for value in (declaration, methods, entities))
    policy = validate_source_policy(source_policy)
    cutoff, expiry, decision = _declaration(declaration, policy)
    # The packet's deadline covers every row, including empty and rejected inputs.
    guard = engine._replay_guard(cutoff, expiry, started)
    guard()
    if declaration["hashes"]["normalized_csv_sha256"] != digest(csv_bytes):
        raise Invalid("normalized CSV hash mismatch")
    guard()
    calendar = _configuration(declaration, methods, entities, policy, guard)
    facts, parsed = _parse(csv_bytes, declaration, methods, entities, calendar, guard)
    payload = {"schema_version": 2, "source_mode": "live_observation", "as_of": declaration["source_as_of"],
               "calendar": declaration["calendar"], "facts": facts}
    guard()
    return declaration, methods, entities, policy, payload, parsed, decision, guard


def _decision_output(decision):
    result = bounded_copy(decision)
    for right in result["applicable_rights"].values():
        right["basis_refs"] = [{"locator": ref, "locator_role": ENGINE_ROLE if ref == ENGINE_LOCATOR
                                else "rights_basis_only_not_acquisition_origin"} for ref in right["basis_refs"]]
    return result


def _content(declaration, methods, entities, policy, payload, parsed, decision, guard, processing):
    custody = declaration["custody"]
    observation = {"source_id": custody["source_id"], "observed_at": custody["observed_at"],
                   "fact_ids": [digest(row) for row in payload["facts"]],
                   "locator": ENGINE_LOCATOR, "locator_role": ENGINE_ROLE}
    content = engine._compose_observed_daily(payload, methods, entities, policy, observation,
                                            instant(processing["started_at"]), guard)
    provenance = [{key: value for key, value in row.items() if key != "payload"}
                  | {"fact_id": digest(row), "locator_role": ENGINE_ROLE,
                     "observed_at": custody["observed_at"], "custody_hash": digest(custody),
                     "declaration_hash": digest(declaration)} for row in payload["facts"]]
    benchmarks = content["results"]["market"]["benchmarks"]
    for row in benchmarks:
        row.update({"availability_scope": "latest_completed_session_in_supplied_calendar_only",
                    "calendar_completeness_verified": False, "current_market_freshness": "unverified",
                    "finality_verified": False})
    content.update({
        "schema": SCHEMA, "schema_version": 1, "implementation_version": IMPLEMENTATION_VERSION,
        "source_mode": "live_observation", "processing_mode": "historical_replay",
        "source_as_of": declaration["source_as_of"], "processing": processing,
        "declaration": declaration, "parse": parsed, "provenance": provenance,
        "source_policy_decisions": [_decision_output(decision)],
        "source_policy_locator_role": "documentation_namespace_not_acquisition_origin",
        "normalized_csv_hash_verified": True, "original_response_hash_verified": False,
        "provenance_scope": "caller_supplied_only_not_acquisition_proof",
        "acquisition_verified": False, "finality_verified": False, "legal_entitlement_verified": False,
        "calendar_completeness_verified": False, "current_market_freshness": "unverified",
        "warnings": ["unknown_publication_time", "unauthenticated_caller_assertions",
                     "original_response_hash_unverified", "calendar_completeness_unverified",
                     "latest_declared_session_not_verified_current_quote"],
        "input_market_coverage": {"covered": sum(row["status"] == "available" for row in benchmarks),
                                  "expected": 5, "missing": [row["symbol"] for row in benchmarks
                                                              if row["status"] != "available"]},
        "expected_universe": {"id": methods["universe_id"], "benchmarks": list(UNIVERSE),
                              "constituents": methods["constituents"]},
        "readiness": "BLOCKED_EXTERNAL_PREREQUISITES", "permission_grant": False,
        "new_live_runs": 0, "network_requests": 0, "runtime_store_reads": 0,
        "operations_enabled": {operation: False for operation in OPERATIONS},
        "historical_receipts_reinterpreted": False,
    })
    content["fingerprints"].update({"declaration": digest(declaration), "source_policy": digest(policy),
                                     "sources": digest(methods["sources"]), "processing": digest(processing),
                                     "normalized_csv": declaration["hashes"]["normalized_csv_sha256"],
                                     "parse": digest(parsed), "source_as_of": digest(declaration["source_as_of"]),
                                     "projection": digest(observation)})
    guard()
    return content


def build_daily_replay_bundle(csv_bytes, declaration, methods, entities, source_policy):
    """Compute private supplied-session diagnostics; this grants no operation."""
    started = engine._replay_clock()
    inputs = _inputs(csv_bytes, declaration, methods, entities, source_policy, started)
    guard = inputs[-1]
    before_use = guard()
    processing = {"started_at": stamp(before_use), "completed_at": stamp(before_use), "clock": "system_utc"}
    content = _content(*inputs, processing)
    processing["completed_at"] = stamp(guard())
    content["fingerprints"]["processing"] = digest(processing)
    bundle = engine._replay_bundle(content)
    guard()
    return bundle


def verify_daily_replay_bundle(bundle, csv_bytes, declaration, methods, entities, source_policy):
    """Reparse and recompute all bindings under current retention and system time."""
    now = engine._replay_clock()
    bundle = bounded_copy(bundle, 2 * MAX_BYTES)
    exact(bundle, {"bundle_id", "content"})
    fingerprint(bundle["bundle_id"])
    content = bundle["content"]
    if (type(content) is not dict or content.get("schema") != SCHEMA
            or type(content.get("schema_version")) is not int or content["schema_version"] != 1):
        raise Invalid("invalid observed daily replay schema")
    processing = content.get("processing")
    exact(processing, {"started_at", "completed_at", "clock"})
    started, completed = instant(processing["started_at"]), instant(processing["completed_at"])
    if (processing["clock"] != "system_utc" or stamp(started) != processing["started_at"]
            or stamp(completed) != processing["completed_at"] or started > completed or completed > now):
        raise Invalid("invalid processing metadata or clock rollback")
    inputs = _inputs(csv_bytes, declaration, methods, entities, source_policy, now)
    declaration, guard = inputs[0], inputs[-1]
    for when in (started, completed):
        engine._retention_time(instant(declaration["source_as_of"]), instant(declaration["custody"]["expires_at"]), when)
    guard()
    expected = engine._replay_bundle(_content(*inputs, processing))
    if canonical(bundle) != canonical(expected):
        raise Invalid("daily replay recomputation mismatch")
    guard()
    return True
