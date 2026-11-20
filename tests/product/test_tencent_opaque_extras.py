"""Authored examples of the sanitized non-scalar-extra shape; no source payloads."""

import json
import unittest

from market_intelligence import tencent_daily as daily
from market_intelligence.models import Invalid


NOW = "2026-11-19T08:00:00Z"
SYMBOL = "sh000001"
PREFIX = ["2026-11-19", "10", "11", "12", "9", "1"]


def json_bytes(value):
    return json.dumps(value).encode("utf-8")


def response(rows):
    return json_bytes({"code": 0, "msg": "", "data": {SYMBOL: {"day": rows}}})


class TencentOpaqueExtrasTests(unittest.TestCase):
    def setUp(self):
        self.window = daily.plan(NOW)

    def parse_bytes(self, body, symbol=SYMBOL, observed_at=NOW):
        return daily.parse_opaque_extras(body, symbol, self.window, observed_at)

    def summary(self, rows):
        return self.parse_bytes(response(rows))["summary"]

    def test_containers_are_opaque_in_bare_json_and_fixed_assignment(self):
        marker = "authored-extra-not-reportable"
        raw = response([PREFIX + [{"nested": [marker, {"close": "not a price"}]}, []]])
        wrapped = b" \tkline_dayqfq=" + raw + b";\r\n"
        result = self.parse_bytes(raw)
        self.assertEqual(self.parse_bytes(wrapped), result)
        self.assertEqual(result["summary"]["accepted"], 1)
        self.assertEqual(result["summary"]["parser_version"], daily.OPAQUE_EXTRAS_PARSER)
        self.assertEqual(result["summary"]["source_policy_state"], "unknown")
        self.assertEqual(result["summary"]["normal_composition"], "BLOCKED_SOURCE_RIGHTS")
        self.assertEqual(result["facts"][0]["payload"]["close"], 11)
        self.assertIsNone(result["facts"][0]["payload"]["turnover"])
        self.assertEqual(result["facts"][0]["expires_at"], NOW)
        self.assertNotIn(marker, json.dumps(result))

        legacy = daily.parse(wrapped, SYMBOL, self.window, NOW)
        self.assertEqual(legacy["summary"]["reasons"], {"invalid_row": 1})
        self.assertEqual(legacy["summary"]["parser_version"], daily.PARSER)
        daily.validate_summary(legacy["summary"], self.window)
        with self.assertRaisesRegex(Invalid, "invalid_summary"):
            daily.validate_summary(result["summary"], self.window)

    def test_scalar_only_facts_match_legacy_with_distinct_replay_provenance(self):
        raw = response([PREFIX + ["", 0, False, None]])
        legacy = daily.parse(raw, SYMBOL, self.window, NOW)
        candidate = self.parse_bytes(raw)
        self.assertEqual(candidate["facts"], legacy["facts"])
        self.assertNotEqual(candidate["replay_id"], legacy["replay_id"])

    def test_legacy_rows_helper_keeps_two_argument_default(self):
        item = {"day": [PREFIX + [{}]]}
        raw = json_bytes({"code": 0, "data": {SYMBOL: item, "sz399001": item}})
        self.assertEqual(daily._rows(raw, SYMBOL), (item["day"], "day"))
        with self.assertRaisesRegex(Invalid, "response_schema"):
            self.parse_bytes(raw)

    def test_each_of_first_six_positions_still_rejects_containers(self):
        for index in range(6):
            for container in ([], {}):
                row = PREFIX + [{}]
                row[index] = container
                reason = "invalid_date" if index == 0 else "invalid_row"
                with self.subTest(index=index, container=container):
                    self.assertEqual(self.summary([row])["reasons"], {reason: 1})

    def test_row_width_and_string_limits(self):
        self.assertEqual(self.summary([PREFIX])["accepted"], 1)
        self.assertEqual(self.summary([PREFIX + [0] * 12 + ["x" * 256, ["x" * 256]]])[
            "accepted"], 1)
        for row in (PREFIX[:5], PREFIX + [{}] * 15, PREFIX[:5] + ["x" * 257, []],
                    PREFIX + ["x" * 257, {}], PREFIX + [{"nested": ["x" * 257]}]):
            with self.subTest(width=len(row)):
                self.assertEqual(self.summary([row])["reasons"], {"invalid_row": 1})

    def test_ohlc_remains_positive_bounded_decimal_strings_and_ordered(self):
        for close in ("1e1", "-1", "0", "0.0000000000001", "1000000000000001",
                      "1" * 41, 11, True, None, "13", "8"):
            row = PREFIX + [{}]
            row[2] = close
            with self.subTest(close=close):
                self.assertEqual(self.summary([row])["reasons"], {"invalid_ohlc": 1})

    def test_outside_dates_weekend_and_invalid_date_remain_rejected(self):
        rows = [[day, *PREFIX[1:], {}] for day in
                ("2026-11-05", "2026-11-20", "2026-11-15", "2026-11-32")]
        summary = self.summary(rows)
        self.assertEqual(summary["reasons"], {"outside_window": 2, "not_scheduled_session": 1,
                                              "invalid_date": 1})
        self.assertEqual(summary["latest_returned_session"], "2026-11-20")
        self.assertEqual(summary["accepted"], 0)

    def test_exact_full_row_duplicates_fold_and_trailer_differences_conflict(self):
        row = PREFIX + [{"opaque": [1]}, []]
        same = PREFIX + [{"opaque": [1]}, []]
        summary = self.summary([row, same])
        self.assertEqual((summary["accepted"], summary["duplicate_rows"], summary["rejected"]),
                         (1, 1, 0))
        for different in (PREFIX + [{"opaque": [2]}, []], PREFIX + [{"opaque": [1]}, {}],
                          PREFIX + [{"opaque": [1]}], PREFIX + ["x" * 257]):
            with self.subTest(different=different):
                summary = self.summary([row, same, different])
                self.assertEqual(summary["reasons"], {"conflicting_key": 3})
                self.assertEqual(summary["conflicting_rows"], 3)
                self.assertEqual(summary["duplicate_rows"], 0)
                self.assertEqual(summary["accepted"], 0)

    def test_distinct_number_tokens_conflict_even_when_floats_round_to_same_value(self):
        raw = response([PREFIX + [{"nested": ["FIRST"]}], PREFIX + [{"nested": ["SECOND"]}]])
        for first, second in ((b"9007199254740992.0", b"9007199254740993.0"),
                              (b"1e-400", b"2e-400"), (b"0", b"-0")):
            with self.subTest(first=first, second=second):
                summary = self.parse_bytes(raw.replace(b'"FIRST"', first).replace(
                    b'"SECOND"', second))["summary"]
                self.assertEqual(summary["reasons"], {"conflicting_key": 2})
                self.assertEqual((summary["accepted"], summary["duplicate_rows"]), (0, 0))

    def test_numeric_token_does_not_collide_with_string_or_tag_shaped_array(self):
        raw = response([PREFIX + [{"nested": ["FIRST"]}], PREFIX + [{"nested": ["SECOND"]}]])
        token = b"9007199254740992.0"
        raw = raw.replace(b'"FIRST"', token)
        for other in (json_bytes(token.decode()), json_bytes(["number", token.decode()])):
            with self.subTest(other=other):
                summary = self.parse_bytes(raw.replace(b'"SECOND"', other))["summary"]
                self.assertEqual(summary["reasons"], {"conflicting_key": 2})
                self.assertEqual((summary["accepted"], summary["duplicate_rows"]), (0, 0))

    def test_identical_nested_number_tokens_still_fold(self):
        row = PREFIX + [{"nested": ["TOKEN"]}]
        raw = response([row, row]).replace(b'"TOKEN"', b"9007199254740993.0")
        summary = self.parse_bytes(raw)["summary"]
        self.assertEqual((summary["accepted"], summary["duplicate_rows"], summary["rejected"]),
                         (1, 1, 0))

    def test_data_must_contain_exactly_the_requested_symbol(self):
        item = {"day": [PREFIX + [{}]]}
        for data in ({}, {"sz399001": item}, {SYMBOL: []}, {SYMBOL: item, "sz399001": item},
                     {SYMBOL: item, "unknown": None}):
            with self.subTest(keys=list(data)), self.assertRaisesRegex(Invalid, "response_schema"):
                self.parse_bytes(json_bytes({"code": 0, "data": data}))
        for symbol in ("SH000001", "sh600000", [SYMBOL]):
            with self.subTest(symbol=symbol), self.assertRaisesRegex(Invalid, "unsupported_symbol"):
                self.parse_bytes(response([]), symbol=symbol)

    def test_envelope_rejects_conflicting_identity_and_invalid_status(self):
        data = {SYMBOL: {"day": []}}
        for changes in ({"symbol": "sz399001"}, {"sz399001": {}}, {"msg": {"symbol": SYMBOL}},
                        {"code": False}, {"code": 1}):
            payload = {"code": 0, "data": data} | changes
            with self.subTest(changes=changes), self.assertRaisesRegex(Invalid, "response_schema"):
                self.parse_bytes(b"kline_dayqfq=" + json_bytes(payload) + b";")

    def test_adjustment_ambiguity_fails_closed(self):
        rows = [PREFIX + [{}]]
        for item in ({}, {"day": [], "qfqday": []}, {"day": rows, "qfqday": rows},
                     {"day": 0, "qfqday": rows}):
            with self.subTest(item=item), self.assertRaisesRegex(Invalid, "response_schema"):
                self.parse_bytes(json_bytes({"code": 0, "data": {SYMBOL: item}}))
        valid = {"code": 0, "data": {SYMBOL: {"day": [], "qfqday": rows}}}
        self.assertEqual(self.parse_bytes(json_bytes(valid))["summary"]["adjustment"], "qfqday")

    def test_json_rejects_execution_html_and_non_strict_values_at_any_depth(self):
        raw = response([PREFIX])
        with_trailer = response([PREFIX + ["EXTRA"]])
        for body in (b"<html>" + raw + b"</html>", b"callback(" + raw + b")",
                     b"kline_dayqfq=" + raw + b";alert(1)", b"other=" + raw,
                     b"\v" + raw, b"\xff", b'{"code":0,"data":{"sh000001":{},"sh000001":{}}}',
                     *(with_trailer.replace(b'"EXTRA"', extra) for extra in
                       (b'{"k": 1, "k": 2}', b'[NaN]', b'[Infinity]', b'[1e999]'))):
            with self.subTest(prefix=body[:60]), self.assertRaisesRegex(Invalid, "response_schema"):
                self.parse_bytes(body)

    def test_raw_byte_and_row_limits(self):
        raw = response([PREFIX + [{}]])
        padded = raw.ljust(daily.MAX_BYTES)
        self.assertEqual(self.parse_bytes(padded)["summary"]["accepted"], 1)
        for body in (padded + b" ", bytearray(raw)):
            with self.subTest(size=len(body)), self.assertRaisesRegex(Invalid, "response_size"):
                self.parse_bytes(body)
        self.assertEqual(self.summary([PREFIX + [[]]] * daily.MAX_ROWS)["duplicate_rows"],
                         daily.MAX_ROWS - 1)
        with self.assertRaisesRegex(Invalid, "response_schema"):
            self.summary([PREFIX + [[]]] * (daily.MAX_ROWS + 1))

    def test_nested_depth_and_structure_limits(self):
        trailer = []
        for _ in range(11):
            trailer = [trailer]
        self.assertEqual(self.summary([PREFIX + [trailer]])["accepted"], 1)
        for invalid in ([trailer], [0] * 4097, [[0] * 4096] * 8,
                        {"x" * 2049: 0}, {str(i): 0 for i in range(1025)}):
            with self.subTest(shape=type(invalid)), self.assertRaisesRegex(Invalid, "response_schema"):
                self.summary([PREFIX + [invalid]])

    def test_window_and_clock_remain_validated(self):
        raw = response([PREFIX + [{}]])
        with self.assertRaisesRegex(Invalid, "observation_time"):
            self.parse_bytes(raw, observed_at="2026-11-19T07:59:59Z")
        changed = self.window | {"end_date": "2026-11-20"}
        with self.assertRaisesRegex(Invalid, "plan_changed"):
            daily.parse_opaque_extras(raw, SYMBOL, changed, NOW)


if __name__ == "__main__":
    unittest.main()
