"""One released NBS metadata acquisition; private, provenance-bound P4I replay.

Raw bytes are transient. Stored checksums audit local custody, not remote truth
or a replay of the unavailable raw parser input. No caller-selectable capability.
"""

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import timedelta
from email.utils import parsedate_to_datetime
import http.client
import io
import json
from pathlib import Path
import queue
import re
import socket
import ssl
import sys
import threading
import time
import xml.etree.ElementTree as ET

from . import intelligence, preparation, runtime, sources
from .collection import _MetadataStore, _release
from .gate import Calendar, Profile, Rule, evaluate
from .models import (Capture, Evidence, Invalid, UNIVERSE, canonical, digest,
                     evidence_for, exact, fingerprint, instant, integer, stamp, utc_now)
from .source_policy import (PURPOSE, RIGHTS, bounded_copy, https_location,
                            validate_source_policy)
from .store import Busy, CHECKOUT, external_path

ROOT = CHECKOUT
HOST = "www.stats.gov.cn"
PREFIX = "https://www.stats.gov.cn/sj/zxfb/"
ENDPOINT = PREFIX + "rss.xml"
SOURCE = "nbs_latest"
TASK = "p4n-nbs-one-shot"
INTENT = TASK + ":"
MODE = "live_observation"
MAX_BYTES = 512000
MAX_ITEMS = 100
TIMEOUT = 10
TTL = 86400
TERMS = "https://www.stats.gov.cn/wzgl/202302/t20230217_1912857.html"
GUIDANCE = "https://www.stats.gov.cn/wzgl/rss/202302/t20230217_1912859.html"
LIMITATIONS = ["metadata_auditable_only", "raw_replay_unavailable",
               "remote_truth_not_authenticated", "legal_entitlement_not_verified"]
REJECTIONS = {"unknown_attribution", "conflicting_attribution", "unknown_rights",
              "invalid_fields", "invalid_locator", "unknown_publication_timezone",
              "invalid_publication_time", "invalid_title", "duplicate_item"}
TRANSPORT_FAILURES = {"http_status", "content_type", "content_encoding", "content_length",
                      "transfer_encoding", "response_size", "empty_dns", "private_dns"}
UNKNOWN_FAILURES = {"transport_unknown", "transport_deadline", "truncated_response",
                    "collection_interrupted", "prior_attempt_incomplete"}
ACQUISITION_REASONS = TRANSPORT_FAILURES | UNKNOWN_FAILURES | {
    "metadata_recorded", "empty_or_all_rejected_feed", "parse_failed"}
COMPOSITION_REASONS = {"awaiting_composition", "local_composition", "no_eligible_metadata",
                       "input_unavailable", "expired_input", "tombstoned_input",
                       "clock_rollback", "input_mismatch", "composition_interrupted"}
OWN_LABELS = {"\u56fd\u5bb6\u7edf\u8ba1\u5c40", "National Bureau of Statistics",
              "National Bureau of Statistics of China"}
DC = "{http://purl.org/dc/elements/1.1/}"
ATTRIBUTION_TAGS = {"author", "source", "publisher", DC + "creator", DC + "source", DC + "publisher"}
RIGHTS_TAGS = {"copyright", "rights", "license", DC + "rights"}


def _require(condition):
    if not condition:
        raise Invalid("invalid_nbs_state")


def _time(value):
    parsed = instant(value)
    _require(stamp(parsed) == value)
    return parsed


def _id(value):
    _require(type(value) is str and re.fullmatch(r"[0-9a-f]{32}", value) is not None)


def _counts(value):
    exact(value, {"total", "accepted", "rejected", "reasons"})
    for name in ("total", "accepted", "rejected"):
        integer(value[name], 0, MAX_ITEMS)
    _require(type(value["reasons"]) is dict and set(value["reasons"]) <= REJECTIONS)
    for count in value["reasons"].values():
        integer(count, 1, MAX_ITEMS)
    _require(value["total"] == value["accepted"] + value["rejected"]
             and value["rejected"] == sum(value["reasons"].values()))


def _coverage():
    return {"covered": 0, "expected": 5, "missing": list(UNIVERSE)}


def not_run_record(snapshot_id):
    """Pure placeholder; never authorizes a request or consults a runtime store."""
    return validate_observation_record({"schema_version": 1, "package_id": "P4N",
        "snapshot_id": snapshot_id, "status": "NOT_RUN", "observed_at": None,
        "observation": None, "reason": "awaiting_release"}, snapshot_id)


