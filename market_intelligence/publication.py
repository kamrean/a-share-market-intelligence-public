"""Fixed authored P3 publication only; no live publication authorization."""

from functools import lru_cache
from pathlib import Path
import re
import sqlite3
import sys
from tempfile import TemporaryDirectory
import tomllib
import uuid

from . import research
from .intelligence import ROOT, decode_input, load_inputs
from .models import Invalid, canonical, digest, exact, fingerprint, instant, integer, stamp, utc_now

VERSION = "p4-fixed-synthetic-v1"
LABEL = "SYNTHETIC LOCAL PUBLICATION; NOT REAL TELEGRAM DELIVERY"
CORRECTION_REASON = "Clarification: synthetic acknowledgements are not real delivery; live coverage remains 0/5."
ASSETS = {
    "tests/fixtures/p2_input.json": "3234ab1eedf67921ee679fa772f1a24d3e0fda59b7dcf3cc8efa8bf9f656c3ec",
    "config/p2_methods.toml": "33cc5cc04afba6dd5ac711edf4d4f29dd3f9c83327b16fff0f792a1c03bc32ec",
    "config/p2_entities.toml": "b375f00ceb4f822ebed9e896c2c97a6e2eeb8b03cbb50c2f0d561f05ea6531e8",
    "config/p3_research.toml": "fc7d91203c154e5aca78904a0e0f8f16d8acab8c6a778679e6a4197b52edeb7a",
    "tests/fixtures/p3_responses.json": "64c647733a780bc20083ee6e7b77fa6cbf83802316c59dfcf257a8c3131765c0",
}
LIMITATIONS = [
    "Fixed authored synthetic dataset; historical as_of is not current market news.",
    "Live benchmark coverage 0/5; flow unavailable, never zero-filled.",
    "Model and real Telegram unverified; MVP not_implemented; observation not_started; VPS not_authorized.",
    "Citation validity and synthetic role agreement are not truth, entailment or causal verification.",
]
GATES = {"stage7": "partial_offline_engineering", "stage8": "partial_offline_quality",
         "live_market_coverage": "0/5", "real_model": "unverified", "real_telegram": "unverified",
         "research_mvp": "not_implemented", "forward_observation": "not_started", "vps": "not_authorized"}


def validate_config(config):
    exact(config, {"schema_version", "version", "adapter", "sink", "chunk_chars", "max_chunks",
                   "max_report_bytes", "max_reports", "max_attempts", "max_store_bytes",
                   "attempts_per_run", "attempts_per_day", "chars_per_run", "chars_per_day",
                   "rate_calls", "rate_seconds", "timeout_seconds"})
    integer(config["schema_version"], 1, 1)
    if (config["version"] != VERSION or config["adapter"] != "offline_fake"
            or config["sink"] != "synthetic-local-sink"):
        raise Invalid("fixed offline configuration required")
    bounds = {"chunk_chars": (2048, 8192), "max_chunks": (3, 96), "max_report_bytes": (1024, 500000),
              "max_reports": (1, 64), "max_attempts": (1, 512), "max_store_bytes": (131072, 8388608),
              "attempts_per_run": (1, 96), "attempts_per_day": (1, 512),
              "chars_per_run": (1, 1000000), "chars_per_day": (1, 4000000),
              "rate_calls": (1, 512), "rate_seconds": (1, 86400), "timeout_seconds": (1, 30)}
    for key, (low, high) in bounds.items():
        integer(config[key], low, high)
    return config


def load_config():
    path = ROOT / "config/p4_publication.toml"
    if any(p.is_symlink() or p.is_junction() for p in (path, *path.parents)):
        raise Invalid("linked publication configuration")
    with path.open("rb") as stream:
        body = stream.read(8193)
    if len(body) > 8192:
        raise Invalid("configuration bound")
    return validate_config(tomllib.loads(body.decode("utf-8")))


