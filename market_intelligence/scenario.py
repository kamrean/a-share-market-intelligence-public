"""Bounded authored scenarios, not live research or authenticated observations.

Verification re-executes the existing offline protocol. Content hashes bind a
local record; they are not signatures or independent evidence of external truth.
"""

from functools import lru_cache
import math
from pathlib import Path
import re
from tempfile import TemporaryDirectory

from . import research
from .cost_guard import BudgetStore
from .evaluation import measure
from .intelligence import build_bundle, decode_input, load_inputs
from .models import Invalid, canonical, digest, exact, instant, integer, stamp, utc_now

SCHEMA = "p4c-scenario-execution"
VERSION = "p4c-offline-scenario-v1"
LABEL = "SYNTHETIC OFFLINE SCENARIO; AUTHORED RESPONSES, NOT LIVE RESEARCH"
RESPONSE_LABEL = "AUTHORED SYNTHETIC RESPONSES; NOT MODEL OUTPUT"
MAX_CASE_BYTES = 128000
MAX_SOURCE_BYTES = 320000
CASE_KEYS = {"schema_version", "label", "payload", "methods", "entities",
             "research_config", "responses", "publication_config"}
SOURCE_KEYS = {"schema", "schema_version", "version", "label", "case", "case_id",
               "task_key", "intent_id", "started_at", "generated_at", "bundle",
               "research", "evaluation", "execution_digest"}


def _copy(value, limit):
    # JSON round trips alone silently accept tuples and non-string object keys.
    pending, count = [(value, 0)], 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if depth > 24 or count > 40000:
            raise Invalid("scenario structure bound")
        if type(item) is dict:
            if len(item) > 40000 or any(type(k) is not str for k in item):
                raise Invalid("JSON string keys required")
            pending.extend((v, depth + 1) for v in item.values())
        elif type(item) is list:
            if len(item) > 40000:
                raise Invalid("scenario array bound")
            pending.extend((v, depth + 1) for v in item)
        elif type(item) is str:
            if len(item) > limit or any(0xD800 <= ord(c) <= 0xDFFF for c in item):
                raise Invalid("invalid scenario text")
        elif item is None or type(item) in (bool, int):
            pass
        elif type(item) is float and math.isfinite(item):
            pass
        else:
            raise Invalid("plain finite JSON required")
    encoded = canonical(value).encode()
    if len(encoded) > limit:
        raise Invalid("scenario byte bound")
    return decode_input(encoded)


def _line(value, name, limit):
    research.safe_text(value, name, limit)
    if any(ord(c) < 32 or 0x7F <= ord(c) <= 0x9F or
           0x202A <= ord(c) <= 0x202E or 0x2066 <= ord(c) <= 0x2069 for c in value):
        raise Invalid("single visible line required")
    return value


def _responses(fixture, config):
    exact(fixture, {"schema_version", "label", "responses"})
    integer(fixture["schema_version"], 1, 1)
    if fixture["label"] != RESPONSE_LABEL:
        raise Invalid("authored response label required")
    rows = fixture["responses"]
    if type(rows) is not list or not 1 <= len(rows) <= 5:
        raise Invalid("script response bound")
    for row in rows:
        exact(row, {"role", "round", "body", "usage"})
        if type(row["role"]) is not str or row["role"] not in research.INSTRUCTIONS:
            raise Invalid("unknown scripted role")
        integer(row["round"], 1, config["max_rounds"])
        role, body = row["role"], row["body"]
        key = {"researcher": "claims", "reviewer": "verdicts", "editor": "selected_ids"}[role]
        exact(body, {key})
        if type(body[key]) is not list or not 1 <= len(body[key]) <= config["max_claims"]:
            raise Invalid("script body bound")
        for item in body[key]:
            if role == "researcher":
                exact(item, research.Claim.__dataclass_fields__)
            elif role == "reviewer":
                exact(item, {"claim_id", "claim_digest", "verdict", "reason"})
            elif type(item) is not str:
                raise Invalid("editor claim identity required")
        if row["usage"] is not None:
            exact(row["usage"], {"input_tokens", "output_tokens"})
            for used in row["usage"].values():
                integer(used, 0, 8 * 1024 * 1024)
        if len(canonical({"body": body, "usage": row["usage"]}).encode()) > config["max_response_bytes"]:
            raise Invalid("script response byte bound")


