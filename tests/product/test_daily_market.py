"""Authored offline daily inputs only: no actual market observations or grants."""

from contextlib import ExitStack, contextmanager
import copy
from datetime import timedelta
import inspect
import unittest
from unittest.mock import patch

from market_intelligence import daily_market as daily
from market_intelligence import delivery, intelligence as api, market_state, publication, research, scenario, workflow
from market_intelligence.models import Invalid, UNIVERSE, canonical, digest, instant, stamp, utc_now
from market_intelligence.source_policy import RIGHTS, PURPOSE

NOW = instant("2020-01-09T00:00:00Z")
SOURCE = "authored_baostock_assertion"
NETWORK = None


def forbidden(*args, **kwargs):
    raise AssertionError("daily replay attempted external I/O, persistence or secret access")


def setUpModule():
    global NETWORK
    NETWORK = ExitStack()
    for name in ("socket.socket", "socket.create_connection", "socket.getaddrinfo", "socket.gethostbyname",
                 "socket.gethostbyname_ex", "socket.gethostbyaddr", "http.client.HTTPConnection.request",
                 "http.client.HTTPSConnection.request", "urllib.request.urlopen", "urllib.request.OpenerDirector.open",
                 "sqlite3.connect", "market_intelligence.store.Store.__init__"):
        NETWORK.enter_context(patch(name, side_effect=forbidden))


def tearDownModule():
    NETWORK.close()


@contextmanager
def pure_only():
    with ExitStack() as stack:
        for name in ("builtins.open", "io.open", "os.open", "os.getenv", "os._Environ.__getitem__",
                     "pathlib.Path.read_bytes", "pathlib.Path.read_text", "pathlib.Path.write_bytes",
                     "pathlib.Path.write_text"):
            stack.enter_context(patch(name, side_effect=forbidden))
        yield


def authored_inputs():
    rows = ["2020-01-08,sh.000001,100,110,90,105,100,1000,10000,5",
            "2020-01-08,sz.399001,200,220,180,210,200,2000,20000,5"]
    body = (",".join(daily.HEADER) + "\n" + "\n".join(rows) + "\n").encode()
    custody = {"source_id": SOURCE, "run_id": "authored-run-1", "request_id": "authored-request-1",
               "first_seen_at": "2020-01-08T07:01:00Z", "retrieved_at": "2020-01-08T07:02:00Z",
               "observed_at": "2020-01-08T07:03:00Z", "published_at": None,
               "expires_at": "2020-01-09T07:01:00Z"}
    declaration = {"schema_version": 1, "version": daily.INPUT_VERSION, "parser_version": daily.PARSER_VERSION,
                   "source_mode": "live_observation", "source_as_of": "2020-01-08T23:00:00Z",
                   "calendar": {"version": "authored-supplied-grid", "sessions": [
                       {"calendar_id": cal, "timezone": "Asia/Shanghai", "session_id": "2020-01-08",
                        "close_at": "2020-01-08T15:00:00+08:00"} for cal in ("SSE", "SZSE")]},
                   "custody": custody,
                   "hashes": {"normalized_csv_sha256": digest(body), "original_response_sha256": digest(b"authored absent raw response")},
                   "transport": {key: custody[key] for key in ("source_id", "run_id", "request_id")} |
                                {"locator": daily.TRANSPORT, "protocol": "tcp", "tls": False, "peer_authenticated": False},
                   "documentation_reference": {"source_id": SOURCE, "locator": daily.DOCUMENT, "locator_role": daily.DOC_ROLE},
                   "engine_locator": {"source_id": SOURCE, "locator": daily.ENGINE_LOCATOR,
                                      "locator_prefix": daily.ENGINE_PREFIX, "locator_role": daily.ENGINE_ROLE}}
    policy = {"schema_version": 1, "version": "authored-daily-rights-v1", "sources": [{
        "source_id": SOURCE, "synthetic": False, "purpose": PURPOSE, "origin": "https://www.baostock.com",
        "locator_prefix": daily.ENGINE_PREFIX,
        "retention": {"class": "normalized_only", "max_age_seconds": 86400, "basis_refs": [daily.DOCUMENT]},
        "rights": {name: {"state": ("permitted" if name in {"normalized_retention", "derived_analysis"}
                                    else "denied" if name == "raw_retention" else "unknown"),
                          "basis_refs": [daily.DOCUMENT]} for name in RIGHTS}}]}
    methods = {"schema_version": 2, "version": "authored-daily-method-v1", "profile_version": "authored-profile-v1",
               "universe_id": "authored-fixed-five-benchmarks", "benchmark_symbols": list(UNIVERSE),
               "constituents": [], "lookback_sessions": 1, "news_half_life_seconds": 86400,
               "news_ttl_seconds": 86400, "approach_seconds": 86400, "confirmation_grace_seconds": 21600,
               "event_ttl_seconds": 604800,
               "flow": {"provider": "unconfigured", "series_kind": "provider_estimate", "enabled": False},
               "sources": [{"source_id": SOURCE, "tier": "B", "locator_prefix": daily.ENGINE_PREFIX,
                            "scopes": ["market"], "event_ids": [], "synthetic": False}],
               "rules": [{"domain": domain, "validator": domain + "-p2-v1", "required": domain != "flow",
                          "allow_degraded": domain == "flow"}
                         for domain in ("market", "sector", "news", "entity", "event", "flow")]}
    entities = {"schema_version": 2, "version": "authored-daily-mapping-v1",
                "mapping_refs": [daily.ENGINE_LOCATOR], "entities": []}
    for code, symbol in daily.SYMBOLS.items():
        entities["entities"].append({"entity_id": symbol, "provider_symbol": code, "aliases": [symbol],
                                     "sector_id": "benchmark", "calendar_id": "SSE" if code.startswith("sh.") else "SZSE",
                                     "currency": "CNY", "unit": "index_points", "provider": SOURCE,
                                     "publication_delay_seconds": None})
    return body, declaration, methods, entities, policy