def _asset_key():
    values = []
    for name, expected in ASSETS.items():
        path = ROOT / name
        if any(p.is_symlink() or p.is_junction() for p in (path, *path.parents)):
            raise Invalid("linked fixed asset")
        with path.open("rb") as stream:
            body = stream.read(512001)
        actual = digest(body.replace(b"\r\n", b"\n"))
        if len(body) > 512000 or actual != expected:
            raise Invalid("original fixed asset mismatch")
        values.append(actual)
    return tuple(values)


@lru_cache(maxsize=8)
def _verified_source(encoded, assets):
    # Cache immutable bytes, never caller-owned objects or an unvalidated hash.
    sample = decode_input(encoded.encode())
    research.verify_demo(sample)
    if _asset_key() != assets:
        raise Invalid("fixed assets changed during verification")
    return encoded


def verify_source(sample):
    try:
        encoded = canonical(sample)
        if len(encoded) > 240000:
            raise Invalid("source size bound")
        _verified_source(encoded, _asset_key())
        if instant(sample["generated_at"]) > utc_now():
            raise Invalid("future execution metadata")
        return decode_input(encoded.encode())
    except (KeyError, TypeError, RecursionError, ValueError) as exc:
        raise Invalid("invalid fixed P3 input") from exc


def _references(value, known):
    if type(value) is str:
        return {value} if value in known else set()
    if type(value) is dict:
        return set().union(*(_references(v, known) for v in value.values())) if value else set()
    if type(value) is list:
        return set().union(*(_references(v, known) for v in value)) if value else set()
    return set()


def _sections(source):
    bundle = source["bundle"]["content"]
    results = bundle["results"]
    refs = {r["fact_id"]: r for r in bundle["references"]}
    payload, _, _ = load_inputs()
    facts = {digest(row): row["payload"] for row in payload["facts"]}
    bars = sorted(i for i in refs if facts[i]["kind"] == "bar")
    sections = []

    def section(title, entries):
        items = []
        for kind, data, evidence in entries:
            item = {"kind": kind, "data": data, "evidence_ids": sorted(set(evidence)),
                    "method_id": bundle["fingerprints"]["method"]}
            items.append({"item_id": digest(item), **item})
        used = sorted({i for row in items for i in row["evidence_ids"]})
        sections.append({"title": title, "items": items, "sources": [refs[i] for i in used]})

    news = [("sourced_news", row | {"titles": sorted({facts[i]["title"] for i in row["member_ids"]})},
             row["member_ids"]) for row in results["news"]]
    events = [("event_state_not_causal_claim", row, _references(row, refs)) for row in results["events"]["items"]]
    section("1. Sourced news and upcoming events", news + events or [
        ("empty_fixed_day", {"news": "none", "events": "none", "live_discovery": "unverified"}, [])])
    market = results["market"]
    section("2. Benchmarks, breadth, turnover and unavailable flow", [
        ("benchmark", row, [i for i in bars if facts[i]["symbol"] == row["symbol"]])
        for row in market["benchmarks"]] + [
        ("breadth", market["breadth"], bars), ("turnover", market["turnover"], bars),
        ("flow_unavailable", results["flow"], [])])
    section("3. Sectors and approved claims", [
        ("sector_statistic", row, bars) for row in market["sectors"]] + [
        ("approved_" + row["kind"], row, row["evidence_ids"]) for row in source["research"]["claims"]])
    if {s["fact_id"] for part in sections for s in part["sources"]} != set(refs):
        raise Invalid("incomplete source coverage")
    return sections


def split_text(value, limit):
    """Lossless Unicode code-point splitting, preferring complete lines."""
    integer(limit, 1, 8192)
    if type(value) is not str or not value or len(value) > 200000:
        raise Invalid("invalid chunk text")
    if any(0xD800 <= ord(c) <= 0xDFFF for c in value):
        raise Invalid("invalid Unicode scalar")
    chunks = []
    while value:
        end = min(len(value), limit)
        if end < len(value):
            newline = value.rfind("\n", 0, end)
            if newline >= limit // 2:
                end = newline + 1
        chunks.append(value[:end])
        value = value[end:]
    return chunks


