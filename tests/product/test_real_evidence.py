"""Authored real-shaped records only, never actual observations or source grants."""

from contextlib import ExitStack, contextmanager
import copy
from datetime import datetime, timedelta
import inspect
import time
import unittest
from unittest.mock import patch

from market_intelligence import intelligence as api
from market_intelligence import market_state, publication, research, scenario, workflow
from market_intelligence.market_state import validate_entities, validate_methods
from market_intelligence.models import Invalid, MARKETS, UNIVERSE, digest, instant, stamp, utc_now
from market_intelligence.source_policy import (OPERATIONS, PURPOSE, RIGHTS, assess_source_policy,
                                               basis_reference, validate_source_policy)

NOW = instant("2020-01-09T00:00:00Z")
ORIGIN = "https://authored.invalid"
PREFIX = ORIGIN + "/records/"
NETWORK = None


def forbidden(*args, **kwargs):
    raise AssertionError("pure replay attempted external I/O or secret access")


def setUpModule():
    global NETWORK
    NETWORK = ExitStack()
    for name in ("socket.socket", "socket.create_connection", "socket.getaddrinfo",
                 "socket.gethostbyname", "socket.gethostbyname_ex", "socket.gethostbyaddr",
                 "http.client.HTTPConnection.request", "http.client.HTTPSConnection.request",
                 "urllib.request.urlopen", "urllib.request.OpenerDirector.open",
                 "sqlite3.connect", "market_intelligence.store.Store.__init__"):
        NETWORK.enter_context(patch(name, side_effect=forbidden))


def tearDownModule():
    NETWORK.close()


@contextmanager
def pure_only():
    with ExitStack() as stack:
        for name in ("builtins.open", "io.open", "os.open", "os.getenv",
                     "os._Environ.__getitem__", "pathlib.Path.read_bytes",
                     "pathlib.Path.read_text", "pathlib.Path.write_bytes", "pathlib.Path.write_text"):
            stack.enter_context(patch(name, side_effect=forbidden))
        yield


def authored_inputs():
    """No source selection: these identities and contents exist only in this test."""
    policy = {"schema_version": 1, "version": "authored-policy-v1", "sources": [{
        "source_id": "authored_primary", "synthetic": False, "purpose": PURPOSE,
        "origin": ORIGIN, "locator_prefix": PREFIX,
        "retention": {"class": "reprocessable_raw", "max_age_seconds": 604800,
                      "basis_refs": [ORIGIN + "/terms/retention"]},
        "rights": {name: {"state": "permitted" if name in {"normalized_retention", "derived_analysis"} else "unknown",
                          "basis_refs": [ORIGIN + "/terms/" + name]} for name in RIGHTS}}]}
    methods = {"schema_version": 2, "version": "authored-method-v1", "profile_version": "authored-profile-v1",
               "universe_id": "authored-two-constituents", "benchmark_symbols": list(UNIVERSE),
               "constituents": ["600001.SH", "600002.SH"], "lookback_sessions": 1,
               "news_half_life_seconds": 86400, "news_ttl_seconds": 86400,
               "approach_seconds": 86400, "confirmation_grace_seconds": 21600,
               "event_ttl_seconds": 604800,
               "flow": {"provider": "unconfigured", "series_kind": "provider_estimate", "enabled": False},
               "sources": [{"source_id": "authored_primary", "tier": "A", "locator_prefix": PREFIX,
                            "scopes": ["market", "news", "event", "impact", "reaction"],
                            "event_ids": ["AUTHORED_EVENT"], "synthetic": False}],
               "rules": [{"domain": domain, "validator": domain + "-p2-v1", "required": domain != "flow",
                          "allow_degraded": domain == "flow"}
                         for domain in ("market", "sector", "news", "event", "entity", "flow")]}
    entities = {"schema_version": 2, "version": "authored-mapping-v1",
                "mapping_refs": [ORIGIN + "/mapping/v1"], "entities": []}
    for symbol in [*UNIVERSE, *methods["constituents"]]:
        cal = MARKETS[symbol][0] if symbol in MARKETS else "SSE"
        entities["entities"].append({
            "entity_id": symbol, "aliases": [symbol],
            "sector_id": "benchmark" if symbol in UNIVERSE else "authored_sector",
            "calendar_id": cal, "currency": {"SSE": "CNY", "SZSE": "CNY", "KRX": "KRW", "US_CASH": "USD"}[cal],
            "unit": "index_points" if symbol in UNIVERSE else "currency_units",
            "provider": "authored_primary", "provider_symbol": symbol,
            "publication_delay_seconds": None})
    data = {"schema_version": 2, "source_mode": "live_observation", "as_of": "2020-01-08T23:00:00Z",
            "calendar": {"version": "authored-explicit-sessions-v1", "sessions": []}, "facts": []}
    for day in (6, 7, 8):
        for cal, zone, close in (("SSE", "Asia/Shanghai", "15:00:00+08:00"),
                                 ("SZSE", "Asia/Shanghai", "15:00:00+08:00"),
                                 ("KRX", "Asia/Seoul", "15:30:00+09:00"),
                                 ("US_CASH", "America/New_York", "16:00:00-05:00")):
            date = "2020-01-0" + str(day)
            session = {"calendar_id": cal, "timezone": zone, "session_id": date, "close_at": date + "T" + close}
            data["calendar"]["sessions"].append(session)
            for entity in entities["entities"]:
                if entity["calendar_id"] == cal:
                    add_fact(data, {"kind": "bar", "symbol": entity["entity_id"], "session_id": date,
                                    "close": 1000 + day, "turnover": None if entity["entity_id"] in UNIVERSE else 100 + day},
                             instant(session["close_at"]) + timedelta(hours=1))
    news = add_fact(data, {"kind": "news", "title": "Authored 600001.SH event notice"}, instant("2020-01-08T22:10:00Z"))
    add_fact(data, {"kind": "event", "event_id": "AUTHORED_EVENT", "title": "Authored scheduled disclosure",
                   "asserted_state": "scheduled", "planned_at": "2020-01-09T12:00:00Z", "occurred_at": None,
                   "news_refs": [news], "supersedes": None, "success": None}, instant("2020-01-08T22:20:00Z"))
    return data, methods, entities, policy