def validate_observation_record(value, snapshot_id):
    """Validate sanitized evidence without filesystem, store or external I/O."""
    try:
        value = bounded_copy(value, 160000)
        fingerprint(snapshot_id)
        exact(value, {"schema_version", "package_id", "snapshot_id", "status",
                      "observed_at", "observation", "reason"})
        integer(value["schema_version"], 1, 1)
        _require(value["package_id"] == "P4N" and value["snapshot_id"] == snapshot_id)
        if value["status"] == "NOT_RUN":
            _require(value["observed_at"] is None and value["observation"] is None
                     and value["reason"] == "awaiting_release")
            return value
        _require(value["status"] in {"RECORDED", "FAILED", "UNKNOWN"}
                 and value["reason"] == "source_observation")
        o = value["observation"]
        exact(o, {"source_id", "run_id", "attempt_id", "started_at", "finished_at",
                  "request_outcome", "reserved_bytes", "observed_bytes", "capture",
                  "parse", "records", "source_as_of", "source_policy_hash", "manifest_hash",
                  "summary_hash", "source_gate_id", "composition", "reason", "limitations",
                  "benchmark_coverage", "new_live_research_runs"})
        _require(o["source_id"] == SOURCE and o["limitations"] == LIMITATIONS
                 and canonical(o["benchmark_coverage"]) == canonical(_coverage()))
        integer(o["new_live_research_runs"], 0, 0)
        _id(o["run_id"])
        _id(o["attempt_id"])
        started, finished = _time(o["started_at"]), _time(o["finished_at"])
        now = utc_now()
        _require(started <= finished <= now and value["observed_at"] == o["finished_at"])
        integer(o["reserved_bytes"], MAX_BYTES, MAX_BYTES)
        _require(o["request_outcome"] in {"success", "failed", "unknown"})
        if o["observed_bytes"] is not None:
            integer(o["observed_bytes"], 0, MAX_BYTES)
        _require(o["request_outcome"] != "unknown" or o["observed_bytes"] is None)
        _require(o["reason"] in ACQUISITION_REASONS)
        _require(o["source_policy_hash"] == digest(_policy()))
        for name in ("manifest_hash", "summary_hash", "source_gate_id"):
            if o[name] is not None:
                fingerprint(o[name])
        _require((o["summary_hash"] is None) == (o["source_gate_id"] is None))
        capture = o["capture"]
        if capture is not None:
            exact(capture, {"capture_id", "content_hash", "response_hash", "retrieved_at",
                            "first_seen_at", "expires_at", "parser_version", "retention"})
            for name in ("capture_id", "content_hash", "response_hash"):
                fingerprint(capture[name])
            retrieved = _time(capture["retrieved_at"])
            _require(started <= retrieved <= finished and capture["first_seen_at"] == capture["retrieved_at"]
                     and _time(capture["expires_at"]) - retrieved == timedelta(seconds=TTL)
                     and capture["parser_version"] == "rss-v1" and capture["retention"] == "metadata_only"
                     and o["source_as_of"] == capture["retrieved_at"] and o["request_outcome"] == "success")
        else:
            _require(o["source_as_of"] is None and o["parse"] is None and o["manifest_hash"] is None)
        _require(type(o["records"]) is list and len(o["records"]) <= MAX_ITEMS)
        if o["parse"] is None:
            _require(not o["records"])
        else:
            _counts(o["parse"])
            _require(capture is not None and len(o["records"]) == o["parse"]["accepted"])
        ids, items = [], []
        for row in o["records"]:
            exact(row, {"evidence_id", "item_id", "normalized_hash", "published_at"})
            for name in ("evidence_id", "item_id", "normalized_hash"):
                fingerprint(row[name])
            _require(row["normalized_hash"] == row["evidence_id"]
                     and instant(row["published_at"]) <= instant(capture["first_seen_at"]))
            ids.append(row["evidence_id"])
            items.append(row["item_id"])
        _require(ids == sorted(set(ids)) and len(items) == len(set(items)))
        if o["manifest_hash"] is not None:
            _require(o["manifest_hash"] == digest(_manifest(o)))
        if o["reason"] == "prior_attempt_incomplete":
            _require(value["status"] == "UNKNOWN" and o["summary_hash"] is None)
        elif o["request_outcome"] == "unknown":
            _require(value["status"] == "UNKNOWN" and o["reason"] in UNKNOWN_FAILURES and capture is None)
        elif o["request_outcome"] == "failed":
            _require(value["status"] == "FAILED" and o["reason"] in TRANSPORT_FAILURES and capture is None)
        else:
            _require(o["observed_bytes"] is not None and capture is not None and o["manifest_hash"] is not None)
            if o["parse"] is None:
                _require(value["status"] == "FAILED" and o["reason"] == "parse_failed")
            else:
                accepted = o["parse"]["accepted"] > 0
                _require(value["status"] == ("RECORDED" if accepted else "FAILED")
                         and o["reason"] == ("metadata_recorded" if accepted else "empty_or_all_rejected_feed"))
        c = o["composition"]
        if o["reason"] != "prior_attempt_incomplete":
            _require(o["summary_hash"] is not None and o["source_gate_id"] is not None)
        exact(c, {"status", "reason", "input_count", "rejected_count", "reason_counts",
                  "bundle_id", "fingerprint", "processed_at"})
        _require(c["status"] in {"NOT_RUN", "COMPOSED", "UNAVAILABLE"} and c["reason"] in COMPOSITION_REASONS)
        expected_count = o["parse"]["accepted"] if o["parse"] else 0
        integer(c["input_count"], expected_count, expected_count)
        integer(c["rejected_count"], 0, expected_count)
        _require(type(c["reason_counts"]) is dict and set(c["reason_counts"]) <= {
            "stale_news", "conflicting_news_versions", "future_or_inconsistent_possession", "expired_evidence"})
        for count in c["reason_counts"].values():
            integer(count, 1, MAX_ITEMS)
        _require(sum(c["reason_counts"].values()) == c["rejected_count"])
        if c["status"] == "COMPOSED":
            _require(value["status"] == "RECORDED" and expected_count > 0 and c["reason"] == "local_composition")
            fingerprint(c["bundle_id"])
            fingerprint(c["fingerprint"])
            _require(o["summary_hash"] is not None and finished <= _time(c["processed_at"]) <= now
                     and _time(c["processed_at"]) < instant(capture["expires_at"]))
        else:
            _require(c["rejected_count"] == 0 and not c["reason_counts"]
                     and c["bundle_id"] is None and c["fingerprint"] is None)
            if c["status"] == "NOT_RUN":
                _require(c["reason"] == "awaiting_composition" and c["processed_at"] is None)
            else:
                _require(c["reason"] not in {"awaiting_composition", "local_composition"})
                _require(_time(c["processed_at"]) <= now)
        return value
    except Exception:
        raise Invalid("invalid_observation_record") from None


def _policy():
    # Denied means disabled by this package, not a claim that NBS universally
    # forbids every future egress operation. No source-policy grant is implied.
    return validate_source_policy({"schema_version": 1, "version": "p4n-normalized-v1", "sources": [{
        "source_id": SOURCE, "synthetic": False, "purpose": PURPOSE,
        "origin": "https://" + HOST, "locator_prefix": PREFIX,
        "retention": {"class": "normalized_only", "max_age_seconds": TTL, "basis_refs": [TERMS]},
        "rights": {name: {"state": "permitted" if name in {
            "automated_retrieval", "normalized_retention", "derived_analysis"} else "denied",
            "basis_refs": [GUIDANCE if name == "automated_retrieval" else TERMS]} for name in RIGHTS}}]})


