from __future__ import annotations

import unittest

from src.risk.manager import RiskManager


class RiskManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manager = RiskManager(
            {
                "max_position_pct": 0.10,
                "daily_loss_limit_pct": 0.03,
                "max_sl_distance_pct": 0.02,
                "min_conviction": 0.60,
                "max_open_positions": 3,
                "max_symbol_exposure_pct": 0.30,
                "min_cash_buffer_pct": 0.20,
                "atr_stop_multiplier": 1.5,
                "risk_per_trade_pct": 0.01,
            }
        )

        self.base_portfolio = {
            "daily_pnl_pct": 0.0,
            "open_positions": 1,
            "symbol_exposure_pct": 0.10,
            "cash_buffer_pct": 0.50,
            "equity": 10000.0,
        }
        self.base_market = {
            "price": 50000.0,
            "atr": 400.0,
        }

    def test_rejects_low_conviction(self) -> None:
        decision = self.manager.evaluate(
            action="BUY",
            weighted_confidence=0.55,
            portfolio_state=self.base_portfolio,
            market_state=self.base_market,
        )

        self.assertFalse(decision.approved)
        self.assertEqual(decision.reason, "below_min_conviction")

    def test_rejects_daily_loss_limit_breach(self) -> None:
        portfolio = dict(self.base_portfolio)
        portfolio["daily_pnl_pct"] = -0.031

        decision = self.manager.evaluate(
            action="BUY",
            weighted_confidence=0.75,
            portfolio_state=portfolio,
            market_state=self.base_market,
        )

        self.assertFalse(decision.approved)
        self.assertEqual(decision.reason, "daily_loss_limit_reached")

    def test_rejects_hold_action(self) -> None:
        decision = self.manager.evaluate(
            action="HOLD",
            weighted_confidence=0.95,
            portfolio_state=self.base_portfolio,
            market_state=self.base_market,
        )

        self.assertFalse(decision.approved)
        self.assertEqual(decision.reason, "consensus_not_actionable")

    def test_approves_valid_setup_and_sizes_position(self) -> None:
        decision = self.manager.evaluate(
            action="BUY",
            weighted_confidence=0.80,
            portfolio_state=self.base_portfolio,
            market_state=self.base_market,
        )

        self.assertTrue(decision.approved)
        self.assertEqual(decision.reason, "approved")
        self.assertGreater(decision.position_pct, 0.0)
        self.assertLessEqual(decision.position_pct, 0.10)
        self.assertGreater(decision.stop_loss_pct, 0.0)
        self.assertLessEqual(decision.stop_loss_pct, 0.02)
        self.assertGreaterEqual(decision.take_profit_pct, decision.stop_loss_pct)


if __name__ == "__main__":
    unittest.main()