def _header(report_id, content, part):
    return (LABEL + "\nreport_id=" + report_id + " part=" + str(part) + "/3\n"
            "mode=synthetic_test as_of=" + content["as_of"] + "\n"
            "kind=" + content["kind"] + " supersedes=" + str(content["supersedes"]) + "\n"
            "correction_reason=" + str(content["correction_reason"]) + "\n"
            + "\n".join("GAP: " + gap for gap in content["gaps"]) + "\n")


def _item_text(item):
    kind, data = item["kind"], item["data"]
    if kind == "sourced_news":
        return ["News: " + "; ".join(data["titles"]), "Classification: " + data["classification"]]
    if kind == "event_state_not_causal_claim":
        lines = ["Event: " + data["event_id"] + "; state=" + data["state"],
                 "Planned: " + str(data["planned_at"]) + "; occurred: " + str(data["occurred_at"]),
                 "Success: " + ("unassessed" if data["success"] is None else str(data["success"]))
                 + "; confirmation overdue=" + str(data["confirmation_overdue"])]
        if data["expected_impact"] is not None:
            impact = data["expected_impact"]
            lines.append("Impact hypothesis only: " + impact["sector_id"] + "; direction=" + impact["direction"])
        if data["observed_reaction"] is not None:
            reaction = data["observed_reaction"]
            lines.append("Observed synthetic reaction: " + reaction["symbol"] + "; return=" + str(reaction["return"])
                         + "; sessions=" + reaction["start_session"] + " to " + reaction["end_session"]
                         + "; causal attribution not established.")
        return lines
    if kind == "benchmark":
        return ["Benchmark: " + data["symbol"] + "; value=" + str(data["value"])
                + "; session=" + data["session_id"] + "; " + data["status"]]
    if kind == "breadth":
        return ["Synthetic breadth: " + "; ".join(k + "=" + str(data[k]) for k in
                ("advancing", "declining", "unchanged", "eligible", "expected")),
                "Ratios: " + "; ".join(k + "=" + str(v) for k, v in sorted(data["ratios"].items()))]
    if kind == "turnover":
        return ["Turnover: " + str(data["value"]) + " " + data["currency"] + " " + data["unit"]
                + "; relative=" + str(data["relative"]) + "; reason=" + str(data["reason"])]
    if kind == "flow_unavailable":
        return ["Flow: unavailable; value=unavailable; unit=unavailable; provider=" + data["provider"]
                + "; series=" + data["series_kind"] + "; reason=" + data["reason"]]
    if kind == "sector_statistic":
        return ["Sector: " + data["sector_id"] + "; " + data["status"],
                "Method: equal-weight synthetic return=" + str(data["equal_weight_return"])
                + "; turnover share=" + str(data["turnover_share"]),
                "Rank=" + str(data["rank"]) + "; rank change=" + str(data["rank_change"])
                + "; coverage=" + str(data["eligible"]) + "/" + str(data["expected"])]
    if kind.startswith("approved_"):
        return ["Approved claim: " + data["claim_id"] + "; kind=" + data["kind"]
                + "; stance=" + data["stance"] + "; confidence=" + data["confidence"],
                data["statement"], "Counterargument: " + data["counterargument"],
                "Invalidation: " + data["invalidation"]]
    if kind == "empty_fixed_day":
        return ["No sourced news or upcoming events in this fixed input; live discovery remains unverified."]
    raise Invalid("unknown report item kind")


