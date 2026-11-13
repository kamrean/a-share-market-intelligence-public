"""Domain-specific evidence eligibility and explicit session calendars."""

from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
import tomllib

from .models import (Evidence, Invalid, MARKETS, MODES, UNIVERSE, digest, exact, fingerprint,
                     instant, integer, stamp, text, utc_now)


@dataclass(frozen=True)
class Session:
    calendar_id: str
    timezone: str
    session_id: str
    close_at: str

    def validate(self):
        identities = set(MARKETS.values())
        if (self.calendar_id, self.timezone) not in identities:
            raise Invalid("unknown calendar identity")
        instant(self.close_at)
        local = datetime.fromisoformat(self.close_at.replace("Z", "+00:00"))
        if local.date().isoformat() != self.session_id:
            raise Invalid("session identity disagrees with local close date")
        offsets = {"Asia/Shanghai": {28800}, "Asia/Seoul": {32400},
                   "America/New_York": {-14400, -18000}}
        if local.utcoffset().total_seconds() not in offsets[self.timezone]:
            raise Invalid("close offset disagrees with market identity")
        return self

    @classmethod
    def from_dict(cls, data):
        exact(data, cls.__dataclass_fields__)
        return cls(**data).validate()


@dataclass(frozen=True)
class Calendar:
    version: str
    sessions: tuple

    def validate(self):
        text(self.version, "calendar version", 100)
        if type(self.sessions) is not tuple:
            raise Invalid("explicit sessions required")
        seen = set()
        for session in self.sessions:
            session.validate()
            identity = (session.calendar_id, session.session_id)
            if identity in seen:
                raise Invalid("duplicate calendar session")
            seen.add(identity)
        return self

    def to_dict(self):
        self.validate()
        return {"version": self.version, "sessions": [asdict(s) for s in self.sessions]}

    @classmethod
    def from_dict(cls, data):
        exact(data, {"version", "sessions"})
        if type(data["sessions"]) is not list:
            raise Invalid("session list required")
        return cls(data["version"], tuple(Session.from_dict(s) for s in data["sessions"])).validate()

    def latest(self, calendar_id, as_of):
        eligible = [s for s in self.sessions if s.calendar_id == calendar_id and instant(s.close_at) <= as_of]
        if not eligible:
            raise Invalid("no explicit completed session")
        return max(eligible, key=lambda s: instant(s.close_at))


@dataclass(frozen=True)
class Rule:
    domain: str
    validator: str
    required: bool
    minimum_coverage: float
    max_age_seconds: int
    allow_degraded: bool

    def validate(self):
        if self.domain not in {"market", "news"}:
            raise Invalid("unsupported domain")
        text(self.validator, "validator", 100)
        if type(self.required) is not bool or type(self.allow_degraded) is not bool:
            raise Invalid("boolean rule required")
        if (type(self.minimum_coverage) not in (int, float)
                or not 0 < self.minimum_coverage <= 1):
            raise Invalid("invalid coverage rule")
        integer(self.max_age_seconds, 1, 315576000)
        if self.domain == "market" and (not self.required or self.minimum_coverage != 1):
            raise Invalid("fixed five-target market requirement cannot be relaxed")
        return self

    @classmethod
    def from_dict(cls, data):
        exact(data, cls.__dataclass_fields__)
        return cls(**data).validate()


@dataclass(frozen=True)
class Profile:
    version: str
    method_version: str
    mode: str
    universe: tuple
    rules: tuple
    schema_version: int = 1

    def validate(self):
        integer(self.schema_version, 1, 1)
        text(self.version, "profile version", 100)
        text(self.method_version, "method version", 100)
        if self.mode not in MODES or self.universe != UNIVERSE:
            raise Invalid("unknown mode or changed universe")
        if type(self.rules) is not tuple or {r.domain for r in self.rules} != {"market", "news"} or len(self.rules) != 2:
            raise Invalid("both domain rules required exactly once")
        for rule in self.rules:
            rule.validate()
        return self

    def to_dict(self):
        self.validate()
        return {"schema_version": 1, "version": self.version, "method_version": self.method_version,
                "mode": self.mode, "universe": list(self.universe), "rules": [asdict(r) for r in self.rules]}

    @classmethod
    def from_dict(cls, data):
        exact(data, {"schema_version", "version", "method_version", "mode", "universe", "rules"})
        if type(data["universe"]) is not list or type(data["rules"]) is not list:
            raise Invalid("profile arrays required")
        return cls(data["version"], data["method_version"], data["mode"], tuple(data["universe"]),
                   tuple(Rule.from_dict(r) for r in data["rules"]), data["schema_version"]).validate()


