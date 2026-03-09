from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src import main
from src.storage.repository import StorageRepository
from src.utils.settings import reset_settings_cache


class PaperSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmpdir.name)
        self.db_path = self.tmp_path / "paper_smoke.db"
        self.db_url = f"sqlite:///{self.db_path}"

    def tearDown(self) -> None:
        reset_settings_cache()
        self._tmpdir.cleanup()

    def test_execute_paper_smoke_persists_open_close_and_performance(self) -> None:
        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": self.db_url,
                "TELEGRAM_NOTIFY_TRADES": "false",
                "TELEGRAM_BOT_TOKEN": "",
                "EXECUTION_MODE": "paper",
            },
            clear=False,
        ):
            reset_settings_cache()
            summary = main.execute_paper_smoke(
                pair="BTC/USDC",
                timeframe="4h",
                buy_price=100.0,
                sell_price=101.0,
                size_pct=0.10,
                sl_pct=0.01,
                tp_pct=0.02,
                notify=False,
            )

        self.assertGreaterEqual(int(summary["trade_events_count"]), 2)
        self.assertEqual(int(summary["active_positions_after_close"]), 0)
        self.assertGreater(float(summary["realized_pnl_abs"]), 0.0)

        repo = StorageRepository(self.db_url)
        repo.initialize()
        run_ids = {str(summary["run_id_open"]), str(summary["run_id_close"])}
        smoke_trades = [row for row in repo.list_trades_for_dashboard() if str(row.get("run_id", "")) in run_ids]
        self.assertEqual(len(smoke_trades), 2)
        self.assertEqual({str(row["side"]).upper() for row in smoke_trades}, {"LONG"})

        actions_by_run = {str(row["run_id"]): str(row["status"]).upper() for row in smoke_trades}
        self.assertIn(str(summary["run_id_open"]), actions_by_run)
        self.assertIn(str(summary["run_id_close"]), actions_by_run)
        self.assertEqual(actions_by_run[str(summary["run_id_close"])], "CLOSED")

        active_positions = repo.list_active_positions()
        self.assertEqual(len(active_positions), 0)

        perf_rows = [row for row in repo.list_performance_snapshots() if str(row.get("run_id", "")) == str(summary["run_id_close"])]
        self.assertTrue(perf_rows)
        self.assertGreaterEqual(int(perf_rows[0]["total_trades"]), 1)

    def test_execute_paper_smoke_notify_forces_trade_notifications(self) -> None:
        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": self.db_url,
                "TELEGRAM_NOTIFY_TRADES": "false",
                "TELEGRAM_BOT_TOKEN": "",
                "EXECUTION_MODE": "paper",
            },
            clear=False,
        ):
            reset_settings_cache()
            with patch("src.telegram_notifier.notify_trade_event") as notify_mock:
                summary = main.execute_paper_smoke(
                    pair="BTC/USDC",
                    timeframe="4h",
                    buy_price=100.0,
                    sell_price=101.0,
                    size_pct=0.10,
                    sl_pct=0.01,
                    tp_pct=0.02,
                    notify=True,
                )

        self.assertGreaterEqual(int(summary["trade_events_count"]), 2)
        self.assertEqual(notify_mock.call_count, 2)
        for call in notify_mock.call_args_list:
            self.assertTrue(bool(call.kwargs.get("force")))


if __name__ == "__main__":
    unittest.main()