def add_fact(data, payload, published):
    seen = published + timedelta(minutes=1)
    row = {"source_id": "authored_primary", "locator": PREFIX + str(len(data["facts"])),
           "mode": "live_observation", "published_at": stamp(published), "retrieved_at": stamp(seen),
           "first_seen_at": stamp(seen), "expires_at": stamp(seen + timedelta(days=7)),
           "parser_version": api.PARSERS[payload["kind"]], "content_hash": digest(payload), "payload": payload}
    data["facts"].append(row)
    return digest(row)


class RealEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.data, self.methods, self.entities, self.policy = authored_inputs()
        clock_patch = patch.object(api, "utc_now", return_value=NOW)
        self.clock = clock_patch.start()
        self.addCleanup(clock_patch.stop)

    def args(self):
        return self.data, self.methods, self.entities, self.policy

    def build(self):
        with pure_only():
            return api.build_replay_bundle(*self.args())

    def verify(self, bundle):
        with pure_only():
            return api.verify_replay_bundle(bundle, *self.args())

    def reasons(self, content):
        return {r["reason"] for r in content["rejected"]}

    @contextmanager
    def advance_after(self, target, name, when):
        original = getattr(target, name)

        def delayed(*args, **kwargs):
            result = original(*args, **kwargs)
            self.clock.return_value = when
            return result

        with patch.object(target, name, side_effect=delayed) as observed:
            yield observed

    def edit_fact(self, index=0, fields=None, payload=None):
        row = self.data["facts"][index]
        old = digest(row)
        row.update(fields or {})
        row["payload"].update(payload or {})
        row["content_hash"] = digest(row["payload"])
        for other in self.data["facts"]:
            if old in other["payload"].get("news_refs", []):
                other["payload"]["news_refs"] = [digest(row) if r == old else r for r in other["payload"]["news_refs"]]
                other["content_hash"] = digest(other["payload"])

    def test_complete_authored_domains_never_grant_operations(self):
        bundle = self.build()
        content = bundle["content"]
        self.assertTrue(self.verify(bundle))
        self.assertEqual(content["status"], "DEGRADED")
        self.assertEqual(content["domains"]["market"]["covered"], 5)
        self.assertEqual(content["readiness"], "BLOCKED_EXTERNAL_PREREQUISITES")
        self.assertFalse(content["permission_grant"])
        self.assertEqual(content["new_live_runs"], 0)
        self.assertEqual(content["network_requests"], 0)
        self.assertEqual(content["runtime_store_reads"], 0)
        self.assertEqual(content["live_market_coverage"], {"covered": 0, "expected": 5, "missing": list(UNIVERSE)})
        self.assertFalse(any(content["operations_enabled"].values()))
        self.assertFalse(content["historical_receipts_reinterpreted"])
        self.assertIn("audited_runtime_importer", content["next_gate"])

    def test_modes_and_original_possession_are_preserved(self):
        original = copy.deepcopy(self.args())
        content = self.build()["content"]
        self.assertEqual(original, self.args())
        self.assertEqual(content["processing_mode"], "historical_replay")
        self.assertEqual(content["source_mode"], "live_observation")
        self.assertEqual(content["source_as_of"], self.data["as_of"])
        for reference in content["provenance"]:
            fact = next(row for row in self.data["facts"] if digest(row) == reference["fact_id"])
            for key in ("mode", "published_at", "retrieved_at", "first_seen_at", "expires_at"):
                self.assertEqual(reference[key], fact[key])
        self.assertTrue(content["references"])
        self.assertTrue(all(row["mode"] == "live_observation" for row in content["references"]))

    def test_no_public_clock_argument(self):
        self.assertEqual(list(inspect.signature(api.build_replay_bundle).parameters),
                         ["payload", "methods", "entities", "source_policy"])
        self.assertEqual(list(inspect.signature(api.verify_replay_bundle).parameters),
                         ["bundle", "payload", "methods", "entities", "source_policy"])

    def test_actual_clock_is_used_without_override(self):
        self.clock.side_effect = utc_now
        before = utc_now()
        self.data["as_of"] = stamp(before - timedelta(seconds=1))
        self.data["facts"] = []
        content = self.build()["content"]
        self.assertLessEqual(before, instant(content["processing"]["started_at"]))
        self.assertLessEqual(instant(content["processing"]["completed_at"]), utc_now())

    def test_repeat_recomputation_uses_recorded_metadata(self):
        bundle = self.build()
        self.clock.return_value = NOW + timedelta(hours=1)
        self.assertTrue(self.verify(bundle))
        self.assertNotEqual(bundle["bundle_id"], self.build()["bundle_id"])

    def test_normalized_and_analysis_rights_are_both_required(self):
        for name in ("normalized_retention", "derived_analysis"):
            for state in ("unknown", "denied"):
                with self.subTest(name=name, state=state):
                    policy = copy.deepcopy(self.policy)
                    self.policy["sources"][0]["rights"][name]["state"] = state
                    with self.assertRaises(Invalid):
                        self.build()
                    self.policy = policy

    def test_future_egress_and_unperformed_retrieval_do_not_block(self):
        for name in set(RIGHTS) - {"normalized_retention", "derived_analysis"}:
            for state in ("unknown", "denied"):
                with self.subTest(name=name, state=state):
                    self.policy["sources"][0]["rights"][name]["state"] = state
                    self.assertEqual(self.build()["content"]["domains"]["market"]["status"], "PASS")

    def test_every_operation_assesses_only_its_required_rights(self):
        for name in RIGHTS:
            for state in ("permitted", "denied", "unknown"):
                with self.subTest(name=name, state=state):
                    self.policy["sources"][0]["rights"][name]["state"] = state
                    decision = assess_source_policy(self.policy, "authored_primary", name, PURPOSE)
                    self.assertEqual(decision["state"], state)
                    self.assertEqual(set(decision["applicable_rights"]), {name})
                    self.assertFalse(decision["permission_grant"])
                    self.assertFalse(decision["operation_enabled"])

    def test_rights_unknown_can_have_no_basis_but_permitted_cannot(self):
        right = self.policy["sources"][0]["rights"]["external_model_input"]
        right["basis_refs"] = []
        validate_source_policy(self.policy)
        right["state"] = "permitted"
        with self.assertRaises(Invalid):
            validate_source_policy(self.policy)

    def test_cross_origin_query_basis_does_not_authorize_evidence_origin(self):
        refs = ["https://licensor.invalid/mainContent?file=indexData.md"]
        source = self.policy["sources"][0]
        source["retention"]["basis_refs"] = refs
        source["rights"]["normalized_retention"]["basis_refs"] = refs
        self.assertTrue(self.verify(self.build()))
        self.data["facts"][0]["locator"] = "https://licensor.invalid/records/0"
        with self.assertRaises(Invalid):
            self.build()

    def test_basis_query_bounds_and_confusion_rejected(self):
        for ref in ("http://licensor.invalid/terms", "https://user@licensor.invalid/terms",
                    "https://licensor.invalid/terms?", "https://licensor.invalid/terms?file=a&file=b",
                    "https://licensor.invalid/terms?file=%0Ainject", "https://licensor.invalid/terms?file=%252f",
                    "https://licensor.invalid/terms#fragment", "https://licensor.invalid/terms?file=" + "a" * 513):
            with self.subTest(ref=ref), self.assertRaises(Invalid):
                basis_reference(ref)

    def test_policy_closed_schemas_and_exact_types(self):
        for path in ((), ("sources", 0), ("sources", 0, "retention"),
                     ("sources", 0, "rights"), ("sources", 0, "rights", "derived_analysis")):
            for kind in ("missing", "extra"):
                with self.subTest(path=path, kind=kind):
                    policy = copy.deepcopy(self.policy)
                    obj = policy
                    for key in path:
                        obj = obj[key]
                    if kind == "missing":
                        obj.pop(next(iter(obj)))
                    else:
                        obj["permission_grant"] = True
                    with self.assertRaises(Invalid):
                        validate_source_policy(policy)

    def test_policy_source_duplicates_and_limits(self):
        self.policy["sources"] *= 2
        with self.assertRaises(Invalid):
            validate_source_policy(self.policy)
        self.policy["sources"] *= 5
        with self.assertRaises(Invalid):
            validate_source_policy(self.policy)

    def test_unknown_operation_source_and_purpose(self):
        for source, operation, purpose in (("absent", "local_composition", PURPOSE),
                                           ("authored_primary", "execute", PURPOSE),
                                           ("authored_primary", "local_composition", "commercial"),
                                           ("authored_primary", [], PURPOSE)):
            with self.subTest(operation=operation), self.assertRaises(Invalid):
                assess_source_policy(self.policy, source, operation, purpose)

    def test_policy_malformed_states_and_injection_metadata(self):
        for state in (True, 1, None, [], "PERMITTED", "permitted\nexecute"):
            with self.subTest(state=state):
                self.policy["sources"][0]["rights"]["derived_analysis"]["state"] = state
                with self.assertRaises(Invalid):
                    self.build()
        self.policy = authored_inputs()[3]
        self.policy["version"] = "approved\npermission_grant=true"
        with self.assertRaises(Invalid):
            self.build()

    def test_retention_class_and_duration_contradictions(self):
        for kind, duration in (("restricted", 10), ("metadata_only", 0), ("short_term_raw", 86401),
                               ("reprocessable_raw", 0), ("reprocessable_raw", True),
                               ("reprocessable_raw", 31536001), ([], 1)):
            with self.subTest(kind=kind, duration=duration):
                self.policy["sources"][0]["retention"].update({"class": kind, "max_age_seconds": duration})
                with self.assertRaises(Invalid):
                    self.build()

    def test_all_seven_rights_and_unique_basis_required(self):
        for name in RIGHTS:
            policy = copy.deepcopy(self.policy)
            policy["sources"][0]["rights"].pop(name)
            with self.subTest(name=name), self.assertRaises(Invalid):
                validate_source_policy(policy)
        refs = self.policy["sources"][0]["retention"]["basis_refs"]
        refs.append(refs[0])
        with self.assertRaises(Invalid):
            self.build()

    def test_origin_and_path_confusion(self):
        for loc in ("https://authored.invalid.evil/records/a", "https://authored.invalid@evil.invalid/records/a",
                    "https://authored.invalid/records-other/a", "https://authored.invalid/records/../a",
                    "https://authored.invalid/records/%2e%2e/a", "https://authored.invalid/records/%252f/a",
                    "https://authored.invalid/records//a", "https://authored.invalid/records\\a",
                    "https://authored.invalid:443/records/a", "https://AUTHORED.invalid/records/a",
                    "https://authored.invalid/records/a?q=1", "https://authored.invalid/records/a#f",
                    "https://authored.invalid/records/a\n", "http://authored.invalid/records/a"):
            with self.subTest(locator=loc):
                self.data["facts"][0]["locator"] = loc
                with self.assertRaises(Invalid):
                    self.build()

    def test_policy_and_method_prefix_must_match_exactly(self):
        self.methods["sources"][0]["locator_prefix"] = ORIGIN + "/other/"
        with self.assertRaises(Invalid):
            self.build()

    def test_synthetic_registry_and_facts_rejected(self):
        for target in (self.methods["sources"][0], self.policy["sources"][0]):
            target["synthetic"] = True
            with self.assertRaises(Invalid):
                self.build()
            target["synthetic"] = False
        for mode in ("synthetic_test", "historical_replay"):
            self.data["facts"][0]["mode"] = mode
            with self.assertRaises(Invalid):
                self.build()

    def test_wrong_payload_source_modes_rejected(self):
        for mode in ("synthetic_test", "historical_replay", True, None):
            self.data["source_mode"] = mode
            with self.subTest(mode=mode), self.assertRaises(Invalid):
                self.build()

    def test_future_original_asof_and_possession(self):
        for key in ("retrieved_at", "first_seen_at", "published_at"):
            original = copy.deepcopy(self.data)
            self.data["facts"][0][key] = "2020-01-09T00:01:00Z"
            with self.subTest(key=key), self.assertRaises(Invalid):
                self.build()
            self.data = original
        self.data["as_of"] = "2020-01-09T00:01:00Z"
        with self.assertRaises(Invalid):
            self.build()

    def test_possession_order_and_late_retrieval_not_historical_live(self):
        row = self.data["facts"][0]
        row["first_seen_at"] = "2020-01-09T01:00:00Z"
        row["retrieved_at"] = "2020-01-09T01:00:00Z"
        with self.assertRaises(Invalid):
            self.build()

    def test_naive_and_invalid_timestamps(self):
        for value in ("2020-01-08", "2020-01-08T00:00:00", True, None, "bad"):
            with self.subTest(value=value):
                self.data["facts"][0]["retrieved_at"] = value
                with self.assertRaises(Invalid):
                    self.build()

    def test_expiry_required_and_consistent(self):
        for expiry in (None, self.data["facts"][0]["retrieved_at"], "2020-02-09T00:00:00Z", stamp(NOW)):
            with self.subTest(expiry=expiry):
                self.data["facts"][0]["expires_at"] = expiry
                with self.assertRaises(Invalid):
                    self.build()

    def test_first_seen_prevents_retention_extension_by_recapture(self):
        row = self.data["facts"][0]
        row["retrieved_at"] = "2020-01-08T22:00:00Z"
        row["expires_at"] = "2020-01-15T22:00:00Z"
        with self.assertRaises(Invalid):
            self.build()

    def test_expiry_before_use_blocks_semantic_computation(self):
        self.data["facts"][0]["expires_at"] = stamp(NOW)
        with patch.object(api, "_compose", side_effect=forbidden), self.assertRaises(Invalid):
            self.build()

    def test_expiry_during_computation_blocks_return(self):
        deadline = NOW + timedelta(seconds=1)
        self.data["facts"][0]["expires_at"] = stamp(deadline)
        with self.advance_after(api, "process_news", deadline), self.assertRaises(Invalid):
            self.build()

    def test_expiry_at_final_return_check(self):
        deadline = NOW + timedelta(seconds=1)
        self.data["facts"][0]["expires_at"] = stamp(deadline)
        with self.advance_after(api, "_replay_bundle", deadline), self.assertRaises(Invalid):
            self.build()

    def test_verification_cannot_revive_expired_record(self):
        self.data["facts"][0]["expires_at"] = stamp(NOW + timedelta(seconds=1))
        bundle = self.build()
        self.clock.return_value = NOW + timedelta(seconds=1)
        with patch.object(api, "_replay_content", side_effect=forbidden), self.assertRaises(Invalid):
            self.verify(bundle)

    def test_verification_rechecks_retention_before_return(self):
        deadline = NOW + timedelta(seconds=1)
        self.data["facts"][0]["expires_at"] = stamp(deadline)
        bundle = self.build()
        with self.advance_after(api, "_replay_bundle", deadline), self.assertRaises(Invalid):
            self.verify(bundle)

    def test_build_clock_rollback_fails_at_each_boundary(self):
        for phase in ("_replay_inputs", "_replay_retention", "_replay_content", "_replay_bundle"):
            self.clock.return_value = NOW
            with self.subTest(phase=phase), self.advance_after(api, phase, NOW - timedelta(seconds=1)), \
                    self.assertRaisesRegex(Invalid, "clock rollback"):
                self.build()

    def test_verifier_clock_rollback_fails(self):
        bundle = self.build()
        self.clock.return_value = NOW - timedelta(seconds=1)
        with self.assertRaises(Invalid):
            self.verify(bundle)
        self.clock.return_value = NOW
        with self.advance_after(api, "_replay_bundle", NOW - timedelta(seconds=1)), self.assertRaises(Invalid):
            self.verify(bundle)

    def test_invalid_clock_fails_closed(self):
        self.clock.return_value = datetime(2020, 1, 9)
        with self.assertRaises(Invalid):
            self.build()

    def test_empty_unconfigured_domains_never_pass(self):
        self.data["facts"] = []
        self.data["calendar"]["sessions"] = []
        self.methods["constituents"] = []
        self.methods["sources"] = []
        self.entities["entities"] = []
        self.entities["mapping_refs"] = []
        self.policy["sources"] = []
        content = self.build()["content"]
        self.assertEqual(content["status"], "BLOCK")
        self.assertTrue(all(r["status"] != "PASS" for r in content["domains"].values()))
        self.assertEqual(content["domains"]["market"]["expected"], 5)
        self.assertEqual(content["domains"]["sector"]["expected"], 1)
        self.assertIsNone(content["results"]["market"]["breadth"]["ratios"])
        self.assertIsNone(content["results"]["market"]["turnover"]["value"])
        self.assertFalse(content["results"]["events"]["empty_day"])

    def test_missing_mapping_preserves_full_benchmark_target(self):
        self.entities["entities"] = self.entities["entities"][1:]
        content = self.build()["content"]
        self.assertEqual(len(content["results"]["market"]["benchmarks"]), 5)
        self.assertEqual(content["domains"]["market"]["covered"], 4)
        self.assertIn("unconfigured_mapping", self.reasons(content))
        self.assertEqual(content["domains"]["entity"]["status"], "BLOCK")

    def test_missing_constituent_mapping_does_not_shrink_denominator(self):
        self.entities["entities"] = self.entities["entities"][:-1]
        market = self.build()["content"]["results"]["market"]
        self.assertEqual(market["breadth"]["expected"], 2)
        self.assertIsNone(market["turnover"]["value"])
        self.assertIn("unconfigured_mapping", market["sector_reasons"])

    def test_absent_calendars_produce_missing_diagnostics(self):
        self.data["calendar"]["sessions"] = []
        content = self.build()["content"]
        self.assertEqual(content["domains"]["market"]["covered"], 0)
        self.assertIn("unavailable_calendar", self.reasons(content))

    def test_missing_history_not_zero_filled(self):
        self.methods["lookback_sessions"] = 10
        market = self.build()["content"]["results"]["market"]
        self.assertEqual(market["turnover"]["reason"], "insufficient_history")
        self.assertIsNone(market["turnover"]["relative"])
        self.assertTrue(all(row["equal_weight_return"] is None for row in market["sectors"]))

    def test_optional_degradation_is_explicit_and_required_blocks(self):
        self.data["facts"] = []
        for rule in self.methods["rules"]:
            rule.update(required=False, allow_degraded=True)
        self.assertEqual(self.build()["content"]["status"], "DEGRADED")
        self.methods["rules"][0].update(required=True, allow_degraded=False)
        self.assertEqual(self.build()["content"]["status"], "BLOCK")

    def test_missing_validator_cannot_degrade(self):
        self.methods["rules"][0].update(required=False, allow_degraded=True, validator="absent")
        self.assertEqual(self.build()["content"]["domains"]["market"]["status"], "BLOCK")

    def test_exact_five_indices_no_substitutions(self):
        for symbols in (list(UNIVERSE)[:-1], ["ETF", *UNIVERSE[1:]], list(reversed(UNIVERSE))):
            self.methods["benchmark_symbols"] = symbols
            with self.subTest(symbols=symbols), self.assertRaises(Invalid):
                self.build()

    def test_numeric_bounds_and_types(self):
        for key in ("close", "turnover"):
            for val in (True, "100", None, -1, 1e16, float("nan"), float("inf"), []):
                original = copy.deepcopy(self.data)
                if key == "turnover":
                    self.data["facts"][0]["payload"]["symbol"] = "600001.SH"
                self.data["facts"][0]["payload"][key] = val
                if type(val) is not float or val not in (float("inf"),) and val == val:
                    self.data["facts"][0]["content_hash"] = digest(self.data["facts"][0]["payload"])
                with self.subTest(key=key, val=val), self.assertRaises(Invalid):
                    self.build()
                self.data = original

    def test_zero_and_tiny_close_rejected(self):
        for value in (0, 1e-13):
            self.edit_fact(payload={"close": value})
            with self.assertRaises(Invalid):
                self.build()

    def test_conflicting_bars_rejected_even_when_rehashed(self):
        row = copy.deepcopy(self.data["facts"][0])
        row["payload"]["close"] += 10
        row["content_hash"] = digest(row["payload"])
        self.data["facts"].append(row)
        content = self.build()["content"]
        self.assertIn("conflicting_market_evidence", self.reasons(content))
        self.assertEqual(content["domains"]["market"]["status"], "BLOCK")

    def test_unknown_source_parser_and_symbol(self):
        for key, val in (("source_id", "unknown"), ("parser_version", "unknown")):
            original = copy.deepcopy(self.data)
            self.edit_fact(fields={key: val})
            with self.subTest(key=key), self.assertRaises(Invalid):
                self.build()
            self.data = original
        self.edit_fact(payload={"symbol": "ETF"})
        with self.assertRaises(Invalid):
            self.build()

    def test_duplicate_fact_identity(self):
        self.data["facts"].append(copy.deepcopy(self.data["facts"][0]))
        with self.assertRaises(Invalid):
            self.build()

    def test_source_authority_and_tier_c_are_not_truth(self):
        self.methods["sources"][0]["tier"] = "C"
        content = self.build()["content"]
        self.assertEqual(content["domains"]["news"]["status"], "BLOCK")
        self.assertEqual(content["domains"]["market"]["covered"], 0)
        self.assertIn("source_authority_scope", self.reasons(content))

    def test_event_authority_scope_enforced(self):
        self.methods["sources"][0]["event_ids"] = []
        self.assertIn("source_event_scope", self.reasons(self.build()["content"]))

    def test_news_scope_enforced(self):
        self.methods["sources"][0]["scopes"].remove("news")
        self.assertIn("source_authority_scope", self.reasons(self.build()["content"]))

    def test_mapping_calendar_currency_unit_validation(self):
        for field, value in (("calendar_id", "KRX"), ("currency", "USD"), ("unit", "shares"),
                              ("sector_id", "industry"), ("provider", "unconfigured"),
                              ("publication_delay_seconds", True), ("provider_symbol", "bad\nname")):
            entities = copy.deepcopy(self.entities)
            self.entities["entities"][0][field] = value
            with self.subTest(field=field), self.assertRaises(Invalid):
                self.build()
            self.entities = entities

    def test_wrong_provider_rejected(self):
        policy = copy.deepcopy(self.policy["sources"][0])
        policy["source_id"] = "authored_other"
        self.policy["sources"].append(policy)
        method = copy.deepcopy(self.methods["sources"][0])
        method["source_id"] = "authored_other"
        self.methods["sources"].append(method)
        self.edit_fact(fields={"source_id": "authored_other"})
        self.assertIn("series_provider_mismatch", self.reasons(self.build()["content"]))

    def test_calendar_identity_offset_duplicate_and_bounds(self):
        for change in ({"timezone": "America/New_York"}, {"close_at": "2020-01-06T15:00:00Z"},
                       {"session_id": "2020-01-07"}):
            original = copy.deepcopy(self.data["calendar"])
            self.data["calendar"]["sessions"][0].update(change)
            with self.subTest(change=change), self.assertRaises(Invalid):
                self.build()
            self.data["calendar"] = original
        self.data["calendar"]["sessions"].append(copy.deepcopy(self.data["calendar"]["sessions"][0]))
        with self.assertRaises(Invalid):
            self.build()

    def test_known_delay_checked_unknown_not_invented(self):
        self.entities["entities"][0]["publication_delay_seconds"] = 7200
        self.assertIn("publication_precedes_available_close", self.reasons(self.build()["content"]))
        self.entities["entities"][0]["publication_delay_seconds"] = None
        self.assertEqual(self.build()["content"]["domains"]["market"]["status"], "PASS")

    def test_unknown_publication_not_inferred_from_session(self):
        self.edit_fact(fields={"published_at": None})
        self.assertIn("unknown_publication_time", self.reasons(self.build()["content"]))

    def test_unclosed_bar_is_rejected(self):
        self.edit_fact(fields={"published_at": "2020-01-06T06:00:00Z", "first_seen_at": "2020-01-06T06:01:00Z",
                               "retrieved_at": "2020-01-06T06:01:00Z", "expires_at": "2020-01-13T06:01:00Z"})
        self.assertIn("future_or_unconfirmed_market_close", self.reasons(self.build()["content"]))

    def test_stale_news_remains_rejected(self):
        self.methods["news_ttl_seconds"] = 1
        content = self.build()["content"]
        self.assertIn("stale_news", self.reasons(content))
        self.assertEqual(content["results"]["news"], [])

    def test_future_event_occurrence_rejected(self):
        self.edit_fact(-1, payload={"asserted_state": "confirmed", "occurred_at": "2020-01-09T12:00:00Z"})
        self.assertIn("future_actual_occurrence", self.reasons(self.build()["content"]))

    def test_no_news_or_events_not_observed_empty_day(self):
        self.data["facts"] = [r for r in self.data["facts"] if r["payload"]["kind"] == "bar"]
        content = self.build()["content"]
        self.assertFalse(content["results"]["events"]["empty_day"])
        self.assertEqual(content["domains"]["event"]["status"], "BLOCK")

    def test_boundaries_closed_schema_numbers_and_structures(self):
        for index in range(4):
            args = list(copy.deepcopy(self.args()))
            args[index]["schema_version"] = True
            with self.subTest(index=index), self.assertRaises(Invalid):
                api.build_replay_bundle(*args)
        self.data["facts"] = tuple(self.data["facts"])
        with self.assertRaises(Invalid):
            self.build()

    def test_payload_size_depth_and_record_bounds(self):
        original = copy.deepcopy(self.data)
        self.data["facts"] *= 50
        with self.assertRaises(Invalid):
            self.build()
        self.data = original
        self.data["extra"] = "x" * 512001
        with self.assertRaises(Invalid):
            self.build()
        self.data["extra"] = self.data
        with self.assertRaises(Invalid):
            self.build()

    def test_duplicate_json_fields_rejected_by_shared_decoder(self):
        with self.assertRaises(Invalid):
            api.decode_input(b'{"source_mode":"live_observation","source_mode":"synthetic_test"}')

    def test_all_input_fingerprints_recomputed(self):
        bundle = self.build()
        for target, key in ((self.methods, "version"), (self.entities, "version"),
                            (self.policy, "version"), (self.data["calendar"], "version")):
            original = target[key]
            target[key] += "-changed"
            with self.subTest(key=key), self.assertRaises(Invalid):
                self.verify(bundle)
            target[key] = original
        self.edit_fact(payload={"close": 1001.5})
        with self.assertRaises(Invalid):
            self.verify(bundle)

    def test_rehashing_forged_results_does_not_pass(self):
        bundle = self.build()
        changes = [("readiness", "READY"), ("permission_grant", True), ("new_live_runs", 1),
                   ("source_as_of", "2020-01-07T00:00:00Z"), ("mode", "synthetic_test"),
                   ("processing_mode", "live_observation"), ("rejected", []),
                   ("references", []), ("provenance", []), ("source_policy_decisions", []),
                   ("live_market_coverage", {"covered": 5, "expected": 5, "missing": []})]
        # Give rejections a real entry so deleting it is a substantive forgery.
        self.methods["news_ttl_seconds"] = 1
        rejected_bundle = self.build()
        self.methods["news_ttl_seconds"] = 86400
        for key, value in changes:
            forged = copy.deepcopy(rejected_bundle if key == "rejected" else bundle)
            forged["content"][key] = value
            forged["bundle_id"] = digest(forged["content"])
            with self.subTest(key=key), self.assertRaises(Invalid):
                self.verify(forged)

    def test_rehashed_metric_and_policy_forgery(self):
        bundle = self.build()
        for target in ("metric", "policy", "fingerprint", "domain", "operations"):
            forged = copy.deepcopy(bundle)
            c = forged["content"]
            if target == "metric":
                c["results"]["market"]["benchmarks"][0]["value"] = 12345
            elif target == "policy":
                c["source_policy_decisions"][0]["permission_grant"] = True
            elif target == "fingerprint":
                c["fingerprints"]["universe"] = "a" * 64
            elif target == "domain":
                c["domains"]["flow"]["status"] = "PASS"
            else:
                c["operations_enabled"]["external_model_input"] = True
            forged["bundle_id"] = digest(c)
            with self.subTest(target=target), self.assertRaises(Invalid):
                self.verify(forged)

    def test_processing_metadata_recomputed_and_temporally_checked(self):
        bundle = self.build()
        for key, value in (("clock", "caller"), ("started_at", "2020-01-01T00:00:00Z"),
                           ("completed_at", "2030-01-01T00:00:00Z"), ("completed_at", "2020-01-08T23:30:00Z")):
            forged = copy.deepcopy(bundle)
            c = forged["content"]
            c["processing"][key] = value
            c["fingerprints"]["processing"] = digest(c["processing"])
            forged["bundle_id"] = digest(c)
            with self.subTest(key=key, value=value), self.assertRaises(Invalid):
                self.verify(forged)
        forged = copy.deepcopy(bundle)
        forged["content"]["processing"]["completed_at"] = stamp(NOW + timedelta(microseconds=1))
        forged["bundle_id"] = digest(forged["content"])
        self.clock.return_value = NOW + timedelta(seconds=1)
        with self.assertRaises(Invalid):
            self.verify(forged)

    def test_new_bundle_is_rejected_by_all_legacy_boundaries(self):
        bundle = self.build()
        calls = (lambda: api.build_bundle(self.data, self.methods, self.entities),
                 lambda: api.verify_bundle(bundle, self.data, self.methods, self.entities),
                 lambda: research.verified_input(bundle, self.data, self.methods, self.entities),
                 lambda: scenario.verify_execution(bundle), lambda: scenario.validate_case(bundle),
                 lambda: publication.verify_source(bundle), lambda: publication.verify_report(bundle, {}),
                 lambda: workflow.run_case(bundle, "forbidden-runtime-store", "authored"))
        # Legacy publication verifies its immutable bundled assets before rejection.
        # The module-wide network and actual-store guards remain active here.
        for index, call in enumerate(calls):
            with self.subTest(boundary=index), self.assertRaises(Invalid):
                call()

    def test_legacy_default_validators_stay_synthetic_only(self):
        with self.assertRaises(Invalid):
            validate_methods(self.methods)
        with self.assertRaises(Invalid):
            validate_entities(self.entities, self.methods)

    def test_multiple_rejection_reasons_are_retained(self):
        self.data["calendar"]["sessions"] = []
        self.methods["sources"][0]["tier"] = "C"
        self.edit_fact(fields={"published_at": None})
        identity = digest(self.data["facts"][0])
        content = self.build()["content"]
        reasons = {r["reason"] for r in content["rejected"] if r["fact_id"] == identity}
        self.assertEqual(reasons, {"unavailable_calendar", "future_or_unconfirmed_market_close",
                                   "unknown_publication_time"})

    def test_rights_never_transfer_between_sources(self):
        policy = copy.deepcopy(self.policy["sources"][0])
        policy["source_id"] = "authored_other"
        policy["rights"]["derived_analysis"]["state"] = "unknown"
        self.policy["sources"].append(policy)
        method = copy.deepcopy(self.methods["sources"][0])
        method["source_id"] = "authored_other"
        self.methods["sources"].append(method)
        # An unused source's future specification does not authorize or block another source.
        self.build()
        self.data["facts"][-2]["source_id"] = "authored_other"
        with self.assertRaises(Invalid):
            self.build()

    def test_policy_origin_prefix_and_root_scope_rejected(self):
        for field, value in (("origin", "https://other.invalid"), ("origin", ORIGIN + "/"),
                              ("locator_prefix", ORIGIN + "/"), ("locator_prefix", ORIGIN + "/records")):
            original = copy.deepcopy(self.policy)
            self.policy["sources"][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(Invalid):
                self.build()
            self.policy = original

    def test_flow_remains_explicitly_unavailable(self):
        flow = self.build()["content"]["results"]["flow"]
        self.assertEqual(flow["status"], "unavailable")
        self.assertIsNone(flow["value"])
        self.methods["flow"]["enabled"] = True
        with self.assertRaises(Invalid):
            self.build()

    def test_news_and_event_record_limits(self):
        original = copy.deepcopy(self.data)
        for number in range(100):
            add_fact(self.data, {"kind": "news", "title": "Authored notice " + str(number)},
                     instant("2020-01-08T22:10:00Z"))
        with self.assertRaises(Invalid):
            self.build()
        self.data = original
        for number in range(20):
            row = copy.deepcopy(self.data["facts"][-1]["payload"])
            row["event_id"] = "event" + str(number)
            add_fact(self.data, row, instant("2020-01-08T22:20:00Z"))
        with self.assertRaises(Invalid):
            self.build()

    def test_closed_schemas_reject_extra_input_fields(self):
        for path in ((), ("facts", 0), ("facts", 0, "payload"), ("calendar",),
                     ("calendar", "sessions", 0)):
            data = copy.deepcopy(self.data)
            target = data
            for key in path:
                target = target[key]
            target["processing_at"] = stamp(NOW)
            with self.subTest(path=path), self.assertRaises(Invalid):
                api.build_replay_bundle(data, self.methods, self.entities, self.policy)

    def test_mapping_and_universe_binding_tampering_rejected(self):
        bundle = self.build()
        forged = copy.deepcopy(bundle)
        forged["content"]["expected_universe"]["benchmarks"] = ["ETF"]
        forged["bundle_id"] = digest(forged["content"])
        with self.assertRaises(Invalid):
            self.verify(forged)
        self.entities["entities"][0]["provider_symbol"] = "another_symbol"
        with self.assertRaises(Invalid):
            self.verify(bundle)

    def test_policy_hash_recomputed_after_caller_policy_change(self):
        bundle = self.build()
        self.policy["sources"][0]["rights"]["private_delivery"]["state"] = "permitted"
        with self.assertRaises(Invalid):
            self.verify(bundle)
        self.assertFalse(self.build()["content"]["operations_enabled"]["private_delivery"])

    def test_missing_and_rehashed_provenance_not_accepted(self):
        forged = self.build()
        forged["content"]["provenance"][0]["first_seen_at"] = "2020-01-01T00:00:00Z"
        forged["bundle_id"] = digest(forged["content"])
        with self.assertRaises(Invalid):
            self.verify(forged)

    def test_fixture_guards_are_active(self):
        import socket
        import sqlite3
        import urllib.request
        from market_intelligence.store import Store
        for call in (lambda: socket.getaddrinfo("authored.invalid", 443),
                     lambda: urllib.request.urlopen(ORIGIN), lambda: sqlite3.connect(":memory:"),
                     lambda: Store("forbidden-runtime-store")):
            with self.assertRaises(AssertionError):
                call()

    def test_r1_build_policy_validation_expiry_prevents_semantic_entry(self):
        deadline = NOW + timedelta(seconds=1)
        self.data["facts"][0]["expires_at"] = stamp(deadline)
        with self.advance_after(api, "assess_source_policy", deadline) as assessed, \
                patch.object(api, "_compose", side_effect=forbidden), self.assertRaisesRegex(Invalid, "expired"):
            self.build()
        self.assertEqual(assessed.call_count, 1)

    def test_r1_verify_policy_validation_expiry_prevents_semantic_entry(self):
        deadline = NOW + timedelta(seconds=1)
        self.data["facts"][0]["expires_at"] = stamp(deadline)
        bundle = self.build()
        with self.advance_after(api, "assess_source_policy", deadline) as assessed, \
                patch.object(api, "_compose", side_effect=forbidden), self.assertRaisesRegex(Invalid, "expired"):
            self.verify(bundle)
        self.assertEqual(assessed.call_count, 1)

    def final_policy_work(self, verify):
        """Reproduce the old final assessment gap if policy work is reintroduced."""
        deadline = NOW + timedelta(seconds=1)
        self.data["facts"][0]["expires_at"] = stamp(deadline)
        bundle = self.build() if verify else None
        final_phase = False
        freeze, assess = api._replay_bundle, api.assess_source_policy

        def freezing(*args, **kwargs):
            nonlocal final_phase
            result = freeze(*args, **kwargs)
            final_phase = True
            return result

        def assessment(*args, **kwargs):
            result = assess(*args, **kwargs)
            if final_phase:
                self.clock.return_value = deadline
            return result

        with patch.object(api, "_replay_bundle", side_effect=freezing), \
                patch.object(api, "assess_source_policy", side_effect=assessment) as assessed:
            result = self.verify(bundle) if verify else self.build()
        self.assertTrue(result)
        self.assertTrue(final_phase)
        self.assertEqual(assessed.call_count, 1, "policy decisions must be static, not repeated at return")
        self.assertLess(self.clock(), deadline, "returned after validation crossed expiry")

    def test_r1_build_final_return_has_no_policy_work_after_clock(self):
        self.final_policy_work(False)

    def test_r1_verify_final_return_has_no_policy_work_after_clock(self):
        self.final_policy_work(True)

    def test_r1_static_metadata_validation_expiry_blocks_use(self):
        deadline = NOW + timedelta(seconds=1)
        self.data["facts"][0]["expires_at"] = stamp(deadline)
        with self.advance_after(api, "location_matches", deadline), \
                patch.object(api, "_compose", side_effect=forbidden), self.assertRaisesRegex(Invalid, "expired"):
            self.build()

    def test_r1_policy_validation_clock_rollback_blocks_use(self):
        bundle = self.build()
        for verify in (False, True):
            self.clock.return_value = NOW
            with self.subTest(verify=verify), \
                    self.advance_after(api, "assess_source_policy", NOW - timedelta(seconds=1)), \
                    patch.object(api, "_compose", side_effect=forbidden), \
                    self.assertRaisesRegex(Invalid, "clock rollback"):
                self.verify(bundle) if verify else self.build()

    def test_r1_shared_market_validation_guard_precedes_calculations(self):
        deadline = NOW + timedelta(seconds=1)
        self.data["facts"][0]["expires_at"] = stamp(deadline)
        bundle = self.build()
        for verify in (False, True):
            for phase in ("_validate_methods", "_validate_entities"):
                for when, reason in ((deadline, "expired"), (NOW - timedelta(seconds=1), "clock rollback")):
                    self.clock.return_value = NOW
                    with self.subTest(verify=verify, phase=phase, reason=reason), \
                            self.advance_after(market_state, phase, when), \
                            patch.object(market_state, "rounded", side_effect=forbidden), \
                            self.assertRaisesRegex(Invalid, reason):
                        self.verify(bundle) if verify else self.build()

    def test_r1_calendar_validation_guard_precedes_fact_use(self):
        deadline = NOW + timedelta(seconds=1)
        self.data["facts"][0]["expires_at"] = stamp(deadline)
        with self.advance_after(api.Calendar, "from_dict", deadline), \
                patch.object(api.Fact, "from_dict", side_effect=forbidden), \
                self.assertRaisesRegex(Invalid, "expired"):
            self.build()

    def test_r1_fact_validation_guard_precedes_numeric_use(self):
        deadline = NOW + timedelta(seconds=1)
        self.data["facts"][0]["expires_at"] = stamp(deadline)
        with self.advance_after(api.Fact, "from_dict", deadline), \
                patch.object(api, "numeric", side_effect=forbidden), self.assertRaisesRegex(Invalid, "expired"):
            self.build()

    def test_r1_preparation_expiry_prevents_domain_dispatch(self):
        deadline = NOW + timedelta(seconds=1)
        self.data["facts"][0]["expires_at"] = stamp(deadline)
        with self.advance_after(api, "_prepare", deadline), \
                patch.object(api, "_evaluate_market", side_effect=forbidden), self.assertRaisesRegex(Invalid, "expired"):
            self.build()

    def test_r1_verification_comparison_work_cannot_bypass_last_guard(self):
        deadline = NOW + timedelta(seconds=1)
        self.data["facts"][0]["expires_at"] = stamp(deadline)
        bundle = self.build()
        original = api.canonical
        comparisons = []

        def delayed(value):
            result = original(value)
            if type(value) is dict and set(value) == {"bundle_id", "content"}:
                comparisons.append(value["bundle_id"])
                self.clock.return_value = deadline
            return result

        with patch.object(api, "canonical", side_effect=delayed), self.assertRaisesRegex(Invalid, "expired"):
            self.verify(bundle)
        self.assertEqual(len(comparisons), 2)

    def test_r1_guard_uses_earliest_expiry_including_rejected_facts(self):
        deadline = NOW + timedelta(seconds=1)
        self.edit_fact(-1, fields={"expires_at": stamp(deadline)},
                       payload={"asserted_state": "confirmed", "occurred_at": "2020-01-09T12:00:00Z"})
        with self.advance_after(api, "_replay_bundle", deadline), self.assertRaisesRegex(Invalid, "expired"):
            self.build()

    def wall_clock_expiry(self, verify, final):
        """Allow ten seconds to reach the target; wait only inside that target.

        Entry assertions and a hit marker forbid false positives from premature
        expiry. The sleep is bounded; no busy wait, remote I/O or real data.
        """
        self.clock.side_effect = utc_now
        now = utc_now()
        deadline = now + timedelta(seconds=10)
        self.data["as_of"] = stamp(now - timedelta(seconds=1))
        self.data["calendar"]["sessions"] = []
        self.data["facts"] = []
        self.methods["constituents"] = []
        self.entities["entities"] = []
        self.entities["mapping_refs"] = []
        add_fact(self.data, {"kind": "news", "title": "Authored clock-window notice"}, now - timedelta(minutes=2))
        self.data["facts"][0]["expires_at"] = stamp(deadline)
        bundle = self.build() if verify else None
        target = "_replay_bundle" if final else "assess_source_policy"
        original = getattr(api, target)
        entries = []

        def delayed(*args, **kwargs):
            self.assertLess(utc_now(), deadline, "expiry occurred before the intended regression target")
            result = original(*args, **kwargs)
            entered = utc_now()
            self.assertLess(entered, deadline, "validation already expired before the controlled delay")
            entries.append(entered)
            remaining = (deadline - entered).total_seconds()
            self.assertGreater(remaining, 0)
            self.assertLessEqual(remaining, 10)
            time.sleep(remaining + 0.02)
            self.assertGreaterEqual(utc_now(), deadline)
            return result

        with ExitStack() as stack:
            stack.enter_context(patch.object(api, target, side_effect=delayed))
            if not final:
                stack.enter_context(patch.object(api, "_compose", side_effect=forbidden))
            with self.assertRaisesRegex(Invalid, "expired"):
                self.verify(bundle) if verify else self.build()
        self.assertEqual(len(entries), 1, "target validation must actually execute before expiry")

    def test_r1_wall_clock_build_validation_expiry_before_use(self):
        self.wall_clock_expiry(verify=False, final=False)

    def test_r1_wall_clock_verify_validation_expiry_before_use(self):
        self.wall_clock_expiry(verify=True, final=False)

    def test_r1_wall_clock_build_final_work_expiry_no_return(self):
        self.wall_clock_expiry(verify=False, final=True)

    def test_r1_wall_clock_verify_final_work_expiry_no_return(self):
        self.wall_clock_expiry(verify=True, final=True)

    def test_legacy_exact_demo_hash_unchanged(self):
        self.clock.side_effect = utc_now
        data, methods, entities = api.load_inputs()
        with pure_only():
            bundle = api.build_bundle(data, methods, entities)
            self.assertEqual(bundle["bundle_id"], "f4c8c13da591b461976b76939c362a2f7039600116daf7dc95dd4f79c099781d")
            self.assertTrue(api.verify_bundle(bundle, data, methods, entities))
        data["mode"] = "live_observation"
        with self.assertRaises(Invalid):
            api.build_bundle(data, methods, entities)


if __name__ == "__main__":
    unittest.main()
