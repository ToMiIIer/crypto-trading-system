from __future__ import annotations

import unittest

from src.graph.nodes.agent_runner import AgentRunnerNode


def _ta_config() -> dict[str, object]:
    return {
        "decision": {"buy_threshold": 0.34, "sell_threshold": -0.34, "hold_band": 0.15},
        "indicators": {
            "ema_ma": {"enabled": True, "ema_fast_period": 3, "ema_slow_period": 5, "ma_period": 7, "min_trend_gap_pct": 0.0},
            "obv": {"enabled": True, "ma_period": 3, "min_delta_ratio": 0.0},
            "rsi": {"enabled": True, "period": 3, "buy_level": 35, "sell_level": 65},
            "macd": {"enabled": True, "fast_period": 3, "slow_period": 6, "signal_period": 3, "histogram_epsilon": 0.0},
            "atr": {"enabled": True, "period": 3, "low_volatility_pct": 0.01, "high_volatility_pct": 0.05},
            "bollinger": {"enabled": True, "period": 5, "std_dev_mult": 2.0, "touch_buffer_pct": 0.01},
            "market_structure": {"enabled": True, "pivot_lookback": 2},
        },
    }


def _ohlcv_rows() -> list[dict[str, float]]:
    rows: list[dict[str, float]] = []
    price = 100.0
    for idx in range(20):
        price += 1.0
        rows.append(
            {
                "open_time": float(idx),
                "open": price - 0.5,
                "high": price + 1.0,
                "low": price - 1.0,
                "close": price,
                "volume": 1000.0 + (idx * 25.0),
                "close_time": float(idx) + 0.5,
            }
        )
    return rows


class _FailingLLMClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def complete(self, *, agent_id: str, model_cfg: object, prompt: str, context: dict[str, object]) -> dict[str, object]:
        self.calls.append(agent_id)
        raise RuntimeError("llm_should_not_be_called")


class _RecordingLLMClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def complete(self, *, agent_id: str, model_cfg: object, prompt: str, context: dict[str, object]) -> dict[str, object]:
        self.calls.append(agent_id)
        return {
            "action": "BUY",
            "confidence": 0.72,
            "reasoning": "llm path",
            "risk_notes": "llm notes",
            "provider_used": "openai",
            "error_code": None,
            "error_message": None,
        }


class AgentRunnerRoutingTests(unittest.TestCase):
    def test_non_llm_agents_use_deterministic_logic_without_llm_calls(self) -> None:
        llm_client = _FailingLLMClient()
        node = AgentRunnerNode(
            agent_configs=[
                {
                    "agent_id": "technical_analyst",
                    "enabled": True,
                    "uses_llm": False,
                    "model": {"provider": "openai", "model_name": "gpt-4o-mini", "temp": 0.1, "max_tokens": 256},
                    "system_prompt": "tech",
                    "required_data": ["ticker_24h", "indicators"],
                    "indicators": ["ema_21", "ema_50", "rsi_14"],
                    "output_schema": {},
                },
                {
                    "agent_id": "sentiment_analyst",
                    "enabled": True,
                    "uses_llm": False,
                    "model": {"provider": "openai", "model_name": "gpt-4o-mini", "temp": 0.1, "max_tokens": 256},
                    "system_prompt": "sentiment",
                    "required_data": ["sentiment", "ticker_24h"],
                    "indicators": [],
                    "output_schema": {},
                },
            ],
            llm_client=llm_client,  # type: ignore[arg-type]
        )

        with self.assertLogs("agent_runner", level="INFO") as captured:
            results = node.run(
                run_id="run-routing-1",
                pair="BTC/USDC",
                timeframe="4h",
                market_context={
                    "ohlcv": _ohlcv_rows(),
                    "ticker_24h": {"last_price": 100.0},
                    "indicators": {"ema_21": 101.0, "ema_50": 100.0, "rsi_14": 55.0},
                    "ta_config": _ta_config(),
                    "sentiment": {"score": 0.1},
                },
            )

        self.assertEqual(llm_client.calls, [])
        self.assertEqual(len(results), 2)
        providers = {result.agent_id: result.provider_used for result in results}
        self.assertEqual(providers["technical_analyst"], "deterministic_ta")
        self.assertEqual(providers["sentiment_analyst"], "deterministic")
        self.assertTrue(any("llm_calls_executed=0" in line for line in captured.output))

    def test_only_llm_analyst_calls_llm_client_once(self) -> None:
        llm_client = _RecordingLLMClient()
        node = AgentRunnerNode(
            agent_configs=[
                {
                    "agent_id": "technical_analyst",
                    "enabled": True,
                    "uses_llm": False,
                    "model": {"provider": "mock", "model_name": "mock-tech", "temp": 0.1, "max_tokens": 256},
                    "system_prompt": "tech",
                    "required_data": ["ticker_24h", "indicators"],
                    "indicators": ["ema_21", "ema_50", "rsi_14"],
                    "output_schema": {},
                },
                {
                    "agent_id": "sentiment_analyst",
                    "enabled": True,
                    "uses_llm": False,
                    "model": {"provider": "mock", "model_name": "mock-sent", "temp": 0.1, "max_tokens": 256},
                    "system_prompt": "sent",
                    "required_data": ["sentiment", "ticker_24h"],
                    "indicators": [],
                    "output_schema": {},
                },
                {
                    "agent_id": "llm_analyst",
                    "enabled": True,
                    "uses_llm": True,
                    "model": {"provider": "openai", "model_name": "gpt-4o-mini", "temp": 0.1, "max_tokens": 256},
                    "system_prompt": "llm",
                    "required_data": ["sentiment", "ticker_24h", "indicators"],
                    "indicators": [],
                    "output_schema": {},
                },
            ],
            llm_client=llm_client,  # type: ignore[arg-type]
        )

        with self.assertLogs("agent_runner", level="INFO") as captured:
            results = node.run(
                run_id="run-routing-2",
                pair="BTC/USDC",
                timeframe="4h",
                market_context={
                    "ohlcv": _ohlcv_rows(),
                    "ticker_24h": {"last_price": 100.0},
                    "indicators": {"ema_21": 101.0, "ema_50": 100.0, "rsi_14": 55.0},
                    "ta_config": _ta_config(),
                    "sentiment": {"score": 0.1},
                },
            )

        self.assertEqual(llm_client.calls, ["llm_analyst"])
        providers = {result.agent_id: result.provider_used for result in results}
        self.assertEqual(providers["llm_analyst"], "openai")
        self.assertEqual(providers["technical_analyst"], "deterministic_ta")
        self.assertEqual(providers["sentiment_analyst"], "deterministic")
        self.assertTrue(any("llm_calls_executed=1" in line for line in captured.output))


if __name__ == "__main__":
    unittest.main()