def _manifest(observation):
    return {name: observation[name] for name in ("capture", "parse", "records", "source_as_of", "source_policy_hash")}


def _composition(count=0):
    return {"status": "NOT_RUN", "reason": "awaiting_composition", "input_count": count,
            "rejected_count": 0, "reason_counts": {}, "bundle_id": None,
            "fingerprint": None, "processed_at": None}


def _locator(value):
    origin, path = https_location(value)
    _require(origin == "https://" + HOST and path.startswith("/sj/zxfb/")
             and re.fullmatch(r"/sj/zxfb/(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-]+\.html?", path) is not None)
    return value


def _parse(body, capture):
    capture.validate()
    _require(type(body) is bytes and len(body) <= MAX_BYTES and digest(body) == capture.content_hash)
    value = sources._safe_text(body)
    if re.search(r"<!\s*(DOCTYPE|ENTITY)", value, re.I):
        raise Invalid("parse_failed")
    encoding = re.search(r"<\?xml[^>]*encoding\s*=\s*['\"]([^'\"]+)", value, re.I)
    _require(not encoding or encoding[1].lower() in {"utf-8", "utf8", "us-ascii"})
    depth, nodes = 0, 0
    parser = ET.iterparse(io.StringIO(value), events=("start", "end"))
    try:
        for event, element in parser:
            if event == "start":
                depth += 1
                nodes += 1
                _require(depth <= 12 and nodes <= 5000 and len(element.attrib) <= 16)
            else:
                depth -= 1
        root = parser.root
    except ET.ParseError:
        raise Invalid("parse_failed") from None
    _require(root.tag == "rss" and root.attrib == {"version": "2.0"}
             and len(root.findall("channel")) == 1)
    items = root.findall("./channel/item")
    _require(len(items) <= MAX_ITEMS and sum(e.tag == "item" for e in root.iter()) == len(items))
    records, rejected, seen = [], Counter(), set()
    channel = root.find("channel")
    inherited_rights = [child for child in channel if child.tag in RIGHTS_TAGS]
    # Channel restrictions apply to every item, but item subtrees retain their
    # own scope. Unknown namespaces and nesting are not understood permissions.
    ambiguous_channel_rights = any(
        node.tag.rsplit("}", 1)[-1].lower() in {"rights", "copyright", "license"}
        and (node is not child or node.tag not in RIGHTS_TAGS)
        for child in channel if child.tag != "item" for node in child.iter())
    for item in items:
        reason = "invalid_fields"
        try:
            reason = "unknown_rights"
            _require(not ambiguous_channel_rights)
            attrs = [child for child in item if child.tag in ATTRIBUTION_TAGS]
            suspicious = [child for child in item.iter() if child.tag.rsplit("}", 1)[-1].lower() in {
                "author", "creator", "publisher", "source", "rights", "copyright", "license"}
                and child not in attrs and not (child in list(item) and child.tag in RIGHTS_TAGS)]
            reason = "unknown_attribution"
            _require(attrs and not suspicious)
            reason = "conflicting_attribution"
            _require(len({child.tag for child in attrs}) == len(attrs)
                     and all(len(child) == 0 and (child.text or "").strip() in OWN_LABELS
                         and (not child.attrib or child.tag == "source" and child.attrib == {"url": ENDPOINT})
                         for child in attrs))
            reason = "unknown_rights"
            # Only explicit references to the assessed terms are understood. No
            # absent restriction is converted into a new permission statement.
            rights = inherited_rights + [child for child in item if child.tag in RIGHTS_TAGS]
            _require(all(len(child) == 0 and not child.attrib and (child.text or "").strip() == TERMS for child in rights))
            reason = "invalid_fields"
            _require(not item.attrib and all(len(item.findall(name)) == 1 for name in ("title", "link", "pubDate")))
            for name in ("title", "link", "pubDate"):
                child = item.find(name)
                _require(not child.attrib and len(child) == 0)
            reason = "invalid_locator"
            link = _locator((item.findtext("link") or "").strip())
            reason = "unknown_publication_timezone"
            date = (item.findtext("pubDate") or "").strip()
            _require(re.search(r"(?:[+-]\d{4}|GMT|UT)\Z", date) is not None and not date.endswith("-0000"))
            reason = "invalid_publication_time"
            published = parsedate_to_datetime(date)
            _require(published.tzinfo is not None and published.utcoffset() is not None
                     and published <= instant(capture.first_seen_at))
            reason = "invalid_title"
            title = (item.findtext("title") or "").strip()
            _require(0 < len(title) <= 2000 and not any(ord(c) < 32 or c in "<>" for c in title))
            record = evidence_for(capture, domain="news", key=link, locator=link,
                                  title=title, published_at=published.isoformat())
            reason = "duplicate_item"
            _require(link not in seen)
            seen.add(link)
            records.append(record)
        except (Invalid, ValueError, TypeError, OverflowError):
            rejected[reason] += 1
    return sources.Parsed(tuple(records), len(items), dict(rejected))


class _Transport(sources.TransportError):
    def __init__(self, reason, outcome="unknown", observed_bytes=None):
        super().__init__(reason, outcome)
        self.observed_bytes = observed_bytes


class _Deadline:
    """Cancellation also closes TLS during handshake/header slow-drip reads."""
    def __init__(self):
        self.end = time.monotonic() + TIMEOUT
        self.cancelled = threading.Event()
        self.lock = threading.Lock()
        self.sockets = []

    def remaining(self):
        remaining = self.end - time.monotonic()
        if self.cancelled.is_set() or remaining <= 0:
            raise _Transport("transport_deadline")
        return remaining

    def bind(self, sock):
        with self.lock:
            if self.cancelled.is_set():
                sock.close()
                raise _Transport("transport_deadline")
            self.sockets.append(sock)
        sock.settimeout(self.remaining())
        return sock

    def abort(self):
        self.cancelled.set()
        with self.lock:
            sockets = list(self.sockets)
        for sock in sockets:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()


