"""Strict, versioned records shared by collectors, storage and gates."""

from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
import hashlib
import json
import math
import re
from urllib.parse import urlsplit

UTC = timezone.utc
MODES = frozenset({"synthetic_test", "live_observation", "historical_replay"})
RETENTIONS = frozenset({"metadata_only", "short_term_raw", "reprocessable_raw", "restricted"})
UNIVERSE = ("000001.SH", "399001.SZ", "399006.SZ", "KOSPI", "SP500")
MARKETS = {
    "000001.SH": ("SSE", "Asia/Shanghai"),
    "399001.SZ": ("SZSE", "Asia/Shanghai"),
    "399006.SZ": ("SZSE", "Asia/Shanghai"),
    "KOSPI": ("KRX", "Asia/Seoul"),
    "SP500": ("US_CASH", "America/New_York"),
}
PARSERS = frozenset({"rss-v1", "market-csv-v1"})


class Invalid(ValueError):
    """A boundary rejected untrusted or inconsistent data."""


def utc_now():
    return datetime.now(UTC)


def instant(value):
    if type(value) is not str or len(value) > 40:
        raise Invalid("timestamp must be an aware ISO string")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise Invalid("invalid timestamp") from exc
    if "T" not in value or result.tzinfo is None or result.utcoffset() is None:
        raise Invalid("naive timestamp")
    return result.astimezone(UTC)


def stamp(value):
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise Invalid("aware clock required")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def text(value, name, limit=512):
    if type(value) is not str or not value.strip() or len(value) > limit:
        raise Invalid("invalid " + name)
    if any(ord(c) < 32 and c not in "\t\n\r" for c in value):
        raise Invalid("control character in " + name)
    return value


def exact(data, keys):
    if type(data) is not dict or set(data) != set(keys):
        raise Invalid("unknown or missing fields")


def integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise Invalid("invalid integer")
    return value


def finite(value):
    if type(value) not in (float, int) or not math.isfinite(value) or value <= 0:
        raise Invalid("market value must be finite and positive")
    return value


def canonical(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise Invalid("invalid JSON record") from exc


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else canonical(value).encode()).hexdigest()


def fingerprint(value):
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise Invalid("invalid sha256")


def locator(value):
    text(value, "locator", 2048)
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise Invalid("unsafe locator") from exc
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or port not in (None, 443)):
        raise Invalid("unsafe locator")


@dataclass(frozen=True)
class Capture:
    source_id: str
    locator: str
    retrieved_at: str
    first_seen_at: str
    source_timestamp: str | None
    content_hash: str
    response_hash: str
    content_type: str
    parser_version: str
    retention: str
    retention_basis: str
    expires_at: str | None
    mode: str
    request_id: str
    schema_version: int = 1

    def validate(self, now=None):
        integer(self.schema_version, 1, 1)
        text(self.source_id, "source_id", 80)
        locator(self.locator)
        text(self.request_id, "request_id", 100)
        text(self.retention_basis, "retention_basis", 1000)
        if self.mode not in MODES or self.retention not in RETENTIONS or self.parser_version not in PARSERS:
            raise Invalid("unknown mode, retention or parser")
        if self.content_type not in {"application/rss+xml", "application/xml", "text/xml", "text/csv"}:
            raise Invalid("unsupported content type")
        fingerprint(self.content_hash)
        fingerprint(self.response_hash)
        retrieved, seen = instant(self.retrieved_at), instant(self.first_seen_at)
        if seen > retrieved or retrieved > (now or utc_now()):
            raise Invalid("future or contradictory capture time")
        if self.source_timestamp is not None and instant(self.source_timestamp) > retrieved:
            raise Invalid("future source timestamp")
        if self.retention == "short_term_raw" and self.expires_at is None:
            raise Invalid("short term raw requires expiry")
        if self.expires_at is not None:
            expiry = instant(self.expires_at)
            if expiry <= retrieved:
                raise Invalid("expiry precedes capture")
            if self.retention == "short_term_raw" and (expiry - retrieved).total_seconds() > 86400:
                raise Invalid("raw retention exceeds 24 hours")
        return self

    def to_dict(self):
        self.validate()
        return asdict(self)

    @property
    def version_id(self):
        return digest(self.to_dict())

    @classmethod
    def from_dict(cls, value):
        exact(value, (f.name for f in fields(cls)))
        return cls(**value).validate()


