"""Caller-supplied rights specifications, never legal or runtime authorization.

There are no source defaults and no dispatch implementations. URL validation is
deliberately a narrow canonical HTTPS subset, not a URL normalization service.
"""

import json
import math
import re
from urllib.parse import parse_qsl, quote, urlencode, urlsplit

from .models import Invalid, RETENTIONS, canonical, digest, exact, integer, text

RIGHTS = ("automated_retrieval", "raw_retention", "normalized_retention",
          "derived_analysis", "external_model_input", "private_delivery",
          "public_redistribution")
PURPOSE = "personal_local_research"
OPERATIONS = {right: (right,) for right in RIGHTS} | {
    "local_composition": ("normalized_retention", "derived_analysis")}
MAX_POLICY_BYTES = 65536


def bounded_copy(value, limit=512000):
    """Detach strictly JSON-shaped data without coercing tuples or object keys."""
    pending, nodes = [(value, 0)], 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if nodes > 30000 or depth > 16:
            raise Invalid("JSON structure bound exceeded")
        if type(item) is dict:
            if len(item) > 1024 or any(type(k) is not str or len(k) > 2048 for k in item):
                raise Invalid("invalid JSON object")
            pending.extend((v, depth + 1) for v in item.values())
        elif type(item) is list:
            if len(item) > 4096:
                raise Invalid("JSON array bound exceeded")
            pending.extend((v, depth + 1) for v in item)
        elif type(item) is str:
            if len(item) > limit:
                raise Invalid("JSON string bound exceeded")
        elif type(item) in (int, float):
            if abs(item) > 1e30 or not math.isfinite(item):
                raise Invalid("JSON number bound exceeded")
        elif item is not None and type(item) is not bool:
            raise Invalid("JSON value required")
    body = canonical(value)
    if len(body.encode("utf-8")) > limit:
        raise Invalid("JSON byte limit")
    return json.loads(body)


def identifier(value, name, limit=100):
    text(value, name, limit)
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]*", value) is None:
        raise Invalid("invalid " + name + " identity")
    return value


def https_location(value, *, prefix=False, origin=False):
    text(value, "HTTPS locator", 2048)
    if (not value.isascii() or any(c.isspace() for c in value)
            or any(c in value for c in "\\%?#@")):
        raise Invalid("noncanonical HTTPS locator")
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        if (parsed.scheme != "https" or host is None or parsed.netloc != host
                or len(host) > 253 or "." not in host
                or any(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) is None
                       for label in host.split("."))):
            raise Invalid("invalid HTTPS origin")
    except ValueError as exc:
        raise Invalid("invalid HTTPS origin") from exc
    path = parsed.path
    if origin:
        if path or value != "https://" + host:
            raise Invalid("exact HTTPS origin required")
    elif (not path.startswith("/") or "//" in path
          or re.fullmatch(r"/[A-Za-z0-9/._~-]*", path) is None
          or any(part in {".", ".."} for part in path.split("/"))):
        raise Invalid("noncanonical HTTPS path")
    if prefix and (path == "/" or not path.endswith("/")):
        raise Invalid("bounded directory prefix required")
    return "https://" + host, path


def location_matches(value, prefix):
    origin, path = https_location(value)
    expected_origin, directory = https_location(prefix, prefix=True)
    return origin == expected_origin and path.startswith(directory)


def basis_reference(value):
    """A document reference may use another licensor host and bounded queries.

    This does not authorize its origin as an evidence location or fetch anything.
    """
    text(value, "basis reference", 2048)
    if not value.isascii() or any(c.isspace() for c in value) or "#" in value:
        raise Invalid("noncanonical basis reference")
    try:
        parsed = urlsplit(value)
        https_location(parsed._replace(query="").geturl())
        if "?" in value:
            if not parsed.query or len(parsed.query) > 512:
                raise Invalid("basis query bound")
            pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True,
                              max_num_fields=8, errors="strict")
            if (len({key for key, _ in pairs}) != len(pairs)
                    or any(re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,63}", key) is None
                           or re.fullmatch(r"[A-Za-z0-9._~/-]{1,256}", val) is None for key, val in pairs)
                    or urlencode(pairs, quote_via=quote) != parsed.query):
                raise Invalid("noncanonical basis query")
    except (ValueError, UnicodeError) as exc:
        raise Invalid("invalid basis reference") from exc
    return value


