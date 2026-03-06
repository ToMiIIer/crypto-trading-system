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


if __name__ == "__main__":
    unittest.main()