class DailyMarketTests(unittest.TestCase):
    def setUp(self):
        self.body, self.declaration, self.methods, self.entities, self.policy = authored_inputs()
        self.clock = self.enterContext(patch.object(api, "utc_now", return_value=NOW))

    def args(self):
        return self.body, self.declaration, self.methods, self.entities, self.policy

    def build(self):
        with pure_only():
            return daily.build_daily_replay_bundle(*self.args())

    def verify(self, bundle):
        with pure_only():
            return daily.verify_daily_replay_bundle(bundle, *self.args())

    def csv(self, body):
        self.body = body
        self.declaration["hashes"]["normalized_csv_sha256"] = digest(body)

    def rows(self, rows):
        self.csv((",".join(daily.HEADER) + "\n" + "\n".join(rows) + ("\n" if rows else "")).encode())

    def edit_cell(self, name, value, index=0):
        rows = self.body.decode().splitlines()[1:]
        fields = rows[index].split(",")
        fields[daily.HEADER.index(name)] = value
        rows[index] = ",".join(fields)
        self.rows(rows)

    @contextmanager
    def advance_after(self, target, name, when):
        original = getattr(target, name)

        def delayed(*args, **kwargs):
            result = original(*args, **kwargs)
            self.clock.return_value = when
            return result

        with patch.object(target, name, side_effect=delayed) as mocked:
            yield mocked

    def test_positive_two_values_reach_existing_engine(self):
        with patch.object(api, "_evaluate_market", wraps=api._evaluate_market) as evaluated:
            bundle = self.build()
        self.assertEqual(evaluated.call_count, 1)
        self.assertEqual(len(evaluated.call_args.args[0]), 2)
        content = bundle["content"]
        benchmarks = content["results"]["market"]["benchmarks"]
        self.assertEqual([row["value"] for row in benchmarks], [105.0, 210.0, None, None, None])
        self.assertEqual(content["input_market_coverage"], {"covered": 2, "expected": 5,
                                                         "missing": ["399006.SZ", "KOSPI", "SP500"]})
        self.assertEqual(content["domains"]["market"]["covered"], 2)
        self.assertEqual(content["status"], "BLOCK")
        self.assertTrue(self.verify(bundle))

    def test_no_operational_or_truth_promotion(self):
        content = self.build()["content"]
        self.assertEqual(content["live_market_coverage"], {"covered": 0, "expected": 5, "missing": list(UNIVERSE)})
        self.assertEqual(content["readiness"], "BLOCKED_EXTERNAL_PREREQUISITES")
        for key in ("permission_grant", "acquisition_verified", "legal_entitlement_verified", "finality_verified",
                    "calendar_completeness_verified", "original_response_hash_verified", "historical_receipts_reinterpreted"):
            self.assertIs(content[key], False, key)
        for key in ("new_live_runs", "network_requests", "runtime_store_reads"):
            self.assertEqual(content[key], 0)
        self.assertFalse(any(content["operations_enabled"].values()))
        self.assertEqual(content["provenance_scope"], "caller_supplied_only_not_acquisition_proof")

    def test_no_mutation_or_raw_csv_output(self):
        original = copy.deepcopy(self.args())
        bundle = self.build()
        self.verify(bundle)
        self.assertEqual(self.args(), original)
        self.assertNotIn(self.body.decode().splitlines()[1], canonical(bundle))
        self.assertNotIn("facts", bundle["content"])
        self.assertTrue(all("payload" not in row for row in bundle["content"]["provenance"]))

    def test_unknown_publication_and_original_custody_preserved(self):
        content = self.build()["content"]
        self.assertEqual(content["declaration"], self.declaration)
        self.assertEqual(content["source_mode"], "live_observation")
        self.assertEqual(content["processing_mode"], "historical_replay")
        for row in content["provenance"]:
            for key in ("published_at", "first_seen_at", "retrieved_at", "observed_at", "expires_at"):
                self.assertEqual(row[key], self.declaration["custody"][key])
        self.assertTrue(all(row["publication_delay_seconds"] is None for row in self.entities["entities"]))

    def test_every_projected_location_has_role_including_rights_basis(self):
        for right in self.policy["sources"][0]["rights"].values():
            right["basis_refs"] = [daily.ENGINE_LOCATOR]
        bundle = self.build()
        found = []

        def inspect_node(value):
            if type(value) is dict:
                for key, item in value.items():
                    if item == daily.ENGINE_LOCATOR:
                        found.append(key)
                        self.assertEqual(value.get("locator_role"), daily.ENGINE_ROLE)
                    inspect_node(item)
            elif type(value) is list:
                for item in value:
                    inspect_node(item)

        inspect_node(bundle)
        self.assertGreaterEqual(len(found), 5)
        self.assertTrue(self.verify(bundle))

    def test_nonblocked_references_also_have_projection_role(self):
        for rule in self.methods["rules"]:
            rule.update(required=False, allow_degraded=True)
        bundle = self.build()
        self.assertEqual(bundle["content"]["status"], "DEGRADED")
        self.assertEqual(len(bundle["content"]["references"]), 2)
        for row in bundle["content"]["references"]:
            self.assertEqual(row["locator_role"], daily.ENGINE_ROLE)
            self.assertIsNone(row["published_at"])
        self.assertTrue(self.verify(bundle))

    def test_old_session_observed_now_is_not_current_market(self):
        self.clock.side_effect = utc_now
        now = utc_now()
        custody = self.declaration["custody"]
        for key in ("first_seen_at", "retrieved_at", "observed_at"):
            custody[key] = stamp(now - timedelta(minutes=1))
        custody["expires_at"] = stamp(now + timedelta(hours=1))
        self.declaration["source_as_of"] = stamp(now)
        bundle = self.build()
        content = bundle["content"]
        for row in content["results"]["market"]["benchmarks"][:2]:
            self.assertEqual(row["session_id"], "2020-01-08")
            self.assertEqual(row["current_market_freshness"], "unverified")
            self.assertIs(row["calendar_completeness_verified"], False)
        self.assertIn("latest_declared_session_not_verified_current_quote", content["warnings"])
        self.assertTrue(self.verify(bundle))

    def test_later_declared_session_does_not_relabel_old_value(self):
        self.declaration["calendar"]["sessions"].append({"calendar_id": "SSE", "timezone": "Asia/Shanghai",
                                                       "session_id": "2020-01-09", "close_at": "2020-01-09T15:00:00+08:00"})
        self.clock.return_value = instant("2020-01-09T08:00:00Z")
        self.declaration["source_as_of"] = "2020-01-09T07:30:00Z"
        self.policy["sources"][0]["retention"].update({"class": "reprocessable_raw", "max_age_seconds": 172800})
        self.declaration["custody"]["expires_at"] = "2020-01-10T07:00:00Z"
        row = self.build()["content"]["results"]["market"]["benchmarks"][0]
        self.assertEqual(row["session_id"], "2020-01-09")
        self.assertIsNone(row["value"])

    def test_unknown_publication_cannot_be_backfilled(self):
        self.declaration["custody"]["published_at"] = self.declaration["custody"]["retrieved_at"]
        with self.assertRaises(Invalid):
            self.build()

    def test_publication_delay_must_stay_unknown(self):
        for delay in (0, 9000, 63000):
            self.entities["entities"][0]["publication_delay_seconds"] = delay
            with self.subTest(delay=delay), self.assertRaises(Invalid):
                self.build()

    def test_benchmark_turnover_and_missing_domains_not_fabricated(self):
        content = self.build()["content"]
        self.assertIsNone(content["results"]["market"]["turnover"]["value"])
        self.assertIsNone(content["results"]["flow"]["value"])
        self.assertFalse(content["results"]["events"]["empty_day"])
        for domain in ("market", "sector", "news", "entity", "event"):
            self.assertEqual(content["domains"][domain]["status"], "BLOCK")

    def test_empty_success_has_zero_observation_gap(self):
        self.rows([])
        content = self.build()["content"]
        self.assertEqual(content["parse"], {"total": 0, "accepted": 0, "rejected": 0, "reasons": {}, "rows": [],
                                            "outcome": "zero_eligible_observations"})
        self.assertEqual(content["input_market_coverage"]["covered"], 0)
        self.assertEqual(content["status"], "BLOCK")

    def test_exact_header_and_encoding_required(self):
        original = self.body
        for body in (b"", original.replace(b"pctChg", b"pctchg"), original.replace(b"date,code", b"code,date"),
                     b"\xef\xbb\xbf" + original, original + b"\xff", original.replace(b"volume", b"frequency")):
            self.csv(body)
            with self.subTest(body=body[:30]), self.assertRaises(Invalid):
                self.build()

    def test_malformed_structure_rejected(self):
        header = ",".join(daily.HEADER).encode() + b"\n"
        for suffix in (b'"unclosed\n', b'a,b\n', b'\n', b'a,b,c,d,e,f,g,h,i,j,k\n', b'\x00\n',
                       b'"a\nb",c,d,e,f,g,h,i,j,k\n', b'"a"suffix,b,c,d,e,f,g,h,i,j\n'):
            self.csv(header + suffix)
            with self.subTest(suffix=suffix), self.assertRaises(Invalid):
                self.build()

    def test_byte_limit_and_exact_bytes_type(self):
        for body in (b"x" * (daily.MAX_BYTES + 1), bytearray(self.body), self.body.decode(), None):
            with self.subTest(kind=type(body)), pure_only(), self.assertRaises(Invalid):
                daily.build_daily_replay_bundle(body, self.declaration, self.methods, self.entities, self.policy)

    def test_row_limit_exact_and_overflow(self):
        row = self.body.decode().splitlines()[1]
        self.rows([row] * 1024)
        parsed = self.build()["content"]["parse"]
        self.assertEqual((parsed["total"], parsed["accepted"], parsed["rejected"]), (1024, 0, 1024))
        self.rows([row] * 1025)
        with self.assertRaises(Invalid):
            self.build()

    def test_exact_byte_limit_is_supported(self):
        header = ",".join(daily.HEADER) + "\n"
        row = "2020-01-08,sh.000001," + ",".join(["0" * 128] * 8) + "\n"
        rows = [row] * ((daily.MAX_BYTES - len(header)) // len(row))
        remaining = daily.MAX_BYTES - len(header) - sum(map(len, rows))
        # Redistribute a short remainder into the preceding bounded row.
        if remaining < 30:
            rows.pop()
            remaining += len(row)
            first_size = remaining // 2
            remaining -= first_size
            sizes = [first_size, remaining]
        else:
            sizes = [remaining]
        for size in sizes:
            cells = ["0"] * 8
            extra = size - len("2020-01-08,sh.000001," + ",".join(cells) + "\n")
            for index in range(8):
                added = min(extra, 127)
                cells[index] += "0" * added
                extra -= added
            self.assertEqual(extra, 0)
            rows.append("2020-01-08,sh.000001," + ",".join(cells) + "\n")
        self.csv((header + "".join(rows)).encode())
        self.assertEqual(len(self.body), daily.MAX_BYTES)
        parsed = self.build()["content"]["parse"]
        self.assertEqual(parsed["rejected"], len(rows))
        self.assertEqual(parsed["accepted"], 0)

    def test_field_limit_exact_and_overflow(self):
        self.edit_cell("code", "x" * 128)
        self.assertEqual(self.build()["content"]["parse"]["rejected"], 1)
        self.edit_cell("code", "x" * 129)
        with self.assertRaises(Invalid):
            self.build()

    def test_csv_hash_mismatch_fails_before_parse(self):
        self.declaration["hashes"]["normalized_csv_sha256"] = "0" * 64
        with patch.object(daily, "_csv_rows", side_effect=forbidden), self.assertRaisesRegex(Invalid, "hash mismatch"):
            self.build()

    def test_original_hash_remains_asserted_not_verified(self):
        self.declaration["hashes"]["original_response_sha256"] = "0" * 64
        content = self.build()["content"]
        self.assertIs(content["original_response_hash_verified"], False)
        self.assertIs(content["normalized_csv_hash_verified"], True)
        self.assertIn("original_response_hash_unverified", content["warnings"])

    def test_decimal_rejections_and_finite_bounds(self):
        original = self.body
        for value in ("nan", "NaN", "inf", "-1", "0", "1e3", " 100", "100 ", "1_000", "1000000000000001",
                      "0.00000000000099999999999", "1000000000000000.0000001", "\u0661"):
            self.csv(original)
            self.edit_cell("close", value)
            with self.subTest(value=value):
                parsed = self.build()["content"]["parse"]
                self.assertEqual(parsed["reasons"], {"invalid_daily_values": 1})
                self.assertEqual(parsed["accepted"], 1)

    def test_all_numeric_fields_checked(self):
        original = self.body
        for name in daily.HEADER[2:]:
            self.csv(original)
            self.edit_cell(name, "NaN")
            with self.subTest(name=name):
                self.assertEqual(self.build()["content"]["parse"]["rejected"], 1)

    def test_negative_volume_amount_and_inconsistent_ohlc(self):
        original = self.body
        for field, value in (("volume", "-1"), ("amount", "-1"), ("low", "106"), ("high", "104"), ("pctChg", "-101")):
            self.csv(original)
            self.edit_cell(field, value)
            with self.subTest(field=field):
                self.assertEqual(self.build()["content"]["parse"]["rejected"], 1)

    def test_decimal_bounds_do_not_round_before_validation(self):
        original = self.body
        for field, value in (("volume", "-0." + "0" * 110 + "1"),
                             ("amount", "-0." + "0" * 110 + "1"),
                             ("amount", "1000000000000000." + "0" * 100 + "1"),
                             ("pctChg", "1000000000000000." + "0" * 100 + "1")):
            self.csv(original)
            self.edit_cell(field, value)
            with self.subTest(field=field, value=value):
                self.assertEqual(self.build()["content"]["parse"]["reasons"], {"invalid_daily_values": 1})

    def test_zero_aggregates_and_negative_change_are_valid(self):
        self.edit_cell("volume", "0")
        self.edit_cell("amount", "0")
        self.edit_cell("close", "95")
        self.edit_cell("pctChg", "-5")
        content = self.build()["content"]
        self.assertEqual(content["parse"]["accepted"], 2)
        self.assertEqual(content["results"]["market"]["benchmarks"][0]["value"], 95)

    def test_duplicate_entire_group_rejected(self):
        rows = self.body.decode().splitlines()[1:]
        self.rows([rows[0], rows[0], rows[1]])
        parsed = self.build()["content"]["parse"]
        self.assertEqual((parsed["total"], parsed["accepted"], parsed["rejected"]), (3, 1, 2))
        self.assertEqual(parsed["reasons"], {"duplicate_code_date": 2})
        self.assertTrue(all(row["fact_id"] is None for row in parsed["rows"][:2]))

    def test_conflicting_entire_group_rejected(self):
        rows = self.body.decode().splitlines()[1:]
        self.rows([rows[0], rows[0].replace(",105,", ",106,"), rows[1]])
        content = self.build()["content"]
        self.assertEqual(content["parse"]["reasons"], {"conflicting_code_date": 2})
        self.assertIsNone(content["results"]["market"]["benchmarks"][0]["value"])

    def test_invalid_numeric_twin_rejects_good_row_too(self):
        rows = self.body.decode().splitlines()[1:]
        self.rows([rows[0], rows[0].replace(",105,", ",NaN,"), rows[1]])
        content = self.build()["content"]
        parsed = content["parse"]
        self.assertEqual((parsed["total"], parsed["accepted"], parsed["rejected"]), (3, 1, 2))
        self.assertEqual(sum(parsed["reasons"].values()), parsed["rejected"])
        self.assertEqual(parsed["reasons"], {"conflicting_code_date": 2})
        self.assertIsNone(content["results"]["market"]["benchmarks"][0]["value"])

    def test_unsupported_and_unverified_symbols_not_substituted(self):
        original = self.body
        for code, reason in (("sz.399006", "unverified_symbol"), ("sh.510300", "unsupported_symbol"),
                             ("000001.SH", "unsupported_symbol"), ("SH.000001", "unsupported_symbol")):
            self.csv(original)
            self.edit_cell("code", code)
            with self.subTest(code=code):
                self.assertEqual(self.build()["content"]["parse"]["reasons"], {reason: 1})
        self.assertEqual(tuple(UNIVERSE), ("000001.SH", "399001.SZ", "399006.SZ", "KOSPI", "SP500"))

    def test_dates_are_exact_session_dates_not_instants(self):
        original = self.body
        for value in ("2020-1-08", "20200108", "2020-02-30", "2020-01-08T15:00:00+08:00"):
            self.csv(original)
            self.edit_cell("date", value)
            with self.subTest(value=value):
                self.assertEqual(self.build()["content"]["parse"]["reasons"], {"invalid_session_date": 1})

    def test_missing_calendar_is_not_inferred(self):
        self.declaration["calendar"]["sessions"] = []
        content = self.build()["content"]
        self.assertEqual(content["parse"]["reasons"], {"unavailable_calendar": 2})
        self.assertEqual(content["input_market_coverage"]["covered"], 0)

    def test_calendar_conflicts_and_wrong_offsets_rejected(self):
        original = copy.deepcopy(self.declaration)
        for kind in ("duplicate", "wrong_offset", "wrong_date", "wrong_market"):
            self.declaration = copy.deepcopy(original)
            sessions = self.declaration["calendar"]["sessions"]
            if kind == "duplicate":
                sessions.append(copy.deepcopy(sessions[0]))
            elif kind == "wrong_offset":
                sessions[0]["close_at"] = "2020-01-08T15:00:00Z"
            elif kind == "wrong_date":
                sessions[0]["close_at"] = "2020-01-09T15:00:00+08:00"
            else:
                sessions[0]["calendar_id"] = "KRX"
            with self.subTest(kind=kind), self.assertRaises(Invalid):
                self.build()

    def test_close_after_original_possession_rejected(self):
        self.declaration["calendar"]["sessions"][0]["close_at"] = "2020-01-08T15:02:00+08:00"
        parsed = self.build()["content"]["parse"]
        self.assertEqual(parsed["reasons"], {"session_not_closed_at_possession": 1})

    def test_future_session_rejected(self):
        self.edit_cell("date", "2020-01-10")
        self.declaration["calendar"]["sessions"].append({"calendar_id": "SSE", "timezone": "Asia/Shanghai",
                                                       "session_id": "2020-01-10", "close_at": "2020-01-10T15:00:00+08:00"})
        self.assertEqual(self.build()["content"]["parse"]["reasons"], {"session_not_closed_at_possession": 1})

    def test_missing_mapping_remains_gap(self):
        self.entities["entities"] = []
        parsed = self.build()["content"]["parse"]
        self.assertEqual(parsed["reasons"], {"unconfigured_mapping": 2})

    def test_mapping_identity_provider_currency_and_unit_guarded(self):
        original = copy.deepcopy(self.entities)
        for field, value in (("provider_symbol", "sz.399006"), ("provider_symbol", "sz.399001"),
                             ("provider", "other"), ("currency", "USD"), ("unit", "currency_units"),
                             ("calendar_id", "SZSE"), ("entity_id", "399006.SZ")):
            self.entities = copy.deepcopy(original)
            self.entities["entities"][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(Invalid):
                self.build()

    def test_fixed_universe_cannot_be_reduced(self):
        self.methods["benchmark_symbols"] = list(UNIVERSE[:2])
        with self.assertRaises(Invalid):
            self.build()

    def test_source_authority_c_cannot_supply_values(self):
        self.methods["sources"][0]["tier"] = "C"
        parsed = self.build()["content"]["parse"]
        self.assertEqual(parsed["reasons"], {"source_authority_scope": 2})

    def test_source_scopes_and_registry_must_match(self):
        self.methods["sources"][0]["scopes"] = ["news"]
        with self.assertRaises(Invalid):
            self.build()

    def test_source_and_request_ids_cannot_be_swapped(self):
        original = copy.deepcopy(self.declaration)
        for section, key in (("transport", "source_id"), ("transport", "request_id"), ("transport", "run_id"),
                             ("documentation_reference", "source_id"), ("engine_locator", "source_id"),
                             ("custody", "source_id")):
            self.declaration = copy.deepcopy(original)
            self.declaration[section][key] = "another-identity"
            with self.subTest(section=section, key=key), self.assertRaises(Invalid):
                self.build()

    def test_exact_transport_no_tls_or_authentication_forgery(self):
        original = copy.deepcopy(self.declaration)
        for key, value in (("locator", daily.ENGINE_LOCATOR), ("locator", daily.TRANSPORT + "/"),
                           ("locator", "tcp://other.invalid:10030"), ("protocol", "https"),
                           ("tls", True), ("tls", 0), ("peer_authenticated", True), ("peer_authenticated", 0)):
            self.declaration = copy.deepcopy(original)
            self.declaration["transport"][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(Invalid):
                self.build()

    def test_documentation_and_projection_roles_required(self):
        original = copy.deepcopy(self.declaration)
        for section in ("documentation_reference", "engine_locator"):
            for key, value in (("locator", daily.TRANSPORT), ("locator_role", "acquisition_endpoint")):
                self.declaration = copy.deepcopy(original)
                self.declaration[section][key] = value
                with self.subTest(section=section, key=key), self.assertRaises(Invalid):
                    self.build()
            self.declaration = copy.deepcopy(original)
            del self.declaration[section]["locator_role"]
            with self.subTest(section=section, missing=True), self.assertRaises(Invalid):
                self.build()

    def test_policy_origin_prefix_and_source_identity_bound(self):
        original = copy.deepcopy(self.policy)
        for key, value in (("source_id", "other"), ("origin", "https://other.invalid"),
                           ("locator_prefix", "https://www.baostock.com/other/"),
                           ("locator_prefix", "https://www.baostock.com/")):
            self.policy = copy.deepcopy(original)
            self.policy["sources"][0][key] = value
            with self.subTest(key=key), self.assertRaises(Invalid):
                self.build()

    def test_applicable_rights_required_even_with_empty_or_rejected_rows(self):
        original = copy.deepcopy(self.policy)
        for name in ("normalized_retention", "derived_analysis"):
            for state in ("unknown", "denied"):
                for empty in (False, True):
                    self.policy = copy.deepcopy(original)
                    self.policy["sources"][0]["rights"][name]["state"] = state
                    if empty:
                        self.rows([])
                    with self.subTest(name=name, state=state, empty=empty), self.assertRaises(Invalid):
                        self.build()

    def test_future_rights_do_not_enable_operations(self):
        for name in ("automated_retrieval", "external_model_input", "private_delivery", "public_redistribution"):
            self.policy["sources"][0]["rights"][name]["state"] = "permitted"
        content = self.build()["content"]
        self.assertEqual(content["source_policy_decisions"][0]["state"], "permitted")
        self.assertFalse(any(content["operations_enabled"].values()))
        self.assertIs(content["legal_entitlement_verified"], False)

    def test_custody_time_ordering_and_aware_instants(self):
        original = copy.deepcopy(self.declaration)
        for field, value in (("first_seen_at", "2020-01-08T07:03:00Z"),
                             ("retrieved_at", "2020-01-08T07:04:00Z"),
                             ("observed_at", "2020-01-09T01:00:00Z"),
                             ("observed_at", "2020-01-08T07:01:00Z"),
                             ("retrieved_at", "2020-01-08T07:02:00"), ("expires_at", None)):
            self.declaration = copy.deepcopy(original)
            self.declaration["custody"][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(Invalid):
                self.build()

    def test_future_as_of_cannot_reach_payload_hash_or_parse(self):
        self.declaration["source_as_of"] = "2020-01-10T00:00:00Z"
        with patch.object(daily, "digest", side_effect=forbidden), patch.object(daily, "_csv_rows", side_effect=forbidden), \
                self.assertRaisesRegex(Invalid, "precedes"):
            self.build()

    def test_expired_even_if_every_row_rejected_or_empty(self):
        self.declaration["custody"]["expires_at"] = stamp(NOW)
        original = self.body
        for body in (original, b",".join(name.encode() for name in daily.HEADER) + b"\n",
                     original.replace(b"sh.000001", b"sz.399006").replace(b"sz.399001", b"sz.399006")):
            self.csv(body)
            with self.subTest(body=body[:20]), patch.object(daily, "digest", side_effect=forbidden), \
                    patch.object(daily, "_csv_rows", side_effect=forbidden), \
                    patch.object(api, "_evaluate_market", side_effect=forbidden), self.assertRaisesRegex(Invalid, "expired"):
                self.build()

    def test_retention_duration_and_expiry_before_retrieval_rejected(self):
        for expiry in ("2020-01-10T07:01:00Z", "2020-01-08T07:02:00Z"):
            self.declaration["custody"]["expires_at"] = expiry
            with self.subTest(expiry=expiry), self.assertRaises(Invalid):
                self.build()

    def test_closed_declaration_schemas(self):
        original = copy.deepcopy(self.declaration)
        for path in ((), ("custody",), ("transport",), ("hashes",), ("engine_locator",),
                     ("documentation_reference",), ("calendar",), ("calendar", "sessions", 0)):
            self.declaration = copy.deepcopy(original)
            target = self.declaration
            for key in path:
                target = target[key]
            target["frequency"] = "minute"
            with self.subTest(path=path), self.assertRaises(Invalid):
                self.build()

    def test_wrong_input_versions_modes_and_types(self):
        original = copy.deepcopy(self.declaration)
        for key, value in (("schema_version", True), ("schema_version", 2), ("version", "future"),
                           ("parser_version", "minute-v1"), ("source_mode", "synthetic_test"),
                           ("source_mode", "historical_replay"), ("source_mode", {}), ("calendar", [])):
            self.declaration = copy.deepcopy(original)
            self.declaration[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(Invalid):
                self.build()

    def test_public_api_has_no_clock_loader_or_mode_switch(self):
        names = ["csv_bytes", "declaration", "methods", "entities", "source_policy"]
        self.assertEqual(list(inspect.signature(daily.build_daily_replay_bundle).parameters), names)
        self.assertEqual(list(inspect.signature(daily.verify_daily_replay_bundle).parameters), ["bundle", *names])
        self.assertFalse(hasattr(daily, "main"))
        self.assertFalse(hasattr(daily, "load_inputs"))

    def test_system_clock_used_when_not_mocked(self):
        self.clock.side_effect = utc_now
        now = utc_now()
        for key in ("first_seen_at", "retrieved_at", "observed_at"):
            self.declaration["custody"][key] = stamp(now - timedelta(seconds=1))
        self.declaration["custody"]["expires_at"] = stamp(now + timedelta(minutes=1))
        self.declaration["source_as_of"] = stamp(now)
        processing = self.build()["content"]["processing"]
        self.assertLessEqual(now, instant(processing["started_at"]))
        self.assertLessEqual(instant(processing["completed_at"]), utc_now())

    def test_repeat_verify_uses_recorded_processing_not_fresh_identity(self):
        bundle = self.build()
        self.clock.return_value = NOW + timedelta(hours=1)
        self.assertTrue(self.verify(bundle))
        self.assertNotEqual(self.build()["bundle_id"], bundle["bundle_id"])

    def test_rehashed_schema_counts_results_and_provenance_tampering(self):
        bundle = self.build()
        changes = [(('schema',), 'private-real-evidence-replay'), (('schema_version',), True),
                   (('parse', 'accepted'), 0), (('parse', 'rows', 0, 'fact_id'), '0' * 64),
                   (('provenance', 0, 'locator_role'), 'acquisition_endpoint'),
                   (('declaration', 'transport', 'locator'), daily.ENGINE_LOCATOR),
                   (('declaration', 'transport', 'tls'), True),
                   (('declaration', 'custody', 'request_id'), 'forged-request'),
                   (('declaration', 'hashes', 'original_response_sha256'), '0' * 64),
                   (('results', 'market', 'benchmarks', 0, 'value'), 999),
                   (('results', 'market', 'benchmarks', 0, 'session_id'), '2020-01-09'),
                   (('permission_grant',), True), (('original_response_hash_verified',), True),
                   (('current_market_freshness',), 'verified'), (('calendar_completeness_verified',), True),
                   (('warnings',), []), (('source_policy_decisions', 0, 'permission_grant'), True),
                   (('fingerprints', 'declaration'), '0' * 64), (('operations_enabled', 'private_delivery'), True)]
        for path, value in changes:
            forged = copy.deepcopy(bundle)
            target = forged["content"]
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            forged["bundle_id"] = digest(forged["content"])
            with self.subTest(path=path), self.assertRaises(Invalid):
                self.verify(forged)

    def test_rehashed_projected_role_removal_rejected(self):
        bundle = self.build()
        for path in (("provenance", 0), ("declaration", "engine_locator"), ("declaration", "documentation_reference")):
            forged = copy.deepcopy(bundle)
            target = forged["content"]
            for key in path:
                target = target[key]
            del target["locator_role"]
            forged["bundle_id"] = digest(forged["content"])
            with self.subTest(path=path), self.assertRaises(Invalid):
                self.verify(forged)

    def test_changed_input_bytes_and_configuration_reject_existing_bundle(self):
        bundle = self.build()
        original = copy.deepcopy(self.args())
        for changed in ("csv", "declaration", "methods", "entities", "policy"):
            self.body, self.declaration, self.methods, self.entities, self.policy = copy.deepcopy(original)
            if changed == "csv":
                self.edit_cell("amount", "10001")
            elif changed == "declaration":
                self.declaration["custody"]["run_id"] = "authored-run-2"
                self.declaration["transport"]["run_id"] = "authored-run-2"
            elif changed == "methods":
                self.methods["version"] = "changed-method"
            elif changed == "entities":
                self.entities["version"] = "changed-mapping"
            else:
                self.policy["sources"][0]["rights"]["private_delivery"]["state"] = "permitted"
            with self.subTest(changed=changed), self.assertRaises(Invalid):
                self.verify(bundle)

    def test_processing_tampering_and_current_expiry(self):
        bundle = self.build()
        for key, value in (("clock", "caller"), ("started_at", "2019-01-01T00:00:00Z"),
                           ("completed_at", "2030-01-01T00:00:00Z")):
            forged = copy.deepcopy(bundle)
            forged["content"]["processing"][key] = value
            forged["content"]["fingerprints"]["processing"] = digest(forged["content"]["processing"])
            forged["bundle_id"] = digest(forged["content"])
            with self.subTest(key=key), self.assertRaises(Invalid):
                self.verify(forged)
        self.clock.return_value = instant(self.declaration["custody"]["expires_at"])
        with self.assertRaisesRegex(Invalid, "expired"):
            self.verify(bundle)

    def test_expiry_and_rollback_during_policy_work_prevent_payload_use(self):
        bundle = self.build()
        deadline = instant(self.declaration["custody"]["expires_at"])
        for verify in (False, True):
            for when, reason in ((deadline, "expired"), (NOW - timedelta(seconds=1), "rollback")):
                self.clock.return_value = NOW
                with self.subTest(verify=verify, reason=reason), \
                        self.advance_after(daily, "assess_source_policy", when), \
                        patch.object(daily, "digest", side_effect=forbidden), \
                        patch.object(daily, "_csv_rows", side_effect=forbidden), \
                        patch.object(api, "_evaluate_market", side_effect=forbidden), self.assertRaisesRegex(Invalid, reason):
                    self.verify(bundle) if verify else self.build()

    def test_guard_after_csv_hash_before_parse(self):
        original = daily.digest
        deadline = instant(self.declaration["custody"]["expires_at"])

        def delayed(value):
            result = original(value)
            if type(value) is bytes:
                self.clock.return_value = deadline
            return result

        with patch.object(daily, "digest", side_effect=delayed), patch.object(daily, "_csv_rows", side_effect=forbidden), \
                self.assertRaisesRegex(Invalid, "expired"):
            self.build()

    def test_guard_after_calendar_validation_before_row_parsing(self):
        deadline = instant(self.declaration["custody"]["expires_at"])
        with self.advance_after(daily.Calendar, "from_dict", deadline), \
                patch.object(daily, "_csv_rows", side_effect=forbidden), self.assertRaisesRegex(Invalid, "expired"):
            self.build()

    def test_guard_after_fact_validation_before_domain_use(self):
        deadline = instant(self.declaration["custody"]["expires_at"])
        with self.advance_after(api.Fact, "from_dict", deadline), \
                patch.object(api, "_evaluate_market", side_effect=forbidden), self.assertRaisesRegex(Invalid, "expired"):
            self.build()

    def test_shared_engine_guard_after_method_validation(self):
        deadline = instant(self.declaration["custody"]["expires_at"])
        with self.advance_after(market_state, "_validate_methods", deadline), \
                patch.object(market_state, "rounded", side_effect=forbidden), self.assertRaisesRegex(Invalid, "expired"):
            self.build()

    def test_guard_after_parser_and_before_composition(self):
        deadline = instant(self.declaration["custody"]["expires_at"])
        with self.advance_after(daily, "_parse", deadline), \
                patch.object(api, "_compose_observed_daily", side_effect=forbidden), self.assertRaisesRegex(Invalid, "expired"):
            self.build()

    def test_last_guard_after_serialization_for_build_and_verify(self):
        bundle = self.build()
        deadline = instant(self.declaration["custody"]["expires_at"])
        for verify in (False, True):
            for when, reason in ((deadline, "expired"), (NOW - timedelta(seconds=1), "rollback")):
                self.clock.return_value = NOW
                with self.subTest(verify=verify, reason=reason), self.advance_after(api, "_replay_bundle", when), \
                        self.assertRaisesRegex(Invalid, reason):
                    self.verify(bundle) if verify else self.build()

    def test_last_guard_after_canonical_comparison(self):
        bundle = self.build()
        deadline = instant(self.declaration["custody"]["expires_at"])
        original = daily.canonical
        comparisons = []

        def delayed(value):
            result = original(value)
            comparisons.append(value["bundle_id"])
            self.clock.return_value = deadline
            return result

        with patch.object(daily, "canonical", side_effect=delayed), self.assertRaisesRegex(Invalid, "expired"):
            self.verify(bundle)
        self.assertEqual(len(comparisons), 2)

    def test_legacy_replay_still_rejects_unknown_publication(self):
        captured = []
        original = api._compose_observed_daily

        def capture(payload, *args):
            captured.append(copy.deepcopy(payload))
            return original(payload, *args)

        with patch.object(api, "_compose_observed_daily", side_effect=capture):
            self.build()
        with pure_only():
            content = api.build_replay_bundle(captured[0], self.methods, self.entities, self.policy)["content"]
        self.assertEqual(content["domains"]["market"]["covered"], 0)
        self.assertIn("unknown_publication_time", {row["reason"] for row in content["rejected"]})

    def test_new_bundle_rejected_by_legacy_consumers_even_rehashed(self):
        bundle = self.build()
        synthetic, methods, entities = api.load_inputs()
        for forged in (bundle, {"bundle_id": digest(bundle["content"] | {"mode": "synthetic_test"}),
                               "content": bundle["content"] | {"mode": "synthetic_test"}}):
            calls = (lambda: api.build_bundle(self.declaration, self.methods, self.entities),
                     lambda: api.verify_bundle(forged, synthetic, methods, entities),
                     lambda: api.verify_replay_bundle(forged, {}, self.methods, self.entities, self.policy),
                     lambda: research.verified_input(forged, synthetic, methods, entities),
                     lambda: scenario.verify_execution(forged), lambda: scenario.validate_case(forged),
                     lambda: workflow.run_case(forged, "forbidden-runtime-store", "authored"),
                     lambda: publication.verify_source(forged), lambda: publication.verify_report(forged, {}),
                     lambda: delivery.verify_snapshot(forged, {}, NOW, NOW, []),
                     lambda: delivery.UnavailableLiveAdapter().send(forged))
            for index, call in enumerate(calls):
                with self.subTest(boundary=index, mode=forged["content"]["mode"]), self.assertRaises(Invalid):
                    call()

    def test_legacy_synthetic_bundle_identity_unchanged(self):
        self.clock.side_effect = utc_now
        data, methods, entities = api.load_inputs()
        with pure_only():
            bundle = api.build_bundle(data, methods, entities)
            self.assertEqual(bundle["bundle_id"], "f4c8c13da591b461976b76939c362a2f7039600116daf7dc95dd4f79c099781d")
            self.assertTrue(api.verify_bundle(bundle, data, methods, entities))

    def test_io_guards_are_active(self):
        import os
        import socket
        import sqlite3
        import urllib.request
        from market_intelligence.store import Store
        calls = (lambda: socket.getaddrinfo("authored.invalid", 443), lambda: socket.create_connection(("authored.invalid", 80)),
                 lambda: urllib.request.urlopen(daily.DOCUMENT), lambda: sqlite3.connect(":memory:"),
                 lambda: Store("forbidden-runtime-store"), lambda: open("forbidden-file", "w"), lambda: os.getenv("AUTHORED_SECRET"))
        for call in calls:
            with pure_only(), self.assertRaises(AssertionError):
                call()


if __name__ == "__main__":
    unittest.main()
