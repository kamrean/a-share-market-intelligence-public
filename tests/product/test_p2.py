"""Offline P2 acceptance; synthetic results never establish live readiness."""

from contextlib import ExitStack, redirect_stdout
import copy
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from market_intelligence import intelligence as p2
from market_intelligence.market_state import UnavailableFlowAdapter
from market_intelligence.models import Invalid, canonical, digest, instant, utc_now
from market_intelligence.news_events import STATES, map_entities

NETWORK = None


def forbidden(*args, **kwargs):
    raise AssertionError("P2 attempted network, DNS, or a write")


def setUpModule():
    global NETWORK
    NETWORK = ExitStack()
    for name in ("socket.socket", "socket.create_connection", "socket.getaddrinfo",
                 "http.client.HTTPConnection.request", "urllib.request.urlopen"):
        NETWORK.enter_context(patch(name, side_effect=forbidden))


def tearDownModule():
    NETWORK.close()


class P2Tests(unittest.TestCase):
    def setUp(self):
        self.data, self.methods, self.entities = p2.load_inputs()

    def build(self):
        return p2.build_bundle(self.data, self.methods, self.entities)

    def content(self):
        return self.build()["content"]

    def index(self, kind, predicate=lambda row: True):
        return next(i for i, r in enumerate(self.data["facts"]) if r["payload"]["kind"] == kind and predicate(r))

    def edit(self, index, fields=None, payload=None):
        row = self.data["facts"][index]
        old_id = digest(row)
        row.update(fields or {})
        row["payload"].update(payload or {})
        row["content_hash"] = digest(row["payload"])
        new_id = digest(row)
        for other in self.data["facts"]:
            if old_id in other["payload"].get("news_refs", []):
                other["payload"]["news_refs"] = [new_id if r == old_id else r for r in other["payload"]["news_refs"]]
                other["content_hash"] = digest(other["payload"])
        return new_id

    def add(self, row):
        row = copy.deepcopy(row)
        row["content_hash"] = digest(row["payload"])
        self.data["facts"].append(row)
        return digest(row)

    def event_case(self, target, planned=None, occurred=None):
        prototype = copy.deepcopy(self.data["facts"][self.index("event")])
        self.data["facts"] = [r for r in self.data["facts"] if r["payload"]["kind"] in {"bar", "news"}]
        prototype["payload"].update(asserted_state=target, planned_at=planned, occurred_at=occurred)
        prototype["published_at"] = "2020-01-10T09:00:00Z"
        prototype["locator"] = "https://example.invalid/primary/event-current"
        if target in {"delayed", "cancelled", "superseded"}:
            first = copy.deepcopy(prototype)
            first["published_at"] = "2020-01-10T08:30:00Z"
            first["payload"].update(asserted_state="scheduled", planned_at="2020-01-11T12:00:00Z", occurred_at=None)
            self.add(first)
        if target == "superseded":
            replacement = copy.deepcopy(prototype)
            replacement["payload"].update(event_id="SYN_REPLACEMENT", asserted_state="scheduled", planned_at="2020-01-20T12:00:00Z")
            self.add(replacement)
            prototype["payload"]["supersedes"] = "SYN_REPLACEMENT"
        self.add(prototype)

    def assert_block(self, reason=None):
        content = self.content()
        self.assertEqual(content["status"], "BLOCK")
        self.assertEqual(content["references"], [])
        if reason:
            self.assertIn(reason, canonical(content))
        return content

    def test_default_profile_and_fixed_live_gap(self):
        content = self.content()
        self.assertEqual(content["status"], "DEGRADED")
        self.assertEqual(content["confidence_ceiling"], "limited")
        self.assertEqual({k for k, v in content["domains"].items() if v["status"] == "PASS"},
                         {"market", "sector", "news", "event", "entity"})
        self.assertEqual(content["live_market_coverage"]["covered"], 0)
        self.assertEqual(content["live_market_coverage"]["expected"], 5)
        self.assertEqual(content["readiness"], "BLOCKED_MARKET_SOURCES")
        self.assertEqual(len(content["references"]), 40)
        self.assertEqual(content["rejected"], [])

    def test_breadth_turnover_equal_weight_and_rotation(self):
        market = self.content()["results"]["market"]
        self.assertEqual([market["breadth"][k] for k in ("advancing", "declining", "unchanged")], [1, 1, 2])
        self.assertEqual(market["breadth"]["expected"], 4)
        self.assertEqual(market["turnover"]["value"], 1240)
        self.assertAlmostEqual(market["turnover"]["relative"], 1240 / 1160)
        technology = next(r for r in market["sectors"] if r["sector_id"] == "synthetic_technology")
        self.assertAlmostEqual(technology["equal_weight_return"], ((110 / 103 - 1) + (104 / 102 - 1)) / 2)
        self.assertAlmostEqual(technology["turnover_share"], 420 / 1240)
        self.assertEqual(technology["rank"], 1)
        self.assertIsInstance(technology["rank_change"], int)

    def test_insufficient_history_never_invents_rank(self):
        self.data["facts"] = [r for r in self.data["facts"] if not (r["payload"]["kind"] == "bar"
                              and r["payload"]["symbol"] == "SYN_A" and r["payload"]["session_id"] == "2020-01-02")]
        content = self.assert_block("insufficient_history")
        for sector in content["results"]["market"]["sectors"]:
            self.assertIsNone(sector["rank"])
            self.assertIsNone(sector["equal_weight_return"])

    def test_missing_constituent_preserves_denominator(self):
        self.data["facts"].pop(self.index("bar", lambda r: r["payload"]["symbol"] == "SYN_D"
                                          and r["payload"]["session_id"] == "2020-01-10"))
        breadth = self.assert_block()["results"]["market"]["breadth"]
        self.assertEqual((breadth["eligible"], breadth["expected"]), (3, 4))
        self.assertIsNone(breadth["ratios"])

    def test_zero_baseline_not_zero_flow_or_infinite_ratio(self):
        for i, r in enumerate(self.data["facts"]):
            if r["payload"]["kind"] == "bar" and r["payload"]["symbol"].startswith("SYN_") and r["payload"]["session_id"] < "2020-01-10":
                self.edit(i, payload={"turnover": 0})
        content = self.assert_block("zero_baseline")
        self.assertIsNone(content["results"]["market"]["turnover"]["relative"])
        self.assertIsNone(content["results"]["flow"]["value"])

    def test_cross_currency_or_unit_sum_is_rejected(self):
        for key, value in (("currency", "USD"), ("unit", "shares")):
            with self.subTest(key=key):
                entities = copy.deepcopy(self.entities)
                entities["entities"][-1][key] = value
                with self.assertRaises(Invalid):
                    p2.build_bundle(self.data, self.methods, entities)

    def test_unavailable_flow_types_and_required_rule(self):
        for kind in ("provider_estimate", "exchange_statistic", "turnover_proxy"):
            value = UnavailableFlowAdapter().evaluate("unconfigured", kind, self.data["as_of"]).to_dict()
            self.assertEqual(value["series_kind"], kind)
            self.assertIsNone(value["value"])
        rule = next(r for r in self.methods["rules"] if r["domain"] == "flow")
        rule.update(required=True, allow_degraded=False)
        self.assert_block("not_integrated")

    def test_dedup_members_and_explainable_rank(self):
        news = self.content()["results"]["news"]
        self.assertEqual(len(news), 1)
        self.assertEqual(len(news[0]["members"]), 2)
        self.assertEqual(news[0]["score"], 5)
        self.assertEqual({r["authority"] for r in news[0]["factors"]}, {4, 2})
        self.assertEqual(len(news[0]["member_ids"]), 2)

    def test_decay_and_staleness_are_not_fresh_confirmation(self):
        i = self.index("news")
        self.edit(i, fields={"published_at": "2020-01-09T08:00:00Z"})
        factors = self.content()["results"]["news"][0]["factors"]
        self.assertIn(1, [r["decay_steps"] for r in factors])
        stale_id = self.edit(i, fields={"published_at": "2020-01-01T08:00:00Z"})
        content = self.assert_block("stale_news")
        self.assertIn(stale_id, [r["fact_id"] for r in content["rejected"]])

    def test_conflicting_news_preserves_versions_without_promotion(self):
        row = copy.deepcopy(self.data["facts"][self.index("news")])
        row["payload"]["title"] = "SYNTHETIC Synthetic Alpha contradictory correction"
        self.add(row)
        content = self.assert_block("conflicting_news_versions")
        self.assertEqual(content["results"]["news"], [])

    def test_ambiguous_unknown_entities_and_injection_are_data(self):
        self.assertEqual(map_entities("Synthetic Shared", self.entities)["status"], "ambiguous")
        self.assertEqual(map_entities("not a known entity", self.entities)["status"], "unresolved")
        self.assertEqual(map_entities("Synthetic Alpha; __import__('os').system('NEVER')", self.entities)["entity_ids"], ["SYN_A"])
        self.edit(self.index("news"), payload={"title": "SYNTHETIC Synthetic Shared"})
        self.assert_block("ambiguous_entity")

    def test_all_nine_event_states(self):
        observed = set()
        cases = [("discovered", None, None, "discovered"),
                 ("scheduled", "2020-01-20T12:00:00Z", None, "scheduled"),
                 ("scheduled", "2020-01-11T00:00:00Z", None, "approaching"),
                 ("scheduled", "2020-01-10T00:00:00Z", None, "awaiting_confirmation"),
                 ("confirmed", None, "2020-01-09T07:00:00Z", "confirmed"),
                 ("delayed", "2020-01-20T12:00:00Z", None, "delayed"),
                 ("cancelled", None, None, "cancelled"),
                 ("scheduled", "2020-01-01T00:00:00Z", None, "expired"),
                 ("superseded", None, None, "superseded")]
        for asserted, planned, occurred, expected in cases:
            with self.subTest(state=expected):
                self.setUp()
                self.event_case(asserted, planned, occurred)
                content = self.content()
                self.assertEqual(content["domains"]["event"]["status"], "PASS", content["domains"]["event"])
                event = next(r for r in content["results"]["events"]["items"] if r["event_id"] == "SYN_EVENT")
                self.assertEqual(event["state"], expected)
                observed.add(event["state"])
                if expected != "confirmed":
                    self.assertIsNone(event["occurred_at"])
                    self.assertIsNone(event["success"])
        self.assertEqual(observed, STATES)

    def test_time_passage_does_not_confirm_or_infer_success(self):
        self.event_case("scheduled", "2020-01-10T00:00:00Z")
        event = self.content()["results"]["events"]["items"][0]
        self.assertEqual(event["state"], "awaiting_confirmation")
        self.assertTrue(event["confirmation_overdue"])
        self.assertIsNone(event["success"])

    def test_confirmation_requires_actual_time_and_authorized_news(self):
        self.event_case("confirmed")
        self.assert_block("actual_occurrence_required")
        self.setUp()
        index = self.index("event", lambda r: r["payload"]["asserted_state"] == "confirmed")
        secondary = digest(self.data["facts"][self.index("news", lambda r: r["source_id"] == "synthetic_secondary")])
        self.edit(index, payload={"news_refs": [secondary]})
        self.assert_block("news_authority_scope")

    def test_tier_c_alone_cannot_confirm(self):
        row = copy.deepcopy(self.data["facts"][self.index("news")])
        row.update(source_id="synthetic_lead", locator="https://example.invalid/lead/news")
        lead = self.add(row)
        self.edit(self.index("event", lambda r: r["payload"]["asserted_state"] == "confirmed"), payload={"news_refs": [lead]})
        self.assert_block("news_authority_scope")

    def test_unknown_after_asof_and_expired_news_refs_block(self):
        for change in ("unknown", "future", "expired"):
            with self.subTest(change=change):
                self.setUp()
                if change == "unknown":
                    self.edit(self.index("event"), payload={"news_refs": ["0" * 64]})
                elif change == "future":
                    self.edit(self.index("news"), fields={"retrieved_at": "2020-01-11T00:00:00Z"})
                else:
                    self.edit(self.index("news"), fields={"expires_at": "2020-01-10T11:30:00Z"})
                self.assert_block("unknown_or_ineligible_news_ref")

    def test_terminal_reopening_and_contradictory_assertions_block(self):
        self.event_case("confirmed", occurred="2020-01-09T07:00:00Z")
        row = copy.deepcopy(self.data["facts"][-1])
        row["published_at"] = "2020-01-10T10:00:00Z"
        row["payload"].update(asserted_state="scheduled", occurred_at=None, planned_at="2020-01-20T12:00:00Z")
        self.add(row)
        self.assert_block("illegal_event_transition")
        self.setUp()
        row = copy.deepcopy(self.data["facts"][self.index("event")])
        row["payload"]["asserted_state"] = "cancelled"
        self.add(row)
        self.assert_block("conflicting_event_assertions")

    def test_source_specific_event_authority_is_enforced(self):
        self.methods["sources"][0]["event_ids"] = ["SYN_REPLACEMENT"]
        self.assert_block("source_event_scope")

    def test_expired_terminal_cannot_reopen_via_late_confirmation(self):
        self.event_case("scheduled", "2020-01-01T00:00:00Z")
        row = copy.deepcopy(self.data["facts"][-1])
        row["published_at"] = "2020-01-10T10:00:00Z"
        row["payload"].update(asserted_state="confirmed", occurred_at="2020-01-09T07:00:00Z")
        self.add(row)
        self.assert_block("illegal_event_transition")

    def test_news_possession_cannot_backfill_earlier_assertion(self):
        self.edit(self.index("event"), fields={"first_seen_at": "2020-01-10T10:00:00Z"})
        self.assert_block("news_not_available_at_assertion")

    def test_unknown_optional_validator_also_denies_evidence(self):
        rule = next(r for r in self.methods["rules"] if r["domain"] == "news")
        rule.update(required=False, allow_degraded=True, validator="not-implemented")
        self.assert_block("missing_validator")

    def test_decay_remains_bounded_at_extreme_valid_configuration(self):
        self.methods["news_half_life_seconds"] = 1
        for item in self.content()["results"]["news"]:
            self.assertEqual(item["score"], 0)

    def test_reaction_must_follow_actual_occurrence(self):
        self.edit(self.index("event", lambda r: r["payload"]["asserted_state"] == "confirmed"),
                  payload={"occurred_at": "2020-01-10T07:00:00Z"})
        self.assert_block("reaction_precedes_occurrence")

    def test_benchmark_conflict_does_not_fabricate_sector_failure(self):
        row = copy.deepcopy(self.data["facts"][self.index("bar", lambda r: r["payload"]["symbol"] == "SP500")])
        row["payload"]["close"] = 999
        self.add(row)
        content = self.assert_block("conflicting_market_evidence")
        self.assertEqual(content["domains"]["sector"]["status"], "PASS")

    def test_news_cap_and_large_number_are_bounded(self):
        prototype = copy.deepcopy(self.data["facts"][self.index("news")])
        for i in range(100):
            row = copy.deepcopy(prototype)
            row["locator"] += "/" + str(i)
            self.add(row)
        with self.assertRaisesRegex(Invalid, "bound"):
            self.build()
        self.setUp()
        self.edit(0, payload={"close": 10 ** 1000})
        with self.assertRaises(Invalid):
            self.build()

    def test_outcome_impact_reaction_are_separate(self):
        event = next(r for r in self.content()["results"]["events"]["items"] if r["event_id"] == "SYN_EVENT")
        self.assertEqual(event["state"], "confirmed")
        self.assertIsNone(event["success"])
        self.assertEqual(event["expected_impact"]["kind"], "hypothesis")
        self.assertFalse(event["observed_reaction"]["causal_attribution"])
        self.assertAlmostEqual(event["observed_reaction"]["return"], 110 / 107 - 1)

    def test_explicit_empty_day_not_missing_domain(self):
        prototype = copy.deepcopy(self.data["facts"][self.index("event")])
        self.data["facts"] = [r for r in self.data["facts"] if r["payload"]["kind"] in {"bar", "news"}]
        self.assert_block("missing_explicit_event_day")
        prototype["payload"] = {"kind": "event_day", "empty": True, "news_refs": prototype["payload"]["news_refs"]}
        self.add(prototype)
        self.assertTrue(self.content()["results"]["events"]["empty_day"])
        self.assertEqual(self.content()["domains"]["event"]["status"], "PASS")

    def test_market_conflict_excluded_and_no_silent_value_selection(self):
        row = copy.deepcopy(self.data["facts"][0])
        row["payload"]["close"] = 999
        self.add(row)
        self.assert_block("conflicting_market_evidence")

    def test_missing_validator_and_unknown_config_fail_closed(self):
        self.methods["rules"][0]["validator"] = "not-implemented"
        self.assert_block("missing_required_validator")
        self.methods["unknown"] = True
        with self.assertRaises(Invalid):
            self.build()

    def test_strict_unknown_fields_and_duplicate_identity(self):
        for target in ("root", "fact", "payload", "entities"):
            with self.subTest(target=target):
                self.setUp()
                if target == "root":
                    self.data["unknown"] = True
                elif target == "entities":
                    self.entities["entities"][0]["unknown"] = True
                elif target == "fact":
                    self.data["facts"][0]["unknown"] = True
                else:
                    self.edit(0, payload={"unknown": True})
                with self.assertRaises(Invalid):
                    self.build()
        self.setUp()
        self.data["facts"].append(copy.deepcopy(self.data["facts"][0]))
        with self.assertRaisesRegex(Invalid, "duplicate"):
            self.build()

    def test_invalid_numbers_and_content_hash(self):
        for value in (True, False, 0, -1, float("nan"), float("inf"), "100"):
            with self.subTest(value=value):
                self.setUp()
                if type(value) is float and value != value or value == float("inf"):
                    self.data["facts"][0]["payload"]["close"] = value
                else:
                    self.edit(0, payload={"close": value})
                with self.assertRaises(Invalid):
                    self.build()
        self.setUp()
        self.data["facts"][0]["content_hash"] = "0" * 64
        with self.assertRaises(Invalid):
            self.build()

    def test_modes_dates_calendar_and_provider_mismatch(self):
        for fields in ({"mode": "historical_replay"}, {"published_at": "2020-01-11T00:00:00Z"},
                       {"first_seen_at": "2020-01-11T00:00:00Z"}):
            self.setUp()
            self.edit(0, fields=fields)
            self.assert_block()
        for update in ({"as_of": "2020-01-10T12:00:00"}, {"as_of": "2090-01-01T00:00:00Z"}, {"mode": "live_observation"}):
            self.setUp()
            self.data.update(update)
            with self.assertRaises(Invalid):
                self.build()
        self.setUp()
        self.edit(0, payload={"session_id": "2020-02-30"})
        with self.assertRaises(Invalid):
            self.build()

    def test_caps_news_events_references_and_json_parser(self):
        self.event_case("discovered")
        prototype = copy.deepcopy(self.data["facts"][-1])
        for n in range(21):
            row = copy.deepcopy(prototype)
            row["payload"]["event_id"] = "CAP_" + str(n)
            self.add(row)
        with self.assertRaisesRegex(Invalid, "bound"):
            self.build()
        self.setUp()
        self.edit(self.index("event"), payload={"news_refs": [format(n, "064x") for n in range(9)]})
        with self.assertRaises(Invalid):
            self.build()
        for body in (b'{"a":1,"a":2}', b'{"a":NaN}', b'x' * (p2.MAX_BYTES + 1)):
            with self.assertRaises(Invalid):
                p2.decode_input(body)

    def test_optional_excluded_news_never_becomes_usable(self):
        for rule in self.methods["rules"]:
            if rule["domain"] in {"news", "event"}:
                rule.update(required=False, allow_degraded=True)
        excluded = self.edit(self.index("news"), fields={"expires_at": "2020-01-10T11:30:00Z"})
        content = self.content()
        self.assertEqual(content["status"], "DEGRADED")
        self.assertNotIn(excluded, {r["fact_id"] for r in content["references"]})
        self.assertTrue({r["fact_id"] for r in content["references"]}.isdisjoint({r["fact_id"] for r in content["rejected"]}))

    def test_reaction_rejects_later_bar_versions(self):
        for session in ("2020-01-09", "2020-01-10"):
            for fields in ({"first_seen_at": "2020-01-10T11:30:00Z", "retrieved_at": "2020-01-10T11:30:00Z"},
                           {"retrieved_at": "2020-01-10T11:30:00Z"},
                           {"published_at": "2020-01-10T10:40:00Z"}):
                self.setUp()
                target = self.data["facts"][self.index("reaction")]["payload"]["event_id"]
                index = self.index("bar", lambda r: r["payload"]["symbol"] == "SYN_A" and r["payload"]["session_id"] == session)
                self.edit(index, fields=fields)
                with self.subTest(session=session, fields=fields):
                    content = self.assert_block("reaction_version_not_available_at_assertion")
                    self.assertEqual(content["domains"]["market"]["status"], "PASS")
                    self.assertEqual(content["domains"]["event"]["status"], "BLOCK")
                    self.assertFalse(any(e["event_id"] == target for e in content["results"]["events"]["items"]))

    def test_reaction_checks_every_equivalent_bar_version(self):
        index = self.index("bar", lambda r: r["payload"]["symbol"] == "SYN_A" and r["payload"]["session_id"] == "2020-01-10")
        later = copy.deepcopy(self.data["facts"][index])
        later["retrieved_at"] = "2020-01-10T11:30:00Z"
        later["first_seen_at"] = "2020-01-10T11:30:00Z"
        self.add(later)
        content = self.assert_block("reaction_version_not_available_at_assertion")
        self.assertEqual(content["domains"]["market"]["status"], "PASS")

    def test_reaction_possession_cannot_backfill_first_seen_assertion(self):
        for index, row in enumerate(self.data["facts"]):
            if row["payload"]["kind"] == "news":
                self.edit(index, fields={"first_seen_at": "2020-01-10T10:00:00Z", "retrieved_at": "2020-01-10T10:00:00Z"})
            elif row["payload"]["kind"] == "event":
                self.edit(index, fields={"first_seen_at": "2020-01-10T10:10:00Z", "retrieved_at": "2020-01-10T10:10:00Z"})
        self.edit(self.index("reaction"), fields={"first_seen_at": "2020-01-10T10:20:00Z"})
        index = self.index("bar", lambda r: r["payload"]["symbol"] == "SYN_A" and r["payload"]["session_id"] == "2020-01-10")
        self.edit(index, fields={"first_seen_at": "2020-01-10T10:30:00Z", "retrieved_at": "2020-01-10T10:30:00Z"})
        content = self.assert_block("reaction_version_not_available_at_assertion")
        self.assertEqual(content["domains"]["market"]["status"], "PASS")

    def test_reaction_cannot_use_later_confirmation_version(self):
        index = self.index("event", lambda r: r["payload"]["asserted_state"] == "confirmed")
        self.edit(index, fields={"first_seen_at": "2020-01-10T11:30:00Z", "retrieved_at": "2020-01-10T11:30:00Z"})
        self.assert_block("reaction_version_not_available_at_assertion")

    def test_reaction_version_equal_boundary_and_unknown_publication(self):
        for publication in ("2020-01-10T10:20:00Z", None):
            self.setUp()
            for session in ("2020-01-09", "2020-01-10"):
                index = self.index("bar", lambda r: r["payload"]["symbol"] == "SYN_A" and r["payload"]["session_id"] == session)
                self.edit(index, fields={"published_at": publication, "retrieved_at": "2020-01-10T11:00:00Z",
                                         "first_seen_at": "2020-01-10T11:00:00Z"})
            with self.subTest(publication=publication):
                content = self.content()
                self.assertEqual(content["domains"]["event"]["status"], "PASS")
                self.assertTrue(any(e["observed_reaction"] is not None for e in content["results"]["events"]["items"]))

    def test_committed_demo_has_verifiable_cli_content(self):
        sample = p2.decode_input((p2.ROOT / "docs/evidence/p2-demo.json").read_bytes())
        self.assertIn("SYNTHETIC", sample["label"])
        self.assertLessEqual(instant(sample["generated_at"]), utc_now())
        self.assertTrue(p2.verify_bundle(sample["bundle"], self.data, self.methods, self.entities))
        self.assertEqual(sample["bundle"], self.build())

    def test_determinism_full_fingerprints_and_recomputation(self):
        original = self.build()
        self.assertEqual(original, self.build())
        self.assertTrue(p2.verify_bundle(original, self.data, self.methods, self.entities))
        for target in ("source", "method", "mapping", "calendar", "profile"):
            self.setUp()
            if target == "source":
                self.methods["sources"][1]["tier"] = "C"
            elif target == "method":
                self.methods["version"] += "-changed"
            elif target == "mapping":
                self.entities["version"] += "-changed"
            elif target == "calendar":
                self.data["calendar"]["version"] += "-changed"
            else:
                self.methods["profile_version"] += "-changed"
            self.assertNotEqual(original["bundle_id"], self.build()["bundle_id"], target)
        self.setUp()
        forged = copy.deepcopy(original)
        forged["content"]["readiness"] = "READY"
        with self.assertRaisesRegex(Invalid, "hash"):
            p2.verify_bundle(forged, self.data, self.methods, self.entities)
        forged["bundle_id"] = digest(forged["content"])
        with self.assertRaisesRegex(Invalid, "recomputation"):
            p2.verify_bundle(forged, self.data, self.methods, self.entities)

    def test_demo_actual_envelope_fixed_core_and_no_writes(self):
        before = utc_now()
        with patch.object(Path, "write_text", side_effect=forbidden), patch.object(Path, "write_bytes", side_effect=forbidden):
            first, second = p2.demo(), p2.demo()
        self.assertLessEqual(before, instant(first["generated_at"]))
        self.assertLessEqual(instant(first["generated_at"]), utc_now())
        self.assertNotEqual(first["run_id"], second["run_id"])
        self.assertEqual(first["bundle"], second["bundle"])
        self.assertEqual(first["bundle"]["content"]["as_of"], "2020-01-10T12:00:00Z")

    def test_functional_cli_has_no_network_or_file_writes(self):
        child = (
            "import socket,http.client,urllib.request,runpy,sys,os; "
            "deny=lambda *a,**k: (_ for _ in ()).throw(AssertionError('FORBIDDEN')); "
            "socket.socket=socket.create_connection=socket.getaddrinfo=deny; "
            "http.client.HTTPConnection.request=urllib.request.urlopen=deny; "
            "sys.addaudithook(lambda e,a: deny() if e=='open' and isinstance(a[2],int) and a[2] & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC) else None); "
            "sys.argv=['market_intelligence.intelligence','demo']; "
            "runpy.run_module('market_intelligence.intelligence',run_name='__main__')"
        )
        completed = subprocess.run([sys.executable, "-B", "-c", child], cwd=p2.ROOT,
                                   capture_output=True, text=True, timeout=20)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        value = json.loads(completed.stdout)
        self.assertIn("SYNTHETIC", value["label"])
        self.assertNotIn(str(p2.ROOT), completed.stdout)
        self.assertLess(len(completed.stdout), 2 * p2.MAX_BYTES)

    def test_cli_rejects_paths_and_redacts_operational_errors(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(p2.main(["demo", "--output", "PRIVATE_PATH"]), 2)
        self.assertNotIn("PRIVATE", output.getvalue())
        output = io.StringIO()
        with patch.object(p2, "load_inputs", side_effect=OSError("PRIVATE_SECRET")), redirect_stdout(output):
            self.assertEqual(p2.main(["demo"]), 2)
        self.assertEqual(json.loads(output.getvalue()), {"reason": "synthetic_demo_failed", "status": "ERROR"})


if __name__ == "__main__":
    unittest.main()
