"""Offline tests for the versioned P4CORE product boundary."""

from contextlib import ExitStack, redirect_stderr, redirect_stdout
from dataclasses import asdict, replace
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from market_intelligence import gate, models, mvp_core, runtime


AS_OF = "2020-01-03T00:00:00Z"
NOW = models.instant("2020-01-03T01:00:00Z")
CORE = ("000001.SH", "399001.SZ", "399006.SZ")
OPTIONAL = ("KOSPI", "SP500")
CALENDAR = runtime.synthetic_calendar()
CORE_CALENDAR = replace(CALENDAR, sessions=tuple(s for s in CALENDAR.sessions if s.calendar_id in ("SSE", "SZSE")))
OPTIONAL_CALENDARS = {
    key: replace(CALENDAR, sessions=(CALENDAR.latest(models.MARKETS[key][0], NOW),))
    for key in OPTIONAL
}


def fixture():
    news = models.Evidence(
        capture_id="1" * 64, source_id="synthetic", locator="https://example.invalid/item",
        domain="news", key="fixture-release", tier="A", title="Synthetic primary release",
        value=None, published_at="2020-01-02T12:00:00Z", occurred_at=None, effective_at=None,
        observed_at=None, retrieved_at=AS_OF, first_seen_at=AS_OF, expires_at=None,
        mode="synthetic_test", parser_version="rss-v1", calendar_id=None, session_id=None,
        close_at=None,
    )
    items = {"news": news}
    for key in CORE + OPTIONAL:
        session = CALENDAR.latest(models.MARKETS[key][0], NOW)
        items[key] = replace(
            news, domain="market", key=key, title=None, value=100, published_at=None,
            parser_version="market-csv-v1", calendar_id=session.calendar_id,
            session_id=session.session_id, close_at=session.close_at, observed_at=session.close_at,
        )
    return items


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.items = fixture()

    def evaluate(self, *items, calendar=CORE_CALENDAR, as_of=AS_OF, now=NOW, optional=None):
        return mvp_core.evaluate(items, calendar, as_of, now=now, optional=optional)

    def core(self):
        return [self.items[key] for key in (*CORE, "news")]

    def slot(self, key, *items):
        return {"calendar": OPTIONAL_CALENDARS[key], "records": items}

    def context(self):
        return {key: self.slot(key, self.items[key]) for key in OPTIONAL}

    def assert_unavailable(self, row):
        self.assertEqual(
            (row["status"], row["availability"], row["confidence_ceiling"]),
            ("DEGRADED", "unavailable", "unavailable"),
        )
        self.assertEqual(row.get("accepted_ids", []), [])

    def test_missing_optional_degrades_without_reducing_core_coverage(self):
        result = self.evaluate(*self.core())
        self.assertEqual((result["status"], result["confidence_ceiling"]), ("DEGRADED", "limited"))
        self.assertEqual(result["evidence_scope"], "synthetic_engineering_only")
        self.assertEqual(result["profile_version"], "p4core-a-share-v1")
        self.assertEqual(result["domains"]["market"]["covered"], 3)
        self.assertEqual(result["domains"]["market"]["expected"], 3)
        self.assertTrue(all(row["status"] == "PASS" for row in result["domains"].values()))
        for key in OPTIONAL:
            self.assert_unavailable(result["optional"][key])
            self.assertIn("insufficient_coverage", result["optional"][key]["reasons"])

    def test_valid_optional_stays_separate_and_supported(self):
        result = self.evaluate(*self.core(), optional=self.context())
        self.assertEqual((result["status"], result["confidence_ceiling"]), ("PASS", "supported"))
        self.assertEqual(result["accepted_ids"], sorted(row.evidence_id for row in self.core()))
        for key in OPTIONAL:
            row = result["optional"][key]
            self.assertEqual((row["status"], row["availability"], row["confidence_ceiling"]),
                             ("PASS", "available", "supported"))
            self.assertEqual(row["accepted_ids"], [self.items[key].evidence_id])

    def test_optional_failures_are_isolated_and_cannot_use_a_valid_sibling(self):
        for key, other in (OPTIONAL, OPTIONAL[::-1]):
            item = self.items[key]
            for fault, reason in (
                (asdict(item) | {"value": []}, "market value"),
                (replace(item, value=10**400), "too large"),
                (replace(item, calendar_id="SSE"), "wrong or incomplete session identity"),
                (replace(item, tier="C", capture_id="2" * 64), None),
                (replace(item, value=101), "conflicting evidence"),
                (item, "duplicate evidence reference"),
                (replace(item, expires_at="2020-01-03T00:30:00Z"), "expired evidence"),
            ):
                with self.subTest(key=key, reason=reason):
                    # Tier C alone is untrusted; other faults also invalidate a slot with a good row.
                    siblings = () if reason is None else (item,)
                    envelopes = self.context() | {key: self.slot(key, fault, *siblings)}
                    result = self.evaluate(*self.core(), optional=envelopes)
                    row = result["optional"][key]
                    self.assertEqual(result["status"], "DEGRADED")
                    self.assert_unavailable(row)
                    self.assertTrue(any((reason or "Tier C cannot confirm") in r for r in row["reasons"]))
                    self.assertEqual(result["optional"][other]["status"], "PASS")

    def test_stale_optional_does_not_block_core(self):
        item = self.items["KOSPI"]
        close = "2019-12-24T15:30:00+09:00"
        calendar = OPTIONAL_CALENDARS["KOSPI"]
        session = replace(calendar.sessions[0], session_id="2019-12-24", close_at=close)
        old = replace(item, session_id="2019-12-24", close_at=close, observed_at=close)
        envelopes = self.context() | {"KOSPI": {"calendar": replace(calendar, sessions=(session,)),
                                                "records": [old]}}
        result = self.evaluate(*self.core(), optional=envelopes)
        self.assertEqual(result["status"], "DEGRADED")
        self.assert_unavailable(result["optional"]["KOSPI"])
        self.assertIn("stale evidence", result["optional"]["KOSPI"]["reasons"])

    def test_each_required_slot_blocks_even_with_valid_foreign_indices(self):
        for key in (*CORE, "news"):
            item = self.items[key]
            domain = item.domain
            for bad in (None, replace(item, tier="C")):
                with self.subTest(key=key, bad=bad is not None):
                    items = [self.items[name] for name in (*CORE, "news") if name != key]
                    if bad is not None:
                        items.append(bad)
                    result = self.evaluate(*items, optional=self.context())
                    self.assertEqual((result["status"], result["confidence_ceiling"]), ("BLOCK", "none"))
                    self.assertEqual(result["domains"][domain]["missing"], [key if domain == "market" else "primary_news"])
                    self.assertTrue(all(row["status"] == "PASS" for row in result["optional"].values()))
        self.assertEqual(self.evaluate(self.items["news"])["status"], "BLOCK")

    def test_primary_requires_tier_a_and_never_promotes_b_or_c(self):
        primary = self.items["news"]
        for tier in ("B", "C"):
            weaker = replace(primary, tier=tier, capture_id="2" * 64)
            with self.subTest(tier=tier):
                self.assertEqual(self.evaluate(*self.core()[:-1], weaker)["domains"]["news"]["status"], "BLOCK")
                result = self.evaluate(*self.core(), weaker)
                self.assertEqual(result["domains"]["news"]["status"], "PASS")
                self.assertNotIn(weaker.evidence_id, result["accepted_ids"])
                self.assertIn(primary.evidence_id, result["accepted_ids"])
                conflict = replace(weaker, title="Conflicting release")
                self.assertEqual(self.evaluate(*self.core(), conflict)["domains"]["news"]["status"], "BLOCK")

    def test_required_dates_expiry_and_conflicts_keep_existing_checks(self):
        item = self.items[CORE[0]]
        news = self.items["news"]
        for row, reason in (
            (replace(item, value=999), "conflicting evidence"),
            (replace(item, session_id="2020-01-01"), "wrong or incomplete session identity"),
            (replace(item, retrieved_at="2020-01-03T00:30:00Z"), "retrieved_at after as_of"),
            (replace(news, published_at="2020-01-03T00:30:00Z"), "future published_at"),
            (replace(news, expires_at="2020-01-03T00:30:00Z"), "expired evidence"),
            (replace(news, published_at="2019-11-25T00:00:00Z"), "stale evidence"),
        ):
            with self.subTest(reason=reason):
                result = self.evaluate(*self.core(), row, optional=self.context())
                self.assertEqual(result["status"], "BLOCK")
                self.assertIn(reason, result["domains"][row.domain]["reasons"])
        with self.assertRaisesRegex(models.Invalid, "future as_of"):
            self.evaluate(*self.core(), as_of="2020-01-03T01:00:01Z")

    def test_non_synthetic_records_are_rejected_in_their_own_slots(self):
        for mode in ("live_observation", "historical_replay"):
            for key in (*CORE, "news", *OPTIONAL):
                with self.subTest(mode=mode, key=key):
                    bad = asdict(replace(self.items[key], mode=mode))
                    optional = self.context()
                    core = [asdict(self.items[name]) if name != key else bad for name in (*CORE, "news")]
                    if key in OPTIONAL:
                        optional[key] = self.slot(key, bad)
                    result = self.evaluate(*core, optional=optional)
                    if key in OPTIONAL:
                        self.assertEqual(result["status"], "DEGRADED")
                        row = result["optional"][key]
                        self.assert_unavailable(row)
                    else:
                        self.assertEqual(result["status"], "BLOCK")
                        row = result["domains"][bad["domain"]]
                    self.assertIn("wrong evidence mode", row["reasons"])

    def test_kospi_row_missing_domain_degrades_only_kospi(self):
        optional = self.context() | {"KOSPI": self.slot("KOSPI", {"key": "KOSPI"})}
        result = self.evaluate(*self.core(), optional=optional)
        self.assertEqual(result["status"], "DEGRADED")
        self.assertTrue(all(row["status"] == "PASS" for row in result["domains"].values()))
        self.assert_unavailable(result["optional"]["KOSPI"])
        self.assertEqual(result["optional"]["SP500"]["status"], "PASS")

    def test_duplicate_krx_calendar_degrades_only_kospi(self):
        calendar = OPTIONAL_CALENDARS["KOSPI"]
        duplicate = replace(calendar, sessions=calendar.sessions * 2)
        optional = self.context() | {"KOSPI": {"calendar": duplicate, "records": [self.items["KOSPI"]]}}
        result = self.evaluate(*self.core(), optional=optional)
        self.assertEqual(result["status"], "DEGRADED")
        self.assertTrue(all(row["status"] == "PASS" for row in result["domains"].values()))
        self.assert_unavailable(result["optional"]["KOSPI"])
        self.assertIn("invalid_optional_calendar", result["optional"]["KOSPI"]["reasons"])
        self.assertEqual(result["optional"]["SP500"]["status"], "PASS")

    def test_optional_slot_cannot_borrow_core_news_or_other_slot_records(self):
        accepted = self.items["KOSPI"]
        for wrong in (self.items["SP500"], self.items[CORE[0]], self.items["news"]):
            with self.subTest(key=wrong.key):
                optional = self.context() | {"KOSPI": self.slot("KOSPI", accepted, wrong)}
                result = self.evaluate(*self.core(), optional=optional)
                self.assertEqual(result["status"], "DEGRADED")
                self.assertEqual(result["accepted_ids"], sorted(item.evidence_id for item in self.core()))
                self.assertEqual(result["optional"]["SP500"]["accepted_ids"], [self.items["SP500"].evidence_id])
                row = result["optional"]["KOSPI"]
                self.assert_unavailable(row)
                for item in (accepted, wrong):
                    self.assertNotIn(item.evidence_id, json.dumps(row))

    def test_optional_envelope_failures_stay_local(self):
        good = self.context()["KOSPI"]
        for block in (None, {"records": good["records"]}, {"calendar": good["calendar"]},
                      good | {"calendar": None}, good | {"calendar": OPTIONAL_CALENDARS["SP500"]},
                      good | {"records": None}, good | {"records": [asdict(self.items["KOSPI"]) | {"value": None}]}):
            with self.subTest(block=block):
                result = self.evaluate(*self.core(), optional=self.context() | {"KOSPI": block})
                self.assertEqual(result["status"], "DEGRADED")
                self.assert_unavailable(result["optional"]["KOSPI"])
                self.assertEqual(result["optional"]["SP500"]["status"], "PASS")

    def test_absent_and_malformed_core_cannot_use_optional_context(self):
        result = self.evaluate(optional=self.context())
        self.assertEqual((result["status"], result["confidence_ceiling"]), ("BLOCK", "none"))
        self.assertEqual(result["domains"]["market"]["missing"], list(CORE))
        self.assertEqual(result["domains"]["news"]["missing"], ["primary_news"])
        self.assertEqual(result["accepted_ids"], [])
        for wrong in ({"key": "KOSPI"}, self.items["KOSPI"]):
            with self.subTest(wrong=wrong):
                result = self.evaluate(*self.core(), wrong, optional=self.context())
                self.assertEqual(result["status"], "BLOCK")
                self.assertEqual(result["optional"]["KOSPI"]["status"], "PASS")
        with self.assertRaisesRegex(models.Invalid, "configured optional symbols"):
            self.evaluate(*self.core(), optional={CORE[0]: self.context()["KOSPI"]})

    def test_invalid_core_calendar_is_rejected_even_with_valid_optional_calendars(self):
        duplicate = replace(CORE_CALENDAR, sessions=CORE_CALENDAR.sessions * 2)
        for calendar in (None, duplicate, CALENDAR):
            with self.subTest(calendar=calendar), self.assertRaises(models.Invalid):
                self.evaluate(*self.core(), calendar=calendar, optional=self.context())

    def test_preflight_cli_has_no_network_or_source_permission(self):
        with ExitStack() as stack:
            for target in ("socket.socket", "socket.getaddrinfo", "socket.create_connection",
                           "http.client.HTTPConnection.request", "urllib.request.urlopen",
                           "market_intelligence.sources.fetch"):
                stack.enter_context(patch(target, side_effect=AssertionError("Network forbidden")))
            output = stack.enter_context(redirect_stdout(io.StringIO()))
            self.assertEqual(mvp_core.main(["preflight"]), 0)
        result = json.loads(output.getvalue())
        self.assertEqual((result["status"], result["confidence_ceiling"]), ("BLOCK", "none"))
        self.assertEqual(result["live_market_coverage"], {"covered": 0, "expected": 3, "missing": list(CORE)})
        self.assertEqual(result["source_requirements"], {"free": True, "no_registration": True, "no_key": True})
        self.assertEqual(result["requests_attempted"], 0)
        self.assertEqual(result["permissions_enabled"], [])
        self.assertEqual(result["permissions_granted"], [])
        self.assertEqual(result["news_candidate"]["status"], "documented_candidate_only")
        self.assertIn("Board-authored releases only", result["news_candidate"]["scope"])
        self.assertIn("excludes third-party material", result["news_candidate"]["scope"])
        for key in OPTIONAL:
            self.assert_unavailable(result["optional"][key])
        self.assertEqual(self.evaluate(*self.core(), optional=self.context())["status"], "PASS")
        self.assertEqual(mvp_core.preflight(), result)

    def test_profile_tamper_rejected_by_evaluation_and_preflight(self):
        profile = mvp_core._PROFILE_PATH.read_bytes()
        for before, after in (
            (b"market-v1", b"market-unknown"),
            (b'399006.SZ', b'KOSPI'),
            (b'synthetic_test', b'live_observation'),
            (b'required_news_tier = "A"', b'required_news_tier = "B"'),
            (b'no_key = true', b'no_key = false'),
        ):
            with self.subTest(before=before), patch.object(
                Path, "open", side_effect=lambda *_: io.BytesIO(profile.replace(before, after)),
            ):
                with self.assertRaisesRegex(models.Invalid, "v1 pin"):
                    self.evaluate(*self.core(), optional=self.context())
                with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()):
                    self.assertEqual(mvp_core.main(["preflight"]), 2)
                    self.assertEqual(out.getvalue(), "")

    def test_legacy_profile_still_requires_five_and_uses_legacy_receipt(self):
        profile = runtime.profile_for("live_observation")
        self.assertEqual(profile.version, "p1-five-benchmarks-v1")
        self.assertEqual(profile.universe, CORE + OPTIONAL)
        legacy = runtime.profile_for("synthetic_test", True)
        partial = gate.evaluate(self.core(), legacy, CALENDAR, AS_OF, now=NOW)
        self.assertEqual(partial["status"], "BLOCK")
        self.assertEqual(partial["domains"]["market"]["missing"], list(OPTIONAL))
        items = self.items | {"news": replace(self.items["news"], tier="B")}
        full = gate.evaluate(items.values(), legacy, CALENDAR, AS_OF, now=NOW)
        self.assertEqual(full["status"], "PASS")
        self.assertEqual(gate.validate_result(full), full)
        self.assertEqual(full["schema_version"], 1)
