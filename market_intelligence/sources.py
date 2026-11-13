"""Closed source catalog, bounded transport and non-executing parsers."""

from dataclasses import dataclass
from email.utils import parsedate_to_datetime
import csv
import http.client
import io
import ipaddress
from pathlib import Path
import queue
import re
import socket
import ssl
import threading
import time
import tomllib
import xml.etree.ElementTree as ET

from .models import (Capture, Invalid, UNIVERSE, digest, evidence_for, exact,
                     finite, integer, locator, text)

FED_URL = "https://www.federalreserve.gov/feeds/press_all.xml"
FED_HOST = "www.federalreserve.gov"
MAX_BYTES = 512000
TIMEOUT = 10
STATES = {"configured", "implemented_unverified", "live_verified", "degraded", "disabled", "unavailable"}


@dataclass(frozen=True)
class Source:
    source_id: str
    endpoint: str
    adapter: str
    state: str
    rights: str
    requires_key: bool
    paid: bool
    retention: str
    retention_basis: str
    retention_seconds: int

    @classmethod
    def from_dict(cls, data):
        exact(data, cls.__dataclass_fields__)
        item = cls(**data)
        text(item.source_id, "source id", 80)
        if item.state not in STATES or item.rights not in {"permitted", "unknown", "restricted"}:
            raise Invalid("invalid source state or rights")
        if item.adapter not in {"rss", "market_csv", "paid_disabled"}:
            raise Invalid("unknown adapter")
        if type(item.requires_key) is not bool or type(item.paid) is not bool:
            raise Invalid("source flags must be boolean")
        if item.retention not in {"short_term_raw", "metadata_only", "restricted"}:
            raise Invalid("invalid source retention")
        integer(item.retention_seconds, 0, 86400)
        text(item.retention_basis, "retention basis", 1000)
        if item.endpoint:
            locator(item.endpoint)
        return item

    def authorize(self):
        if (self.source_id != "fed_press" or self.endpoint != FED_URL or self.adapter != "rss"
                or self.rights != "permitted" or self.requires_key or self.paid
                or self.state not in {"implemented_unverified", "live_verified", "degraded"}
                or self.retention != "short_term_raw" or self.retention_seconds != 86400):
            raise Invalid("source not authorized for P1 requests")


def load_catalog(path):
    with Path(path).open("rb") as handle:
        data = tomllib.load(handle)
    exact(data, {"schema_version", "sources", "markets"})
    integer(data["schema_version"], 1, 1)
    sources = [Source.from_dict(row) for row in data["sources"]]
    result = {s.source_id: s for s in sources}
    if len(result) != len(sources) or set(result) != {"fed_press", "market_csv", "paid_provider"}:
        raise Invalid("unexpected source selection")
    targets = []
    for row in data["markets"]:
        exact(row, {"symbol", "state", "rights", "delay", "calendar_id", "timezone"})
        from .models import MARKETS
        if (row["symbol"] not in MARKETS or row["state"] != "unavailable"
                or row["rights"] != "unknown" or row["delay"] != "unknown"
                or (row["calendar_id"], row["timezone"]) != MARKETS[row["symbol"]]):
            raise Invalid("market coverage cannot be promoted by configuration")
        targets.append(row["symbol"])
    if tuple(targets) != UNIVERSE:
        raise Invalid("wrong market denominator")
    result["fed_press"].authorize()
    return result


class TransportError(Invalid):
    def __init__(self, reason, outcome="failed"):
        super().__init__(reason)
        self.outcome = outcome


@dataclass(frozen=True)
class Response:
    body: bytes
    content_type: str
    status: int = 200

    def validate(self):
        if type(self.body) is not bytes or len(self.body) > MAX_BYTES:
            raise TransportError("response_size")
        if self.content_type not in {"application/rss+xml", "application/xml", "text/xml"}:
            raise TransportError("content_type")
        integer(self.status, 100, 599)
        if self.status != 200:
            raise TransportError("http_status_" + str(self.status))
        return self

    @property
    def response_hash(self):
        return digest({"status": self.status, "content_type": self.content_type,
                       "content_hash": digest(self.body)})


