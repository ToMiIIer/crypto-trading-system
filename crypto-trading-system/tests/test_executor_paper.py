from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from src.graph.nodes.executor import PaperExecutorNode
from src.risk.manager import RiskDecision
from src.storage.repository import StorageRepository


class PaperExecutorNodeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmpdir.name)
        self.db_path = self.tmp_path / "paper_executor_test.db"
        self.repo = StorageRepository(f"sqlite:///{self.db_path}")
        self.repo.initialize()
        self.node = PaperExecutorNode(self.repo)

        self.market_data = {
            "ticker_24h": {"last_price": 100.0},
            "indicators": {"atr_14": 1.0},
            "risk_params": {"max_sl_distance_pct": 0.02, "atr_stop_multiplier": 1.5},
        }
        self.portfolio = {"equity": 10000.0}

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    @staticmethod
    def _decision(action: str, approved: bool = True) -> RiskDecision:
        return RiskDecision(
            approved=approved,
            action=action,
            reason="approved" if approved else "not_approved",
            position_pct=0.10 if approved else 0.0,
            stop_loss_pct=0.02 if approved else 0.0,
            take_profit_pct=0.04 if approved else 0.0,
        )

    def test_buy_opens_long_position_and_persists_trade(self) -> None:
        result = self.node.run(
            run_id="run-open",
            pair="BTC/USDC",
            timeframe="4h",
            risk_decision=self._decision("BUY", approved=True),
            market_data=self.market_data,
            portfolio_state=self.portfolio,
        )

        self.assertEqual(result["status"], "SIMULATED_TRADE_OPENED")
        self.assertEqual(result["action"], "BUY")
        self.assertGreater(float(result["quantity"]), 0.0)
        self.assertGreater(float(result["stop_loss_price"]), 0.0)
        self.assertGreater(float(result["take_profit_price"]), float(result["entry_price"]))

        active_positions = self.repo.list_active_positions()
        self.assertEqual(len(active_positions), 1)
        self.assertEqual(active_positions[0]["pair"], "BTC/USDC")

        trades = self.repo.list_trades_for_dashboard()
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["status"], "OPEN")

    def test_sell_without_open_position_is_no_trade(self) -> None:
        result = self.node.run(
            run_id="run-sell-empty",
            pair="BTC/USDC",
            timeframe="4h",
            risk_decision=self._decision("SELL", approved=True),
            market_data=self.market_data,
            portfolio_state=self.portfolio,
        )

        self.assertEqual(result["status"], "NO_TRADE")
        self.assertEqual(result["reason"], "sell_signal_without_open_long")
        self.assertEqual(len(self.repo.list_active_positions()), 0)

    def test_sell_closes_existing_long_position(self) -> None:
        self.node.run(
            run_id="run-open-2",
            pair="BTC/USDC",
            timeframe="4h",
            risk_decision=self._decision("BUY", approved=True),
            market_data=self.market_data,
            portfolio_state=self.portfolio,
        )

        sell_market_data = {
            "ticker_24h": {"last_price": 105.0},
            "indicators": {"atr_14": 1.0},
            "risk_params": {"max_sl_distance_pct": 0.02, "atr_stop_multiplier": 1.5},
        }
        close_result = self.node.run(
            run_id="run-close-2",
            pair="BTC/USDC",
            timeframe="4h",
            risk_decision=self._decision("SELL", approved=True),
            market_data=sell_market_data,
            portfolio_state=self.portfolio,
        )

        self.assertEqual(close_result["status"], "SIMULATED_TRADE_CLOSED")
        self.assertEqual(close_result["action"], "SELL")
        self.assertGreater(float(close_result["pnl_abs"]), 0.0)
        self.assertEqual(len(self.repo.list_active_positions()), 0)

        performance = self.repo.list_performance_snapshots()
        self.assertTrue(performance)
        self.assertTrue(any(int(row["total_trades"]) >= 1 for row in performance))

    def test_take_profit_closes_position_even_when_risk_not_approved(self) -> None:
        self.node.run(
            run_id="run-open-3",
            pair="BTC/USDC",
            timeframe="4h",
            risk_decision=self._decision("BUY", approved=True),
            market_data=self.market_data,
            portfolio_state=self.portfolio,
        )

        hold_decision = self._decision("HOLD", approved=False)
        take_profit_market_data = {
            "ticker_24h": {"last_price": 106.0},
            "indicators": {"atr_14": 1.0},
            "risk_params": {"max_sl_distance_pct": 0.02, "atr_stop_multiplier": 1.5},
        }
        result = self.node.run(
            run_id="run-auto-close",
            pair="BTC/USDC",
            timeframe="4h",
            risk_decision=hold_decision,
            market_data=take_profit_market_data,
            portfolio_state=self.portfolio,
        )

        self.assertEqual(result["status"], "SIMULATED_TRADE_CLOSED")
        self.assertEqual(result["reason"], "take_profit_hit")
        self.assertEqual(len(self.repo.list_active_positions()), 0)


if __name__ == "__main__":
    unittest.main()
