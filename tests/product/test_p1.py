"""Offline P1 acceptance: sockets and DNS are prohibited for this whole suite."""

from contextlib import ExitStack, closing, redirect_stdout
from dataclasses import replace
from datetime import timedelta
from email.utils import format_datetime
import copy
from concurrent.futures import ThreadPoolExecutor
import http.client
import io
import json
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from market_intelligence import gate, models, runtime, sources, store

ROOT = Path(__file__).resolve().parents[2]
FEED = (ROOT / "tests/fixtures/p1_feed.xml").read_bytes()
CSV = (ROOT / "tests/fixtures/p1_market.csv").read_bytes()
NETWORK = None


def forbidden(*args, **kwargs):
    raise AssertionError("OFFLINE TEST ATTEMPTED NETWORK OR DNS")


def setUpModule():
    global NETWORK
    NETWORK = ExitStack()
    for name in ("socket.socket", "socket.create_connection", "socket.getaddrinfo",
                 "http.client.HTTPConnection.request", "urllib.request.urlopen"):
        NETWORK.enter_context(patch(name, side_effect=forbidden))


def tearDownModule():
    NETWORK.close()


def capture(body=FEED, parser="rss-v1", **changes):
    now = models.stamp(models.utc_now())
    item = models.Capture("synthetic_" + parser, "https://example.invalid/synthetic",
                          now, now, None, models.digest(body), models.digest(body),
                          "text/csv" if parser == "market-csv-v1" else "application/rss+xml",
                          parser, "reprocessable_raw", "Independently authored SYNTHETIC fixture",
                          None, "synthetic_test", "fixture-test")
    return replace(item, **changes)


def current_feed(title="SYNTHETIC current observation", date=None):
    date = date or format_datetime(models.utc_now() - timedelta(hours=1))
    return ("<rss><channel><item><title>" + title + "</title><link>https://example.invalid/item</link>"
            "<pubDate>" + date + "</pubDate></item></channel></rss>").encode()


def records():
    return list(sources.parse(FEED, capture()).records) + list(sources.parse(CSV, capture(CSV, "market-csv-v1")).records)


def evaluate(items=None, profile=None, calendar=None, as_of=None):
    return gate.evaluate(records() if items is None else items,
                         profile or runtime.profile_for("synthetic_test", True),
                         calendar or runtime.synthetic_calendar(), as_of or models.stamp(models.utc_now()))


class BoundaryTests(unittest.TestCase):
    def test_schema_round_trip_and_unknown_fields(self):
        for item in (capture(), records()[0]):
            self.assertEqual(type(item).from_dict(item.to_dict()), item)
            bad = item.to_dict() | {"instruction": "execute"}
            with self.assertRaises(models.Invalid):
                type(item).from_dict(bad)

    def test_aware_time_and_future_capture(self):
        for value in ("2020-01-02T12:00:00", "2020-01-02", "not-a-date", True):
            with self.subTest(value=value), self.assertRaises(models.Invalid):
                models.instant(value)
        future = models.stamp(models.utc_now() + timedelta(hours=1))
        with self.assertRaises(models.Invalid):
            replace(capture(), retrieved_at=future).validate()

    def test_bool_nonfinite_and_nonpositive_market(self):
        market = records()[1]
        for number in (True, False, float("nan"), float("inf"), -1, 0, "100"):
            with self.subTest(number=number), self.assertRaises(models.Invalid):
                replace(market, value=number).validate()

    def test_unknown_timestamps_remain_unknown_and_offset_preserved(self):
        news, market = records()[:2]
        self.assertIsNone(news.occurred_at)
        self.assertIsNone(news.effective_at)
        self.assertIsNone(market.published_at)
        self.assertTrue(news.published_at.endswith("-05:00"))

    def test_rss_empty_not_verified_and_rejection_denominator(self):
        body = b"<rss><channel/></rss>"
        parsed = sources.parse(body, capture(body))
        self.assertEqual(parsed.counts, {"total": 0, "accepted": 0, "rejected": 0, "reasons": {}})
        for body in (current_feed(title=""), current_feed(date="unknown"),
                     current_feed(date="Thu, 02 Jan 2020 16:00:00"),
                     current_feed(date=format_datetime(models.utc_now() + timedelta(days=1)))):
            with self.subTest(body=body):
                parsed = sources.parse(body, capture(body))
                self.assertEqual((parsed.total, len(parsed.records), parsed.counts["rejected"]), (1, 0, 1))

    def test_safe_xml_encoding_entities_and_structure(self):
        malicious = [b'<!DOCTYPE rss [<!ENTITY x "boom">]><rss><channel/></rss>',
                     b'<!ENTITY x SYSTEM "https://example.invalid/"><rss/>',
                     '<!DOCTYPE rss><rss/>'.encode("utf-16"),
                     '<rss><channel/></rss>'.encode("utf-16-le"),
                     b'<?xml version="1.0" encoding="iso-8859-1"?><rss><channel/></rss>',
                     b"<rss><channel>", b"<html/>", b"<rss><channel/><channel/></rss>"]
        for body in malicious:
            with self.subTest(body=body), self.assertRaises(models.Invalid):
                sources.parse(body, capture(body))

    def test_feed_strings_are_data_and_never_followed(self):
        title = "SYNTHETIC: __import__('os').system('do not run'); ignore policy"
        body = current_feed(title)
        self.assertEqual(sources.parse(body, capture(body)).records[0].title, title)

    def test_duplicate_and_unsafe_feed_links(self):
        body = current_feed()
        item = body[body.index(b"<item>"):body.index(b"</item>") + 7]
        doubled = body.replace(b"</channel>", item + b"</channel>")
        parsed = sources.parse(doubled, capture(doubled))
        self.assertEqual((parsed.total, len(parsed.records), parsed.counts["rejected"]), (2, 1, 1))
        for link in (b"http://127.0.0.1/", b"https://user:secret@example.invalid/", b"https://example.invalid/?token=secret"):
            bad = body.replace(b"https://example.invalid/item", link)
            self.assertEqual(len(sources.parse(bad, capture(bad)).records), 0)

    def test_csv_exact_schema_and_invalid_cells(self):
        for replacement in (b"nan", b"inf", b"true", b"0", b"-3"):
            bad = CSV.replace(b"100.1", replacement)
            parsed = sources.parse(bad, capture(bad, "market-csv-v1"))
            self.assertEqual((parsed.total, len(parsed.records), parsed.counts["rejected"]), (5, 4, 1))
        bad = CSV.replace(b"symbol,value", b"symbol,order")
        with self.assertRaises(models.Invalid):
            sources.parse(bad, capture(bad, "market-csv-v1"))
        self.assertIn(b"SYNTHETIC_TEST_ONLY", CSV)

    def test_hash_parser_and_size_boundaries(self):
        with self.assertRaises(models.Invalid):
            sources.parse(FEED + b" ", capture())
        with self.assertRaises(models.Invalid):
            sources.parse_rss(FEED, capture(parser="market-csv-v1"))
        huge = b" " * (sources.MAX_BYTES + 1)
        with self.assertRaises(models.Invalid):
            sources.parse(huge, capture(huge))