def _validate_rejections(case, bundle):
    content = bundle["content"]
    rules = {r["domain"]: r for r in case["methods"]["rules"]}
    allowed = {"stale_news", "expired_evidence", "not_used_by_method"}
    for row in content["rejected"]:
        rule = rules[row["domain"]]
        if (row["reason"] not in allowed or rule["required"] or not rule["allow_degraded"]
                or content["domains"][row["domain"]]["status"] != "DEGRADED"):
            raise Invalid("scenario contains rejected evidence")

    expired = {r["fact_id"] for r in content["rejected"] if r["reason"] == "expired_evidence"}
    if not expired:
        return
    # P2 records one preparation reason; expiry can mask possession/source faults.
    # Recheck without expiry only for diagnostics, never as publishable evidence.
    payload = _copy(case["payload"], MAX_CASE_BYTES)
    news_ids = {}
    for fact in payload["facts"]:
        identity = digest(fact)
        if identity in expired:
            fact["expires_at"] = None
        if fact["payload"]["kind"] == "news":
            news_ids[identity] = digest(fact)
    for fact in payload["facts"]:
        if "news_refs" in fact["payload"]:
            fact["payload"]["news_refs"] = [news_ids.get(ref, ref) for ref in fact["payload"]["news_refs"]]
            fact["content_hash"] = digest(fact["payload"])
    checked = build_bundle(payload, case["methods"], case["entities"])
    if any(r["reason"] not in {"stale_news", "not_used_by_method"} for r in checked["content"]["rejected"]):
        raise Invalid("expired evidence hides invalid evidence")


def validate_case(case):
    """Validate and detach inputs; semantic role refusals remain execution results."""
    from .publication import validate_config
    try:
        case = _copy(case, MAX_CASE_BYTES)
        exact(case, CASE_KEYS)
        integer(case["schema_version"], 1, 1)
        _line(case["label"], "synthetic case label", 160)
        if not case["label"].startswith("SYNTHETIC "):
            raise Invalid("explicit synthetic case label required")
        config = research.validate_config(case["research_config"])
        if config["adapter"] != "synthetic_fixture":
            raise Invalid("only authored offline adapter allowed")
        for role in config["roles"].values():
            if re.fullmatch(r"synthetic-[A-Za-z0-9_.-]{1,64}", role["model"]) is None:
                raise Invalid("synthetic model identity required")
        validate_config(case["publication_config"])
        _responses(case["responses"], config)
        bundle = build_bundle(case["payload"], case["methods"], case["entities"])
        if any(f["mode"] != "synthetic_test" for f in case["payload"]["facts"]):
            raise Invalid("mixed evidence modes forbidden")
        _validate_rejections(case, bundle)
        return case
    except (KeyError, TypeError, ValueError, RecursionError, OverflowError) as exc:
        raise Invalid("invalid synthetic scenario") from exc


def example_case():
    """Return a self-contained editable example, without executing research."""
    from .publication import load_config
    payload, methods, entities = load_inputs()
    config, responses = research.load_assets()
    return validate_case({"schema_version": 1, "label": LABEL, "payload": payload,
                          "methods": methods, "entities": entities, "research_config": config,
                          "responses": responses, "publication_config": load_config()})


def _run(case, bundle, store, task_key):
    adapter = research.SyntheticAdapter(case["responses"])
    result = research.run_research(bundle, case["payload"], case["methods"], case["entities"],
                                   case["research_config"], store, task_key, adapter)
    if result["status"] == "COMPLETE_SYNTHETIC" and adapter.index != len(adapter.responses):
        raise Invalid("unused authored responses")
    return result


def _evaluation(case, bundle, result):
    if result["status"] != "COMPLETE_SYNTHETIC":
        return None
    return measure(result["claims"], bundle["content"]["references"], bundle["content"]["domains"],
                   {r["entity_id"] for r in case["entities"]["entities"]}, case["payload"]["as_of"])


def execute_case(case, budget_store, task_key):
    """Execute authored responses with persistent reservations, never a live API."""
    started = stamp(utc_now())
    case = validate_case(case)
    _line(task_key, "task key", 160)
    if type(budget_store) is not BudgetStore or budget_store.config_id != digest(case["research_config"]):
        raise Invalid("matching persistent budget store required")
    bundle = build_bundle(case["payload"], case["methods"], case["entities"])
    research.verified_input(bundle, case["payload"], case["methods"], case["entities"])
    result = _run(case, bundle, budget_store, task_key)
    case_id = digest(case)
    source = {"schema": SCHEMA, "schema_version": 1, "version": VERSION, "label": LABEL,
              "case": case, "case_id": case_id, "task_key": task_key,
              "intent_id": digest({"case_id": case_id, "task_key": task_key}),
              "started_at": started, "generated_at": stamp(utc_now()), "bundle": bundle,
              "research": result, "evaluation": _evaluation(case, bundle, result)}
    source["execution_digest"] = digest(source)
    return _copy(source, MAX_SOURCE_BYTES)