class _TLS(http.client.HTTPSConnection):
    def __init__(self, address, deadline):
        super().__init__(HOST, timeout=TIMEOUT, context=ssl.create_default_context())
        self.address, self.deadline = address, deadline

    def connect(self):
        family, kind, proto, _, address = self.address
        raw = self.deadline.bind(socket.socket(family, kind, proto))
        self.sock = raw
        raw.connect(address)
        raw.settimeout(self.deadline.remaining())
        self.sock = self.deadline.bind(self._context.wrap_socket(
            raw, server_hostname=HOST, do_handshake_on_connect=False))
        self.sock.do_handshake()
        self.sock.settimeout(self.deadline.remaining())


def _exchange(deadline):
    addresses = sources.public_addresses(socket.getaddrinfo(HOST, 443, type=socket.SOCK_STREAM))
    deadline.remaining()
    connection = _TLS(addresses[0], deadline)
    try:
        deadline.remaining()
        connection.request("GET", "/sj/zxfb/rss.xml", headers={
            "User-Agent": "P4N-ReadOnly-Metadata/1.0", "Accept": "application/rss+xml, application/xml, text/xml",
            "Accept-Encoding": "identity", "Connection": "close"})
        response = connection.getresponse()
        deadline.remaining()
        if response.status != 200:
            raise _Transport("http_status", "failed", 0)
        for header in ("Content-Type", "Content-Encoding", "Content-Length", "Transfer-Encoding"):
            if len(response.headers.get_all(header, [])) > 1:
                raise _Transport("content_length", "failed", 0)
        if response.getheader("Content-Encoding", "identity").lower() != "identity":
            raise _Transport("content_encoding", "failed", 0)
        media = response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
        if media not in {"application/rss+xml", "application/xml", "text/xml"}:
            raise _Transport("content_type", "failed", 0)
        length = response.getheader("Content-Length")
        if length is not None and (re.fullmatch(r"[0-9]{1,6}", length) is None or int(length) > MAX_BYTES):
            raise _Transport("content_length", "failed", 0)
        transfer = response.getheader("Transfer-Encoding")
        if transfer is not None and (transfer.lower() != "chunked" or length is not None):
            raise _Transport("transfer_encoding", "failed", 0)
        body = bytearray()
        while True:
            deadline.remaining()
            chunk = response.read1(min(16384, MAX_BYTES + 1 - len(body)))
            deadline.remaining()
            if not chunk:
                break
            body.extend(chunk)
            if len(body) > MAX_BYTES:
                raise _Transport("response_size", "failed", None)
        if length is not None and len(body) != int(length):
            raise _Transport("truncated_response")
        return sources.Response(bytes(body), media).validate()
    finally:
        connection.close()


def _fetch():
    """Hard caller deadline includes DNS, TLS, headers and body; never retry."""
    deadline, result = _Deadline(), queue.Queue(maxsize=1)

    def work():
        try:
            response = _exchange(deadline)
            deadline.remaining()
            result.put(response)
        except BaseException as exc:
            if type(exc) is _Transport:
                result.put(exc)
            elif isinstance(exc, sources.TransportError) and str(exc) in {"empty_dns", "private_dns"}:
                result.put(_Transport(str(exc), "failed", 0))
            else:
                result.put(_Transport("transport_unknown"))

    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    try:
        try:
            response = result.get(timeout=deadline.remaining())
        except queue.Empty:
            raise _Transport("transport_deadline") from None
        deadline.remaining()
        if isinstance(response, _Transport):
            raise response
        return response
    finally:
        deadline.abort()


def _profile():
    return Profile("p4n-metadata-v1", "p4n-metadata-v1", MODE, UNIVERSE,
                   (Rule("market", "market-v1", True, 1, TTL, False),
                    Rule("news", "news-v1", True, 1, TTL, False)))


def _calendar():
    return Calendar("p4n-unavailable-calendars-v1", ())


def _authorize():
    try:
        from tools.governance import nbs_gate
        snapshot = nbs_gate(ROOT)
        fingerprint(snapshot)
        return snapshot
    except Exception:
        raise Invalid("nbs_gate_blocked") from None


def _path():
    return external_path(Path.home() / ".a-share-market-intelligence" / "p4n-nbs-ingestion.sqlite")


class _ReadOnlyStore(preparation._ReadOnlyStore):
    """Reuse guarded decoding/schema/path checks, never the Fed reconciler."""
    def _authorizer(self, action, table, column, database, trigger):
        # Raw is only inspected with IS NULL, never returned or decoded. Evidence
        # text remains inaccessible until acquisition/expiry is bound below.
        if table == "captures" and column == "payload":
            return 0
        return super()._authorizer(action, table, column, database, trigger)

    def check_time(self, payload=False):
        now = utc_now()
        reason = "clock_rollback" if now < self.last_time else None
        self.last_time = max(self.last_time, now)
        if payload and reason is None:
            reason = ("tombstoned_input" if self.tombstoned else
                      "input_unavailable" if self.expiry is None else
                      "expired_input" if now >= self.expiry else None)
        if reason:
            self.blocked_reason = reason
            raise preparation._Unavailable(reason)
        return now


def _capture_metadata(capture):
    return {"capture_id": capture.version_id, **{name: getattr(capture, name) for name in (
        "content_hash", "response_hash", "retrieved_at", "first_seen_at", "expires_at",
        "parser_version", "retention")}}


def _provenance(records):
    return sorted(({"evidence_id": r.evidence_id, "item_id": digest(r.locator),
                    "normalized_hash": digest(r.to_dict()), "published_at": r.published_at}
                   for r in records), key=lambda row: row["evidence_id"])


