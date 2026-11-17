"""P4E: read-only retained evidence preparation, never a new source run."""

from collections import Counter
from contextlib import closing
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import sqlite3
import sys
import tomllib
from urllib.parse import urlsplit

from . import collection, runtime, sources
from .gate import Profile, validate_result
from .intelligence import Fact, decode_input
from .models import (Invalid, canonical, digest, exact, fingerprint, instant,
                     integer, stamp, utc_now)
from .news_events import process_news, validate_payload
from .store import (APPLICATION_ID, CHECKOUT, DATABASE_LIMIT, SCHEMA,
                    _schema_signature, external_path)

ROOT = CHECKOUT
MAX_RECORDS = 100
MAX_BYTES = 512000
MAX_RECEIPT_BYTES = 160000
OBSERVATION_FIELDS = frozenset({
    "schema_version", "source_id", "source_snapshot", "source_probe_digest",
    "source_run_id", "source_bundle_id", "source_mode", "processing_mode",
    "source_state", "source_gate", "source_as_of", "processed_at", "outcome",
    "reason", "input_count", "prepared_count", "eligible_count", "excluded_count",
    "cluster_count", "parse_rejected", "reason_counts", "provenance", "clusters",
    "prepared_digest", "network_requests", "new_live_runs", "live_market_coverage",
    "unavailable_domains",
})
UNAVAILABLE_REASONS = frozenset({
    "missing_store", "invalid_store", "input_mismatch", "input_unavailable",
    "expired_input", "tombstoned_input", "interrupted_input", "input_limit",
    "clock_rollback", "local_operation_failed", "preparation_interrupted",
})
EXCLUSION_REASONS = frozenset({
    "stale_news", "conflicting_news_versions", "non_board_locator", "non_plain_title",
})
LOCAL_ERRORS = frozenset({"invalid_cli_arguments", "preparation_gate_blocked",
                          "invalid_preparation_record", "local_operation_failed",
                          "preparation_interrupted"})
UNAVAILABLE_DOMAINS = ["entity", "event", "flow", "market", "sector"]
PROVENANCE_FIELDS = frozenset({
    "evidence_id", "capture_id", "fact_id", "capture_hash", "fact_payload_hash",
    "source_mode", "published_at", "retrieved_at", "first_seen_at", "expires_at",
    "disposition", "reason",
})


def _require(condition):
    if not condition:
        raise Invalid("invalid_preparation_record")


def _time(value):
    result = instant(value)
    _require(stamp(result) == value and result <= utc_now())
    return result


def _original(root):
    """Only checkout artifacts are consulted, including for temporary checkouts."""
    root = Path(root)
    contract = tomllib.loads((root / "contracts/p4e_evidence_preparation.toml").read_text(encoding="utf-8"))
    config = contract["local_preparation"]
    _require(contract["package_id"] == "P4E")
    exact(config, {"input_store", "network_requests", "processing_mode", "read_only",
                   "requires_independent_review", "requires_preparation_release", "source",
                   "source_probe_digest", "source_receipt", "source_snapshot"})
    _require(config["source"] == "fed_press" and config["input_store"] == "p4s-fed-probe.sqlite"
             and config["source_receipt"] == "docs/evidence/p4s-source-probe.json"
             and config["processing_mode"] == "historical_replay"
             and config["read_only"] is True and config["requires_independent_review"] is True
             and config["requires_preparation_release"] is True)
    integer(config["network_requests"], 0, 0)
    fingerprint(config["source_snapshot"])
    fingerprint(config["source_probe_digest"])
    path = root / config["source_receipt"]
    _require(path.stat().st_size <= MAX_RECEIPT_BYTES)
    receipt = decode_input(path.read_bytes())
    collection.validate_probe_record(receipt, config["source_snapshot"])
    _require(receipt["status"] == "RECORDED")
    original = receipt["observation"]
    _require(digest(receipt) == config["source_probe_digest"]
             and original["source_state"] == "degraded"
             and original["gate"]["domains"]["news"]["status"] == "BLOCK")
    return config, original