def load_profile(path):
    with Path(path).open("rb") as handle:
        return Profile.from_dict(tomllib.load(handle))


def evaluate(records, profile, calendar, as_of, now=None):
    profile.validate()
    calendar.validate()
    now = now or utc_now()
    cutoff = instant(as_of)
    if cutoff > now:
        raise Invalid("future as_of")
    eligible = {"market": [], "news": []}
    rejected, domain_faults = [], {"market": [], "news": []}
    ids = set()
    for original in records:
        domain, identity = "unknown", "unreadable"
        try:
            if isinstance(original, Evidence):
                domain = original.domain
                record = original.validate(now)
            else:
                domain = original.get("domain", "unknown") if type(original) is dict else "unknown"
                record = Evidence.from_dict(original)
            identity = record.evidence_id
            if identity in ids:
                raise Invalid("duplicate evidence reference")
            ids.add(identity)
            if record.mode != profile.mode:
                raise Invalid("wrong evidence mode")
            for name in ("retrieved_at", "first_seen_at", "published_at", "occurred_at",
                         "effective_at", "observed_at", "close_at"):
                value = getattr(record, name)
                if value is not None and instant(value) > cutoff:
                    raise Invalid(name + " after as_of")
            if record.expires_at and instant(record.expires_at) <= now:
                raise Invalid("expired evidence")
            rule = next(r for r in profile.rules if r.domain == domain)
            anchor = instant(record.close_at if domain == "market" else record.published_at)
            if (cutoff - anchor).total_seconds() > rule.max_age_seconds:
                raise Invalid("stale evidence")
            if domain == "market":
                calendar_id, timezone = MARKETS[record.key]
                session = calendar.latest(calendar_id, cutoff)
                if (record.calendar_id != calendar_id or session.timezone != timezone
                        or record.session_id != session.session_id
                        or instant(record.close_at) != instant(session.close_at)):
                    raise Invalid("wrong or incomplete session identity")
            eligible[domain].append(record)
        except (Invalid, ValueError, TypeError, StopIteration) as exc:
            reason = str(exc) or "invalid record"
            rejected.append({"evidence_id": identity, "domain": domain, "reason": reason})
            for affected in ([domain] if domain in domain_faults else domain_faults):
                domain_faults[affected].append(reason)
    accepted, domains = [], {}
    invalid_ids = {r["evidence_id"] for r in rejected}
    for rule in profile.rules:
        groups = {}
        for record in eligible[rule.domain]:
            if record.evidence_id in invalid_ids:
                continue
            groups.setdefault(record.key, []).append(record)
        confirmed = []
        faults = list(domain_faults[rule.domain])
        if rule.validator != rule.domain + "-v1":
            faults.append("missing_required_validator" if rule.required else "missing_validator")
        for key, group in sorted(groups.items()):
            signatures = {(r.value, r.session_id, instant(r.close_at)) if rule.domain == "market"
                          else (r.title, instant(r.published_at)) for r in group}
            reason = "conflicting evidence" if len(signatures) > 1 else None
            trusted = [r for r in group if r.tier != "C"]
            if not trusted:
                reason = reason or "Tier C cannot confirm"
            if reason:
                faults.append(reason)
                rejected.extend({"evidence_id": r.evidence_id, "domain": rule.domain, "reason": reason} for r in group)
            else:
                confirmed.extend(trusted)
                rejected.extend({"evidence_id": r.evidence_id, "domain": rule.domain,
                                 "reason": "Tier C excluded from confirmed evidence"} for r in group if r.tier == "C")
        covered = len({r.key for r in confirmed}) if rule.domain == "market" else int(bool(confirmed))
        denominator = len(UNIVERSE) if rule.domain == "market" else 1
        missing = [key for key in UNIVERSE if key not in {r.key for r in confirmed}] if rule.domain == "market" else ([] if confirmed else ["primary_news"])
        if covered / denominator < rule.minimum_coverage:
            faults.append("insufficient_coverage")
        status = "PASS" if not faults else ("DEGRADED" if not rule.required and rule.allow_degraded else "BLOCK")
        domains[rule.domain] = {"status": status, "covered": covered, "expected": denominator,
                                "missing": missing, "reasons": sorted(set(faults)),
                                "confidence_ceiling": "supported" if status == "PASS" else "unavailable"}
        # A missing validator cannot license even individually well-formed inputs.
        if rule.validator == rule.domain + "-v1":
            accepted.extend(confirmed)
    status = "BLOCK" if any(d["status"] == "BLOCK" for d in domains.values()) else (
        "DEGRADED" if any(d["status"] == "DEGRADED" for d in domains.values()) else "PASS")
    return {"schema_version": 1, "status": status, "as_of": stamp(cutoff), "mode": profile.mode,
            "confidence_ceiling": "none" if status == "BLOCK" else ("limited" if status == "DEGRADED" else "supported"),
            "domains": domains, "accepted_ids": sorted(r.evidence_id for r in accepted),
            "rejected": sorted(rejected, key=lambda r: (r["domain"], r["evidence_id"], r["reason"])),
            "profile": profile.to_dict(), "calendar": calendar.to_dict(),
            "profile_hash": digest(profile.to_dict()), "calendar_hash": digest(calendar.to_dict()),
            "method_version": profile.method_version}