def _make_manifest(capture, parsed):
    return {"capture": _capture_metadata(capture), "parse": parsed.counts if parsed else None,
            "records": _provenance(parsed.records) if parsed else [],
            "source_as_of": capture.retrieved_at, "source_policy_hash": digest(_policy())}


def _record(snapshot, run, manifest=None, gate_id=None):
    attempts = run["attempts"]
    _require(len(attempts) == 1)
    attempt = attempts[0]
    summary = run["summary"]
    reason = summary["error_codes"][0] if summary else "prior_attempt_incomplete"
    outcome = attempt["outcome"] if attempt["outcome"] != "reserved" else "unknown"
    status = ("UNKNOWN" if not summary or outcome == "unknown" else
              "RECORDED" if reason == "metadata_recorded" else "FAILED")
    finished = summary["finished_at"] if summary else run["last_event_at"]
    acquisition = manifest or {"capture": None, "parse": None, "records": [],
                              "source_as_of": None, "source_policy_hash": digest(_policy())}
    observation = {"source_id": SOURCE, "run_id": run["run_id"], "attempt_id": attempt["attempt_id"],
        "started_at": run["started_at"], "finished_at": finished, "request_outcome": outcome,
        "reserved_bytes": MAX_BYTES, "observed_bytes": attempt["observed_bytes"], **acquisition,
        "manifest_hash": digest(manifest) if manifest else None,
        "summary_hash": digest(summary) if summary else None, "source_gate_id": gate_id if summary else None,
        "composition": _composition(acquisition["parse"]["accepted"] if acquisition["parse"] else 0),
        "reason": reason, "limitations": list(LIMITATIONS), "benchmark_coverage": _coverage(),
        "new_live_research_runs": 0}
    return validate_observation_record({"schema_version": 1, "package_id": "P4N", "snapshot_id": snapshot,
        "status": status, "observed_at": finished, "observation": observation,
        "reason": "source_observation"}, snapshot)