def _render(content, config):
    identity = digest(content)
    parts, chunks = [], []
    aliases = {r["fact_id"]: "E" + str(i).zfill(3)
               for i, r in enumerate(content["source"]["bundle"]["content"]["references"], 1)}
    for number, section in enumerate(content["sections"], 1):
        lines = [section["title"]]
        for item in section["items"]:
            lines.extend(["", "Item " + item["item_id"], *_item_text(item),
                          "Evidence: " + (", ".join(aliases[i] for i in item["evidence_ids"])
                                          or "unavailable (no source)"),
                          "Method fingerprint: " + item["method_id"]])
        lines.append("Source locators (synthetic; not fetched):")
        lines.extend(aliases[r["fact_id"]] + " | " + r["fact_id"] + " | " + r["source_id"]
                     + " | " + r["locator"] for r in section["sources"])
        body = "\n".join(lines) + "\n"
        header = _header(identity, content, number)
        part_id = digest({"report_id": identity, "part": number, "body": body})
        parts.append({"part_id": part_id, "ordinal": number, "body": body, "text": header + body})
        slices = split_text(body, config["chunk_chars"] - len(header) - 250)
        for index, fragment in enumerate(slices, 1):
            chunk = {"report_id": identity, "part_id": part_id, "part": number,
                     "index": index, "total": len(slices), "ordinal": len(chunks) + 1, "body": fragment}
            chunk_id = digest(chunk)
            rendered = (header + "part_id=" + part_id + "\nchunk_id=" + chunk_id
                        + " chunk=" + str(index) + "/" + str(len(slices)) + "\n" + fragment)
            if len(rendered) > config["chunk_chars"]:
                raise Invalid("chunk size bound")
            chunks.append({"chunk_id": chunk_id, **chunk, "text": rendered})
    result = {"report_id": identity, "content": content, "parts": parts, "chunks": chunks}
    if len(chunks) > config["max_chunks"] or len(canonical(result)) > config["max_report_bytes"]:
        raise Invalid("report storage bound")
    return result


def build_report(sample, config=None, *, supersedes=None, reason=None):
    config = validate_config(load_config() if config is None else config)
    source = verify_source(sample)
    if supersedes is None:
        if reason is not None:
            raise Invalid("ordinary report cannot carry correction reason")
        kind = "daily_brief"
    else:
        fingerprint(supersedes)
        research.safe_text(reason, "correction reason", 500)
        # This package permits explanatory clarification, not new factual claims.
        if reason != CORRECTION_REASON:
            raise Invalid("unsupported explanatory correction")
        kind = "correction"
    content = {"schema_version": 1, "version": VERSION, "config_id": digest(config),
               "mode": "synthetic_test", "as_of": source["research"]["as_of"],
               "dataset_id": digest(research.deterministic_content(source)),
               "research_id": digest(source["research"]), "source": source,
               "kind": kind, "supersedes": supersedes, "correction_reason": reason,
               "gaps": list(LIMITATIONS), "sections": _sections(source)}
    return _render(content, config)


def verify_report(report, config=None):
    config = validate_config(load_config() if config is None else config)
    try:
        exact(report, {"report_id", "content", "parts", "chunks"})
        if len(canonical(report)) > config["max_report_bytes"]:
            raise Invalid("report size bound")
        c = report["content"]
        expected = build_report(c["source"], config, supersedes=c["supersedes"], reason=c["correction_reason"])
        if canonical(report) != canonical(expected):
            raise Invalid("report recomputation mismatch")
        return True
    except (KeyError, TypeError, RecursionError, ValueError) as exc:
        raise Invalid("invalid publication record") from exc


def report_text(report, config=None):
    verify_report(report, config)
    return "\n".join(part["text"] for part in report["parts"])


def _summary(report):
    c = report["content"]
    return {"report_id": report["report_id"], "kind": c["kind"], "supersedes": c["supersedes"],
            "correction_reason": c["correction_reason"], "record_digest": digest(report),
            "parts": 3, "chunks": len(report["chunks"])}


def _quality(source):
    return {"label": "OFFLINE DETERMINISTIC CHECKS ONLY", "evaluation": source["evaluation"],
            "semantic_support": "UNASSESSED; no human labels", "live_gates": dict(GATES),
            "error_exercises": ["definitive_not_sent_stops", "malformed_ack_unknown_stops"],
            "limits": "One canonical store only; no global multi-store quota or exactly-once remote guarantee."}


