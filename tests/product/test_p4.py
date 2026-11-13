"""Offline fixed-publication, append-only ledger and durable dispatch tests."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, closing, redirect_stdout
from copy import deepcopy
from datetime import timedelta
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch

from market_intelligence import publication as p4, research
from market_intelligence.delivery import FakeTransport, UnavailableLiveAdapter, deliver
from market_intelligence.ledger import DeliveryBlocked, Ledger
from market_intelligence.models import Invalid, canonical, digest, instant, stamp, utc_now

ROOT = Path(__file__).resolve().parents[2]
NETWORK = None
SOURCE = None


def forbidden(*args, **kwargs):
    raise AssertionError("P4 sockets, DNS and live access forbidden")


def setUpModule():
    global NETWORK, SOURCE
    NETWORK = ExitStack()
    for name in ("socket.socket", "socket.create_connection", "socket.getaddrinfo", "socket.gethostbyname",
                 "socket.gethostbyname_ex", "socket.gethostbyaddr", "socket.getnameinfo"):
        NETWORK.enter_context(patch(name, side_effect=forbidden))
    SOURCE = research.demo()


def tearDownModule():
    NETWORK.close()


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.config = p4.load_config()
        self.source = deepcopy(SOURCE)
        self.report = p4.build_report(self.source)

    def test_three_parts_stable_content_and_bound_chunks(self):
        self.assertEqual(self.report, p4.build_report(self.source))
        self.assertEqual([p["ordinal"] for p in self.report["parts"]], [1, 2, 3])
        self.assertEqual(len(self.report["chunks"]), 5)
        self.assertTrue(p4.verify_report(self.report))
        for part in self.report["parts"]:
            chunks = [c for c in self.report["chunks"] if c["part_id"] == part["part_id"]]
            self.assertEqual("".join(c["body"] for c in chunks), part["body"])
            for chunk in chunks:
                self.assertLessEqual(len(chunk["text"]), self.config["chunk_chars"])
                for value in (self.report["report_id"], part["part_id"], chunk["chunk_id"],
                              "synthetic_test", "2020-01-10T12:00:00Z", "supersedes=", "GAP:"):
                    self.assertIn(value, chunk["text"])

    def test_readable_known_kind_format_and_no_json_dump(self):
        body = p4.report_text(self.report)
        for phrase in ("News: SYNTHETIC", "Classification: sourced_report", "Event: SYN_EVENT",
                       "Planned: 2020-01-11", "Success: unassessed", "causal attribution not established",
                       "Benchmark: KOSPI; value=1000", "Synthetic breadth:", "Turnover:", "Flow: unavailable",
                       "Sector: synthetic_technology", "equal-weight synthetic return=", "Approved claim:",
                       "stance=", "Counterargument:", "Invalidation:", "Source locators", "Evidence: E"):
            self.assertIn(phrase, body)
        self.assertNotIn('{"', body)
        self.assertNotIn('"value":0', body)
        self.assertNotIn(str(ROOT), body)
        self.assertIn("value=unavailable", body)
        self.assertIn("Evidence: unavailable (no source)", body)
        self.assertTrue(all(line == line.rstrip() for line in body.splitlines()))

    def test_lossless_source_claim_and_gap_coverage(self):
        content = self.report["content"]
        self.assertEqual(content["source"], self.source)
        self.assertEqual(content["gaps"], p4.LIMITATIONS)
        references = self.source["bundle"]["content"]["references"]
        body = p4.report_text(self.report)
        for row in references:
            self.assertIn(row["fact_id"], body)
            self.assertIn(row["source_id"], body)
            self.assertIn(row["locator"], body)
        for claim in self.source["research"]["claims"]:
            for key in ("claim_id", "statement", "counterargument", "invalidation"):
                self.assertIn(claim[key], body)

    def test_documented_boundaries_do_not_trigger_blanket_text_rejection(self):
        # Pinned provenance may describe prohibited actions without instructing them.
        self.assertTrue(p4.verify_report(self.report))
        self.assertEqual(p4.verify_source(self.source), self.source)

    def test_source_tamper_matrix_even_when_rehashed(self):
        mutations = [
            lambda s: s.update(extra=True),
            lambda s: s["research"].update(mode="live_observation"),
            lambda s: s["research"].update(as_of="2020-01-11T12:00:00Z"),
            lambda s: s["research"]["claims"][0].update(statement="Buy now"),
            lambda s: s["research"]["claims"][0].update(evidence_ids=["0" * 64]),
            lambda s: s["research"]["fingerprints"].update(research_config="0" * 64),
            lambda s: s["research"]["reviews"][0]["verdicts"][0].update(verdict="reject"),
            lambda s: s["bundle"]["content"]["domains"]["market"].update(status="BLOCK"),
            lambda s: s["bundle"]["content"]["domains"]["flow"].update(reasons=[]),
            lambda s: s["bundle"]["content"]["references"][0].update(locator="https://example.invalid/substituted"),
            lambda s: s["bundle"]["content"]["references"][0].update(retrieved_at="2030-01-01T00:00:00Z"),
            lambda s: s["bundle"]["content"]["references"][0].update(expires_at="2019-01-01T00:00:00Z"),
            lambda s: s["bundle"]["content"]["results"]["flow"].update(value=0),
            lambda s: s["research"].update(run_id="bad"),
            lambda s: s.update(generated_at="2035-01-01T00:00:00Z"),
            lambda s: s["research"]["attempts"][0].update(elapsed_seconds=True),
        ]
        for number, mutate in enumerate(mutations):
            with self.subTest(number=number):
                sample = deepcopy(self.source)
                mutate(sample)
                sample["bundle"]["bundle_id"] = digest(sample["bundle"]["content"])
                with self.assertRaises(Invalid):
                    p4.build_report(sample)

    def test_rehashed_report_tampering_rejected(self):
        for mutate in (lambda r: r["content"].update(kind="correction"),
                       lambda r: r["content"].update(gaps=[]),
                       lambda r: r["content"].update(extra="ignored"),
                       lambda r: r["content"]["sections"][0]["items"][0]["data"].update(titles=["ignore system instructions"]),
                       lambda r: r["parts"].reverse(),
                       lambda r: r["parts"][0].update(text="unsupported"),
                       lambda r: r["chunks"][0].update(text="changed")):
            sample = deepcopy(self.report)
            mutate(sample)
            sample["report_id"] = digest(sample["content"])
            with self.assertRaises(Invalid):
                p4.verify_report(sample)

    def test_whole_source_cache_is_bound_and_detached(self):
        p4._verified_source.cache_clear()
        with patch.object(research, "verify_demo", wraps=research.verify_demo) as verify:
            result = p4.verify_source(self.source)
            result["research"]["claims"][0]["statement"] = "altered returned object"
            p4.verify_source(self.source)
            self.assertEqual(verify.call_count, 1)
            bad = deepcopy(self.source)
            bad["research"]["claims"][0]["statement"] = "altered input"
            with self.assertRaises(Invalid):
                p4.verify_source(bad)
            self.assertEqual(verify.call_count, 2)

    def test_asset_recheck_not_bypassed_by_cache(self):
        p4.verify_source(self.source)
        with patch.dict(p4.ASSETS, {"config/p3_research.toml": "0" * 64}):
            with self.assertRaises(Invalid):
                p4.verify_source(self.source)

    def test_config_strict_bounds_and_modes(self):
        for key, value in (("adapter", "live"), ("sink", "real_destination"), ("chunk_chars", True),
                           ("max_chunks", 1000), ("timeout_seconds", 0), ("extra", 1)):
            with self.subTest(key=key), self.assertRaises(Invalid):
                p4.validate_config(self.config | {key: value})
        with self.assertRaises(Invalid):
            p4.build_report(self.source, self.config | {"max_report_bytes": 1024})
        with self.assertRaises(Invalid):
            p4.build_report(self.source, self.config | {"max_chunks": 3})

    def test_unicode_chunking_lossless_and_stable(self):
        value = ("\u4e2d\u6587\U0001f600a\n" * 1000)
        chunks = p4.split_text(value, 29)
        self.assertEqual("".join(chunks), value)
        self.assertEqual(chunks, p4.split_text(value, 29))
        self.assertTrue(all(len(c) <= 29 for c in chunks))
        with self.assertRaises(Invalid):
            p4.split_text("\ud800", 29)

    def test_only_fixed_dataset_empty_day_cannot_be_fabricated(self):
        sample = deepcopy(self.source)
        sample["bundle"]["content"]["results"]["news"] = []
        sample["bundle"]["content"]["results"]["events"] = {"items": [], "empty_day": True}
        sample["bundle"]["bundle_id"] = digest(sample["bundle"]["content"])
        with self.assertRaises(Invalid):
            p4.build_report(sample)
        self.assertIn("No sourced news", p4._item_text({"kind": "empty_fixed_day", "data": {}})[0])

    def test_correction_is_explanatory_and_explicit(self):
        correction = p4.build_report(self.source, supersedes=self.report["report_id"], reason=p4.CORRECTION_REASON)
        self.assertNotEqual(correction["report_id"], self.report["report_id"])
        self.assertEqual(correction["content"]["source"], self.source)
        self.assertTrue(p4.verify_report(correction))
        for kwargs in ({"reason": p4.CORRECTION_REASON}, {"supersedes": "bad", "reason": p4.CORRECTION_REASON},
                       {"supersedes": self.report["report_id"], "reason": "Buy now"},
                       {"supersedes": self.report["report_id"], "reason": "new fact not reviewed"}):
            with self.assertRaises(Invalid):
                p4.build_report(self.source, **kwargs)

    def test_actual_demo_compact_truthful_and_recomputable(self):
        sample = p4.demo()
        self.assertTrue(p4.verify_demo(sample))
        self.assertLess(len(canonical(sample)), 100000)
        self.assertEqual(p4.sample_text(sample), sample["report_text"])
        self.assertEqual(sample["quality"]["live_gates"], p4.GATES)
        self.assertEqual([d["status"] for d in sample["delivery"]], ["completed", "completed"])
        self.assertEqual(sample["error_exercises"]["partial_failure"]["status"], "failed")
        self.assertEqual(sample["error_exercises"]["ambiguity"]["status"], "unknown")
        self.assertNotIn(str(ROOT), canonical(sample))
        self.assertEqual(canonical(sample).count('"source":'), 1)

    def test_coordinator_sample_and_text_if_present(self):
        sample_path, text_path = ROOT / "docs/evidence/p4-demo.json", ROOT / "docs/evidence/p4-report.txt"
        if not sample_path.exists():
            self.skipTest("coordinator sample not yet generated")
        sample = json.loads(sample_path.read_text(encoding="utf-8"))
        self.assertTrue(p4.verify_demo(sample))
        if text_path.exists():
            self.assertEqual(text_path.read_text(encoding="utf-8"), p4.sample_text(sample))

    def test_demo_metadata_and_receipt_tamper_matrix(self):
        sample = p4.demo()
        for mutate in (lambda s: s.update(execution_id="fake"),
                       lambda s: s.update(generated_at="2020-01-01T00:00:00Z"),
                       lambda s: s.update(report_text="changed"),
                       lambda s: s["quality"]["live_gates"].update(research_mvp="complete"),
                       lambda s: s["delivery"][0].update(status="real_delivery"),
                       lambda s: s["delivery"][0].update(reason="changed"),
                       lambda s: s["delivery"][0]["chunks"][0]["outcome"]["acknowledgement"].update(sink="other"),
                       lambda s: s["delivery"][0]["chunks"][0]["reservation"].update(attempt_id=True),
                       lambda s: s["delivery"][0]["chunks"][0].update(extra=True),
                       lambda s: s["error_exercises"]["ambiguity"]["chunks"][0].update(status="sent")):
            broken = deepcopy(sample)
            mutate(broken)
            with self.assertRaises(Invalid):
                p4.verify_demo(broken)

    def test_cross_report_receipt_chronology_cannot_be_forged(self):
        sample = p4.demo()
        self.assertTrue(p4.verify_demo(sample))
        bad = deepcopy(sample)
        first = bad["delivery"][0]["chunks"][0]["reservation"]["reserved_at"]
        for row in bad["delivery"][1]["chunks"]:
            row["reservation"]["reserved_at"] = first
            row["reservation"]["day"] = first[:10]
            row["outcome"]["finished_at"] = first
            row["outcome"]["acknowledgement"]["reservation_id"] = digest(row["reservation"])
        with self.assertRaises(Invalid):
            p4.verify_demo(bad)

        independent = deepcopy(sample)
        for snapshot in independent["error_exercises"].values():
            for row in snapshot["chunks"]:
                if row["reservation"] is None:
                    continue
                row["reservation"]["reserved_at"] = first
                row["reservation"]["day"] = first[:10]
                row["outcome"]["finished_at"] = first
                if row["outcome"]["acknowledgement"] is not None:
                    row["outcome"]["acknowledgement"]["reservation_id"] = digest(row["reservation"])
        self.assertTrue(p4.verify_demo(independent))

    def test_cli_in_process_and_invalid_command(self):
        stream = io.StringIO()
        with redirect_stdout(stream):
            self.assertEqual(p4.main(["demo"]), 0)
        self.assertTrue(p4.verify_demo(json.loads(stream.getvalue())))
        stream = io.StringIO()
        with redirect_stdout(stream):
            self.assertEqual(p4.main(["live"]), 2)
        self.assertEqual(json.loads(stream.getvalue())["reason"], "invalid_command")

    def test_actual_module_cli_subprocess_network_blocked(self):
        code = ("import socket,ssl,runpy,sys; deny=lambda *a,**k: (_ for _ in ()).throw(AssertionError('offline')); "
                "names=['socket','create_connection','getaddrinfo','gethostbyname','gethostbyname_ex','gethostbyaddr','getnameinfo']; "
                "[(setattr(socket,n,deny)) for n in names]; sys.argv=['publication','demo']; "
                "runpy.run_module('market_intelligence.publication',run_name='__main__')")
        result = subprocess.run([sys.executable, "-B", "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(p4.verify_demo(json.loads(result.stdout)))


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory(prefix="p4-tests-")
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "ledger.sqlite"
        self.config = p4.load_config()
        self.report = p4.build_report(SOURCE)
        self.now = utc_now()

    def store(self, config=None, path=None):
        return Ledger(path or self.path, config or self.config, clock=lambda: self.now)

    def correction(self, target=None, source=None):
        return p4.build_report(source or SOURCE, self.config, supersedes=target or self.report["report_id"],
                               reason=p4.CORRECTION_REASON)

    def test_append_reopen_idempotence_and_immutable_rows(self):
        with self.store() as ledger:
            self.assertEqual(ledger.append(self.report), self.report["report_id"])
            ledger.append(self.report)
            self.assertEqual(ledger.db.execute("SELECT count(*) FROM reports").fetchone()[0], 1)
            with self.assertRaises(sqlite3.IntegrityError):
                ledger.db.execute("UPDATE reports SET created_at='changed'")
            with self.assertRaises(sqlite3.IntegrityError):
                ledger.db.execute("DELETE FROM reports")
        with self.store() as ledger:
            ledger.append(self.report)
            self.assertEqual(ledger.snapshot(self.report["report_id"])["status"], "queued")

    def test_linked_corrections_preserve_original_and_reject_dangling(self):
        with self.store() as ledger:
            with self.assertRaises(Invalid):
                ledger.append(self.correction())
            ledger.append(self.report)
            before = ledger.db.execute("SELECT record FROM reports").fetchone()[0]
            first = self.correction()
            ledger.append(first)
            ledger.append(first)
            ledger.append(self.correction(first["report_id"]))
            self.assertEqual(before, ledger.db.execute("SELECT record FROM reports WHERE sequence=1").fetchone()[0])
            self.assertEqual(ledger.db.execute("SELECT count(*) FROM reports").fetchone()[0], 3)

    def test_forked_correction_rejected_without_altering_original(self):
        other_source = deepcopy(SOURCE)
        other_source["research"]["run_id"] = "a" * 32
        for row in other_source["research"]["accounting"]["attempts"]:
            row["run_id"] = "a" * 32
        with self.store() as ledger:
            ledger.append(self.report)
            ledger.append(self.correction())
            before = ledger.db.execute("SELECT record FROM reports ORDER BY sequence").fetchall()
            with self.assertRaises(Invalid):
                ledger.append(self.correction(source=other_source))
            self.assertEqual(before, ledger.db.execute("SELECT record FROM reports ORDER BY sequence").fetchall())

    def test_cycle_wrong_dataset_and_conflicting_report_rejected(self):
        with self.store() as ledger:
            ledger.append(self.report)
            for mutate in (lambda r: r["content"].update(supersedes=r["report_id"]),
                           lambda r: r["content"].update(dataset_id="0" * 64),
                           lambda r: r["content"].update(as_of="2020-02-01T00:00:00Z"),
                           lambda r: r["content"].update(mode="live_observation"),
                           lambda r: r["parts"][0].update(text="changed")):
                report = self.correction()
                mutate(report)
                with self.assertRaises(Invalid):
                    ledger.append(report)
            self.assertEqual(ledger.db.execute("SELECT count(*) FROM reports").fetchone()[0], 1)

    def test_reserve_before_send_and_ordered_bound_acknowledgements(self):
        with self.store() as ledger:
            ledger.append(self.report)
            parent = self
            class Observing(FakeTransport):
                def send(self, request):
                    with parent.store() as other:
                        snapshot = other.snapshot(parent.report["report_id"])
                        parent.assertEqual(snapshot["chunks"][self.calls]["status"], "unknown")
                        with parent.assertRaises(DeliveryBlocked):
                            other.reserve(parent.report["report_id"])
                    return super().send(request)
            adapter = Observing()
            result = deliver(ledger, self.report["report_id"], adapter)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(adapter.calls, len(self.report["chunks"]))
            self.assertEqual([r["ordinal"] for r in result["chunks"]], list(range(1, adapter.calls + 1)))

    def test_duplicate_delivery_after_restart_makes_zero_calls(self):
        with self.store() as ledger:
            ledger.append(self.report)
            deliver(ledger, self.report["report_id"], FakeTransport())
        with self.store() as ledger:
            fake = FakeTransport()
            result = deliver(ledger, self.report["report_id"], fake)
            self.assertEqual(fake.calls, 0)
            self.assertEqual(result["status"], "completed")

    def test_partial_failure_stops_and_never_retries(self):
        with self.store() as ledger:
            ledger.append(self.report)
            fake = FakeTransport(fail_at=2)
            result = deliver(ledger, self.report["report_id"], fake)
            self.assertEqual([r["status"] for r in result["chunks"]], ["sent", "failed", "queued", "queued", "queued"])
            self.assertEqual(result["status"], "failed")
        with self.store() as ledger:
            fake = FakeTransport()
            result = deliver(ledger, self.report["report_id"], fake)
            self.assertEqual(fake.calls, 0)
            self.assertEqual(result["reason"], "bound_fake_failed")

    def test_malformed_ack_matrix_is_unknown_and_persistent(self):
        mutations = [lambda a: None, lambda a: {}, lambda a: a | {"sink": "other"},
                     lambda a: a | {"received_chars": True}, lambda a: a | {"not_sent": 0},
                     lambda a: a | {"chunk_id": "0" * 64}, lambda a: a | {"reservation_id": "0" * 64},
                     lambda a: a | {"extra": True}, lambda a: a | {"status": "failed", "not_sent": False},
                     lambda a: a | {"label": "REAL DELIVERY"}]
        for number, mutate in enumerate(mutations):
            path = self.path.with_name("bad" + str(number) + ".sqlite")
            with self.subTest(number=number), self.store(path=path) as ledger:
                ledger.append(self.report)
                class Malformed(FakeTransport):
                    def send(self, request):
                        return mutate(super().send(request))
                fake = Malformed()
                result = deliver(ledger, self.report["report_id"], fake)
                self.assertEqual(fake.calls, 1)
                self.assertEqual(result["status"], "unknown")
                self.assertEqual(result["reason"], "invalid_ack")
            with self.store(path=path) as ledger:
                fake = FakeTransport()
                self.assertEqual(deliver(ledger, self.report["report_id"], fake)["status"], "unknown")
                self.assertEqual(fake.calls, 0)

    def test_exception_is_sanitized_unknown(self):
        class Broken(FakeTransport):
            def send(self, request):
                raise RuntimeError("private path or provider detail must never be echoed")
        with self.store() as ledger:
            ledger.append(self.report)
            result = deliver(ledger, self.report["report_id"], Broken())
            self.assertEqual(result["reason"], "adapter_exception")
            self.assertNotIn("private path", canonical(result))

    def test_timeout_unknown_even_with_valid_ack(self):
        with self.store() as ledger:
            ledger.append(self.report)
            with patch("market_intelligence.delivery.time.monotonic", side_effect=[0, 6]):
                result = deliver(ledger, self.report["report_id"], FakeTransport())
            self.assertEqual(result["status"], "unknown")
            self.assertEqual(result["reason"], "timeout")

    def test_interrupted_pending_blocks_other_report_handle_and_day(self):
        correction = self.correction()
        with self.store() as ledger:
            ledger.append(self.report)
            ledger.append(correction)
            ledger.reserve(self.report["report_id"])
        self.now += timedelta(days=1)
        with patch("market_intelligence.ledger.utc_now", return_value=self.now), self.store() as ledger:
            fake = FakeTransport()
            result = deliver(ledger, correction["report_id"], fake)
            self.assertEqual(fake.calls, 0)
            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["reason"], "store_has_unknown_pending_or_failed_send")
            self.assertEqual(ledger.snapshot(self.report["report_id"])["status"], "unknown")

    def test_unknown_ack_blocks_other_report_and_day(self):
        correction = self.correction()
        with self.store() as ledger:
            ledger.append(self.report)
            ledger.append(correction)
            deliver(ledger, self.report["report_id"], FakeTransport(malformed_at=1))
        self.now += timedelta(days=1)
        with patch("market_intelligence.ledger.utc_now", return_value=self.now), self.store() as ledger:
            fake = FakeTransport()
            self.assertEqual(deliver(ledger, correction["report_id"], fake)["status"], "blocked")
            self.assertEqual(fake.calls, 0)

    def test_foreign_and_repeated_settlement_rejected(self):
        with self.store() as first, self.store() as second:
            first.append(self.report)
            request = first.reserve(self.report["report_id"])
            ack = FakeTransport().send(request)
            with self.assertRaises(Invalid):
                second.settle(request, ack)
            self.assertEqual(first.settle(request, ack), "sent")
            with self.assertRaises(Invalid):
                first.settle(request, ack)

    def test_transport_mutation_does_not_change_reservation(self):
        class Mutating(FakeTransport):
            def send(self, request):
                request["reservation"]["chars"] = 1
                request["chunk"]["text"] = "changed"
                return super().send(request)
        with self.store() as ledger:
            ledger.append(self.report)
            result = deliver(ledger, self.report["report_id"], Mutating())
            self.assertEqual(result["status"], "unknown")
            self.assertEqual(result["chunks"][0]["chars"], len(self.report["chunks"][0]["text"]))

    def test_disabled_live_adapter_and_wrong_modes_zero_calls(self):
        with self.store() as ledger:
            ledger.append(self.report)
            for mode in ("unavailable_live", "live_observation", None):
                fake = FakeTransport()
                fake.mode = mode
                with self.assertRaises(Invalid):
                    deliver(ledger, self.report["report_id"], fake)
                self.assertEqual(fake.calls, 0)
            with patch.object(UnavailableLiveAdapter, "send", side_effect=forbidden) as send:
                with self.assertRaises(Invalid):
                    deliver(ledger, self.report["report_id"], UnavailableLiveAdapter())
                send.assert_not_called()
            self.assertEqual(ledger.db.execute("SELECT count(*) FROM reservations").fetchone()[0], 0)
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(Invalid):
            UnavailableLiveAdapter().send({})

    def test_concurrent_handles_only_one_reserves(self):
        with self.store() as ledger:
            ledger.append(self.report)
        barrier = threading.Barrier(2)
        def work():
            with self.store() as ledger:
                barrier.wait(timeout=5)
                try:
                    return ledger.reserve(self.report["report_id"]) is not None
                except DeliveryBlocked:
                    return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(work) for _ in range(2)]
            self.assertEqual(sorted(f.result(timeout=15) for f in futures), [False, True])

    def test_run_attempt_and_volume_limits_persist(self):
        for key, value in (("attempts_per_run", 1), ("chars_per_run", len(self.report["chunks"][0]["text"]))):
            config = self.config | {key: value}
            report = p4.build_report(SOURCE, config)
            path = self.path.with_name(key + ".sqlite")
            # Configuration fingerprints change text identities, not text lengths.
            with self.store(config, path) as ledger:
                ledger.append(report)
                result = deliver(ledger, report["report_id"], FakeTransport())
                self.assertEqual(result["status"], "blocked")
                self.assertEqual(result["reason"], "persistent_run_budget")
            with self.store(config, path) as ledger:
                fake = FakeTransport()
                self.assertEqual(deliver(ledger, report["report_id"], fake)["reason"], "persistent_run_budget")
                self.assertEqual(fake.calls, 0)

    def test_day_attempt_and_volume_limits_across_reports(self):
        for key, value in (("attempts_per_day", 5), ("chars_per_day", sum(len(c["text"]) for c in self.report["chunks"]))):
            config = self.config | {key: value}
            report = p4.build_report(SOURCE, config)
            correction = p4.build_report(SOURCE, config, supersedes=report["report_id"], reason=p4.CORRECTION_REASON)
            path = self.path.with_name(key + ".sqlite")
            with self.store(config, path) as ledger:
                ledger.append(report)
                ledger.append(correction)
                self.assertEqual(deliver(ledger, report["report_id"], FakeTransport())["status"], "completed")
            with self.store(config, path) as ledger:
                fake = FakeTransport()
                result = deliver(ledger, correction["report_id"], fake)
                self.assertEqual(fake.calls, 0)
                self.assertEqual(result["reason"], "persistent_day_budget")

    def test_rate_refusal_recorded_and_later_restart_continues_order(self):
        config = self.config | {"rate_calls": 1}
        report = p4.build_report(SOURCE, config)
        with self.store(config) as ledger:
            ledger.append(report)
            result = deliver(ledger, report["report_id"], FakeTransport())
            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["reason"], "persistent_rate_limit")
        self.now += timedelta(seconds=60)
        with patch("market_intelligence.ledger.utc_now", return_value=self.now), self.store(config) as ledger:
            fake = FakeTransport()
            result = deliver(ledger, report["report_id"], fake)
            self.assertEqual(fake.calls, 1)
            self.assertEqual([r["status"] for r in result["chunks"]][:3], ["sent", "sent", "queued"])

    def test_clock_rollback_and_future_clock_zero_calls(self):
        with self.store() as ledger:
            ledger.append(self.report)
            self.now -= timedelta(seconds=1)
            fake = FakeTransport()
            result = deliver(ledger, self.report["report_id"], fake)
            self.assertEqual(result["reason"], "clock_rollback")
            self.assertEqual(fake.calls, 0)
            self.now = utc_now() + timedelta(days=2)
            result = deliver(ledger, self.report["report_id"], fake)
            self.assertEqual(result["reason"], "clock_out_of_bounds")

    def test_report_attempt_and_storage_bounds(self):
        config = self.config | {"max_reports": 1, "max_attempts": 1}
        report = p4.build_report(SOURCE, config)
        with self.store(config) as ledger:
            ledger.append(report)
            correction = p4.build_report(SOURCE, config, supersedes=report["report_id"], reason=p4.CORRECTION_REASON)
            with self.assertRaises(DeliveryBlocked):
                ledger.append(correction)
            result = deliver(ledger, report["report_id"], FakeTransport())
            self.assertEqual(result["reason"], "attempt_storage_limit")
        tiny = self.config | {"max_store_bytes": 131072}
        with self.store(tiny, self.path.with_name("tiny.sqlite")) as ledger:
            with self.assertRaises(DeliveryBlocked):
                ledger.append(p4.build_report(SOURCE, tiny))

    def test_refusal_does_not_create_fictional_attempt(self):
        config = self.config | {"chars_per_run": 1}
        report = p4.build_report(SOURCE, config)
        with self.store(config) as ledger:
            ledger.append(report)
            result = deliver(ledger, report["report_id"], FakeTransport())
            self.assertEqual(result["reason"], "persistent_run_budget")
            self.assertTrue(all(r["status"] == "queued" for r in result["chunks"]))
            self.assertEqual(ledger.db.execute("SELECT count(*) FROM reservations").fetchone()[0], 0)

    def test_unrelated_and_empty_database_unchanged(self):
        for name, body in (("empty.sqlite", b""), ("garbage.sqlite", b"not a sqlite database")):
            path = self.path.with_name(name)
            path.write_bytes(body)
            with self.assertRaises(Invalid):
                self.store(path=path)
            self.assertEqual(path.read_bytes(), body)
        with closing(sqlite3.connect(self.path, isolation_level=None)) as db:
            db.execute("CREATE TABLE unrelated (value TEXT)")
        before = self.path.read_bytes()
        with self.assertRaises(Invalid):
            self.store()
        self.assertEqual(self.path.read_bytes(), before)

    def test_p1_and_p3_database_rejected_without_mutation(self):
        from market_intelligence.store import Store
        from market_intelligence.cost_guard import BudgetStore
        config, _ = research.load_assets()
        for number, factory in enumerate((Store, lambda path: BudgetStore(path, config))):
            path = self.path.with_name("previous" + str(number) + ".sqlite")
            with factory(path):
                pass
            before = path.read_bytes()
            with self.assertRaises(Invalid):
                self.store(path=path)
            self.assertEqual(path.read_bytes(), before)

    def test_changed_config_and_schema_rejected_before_mutation(self):
        with self.store() as ledger:
            ledger.append(self.report)
        before = self.path.read_bytes()
        with self.assertRaises(Invalid):
            self.store(self.config | {"rate_calls": 1})
        self.assertEqual(self.path.read_bytes(), before)
        with closing(sqlite3.connect(self.path, isolation_level=None)) as db:
            db.execute("CREATE TABLE extra (x TEXT)")
        before = self.path.read_bytes()
        with self.assertRaises(Invalid):
            self.store()
        self.assertEqual(self.path.read_bytes(), before)

    def test_malformed_canonical_record_rejected_before_mutation(self):
        with self.store() as ledger:
            ledger.append(self.report)
        with closing(sqlite3.connect(self.path, isolation_level=None)) as db:
            db.execute("INSERT INTO reservations VALUES (1,?,?)",
                       (self.report["chunks"][0]["chunk_id"], '{"attempt_id":true}'))
        before = self.path.read_bytes()
        with self.assertRaises(Invalid):
            self.store()
        self.assertEqual(self.path.read_bytes(), before)

    def test_malformed_ack_existing_database_rejected(self):
        with self.store() as ledger:
            ledger.append(self.report)
            request = ledger.reserve(self.report["report_id"])
        value = {"attempt_id": 1, "status": "sent", "reason": "bound_fake_sent",
                 "finished_at": request["reservation"]["reserved_at"], "acknowledgement": {}}
        with closing(sqlite3.connect(self.path, isolation_level=None)) as db:
            db.execute("INSERT INTO outcomes VALUES (1,?)", (canonical(value),))
        before = self.path.read_bytes()
        with self.assertRaises(Invalid):
            self.store()
        self.assertEqual(self.path.read_bytes(), before)

    def test_external_paths_and_checkout_guards(self):
        for path in ("relative.sqlite", ROOT / "forbidden.sqlite", Path(self.directory.name)):
            with self.assertRaises(Invalid):
                self.store(path=path)
        checkout = Path(self.directory.name) / "other-checkout"
        checkout.mkdir()
        (checkout / ".git").mkdir()
        with self.assertRaises(Invalid):
            self.store(path=checkout / "store.sqlite")

    def test_hardlink_database_and_sidecar_guards(self):
        with self.store():
            pass
        linked = self.path.with_name("linked.sqlite")
        os.link(self.path, linked)
        with self.assertRaises(Invalid):
            self.store(path=linked)
        target = self.path.with_name("target")
        target.write_bytes(b"x")
        other = self.path.with_name("other.sqlite")
        os.link(target, Path(str(other) + "-journal"))
        with self.assertRaises(Invalid):
            self.store(path=other)

    def test_symbolic_link_guard_without_platform_privilege(self):
        with patch.object(Path, "is_symlink", return_value=True), self.assertRaises(Invalid):
            self.store()

    def test_junction_guard(self):
        with patch.object(Path, "is_junction", return_value=True), self.assertRaises(Invalid):
            self.store()

    def test_exclusive_creation_does_not_overwrite_racing_file(self):
        original = os.open
        def race(path, flags, mode):
            Path(path).write_bytes(b"racing creator")
            return original(path, flags, mode)
        with patch("market_intelligence.ledger.os.open", side_effect=race), self.assertRaises(Invalid):
            self.store()
        self.assertEqual(self.path.read_bytes(), b"racing creator")


if __name__ == "__main__":
    unittest.main()
