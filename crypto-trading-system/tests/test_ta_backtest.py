from __future__ import annotations

import json
import unittest
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

from src.backtest.ta_backtest import backtest_ta, export_backtest_artifacts, load_candles_csv, slice_candles

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "btcusdt_4h_sample.csv"


def _ta_cfg() -> dict[str, object]:
    return {
        "decision": {"buy_threshold": 0.5, "sell_threshold": -0.5, "hold_band": 0.1},
        "indicators": {
            "ema_ma": {"enabled": True, "ema_fast_period": 2, "ema_slow_period": 3, "ma_period": 4, "min_trend_gap_pct": 0.0},
            "obv": {"enabled": False, "ma_period": 2, "min_delta_ratio": 0.0},
            "rsi": {"enabled": False, "period": 2, "buy_level": 35, "sell_level": 65},
            "macd": {"enabled": False, "fast_period": 2, "slow_period": 3, "signal_period": 2, "histogram_epsilon": 0.0},
            "atr": {"enabled": False, "period": 2, "low_volatility_pct": 0.01, "high_volatility_pct": 0.05},
            "bollinger": {"enabled": False, "period": 2, "std_dev_mult": 2.0, "touch_buffer_pct": 0.01},
            "market_structure": {"enabled": False, "pivot_lookback": 1},
        },
    }


class TABacktestTests(unittest.TestCase):
    def test_backtest_produces_trades_deterministically(self) -> None:
        candles = load_candles_csv(FIXTURE_PATH)

        result = backtest_ta(
            candles=candles,
            ta_config=_ta_cfg(),
            symbol="BTCUSDT",
            interval="4h",
            years=6,
            initial_capital=10000.0,
            size_pct=1.0,
            sl_pct=0.20,
            tp_pct=0.40,
            fee_bps=0.0,
        )

        self.assertEqual(len(result["trades"]), 1)
        trade = result["trades"][0]
        self.assertEqual(trade["entry_price"], 106.0)
        self.assertEqual(trade["exit_price"], 101.0)
        self.assertEqual(trade["exit_reason"], "signal_sell")
        self.assertEqual(result["summary"]["total_trades"], 1)
        self.assertFalse(result["summary"]["open_position"])
        self.assertLess(float(trade["pnl_abs"]), 0.0)

        with TemporaryDirectory() as tmp_dir:
            paths = export_backtest_artifacts(result, Path(tmp_dir))
            for key, path_str in paths.items():
                path = Path(path_str)
                self.assertTrue(path.exists(), key)
                self.assertGreater(path.stat().st_size, 0, key)
            summary = json.loads(Path(paths["summary_json"]).read_text(encoding="utf-8"))
            self.assertEqual(summary["total_trades"], 1)

    def test_sl_tp_priority_is_deterministic(self) -> None:
        candles = load_candles_csv(FIXTURE_PATH)
        modified = deepcopy(candles)
        modified[4]["low"] = 103.0

        stop_first = backtest_ta(
            candles=modified,
            ta_config=_ta_cfg(),
            symbol="BTCUSDT",
            interval="4h",
            years=6,
            initial_capital=10000.0,
            size_pct=1.0,
            sl_pct=0.02,
            tp_pct=0.02,
            fee_bps=0.0,
            sl_tp_priority="stop_first",
        )
        tp_first = backtest_ta(
            candles=modified,
            ta_config=_ta_cfg(),
            symbol="BTCUSDT",
            interval="4h",
            years=6,
            initial_capital=10000.0,
            size_pct=1.0,
            sl_pct=0.02,
            tp_pct=0.02,
            fee_bps=0.0,
            sl_tp_priority="tp_first",
        )

        self.assertEqual(stop_first["trades"][0]["exit_reason"], "stop_loss")
        self.assertEqual(tp_first["trades"][0]["exit_reason"], "take_profit")
        self.assertAlmostEqual(float(stop_first["trades"][0]["exit_price"]), 103.88, places=6)
        self.assertAlmostEqual(float(tp_first["trades"][0]["exit_price"]), 108.12, places=6)

    def test_period_slicing_filters_requested_window(self) -> None:
        candles = load_candles_csv(FIXTURE_PATH)
        sliced = slice_candles(candles, start="2024-01-02", end="2024-01-02", years=6)

        self.assertEqual(len(sliced), 6)
        self.assertEqual(int(sliced[0]["open_time"]), 1704153600000)
        self.assertEqual(int(sliced[-1]["open_time"]), 1704225600000)

    def test_fee_bps_changes_pnl_deterministically(self) -> None:
        candles = load_candles_csv(FIXTURE_PATH)
        no_fee = backtest_ta(
            candles=candles,
            ta_config=_ta_cfg(),
            symbol="BTCUSDT",
            interval="4h",
            years=6,
            initial_capital=10000.0,
            size_pct=1.0,
            sl_pct=0.20,
            tp_pct=0.40,
            fee_bps=0.0,
        )
        with_fee = backtest_ta(
            candles=candles,
            ta_config=_ta_cfg(),
            symbol="BTCUSDT",
            interval="4h",
            years=6,
            initial_capital=10000.0,
            size_pct=1.0,
            sl_pct=0.20,
            tp_pct=0.40,
            fee_bps=4.0,
        )

        self.assertLess(with_fee["summary"]["total_pnl_abs"], no_fee["summary"]["total_pnl_abs"])
        self.assertLess(with_fee["trades"][0]["pnl_abs"], no_fee["trades"][0]["pnl_abs"])