def demo():
    from .delivery import FakeTransport, deliver
    from .ledger import Ledger
    started = stamp(utc_now())
    source = research.demo()
    config = load_config()
    original = build_report(source, config)
    correction = build_report(source, config, supersedes=original["report_id"], reason=CORRECTION_REASON)
    with TemporaryDirectory(prefix="p4-offline-") as directory:
        with Ledger(Path(directory) / "ledger.sqlite", config) as ledger:
            ledger.append(original)
            ledger.append(correction)
            delivery = [deliver(ledger, r["report_id"], FakeTransport()) for r in (original, correction)]
        exercises = {}
        for name, transport in (("partial_failure", FakeTransport(fail_at=2)),
                                ("ambiguity", FakeTransport(malformed_at=1))):
            with Ledger(Path(directory) / (name + ".sqlite"), config) as ledger:
                ledger.append(original)
                exercises[name] = deliver(ledger, original["report_id"], transport)
    return {"schema_version": 1, "label": LABEL, "execution_id": uuid.uuid4().hex,
            "started_at": started, "generated_at": stamp(utc_now()), "source": source,
            "reports": [_summary(original), _summary(correction)], "report_text": report_text(original, config),
            "delivery": delivery, "error_exercises": exercises, "quality": _quality(source),
            "metadata_policy": "Actual execution IDs/times and their content-bound report/chunk IDs vary; historical as_of and substantive fixed data do not."}


def verify_demo(sample):
    from .delivery import verify_snapshot
    try:
        exact(sample, {"schema_version", "label", "execution_id", "started_at", "generated_at", "source",
                       "reports", "report_text", "delivery", "error_exercises", "quality", "metadata_policy"})
        integer(sample["schema_version"], 1, 1)
        if sample["label"] != LABEL or type(sample["execution_id"]) is not str or re.fullmatch(r"[0-9a-f]{32}", sample["execution_id"]) is None:
            raise Invalid("invalid demo identity")
        start, finish = instant(sample["started_at"]), instant(sample["generated_at"])
        if not start <= instant(sample["source"]["research"]["accounting"]["started_at"]) <= instant(sample["source"]["generated_at"]) <= finish <= utc_now():
            raise Invalid("invalid execution time")
        original = build_report(sample["source"])
        correction = build_report(sample["source"], supersedes=original["report_id"], reason=CORRECTION_REASON)
        if (canonical(sample["reports"]) != canonical([_summary(original), _summary(correction)])
                or sample["report_text"] != report_text(original)
                or canonical(sample["quality"]) != canonical(_quality(sample["source"]))
                or sample["metadata_policy"] != "Actual execution IDs/times and their content-bound report/chunk IDs vary; historical as_of and substantive fixed data do not."):
            raise Invalid("demo content mismatch")
        if type(sample["delivery"]) is not list or len(sample["delivery"]) != 2:
            raise Invalid("invalid demo delivery")
        first_attempt = 1
        previous_finish = start
        for snapshot, report in zip(sample["delivery"], (original, correction)):
            verify_snapshot(snapshot, report, previous_finish, finish, ["sent"] * len(report["chunks"]),
                            first_attempt=first_attempt)
            previous_finish = instant(snapshot["chunks"][-1]["outcome"]["finished_at"])
            first_attempt += len(report["chunks"])
        exact(sample["error_exercises"], {"partial_failure", "ambiguity"})
        n = len(original["chunks"])
        verify_snapshot(sample["error_exercises"]["partial_failure"], original, start, finish,
                        ["sent", "failed"] + ["queued"] * (n - 2))
        verify_snapshot(sample["error_exercises"]["ambiguity"], original, start, finish,
                        ["unknown"] + ["queued"] * (n - 1))
        return True
    except (KeyError, TypeError, RecursionError, ValueError) as exc:
        raise Invalid("invalid P4 demo") from exc


def sample_text(sample):
    verify_demo(sample)
    return sample["report_text"]


def main(argv=None):
    if (sys.argv[1:] if argv is None else argv) != ["demo"]:
        print('{"status":"ERROR","reason":"invalid_command"}')
        return 2
    try:
        print(canonical(demo()))
        return 0
    except (Invalid, OSError, sqlite3.Error, ValueError, TypeError, KeyError, RecursionError):
        print('{"status":"ERROR","reason":"synthetic_publication_failed"}')
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
