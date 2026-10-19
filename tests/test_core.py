import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from trader import initial_state, initial_rules, equity, learn_from_loss, execute, session_bounds

NY = ZoneInfo("America/New_York")

class CoreTests(unittest.TestCase):
    def test_seed(self):
        s = initial_state()
        self.assertEqual(equity(s), 100_000)
        self.assertEqual(s["positions"], {})

    def test_buy_sell_net_fees_and_slippage(self):
        state = initial_state()
        rules = initial_rules()
        now = datetime(2026, 10, 20, 10, 10, tzinfo=NY)
        log = []
        execute(state, rules, now, "TEST", "BUY", 10, 100,
                "TEST_ENTRY", now.isoformat(), log)
        self.assertEqual(state["positions"]["TEST"]["qty"], 10)
        self.assertLess(state["cash"], 99_000)
        execute(state, rules, now, "TEST", "SELL", 10, 100,
                "STOP", now.isoformat(), log)
        self.assertNotIn("TEST", state["positions"])
        self.assertLess(state["cash"], 100_000)
        self.assertEqual(len(log), 2)
        self.assertLess(log[-1]["realized_pnl"], 0)
        self.assertEqual(len(rules["lessons"]), 1)

    def test_adaptive_rule_after_two_losses(self):
        state, rules = initial_state(), initial_rules()
        now = datetime(2026, 10, 20, 11, 0, tzinfo=NY)
        tx = {"symbol": "ABC", "reason": "STOP", "realized_pnl": -200}
        learn_from_loss(state, rules, tx, now)
        original = rules["risk_per_trade"]
        learn_from_loss(state, rules, tx, now)
        self.assertLess(rules["risk_per_trade"], original)
        self.assertGreater(rules["rvol_min"], 1.30)
        self.assertEqual(rules["version"], 2)
        self.assertIn("hypothesis_not_proven_cause", rules["lessons"][0])

    def test_holiday(self):
        self.assertIsNone(session_bounds(datetime(2026, 12, 25, 12, tzinfo=NY)))

if __name__ == "__main__":
    unittest.main()
