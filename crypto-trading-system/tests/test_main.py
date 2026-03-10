from __future__ import annotations

import io
import json
from pathlib import Path
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from src import main


class SchedulerConfigPathTests(unittest.TestCase):
    def test_scheduler_uses_project_root_path_and_logs_it(self) -> None:
        observed: dict[str, object] = {}

        class FakeConfigLoader:
            def __init__(self, config_dir: str | Path) -> None:
                observed["config_dir"] = Path(config_dir)

            def load_yaml(self, relative_path: str) -> dict[str, object]:
                observed["relative_path"] = relative_path
                return {
                    "default_job": {
                        "pair": "BTC/USDC",
                        "timeframe": "4h",
                        "cron": {"minute": "0", "hour": "*/4"},
                    }
                }

        class FakeScheduler:
            def add_job(self, *_args: object, **kwargs: object) -> None:
                observed["job_id"] = kwargs.get("id")

            def start(self) -> None:
                observed["started"] = True

            def shutdown(self, wait: bool = False) -> None:
                observed["shutdown_wait"] = wait

        expected_path = (main.PROJECT_ROOT / "config" / "scheduling.yaml").resolve()

        with (
            patch("src.main.ConfigLoader", FakeConfigLoader),
            patch("apscheduler.schedulers.background.BackgroundScheduler", FakeScheduler),
            patch("src.main.time.sleep", side_effect=KeyboardInterrupt),
        ):
            with self.assertLogs("main", level="INFO") as captured:
                main.run_scheduler()

        self.assertEqual(observed["config_dir"], expected_path.parent)
        self.assertEqual(observed["relative_path"], "scheduling.yaml")
        self.assertEqual(observed["job_id"], "phase1_main")
        self.assertTrue(bool(observed["started"]))
        self.assertFalse(bool(observed["shutdown_wait"]))
        self.assertTrue(
            any(f"Loaded scheduling config from: {expected_path}" in line for line in captured.output)
        )

    def test_run_once_logs_summary_and_routes_full_payload_to_file_logger(self) -> None:
        observed: dict[str, object] = {}

        class FakeResult:
            def as_dict(self) -> dict[str, object]:
                return {
                    "run_id": "r1",
                    "pair": "BTC/USDC",
                    "timeframe": "4h",
                    "status": "NO_TRADE",
                    "market_data": {"big": "payload"},
                    "consensus": {"action": "HOLD"},
                    "risk_decision": {"reason": "consensus_not_actionable"},
                    "execution": {"reason": "consensus_not_actionable"},
                }

        class FakePipeline:
            def __init__(self, config_dir: str) -> None:
                observed["config_dir"] = config_dir

            def run_once(self, pair: str, timeframe: str) -> FakeResult:
                observed["pair"] = pair
                observed["timeframe"] = timeframe
                return FakeResult()

        class FakePayloadLogger:
            def info(self, message: str, *args: object) -> None:
                observed["payload_log"] = message % args

        class FakeNotifier:
            def __call__(self, summary: dict[str, object]) -> None:
                observed["notify_summary"] = summary

        with (
            patch("src.graph.pipeline.TradingPipeline", FakePipeline),
            patch("src.main.get_payload_logger", return_value=FakePayloadLogger()),
            patch("src.telegram_notifier.notify_pipeline_finished", FakeNotifier()),
            patch("builtins.print") as print_mock,
        ):
            with self.assertLogs("main", level="INFO") as captured:
                main.run_once(pair="BTC/USDC", timeframe="4h")

        print_mock.assert_not_called()
        self.assertEqual(observed["config_dir"], "config")
        self.assertEqual(observed["pair"], "BTC/USDC")
        self.assertEqual(observed["timeframe"], "4h")
        payload_log = str(observed["payload_log"])
        self.assertIn("Run payload JSON:", payload_log)
        self.assertIn('"market_data"', payload_log)
        self.assertEqual(
            observed["notify_summary"],
            {
                "run_id": "r1",
                "pair": "BTC/USDC",
                "timeframe": "4h",
                "status": "NO_TRADE",
                "duration_seconds": unittest.mock.ANY,
            },
        )
        self.assertTrue(
            any(
                "Run summary run_id=r1 pair=BTC/USDC timeframe=4h status=NO_TRADE "
                "consensus_action=HOLD reason=consensus_not_actionable" in line
                for line in captured.output
            )
        )

    def test_run_once_sends_finish_notification_even_when_result_has_errors(self) -> None:
        observed: dict[str, object] = {}

        class FakeResult:
            def as_dict(self) -> dict[str, object]:
                return {
                    "run_id": "r2",
                    "pair": "BTC/USDC",
                    "timeframe": "4h",
                    "status": "NO_TRADE",
                    "consensus": {"action": "HOLD"},
                    "risk_decision": {"reason": "pipeline_error"},
                    "execution": {"reason": "pipeline_error"},
                    "errors": ["pipeline_error:boom"],
                }

        class FakePipeline:
            def __init__(self, config_dir: str) -> None:
                observed["config_dir"] = config_dir

            def run_once(self, pair: str, timeframe: str) -> FakeResult:
                observed["pair"] = pair
                observed["timeframe"] = timeframe
                return FakeResult()

        class FakeNotifier:
            def __call__(self, summary: dict[str, object]) -> None:
                observed["notify_summary"] = summary

        with (
            patch("src.graph.pipeline.TradingPipeline", FakePipeline),
            patch("src.main.get_payload_logger"),
            patch("src.telegram_notifier.notify_pipeline_finished", FakeNotifier()),
        ):
            main.run_once(pair="BTC/USDC", timeframe="4h")

        self.assertEqual(observed["config_dir"], "config")
        self.assertEqual(observed["pair"], "BTC/USDC")
        self.assertEqual(observed["timeframe"], "4h")
        self.assertEqual(
            observed["notify_summary"],
            {
                "run_id": "r2",
                "pair": "BTC/USDC",
                "timeframe": "4h",
                "status": "NO_TRADE",
                "duration_seconds": unittest.mock.ANY,
            },
        )


