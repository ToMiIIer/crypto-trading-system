from __future__ import annotations

import copy
import unittest

from src.ta.deterministic_ta import combine_vote_score, compute_indicators, compute_signals
from src.utils.config_loader import ConfigLoader


def _trend_fixture() -> list[dict[str, float]]:
    rows: list[dict[str, float]] = []
    price = 100.0
    for idx in range(240):
        price += 0.5
        rows.append(
            {
                "open_time": float(idx),
                "open": price - 0.25,
                "high": price + 0.75,
                "low": price - 0.75,
                "close": price,
                "volume": 1000.0 + (idx * 5.0),
                "close_time": float(idx) + 0.5,
            }
        )
    return rows


def _structure_fixture(*, bullish: bool) -> list[dict[str, float]]:
    closes = [10, 11, 12, 11, 13, 12, 14, 13, 15, 14, 16, 15, 17]
    if not bullish:
        closes = list(reversed(closes))

    rows: list[dict[str, float]] = []
    for idx, close in enumerate(closes):
        rows.append(
            {
                "open_time": float(idx),
                "open": float(close) - 0.25,
                "high": float(close) + 0.5,
                "low": float(close) - 0.5,
                "close": float(close),
                "volume": 1000.0,
                "close_time": float(idx) + 0.5,
            }
        )
    return rows


class DeterministicTATests(unittest.TestCase):
    def setUp(self) -> None:
        self.loader = ConfigLoader("config")
        self.cfg = self.loader.load_yaml("ta/deterministic_ta.yaml")

    def test_load_artifact_and_compute_indicators(self) -> None:
        indicators = compute_indicators(_trend_fixture(), self.cfg)

        self.assertIn("ema_ma", indicators)
        self.assertIn("obv", indicators)
        self.assertIn("market_structure", indicators)
        self.assertIsNotNone(indicators["ema_ma"]["ema_fast"])
        self.assertIsNotNone(indicators["rsi"]["rsi"])
        self.assertIsNotNone(indicators["macd"]["histogram"])

    def test_signals_are_deterministic(self) -> None:
        candles = _trend_fixture()
        indicators_a = compute_indicators(candles, self.cfg)
        indicators_b = compute_indicators(candles, self.cfg)
        signals_a = compute_signals(indicators_a, candles, self.cfg)
        signals_b = compute_signals(indicators_b, candles, self.cfg)

        self.assertEqual(signals_a, signals_b)

    def test_disabling_rsi_changes_vote_score(self) -> None:
        candles = _trend_fixture()
        indicators = compute_indicators(candles, self.cfg)
        signals = compute_signals(indicators, candles, self.cfg)
        score_a, _confidence_a, _breakdown_a = combine_vote_score(signals, self.cfg)

        cfg_without_rsi = copy.deepcopy(self.cfg)
        cfg_without_rsi["indicators"]["rsi"]["enabled"] = False
        signals_without_rsi = compute_signals(indicators, candles, cfg_without_rsi)
        score_b, _confidence_b, _breakdown_b = combine_vote_score(signals_without_rsi, cfg_without_rsi)

        self.assertNotEqual(score_a, score_b)

    def test_market_structure_signal_matches_trend_state(self) -> None:
        bullish_cfg = copy.deepcopy(self.cfg)
        bullish_cfg["indicators"]["market_structure"]["pivot_lookback"] = 1
        bearish_cfg = copy.deepcopy(self.cfg)
        bearish_cfg["indicators"]["market_structure"]["pivot_lookback"] = 1

        bullish_candles = _structure_fixture(bullish=True)
        bearish_candles = _structure_fixture(bullish=False)

        bullish_signal = compute_signals(
            compute_indicators(bullish_candles, bullish_cfg),
            bullish_candles,
            bullish_cfg,
        )["market_structure"]["signal"]
        bearish_signal = compute_signals(
            compute_indicators(bearish_candles, bearish_cfg),
            bearish_candles,
            bearish_cfg,
        )["market_structure"]["signal"]

        self.assertEqual(bullish_signal, 1)
        self.assertEqual(bearish_signal, -1)


if __name__ == "__main__":
    unittest.main()