def _base(config, original):
    return {
        "schema_version": 1, "source_id": "fed_press",
        "source_snapshot": config["source_snapshot"],
        "source_probe_digest": config["source_probe_digest"],
        "source_run_id": original["run_id"], "source_bundle_id": original["bundle_id"],
        "source_mode": "live_observation", "processing_mode": "historical_replay",
        "source_state": original["source_state"], "source_gate": original["gate"],
        "source_as_of": None, "processed_at": stamp(utc_now()),
        "outcome": "unavailable", "reason": "input_unavailable",
        "input_count": original["parse"]["accepted"], "prepared_count": 0,
        "eligible_count": 0, "excluded_count": 0, "cluster_count": 0,
        "parse_rejected": original["parse"]["rejected"],
        "reason_counts": dict(original["parse"]["reasons"]),
        "provenance": [], "clusters": [], "prepared_digest": None,
        "network_requests": 0, "new_live_runs": 0,
        "live_market_coverage": original["live_market_coverage"],
        "unavailable_domains": list(UNAVAILABLE_DOMAINS),
    }


def _validate_observation(value, config, original):
    exact(value, OBSERVATION_FIELDS)
    base = _base(config, original)
    for key in ("schema_version", "source_id", "source_snapshot", "source_probe_digest",
                "source_run_id", "source_bundle_id", "source_mode", "processing_mode",
                "source_state", "source_gate", "input_count", "parse_rejected",
                "network_requests", "new_live_runs", "live_market_coverage", "unavailable_domains"):
        _require(canonical(value[key]) == canonical(base[key]))
    processed = _time(value["processed_at"])
    _require(processed >= instant(original["finished_at"]))
    as_of = value["source_as_of"]
    if as_of is not None:
        cutoff = _time(as_of)
        _require(instant(original["started_at"]) <= cutoff <= instant(original["finished_at"]))
        _require(all(instant(row["retrieved_at"]) <= cutoff for row in original["observations"]))
    for key in ("prepared_count", "eligible_count", "excluded_count", "cluster_count"):
        integer(value[key], 0, MAX_RECORDS)
    _require(type(value["reason_counts"]) is dict)
    _require(set(value["reason_counts"]) <= EXCLUSION_REASONS | {"invalid_or_duplicate_item"})
    for count in value["reason_counts"].values():
        integer(count, 1, 1000)
    for key in ("provenance", "clusters"):
        _require(type(value[key]) is list and len(value[key]) <= MAX_RECORDS)
    if value["outcome"] == "unavailable":
        _require(type(value["reason"]) is str and value["reason"] in UNAVAILABLE_REASONS)
        _require(all(value[k] == 0 for k in ("prepared_count", "eligible_count", "excluded_count", "cluster_count")))
        _require(not value["provenance"] and not value["clusters"] and value["prepared_digest"] is None)
        _require(value["reason_counts"] == original["parse"]["reasons"])
        if value["reason"] == "expired_input":
            _require(any(instant(r["expires_at"]) <= processed for r in original["observations"]))
        return
    _require(value["outcome"] == "prepared" and value["reason"] == "local_preparation"
             and as_of is not None and 0 < value["input_count"] <= MAX_RECORDS
             and original["parse"]["total"] <= MAX_RECORDS)
    fingerprint(value["prepared_digest"])
    _require(value["prepared_count"] == value["input_count"] == len(value["provenance"])
             and value["eligible_count"] + value["excluded_count"] == value["prepared_count"])
    captures = {r["capture_id"]: r for r in original["observations"]}
    ttl = next(rule.max_age_seconds for rule in runtime.profile_for("live_observation").rules
               if rule.domain == "news")
    facts, evidence, eligible, counts = set(), set(), set(), Counter(original["parse"]["reasons"])
    for row in value["provenance"]:
        exact(row, PROVENANCE_FIELDS)
        for key in ("evidence_id", "capture_id", "fact_id", "capture_hash", "fact_payload_hash"):
            fingerprint(row[key])
        _require(row["capture_id"] in captures and row["source_mode"] == "live_observation")
        capture = captures[row["capture_id"]]
        _require(row["capture_hash"] == capture["content_hash"])
        for key in ("retrieved_at", "first_seen_at", "expires_at"):
            _require(row[key] == capture[key])
        # Publication format is preserved from Evidence; offsets are legitimate.
        _require(instant(row["published_at"]) <= instant(row["retrieved_at"]) <= cutoff
                 and processed < instant(row["expires_at"]))
        _require(row["fact_id"] not in facts and row["evidence_id"] not in evidence)
        facts.add(row["fact_id"])
        evidence.add(row["evidence_id"])
        stale = (cutoff - instant(row["published_at"])).total_seconds() > ttl
        _require(not stale or (row["disposition"] == "excluded" and row["reason"] == "stale_news"))
        _require(row["reason"] != "stale_news" or stale)
        if row["disposition"] == "eligible":
            _require(row["reason"] is None)
            eligible.add(row["fact_id"])
        else:
            _require(row["disposition"] == "excluded" and type(row["reason"]) is str
                     and row["reason"] in EXCLUSION_REASONS)
            counts[row["reason"]] += 1
    _require(value["provenance"] == sorted(value["provenance"], key=lambda r: r["evidence_id"]))
    _require(value["reason_counts"] == dict(counts) and value["eligible_count"] == len(eligible))
    members, identities = [], []
    for cluster in value["clusters"]:
        exact(cluster, {"cluster_id", "member_ids"})
        fingerprint(cluster["cluster_id"])
        ids = cluster["member_ids"]
        _require(type(ids) is list and 0 < len(ids) <= MAX_RECORDS)
        for identity in ids:
            fingerprint(identity)
        _require(ids == sorted(set(ids)) and set(ids) <= eligible
                 and digest(ids) == cluster["cluster_id"])
        members.extend(ids)
        identities.append(cluster["cluster_id"])
    _require(identities == sorted(set(identities)) and len(members) == len(set(members))
             and set(members) == eligible and len(identities) == value["cluster_count"])