class ValidateConfigTests(unittest.TestCase):
    @staticmethod
    def _ta_cfg() -> dict[str, object]:
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

    @classmethod
    def _load_yaml_side_effect(cls, relative_path: str) -> dict[str, object]:
        if relative_path == "data_sources.yaml":
            return {"sentiment": {"provider": "stub"}}
        if relative_path == "ta/deterministic_ta.yaml":
            return cls._ta_cfg()
        return {}

    class FakeSettings:
        def __init__(
            self,
            *,
            telegram_enabled: bool,
            telegram_bot_token: str,
            llm_provider: str,
            llm_api_key: str,
            llm_model: str,
            llm_strict_validation: bool = False,
            sentiment_api_key: str = "",
            news_api_key: str = "",
        ) -> None:
            self.telegram_enabled = telegram_enabled
            self.telegram_bot_token = telegram_bot_token
            self.llm_provider = llm_provider
            self._llm_api_key = llm_api_key
            self._llm_model = llm_model
            self.llm_strict_validation = llm_strict_validation
            self.sentiment_api_key = sentiment_api_key
            self.news_api_key = news_api_key

        def effective_llm_provider(self, fallback: str = "mock") -> str:
            provider = self.llm_provider.strip().lower()
            return provider or fallback

        def resolve_llm_api_key(self, _provider: str | None = None) -> str:
            return self._llm_api_key

        def effective_llm_model(self, fallback: str = "") -> str:
            return self._llm_model or fallback

    def test_validate_config_returns_zero_for_analysis_defaults(self) -> None:
        fake_settings = self.FakeSettings(
            telegram_enabled=False,
            telegram_bot_token="",
            llm_provider="mock",
            llm_api_key="",
            llm_model="",
        )

        output = io.StringIO()
        with (
            patch("src.main.get_settings", return_value=fake_settings),
            patch("src.main.ConfigLoader.load_yaml", side_effect=self._load_yaml_side_effect),
            patch("src.main.ConfigLoader.load_agent_configs", return_value=[]),
            redirect_stdout(output),
        ):
            code = main.validate_config()

        self.assertEqual(code, 0)
        payload = json.loads(output.getvalue())
        self.assertFalse(payload["telegram_enabled"])
        self.assertFalse(payload["telegram_configured"])
        self.assertFalse(payload["llm_enabled"])
        self.assertTrue(payload["llm_configured"])
        self.assertFalse(payload["llm_agent_enabled"])
        self.assertFalse(payload["sentiment_agent_enabled"])
        self.assertFalse(payload["technical_agent_enabled"])
        self.assertFalse(payload["technical_ta_enabled"])
        self.assertTrue(payload["technical_ta_configured"])
        self.assertFalse(payload["sentiment_source_enabled"])
        self.assertTrue(payload["sentiment_source_configured"])

    def test_validate_config_warns_and_returns_zero_when_llm_credentials_missing(self) -> None:
        fake_settings = self.FakeSettings(
            telegram_enabled=False,
            telegram_bot_token="",
            llm_provider="openai",
            llm_api_key="",
            llm_model="gpt-4o-mini",
            llm_strict_validation=False,
        )

        output = io.StringIO()
        with (
            patch("src.main.get_settings", return_value=fake_settings),
            patch("src.main.ConfigLoader.load_yaml", side_effect=self._load_yaml_side_effect),
            patch(
                "src.main.ConfigLoader.load_agent_configs",
                return_value=[
                    {
                        "agent_id": "llm_analyst",
                        "enabled": True,
                        "uses_llm": True,
                        "model": {"provider": "openai", "model_name": "gpt-4o-mini"},
                    }
                ],
            ),
            redirect_stdout(output),
        ):
            with self.assertLogs("main", level="WARNING") as captured:
                code = main.validate_config()

        self.assertEqual(code, 0)
        payload = json.loads(output.getvalue())
        self.assertTrue(payload["llm_enabled"])
        self.assertFalse(payload["llm_configured"])
        self.assertTrue(payload["llm_agent_enabled"])
        self.assertFalse(payload["technical_ta_enabled"])
        self.assertTrue(payload["technical_ta_configured"])
        self.assertTrue(any("LLM disabled: missing credentials for provider 'openai'" in line for line in captured.output))

    def test_validate_config_returns_two_when_llm_strict_validation_enabled(self) -> None:
        fake_settings = self.FakeSettings(
            telegram_enabled=False,
            telegram_bot_token="",
            llm_provider="openai",
            llm_api_key="",
            llm_model="gpt-4o-mini",
            llm_strict_validation=True,
        )

        output = io.StringIO()
        with (
            patch("src.main.get_settings", return_value=fake_settings),
            patch("src.main.ConfigLoader.load_yaml", side_effect=self._load_yaml_side_effect),
            patch(
                "src.main.ConfigLoader.load_agent_configs",
                return_value=[
                    {
                        "agent_id": "llm_analyst",
                        "enabled": True,
                        "uses_llm": True,
                        "model": {"provider": "openai", "model_name": "gpt-4o-mini"},
                    }
                ],
            ),
            redirect_stdout(output),
        ):
            with self.assertLogs("main", level="ERROR") as captured:
                code = main.validate_config()

        self.assertEqual(code, 2)
        payload = json.loads(output.getvalue())
        self.assertTrue(payload["llm_enabled"])
        self.assertFalse(payload["llm_configured"])
        self.assertTrue(any("OPENAI_API_KEY or LLM_API_KEY" in line for line in captured.output))

    def test_validate_config_reports_sentiment_agent_enabled_without_external_source(self) -> None:
        fake_settings = self.FakeSettings(
            telegram_enabled=False,
            telegram_bot_token="",
            llm_provider="mock",
            llm_api_key="",
            llm_model="",
        )

        output = io.StringIO()
        with (
            patch("src.main.get_settings", return_value=fake_settings),
            patch("src.main.ConfigLoader.load_yaml", side_effect=self._load_yaml_side_effect),
            patch(
                "src.main.ConfigLoader.load_agent_configs",
                return_value=[
                    {"agent_id": "sentiment_analyst", "enabled": True, "uses_llm": False, "model": {"provider": "mock"}},
                    {"agent_id": "technical_analyst", "enabled": True, "uses_llm": False, "model": {"provider": "mock"}},
                    {"agent_id": "llm_analyst", "enabled": False, "uses_llm": True, "model": {"provider": "openai"}},
                ],
            ),
            redirect_stdout(output),
        ):
            code = main.validate_config()

        self.assertEqual(code, 0)
        payload = json.loads(output.getvalue())
        self.assertTrue(payload["sentiment_agent_enabled"])
        self.assertTrue(payload["technical_agent_enabled"])
        self.assertFalse(payload["llm_agent_enabled"])
        self.assertTrue(payload["technical_ta_enabled"])
        self.assertTrue(payload["technical_ta_configured"])
        self.assertFalse(payload["sentiment_source_enabled"])


if __name__ == "__main__":
    unittest.main()