def _inspect_metadata(store, snapshot):
    """Reconcile metadata without loading any retained title or raw byte."""
    runs = store.db.execute("SELECT run_id FROM runs").fetchall()
    _require(len(runs) == 1)
    run = store.run(runs[0][0])
    _id(run["run_id"])
    _require(run["task_key"] == TASK and run["mode"] == MODE and run["intent"] == INTENT + snapshot)
    started = _time(run["started_at"])
    now = utc_now()
    _require(started <= now)
    attempts = run["attempts"]
    _require(len(attempts) == 1 and store.db.execute("SELECT count(*) FROM attempts").fetchone()[0] == 1)
    attempt = attempts[0]
    _id(attempt["attempt_id"])
    _require(attempt["run_id"] == run["run_id"] and started <= _time(attempt["reserved_at"]) <= now
             and attempt["day"] == attempt["reserved_at"][:10])
    if attempt["outcome"] in {"reserved", "unknown"}:
        _require(attempt["observed_bytes"] is None)
    events = [dict(row) for row in store.db.execute("SELECT * FROM events ORDER BY event_id")]
    _require(3 <= len(events) <= 16 and all(row["run_id"] == run["run_id"] for row in events))
    last = started
    groups = {}
    for event in events:
        at = _time(event["at"])
        _require(last <= at <= now)
        last = at
        groups.setdefault(event["kind"], []).append(event)
    _require(set(groups) <= {"started", "p4n_intent", "reserved", "settled", "p4n_manifest",
                             "completed", "released", "interrupted", "resumed"})
    for kind in ("started", "p4n_intent", "reserved"):
        _require(len(groups.get(kind, [])) == 1)
    _require([e["kind"] for e in events[:3]] == ["started", "p4n_intent", "reserved"])
    _require(json.loads(events[0]["detail"]) == {"generation": 1} and events[0]["at"] == run["started_at"])
    _require(json.loads(events[1]["detail"]) == {"snapshot_id": snapshot, "source_policy_hash": digest(_policy())})
    _require(json.loads(events[2]["detail"]) == {"attempt_id": attempt["attempt_id"], "bytes": MAX_BYTES}
             and events[2]["at"] == attempt["reserved_at"])
    settled = groups.get("settled", [])
    _require(len(settled) <= 1)
    if settled:
        _require(json.loads(settled[0]["detail"]) == {"attempt_id": attempt["attempt_id"], "outcome": attempt["outcome"]})
    else:
        _require(attempt["outcome"] in {"reserved", "unknown"})
    if attempt["outcome"] == "success":
        _require(attempt["reason"] == "response_received" and attempt["observed_bytes"] is not None)
    elif attempt["outcome"] == "failed":
        _require(attempt["reason"] in TRANSPORT_FAILURES)
    elif attempt["outcome"] == "unknown":
        _require(attempt["reason"] in UNKNOWN_FAILURES | {"released_pending", "lease_expired"})
    else:
        _require(attempt["reason"] is None)
    for kind in ("released", "resumed", "interrupted"):
        for event in groups.get(kind, []):
            detail = json.loads(event["detail"])
            exact(detail, {"generation", "reason"} if kind == "interrupted" else {"generation"})
            integer(detail["generation"], 1, 8)
            if kind == "interrupted":
                _require(detail["reason"] == "lease_expired")
    captures = [store.capture(row[0]) for row in store.db.execute("SELECT capture_id FROM captures")]
    _require(len(captures) <= 1)
    capture = captures[0] if captures else None
    if capture:
        _require(attempt["outcome"] == "success" and capture.source_id == SOURCE
                 and capture.locator == ENDPOINT and capture.request_id == attempt["attempt_id"]
                 and capture.retention == "metadata_only" and capture.retention_basis == "p4n-normalized-only-project-24h-v1"
                 and capture.content_type in {"application/rss+xml", "application/xml", "text/xml"}
                 and capture.parser_version == "rss-v1" and capture.mode == MODE and capture.source_timestamp is None
                 and capture.first_seen_at == capture.retrieved_at
                 and instant(capture.expires_at) - instant(capture.retrieved_at) == timedelta(seconds=TTL)
                 and instant(attempt["reserved_at"]) <= instant(capture.retrieved_at) <= now
                 and capture.response_hash == digest({"status": 200, "content_type": capture.content_type,
                                                       "content_hash": capture.content_hash}))
    _require(store.db.execute("SELECT count(*) FROM captures WHERE payload IS NOT NULL").fetchone()[0] == 0)
    manifests = groups.get("p4n_manifest", [])
    _require(len(manifests) <= 1)
    manifest = json.loads(manifests[0]["detail"]) if manifests else None
    if manifest is not None:
        exact(manifest, {"capture", "parse", "records", "source_as_of", "source_policy_hash"})
        _require(capture is not None and manifest["capture"] == _capture_metadata(capture)
                 and manifest["source_as_of"] == capture.retrieved_at)
    tombstones = store.db.execute("SELECT * FROM tombstones").fetchall()
    _require(len(tombstones) <= 1)
    if tombstones:
        t = dict(tombstones[0])
        _require(capture is not None and t["capture_id"] == capture.version_id and t["reason"] == "expired"
                 and t["content_hash"] == capture.content_hash and t["parser_version"] == "rss-v1"
                 and instant(capture.expires_at) <= _time(t["at"]) <= now)
    refs = [dict(row) for row in store.db.execute("SELECT evidence_id,capture_id FROM evidence ORDER BY evidence_id")]
    _require(len(refs) <= MAX_ITEMS and (not refs or capture is not None)
             and all(r["capture_id"] == capture.version_id for r in refs))
    _require(not tombstones or not refs)
    if manifest and not tombstones:
        _require([r["evidence_id"] for r in refs] == [r["evidence_id"] for r in manifest["records"]])
    for ref in refs:
        fingerprint(ref["evidence_id"])
    _require(store.db.execute("PRAGMA foreign_key_check").fetchone() is None)
    bundles = store.db.execute("SELECT bundle_id,record FROM bundles").fetchall()
    _require(len(bundles) <= 1)
    gate, bundle_id = None, None
    if bundles:
        bundle_id = bundles[0][0]
        bundle = intelligence.decode_input(bundles[0][1].encode())
        exact(bundle, {"schema_version", "gate", "references"})
        integer(bundle["schema_version"], 1, 1)
        _require(digest(bundle) == bundle_id)
        gate = bundle["gate"]
        from .gate import validate_result
        validate_result(gate)
        _require(gate["profile"] == _profile().to_dict() and gate["calendar"] == _calendar().to_dict()
                 and gate["mode"] == MODE and gate["status"] == "BLOCK"
                 and started <= instant(gate["as_of"]) <= now)
        expected_refs = []
        for identity in gate["accepted_ids"]:
            _require(manifest is not None and identity in {r["evidence_id"] for r in manifest["records"]})
            expected_refs.append({"evidence_id": identity, "capture_id": capture.version_id,
                                  "source_id": SOURCE, "content_hash": capture.content_hash, "parser_version": "rss-v1"})
        _require(bundle["references"] == expected_refs)
        for rejection in gate["rejected"]:
            _require(rejection["domain"] == "news" and rejection["reason"] in {"stale evidence", "conflicting evidence"}
                     and manifest is not None and rejection["evidence_id"] in {r["evidence_id"] for r in manifest["records"]})
    lease = store.db.execute("SELECT * FROM lease").fetchall()
    _require(len(lease) == 1)
    lease = dict(lease[0])
    if lease["owner"] is not None:
        _id(lease["owner"])
        _require(lease["run_id"] == run["run_id"])
        if instant(lease["expires_at"]) > now:
            raise Busy("nbs_in_progress")
    if run["status"] == "complete":
        summary = run["summary"]
        _require(summary is not None and bundle_id == summary["bundle_id"] and lease["owner"] is None
                 and len(groups.get("completed", [])) == 1 and bool(capture) == bool(manifest))
        _require(summary["run_id"] == run["run_id"] and summary["source_id"] == SOURCE
                 and summary["mode"] == MODE and summary["source_state"] == "degraded"
                 and summary["started_at"] == run["started_at"]
                 and _time(summary["finished_at"]) <= _time(run["finished_at"]) <= now
                 and summary["error_codes"] and len(summary["error_codes"]) == 1)
        _require(summary["requests"] == {"attempted": 1, "success": int(attempt["outcome"] == "success"),
            "failed": int(attempt["outcome"] == "failed"), "unknown": int(attempt["outcome"] == "unknown"),
            "reserved_bytes": MAX_BYTES, "observed_bytes": attempt["observed_bytes"]})
        _require(summary["parse"] == (manifest["parse"] if manifest and manifest["parse"] is not None
                                     else {"total": 0, "accepted": 0, "rejected": 0, "reasons": {}}))
        q = summary["quality"]
        _require(q["parse_sample_size"] == int(capture is not None)
                 and q["parse_success_count"] == int(manifest is not None and manifest["parse"] is not None)
                 and q["nonempty_parse_count"] == int(summary["parse"]["accepted"] > 0))
        _require(summary["capture_ids"] == ([capture.version_id] if capture else []))
        for observation in summary["observations"]:
            _require(observation == {key: capture.version_id if key == "capture_id" else getattr(capture, key)
                                     for key in observation})
        _require(summary["gate"] == {key: gate[key] for key in summary["gate"]})
        expected_outcome = ("observed" if summary["error_codes"] == ["metadata_recorded"] else
                            "unknown" if summary["error_codes"][0] in UNKNOWN_FAILURES else "failed")
        _require(summary["outcome"] == expected_outcome)
        if capture:
            _require(gate["as_of"] == capture.retrieved_at)
    else:
        _require(run["summary"] is None and run["finished_at"] is None and not groups.get("completed"))
    run["last_event_at"] = stamp(last)
    record = _record(snapshot, run, manifest, bundle_id)
    return run, capture, manifest, gate, record