@dataclass(frozen=True)
class Evidence:
    capture_id: str
    source_id: str
    locator: str
    domain: str
    key: str
    tier: str
    title: str | None
    value: float | None
    published_at: str | None
    occurred_at: str | None
    effective_at: str | None
    observed_at: str | None
    retrieved_at: str
    first_seen_at: str
    expires_at: str | None
    mode: str
    parser_version: str
    calendar_id: str | None
    session_id: str | None
    close_at: str | None
    schema_version: int = 1

    def validate(self, now=None):
        integer(self.schema_version, 1, 1)
        fingerprint(self.capture_id)
        text(self.source_id, "source_id", 80)
        locator(self.locator)
        text(self.key, "key", 2048)
        if self.domain not in {"market", "news"} or self.tier not in {"A", "B", "C"}:
            raise Invalid("unsupported domain or authority")
        if self.mode not in MODES or self.parser_version not in PARSERS:
            raise Invalid("unknown mode or parser")
        retrieved, seen = instant(self.retrieved_at), instant(self.first_seen_at)
        if seen > retrieved or retrieved > (now or utc_now()):
            raise Invalid("future or contradictory capture time")
        for name in ("published_at", "occurred_at", "effective_at", "observed_at", "close_at"):
            value = getattr(self, name)
            if value is not None and instant(value) > retrieved:
                raise Invalid("future " + name)
        if self.expires_at is not None and instant(self.expires_at) <= retrieved:
            raise Invalid("invalid expiry")
        if self.domain == "news":
            text(self.title, "title", 2000)
            if self.published_at is None:
                raise Invalid("news publication time unknown")
            if self.parser_version != "rss-v1" or any(x is not None for x in
                    (self.value, self.calendar_id, self.session_id, self.close_at)):
                raise Invalid("invalid news fields")
        else:
            if self.key not in UNIVERSE or self.parser_version != "market-csv-v1" or self.title is not None:
                raise Invalid("invalid market fields")
            finite(self.value)
            for name in ("calendar_id", "session_id", "close_at", "observed_at"):
                text(getattr(self, name), name)
            if instant(self.observed_at) != instant(self.close_at):
                raise Invalid("market observation must identify session close")
        return self

    def to_dict(self):
        self.validate()
        return asdict(self)

    @property
    def evidence_id(self):
        return digest(self.to_dict())

    @classmethod
    def from_dict(cls, value):
        exact(value, (f.name for f in fields(cls)))
        return cls(**value).validate()


def evidence_for(capture, **values):
    base = dict(capture_id=capture.version_id, source_id=capture.source_id,
                locator=capture.locator, tier="A", published_at=None, occurred_at=None,
                effective_at=None, observed_at=None, title=None, value=None,
                retrieved_at=capture.retrieved_at, first_seen_at=capture.first_seen_at,
                expires_at=capture.expires_at, mode=capture.mode,
                parser_version=capture.parser_version, calendar_id=None, session_id=None, close_at=None)
    base.update(values)
    return Evidence(**base).validate()