def _substantive(result):
    result = _copy(result, MAX_SOURCE_BYTES)
    result.pop("run_id")
    accounting = result["accounting"]
    accounting.pop("started_at")
    accounting.pop("finished_at")
    for call in result["attempts"]:
        call.pop("elapsed_seconds")
    for number, attempt in enumerate(accounting["attempts"], 1):
        for key in ("run_id", "reserved_at", "day"):
            attempt.pop(key)
        attempt["attempt_id"] = number
    return result


@lru_cache(maxsize=8)
def _replay(encoded_case):
    # The cached value is immutable bytes of fully recomputed substantive data.
    case = decode_input(encoded_case.encode())
    bundle = build_bundle(case["payload"], case["methods"], case["entities"])
    with TemporaryDirectory(prefix="p4c-verify-") as directory:
        with BudgetStore(Path(directory) / "budget.sqlite", case["research_config"]) as store:
            result = _run(case, bundle, store, "scenario-verification")
    if result["status"] != "COMPLETE_SYNTHETIC":
        raise Invalid("scenario does not complete on recomputation")
    return canonical({"bundle": bundle, "research": _substantive(result),
                      "evaluation": _evaluation(case, bundle, result)})


def _time(value):
    when = instant(value)
    if stamp(when) != value:
        raise Invalid("canonical execution timestamp required")
    return when


def _execution_metadata(source):
    result, config = source["research"], source["case"]["research_config"]
    accounting = result["accounting"]
    if type(result["run_id"]) is not str or re.fullmatch(r"[0-9a-f]{32}", result["run_id"]) is None:
        raise Invalid("invalid scenario run identity")
    started, generated = map(_time, (source["started_at"], source["generated_at"]))
    begin, finish = map(_time, (accounting["started_at"], accounting["finished_at"]))
    if not instant(result["as_of"]) <= started <= begin <= finish <= generated <= utc_now():
        raise Invalid("invalid scenario execution chronology")
    calls, attempts = result["attempts"], accounting["attempts"]
    if type(calls) is not list or type(attempts) is not list or not 3 <= len(calls) == len(attempts) <= 5:
        raise Invalid("invalid executed call count")
    previous_time, previous_id = begin, None
    limits = config["budget"]
    day_totals = {}
    for index, (call, row) in enumerate(zip(calls, attempts)):
        when = _time(row["reserved_at"])
        integer(row["attempt_id"], 1, limits["max_runs"] * limits["calls_per_run"])
        if (row["run_id"] != result["run_id"] or not previous_time <= when <= finish
                or row["day"] != stamp(when)[:10]
                or (previous_id is not None and row["attempt_id"] != previous_id + 1)):
            raise Invalid("invalid persistent attempt metadata")
        elapsed = call["elapsed_seconds"]
        if (type(elapsed) not in (float, int) or not math.isfinite(elapsed)
                or not 0 <= elapsed <= config["timeout_seconds"]):
            raise Invalid("invalid dispatch duration")
        # Wall and monotonic clocks differ slightly; tolerate only clock precision.
        next_at = _time(attempts[index + 1]["reserved_at"]) if index + 1 < len(attempts) else finish
        if elapsed > (next_at - when).total_seconds() + 0.02:
            raise Invalid("dispatch duration exceeds execution window")
        day_totals[row["day"]] = day_totals.get(row["day"], 0) + 1
        previous_time, previous_id = when, row["attempt_id"]
    for count in day_totals.values():
        if (count > limits["calls_per_day"] or count * limits["input_per_call"] > limits["input_per_day"]
                or count * limits["output_per_call"] > limits["output_per_day"]):
            raise Invalid("scenario day reservation limit")


def verify_execution(source):
    """Return a detached successful source after substantive replay and checks."""
    try:
        source = _copy(source, MAX_SOURCE_BYTES)
        exact(source, SOURCE_KEYS)
        integer(source["schema_version"], 1, 1)
        if source["schema"] != SCHEMA or source["version"] != VERSION or source["label"] != LABEL:
            raise Invalid("unknown scenario execution schema")
        case = validate_case(source["case"])
        _line(source["task_key"], "task key", 160)
        if (source["case_id"] != digest(case)
                or source["intent_id"] != digest({"case_id": source["case_id"], "task_key": source["task_key"]})
                or source["execution_digest"] != digest({k: v for k, v in source.items() if k != "execution_digest"})):
            raise Invalid("scenario execution binding mismatch")
        if source["research"]["status"] != "COMPLETE_SYNTHETIC":
            raise Invalid("blocked research cannot publish")
        _execution_metadata(source)
        expected = _replay(canonical(case))
        actual = canonical({"bundle": source["bundle"], "research": _substantive(source["research"]),
                            "evaluation": source["evaluation"]})
        if actual != expected:
            raise Invalid("scenario recomputation mismatch")
        return source
    except (KeyError, TypeError, ValueError, RecursionError, OverflowError, OSError) as exc:
        raise Invalid("invalid scenario execution") from exc
