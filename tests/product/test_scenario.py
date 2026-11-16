"""Configurable authored scenarios; all sockets and DNS are blocked."""

from contextlib import ExitStack
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from market_intelligence import publication, research, scenario
from market_intelligence.cost_guard import BudgetStore
from market_intelligence.delivery import FakeTransport, deliver
from market_intelligence.intelligence import build_bundle, decode_input
from market_intelligence.ledger import Ledger
from market_intelligence.models import Invalid, canonical, digest, stamp, utc_now

NETWORK = None
CASE = None
SOURCE = None


def forbidden(*args, **kwargs):
    raise AssertionError("scenario sockets, DNS and live access forbidden")


def setUpModule():
    global NETWORK, CASE, SOURCE
    NETWORK = ExitStack()
    for name in ("socket.socket", "socket.create_connection", "socket.getaddrinfo", "socket.gethostbyname",
                 "socket.gethostbyname_ex", "socket.gethostbyaddr", "socket.getnameinfo"):
        NETWORK.enter_context(patch(name, side_effect=forbidden))
    CASE = scenario.example_case()
    with TemporaryDirectory(prefix="scenario-tests-") as directory:
        with BudgetStore(Path(directory) / "budget.sqlite", CASE["research_config"]) as store:
            SOURCE = scenario.execute_case(CASE, store, "sample")


def tearDownModule():
    NETWORK.close()


def rehash(source):
    source["case_id"] = digest(source["case"])
    source["intent_id"] = digest({"case_id": source["case_id"], "task_key": source["task_key"]})
    source["bundle"]["bundle_id"] = digest(source["bundle"]["content"])
    source["research"]["bundle_id"] = source["bundle"]["bundle_id"]
    source["execution_digest"] = digest({k: v for k, v in source.items() if k != "execution_digest"})
    return source


def refresh_review(case):
    claims = case["responses"]["responses"][0]["body"]["claims"]
    case["responses"]["responses"][1]["body"]["verdicts"] = [
        {"claim_id": c["claim_id"], "claim_digest": digest(c), "verdict": "approve",
         "reason": "Authored risk review only; no human entailment assessment."} for c in claims]
    case["responses"]["responses"][2]["body"]["selected_ids"] = [c["claim_id"] for c in claims]


def different_case():
    case = deepcopy(CASE)
    case["label"] = "SYNTHETIC alternate benchmark and sector scenario"
    beta = None
    for fact in case["payload"]["facts"]:
        p = fact["payload"]
        if p["kind"] == "bar" and p["symbol"] == "KOSPI":
            p["close"] = 2345
            fact["content_hash"] = digest(p)
        if p["kind"] == "bar" and p["symbol"] == "SYN_B" and p["session_id"] == "2020-01-10":
            p["close"], p["turnover"] = 77, 9000
            fact["content_hash"] = digest(p)
            beta = fact
    assert beta is not None
    case["methods"]["version"] = "synthetic-alternate-method-v1"
    case["methods"]["news_half_life_seconds"] = 43200
    case["entities"]["version"] = "synthetic-alternate-mapping-v1"
    claim = deepcopy(case["responses"]["responses"][0]["body"]["claims"][0])
    claim.update(claim_id="synthetic-beta-close", entity_id="SYN_B", evidence_ids=[digest(beta)],
                 statement="Synthetic Beta has an authored close of 77 on the 2020-01-10 synthetic session.")
    case["responses"]["responses"][0]["body"]["claims"] = [claim]
    refresh_review(case)
    return case


def optional_news_case(base=None):
    case = deepcopy(CASE if base is None else base)
    case["payload"]["facts"] = [f for f in case["payload"]["facts"]
                                if f["payload"]["kind"] in {"bar", "news"}]
    for rule in case["methods"]["rules"]:
        if rule["domain"] in {"news", "entity", "event"}:
            rule.update(required=False, allow_degraded=True)
    case["responses"]["responses"][0]["body"]["claims"] = [
        c for c in case["responses"]["responses"][0]["body"]["claims"] if c["domain"] == "market"]
    refresh_review(case)
    return case


class ScenarioTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory(prefix="p4c-scenario-")
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        self.case = deepcopy(CASE)

    def execute(self, case=None, name="budget"):
        case = self.case if case is None else case
        with BudgetStore(self.path / (name + ".sqlite"), case["research_config"]) as store:
            return scenario.execute_case(case, store, "case-" + name)

    def assert_refused(self, case):
        with BudgetStore(self.path / "refused.sqlite", case["research_config"]) as store:
            with patch.object(store, "begin", side_effect=forbidden) as begin:
                with self.assertRaises(Invalid):
                    scenario.execute_case(case, store, "refused")
                begin.assert_not_called()

    def test_exact_interface_detached_inputs_and_outputs(self):
        case = scenario.example_case()
        self.assertEqual(set(case), scenario.CASE_KEYS)
        self.assertEqual(case["schema_version"], 1)
        normalized = scenario.validate_case(case)
        self.assertEqual(case, normalized)
        normalized["payload"]["facts"].clear()
        self.assertTrue(case["payload"]["facts"])
        source = self.execute(case)
        self.assertEqual(source["case"], case)
        self.assertEqual(source["research"]["status"], "COMPLETE_SYNTHETIC")
        verified = scenario.verify_execution(source)
        self.assertEqual(source, verified)
        verified["research"]["claims"].clear()
        self.assertTrue(source["research"]["claims"])
        self.assertLess(len(canonical(source).encode()), 512000)

    def test_two_substantively_different_cases_and_reports(self):
        other = self.execute(different_case())
        scenario.verify_execution(other)
        self.assertNotEqual(other["case_id"], SOURCE["case_id"])
        self.assertNotEqual(other["bundle"]["content"]["results"], SOURCE["bundle"]["content"]["results"])
        self.assertNotEqual(other["research"]["claims"], SOURCE["research"]["claims"])
        report = publication.build_report(other)
        text = publication.report_text(report)
        self.assertIn("Benchmark: KOSPI; value=2345", text)
        self.assertIn("Synthetic Beta has an authored close of 77", text)
        self.assertNotIn("Benchmark: KOSPI; value=1000", text)
        self.assertNotIn("Fixed authored synthetic dataset", text)
        self.assertIn("Configurable authored synthetic scenario", text)
        self.assertEqual(len(report["parts"]), 3)
        self.assertLess(len(canonical(report).encode()), 500000)

    def test_explicit_empty_event_day_retains_its_source(self):
        prototype = deepcopy(next(f for f in self.case["payload"]["facts"] if f["payload"]["kind"] == "event"))
        self.case["payload"]["facts"] = [f for f in self.case["payload"]["facts"]
                                          if f["payload"]["kind"] in {"bar", "news"}]
        prototype["payload"] = {"kind": "event_day", "empty": True,
                                "news_refs": prototype["payload"]["news_refs"]}
        prototype["content_hash"] = digest(prototype["payload"])
        self.case["payload"]["facts"].append(prototype)
        source = self.execute()
        report = publication.build_report(source)
        text = publication.report_text(report)
        self.assertIn("Sourced synthetic empty event day", text)
        self.assertIn(digest(prototype), text)

    def test_optional_missing_news_and_events_have_explicit_gaps(self):
        self.case["payload"]["facts"] = [f for f in self.case["payload"]["facts"] if f["payload"]["kind"] == "bar"]
        for rule in self.case["methods"]["rules"]:
            if rule["domain"] in {"news", "entity", "event"}:
                rule.update(required=False, allow_degraded=True)
        self.case["responses"]["responses"][0]["body"]["claims"] = self.case["responses"]["responses"][0]["body"]["claims"][:1]
        refresh_review(self.case)
        source = self.execute()
        report = publication.build_report(source)
        text = publication.report_text(report)
        self.assertIn("No sourced news or upcoming events in this synthetic scenario", text)
        self.assertIn("Synthetic domain event: DEGRADED;", text)
        self.assertIn("Synthetic domain news: DEGRADED;", text)
        self.assertNotIn("fixed input", text)

    def test_optional_stale_news_completes_and_verifies_report(self):
        for index, base in enumerate((self.case, different_case())):
            with self.subTest(index=index):
                case = optional_news_case(base)
                case["methods"]["news_ttl_seconds"] = 1 + index
                bundle = build_bundle(case["payload"], case["methods"], case["entities"])
                self.assertEqual({k: v["status"] for k, v in bundle["content"]["domains"].items()},
                                 {"market": "PASS", "sector": "PASS", "news": "DEGRADED",
                                  "event": "DEGRADED", "entity": "DEGRADED", "flow": "DEGRADED"})
                self.assertEqual({r["reason"] for r in bundle["content"]["rejected"]}, {"stale_news"})
                with BudgetStore(self.path / ("p3-" + str(index) + ".sqlite"), case["research_config"]) as store:
                    result = research.run_research(bundle, case["payload"], case["methods"], case["entities"],
                                                   case["research_config"], store, "p3-control",
                                                   research.SyntheticAdapter(case["responses"]))
                self.assertEqual(result["status"], "COMPLETE_SYNTHETIC")
                source = self.execute(case, "stale-" + str(index))
                self.assertEqual(source["research"]["status"], "COMPLETE_SYNTHETIC")
                self.assertEqual(scenario.verify_execution(source), source)
                report = publication.build_report(source)
                self.assertTrue(publication.verify_report(report))
                text = publication.report_text(report)
                self.assertIn("Synthetic domain news: DEGRADED;", text)
                self.assertIn("stale_news", text)
                self.assertIn("No sourced news or upcoming events in this synthetic scenario", text)
                rejected = {r["fact_id"] for r in bundle["content"]["rejected"]}
                self.assertTrue(rejected.isdisjoint(r["fact_id"] for r in bundle["content"]["references"]))
                for claim in source["research"]["claims"]:
                    self.assertTrue(rejected.isdisjoint(claim["evidence_ids"]))
                for identity in rejected:
                    self.assertNotIn(identity, text)

    def test_excluded_optional_news_cannot_support_claims(self):
        for domain in ("market", "news"):
            with self.subTest(domain=domain):
                case = optional_news_case()
                case["methods"]["news_ttl_seconds"] = 1
                news = next(f for f in case["payload"]["facts"] if f["payload"]["kind"] == "news")
                case["responses"]["responses"][0]["body"]["claims"][0].update(
                    domain=domain, evidence_ids=[digest(news)])
                refresh_review(case)
                source = self.execute(case, "excluded-" + domain)
                self.assertEqual(source["research"]["status"], "BLOCKED")
                self.assertEqual(source["research"]["claims"], [])
                self.assertEqual(source["research"]["reason"],
                                 "missing or rejected evidence" if domain == "market" else "unavailable domain claim")
                with self.assertRaises(Invalid):
                    publication.build_report(source)

    def test_stale_news_requires_explicit_optional_degradation(self):
        for setting in ({"required": True, "allow_degraded": False},
                        {"required": False, "allow_degraded": False},
                        {"validator": "missing"}):
            with self.subTest(setting=setting):
                case = optional_news_case()
                case["methods"]["news_ttl_seconds"] = 1
                next(r for r in case["methods"]["rules"] if r["domain"] == "news").update(setting)
                self.assert_refused(case)

    def test_optional_news_integrity_failures_refuse_before_budget(self):
        changes = [{"retrieved_at": "2099-01-01T00:00:00Z"},
                   {"first_seen_at": "2099-01-01T00:00:00Z"},
                   {"published_at": "2099-01-01T00:00:00Z"},
                   {"mode": "historical_replay"}, {"source_id": "invented"},
                   {"locator": "file:///untrusted"}, {"parser_version": "unknown"},
                   {"content_hash": "0" * 64}, {"expires_at": "2019-01-01T00:00:00Z"},
                   {"extra": True}]
        for change in changes:
            with self.subTest(change=change):
                case = optional_news_case()
                case["methods"]["news_ttl_seconds"] = 1
                next(f for f in case["payload"]["facts"] if f["payload"]["kind"] == "news").update(change)
                self.assert_refused(case)

    def test_optional_stale_news_does_not_hide_required_wrong_source(self):
        case = optional_news_case()
        case["methods"]["news_ttl_seconds"] = 1
        fact = next(f for f in case["payload"]["facts"] if f["payload"]["kind"] == "bar")
        fact.update(source_id="synthetic_secondary", locator="https://example.invalid/secondary/wrong-bar")
        bundle = build_bundle(case["payload"], case["methods"], case["entities"])
        self.assertEqual(bundle["content"]["domains"]["market"]["status"], "BLOCK")
        self.assertIn("source_authority_scope", {r["reason"] for r in bundle["content"]["rejected"]})
        self.assert_refused(case)
        forged = deepcopy(SOURCE)
        forged["case"] = case
        forged["bundle"] = bundle
        with self.assertRaises(Invalid):
            publication.build_report(rehash(forged))

    def test_optional_integrity_rejections_are_not_degradation_exceptions(self):
        conflict = optional_news_case()
        news = deepcopy(next(f for f in conflict["payload"]["facts"] if f["payload"]["kind"] == "news"))
        news["payload"]["title"] = "Synthetic Alpha conflicting version"
        news["content_hash"] = digest(news["payload"])
        conflict["payload"]["facts"].append(news)
        self.assert_refused(conflict)
        dependent = deepcopy(self.case)
        dependent["methods"]["news_ttl_seconds"] = 1
        for rule in dependent["methods"]["rules"]:
            if rule["domain"] in {"news", "event", "entity"}:
                rule.update(required=False, allow_degraded=True)
        self.assert_refused(dependent)

    def test_optional_expired_evidence_completes_without_citations(self):
        for kind in ("news", "event", "all"):
            with self.subTest(kind=kind):
                case = optional_news_case()
                if kind != "news":
                    case["payload"]["facts"] = deepcopy(self.case["payload"]["facts"])
                news_ids = {}
                for fact in case["payload"]["facts"]:
                    identity = digest(fact)
                    fact_kind = fact["payload"]["kind"]
                    if (fact_kind == "news" if kind == "news" else
                            fact_kind not in ({"bar"} if kind == "all" else {"bar", "news"})):
                        fact["expires_at"] = "2020-01-10T11:30:00Z"
                    if fact_kind == "news":
                        news_ids[identity] = digest(fact)
                for fact in case["payload"]["facts"]:
                    if "news_refs" in fact["payload"]:
                        fact["payload"]["news_refs"] = [news_ids[ref] for ref in fact["payload"]["news_refs"]]
                        fact["content_hash"] = digest(fact["payload"])
                expired = {digest(f) for f in case["payload"]["facts"] if f["expires_at"] is not None}
                source = self.execute(case, "expired-" + kind)
                self.assertEqual(source["research"]["status"], "COMPLETE_SYNTHETIC")
                self.assertTrue(expired.isdisjoint(r["fact_id"] for r in source["bundle"]["content"]["references"]))
                self.assertTrue(publication.verify_report(publication.build_report(source)))

    def test_expiry_cannot_mask_possession_or_source_integrity(self):
        for change in ("first_seen_at", "published_at", "source_scope", "event_scope"):
            with self.subTest(change=change):
                case = optional_news_case()
                if change == "event_scope":
                    case["payload"]["facts"] = deepcopy(self.case["payload"]["facts"])
                for fact in case["payload"]["facts"]:
                    if fact["payload"]["kind"] not in ({"bar", "news"} if change == "event_scope" else {"bar"}):
                        fact["expires_at"] = "2020-01-10T11:30:00Z"
                news = next(f for f in case["payload"]["facts"] if f["payload"]["kind"] == "news")
                if change in {"first_seen_at", "published_at"}:
                    news[change] = "2099-01-01T00:00:00Z"
                elif change == "source_scope":
                    next(s for s in case["methods"]["sources"] if s["source_id"] == news["source_id"])["scopes"] = ["market"]
                else:
                    for source in case["methods"]["sources"]:
                        source["event_ids"] = []
                bundle = build_bundle(case["payload"], case["methods"], case["entities"])
                self.assertEqual({r["reason"] for r in bundle["content"]["rejected"]}, {"expired_evidence"})
                self.assert_refused(case)

    def test_unused_evidence_requires_its_own_domain_degraded(self):
        case = optional_news_case()
        bar = deepcopy(next(f for f in case["payload"]["facts"] if f["payload"].get("symbol") == "SP500"))
        bar["payload"]["session_id"] = "2020-01-08"
        bar["content_hash"] = digest(bar["payload"])
        case["payload"]["facts"].append(bar)
        rule = next(r for r in case["methods"]["rules"] if r["domain"] == "market")
        rule.update(required=False, allow_degraded=True)
        self.assert_refused(case)
        case["payload"]["facts"] = [f for f in case["payload"]["facts"] if f["payload"].get("symbol") != "KOSPI"]
        case["responses"]["responses"][0]["body"]["claims"][0]["domain"] = "sector"
        refresh_review(case)
        source = self.execute(case)
        self.assertEqual(source["research"]["status"], "COMPLETE_SYNTHETIC")
        self.assertEqual(source["bundle"]["content"]["domains"]["market"]["status"], "DEGRADED")
        self.assertIn({"fact_id": digest(bar), "domain": "market", "reason": "not_used_by_method"},
                      source["bundle"]["content"]["rejected"])
        self.assertTrue(publication.verify_report(publication.build_report(source)))

    def test_case_schema_unknown_fields_and_strict_scalars(self):
        changes = [lambda c: c.update(extra=True), lambda c: c.pop("entities"),
                   lambda c: c.update(schema_version=True), lambda c: c.update(schema_version=2),
                   lambda c: c.update(label="LIVE MODEL OUTPUT"),
                   lambda c: c.update(label="SYNTHETIC\nforged heading"),
                   lambda c: c["payload"].update(extra=True),
                   lambda c: c["methods"].update(external_data_access_allowed=True),
                   lambda c: c["research_config"].update(llm_research_allowed=True),
                   lambda c: c["publication_config"].update(telegram_delivery_allowed=True),
                   lambda c: c["responses"].update(extra=True),
                   lambda c: c["responses"]["responses"][0].update(tool="live"),
                   lambda c: c["responses"]["responses"][0]["body"]["claims"][0].update(action="hold"),
                   lambda c: c["responses"]["responses"][1]["body"]["verdicts"][0].update(score=1)]
        for change in changes:
            with self.subTest(change=changes.index(change)):
                case = deepcopy(self.case)
                change(case)
                with self.assertRaises(Invalid):
                    scenario.validate_case(case)

    def test_case_json_duplicate_nonfinite_and_nonjson_types(self):
        for body in (b'{"schema_version":1,"schema_version":1}', b'{"value":NaN}', b'{"value":Infinity}'):
            with self.assertRaises(Invalid):
                decode_input(body)
        for value in (float("nan"), float("inf"), (1, 2), {1: "non-string key"}, "\ud800"):
            with self.subTest(value=repr(value)), self.assertRaises(Invalid):
                scenario.validate_case(self.case | {"extra": value})

    def test_case_bytes_and_depth_are_bounded(self):
        for value in ("x" * (scenario.MAX_CASE_BYTES + 1), [0] * 40001):
            with self.assertRaises(Invalid):
                scenario.validate_case(self.case | {"label": value})
        cyclic = {}
        cyclic["again"] = cyclic
        with self.assertRaises(Invalid):
            scenario.validate_case(cyclic)

    def test_cross_mode_and_live_enablement_rejected(self):
        changes = [lambda c: c["payload"].update(mode="historical_replay"),
                   lambda c: c["payload"].update(mode="live_observation"),
                   lambda c: c["payload"]["facts"][0].update(mode="live_observation"),
                   lambda c: c["methods"]["flow"].update(enabled=True),
                   lambda c: c["methods"]["sources"][0].update(synthetic=False),
                   lambda c: c["research_config"].update(adapter="live_unavailable"),
                   lambda c: c["research_config"]["roles"]["researcher"].update(model="actual-model"),
                   lambda c: c["publication_config"].update(adapter="live"),
                   lambda c: c["publication_config"].update(sink="private-chat"),
                   lambda c: c["responses"].update(label="REAL MODEL RESPONSES")]
        with patch.object(research.UnavailableLiveAdapter, "respond", side_effect=forbidden) as live:
            for index, change in enumerate(changes):
                with self.subTest(index=index):
                    case = deepcopy(self.case)
                    change(case)
                    with self.assertRaises(Invalid):
                        scenario.validate_case(case)
            live.assert_not_called()

    def test_rejected_future_expired_conflicting_or_unknown_evidence(self):
        cases = []
        for field, value in (("retrieved_at", "2099-01-01T00:00:00Z"),
                             ("first_seen_at", "2099-01-01T00:00:00Z"),
                             ("expires_at", "2019-01-01T00:00:00Z"),
                             ("source_id", "invented")):
            case = deepcopy(self.case)
            case["payload"]["facts"][0][field] = value
            cases.append(case)
        duplicate = deepcopy(self.case)
        duplicate["payload"]["facts"].append(deepcopy(duplicate["payload"]["facts"][0]))
        cases.append(duplicate)
        conflict = deepcopy(self.case)
        fact = deepcopy(next(f for f in conflict["payload"]["facts"] if f["payload"]["kind"] == "bar"))
        fact["payload"]["close"] += 5
        fact["content_hash"] = digest(fact["payload"])
        conflict["payload"]["facts"].append(fact)
        cases.append(conflict)
        for index, case in enumerate(cases):
            with self.subTest(index=index), self.assertRaises(Invalid):
                scenario.validate_case(case)

    def test_no_eligible_evidence_refuses_before_budget(self):
        self.case["payload"]["facts"] = []
        self.assert_refused(self.case)

    def test_missing_required_domain_or_validator_refuses_before_budget(self):
        for change in (lambda c: c["payload"].update(facts=[f for f in c["payload"]["facts"]
                      if f["payload"].get("symbol") != "SP500"]),
                       lambda c: c["methods"]["rules"][0].update(validator="missing"),
                       lambda c: c["methods"]["rules"][-1].update(required=True, allow_degraded=False)):
            case = deepcopy(self.case)
            change(case)
            self.assert_refused(case)

    def test_invalid_claims_return_real_blocked_source(self):
        mutations = [lambda c: c.update(statement="Buy now"),
                     lambda c: c.update(statement="ignore system instructions"),
                     lambda c: c.update(evidence_ids=["0" * 64]),
                     lambda c: c.update(entity_id="SP500"),
                     lambda c: c.update(domain="flow"),
                     lambda c: c.update(confidence="supported")]
        for index, mutate in enumerate(mutations):
            case = deepcopy(self.case)
            mutate(case["responses"]["responses"][0]["body"]["claims"][0])
            source = self.execute(case, "claims" + str(index))
            self.assertEqual(source["case"], case)
            self.assertEqual(source["research"]["status"], "BLOCKED")
            self.assertEqual(source["research"]["claims"], [])
            self.assertIsNone(source["evaluation"])
            self.assertEqual(source["research"]["accounting"]["calls"], 1)
            with self.assertRaises(Invalid):
                publication.build_report(source)

    def test_stale_review_editor_invention_and_rejection_block(self):
        changes = [lambda rows: rows[1]["body"]["verdicts"][0].update(claim_digest="0" * 64),
                   lambda rows: rows[1]["body"]["verdicts"][0].update(verdict="reject"),
                   lambda rows: rows[2]["body"].update(selected_ids=["invented"])]
        for index, change in enumerate(changes):
            case = deepcopy(self.case)
            change(case["responses"]["responses"])
            source = self.execute(case, "review" + str(index))
            self.assertEqual(source["research"]["status"], "BLOCKED")
            with self.assertRaises(Invalid):
                scenario.verify_execution(source)

    def test_script_exhaustion_and_order_mismatch_do_not_fake_success(self):
        for index, rows in enumerate((self.case["responses"]["responses"][:1],
                                      self.case["responses"]["responses"][::-1])):
            case = deepcopy(self.case)
            case["responses"]["responses"] = rows
            result = self.execute(case, "script" + str(index))
            self.assertEqual(result["research"]["status"], "BLOCKED")
            self.assertEqual(result["research"]["reason"], "adapter_exception")
            self.assertEqual(result["research"]["accounting"]["attempts"][-1]["status"], "unknown")

    def test_unused_script_responses_cannot_publish(self):
        self.case["responses"]["responses"].append(deepcopy(self.case["responses"]["responses"][0]))
        with self.assertRaises(Invalid):
            self.execute()

    def test_unknown_usage_is_persistent_and_blocks_next_task(self):
        self.case["responses"]["responses"][0]["usage"] = None
        with BudgetStore(self.path / "budget.sqlite", self.case["research_config"]) as store:
            source = scenario.execute_case(self.case, store, "unknown")
            self.assertEqual(source["research"]["reason"], "unknown_or_invalid_usage")
            self.assertIsNone(source["research"]["accounting"]["input_used_simulated"])
        with BudgetStore(self.path / "budget.sqlite", self.case["research_config"]) as store:
            source = scenario.execute_case(CASE, store, "next")
            self.assertEqual(source["research"]["reason"], "unknown_or_pending_usage")
            self.assertIsNone(source["research"]["accounting"])
            self.assertEqual(source["case"], CASE)

    def test_run_budget_refusal_never_becomes_report(self):
        self.case["research_config"]["budget"]["calls_per_run"] = 2
        source = self.execute()
        self.assertEqual(source["research"]["status"], "BLOCKED")
        self.assertEqual(source["research"]["reason"], "calls_run_budget_exhausted")
        self.assertEqual(source["research"]["accounting"]["calls"], 2)
        with self.assertRaises(Invalid):
            publication.build_report(source)

    def test_store_configuration_and_adapter_substitution_rejected(self):
        with BudgetStore(self.path / "mismatch.sqlite", self.case["research_config"] | {"version": "other"}) as store:
            with self.assertRaises(Invalid):
                scenario.execute_case(self.case, store, "mismatch")
        for substitute in (None, object(), research.UnavailableLiveAdapter()):
            with self.assertRaises(Invalid):
                scenario.execute_case(self.case, substitute, "wrong-store")
        with self.assertRaises(TypeError):
            scenario.execute_case(self.case, None, "adapter", adapter=research.UnavailableLiveAdapter())

    def test_persistent_attempt_ids_above_one_survive_restart(self):
        with BudgetStore(self.path / "budget.sqlite", self.case["research_config"]) as store:
            first = scenario.execute_case(self.case, store, "first")
        with BudgetStore(self.path / "budget.sqlite", self.case["research_config"]) as store:
            second = scenario.execute_case(self.case, store, "second")
            denied = scenario.execute_case(self.case, store, "second")
            self.assertEqual(denied["research"]["reason"], "task_already_recorded")
            self.assertEqual(store.db.execute("SELECT count(*) FROM attempts").fetchone()[0], 6)
        self.assertEqual([a["attempt_id"] for a in first["research"]["accounting"]["attempts"]], [1, 2, 3])
        self.assertEqual([a["attempt_id"] for a in second["research"]["accounting"]["attempts"]], [4, 5, 6])
        self.assertEqual(scenario.verify_execution(second), second)

    def test_two_round_risk_review_and_selected_subset(self):
        rows = self.case["responses"]["responses"]
        rejected = deepcopy(rows[1])
        rejected["body"]["verdicts"][0]["verdict"] = "reject"
        later = [deepcopy(r) for r in rows]
        for row in later:
            row["round"] = 2
        later[2]["body"]["selected_ids"] = later[2]["body"]["selected_ids"][:1]
        self.case["responses"]["responses"] = [rows[0], rejected, *later]
        source = self.execute()
        self.assertEqual(source["research"]["rounds"], 2)
        self.assertEqual(source["research"]["accounting"]["calls"], 5)
        self.assertEqual(len(source["research"]["claims"]), 1)
        self.assertEqual(scenario.verify_execution(source), source)

    def test_wrapper_and_output_tampering_rejected_even_rehashed(self):
        mutations = [lambda s: s.update(extra=True), lambda s: s.update(schema_version=True),
                     lambda s: s.update(version="new"), lambda s: s.update(label="LIVE"),
                     lambda s: s["research"].update(mode="live_observation"),
                     lambda s: s["research"].update(status="PASS"),
                     lambda s: s["research"].update(readiness="READY"),
                     lambda s: s["research"]["claims"][0].update(statement="Synthetic close is 9999."),
                     lambda s: s["research"]["claims"][0].update(evidence_ids=["0" * 64]),
                     lambda s: s["research"]["reviews"][0]["verdicts"][0].update(verdict="reject"),
                     lambda s: s["research"]["attempts"][2].update(response_id="0" * 64),
                     lambda s: s["evaluation"].update(extra="PASS"),
                     lambda s: s["bundle"]["content"]["results"]["flow"].update(value=0),
                     lambda s: s["bundle"]["content"]["domains"]["market"].update(status="BLOCK"),
                     lambda s: s["bundle"]["content"]["live_market_coverage"].update(covered=5)]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                source = deepcopy(SOURCE)
                mutate(source)
                with self.assertRaises(Invalid):
                    publication.build_report(rehash(source))

    def test_changed_case_inputs_and_config_cannot_rebind_old_outputs(self):
        def bar(case):
            fact = next(f for f in case["payload"]["facts"] if f["payload"]["kind"] == "bar")
            fact["payload"]["close"] += 1
            fact["content_hash"] = digest(fact["payload"])
        changes = [bar, lambda c: c["methods"].update(news_half_life_seconds=40000),
                   lambda c: c["entities"].update(version="new-mapping"),
                   lambda c: c["research_config"]["roles"]["editor"].update(prompt="new-editor"),
                   lambda c: c["research_config"]["budget"].update(input_per_call=70000),
                   lambda c: c["responses"]["responses"][0]["body"]["claims"][0].update(statement="Changed authored claim."),
                   lambda c: c["responses"]["responses"][0]["usage"].update(input_tokens=1),
                   lambda c: c["responses"]["responses"][2]["body"].update(selected_ids=["synthetic-close"])]
        for index, change in enumerate(changes):
            with self.subTest(index=index):
                source = deepcopy(SOURCE)
                change(source["case"])
                with self.assertRaises(Invalid):
                    scenario.verify_execution(rehash(source))

    def test_forged_accounting_attempts_totals_and_roles_rejected(self):
        mutations = [lambda a: a.update(calls=2), lambda a: a.update(input_reserved=1),
                     lambda a: a.update(input_used_simulated=1), lambda a: a.update(output_used_simulated=None),
                     lambda a: a.update(status="active"), lambda a: a.update(extra=True),
                     lambda a: a["attempts"][0].update(input_reserved=1),
                     lambda a: a["attempts"][0].update(input_used=True),
                     lambda a: a["attempts"][0].update(attempt_id=0),
                     lambda a: a["attempts"][0].update(attempt_id=True),
                     lambda a: a["attempts"][1].update(attempt_id=5000),
                     lambda a: a["attempts"][1].update(attempt_id=1),
                     lambda a: a["attempts"][0].update(status="unknown"),
                     lambda a: a["attempts"][0].update(role="editor"),
                     lambda a: a["attempts"][0].update(round=2),
                     lambda a: a["attempts"][0].update(run_id="0" * 32),
                     lambda a: a["attempts"].pop()]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                source = deepcopy(SOURCE)
                mutate(source["research"]["accounting"])
                with self.assertRaises(Invalid):
                    scenario.verify_execution(rehash(source))

    def test_forged_execution_chronology_and_duration_rejected(self):
        future = stamp(utc_now() + timedelta(days=1))
        mutations = [lambda s: s.update(generated_at=future),
                     lambda s: s.update(started_at="2000-01-01T00:00:00Z"),
                     lambda s: s["research"].update(run_id="bad"),
                     lambda s: s["research"]["accounting"].update(started_at="2019-01-01T00:00:00Z"),
                     lambda s: s["research"]["accounting"].update(finished_at=future),
                     lambda s: s["research"]["accounting"]["attempts"][0].update(reserved_at=future),
                     lambda s: s["research"]["accounting"]["attempts"][0].update(day="2020-01-01"),
                     lambda s: s["research"]["attempts"][0].update(elapsed_seconds=True),
                     lambda s: s["research"]["attempts"][0].update(elapsed_seconds=-1),
                     lambda s: s["research"]["attempts"][0].update(elapsed_seconds=31),
                     lambda s: s["research"]["attempts"][0].update(elapsed_seconds=4),
                     lambda s: s["research"]["accounting"].update(started_at="2020-01-10T12:00:00+08:00")]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                source = deepcopy(SOURCE)
                mutate(source)
                with self.assertRaises(Invalid):
                    scenario.verify_execution(rehash(source))

    def test_scenario_verify_cache_does_not_trust_mutable_objects(self):
        scenario._replay.cache_clear()
        with patch.object(research, "run_research", wraps=research.run_research) as run:
            source = scenario.verify_execution(SOURCE)
            source["research"]["claims"].clear()
            self.assertEqual(scenario.verify_execution(SOURCE), SOURCE)
            self.assertEqual(run.call_count, 1)
            with self.assertRaises(Invalid):
                scenario.verify_execution(rehash(source))

    def test_explicit_schema_dispatch_cannot_bypass_original_asset_gate(self):
        fixed = research.demo()
        for extra in ({"case": self.case}, {"schema": "unknown", "schema_version": 1},
                      {"schema": scenario.SCHEMA, "schema_version": 1},
                      {"schema": scenario.SCHEMA, "schema_version": True}):
            with self.assertRaises(Invalid):
                publication.verify_source(fixed | extra)
        with patch.dict(publication.ASSETS, {"config/p3_research.toml": "0" * 64}):
            with self.assertRaises(Invalid):
                publication.verify_source(fixed)
            self.assertEqual(publication.verify_source(SOURCE), SOURCE)

    def test_publication_config_binding_and_output_bounds(self):
        with self.assertRaises(Invalid):
            publication.build_report(SOURCE, CASE["publication_config"] | {"chunk_chars": 7000})
        self.case["publication_config"]["chunk_chars"] = 7000
        source = self.execute()
        report = publication.build_report(source)
        self.assertTrue(publication.verify_report(report))
        with self.assertRaises(Invalid):
            publication.verify_report(report, CASE["publication_config"])
        for index, setting in enumerate(({"max_report_bytes": 1024}, {"max_chunks": 3})):
            case = deepcopy(self.case)
            case["publication_config"].update(setting)
            source = self.execute(case, "bound" + str(index))
            with self.assertRaises(Invalid):
                publication.build_report(source)

    def test_scenario_report_rehashed_facts_parts_gaps_and_source_rejected(self):
        report = publication.build_report(SOURCE)
        mutations = [lambda r: r["content"]["sections"][0]["items"][0]["data"].update(titles=["invented"]),
                     lambda r: r["content"].update(gaps=[]), lambda r: r["parts"].reverse(),
                     lambda r: r["chunks"][0].update(text="unchecked"),
                     lambda r: r["content"].update(version=publication.VERSION)]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                value = deepcopy(report)
                mutate(value)
                value["report_id"] = digest(value["content"])
                with self.assertRaises(Invalid):
                    publication.verify_report(value)

    def test_explanatory_corrections_require_enum_not_factual_prose(self):
        report = publication.build_report(SOURCE)
        for reason in publication.SCENARIO_CORRECTION_REASONS:
            correction = publication.build_report(SOURCE, supersedes=report["report_id"], reason=reason)
            self.assertEqual(correction["content"]["sections"], report["content"]["sections"])
            self.assertTrue(publication.verify_report(correction))
        for reason in ("SP500 closed at 9999", "Buy now", "explanation\nnew heading", "x" * 501, None):
            with self.assertRaises(Invalid):
                publication.build_report(SOURCE, supersedes=report["report_id"], reason=reason)
        with self.assertRaises(Invalid):
            publication.build_report(SOURCE, reason=next(iter(publication.SCENARIO_CORRECTION_REASONS)))
        fixed = research.demo()
        with self.assertRaises(Invalid):
            publication.build_report(fixed, supersedes="0" * 64,
                                     reason="Clarification of the offline scenario scope.")

    def test_ledger_same_dataset_current_leaf_and_fake_delivery(self):
        report = publication.build_report(SOURCE)
        reasons = sorted(publication.SCENARIO_CORRECTION_REASONS)
        correction = publication.build_report(SOURCE, supersedes=report["report_id"], reason=reasons[0])
        other = self.execute(different_case())
        changed = publication.build_report(other, supersedes=correction["report_id"], reason=reasons[1])
        fork = publication.build_report(SOURCE, supersedes=report["report_id"], reason=reasons[1])
        with Ledger(self.path / "ledger.sqlite", CASE["publication_config"]) as ledger:
            with self.assertRaises(Invalid):
                ledger.append(correction)
            ledger.append(report)
            ledger.append(correction)
            with self.assertRaises(Invalid):
                ledger.append(changed)
            with self.assertRaises(Invalid):
                ledger.append(fork)
            self.assertEqual(deliver(ledger, correction["report_id"], FakeTransport())["status"], "completed")
        with Ledger(self.path / "ledger.sqlite", CASE["publication_config"]) as ledger:
            self.assertEqual(ledger.snapshot(correction["report_id"])["status"], "completed")
            encoded = ledger.db.execute("SELECT record FROM reports WHERE report_id=?",
                                        (correction["report_id"],)).fetchone()[0]
            self.assertEqual(decode_input(encoded.encode()), correction)


if __name__ == "__main__":
    unittest.main()