def _refs(values, minimum):
    if type(values) is not list or not minimum <= len(values) <= 8:
        raise Invalid("invalid source-located basis references")
    seen = set()
    for value in values:
        basis_reference(value)
        if value in seen:
            raise Invalid("duplicate source-located basis reference")
        seen.add(value)


def validate_source_policy(value):
    """Validate and detach an unverified, operation-scoped input specification."""
    data = bounded_copy(value, MAX_POLICY_BYTES)
    exact(data, {"schema_version", "version", "sources"})
    integer(data["schema_version"], 1, 1)
    identifier(data["version"], "policy version")
    if type(data["sources"]) is not list or len(data["sources"]) > 8:
        raise Invalid("invalid source policy array")
    seen = set()
    for row in data["sources"]:
        exact(row, {"source_id", "synthetic", "purpose", "origin", "locator_prefix",
                    "retention", "rights"})
        identity = identifier(row["source_id"], "source", 80)
        if identity in seen or row["synthetic"] is not False or identity.startswith("synthetic"):
            raise Invalid("invalid real source identity")
        seen.add(identity)
        if row["purpose"] != PURPOSE:
            raise Invalid("unsupported source policy purpose")
        origin = https_location(row["origin"], origin=True)[0]
        if (https_location(row["locator_prefix"], prefix=True)[0] != origin
                or origin == "https://example.invalid"):
            raise Invalid("source origin/prefix mismatch or synthetic registry")
        retention = row["retention"]
        exact(retention, {"class", "max_age_seconds", "basis_refs"})
        if type(retention["class"]) is not str or retention["class"] not in RETENTIONS | {"normalized_only"}:
            raise Invalid("unknown retention class")
        integer(retention["max_age_seconds"], 0, 31536000)
        if retention["class"] in {"metadata_only", "restricted"}:
            if retention["max_age_seconds"] != 0:
                raise Invalid("restricted payload cannot have a retention window")
        elif retention["max_age_seconds"] == 0:
            raise Invalid("payload retention duration required")
        if retention["class"] == "short_term_raw" and retention["max_age_seconds"] > 86400:
            raise Invalid("short term retention exceeds 24 hours")
        if retention["class"] == "normalized_only" and retention["max_age_seconds"] > 86400:
            raise Invalid("normalized retention exceeds 24 hours")
        _refs(retention["basis_refs"], 1)
        exact(row["rights"], RIGHTS)
        for name in RIGHTS:
            right = row["rights"][name]
            exact(right, {"state", "basis_refs"})
            if type(right["state"]) is not str or right["state"] not in {"permitted", "denied", "unknown"}:
                raise Invalid("unknown rights state")
            _refs(right["basis_refs"], int(right["state"] != "unknown"))
            if (retention["class"] == "normalized_only" and name == "raw_retention"
                    and right["state"] != "denied"):
                raise Invalid("normalized-only retention must deny raw retention")
            if (name in {"raw_retention", "normalized_retention"}
                    and right["state"] == "permitted"
                    and retention["class"] in {"metadata_only", "restricted"}):
                raise Invalid("contradictory payload retention permission")
    return data


def assess_source_policy(policy, source_id, operation, purpose):
    """Assess specified rights only; even a permitted result enables nothing."""
    data = validate_source_policy(policy)
    identifier(source_id, "source", 80)
    if type(operation) is not str or operation not in OPERATIONS or purpose != PURPOSE:
        raise Invalid("unsupported operation or purpose")
    row = next((r for r in data["sources"] if r["source_id"] == source_id), None)
    if row is None:
        raise Invalid("unknown source policy identity")
    applicable = {name: row["rights"][name] for name in OPERATIONS[operation]}
    states = {right["state"] for right in applicable.values()}
    state = "denied" if "denied" in states else ("unknown" if "unknown" in states else "permitted")
    return {"source_id": source_id, "operation": operation, "purpose": purpose,
            "state": state, "applicable_rights": applicable, "policy_hash": digest(data),
            "assessment": "caller_specification_only", "permission_grant": False,
            "operation_enabled": False, "legal_entitlement_verified": False,
            "acquisition_verified": False}