def validate_summary(value):
    exact(value, {"schema_version", "run_id", "mode", "started_at", "finished_at", "outcome",
                  "source_id", "source_state", "requests", "quality", "parse", "capture_ids",
                  "bundle_id", "observations", "gate", "live_market_coverage", "readiness", "error_codes"})
    integer(value["schema_version"], 1, 1)
    for name in ("run_id", "source_id"):
        text(value[name], name, 160)
    if value["mode"] not in MODES or value["outcome"] not in {
            "not_attempted", "failed", "unknown", "synthetic_demo", "simulated_probe", "observed", "replayed"}:
        raise Invalid("invalid summary mode or outcome")
    if value["source_state"] not in {"configured", "implemented_unverified", "live_verified", "degraded", "disabled", "unavailable"}:
        raise Invalid("invalid source state")
    if value["source_state"] == "live_verified" and (value["mode"] != "live_observation" or value["outcome"] != "observed"):
        raise Invalid("synthetic or replay cannot verify live source")
    if instant(value["started_at"]) > instant(value["finished_at"]) or instant(value["finished_at"]) > utc_now():
        raise Invalid("invalid summary time")
    requests = value["requests"]
    exact(requests, {"attempted", "success", "failed", "unknown", "reserved_bytes", "observed_bytes"})
    for key in ("attempted", "success", "failed", "unknown"):
        integer(requests[key], 0, 2)
    if requests["attempted"] != requests["success"] + requests["failed"] + requests["unknown"]:
        raise Invalid("request denominator mismatch")
    if requests["reserved_bytes"] != requests["attempted"] * 512000:
        raise Invalid("invalid reservation total")
    if requests["observed_bytes"] is not None:
        integer(requests["observed_bytes"], 0, requests["reserved_bytes"])
    if requests["unknown"] and requests["observed_bytes"] is not None:
        raise Invalid("unknown usage cannot be claimed known")
    quality = value["quality"]
    exact(quality, {"request_sample_size", "parse_sample_size", "parse_success_count", "nonempty_parse_count", "item_denominator",
                    "accepted_records", "correction_rate", "publication_delay_seconds", "long_term_reliability"})
    if quality["request_sample_size"] != requests["attempted"]:
        raise Invalid("quality denominator mismatch")
    for key in ("parse_sample_size", "parse_success_count", "nonempty_parse_count"):
        integer(quality[key], 0, 2)
    if not quality["nonempty_parse_count"] <= quality["parse_success_count"] <= quality["parse_sample_size"]:
        raise Invalid("inconsistent parse quality counts")
    for key in ("item_denominator", "accepted_records"):
        integer(quality[key], 0, 2000)
    if any(quality[k] is not None for k in ("correction_rate", "publication_delay_seconds", "long_term_reliability")):
        raise Invalid("unobserved quality must remain unknown")
    parsed = value["parse"]
    exact(parsed, {"total", "accepted", "rejected", "reasons"})
    for key in ("total", "accepted", "rejected"):
        integer(parsed[key], 0, 2000)
    if type(parsed["reasons"]) is not dict:
        raise Invalid("invalid rejection reasons")
    for key, count in parsed["reasons"].items():
        text(key, "rejection reason", 100)
        integer(count, 1, 2000)
    if (parsed["total"] != parsed["accepted"] + parsed["rejected"]
            or parsed["rejected"] != sum(parsed["reasons"].values())
            or quality["item_denominator"] != parsed["total"]
            or quality["accepted_records"] != parsed["accepted"]):
        raise Invalid("parse denominator mismatch")
    if type(value["capture_ids"]) is not list or len(value["capture_ids"]) > 2:
        raise Invalid("invalid capture references")
    for identity in value["capture_ids"]:
        fingerprint(identity)
    if type(value["observations"]) is not list or len(value["observations"]) != len(value["capture_ids"]):
        raise Invalid("invalid capture observations")
    for observation, identity in zip(value["observations"], value["capture_ids"]):
        exact(observation, {"capture_id", "source_id", "locator", "retrieved_at", "first_seen_at", "source_timestamp",
                            "content_hash", "response_hash", "parser_version", "retention", "expires_at"})
        if observation["capture_id"] != identity or observation["parser_version"] not in PARSERS or observation["retention"] not in RETENTIONS:
            raise Invalid("invalid capture observation identity")
        fingerprint(observation["content_hash"])
        fingerprint(observation["response_hash"])
        text(observation["source_id"], "observed source", 80)
        locator(observation["locator"])
        if instant(observation["first_seen_at"]) > instant(observation["retrieved_at"]):
            raise Invalid("invalid observation clock")
        if instant(observation["retrieved_at"]) > instant(value["finished_at"]):
            raise Invalid("observation after run end")
        for name in ("source_timestamp", "expires_at"):
            if observation[name] is not None:
                instant(observation[name])
    fingerprint(value["bundle_id"])
    if (value["live_market_coverage"] != {"covered": 0, "expected": 5, "missing": list(UNIVERSE)}
            or value["readiness"] != "BLOCKED_MARKET_SOURCES"):
        raise Invalid("live market coverage cannot be promoted")
    exact(value["gate"], {"status", "domains", "confidence_ceiling"})
    from .gate import validate_domains
    validate_domains(value["gate"])
    if type(value["error_codes"]) is not list:
        raise Invalid("invalid error codes")
    for reason in value["error_codes"]:
        text(reason, "error code", 100)
    return value
