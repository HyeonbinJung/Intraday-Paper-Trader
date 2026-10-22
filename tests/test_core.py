import unittest
from unittest.mock import patch
from datetime import datetime
from zoneinfo import ZoneInfo
from trader import (START_DATE, initial_state, initial_rules, equity, learn_from_loss,
                    execute, session_bounds, effective_session_bounds, apply_2025_risk_defaults, entry_guards,
                    run_once, summarize_entry_checks, build_batch_record)

NY = ZoneInfo("America/New_York")

class CoreTests(unittest.TestCase):
    def test_start_date(self):
        self.assertEqual(START_DATE.isoformat(), '2026-10-19')

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
        self.assertEqual(rules["version"], 2)
        self.assertLess(original, 0.007)
        learn_from_loss(state, rules, tx, now)
        self.assertLess(rules["risk_per_trade"], original)
        self.assertGreater(rules["rvol_min"], 1.30)
        self.assertEqual(rules["version"], 3)
        self.assertIn("hypothesis_not_proven_cause", rules["lessons"][0])

    def test_portfolio_carries_2025_limits(self):
        state, rules = initial_state(), initial_rules()
        self.assertEqual(rules["max_entries_per_day"], 4)
        self.assertEqual(rules["min_holding_minutes"], 10)
        self.assertEqual(rules["cost_coverage_multiple"], 3)
        state["daily_entries"] = 4
        row = {"symbol": "ABC", "price": 100, "atr14": 2,
               "prior_minute_volume": 100_000}
        checks = entry_guards(state, rules, datetime(2026,10,19,10,0,tzinfo=NY),
                              row, True, 100)
        self.assertFalse(checks["daily_entry_limit"])
        self.assertTrue(checks["payoff_covers_costs"])
        self.assertTrue(checks["volume_participation"])

    def test_fee_dominated_loss_tightens_hurdle(self):
        state, rules = initial_state(), initial_rules()
        now = datetime(2026, 10, 19, 11, tzinfo=NY)
        log = []
        execute(state, rules, now, "ABC", "BUY", 100, 100, "TEST", now.isoformat(), log)
        execute(state, rules, now, "ABC", "SELL", 100, 100.10, "TREND_BREAK",
                now.isoformat(), log)
        self.assertEqual(log[-1]["loss_category"], "COST_DRAG")
        self.assertGreater(rules["cost_coverage_multiple"], 3)
        self.assertEqual(rules["lessons"][-1]["loss_category"], "COST_DRAG")
        self.assertGreater(state["daily_trading_cost"], 0)
        self.assertEqual(state["daily_entries"], 1)

    def test_regime_and_cost_gate(self):
        state, rules = initial_state(), initial_rules()
        now = datetime(2026, 10, 19, 11, tzinfo=NY)
        row = {"symbol": "XYZ", "price": 100, "atr14": 0.10,
               "prior_minute_volume": 100_000}
        checks = entry_guards(state, rules, now, row, False, 100)
        self.assertFalse(checks["market_regime"])
        self.assertFalse(checks["payoff_covers_costs"])
        row["atr14"] = 2
        checks = entry_guards(state, rules, now, row, True, 100)
        self.assertTrue(checks["payoff_covers_costs"])
        state["daily_turnover_notional"] = 124_999
        checks = entry_guards(state, rules, now, row, True, 100)
        self.assertFalse(checks["daily_turnover_limit"])

    def test_migrate_old_state_without_loss(self):
        state, rules = initial_state(), initial_rules()
        state.pop("daily_fills")
        rules.pop("min_entry_minutes_before_close")
        apply_2025_risk_defaults(state, rules)
        self.assertEqual(state["daily_fills"], 0)
        self.assertEqual(rules["min_entry_minutes_before_close"], 45)

    def test_october_19_user_cutoff_at_2pm_et(self):
        before = datetime(2026, 10, 19, 13, 30, tzinfo=NY)
        opening, closing = effective_session_bounds(before)
        self.assertEqual((opening.hour, opening.minute), (9, 30))
        self.assertEqual((closing.hour, closing.minute), (14, 0))
        after = datetime(2026, 10, 19, 14, 9, tzinfo=NY)
        self.assertGreaterEqual(after, effective_session_bounds(after)[1])
        future = datetime(2026, 10, 20, 14, 9, tzinfo=NY)
        self.assertEqual(effective_session_bounds(future)[1].hour, 16)

    def test_integrated_paper_buy_then_stop_exit(self):
        """The complete signal->guard->fill path must be reachable with fresh bars."""
        state, rules = initial_state(), initial_rules()
        now = datetime(2026, 10, 22, 10, 0, tzinfo=NY)
        source = [{"symbol": "ABC", "day_change_pct": 5,
                   "discovery_score": 50}]
        def make_row(sym, moment, change, px=100):
            return {"symbol": sym, "price": px, "ma9": 99,
                    "ma20": 98, "vwap": 99 if sym == "SPY" else 95,
                    "rsi14": 60, "rvol20": 2.0, "atr14": 2.0,
                    "atr_pct": 2.0, "day_change_pct": change,
                    "bar_end": moment.replace(second=0, microsecond=0).isoformat(),
                    "prior_minute_volume": 200_000, "bar_age_min": 0.1}
        def fresh(symbol, moment, change):
            return make_row(symbol, moment, change)
        with patch("trader.indicators", side_effect=fresh):
            first, state, rules = run_once(now, state, rules, source)
        self.assertEqual(first["status"], "EXECUTED")
        self.assertEqual(first["transactions"][0]["side"], "BUY")
        self.assertEqual(state["daily_entries"], 1)
        self.assertIn("ABC", state["positions"])
        self.assertLess(equity(state), 100_000)

        def dropped(symbol, moment, change):
            row = make_row(symbol, moment, change, 96 if symbol == "ABC" else 100)
            return row
        with patch("trader.indicators", side_effect=dropped):
            second, state, rules = run_once(
                datetime(2026, 10, 22, 10, 1, tzinfo=NY),
                state, rules, source)
        self.assertEqual(second["status"], "EXECUTED")
        self.assertEqual(second["transactions"][0]["side"], "SELL")
        self.assertEqual(second["transactions"][0]["reason"], "STOP")
        self.assertNotIn("ABC", state["positions"])
        self.assertEqual(state["daily_fills"], 2)

    def test_rejection_summary_is_diagnostic_only(self):
        events = [{"indicators": [
            {"top_five": True, "entry_checks": {"volume": False, "market_regime": False}},
            {"top_five": True, "entry_checks": {"volume": False, "market_regime": True}},
            {"top_five": False, "entry_checks": {}}]}]
        diag = summarize_entry_checks(events)
        self.assertEqual(diag["top_five_evaluations"], 2)
        self.assertEqual(diag["approved_entries"], 0)
        self.assertEqual(diag["failed_check_counts"]["volume"], 2)
        self.assertEqual(diag["failed_check_counts"]["market_regime"], 1)

    def test_holiday(self):
        self.assertIsNone(session_bounds(datetime(2026, 12, 25, 12, tzinfo=NY)))

if __name__ == "__main__":
    unittest.main()
