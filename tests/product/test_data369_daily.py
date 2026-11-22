"""Authored payloads only; no provider communication, credentials or data."""

from dataclasses import asdict
import json
import unittest
from urllib.parse import parse_qs, urlsplit

from market_intelligence import data369_daily as provider
from market_intelligence.models import Invalid


SESSIONS = ("2026-06-11", "2026-06-12")
AS_OF = "2026-06-12T08:01:00Z"
SYMBOL = "000001.SH"
EARLY = {"date": SESSIONS[0], "close": 11}
LATEST = {"date": SESSIONS[1], "close": 12}


def payload(rows, *, code="sh000001", adjustment="none", status=200):
    return json.dumps({"code": status, "message": "INJECTED_IGNORED",
                       "data": {"code": code, "adjust": adjustment, "rows": rows}}).encode()


class DailyArchiveTests(unittest.TestCase):
    def inspect(self, body):
        return provider.inspect_archive(body, SYMBOL, SESSIONS, AS_OF)

    def test_exact_symbols_and_bounded_get(self):
        for symbol, vendor in [("000001.SH", "sh000001"), ("399001.SZ", "sz399001"),
                               ("399006.SZ", "sz399006")]:
            with self.subTest(symbol=symbol):
                parsed = urlsplit(provider.request_url(symbol, SESSIONS, AS_OF))
                self.assertEqual((parsed.scheme, parsed.netloc, parsed.path),
                                 ("https", "api.369.top", "/api/a/stock/day_kline"))
                self.assertEqual(parse_qs(parsed.query), {
                    "code": [vendor], "start_date": [SESSIONS[0]], "end_date": [SESSIONS[1]],
                    "limit": ["4"], "fields": ["date,close"],
                })
                result = provider.inspect_archive(payload([EARLY, LATEST], code=vendor),
                                                  symbol, SESSIONS, AS_OF)
                self.assertEqual((result.status, result.accepted_sessions), ("complete_window", 2))

    def test_unsupported_symbols_are_not_coerced(self):
        for symbol in ("000001", "000001.SZ", "sz000001", "sp500", None, []):
            with self.subTest(symbol=symbol), self.assertRaisesRegex(Invalid, "unsupported_symbol"):
                provider.request_url(symbol, SESSIONS, AS_OF)

    def test_cutoff_uses_shanghai_time_and_never_calls_scope_final(self):
        with self.assertRaisesRegex(Invalid, "before_archive_candidate_time"):
            provider.request_url(SYMBOL, SESSIONS, "2026-06-12T07:59:59Z")
        result = provider.request_url(SYMBOL, SESSIONS, "2026-06-12T08:00:00Z")
        self.assertTrue(result.startswith(provider.ENDPOINT))

    def test_malformed_future_reverse_and_unbounded_windows(self):
        for sessions in ([], ("2026-06-11",), SESSIONS[::-1], ("2026-6-11", SESSIONS[1]),
                         ("2026-05-01", SESSIONS[1]), ("2026-06-12", "2026-06-13")):
            with self.subTest(sessions=sessions), self.assertRaises(Invalid):
                provider.request_url(SYMBOL, sessions, AS_OF)

    def test_overflow_in_timestamp_conversion_is_a_closed_failure(self):
        for as_of in ("9999-12-31T20:00:00Z", "0001-01-01T00:00:00+09:00"):
            with self.subTest(as_of=as_of):
                with self.assertRaisesRegex(Invalid, "invalid_as_of"):
                    provider.request_url(SYMBOL, SESSIONS, as_of)
                with self.assertRaisesRegex(Invalid, "invalid_as_of"):
                    provider.inspect_archive(b"{}", SYMBOL, SESSIONS, as_of)

    def test_identical_duplicates_are_deduped_without_exporting_rows(self):
        result = self.inspect(payload([LATEST, EARLY, dict(LATEST, close=12.0)]))
        self.assertEqual(asdict(result), {
            "status": "complete_window", "accepted_sessions": 2, "duplicate_rows": 1,
            "conflicting_rows": 0, "rejected_rows": 0, "missing_sessions": (),
        })

    def test_conflicting_duplicates_reject_the_whole_observation(self):
        result = self.inspect(payload([EARLY, LATEST, dict(LATEST, close=13)]))
        self.assertEqual((result.status, result.accepted_sessions, result.conflicting_rows,
                          result.missing_sessions), ("rejected_window", 1, 2, (SESSIONS[-1],)))

    def test_invalid_duplicate_cannot_hide_acceptance(self):
        for duplicate in (dict(LATEST, close=-1), dict(LATEST, code="sz000001")):
            with self.subTest(duplicate=duplicate):
                result = self.inspect(payload([EARLY, LATEST, duplicate]))
                self.assertEqual((result.status, result.rejected_rows, result.accepted_sessions),
                                 ("rejected_window", 2, 1))

    def test_stale_latest_only_and_empty_responses_are_distinct(self):
        for rows, status in [([EARLY], "stale_window"),
                             ([LATEST], "missing_previous_close"), ([], "empty_window")]:
            with self.subTest(status=status):
                self.assertEqual(self.inspect(payload(rows)).status, status)

    def test_outside_window_and_invalid_row_values_fail_closed(self):
        for row in (dict(LATEST, date="2026-06-13"), {"date": []}, dict(LATEST, close=True),
                    dict(LATEST, close="12"), dict(LATEST, close=0), {}, []):
            with self.subTest(row=row):
                result = self.inspect(payload([EARLY, row]))
                self.assertEqual((result.status, result.rejected_rows), ("rejected_window", 1))

    def test_bad_encoding_size_and_ambiguous_json_have_no_payload_output(self):
        for body, status in [
            (b"", "invalid_body"), (bytearray(b"{}"), "invalid_body"),
            (b" " * (provider.MAX_BODY_BYTES + 1), "invalid_body"), (b"\xff", "invalid_json"),
            (b'{"code": 0, "code": 1}', "invalid_json"),
            (b'{"x": NaN}', "invalid_json"), (b"[" * 3000 + b"]" * 3000, "invalid_json"),
        ]:
            with self.subTest(status=status):
                self.assertEqual(self.inspect(body), provider.ArchiveDiagnostic(
                    status, missing_sessions=SESSIONS))

    def test_provider_message_mismatch_and_wrong_adjustment_do_not_leak(self):
        for body, status in [
            (payload([], status=401), "invalid_envelope"), (payload([], status=False), "invalid_envelope"),
            (payload([], status=0), "invalid_envelope"),
            (payload([], code="sz000001"), "wrong_symbol"),
            (payload([], adjustment="qfq"), "wrong_adjustment"),
            (payload([LATEST] * 5), "invalid_rows"),
        ]:
            with self.subTest(status=status):
                result = self.inspect(body)
                self.assertEqual(result.status, status)
                self.assertNotIn("INJECTED_IGNORED", repr(result))