def validate_preparation_record(value, snapshot_id, *, root=ROOT):
    """Validate a sanitized attestation without consulting any runtime store."""
    try:
        fingerprint(snapshot_id)
        exact(value, {"schema_version", "package_id", "snapshot_id", "status",
                      "processed_at", "observation", "reason"})
        integer(value["schema_version"], 1, 1)
        _require(value["package_id"] == "P4E" and value["snapshot_id"] == snapshot_id)
        if value["status"] == "NOT_RUN":
            _require(value["processed_at"] is None and value["observation"] is None
                     and value["reason"] == "awaiting_release")
        else:
            _require(value["status"] == "RECORDED" and value["reason"] == "local_preparation")
            config, original = _original(root)
            _validate_observation(value["observation"], config, original)
            _require(value["processed_at"] == value["observation"]["processed_at"])
        _require(len(canonical(value).encode()) <= MAX_RECEIPT_BYTES)
    except Exception:
        raise Invalid("invalid_preparation_record") from None
    return value


def build_preparation_record(snapshot_id, observation=None, *, root=ROOT):
    """Build the exact governance receipt; this never authorizes or reads input."""
    value = {"schema_version": 1, "package_id": "P4E", "snapshot_id": snapshot_id,
             "status": "NOT_RUN" if observation is None else "RECORDED",
             "processed_at": None if observation is None else observation["processed_at"],
             "observation": observation,
             "reason": "awaiting_release" if observation is None else "local_preparation"}
    return validate_preparation_record(value, snapshot_id, root=root)


class _Unavailable(Invalid):
    def __init__(self, reason):
        if reason not in UNAVAILABLE_REASONS:
            reason = "local_operation_failed"
        self.reason = reason
        super().__init__(reason)