class GateTests(unittest.TestCase):
    def test_synthetic_pass_and_live_market_gap(self):
        self.assertEqual(evaluate()["status"], "PASS")
        self.assertEqual(evaluate(items=records()[:1])["domains"]["market"]["covered"], 0)
        self.assertEqual(evaluate(items=records()[:1])["status"], "BLOCK")

    def test_wrong_modes_captured_or_seen_after_asof(self):
        items = records()
        for mode in ("live_observation", "historical_replay"):
            self.assertEqual(evaluate(items, runtime.profile_for(mode, True))["status"], "BLOCK")
        prior = models.stamp(models.utc_now() - timedelta(seconds=1))
        result = evaluate(items, as_of=prior)
        self.assertEqual(result["status"], "BLOCK")
        self.assertTrue(any("after as_of" in r["reason"] for r in result["rejected"]))

    def test_naive_future_and_unknown_record_fields_block(self):
        for update in ({"published_at": "2020-01-02T12:00:00"},
                       {"retrieved_at": models.stamp(models.utc_now() + timedelta(days=1))},
                       {"domain": "event"}, {"unknown": True}):
            item = records()[0].to_dict() | update
            self.assertEqual(evaluate([item, *records()[1:]])["status"], "BLOCK")

    def test_calendar_missing_wrong_session_close_or_identity(self):
        items = records()
        empty = gate.Calendar("no-sessions-v1", ())
        self.assertEqual(evaluate(items, calendar=empty)["status"], "BLOCK")
        for change in ({"calendar_id": "UNKNOWN"}, {"session_id": "2020-01-03"},
                       {"close_at": "2020-01-02T14:00:00+08:00", "observed_at": "2020-01-02T14:00:00+08:00"}):
            altered = [items[0], replace(items[1], **change), *items[2:]]
            self.assertEqual(evaluate(altered)["status"], "BLOCK")
        with self.assertRaises(models.Invalid):
            gate.Session("SSE", "Asia/Shanghai", "2020-01-02", "2020-01-02T15:00:00Z").validate()

    def test_explicit_early_close_not_weekday_inference(self):
        session = gate.Session("US_CASH", "America/New_York", "2020-01-04", "2020-01-04T13:00:00-05:00")
        calendar = gate.Calendar("synthetic-exception-v1", (session,)).validate()
        self.assertEqual(calendar.latest("US_CASH", models.utc_now()), session)
        with self.assertRaises(models.Invalid):
            calendar.latest("SSE", models.utc_now())

    def test_stale_expired_conflicting_and_tier_c(self):
        items = records()
        strict = runtime.profile_for("synthetic_test")
        self.assertEqual(evaluate(items, strict)["status"], "BLOCK")
        past = models.stamp(models.utc_now() - timedelta(seconds=1))
        # Capture time predates expiry, but current reads/gates must reject it.
        old = models.stamp(models.utc_now() - timedelta(hours=2))
        expired = replace(items[0], retrieved_at=old, first_seen_at=old, expires_at=past)
        self.assertEqual(evaluate([expired, *items[1:]])["status"], "BLOCK")
        conflict = replace(items[1], value=999)
        result = evaluate(items + [conflict])
        self.assertEqual(result["status"], "BLOCK")
        self.assertNotIn(conflict.evidence_id, result["accepted_ids"])
        self.assertNotIn(items[1].evidence_id, result["accepted_ids"])
        self.assertEqual(evaluate([replace(r, tier="C") for r in items])["status"], "BLOCK")

    def test_duplicate_rejected_reference_not_frozen(self):
        items = records()
        result = evaluate(items + [items[0]])
        self.assertEqual(result["status"], "BLOCK")
        self.assertNotIn(items[0].evidence_id, result["accepted_ids"])
        gate.validate_result(result)

    def test_unknown_validator_profile_fields_and_universe(self):
        profile = runtime.profile_for("synthetic_test", True)
        bad = replace(profile, rules=(replace(profile.rules[0], validator="event-v1"), profile.rules[1]))
        self.assertEqual(evaluate(profile=bad)["status"], "BLOCK")
        for change in ({"universe": ["000001.SH"]}, {"mystery": True}):
            with self.assertRaises(models.Invalid):
                gate.Profile.from_dict(profile.to_dict() | change)
        with self.assertRaises(models.Invalid):
            replace(profile.rules[0], minimum_coverage=0.6).validate()
        with self.assertRaises(models.Invalid):
            replace(profile.rules[1], domain="event").validate()

    def test_optional_missing_only_explicitly_degrades(self):
        profile = runtime.profile_for("synthetic_test", True)
        optional = replace(profile, rules=(profile.rules[0], replace(profile.rules[1], required=False, allow_degraded=True)))
        self.assertEqual(evaluate(records()[1:], optional)["status"], "DEGRADED")
        prohibited = replace(optional, rules=(optional.rules[0], replace(optional.rules[1], allow_degraded=False)))
        self.assertEqual(evaluate(records()[1:], prohibited)["status"], "BLOCK")


class TempStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="p1-offline-")
        self.path = Path(self.temp.name) / "evidence.sqlite3"
        self.store = store.Store(self.path)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def begin(self, key="test", now=None):
        return self.store.begin(key, "synthetic_test", now)[0]

    def save(self, lease, body=FEED, parser="rss-v1", **changes):
        cap = capture(body, parser, **changes)
        parsed = sources.parse(body, cap)
        self.store.put_capture(cap, body, parsed.records, lease=lease)
        return cap, parsed

    def test_global_concurrency_across_connections_and_task_keys(self):
        lease = self.begin("task-a")
        with store.Store(self.path) as other:
            with self.assertRaises(store.Busy):
                other.begin("task-b", "synthetic_test")
        self.store.release(lease)
        self.assertIsNotNone(self.begin("task-b"))

    def test_simultaneous_different_tasks_only_one_lease(self):
        barrier = threading.Barrier(2)
        def acquire(key):
            with store.Store(self.path) as connection:
                barrier.wait(timeout=5)
                try:
                    connection.begin(key, "synthetic_test")
                    return "acquired"
                except store.Busy:
                    return "busy"
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(acquire, key) for key in ("thread-a", "thread-b")]
            self.assertEqual(sorted(f.result(timeout=10) for f in futures), ["acquired", "busy"])

    def test_stale_owner_cannot_renew_release_reserve_or_write(self):
        old_time = models.utc_now() - timedelta(minutes=1)
        old = self.begin("old", old_time)
        new = self.begin("new")
        for action in (lambda: self.store.renew(old), lambda: self.store.release(old),
                       lambda: self.store.reserve(old), lambda: self.save(old)):
            with self.subTest(action=action), self.assertRaises(store.Busy):
                action()
        self.store.renew(new)
        self.assertEqual(self.store.run(old.run_id)["status"], "interrupted")
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM captures").fetchone()[0], 0)

    def test_interrupted_without_request_resumes_same_run(self):
        old = self.begin("resume", models.utc_now() - timedelta(minutes=1))
        new = self.begin("resume")
        self.assertEqual(old.run_id, new.run_id)
        self.assertGreater(new.generation, old.generation)
        self.assertIsNotNone(self.store.reserve(new))

    def test_unknown_usage_blocks_different_task_after_reopen(self):
        earlier = models.utc_now() - timedelta(minutes=1)
        old = self.begin("crash", earlier)
        self.store.reserve(old, earlier)
        new = self.begin("other")
        with self.assertRaisesRegex(store.BudgetDenied, "unknown_usage"):
            self.store.reserve(new)
        self.store.release(new)
        with store.Store(self.path) as reopened:
            newer, _ = reopened.begin("third", "synthetic_test")
            with self.assertRaisesRegex(store.BudgetDenied, "unknown_usage"):
                reopened.reserve(newer)
            self.assertEqual(reopened.attempts(old.run_id)[0]["outcome"], "unknown")
            self.assertEqual(reopened.attempts(old.run_id)[0]["reserved_bytes"], 512000)
            self.assertIsNone(reopened.attempts(old.run_id)[0]["observed_bytes"])
            reopened.release(newer)

    def test_per_run_and_daily_reservations_include_failures(self):
        for run_number in range(6):
            lease = self.begin("budget-" + str(run_number))
            for _ in range(2):
                attempt = self.store.reserve(lease)
                self.store.settle(lease, attempt, "failed", 0, "http_status_503")
            with self.assertRaisesRegex(store.BudgetDenied, "budget_exhausted"):
                self.store.reserve(lease)
            self.store.release(lease)
        last = self.begin("daily-denied")
        with self.assertRaisesRegex(store.BudgetDenied, "budget_exhausted"):
            self.store.reserve(last)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM attempts").fetchone()[0], 12)

    def test_attempt_settlement_cannot_change_or_refund(self):
        lease = self.begin()
        attempt = self.store.reserve(lease)
        self.store.settle(lease, attempt, "unknown", None, "timeout")
        with self.assertRaises(models.Invalid):
            self.store.settle(lease, attempt, "success", 20, "late")
        with self.assertRaises(store.BudgetDenied):
            self.store.reserve(lease)

    def test_capture_round_trip_replay_and_hash_version_checks(self):
        lease = self.begin()
        cap, parsed = self.save(lease)
        self.assertEqual(self.store.capture(cap.version_id), cap)
        replayed = self.store.replay(cap.version_id, "rss-v1", lease=lease)
        self.assertEqual(replayed.records[0].mode, "historical_replay")
        self.assertEqual(replayed.records[0].first_seen_at, parsed.records[0].first_seen_at)
        with self.assertRaises(models.Invalid):
            self.store.replay(cap.version_id, "rss-v2", lease=lease)
        self.store.db.execute("UPDATE captures SET payload=? WHERE capture_id=?", (b"corrupt", cap.version_id))
        with self.assertRaises(models.Invalid):
            self.store.replay(cap.version_id, "rss-v1", lease=lease)

    def test_restricted_and_metadata_only_enforced_before_write(self):
        lease = self.begin()
        for retention in ("restricted", "metadata_only"):
            cap = capture(retention=retention, source_id="synthetic_" + retention)
            with self.assertRaises(models.Invalid):
                self.store.put_capture(cap, FEED, lease=lease)
            self.store.put_capture(cap, lease=lease)
            with self.assertRaises(models.Invalid):
                self.store.replay(cap.version_id, "rss-v1", lease=lease)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM captures WHERE payload IS NOT NULL").fetchone()[0], 0)

    def test_expiry_before_write_read_replay_sweep_and_tombstone(self):
        lease = self.begin()
        now = models.utc_now()
        cap, parsed = self.save(lease, retention="short_term_raw", expires_at=models.stamp(now + timedelta(minutes=5)))
        future = now + timedelta(minutes=6)
        found, missing = self.store.records([parsed.records[0].evidence_id], future)
        self.assertFalse(found)
        self.assertEqual(len(missing), 1)
        with self.assertRaises(models.Invalid):
            self.store.replay(cap.version_id, "rss-v1", future, lease=lease)
        expired = replace(cap, retrieved_at=models.stamp(now - timedelta(hours=2)),
                          first_seen_at=models.stamp(now - timedelta(hours=2)), expires_at=models.stamp(now - timedelta(hours=1)))
        with self.assertRaises(models.Invalid):
            self.store.put_capture(expired, FEED, lease=lease)
        self.store.release(lease)
        self.assertEqual(self.store.sweep(future), [cap.version_id])
        self.assertEqual(self.store.sweep(future), [])
        tombstone = dict(self.store.db.execute("SELECT * FROM tombstones").fetchone())
        self.assertEqual(tombstone["content_hash"], cap.content_hash)
        self.assertEqual(tombstone["parser_version"], "rss-v1")
        self.assertIsNone(self.store.db.execute("SELECT payload FROM captures").fetchone()[0])

    def test_first_seen_cannot_be_backdated_or_reset(self):
        lease = self.begin()
        cap, _ = self.save(lease)
        later = capture()
        with self.assertRaises(models.Invalid):
            self.store.put_capture(later, FEED, lease=lease)
        preserved = replace(later, first_seen_at=cap.first_seen_at)
        self.store.put_capture(preserved, FEED, lease=lease)

    def test_strict_stored_record_read(self):
        lease = self.begin()
        cap, parsed = self.save(lease)
        record = parsed.records[0].to_dict() | {"extra": "not permitted"}
        self.store.db.execute("UPDATE evidence SET record=?", (models.canonical(record),))
        with self.assertRaises(models.Invalid):
            self.store.records()
        metadata = cap.to_dict() | {"extra": 1}
        self.store.db.execute("UPDATE captures SET metadata=?", (models.canonical(metadata),))
        with self.assertRaises(models.Invalid):
            self.store.capture(cap.version_id)

    def test_frozen_bundles_are_deterministic_references_not_clones(self):
        result = runtime.demo(self.store, "demo")
        bundle_id = result["bundle_id"]
        saved = self.store.bundle(bundle_id)
        self.assertTrue(saved["usable_now"])
        raw = models.canonical(saved["bundle"])
        self.assertNotIn("Example research announcement", raw)
        self.assertNotIn("100.1", raw)
        lease = self.begin("refreeze")
        self.assertEqual(self.store.freeze(saved["bundle"]["gate"], lease=lease), bundle_id)
        forged = copy.deepcopy(saved["bundle"]["gate"])
        forged["domains"]["market"]["expected"] = 1
        with self.assertRaises(models.Invalid):
            self.store.freeze(forged, lease=lease)
        stale = replace(lease, generation=lease.generation - 1)
        with self.assertRaises(store.Busy):
            self.store.freeze(saved["bundle"]["gate"], lease=stale)

    def optional_news_case(self, max_age=1):
        runtime.demo(self.store, "demo")
        items, unavailable = self.store.records()
        self.assertFalse(unavailable)
        profile = runtime.profile_for("synthetic_test", True)
        return items, replace(profile, rules=(profile.rules[0], replace(
            profile.rules[1], required=False, allow_degraded=True, max_age_seconds=max_age)))

    def test_freeze_optional_stale_preserves_rejections_not_usable_refs(self):
        items, profile = self.optional_news_case()
        result = evaluate(items, profile)
        self.assertEqual(result["status"], "DEGRADED")
        self.assertIn("stale evidence", result["domains"]["news"]["reasons"])
        lease = self.begin("freeze-degraded")
        identity = self.store.freeze(result, lease=lease)
        self.assertEqual(self.store.freeze(result, lease=lease), identity)
        self.store.release(lease)
        with store.Store(self.path) as reopened:
            saved = reopened.bundle(identity)
        self.assertTrue(saved["usable_now"])
        self.assertEqual(saved["bundle"]["gate"], result)
        refs = [r["evidence_id"] for r in saved["bundle"]["references"]]
        self.assertEqual(refs, result["accepted_ids"])
        self.assertEqual(len(refs), 5)
        self.assertTrue(set(refs).isdisjoint(r["evidence_id"] for r in result["rejected"]))
        self.assertNotIn("Example research announcement", models.canonical(saved["bundle"]))

    def test_freeze_optional_missing_and_fresh_controls(self):
        items, profile = self.optional_news_case(315576000)
        lease = self.begin("freeze-controls")
        for selected, status in (([r for r in items if r.domain == "market"], "DEGRADED"),
                                 (items, "PASS")):
            with self.subTest(status=status):
                result = evaluate(selected, profile)
                self.assertEqual(result["status"], status)
                saved = self.store.bundle(self.store.freeze(result, lease=lease))
                self.assertTrue(saved["usable_now"])
                self.assertEqual(saved["bundle"]["gate"], result)

    def test_freeze_optional_conflict_retains_both_rejection_sources(self):
        items, profile = self.optional_news_case(315576000)
        news = next(r for r in items if r.domain == "news")
        conflict = replace(news, title="SYNTHETIC conflicting headline")
        lease = self.begin("freeze-conflict")
        self.store.put_capture(self.store.capture(news.capture_id), records=(conflict,), lease=lease)
        result = evaluate(items + [conflict], profile)
        self.assertEqual(result["status"], "DEGRADED")
        self.assertEqual({r["evidence_id"] for r in result["rejected"]}, {news.evidence_id, conflict.evidence_id})
        saved = self.store.bundle(self.store.freeze(result, lease=lease))
        self.assertTrue(saved["usable_now"])
        self.assertEqual(saved["bundle"]["gate"], result)
        self.assertEqual(len(saved["bundle"]["references"]), 5)

    def test_freeze_pass_keeps_excluded_tier_c_out_of_usable_refs(self):
        items, profile = self.optional_news_case(315576000)
        news = next(r for r in items if r.domain == "news")
        unconfirmed = replace(news, tier="C")
        lease = self.begin("freeze-corroborated")
        self.store.put_capture(self.store.capture(news.capture_id), records=(unconfirmed,), lease=lease)
        result = evaluate(items + [unconfirmed], profile)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual([r["evidence_id"] for r in result["rejected"]], [unconfirmed.evidence_id])
        saved = self.store.bundle(self.store.freeze(result, lease=lease))
        self.assertTrue(saved["usable_now"])
        self.assertEqual(saved["bundle"]["gate"], result)
        self.assertNotIn(unconfirmed.evidence_id, saved["currently_available_ids"])

    def test_freeze_rejects_forged_degradation_or_pass(self):
        items, profile = self.optional_news_case()
        result = evaluate(items, profile)
        lease = self.begin("reject-forgery")
        altered = copy.deepcopy(result)
        altered["rejected"][0]["reason"] = "invented rejection reason"
        omitted = copy.deepcopy(result)
        omitted["rejected"] = []
        unknown = copy.deepcopy(result)
        unknown["rejected"][0]["evidence_id"] = "0" * 64
        duplicated = copy.deepcopy(result)
        duplicated["rejected"] *= 2
        promoted = copy.deepcopy(result)
        promoted.update(status="PASS", confidence_ceiling="supported")
        promoted["domains"]["news"].update(status="PASS", confidence_ceiling="supported", reasons=[])
        for forged in (altered, omitted, unknown, duplicated, promoted):
            with self.subTest(forged=forged):
                gate.validate_result(forged)
                with self.assertRaises(models.Invalid):
                    self.store.freeze(forged, lease=lease)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM bundles").fetchone()[0], 1)

    def test_freeze_rejects_corrupted_rejected_record_provenance(self):
        items, profile = self.optional_news_case()
        result = evaluate(items, profile)
        news = next(r for r in items if r.domain == "news")
        tampered = news.to_dict() | {"title": "SYNTHETIC tampered storage"}
        self.store.db.execute("UPDATE evidence SET record=? WHERE evidence_id=?",
                              (models.canonical(tampered), news.evidence_id))
        lease = self.begin("reject-tampered-provenance")
        with self.assertRaisesRegex(models.Invalid, "stored evidence hash mismatch"):
            self.store.freeze(result, lease=lease)

    def test_freeze_optional_rejection_does_not_bypass_retention(self):
        items, profile = self.optional_news_case()
        lease = self.begin("reject-expired-provenance")
        now = models.utc_now()
        cap, parsed = self.save(lease, source_id="synthetic_expiring_news", retention="short_term_raw",
                                expires_at=models.stamp(now + timedelta(seconds=1)))
        selected = [r for r in items if r.domain == "market"] + list(parsed.records)
        result = evaluate(selected, profile)
        self.assertEqual(result["status"], "DEGRADED")
        with self.assertRaisesRegex(models.Invalid, "unavailable evidence cannot freeze"):
            self.store.freeze(result, now + timedelta(seconds=2), lease=lease)

    def test_bundle_cannot_keep_expired_evidence_usable(self):
        lease = self.begin()
        cap, parsed = self.save(lease, retention="short_term_raw", expires_at=models.stamp(models.utc_now() + timedelta(minutes=1)))
        result = evaluate(list(parsed.records))
        identity = self.store.freeze(result, lease=lease)
        future = models.utc_now() + timedelta(minutes=2)
        self.assertEqual(len(self.store.bundle(identity, future)["unavailable"]), 1)
        self.store.release(lease)
        self.store.sweep(future)
        self.assertFalse(self.store.bundle(identity, future)["usable_now"])
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM bundles").fetchone()[0], 1)

    def test_reject_unrelated_database_without_mutation(self):
        for name, create in (("user.sqlite3", True), ("blank-schema.sqlite3", False)):
            path = Path(self.temp.name) / name
            with closing(sqlite3.connect(path)) as db, db:
                if create:
                    db.execute("CREATE TABLE user_data (important TEXT)")
                    db.execute("INSERT INTO user_data VALUES ('keep me')")
                else:
                    db.execute("VACUUM")
            before = path.read_bytes()
            with self.assertRaises(models.Invalid):
                store.Store(path)
            self.assertEqual(path.read_bytes(), before)

    def test_existing_owned_schema_validated_and_empty_file_accepted(self):
        with store.Store(self.path) as reopened:
            self.assertEqual(reopened.db.execute("PRAGMA application_id").fetchone()[0], store.APPLICATION_ID)
        empty = Path(self.temp.name) / "empty.sqlite3"
        empty.touch()
        with store.Store(empty):
            pass
        self.store.db.execute("CREATE TABLE unexpected (value TEXT)")
        before = self.path.read_bytes()
        with self.assertRaises(models.Invalid):
            store.Store(self.path)
        self.assertEqual(self.path.read_bytes(), before)

    def test_existing_foreign_version_or_trigger_rejected_without_mutation(self):
        for statement in ("PRAGMA user_version=99", "CREATE TRIGGER unexpected AFTER INSERT ON captures BEGIN DELETE FROM evidence; END"):
            separate = Path(self.temp.name) / ("version.sqlite3" if "99" in statement else "trigger.sqlite3")
            with store.Store(separate) as owned:
                owned.db.execute(statement)
            before = separate.read_bytes()
            with self.assertRaises(models.Invalid):
                store.Store(separate)
            self.assertEqual(separate.read_bytes(), before)

    def test_store_bounds_before_dispatch_and_including_journal(self):
        lease = self.begin()
        for n in range(20):
            body = (str(n).encode() + b"x" * (sources.MAX_BYTES - 10))
            cap = capture(body, source_id="synthetic-size-" + str(n))
            try:
                self.store.put_capture(cap, body, lease=lease)
            except store.BudgetDenied:
                break
        else:
            self.fail("store cap did not fire")
        with self.assertRaises(store.BudgetDenied):
            self.store.reserve(lease)
        self.assertEqual(self.store.attempts(lease.run_id), [])
        with self.store.transaction():
            self.store.db.execute("UPDATE captures SET payload=NULL")
            total = sum(p.stat().st_size for p in Path(self.temp.name).iterdir() if p.is_file())
            self.assertLessEqual(total, 10 * 1024 * 1024)

    def test_no_checkout_or_linked_store(self):
        with self.assertRaises(models.Invalid):
            store.Store(ROOT / "forbidden.sqlite3")
        with self.assertRaises(models.Invalid):
            store.Store("relative.sqlite3")
        link = Path(self.temp.name) / "hardlink.sqlite3"
        import os
        try:
            os.link(self.path, link)
        except PermissionError:
            # Some managed Windows test environments prohibit creating hard links.
            # Still exercise the rejection using the real file's identity metadata.
            original = Path.stat
            target = self.path
            def multiple_links(path, *args, **kwargs):
                value = original(path, *args, **kwargs)
                if path == target:
                    fields = list(value)
                    fields[3] = 2
                    return os.stat_result(fields)
                return value
            with patch.object(Path, "stat", multiple_links), self.assertRaises(models.Invalid):
                store.Store(self.path)
            return
        with self.assertRaises(models.Invalid):
            store.Store(link)
        link.unlink()


