"""Trusted synchronous offline transports only; zero live integration."""

import json
import re
import time

from .models import Invalid, canonical, digest, exact, instant, integer, stamp
from .ledger import DeliveryBlocked, Outcome, Reservation, report_status, run_identity, validate_ack


class UnavailableLiveAdapter:
    mode = "unavailable_live"

    def send(self, request):
        raise Invalid("real_telegram_not_authorized_or_integrated")


class FakeTransport:
    mode = "synthetic_test"

    def __init__(self, *, fail_at=None, malformed_at=None):
        self.calls = 0
        self.fail_at, self.malformed_at = fail_at, malformed_at

    def send(self, request):
        self.calls += 1
        if self.calls == self.malformed_at:
            return None
        r = request["reservation"]
        failed = self.calls == self.fail_at
        return {"label": "SIMULATED ACK ONLY", "reservation_id": digest(r), "chunk_id": r["chunk_id"],
                "report_id": r["report_id"], "sink": r["sink"], "status": "failed" if failed else "sent",
                "received_chars": 0 if failed else r["chars"], "not_sent": failed}


def deliver(ledger, report_id, transport):
    if getattr(transport, "mode", None) != "synthetic_test":
        raise Invalid("offline_fake_transport_required")
    refusal = None
    while True:
        try:
            request = ledger.reserve(report_id)
        except DeliveryBlocked as exc:
            # Refusals are recorded in this result, not as fictional send attempts.
            refusal = str(exc)
            break
        if request is None:
            break
        # The committed reservation is independent of the object handed to test code.
        detached = json.loads(canonical(request))
        started = time.monotonic()
        try:
            ack = transport.send(detached)
        except Exception:
            ledger.settle(request, reason="adapter_exception")
            break
        elapsed = time.monotonic() - started
        if not 0 <= elapsed <= ledger.config["timeout_seconds"]:
            ledger.settle(request, reason="timeout")
            break
        if ledger.settle(request, ack) != "sent":
            break
    return ledger.snapshot(report_id, refusal)


def verify_snapshot(snapshot, report, start, finish, statuses, *, first_attempt=1):
    """Validate compact actual fake receipts without rerunning delivery or clocks."""
    exact(snapshot, {"label", "report_id", "run_id", "sink", "status", "reason", "chunks"})
    if (snapshot["label"] != "SIMULATED DELIVERY ONLY" or snapshot["report_id"] != report["report_id"]
            or snapshot["sink"] != "synthetic-local-sink"
            or snapshot["run_id"] != run_identity(report["report_id"], snapshot["sink"])
            or type(snapshot["chunks"]) is not list or len(snapshot["chunks"]) != len(report["chunks"])
            or len(statuses) != len(report["chunks"])):
        raise Invalid("invalid snapshot binding")
    previous, owner = start, None
    for row, chunk, expected_status in zip(snapshot["chunks"], report["chunks"], statuses):
        exact(row, {"chunk_id", "ordinal", "chars", "status", "reservation", "outcome"})
        integer(row["ordinal"], chunk["ordinal"], chunk["ordinal"])
        integer(row["chars"], len(chunk["text"]), len(chunk["text"]))
        if row["chunk_id"] != chunk["chunk_id"] or row["status"] != expected_status:
            raise Invalid("invalid snapshot sequence")
        if expected_status == "queued":
            if row["reservation"] is not None or row["outcome"] is not None:
                raise Invalid("queued chunk has dispatch history")
            continue
        r, o = row["reservation"], row["outcome"]
        exact(r, Reservation.__dataclass_fields__)
        exact(o, Outcome.__dataclass_fields__)
        at, settled = instant(r["reserved_at"]), instant(o["finished_at"])
        if owner is None:
            owner = r["owner"]
        integer(r["attempt_id"], first_attempt, first_attempt)
        integer(o["attempt_id"], first_attempt, first_attempt)
        first_attempt += 1
        integer(r["chars"], row["chars"], row["chars"])
        if (r["chunk_id"] != chunk["chunk_id"] or r["report_id"] != report["report_id"]
                or r["run_id"] != snapshot["run_id"] or r["sink"] != snapshot["sink"]
                or type(owner) is not str or re.fullmatch(r"[0-9a-f]{32}", owner) is None or r["owner"] != owner
                or r["day"] != stamp(at)[:10] or r["reserved_at"] != stamp(at)
                or o["finished_at"] != stamp(settled) or not previous <= at <= settled <= finish
                or at < instant(report["content"]["source"]["generated_at"]) or o["status"] != expected_status):
            raise Invalid("invalid snapshot execution metadata")
        if expected_status in ("sent", "failed"):
            if (validate_ack(o["acknowledgement"], r) != expected_status
                    or o["reason"] != "bound_fake_" + expected_status or (settled - at).total_seconds() > 5):
                raise Invalid("invalid snapshot acknowledgement")
        elif o["reason"] != "invalid_ack" or o["acknowledgement"] is not None:
            raise Invalid("invalid demo ambiguity exercise")
        previous = settled
    status, reason = report_status(snapshot["chunks"])
    if snapshot["status"] != status or snapshot["reason"] != reason:
        raise Invalid("invalid report-level delivery status")
    return True