def _identity(path):
    path = external_path(path)
    for suffix in ("-journal", "-wal", "-shm"):
        if Path(str(path) + suffix).exists():
            raise _Unavailable("invalid_store")
    stat = path.stat()
    if not 0 < stat.st_size <= DATABASE_LIMIT:
        raise _Unavailable("invalid_store")
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def _existing_store(path):
    # A normal mode=ro open can create WAL/SHM files before journal_mode is read.
    # Refuse WAL headers first; immutable also prevents side effects on a race.
    with path.open("rb") as source:
        header = source.read(20)
    if header[:16] != b"SQLite format 3\x00" or header[18:20] != b"\x01\x01":
        return False
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as existing:
        if (existing.execute("PRAGMA user_version").fetchone()[0] != 1
                or existing.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID):
            return False
        with closing(sqlite3.connect(":memory:")) as reference:
            reference.executescript(SCHEMA)
            if _schema_signature(existing) != _schema_signature(reference):
                return False
        return existing.execute("PRAGMA quick_check").fetchone()[0] == "ok"


class _ReadCursor:
    def __init__(self, cursor, guard):
        self.cursor, self.guard = cursor, guard

    def _fetch(self, method, *args):
        self.guard()
        result = getattr(self.cursor, method)(*args)
        self.guard()
        return result

    def fetchone(self):
        return self._fetch("fetchone")

    def fetchall(self):
        return self._fetch("fetchall")

    def fetchmany(self, size=1):
        return self._fetch("fetchmany", size)

    def __iter__(self):
        return self

    def __next__(self):
        row = self.fetchone()
        if row is None:
            raise StopIteration
        return row


class _ReadConnection:
    """Guard execution, SQLite decoding and fetching, not just preparation."""
    def __init__(self, connection, store):
        self.connection, self.store = connection, store
        connection.set_progress_handler(self._progress, 1)
        connection.text_factory = self._text
        connection.row_factory = self._row

    def _guard(self):
        if self.store.expiry is not None:
            self.store.check_time(payload=True)

    def _progress(self):
        try:
            self._guard()
            return 0
        except _Unavailable:
            return 1

    def _text(self, value):
        self._guard()
        result = value.decode("utf-8")
        self._guard()
        return result

    def _row(self, cursor, values):
        self._guard()
        return sqlite3.Row(cursor, values)

    def execute(self, statement, parameters=()):
        self._guard()
        try:
            result = self.connection.execute(statement, parameters)
        except sqlite3.Error:
            if self.store.blocked_reason:
                raise _Unavailable(self.store.blocked_reason) from None
            raise
        self._guard()
        return _ReadCursor(result, self._guard)

    def set_trace_callback(self, callback):
        self.connection.set_trace_callback(callback)

    def close(self):
        self.connection.close()