def _inspect(store, snapshot):
    try:
        return _inspect_metadata(store, snapshot)
    except Busy:
        raise
    except Exception:
        raise Invalid("invalid_nbs_state") from None


def _finish(store, lease, run, snapshot, capture=None, parsed=None, reason="transport_unknown"):
    as_of = capture.retrieved_at if capture else stamp(utc_now())
    gate = evaluate(parsed.records if parsed else (), _profile(), _calendar(), as_of)
    bundle_id = store.freeze(gate, lease=lease)
    outcome = "observed" if reason == "metadata_recorded" else ("unknown" if reason in UNKNOWN_FAILURES else "failed")
    summary = runtime._summary(store, lease, MODE, SOURCE, run["started_at"], outcome, "degraded", parsed,
                               [capture.version_id] if capture else [], bundle_id, gate, [reason])
    store.finish(lease, summary)


def _collect(store, snapshot):
    lease, run = store.begin(TASK, MODE, intent=INTENT + snapshot)
    if lease is None:
        return
    try:
        _require(not run["attempts"])
        with store.transaction():
            store._owned(lease, utc_now())
            _require(store.db.execute("SELECT count(*) FROM events").fetchone()[0] == 1)
            store._event(lease.run_id, utc_now(), "p4n_intent", {
                "snapshot_id": snapshot, "source_policy_hash": digest(_policy())})
        _require(_authorize() == snapshot)
        store.renew(lease)
        _require(store.db.execute("SELECT count(*) FROM attempts").fetchone()[0] == 0)
        attempt = store.reserve(lease)
        try:
            response = _fetch()
            _require(type(response) is sources.Response)
            response.validate()
        except (Exception, KeyboardInterrupt) as exc:
            reason, outcome, count = "transport_unknown", "unknown", None
            if isinstance(exc, KeyboardInterrupt):
                reason = "collection_interrupted"
            elif type(exc) is _Transport and str(exc) in TRANSPORT_FAILURES | UNKNOWN_FAILURES:
                reason, outcome, count = str(exc), exc.outcome, exc.observed_bytes
            store.settle(lease, attempt, outcome, count, reason)
            _finish(store, lease, run, snapshot, reason=reason)
            return
        store.settle(lease, attempt, "success", len(response.body), "response_received")
        now = utc_now()
        capture = Capture(SOURCE, ENDPOINT, stamp(now), stamp(now), None, digest(response.body),
                          response.response_hash, response.content_type, "rss-v1", "metadata_only",
                          "p4n-normalized-only-project-24h-v1", stamp(now + timedelta(seconds=TTL)), MODE, attempt)
        try:
            parsed = _parse(response.body, capture)
        except (Invalid, ValueError, TypeError):
            parsed = None
        _require(now <= utc_now() < instant(capture.expires_at))
        store.put_capture(capture, records=parsed.records if parsed else (), lease=lease)
        manifest = _make_manifest(capture, parsed)
        with store.transaction():
            store._owned(lease, utc_now())
            store._event(lease.run_id, utc_now(), "p4n_manifest", manifest)
        reason = "parse_failed" if parsed is None else ("metadata_recorded" if parsed.records else "empty_or_all_rejected_feed")
        _finish(store, lease, run, snapshot, capture, parsed, reason)
    finally:
        _release(store, lease)


def _methods():
    return {"schema_version": 2, "version": "p4n-metadata-v1", "profile_version": "p4n-required-gaps-v1",
        "universe_id": "p4n-unconfigured-constituents-v1", "benchmark_symbols": list(UNIVERSE),
        "constituents": [], "lookback_sessions": 3, "news_half_life_seconds": TTL, "news_ttl_seconds": TTL,
        "approach_seconds": TTL, "confirmation_grace_seconds": 21600, "event_ttl_seconds": 604800,
        "flow": {"provider": "unconfigured", "series_kind": "provider_estimate", "enabled": False},
        "sources": [{"source_id": SOURCE, "tier": "A", "locator_prefix": PREFIX,
                     "scopes": ["news"], "event_ids": [], "synthetic": False}],
        "rules": [{"domain": d, "validator": d + "-p2-v1", "required": d != "flow", "allow_degraded": d == "flow"}
                  for d in ("market", "sector", "news", "event", "entity", "flow")]}


@dataclass(frozen=True)
class Composition:
    record: dict
    private_bundle: dict | None = None


class _Unavailable(preparation._Unavailable):
    def __init__(self, reason):
        self.reason = reason if reason in COMPOSITION_REASONS else "input_mismatch"
        Invalid.__init__(self, self.reason)