def validate_domains(value):
    if value["status"] not in {"PASS", "BLOCK", "DEGRADED"}:
        raise Invalid("unknown gate status")
    exact(value["domains"], {"market", "news"})
    for name, row in value["domains"].items():
        exact(row, {"status", "covered", "expected", "missing", "reasons", "confidence_ceiling"})
        if row["status"] not in {"PASS", "BLOCK", "DEGRADED"}:
            raise Invalid("unknown domain status")
        expected = 5 if name == "market" else 1
        integer(row["expected"], expected, expected)
        integer(row["covered"], 0, expected)
        if type(row["missing"]) is not list or type(row["reasons"]) is not list:
            raise Invalid("invalid domain details")
        if len(row["missing"]) != expected - row["covered"] or len(set(row["missing"])) != len(row["missing"]):
            raise Invalid("domain missing denominator mismatch")
        if not set(row["missing"]) <= (set(UNIVERSE) if name == "market" else {"primary_news"}):
            raise Invalid("unknown missing target")
        for reason in row["reasons"]:
            text(reason, "gate reason", 150)
        ceiling = "supported" if row["status"] == "PASS" else "unavailable"
        if row["confidence_ceiling"] != ceiling or (row["status"] == "PASS" and row["reasons"]):
            raise Invalid("inconsistent domain result")
    status = "BLOCK" if any(r["status"] == "BLOCK" for r in value["domains"].values()) else (
        "DEGRADED" if any(r["status"] == "DEGRADED" for r in value["domains"].values()) else "PASS")
    ceiling = {"BLOCK": "none", "DEGRADED": "limited", "PASS": "supported"}[status]
    if value["status"] != status or value["confidence_ceiling"] != ceiling:
        raise Invalid("global gate cannot hide domain failure")


def validate_result(result):
    exact(result, {"schema_version", "status", "as_of", "mode", "confidence_ceiling", "domains",
                   "accepted_ids", "rejected", "profile", "calendar", "profile_hash", "calendar_hash", "method_version"})
    integer(result["schema_version"], 1, 1)
    validate_domains(result)
    profile = Profile.from_dict(result["profile"])
    Calendar.from_dict(result["calendar"])
    if (result["profile_hash"] != digest(result["profile"]) or result["calendar_hash"] != digest(result["calendar"])
            or result["mode"] != profile.mode or result["method_version"] != profile.method_version
            or instant(result["as_of"]) > utc_now()):
        raise Invalid("invalid frozen gate metadata")
    if type(result["accepted_ids"]) is not list or type(result["rejected"]) is not list:
        raise Invalid("invalid evidence reference lists")
    if len(set(result["accepted_ids"])) != len(result["accepted_ids"]):
        raise Invalid("duplicate accepted references")
    for identity in result["accepted_ids"]:
        fingerprint(identity)
    for row in result["rejected"]:
        exact(row, {"evidence_id", "domain", "reason"})
        if row["evidence_id"] != "unreadable":
            fingerprint(row["evidence_id"])
        if row["evidence_id"] in result["accepted_ids"]:
            raise Invalid("rejected reference included in bundle")
        text(row["domain"], "rejected domain", 100)
        text(row["reason"], "rejection reason", 150)
    for rule in profile.rules:
        if result["domains"][rule.domain]["status"] == "DEGRADED" and (rule.required or not rule.allow_degraded):
            raise Invalid("degraded not authorized by profile")
    return result