class _ReadOnlyStore(collection._MetadataStore):
    def __init__(self, path):
        # Deliberately do not call the writable Store constructor.
        self.path = external_path(path)
        if not self.path.exists():
            raise _Unavailable("missing_store")
        self.identity = _identity(self.path)
        if not _existing_store(self.path) or _identity(self.path) != self.identity:
            raise _Unavailable("invalid_store")
        self.last_time = utc_now()
        self.expiry = None
        self.tombstoned = False
        self.blocked_reason = None
        self.db = sqlite3.connect(self.path.as_uri() + "?mode=ro&immutable=1", uri=True,
                                  isolation_level=None, cached_statements=0)
        try:
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA query_only=ON")
            self.db.execute("PRAGMA trusted_schema=OFF")
            self.db.execute("BEGIN")
            self.db.set_authorizer(self._authorizer)
            self.db = _ReadConnection(self.db, self)
            self.check_identity()
        except BaseException:
            self.db.close()
            raise

    def check_identity(self):
        if _identity(self.path) != self.identity:
            raise _Unavailable("invalid_store")

    def check_time(self, payload=False):
        now = utc_now()
        reason = None
        if now < self.last_time:
            reason = "clock_rollback"
        self.last_time = max(now, self.last_time)
        if payload and reason is None:
            if self.tombstoned:
                reason = "tombstoned_input"
            elif self.expiry is None:
                reason = "input_unavailable"
            elif now >= self.expiry:
                reason = "expired_input"
        if reason:
            self.blocked_reason = reason
            raise _Unavailable(reason)
        return now

    def _authorizer(self, action, table, column, database, trigger):
        # This preparation-time guard denies payload access before metadata is
        # bound. The connection also guards execution and decoding boundaries.
        if action == sqlite3.SQLITE_READ and (table, column) in {
                ("captures", "payload"), ("evidence", "record")}:
            try:
                self.check_time(payload=True)
            except Exception:
                return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    def retained_parse(self, capture_id, now=None):
        self.check_time(payload=True)
        capture = self.capture(capture_id)
        body = self.db.execute("SELECT payload FROM captures WHERE capture_id=?", (capture_id,)).fetchone()[0]
        self.check_time(payload=True)
        if type(body) is not bytes or len(body) > MAX_BYTES or digest(body) != capture.content_hash:
            raise _Unavailable("input_mismatch")
        self.check_time(payload=True)
        parsed = sources.parse(body, capture)
        self.check_time(payload=True)
        return parsed

    def bundle(self, identity, now=None):
        # Frozen bundles contain IDs and gate metadata, not retained titles.
        fingerprint(identity)
        row = self.db.execute("SELECT record FROM bundles WHERE bundle_id=?", (identity,)).fetchone()
        if row is None or len(row[0]) > MAX_RECEIPT_BYTES:
            raise _Unavailable("input_mismatch")
        value = decode_input(row[0].encode())
        exact(value, {"schema_version", "gate", "references"})
        integer(value["schema_version"], 1, 1)
        validate_result(value["gate"])
        _require(digest(value) == identity and type(value["references"]) is list
                 and len(value["references"]) <= MAX_RECORDS)
        for ref in value["references"]:
            exact(ref, {"evidence_id", "capture_id", "source_id", "content_hash", "parser_version"})
            capture = self.capture(ref["capture_id"])
            fingerprint(ref["evidence_id"])
            _require(ref["content_hash"] == capture.content_hash and ref["source_id"] == capture.source_id
                     and ref["parser_version"] == capture.parser_version)
        _require(sorted(r["evidence_id"] for r in value["references"]) == value["gate"]["accepted_ids"])
        return {"bundle": value}


@dataclass(frozen=True)
class Preparation:
    """Retained private text is confined to facts; record is safe for stdout."""
    record: dict
    facts: tuple = ()
    provenance: tuple = ()
    clusters: tuple = ()


def _board_locator(value):
    parsed = urlsplit(value)
    return (parsed.scheme == "https" and parsed.netloc == "www.federalreserve.gov"
            and parsed.path.startswith("/") and not parsed.query and not parsed.fragment
            and "\\" not in value and not any(ord(c) < 33 for c in value))