def public_addresses(addresses):
    if not addresses:
        raise TransportError("empty_dns")
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global or ip.is_multicast or getattr(ip, "ipv4_mapped", None):
            raise TransportError("private_dns")
    return addresses


def _resolve(deadline):
    result = queue.Queue(maxsize=1)

    def lookup():
        try:
            result.put(socket.getaddrinfo(FED_HOST, 443, type=socket.SOCK_STREAM))
        except OSError:
            result.put(None)

    threading.Thread(target=lookup, daemon=True).start()
    try:
        addresses = result.get(timeout=max(0.001, deadline - time.monotonic()))
    except queue.Empty as exc:
        raise TransportError("dns_timeout", "unknown") from exc
    return public_addresses(addresses)


class _PinnedTLS(http.client.HTTPSConnection):
    def __init__(self, address, deadline):
        super().__init__(FED_HOST, timeout=TIMEOUT, context=ssl.create_default_context())
        self.address, self.deadline = address, deadline

    def connect(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("deadline")
        family, socktype, proto, _, address = self.address
        raw = socket.socket(family, socktype, proto)
        self.sock = raw
        raw.settimeout(remaining)
        try:
            raw.connect(address)
            raw.settimeout(max(0.001, self.deadline - time.monotonic()))
            self.sock = self._context.wrap_socket(raw, server_hostname=FED_HOST)
        except BaseException:
            raw.close()
            raise


def fetch(source):
    """One exact GET; direct verified TLS ignores proxies, cookies and netrc."""
    source.authorize()
    deadline = time.monotonic() + TIMEOUT
    addresses = _resolve(deadline)
    connection = _PinnedTLS(addresses[0], deadline)

    def abort():
        sock = connection.sock
        if sock:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        connection.close()

    timer = threading.Timer(max(0.001, deadline - time.monotonic()), abort)
    timer.daemon = True
    timer.start()
    try:
        connection.request("GET", "/feeds/press_all.xml", headers={
            "User-Agent": "P1-ReadOnly-Evidence/1.0", "Accept": "application/rss+xml, application/xml, text/xml",
            "Accept-Encoding": "identity", "Connection": "close"})
        response = connection.getresponse()
        if response.status != 200:
            raise TransportError("http_status_" + str(response.status))
        if response.getheader("Content-Encoding", "identity").lower() != "identity":
            raise TransportError("content_encoding")
        media = response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
        if media not in {"application/rss+xml", "application/xml", "text/xml"}:
            raise TransportError("content_type")
        lengths = response.headers.get_all("Content-Length", [])
        if len(lengths) > 1 or (lengths and (not lengths[0].isdigit() or int(lengths[0]) > MAX_BYTES)):
            raise TransportError("content_length")
        transfer = response.getheader("Transfer-Encoding")
        if transfer and (transfer.lower() != "chunked" or lengths):
            raise TransportError("transfer_encoding")
        body = bytearray()
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError("deadline")
            chunk = response.read1(min(16384, MAX_BYTES + 1 - len(body)))
            if not chunk:
                break
            body.extend(chunk)
            if len(body) > MAX_BYTES:
                raise TransportError("response_size")
        if lengths and len(body) != int(lengths[0]):
            raise TransportError("truncated_response", "unknown")
        if time.monotonic() >= deadline:
            raise TimeoutError("deadline")
        return Response(bytes(body), media).validate()
    except (OSError, http.client.HTTPException) as exc:
        raise TransportError("transport_unknown", "unknown") from exc
    finally:
        timer.cancel()
        connection.close()


@dataclass(frozen=True)
class Parsed:
    records: tuple
    total: int
    rejections: dict

    @property
    def counts(self):
        return {"total": self.total, "accepted": len(self.records),
                "rejected": sum(self.rejections.values()), "reasons": dict(self.rejections)}


def _safe_text(body):
    if type(body) is not bytes or len(body) > MAX_BYTES:
        raise Invalid("bounded bytes required")
    try:
        value = body.decode("utf-8-sig")
    except UnicodeError as exc:
        raise Invalid("only UTF-8 input supported") from exc
    if "\x00" in value:
        raise Invalid("NUL or alternate encoding")
    return value


def parse_rss(body, capture):
    capture.validate()
    if capture.parser_version != "rss-v1" or digest(body) != capture.content_hash:
        raise Invalid("parser or content hash mismatch")
    value = _safe_text(body)
    if re.search(r"<!\s*(DOCTYPE|ENTITY)", value, re.I):
        raise Invalid("DTD and entities forbidden")
    encoding = re.search(r"<\?xml[^>]*encoding\s*=\s*['\"]([^'\"]+)", value, re.I)
    if encoding and encoding[1].lower() not in {"utf-8", "utf8", "us-ascii"}:
        raise Invalid("unsupported XML encoding")
    try:
        root = ET.fromstring(value)
    except ET.ParseError as exc:
        raise Invalid("malformed XML") from exc
    if root.tag != "rss" or len(root.findall("channel")) != 1 or sum(1 for _ in root.iter()) > 5000:
        raise Invalid("unsupported RSS structure")
    items = root.findall("./channel/item")
    if len(items) > 1000:
        raise Invalid("too many RSS items")
    records, rejected, seen = [], {}, set()
    for item in items:
        try:
            if any(len(item.findall(name)) != 1 for name in ("title", "link", "pubDate")):
                raise Invalid("missing_or_duplicate_fields")
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            text(title, "title", 2000)
            locator(link)
            published = parsedate_to_datetime(item.findtext("pubDate"))
            if published.tzinfo is None:
                raise Invalid("unknown_publication_timezone")
            record = evidence_for(capture, domain="news", key=link, locator=link,
                                  title=title, published_at=published.isoformat())
            if record.evidence_id in seen:
                raise Invalid("duplicate_item")
            seen.add(record.evidence_id)
            records.append(record)
        except (Invalid, TypeError, ValueError, OverflowError):
            rejected["invalid_or_duplicate_item"] = rejected.get("invalid_or_duplicate_item", 0) + 1
    return Parsed(tuple(records), len(items), rejected)


CSV_FIELDS = ("symbol", "value", "calendar_id", "session_id", "close_at", "published_at", "data_label")


def parse_market_csv(body, capture):
    capture.validate()
    if capture.parser_version != "market-csv-v1" or digest(body) != capture.content_hash:
        raise Invalid("parser or content hash mismatch")
    reader = csv.DictReader(io.StringIO(_safe_text(body)), strict=True)
    if tuple(reader.fieldnames or ()) != CSV_FIELDS:
        raise Invalid("unknown or missing CSV columns")
    records, rejected, total = [], {}, 0
    try:
        for row in reader:
            total += 1
            if total > 1000:
                raise Invalid("too many CSV rows")
            try:
                exact(row, CSV_FIELDS)
                if any(value is None for value in row.values()):
                    raise Invalid("missing CSV cell")
                if row["data_label"] != "SYNTHETIC_TEST_ONLY" or capture.mode not in {"synthetic_test", "historical_replay"}:
                    raise Invalid("P1 CSV supports labeled offline input only")
                number = finite(float(row["value"]))
                record = evidence_for(capture, domain="market", key=row["symbol"], value=number,
                                      calendar_id=row["calendar_id"], session_id=row["session_id"],
                                      close_at=row["close_at"], observed_at=row["close_at"],
                                      published_at=row["published_at"] or None)
                records.append(record)
            except (Invalid, ValueError, TypeError):
                rejected["invalid_market_row"] = rejected.get("invalid_market_row", 0) + 1
    except csv.Error as exc:
        raise Invalid("malformed CSV") from exc
    return Parsed(tuple(records), total, rejected)


def parse(body, capture):
    return (parse_rss if capture.parser_version == "rss-v1" else parse_market_csv)(body, capture)


class PaidAdapter:
    state = "disabled"

    def collect(self, *args, **kwargs):
        raise Invalid("paid adapter always disabled; no request made")
