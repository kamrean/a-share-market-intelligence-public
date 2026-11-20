"""Authored fixtures only; no live payload or source request."""

from copy import deepcopy
import json
import unittest

from market_intelligence import tencent_daily as d
from market_intelligence.models import Invalid, canonical
from market_intelligence.source_policy import PURPOSE, RIGHTS, assess_source_policy

NOW = "2026-11-19T08:00:00Z"


def row(day="2026-11-19", close="9999.123"):
    return [day, "9998", close, "10001", "9990", "1", "0", "0", "0"]


def body(rows, symbol="sh000001", adjustment="day"):
    return ("kline_dayqfq=" + json.dumps({"code": 0, "msg": "", "data": {
        symbol: {adjustment: rows}}}) + ";").encode()


class TencentDailyTests(unittest.TestCase):
    def setUp(self):
        self.window = d.plan(NOW)

    def parse(self, rows, **kw):
        return d.parse(body(rows, **kw), kw.get("symbol", "sh000001"), self.window, NOW)

    def test_latest_completed_session(self):
        self.assertEqual(self.window["end_date"], "2026-11-19")
        self.assertEqual(len(self.window["expected_sessions"]), 10)
        self.assertEqual(d.plan("2026-11-19T06:59:59Z")["end_date"], "2026-11-18")
        self.assertEqual(d.plan("2026-11-19T07:00:00Z")["end_date"], "2026-11-19")

    def test_weekend_and_holiday(self):
        self.assertEqual(d.plan("2026-11-22T10:00:00Z")["end_date"], "2026-11-20")
        self.assertEqual(d.plan("2026-10-07T10:00:00Z")["end_date"], "2026-09-30")
        self.assertEqual(d.plan("2026-02-23T10:00:00Z")["end_date"], "2026-02-13")

    def test_unknown_calendar_boundary(self):
        for at in ("2027-01-04T10:00:00Z", "2025-11-19T10:00:00Z", "2026-01-04T10:00:00Z"):
            with self.subTest(at=at), self.assertRaises(Invalid):
                d.plan(at)

    def test_aware_clock_and_tampered_plan(self):
        with self.assertRaises(Invalid):
            d.plan("2026-11-19T10:00:00")
        changed = deepcopy(self.window)
        changed["end_date"] = "2026-11-20"
        with self.assertRaises(Invalid):
            d.request_path("sh000001", changed)

    def test_fixed_symbols_and_request_window(self):
        for symbol in d.SYMBOLS:
            path = d.request_path(symbol, self.window)
            self.assertTrue(path.startswith(d.PATH + "?"))
            self.assertIn(symbol, path)
            self.assertIn("2026-11-06", path)
            self.assertIn("2026-11-19", path)
            self.assertNotIn("640", path)
        with self.assertRaises(Invalid):
            d.request_path("sh600000", self.window)

    def test_current_core_fact_shapes_and_replay(self):
        rows = [row(day) for day in self.window["expected_sessions"]]
        value = self.parse(rows)
        self.assertEqual(value["summary"]["freshness"], "CURRENT")
        self.assertEqual(value["summary"]["accepted"], 10)
        self.assertEqual(canonical(value), canonical(self.parse(rows)))
        self.assertIsNone(value["facts"][0]["published_at"])
        self.assertEqual(value["facts"][0]["expires_at"], NOW)
        self.assertIsNone(value["facts"][0]["payload"]["turnover"])
        self.assertEqual(value["facts"][0]["locator"], d.ENDPOINT)
        d.validate_summary(value["summary"], self.window)

    def test_all_three_symbol_bindings(self):
        for symbol, benchmark in d.SYMBOLS.items():
            result = self.parse([row()], symbol=symbol)
            self.assertEqual(result["facts"][0]["payload"]["symbol"], benchmark)

    def test_no_prices_in_sanitized_summary(self):
        summary = self.parse([row()])["summary"]
        self.assertNotIn("9999.123", canonical(summary))
        self.assertNotIn("payload", summary)
        self.assertEqual(summary["normal_composition"], "BLOCKED_SOURCE_RIGHTS")

    def test_exact_duplicates_collapsed(self):
        result = self.parse([row(), row(), row()])
        self.assertEqual(result["summary"]["accepted"], 1)
        self.assertEqual(result["summary"]["duplicate_rows"], 2)
        self.assertEqual(result["summary"]["rejected"], 0)
        d.validate_summary(result["summary"], self.window)

    def test_conflicting_duplicates_all_quarantined(self):
        result = self.parse([row(), row(), row(close="9999.124")])
        self.assertEqual(result["summary"]["accepted"], 0)
        self.assertEqual(result["summary"]["conflicting_rows"], 3)
        self.assertEqual(result["summary"]["duplicate_rows"], 0)
        d.validate_summary(result["summary"], self.window)

    def test_invalid_same_key_cannot_leave_valid_survivor(self):
        for bad in (["2026-11-19"], row(close="-1")):
            summary = self.parse([row(), bad])["summary"]
            self.assertEqual(summary["accepted"], 0)
            self.assertEqual(summary["conflicting_rows"], 2)

    def test_unknown_unit_change_still_conflicts(self):
        other = row()
        other[5] = "2"
        self.assertEqual(self.parse([row(), other])["summary"]["conflicting_rows"], 2)

    def test_missing_stale_and_empty(self):
        self.assertEqual(self.parse([row()])["summary"]["freshness"], "LATEST_PRESENT_GAPS")
        self.assertEqual(self.parse([row("2026-11-18")])["summary"]["freshness"], "STALE")
        self.assertEqual(self.parse([])["summary"]["freshness"], "EMPTY")

    def test_future_outside_weekend_invalid_date(self):
        summary = self.parse([row("2026-11-20"), row("2026-11-05"), row("2026-11-15"), row("2026-11-99")])["summary"]
        self.assertEqual(summary["accepted"], 0)
        self.assertEqual(summary["reasons"], {"outside_window": 2, "not_scheduled_session": 1, "invalid_date": 1})
        self.assertEqual(summary["latest_returned_session"], "2026-11-20")
        d.validate_summary(summary, self.window)

    def test_invalid_numbers_and_ohlc(self):
        for value in ("NaN", "Infinity", "1e10", "-1", "0", "1" * 41, 1, True, None, "10002"):
            with self.subTest(value=value):
                summary = self.parse([row(close=value)])["summary"]
                self.assertEqual(summary["rejected"], 1)

    def test_plain_json_and_explicit_adjustment(self):
        data = body([row()], adjustment="qfqday")
        self.assertEqual(d.parse(data, "sh000001", self.window, NOW)["summary"]["adjustment"], "qfqday")
        self.assertEqual(d.parse(data[len(b"kline_dayqfq="):-1], "sh000001", self.window, NOW)["summary"]["accepted"], 1)

    def test_strict_nonexecuting_shape(self):
        values = [b'alert(1)', b'callback({})', b'{"code":0,"code":0,"data":{}}',
                  b'{"code":NaN}', b'{"code":true}', b'\xff', b'[' * 2000,
                  body([row()], symbol="sz399001"), body([row()] * 33), b'x' * 512001]
        for value in values:
            with self.subTest(prefix=value[:30]), self.assertRaises(Invalid):
                d.parse(value, "sh000001", self.window, NOW)

    def test_ambiguous_adjustment(self):
        data = {"code": 0, "data": {"sh000001": {"day": [row()], "qfqday": [row()]}}}
        with self.assertRaises(Invalid):
            d.parse(json.dumps(data).encode(), "sh000001", self.window, NOW)

    def test_observation_cannot_precede_possession_window(self):
        with self.assertRaises(Invalid):
            d.parse(body([row()]), "sh000001", self.window, "2026-11-19T07:59:59Z")

    def test_all_normal_source_rights_remain_unknown(self):
        for operation in (*RIGHTS, "local_composition"):
            result = assess_source_policy(d.source_policy(), d.SOURCE, operation, PURPOSE)
            self.assertEqual(result["state"], "unknown")
            self.assertFalse(result["operation_enabled"])
            self.assertFalse(result["legal_entitlement_verified"])

    def test_summary_tamper(self):
        summary = self.parse([row()])["summary"]
        for key, value in (("source_policy_state", "permitted"), ("accepted", True), ("freshness", "CURRENT"),
                           ("finality", "final"), ("publication_time", NOW), ("missing_sessions", []),
                           ("duplicate_rows", 31), ("latest_accepted_session", "2026-11-20")):
            changed = deepcopy(summary)
            changed[key] = value
            with self.subTest(key=key), self.assertRaises(Invalid):
                d.validate_summary(changed, self.window)
        summary["raw_body"] = "forbidden"
        with self.assertRaises(Invalid):
            d.validate_summary(summary, self.window)


if __name__ == "__main__":
    unittest.main()