def _prepare_facts(records, capture, gate):
    profile = Profile.from_dict(gate["profile"])
    _require(profile.to_dict() == runtime.profile_for("live_observation").to_dict())
    ttl = next(rule.max_age_seconds for rule in profile.rules if rule.domain == "news")
    source_rejections = {}
    for row in gate["rejected"]:
        _require(row["reason"] in {"stale evidence", "conflicting evidence"})
        source_rejections[row["evidence_id"]] = {
            "stale evidence": "stale_news", "conflicting evidence": "conflicting_news_versions",
        }[row["reason"]]
    facts, provenance, candidates, rejected = [], [], [], {}
    for record in records:
        _require(record.mode == "live_observation" and record.domain == "news" and record.tier == "A")
        _require(instant(record.published_at) <= instant(record.retrieved_at) <= instant(gate["as_of"])
                 and instant(record.first_seen_at) <= instant(record.retrieved_at))
        payload = validate_payload({"kind": "news", "title": record.title})
        fact = Fact.from_dict({"source_id": record.source_id, "locator": record.locator,
                              "mode": record.mode, "published_at": record.published_at,
                              "retrieved_at": record.retrieved_at, "first_seen_at": record.first_seen_at,
                              "expires_at": record.expires_at, "parser_version": record.parser_version,
                              "content_hash": digest(payload), "payload": payload})
        facts.append(fact)
        provenance.append({"evidence_id": record.evidence_id, "capture_id": record.capture_id,
                           "fact_id": fact.fact_id, "capture_hash": capture.content_hash,
                           "fact_payload_hash": fact.content_hash, "source_mode": record.mode,
                           "published_at": record.published_at, "retrieved_at": record.retrieved_at,
                           "first_seen_at": record.first_seen_at, "expires_at": record.expires_at,
                           "disposition": "eligible", "reason": None})
        if record.evidence_id in source_rejections:
            rejected[fact.fact_id] = source_rejections[record.evidence_id]
        elif not _board_locator(record.locator):
            rejected[fact.fact_id] = "non_board_locator"
        elif any(ord(c) < 32 or c in "<>" for c in record.title):
            rejected[fact.fact_id] = "non_plain_title"
        else:
            candidates.append({**asdict(fact), "fact_id": fact.fact_id})
    news = process_news(candidates, {
        "sources": [{"source_id": "fed_press", "tier": "A"}],
        "news_ttl_seconds": ttl, "news_half_life_seconds": ttl,
    }, {"version": "unresolved-p4e", "entities": []}, gate["as_of"])
    rejected.update({r["fact_id"]: r["reason"] for r in news["rejected"]})
    for row in provenance:
        if row["fact_id"] in rejected:
            row.update(disposition="excluded", reason=rejected[row["fact_id"]])
    provenance.sort(key=lambda r: r["evidence_id"])
    clusters = sorted(({"cluster_id": r["cluster_id"], "member_ids": r["member_ids"]}
                       for r in news["items"]), key=lambda r: r["cluster_id"])
    facts.sort(key=lambda f: f.fact_id)
    return tuple(facts), provenance, clusters


