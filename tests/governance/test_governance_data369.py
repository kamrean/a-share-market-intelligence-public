from pathlib import Path
import unittest

from tools import governance_data369 as gate


ROOT = Path(__file__).resolve().parents[2]


class P4KeyGovernanceTests(unittest.TestCase):
    def setUp(self):
        self.planning = {name: (ROOT / name).read_text(encoding="utf-8")
                         for name in (gate.CONTRACT, gate.ENTRY)}

    def test_frozen_planning_has_closed_runtime_authority(self):
        plan, entry = gate._planning(self.planning)
        self.assertFalse(entry["runtime_authority"])
        self.assertEqual(plan["real_requests_authorized"], 0)
        self.assertEqual(plan["market_symbols"], ["000001.SH", "399001.SZ", "399006.SZ"])

    def test_contract_edits_after_entry_and_runtime_claim_both_block(self):
        for path, original, replacement in [
            (gate.CONTRACT, "real_requests_authorized = 0", "real_requests_authorized = 3"),
            (gate.ENTRY, '"runtime_authority": false', '"runtime_authority": true'),
        ]:
            with self.subTest(path=path), self.assertRaises(gate.g.Block):
                gate._planning({**self.planning,
                                path: self.planning[path].replace(original, replacement)})

    def test_only_three_offline_ci_commands_change(self):
        base = gate.g.git(ROOT, "show", f"{gate.BASE}:{gate.WORKFLOW}")
        current = (ROOT / gate.WORKFLOW).read_text(encoding="utf-8")
        gate._workflow(base, current)
        with self.assertRaises(gate.g.Block):
            gate._workflow(base, current.replace("contents: read", "contents: write"))