def _compose(store, capture, manifest, gate, record):
    o = record["observation"]
    c = o["composition"]
    try:
        if record["status"] != "RECORDED" or not manifest or not manifest["records"]:
            raise _Unavailable("no_eligible_metadata")
        store.tombstoned = bool(store.db.execute("SELECT 1 FROM tombstones").fetchone())
        store.expiry = instant(capture.expires_at)
        store.last_time = max(store.last_time, instant(o["finished_at"]))
        store.check_time(payload=True)
        size = store.db.execute("SELECT coalesce(sum(length(CAST(record AS BLOB))),0) FROM evidence").fetchone()[0]
        _require(size <= MAX_BYTES)
        store.check_time(payload=True)
        records, unavailable = store.records(now=store.check_time(payload=True))
        store.check_time(payload=True)
        _require(not unavailable and _provenance(records) == manifest["records"])
        for r in records:
            _require(r.domain == "news" and r.mode == MODE and r.tier == "A" and r.key == r.locator
                     and r.occurred_at is None and r.effective_at is None and r.observed_at is None)
            _locator(r.locator)
            _require(not any(ord(ch) < 32 or ch in "<>" for ch in r.title))
        store.check_time(payload=True)
        _require(evaluate(records, _profile(), _calendar(), manifest["source_as_of"],
                          instant(manifest["source_as_of"])) == gate)
        store.check_time(payload=True)
        facts, provenance = [], []
        for r in records:
            store.check_time(payload=True)
            payload = {"kind": "news", "title": r.title}
            fact = intelligence.Fact.from_dict({"source_id": SOURCE, "locator": r.locator, "mode": MODE,
                "published_at": r.published_at, "retrieved_at": r.retrieved_at, "first_seen_at": r.first_seen_at,
                "expires_at": r.expires_at, "parser_version": r.parser_version,
                "content_hash": digest(payload), "payload": payload})
            facts.append(asdict(fact))
            provenance.append({"evidence_id": r.evidence_id, "fact_id": fact.fact_id,
                               "fact_payload_hash": fact.content_hash})
        payload = {"schema_version": 2, "source_mode": MODE, "as_of": manifest["source_as_of"],
                   "calendar": _calendar().to_dict(), "facts": facts}
        methods, policy = _methods(), _policy()
        entities = {"schema_version": 2, "version": "p4n-unconfigured-mapping-v1", "mapping_refs": [], "entities": []}
        store.check_time(payload=True)
        inner = intelligence.build_replay_bundle(payload, methods, entities, policy)
        store.check_time(payload=True)
        intelligence.verify_replay_bundle(inner, payload, methods, entities, policy)
        store.check_time(payload=True)
        rejected = inner["content"]["rejected"]
        reasons = dict(Counter(r["reason"] for r in rejected))
        wrapper = {"schema_version": 1, "schema": "private-p4n-acquisition-replay",
            "snapshot_id": record["snapshot_id"], "source_mode": MODE, "processing_mode": "historical_replay",
            "acquisition": {k: v for k, v in o.items() if k != "composition"},
            "provenance": provenance, "input_hash": digest(payload), "source_policy_hash": digest(policy),
            "composition_hash": inner["bundle_id"], "limitations": list(LIMITATIONS),
            "permission_grant": False, "readiness": "BLOCKED_EXTERNAL_PREREQUISITES", "bundle": inner}
        c.update(status="COMPOSED", reason="local_composition", rejected_count=len(rejected), reason_counts=reasons,
                 bundle_id=inner["bundle_id"], fingerprint=digest(wrapper), processed_at=stamp(store.check_time(payload=True)))
        validated = validate_observation_record(record, record["snapshot_id"])
        store.check_identity()
        store.check_time(payload=True)
        return Composition(validated, wrapper)
    except KeyboardInterrupt:
        reason = "composition_interrupted"
    except preparation._Unavailable as exc:
        reason = exc.reason
    except Exception:
        reason = store.blocked_reason or "input_mismatch"
    c.update(_composition(c["input_count"]))
    c.update(status="UNAVAILABLE", reason=reason if reason in COMPOSITION_REASONS else "input_mismatch",
             processed_at=stamp(utc_now()))
    return Composition(validate_observation_record(record, record["snapshot_id"]))


def _read(snapshot, compose=False):
    path = _path()
    if not path.exists():
        return Composition(not_run_record(snapshot))
    with _ReadOnlyStore(path) as store:
        _, capture, manifest, gate, record = _inspect(store, snapshot)
        if compose:
            result = _compose(store, capture, manifest, gate, record)
        else:
            result = Composition(record)
    # Closing a read transaction is also outside the semantic processing guard.
    # Never let a delayed close return a private object past its current expiry.
    try:
        store.check_identity()
        store.check_time(payload=result.private_bundle is not None)
    except (Invalid, OSError):
        # A composition already reduced to sanitized UNAVAILABLE still needs
        # this terminal path when the final guard detects the same rollback.
        if not compose:
            raise
        record = result.record
        c = record["observation"]["composition"]
        c.update(_composition(c["input_count"]))
        c.update(status="UNAVAILABLE", reason=store.blocked_reason or "input_mismatch", processed_at=stamp(utc_now()))
        return Composition(validate_observation_record(record, snapshot))
    return result


def collect(allow_network=False):
    """Only this fixed entrypoint can dispatch, after authorization and reservation."""
    if allow_network is not True:
        raise Invalid("explicit_allow_network_required")
    snapshot = _authorize()
    path = _path()
    if path.exists():
        return _read(snapshot, compose=True).record
    with _MetadataStore(path) as store:
        _collect(store, snapshot)
    return _read(snapshot, compose=True).record


def status():
    return _read(_authorize()).record


def compose():
    """Return private in-memory content; stdout uses only Composition.record."""
    return _read(_authorize(), compose=True)


def sweep():
    snapshot = _authorize()
    path = _path()
    if not path.exists():
        return not_run_record(snapshot)
    with _ReadOnlyStore(path) as readonly:
        run, _, _, _, record = _inspect(readonly, snapshot)
        identity = readonly.identity
        readonly.check_identity()
    _require(preparation._identity(path) == identity)
    with _MetadataStore(path) as store:
        _inspect(store, snapshot)
        if run["status"] != "complete":
            lease, _ = store.begin(TASK, MODE, intent=INTENT + snapshot)
            if lease is not None:
                _release(store, lease)
        store.sweep()
    return _read(snapshot).record


def main(argv=None):
    try:
        args = list(sys.argv[1:] if argv is None else argv)
        if args == ["collect", "--allow-network"]:
            record = collect(True)
        elif args == ["collect"]:
            record = collect()
        elif args == ["status"]:
            record = status()
        elif args == ["compose"]:
            record = compose().record
        elif args == ["sweep"]:
            record = sweep()
        else:
            raise Invalid("invalid_cli_arguments")
        validate_observation_record(record, record["snapshot_id"])
        print(canonical(record))
        return 0 if record["status"] == "RECORDED" else 2
    except (Exception, KeyboardInterrupt) as exc:
        allowed = {"nbs_gate_blocked", "explicit_allow_network_required", "invalid_cli_arguments", "nbs_in_progress"}
        reason = str(exc) if isinstance(exc, Invalid) and str(exc) in allowed else "local_operation_failed"
        print(canonical({"status": "ERROR", "reason": reason}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