def _prepare_path(path, snapshot_id, *, root=ROOT):
    config, original = _original(root)
    observation = _base(config, original)
    store = None
    try:
        store = _ReadOnlyStore(path)
        runs = store.db.execute("SELECT run_id FROM runs").fetchall()
        if len(runs) != 1 or runs[0][0] != original["run_id"]:
            raise _Unavailable("input_mismatch")
        run = store.run(original["run_id"])
        if run["status"] != "complete":
            raise _Unavailable("interrupted_input")
        _require(run["summary"] == original and run["intent"] == collection.INTENT + config["source_snapshot"])
        ids = [r[0] for r in store.db.execute("SELECT capture_id FROM captures")]
        _require(ids == original["capture_ids"])
        if len(ids) != 1:
            raise _Unavailable("input_unavailable")
        capture = store.capture(ids[0])
        _require(original["observations"][0] == {
            key: capture.version_id if key == "capture_id" else getattr(capture, key)
            for key in original["observations"][0]})
        _require(capture.mode == "live_observation" and len(run["attempts"]) == 1
                 and capture.request_id == run["attempts"][0]["attempt_id"])
        store.expiry = instant(capture.expires_at)
        store.tombstoned = bool(store.db.execute("SELECT 1 FROM tombstones WHERE capture_id=?", (ids[0],)).fetchone())
        gate = store.bundle(original["bundle_id"])["bundle"]["gate"]
        _require({k: gate[k] for k in original["gate"]} == original["gate"])
        _require(gate["profile"] == runtime.profile_for("live_observation").to_dict())
        observation["source_as_of"] = gate["as_of"]
        count = store.db.execute("SELECT count(*) FROM evidence").fetchone()[0]
        if original["parse"]["total"] > MAX_RECORDS or count > MAX_RECORDS:
            raise _Unavailable("input_limit")
        store.check_time(payload=True)
        size = store.db.execute("SELECT length(payload) FROM captures WHERE capture_id=?", (ids[0],)).fetchone()[0]
        if size is None:
            raise _Unavailable("input_unavailable")
        if size > MAX_BYTES:
            raise _Unavailable("input_limit")
        normalized_size = store.db.execute(
            "SELECT coalesce(sum(length(CAST(record AS BLOB))),0) FROM evidence").fetchone()[0]
        if normalized_size > MAX_BYTES:
            raise _Unavailable("input_limit")
        # The immutable reconciler verifies run/attempt/events, raw and normalized
        # hashes, parser equality and the original frozen profile/gate.
        inspected = collection._inspect(store)
        _require(inspected["summary"] == original)
        store.check_time(payload=True)
        parsed = store.retained_parse(ids[0])
        _require(parsed.counts == original["parse"] and count == len(parsed.records))
        if not parsed.records:
            raise _Unavailable("input_unavailable")
        store.check_time(payload=True)
        facts, provenance, clusters = _prepare_facts(parsed.records, capture, gate)
        store.check_time(payload=True)
        store.check_identity()
        counts = Counter(original["parse"]["reasons"])
        counts.update(r["reason"] for r in provenance if r["reason"] is not None)
        eligible = sum(r["disposition"] == "eligible" for r in provenance)
        observation.update(outcome="prepared", reason="local_preparation",
                           processed_at=stamp(store.check_time(payload=True)),
                           prepared_count=len(facts), eligible_count=eligible,
                           excluded_count=len(facts) - eligible, cluster_count=len(clusters),
                           reason_counts=dict(counts), provenance=provenance, clusters=clusters,
                           prepared_digest=digest({"facts": [asdict(f) for f in facts],
                                                   "provenance": provenance, "clusters": clusters,
                                                   "source_as_of": gate["as_of"],
                                                   "source_probe_digest": config["source_probe_digest"]}))
        record = build_preparation_record(snapshot_id, observation, root=root)
        store.check_time(payload=True)
        store.check_identity()
        return Preparation(record, facts, tuple(provenance), tuple(clusters))
    except KeyboardInterrupt:
        reason = "preparation_interrupted"
    except _Unavailable as exc:
        reason = exc.reason
    except (Invalid, sqlite3.Error, OSError):
        reason = "input_mismatch" if store is not None else "invalid_store"
    except Exception:
        reason = "local_operation_failed"
    finally:
        if store is not None:
            store.close()
    if store is not None and store.blocked_reason:
        reason = store.blocked_reason
    # Drop every partially prepared private object on a terminal failure.
    failed = _base(config, original)
    failed.update(reason=reason, source_as_of=observation["source_as_of"])
    return Preparation(build_preparation_record(snapshot_id, failed, root=root))


def _authorize():
    try:
        from tools.governance import preparation_gate
        snapshot = preparation_gate(ROOT)
        fingerprint(snapshot)
        return snapshot
    except Exception:
        raise Invalid("preparation_gate_blocked") from None


def _path():
    return Path.home() / ".a-share-market-intelligence" / "p4s-fed-probe.sqlite"


def prepare():
    """Independently authorized, fixed-input in-memory preparation."""
    snapshot = _authorize()
    return _prepare_path(_path(), snapshot, root=ROOT)


def main(argv=None):
    try:
        args = list(sys.argv[1:] if argv is None else argv)
        if args != ["prepare"]:
            raise Invalid("invalid_cli_arguments")
        result = prepare().record
        validate_preparation_record(result, result["snapshot_id"], root=ROOT)
        print(canonical(result))
        return 0
    except KeyboardInterrupt:
        reason = "preparation_interrupted"
    except Exception as exc:
        reason = str(exc) if type(exc) is Invalid and str(exc) in LOCAL_ERRORS else "local_operation_failed"
    print(canonical({"status": "ERROR", "reason": reason}))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