class RuntimeTests(TempStoreTests):
    # unittest inherits the storage assertions as well; keep workflow tests separate
    # by selecting only RuntimeTests' new names through load_tests below.
    def test_demo_idempotent_durable_and_summary_integrity(self):
        first = runtime.demo(self.store, "demo-idempotent")
        second = runtime.demo(self.store, "demo-idempotent")
        self.assertEqual(first, second)
        self.assertEqual(first["mode"], "synthetic_test")
        self.assertEqual(first["gate"]["status"], "PASS")
        self.assertEqual(first["live_market_coverage"]["covered"], 0)
        self.assertEqual(first["requests"]["attempted"], 0)
        with store.Store(self.path) as reopened:
            self.assertEqual(reopened.run(first["run_id"])["summary"], first)
        bad = first | {"secret": "not a schema field"}
        self.store.db.execute("UPDATE runs SET summary=?", (models.canonical(bad),))
        with self.assertRaises(models.Invalid):
            self.store.run(first["run_id"])

    def test_probe_denied_flag_unknown_paid_market_zero_transport(self):
        transport = Mock(side_effect=forbidden)
        for n, (allow, source) in enumerate(((False, "fed_press"), (True, "unknown"), (True, "paid_provider"), (True, "market_csv"))):
            result = runtime.probe(self.store, "denied-" + str(n), allow, source, transport)
            self.assertEqual(result["requests"]["attempted"], 0)
            self.assertEqual(result["outcome"], "not_attempted")
        transport.assert_not_called()

    def test_policy_false_and_audit_absent_prevent_dispatch(self):
        transport = Mock(side_effect=forbidden)
        with patch.object(runtime, "policy_allows", side_effect=models.Invalid("active_policy_denies_network")):
            result = runtime.probe(self.store, "policy-false", True, transport=transport)
        self.assertEqual(result["requests"]["attempted"], 0)
        result = runtime.probe(self.store, "audit-missing", True, transport=transport, policy_root=Path(self.temp.name))
        self.assertEqual(result["requests"]["attempted"], 0)
        transport.assert_not_called()

    def test_injected_probe_never_claims_live_and_gate_market_blocks(self):
        body = current_feed()
        transport = Mock(return_value=sources.Response(body, "application/rss+xml"))
        first = runtime.probe(self.store, "probe", True, transport=transport)
        second = runtime.probe(self.store, "probe", True, transport=transport)
        self.assertEqual(first, second)
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(first["mode"], "synthetic_test")
        self.assertNotEqual(first["source_state"], "live_verified")
        self.assertEqual(first["gate"]["domains"]["news"]["status"], "PASS")
        self.assertEqual(first["gate"]["status"], "BLOCK")
        self.assertEqual(first["quality"]["request_sample_size"], 1)
        self.assertIsNone(first["quality"]["long_term_reliability"])
        cap = self.store.capture(first["capture_ids"][0])
        self.assertEqual(cap.content_hash, models.digest(body))
        self.assertEqual(cap.response_hash, sources.Response(body, "application/rss+xml").response_hash)
        self.assertEqual(first["observations"][0]["content_hash"], cap.content_hash)
        self.assertEqual(first["observations"][0]["response_hash"], cap.response_hash)

    def test_budget_reserved_before_transport_and_retry_once(self):
        def transient(source):
            rows = self.store.db.execute("SELECT * FROM attempts ORDER BY reserved_at").fetchall()
            self.assertEqual(rows[-1]["outcome"], "reserved")
            self.assertEqual(rows[-1]["reserved_bytes"], sources.MAX_BYTES)
            raise sources.TransportError("http_status_503")
        transport = Mock(side_effect=transient)
        result = runtime.probe(self.store, "retry", True, transport=transport)
        self.assertEqual(transport.call_count, 2)
        self.assertEqual(result["requests"]["attempted"], 2)
        self.assertEqual(result["requests"]["failed"], 2)

    def test_unknown_outcome_no_retry_and_new_task_no_network(self):
        transport = Mock(side_effect=TimeoutError("PRIVATE PATH OR CREDENTIAL MUST NOT ESCAPE"))
        first = runtime.probe(self.store, "unknown", True, transport=transport)
        self.assertEqual(first["outcome"], "unknown")
        self.assertIsNone(first["requests"]["observed_bytes"])
        self.assertNotIn("PRIVATE", models.canonical(first))
        second = runtime.probe(self.store, "different-key", True, transport=transport)
        self.assertEqual(second["requests"]["attempted"], 0)
        self.assertIn("unknown_usage_halts_dispatch", second["error_codes"])
        self.assertEqual(transport.call_count, 1)

    def test_empty_malformed_and_rejected_feed_not_source_verified(self):
        for n, body in enumerate((b"<rss><channel/></rss>", b"not XML", current_feed(title=""))):
            result = runtime.probe(self.store, "bad-feed-" + str(n), True,
                                   transport=lambda source: sources.Response(body, "application/rss+xml"))
            self.assertEqual(result["source_state"], "degraded")
            self.assertEqual(result["outcome"], "failed")
            self.assertEqual(result["quality"]["nonempty_parse_count"], 0)
            self.assertEqual(len(result["capture_ids"]), 1)

    def test_resume_after_success_before_finish_does_not_redispatch(self):
        earlier = models.utc_now() - timedelta(minutes=1)
        lease, _ = self.store.begin("resume-success", "synthetic_test", earlier, intent="probe-v1:fed_press:True")
        attempt = self.store.reserve(lease, earlier)
        self.store.settle(lease, attempt, "success", len(FEED), "response_received", earlier)
        # Simulate a process loss after request success but before saving the body.
        transport = Mock(side_effect=forbidden)
        result = runtime.probe(self.store, "resume-success", True, transport=transport)
        self.assertEqual(result["outcome"], "unknown")
        transport.assert_not_called()

    def test_resume_durable_capture_without_redispatch(self):
        earlier = models.utc_now() - timedelta(minutes=1)
        lease, _ = self.store.begin("resume-capture", "synthetic_test", earlier, intent="probe-v1:fed_press:True")
        attempt = self.store.reserve(lease, earlier)
        self.store.settle(lease, attempt, "success", len(FEED), "response_received", earlier)
        cap = capture(retrieved_at=models.stamp(earlier), first_seen_at=models.stamp(earlier), request_id=attempt)
        parsed = sources.parse(FEED, cap)
        self.store.put_capture(cap, FEED, parsed.records, earlier, lease=lease)
        transport = Mock(side_effect=forbidden)
        result = runtime.probe(self.store, "resume-capture", True, transport=transport)
        self.assertEqual(result["outcome"], "simulated_probe")
        self.assertEqual(result["capture_ids"], [cap.version_id])
        self.assertEqual(result["requests"]["attempted"], 1)
        transport.assert_not_called()

    def test_policy_reader_checks_actual_flag_and_audit_hash(self):
        text = (ROOT / "governance/policy.toml").read_text(encoding="utf-8")
        changed = text.replace("external_data_access_allowed = true", "external_data_access_allowed = false")
        with patch.object(runtime.tomllib, "load", return_value=__import__("tomllib").loads(changed)):
            with self.assertRaisesRegex(models.Invalid, "policy_denies"):
                runtime.policy_allows()
        audit = json.loads((ROOT / "docs/reviews/p1-prompt-audit.json").read_text(encoding="utf-8"))
        audit["files"]["governance/policy.toml"] = "0" * 64
        original = Path.read_text
        def altered(path, *args, **kwargs):
            return json.dumps(audit) if path.name == "p1-prompt-audit.json" else original(path, *args, **kwargs)
        with patch.object(Path, "read_text", altered), self.assertRaisesRegex(models.Invalid, "audited_input_changed"):
            runtime.policy_allows()

    def test_daily_limit_prevents_injected_transport(self):
        transport = Mock(side_effect=sources.TransportError("http_status_503"))
        for n in range(6):
            runtime.probe(self.store, "daily-" + str(n), True, transport=transport)
        last = runtime.probe(self.store, "daily-over", True, transport=transport)
        self.assertEqual(transport.call_count, 12)
        self.assertEqual(last["requests"]["attempted"], 0)
        self.assertIn("request_budget_exhausted", last["error_codes"])

    def test_task_intent_conflict_not_reinterpreted(self):
        runtime.demo(self.store, "same-key")
        with self.assertRaises(models.Invalid):
            runtime.probe(self.store, "same-key", True, transport=Mock(side_effect=forbidden))

    def test_cli_generic_errors_do_not_expose_private_details(self):
        output = io.StringIO()
        with patch.object(runtime, "Store", side_effect=OSError("PRIVATE_PATH credential=PRIVATE_SECRET")), redirect_stdout(output):
            self.assertEqual(runtime.main(["demo", "--store", str(self.path)]), 2)
        self.assertEqual(json.loads(output.getvalue()), {"status": "ERROR", "reason": "local_operation_failed"})

    def test_functional_cli_demo_replay_sweep_and_denied_probe(self):
        child = (
            "import socket,http.client,urllib.request,runpy,sys; "
            "defender=lambda *a,**k: (_ for _ in ()).throw(AssertionError('NETWORK FORBIDDEN')); "
            "socket.socket=socket.create_connection=socket.getaddrinfo=defender; "
            "http.client.HTTPConnection.request=urllib.request.urlopen=defender; "
            "sys.argv=['market_intelligence']+sys.argv[1:]; "
            "runpy.run_module('market_intelligence',run_name='__main__')"
        )
        def cli(*args):
            output = subprocess.run([sys.executable, "-B", "-c", child, *args, "--store", str(self.path)],
                                    cwd=ROOT, text=True, capture_output=True, timeout=20)
            self.assertNotIn("Traceback", output.stderr)
            self.assertNotIn(str(self.path), output.stdout)
            return output.returncode, json.loads(output.stdout)
        code, first = cli("demo", "--task-key", "cli-demo")
        self.assertEqual(code, 0)
        self.assertEqual(first["gate"]["status"], "PASS")
        _, repeated = cli("demo", "--task-key", "cli-demo")
        self.assertEqual(repeated, first)
        code, replayed = cli("replay", "--task-key", "cli-replay", "--capture-id", first["capture_ids"][0], "--parser-version", "rss-v1")
        self.assertEqual(code, 0)
        self.assertEqual(replayed["mode"], "historical_replay")
        self.assertEqual(replayed["gate"]["status"], "BLOCK")
        code, denied = cli("probe", "--task-key", "cli-no-network")
        self.assertEqual(code, 2)
        self.assertEqual(denied["requests"]["attempted"], 0)
        code, swept = cli("sweep")
        self.assertEqual(code, 0)
        self.assertEqual(swept["removed_capture_ids"], [])
        self.assertEqual(self.store.run(first["run_id"])["summary"], first)
        self.assertTrue(self.store.bundle(first["bundle_id"])["usable_now"])


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.source = sources.load_catalog(ROOT / "config/sources.toml")["fed_press"]

    def test_catalog_denies_unknown_rights_keys_paid_and_endpoints(self):
        for changes in ({"rights": "unknown"}, {"requires_key": True}, {"paid": True},
                        {"endpoint": sources.FED_URL + "?x=1"}, {"endpoint": "http://www.federalreserve.gov/feeds/press_all.xml"},
                        {"state": "disabled"}, {"source_id": "unknown"}):
            with self.subTest(changes=changes), self.assertRaises(models.Invalid):
                sources.fetch(replace(self.source, **changes))
        with self.assertRaises(models.Invalid):
            sources.PaidAdapter().collect(key="synthetic-not-a-key", transport=forbidden)

    def test_private_dns_and_mixed_addresses_rejected(self):
        for address in ("127.0.0.1", "10.1.2.3", "169.254.169.254", "::1", "::ffff:8.8.8.8", "224.0.0.1"):
            row = (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))
            with self.subTest(address=address), self.assertRaises(sources.TransportError):
                sources.public_addresses([row])

    def test_transport_redirect_types_size_and_safe_headers(self):
        class FakeResponse:
            def __init__(self, status=200, headers=None, body=FEED):
                from email.message import Message
                self.status, self.body = status, body
                self.headers = Message()
                for key, value in (headers or {"Content-Type": "application/rss+xml"}).items():
                    self.headers[key] = value
            def getheader(self, name, default=None):
                return self.headers.get(name, default)
            def read1(self, count):
                chunk, self.body = self.body[:count], self.body[count:]
                return chunk
        cases = [FakeResponse(302), FakeResponse(headers={"Content-Type": "text/html"}),
                 FakeResponse(headers={"Content-Type": "application/rss+xml", "Content-Length": "512001"}),
                 FakeResponse(headers={"Content-Type": "application/rss+xml", "Content-Encoding": "gzip"}),
                 FakeResponse(body=b"x" * 512001)]
        for response in cases + [FakeResponse()]:
            connection = Mock(sock=None)
            connection.getresponse.return_value = response
            with patch.object(sources, "_resolve", return_value=[("public-address",)]), patch.object(sources, "_PinnedTLS", return_value=connection):
                if response in cases:
                    with self.assertRaises(sources.TransportError):
                        sources.fetch(self.source)
                else:
                    self.assertEqual(sources.fetch(self.source).body, FEED)
                self.assertEqual(connection.request.call_count, 1)
                args, kwargs = connection.request.call_args
                self.assertEqual(args, ("GET", "/feeds/press_all.xml"))
                self.assertNotIn("Authorization", kwargs["headers"])
                self.assertNotIn("Cookie", kwargs["headers"])
                connection.close.assert_called()


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for cls in (BoundaryTests, GateTests, TempStoreTests, RuntimeTests, TransportTests):
        names = [name for name in cls.__dict__ if name.startswith("test_")]
        suite.addTests(cls(name) for name in sorted(names))
    return suite


if __name__ == "__main__":
    unittest.main()
