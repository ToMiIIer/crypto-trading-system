from __future__ import annotations

from pathlib import Path
import unittest
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
            patch("src.utils.config_loader.ConfigLoader", FakeConfigLoader),
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

        with (
            patch("src.graph.pipeline.TradingPipeline", FakePipeline),
            patch("src.main.get_payload_logger", return_value=FakePayloadLogger()),
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
        self.assertTrue(
            any(
                "Run summary run_id=r1 pair=BTC/USDC timeframe=4h status=NO_TRADE "
                "consensus_action=HOLD reason=consensus_not_actionable" in line
                for line in captured.output
            )
        )


if __name__ == "__main__":
    unittest.main()
