"""Results-only diagnostics for bounded, in-memory Board RSS responses."""

from datetime import UTC, datetime, timedelta, timezone
from email.utils import format_datetime
import json
import re
import sys
import xml.etree.ElementTree as ET

from . import primary_news_probe as probe, sources
from .models import Invalid


_METADATA = {"schema_version": 1, "mode": "offline", "historical_cause": "unknown_unretained"}
_RESPONSE_REASONS = {
    "response_size": "response_size",
    "content_type": "response_media",
    "invalid integer": "response_http",
}
_PARSE_REASONS = {
    "only UTF-8 input supported": "invalid_encoding",
    "NUL or alternate encoding": "invalid_encoding",
    "forbidden_encoding": "invalid_encoding",
    "forbidden_xml": "unsafe_xml",
    "invalid_rss": "invalid_rss_root",
}
_OPAQUE = "opaque_failure"
_COUNT_KEYS = (*probe._PARTITION, "total", "within_24h")


def _known_code(error):
    if type(error) in (Invalid, sources.TransportError):
        if len(error.args) == 1 and type(error.args[0]) is str:
            return error.args[0]
    return None


def _channel_reason(body):
    """Refine only an invalid_channel failure; do not inspect item fields."""
    # Repeat the original safety checks on the same bounded, immutable bytes.
    value = sources._safe_text(body)
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)", value, re.I):
        return _OPAQUE
    encoding = re.search(r"""<\?xml[^>]*encoding\s*=\s*['"]([^'"]+)""", value, re.I)
    if encoding and encoding[1].lower() not in {"utf-8", "utf8", "us-ascii"}:
        return _OPAQUE
    root = ET.fromstring(value)
    if root.tag != "rss" or root.get("version", "2.0") != "2.0" or len(root) != 1 or root[0].tag != "channel":
        return _OPAQUE
    channel = root[0]
    item_count = len(channel.findall("item"))
    if item_count > probe._MAX_ITEMS or sum(1 for _ in root.iter()) > 5000:
        return "channel_limits"
    if sum(probe._tag(node.tag) == "item" for node in root.iter()) != item_count:
        return "channel_layout"
    links = channel.findall("link")
    if len(links) > 1 or (links and (len(links[0]) or links[0].text not in probe._CHANNEL_URLS)):
        return "channel_identity"
    if not probe._board_only(root) or not probe._board_only(channel):
        return "channel_attribution"
    return _OPAQUE


def _diagnose(response, captured):
    if (type(response) is not sources.Response or type(response.content_type) is not str
            or type(response.body) is not bytes or type(response.status) is not int
            or type(captured) is not datetime or type(captured.tzinfo) is not timezone):
        return {"reason": "invalid_envelope"}
    try:
        response.validate()
    except Invalid as error:
        code = _known_code(error)
        if 100 <= response.status <= 599 and code == f"http_status_{response.status}":
            return {"reason": "response_http"}
        return {"reason": _RESPONSE_REASONS.get(code, _OPAQUE)}

    try:
        counts = probe._parse_counts(response.body, captured.astimezone(UTC))
    except ET.ParseError:
        return {"reason": "malformed_xml"}
    except Invalid as error:
        code = _known_code(error)
        return {"reason": (_channel_reason(response.body) if code == "invalid_channel"
                           else _PARSE_REASONS.get(code, _OPAQUE))}
    if (type(counts) is not dict or len(counts) != len(_COUNT_KEYS)
            or any(type(key) is not str or type(count) is not int or not 0 <= count <= probe._MAX_ITEMS
                   for key, count in counts.items())):
        return {"reason": _OPAQUE}
    return {"reason": "parsed", "counts": {key: counts[key] for key in _COUNT_KEYS}}


def _safe_diagnosis(response, captured):
    try:
        return _diagnose(response, captured)
    except Exception:
        # Unknown validation/parser failures must not expose details.
        return {"reason": _OPAQUE}


def diagnose_response(response, captured):
    """Diagnose a Response at an explicit fixed-offset datetime without I/O."""
    return _METADATA | _safe_diagnosis(response, captured)


def _authored_item(key, published, *, title="Authored example", extra=""):
    link = f"https://{sources.FED_HOST}/newsevents/pressreleases/diagnostic19000101{key}.htm"
    return (f"<item><title>{title}</title><link>{link}</link>"
            f"<pubDate>{format_datetime(published, usegmt=True)}</pubDate>{extra}</item>")


def _authored_feed(content=""):
    return f'<rss version="2.0"><channel>{content}</channel></rss>'.encode("ascii")


def demo():
    """Present fixed authored fixtures, not observations."""
    captured = datetime(2000, 1, 1, 12, tzinfo=UTC)
    today = _authored_item("a", captured)
    mixed = _authored_feed(
        today + today
        + _authored_item("b", captured - timedelta(days=32))
        + _authored_item("c", captured + timedelta(seconds=1))
        + _authored_item("d", captured)
        + _authored_item("d", captured, title="Different authored title")
        + _authored_item("e", captured, extra="<author>Not the Board</author>")
        + "<item/>"
    )
    rss = "application/rss+xml"
    fixtures = {
        "aggregates": sources.Response(mixed, rss),
        "envelope": None,
        "http": sources.Response(b"", rss, 503),
        "media": sources.Response(b"", "text/plain"),
        "size": sources.Response(b"x" * (sources.MAX_BYTES + 1), rss),
        "encoding": sources.Response(b"\xff", rss),
        "unallowed_xml": sources.Response(b"<!DOCTYPE rss>" + _authored_feed(), rss),
        "malformed": sources.Response(b"<rss>", rss),
        "root": sources.Response(b"<feed/>", rss),
        "limits": sources.Response(_authored_feed("<item/>" * (probe._MAX_ITEMS + 1)), rss),
        "layout": sources.Response(_authored_feed("<wrapper><item/></wrapper>"), rss),
        "identity": sources.Response(_authored_feed("<link>Not a Board locator</link>"), rss),
        "attribution": sources.Response(_authored_feed("<publisher>Not the Board</publisher>"), rss),
    }
    return _METADATA | {
        "fixture_origin": "authored",
        "cases": {label: _safe_diagnosis(response, captured) for label, response in fixtures.items()},
    }


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv != ["demo"]:
        print("offline_demo_only", file=sys.stderr)
        return 2
    print(json.dumps(demo(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
