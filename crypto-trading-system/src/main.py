"""CLI entrypoint for Phase 1 MVP trading system."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from src.utils.logger import get_logger, get_payload_logger, setup_logging

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCHEDULING_CONFIG_PATH = PROJECT_ROOT / "config" / "scheduling.yaml"
LOGGER = get_logger("main")


def _notifications_enabled() -> bool:
    return os.getenv("TELEGRAM_NOTIFY_PIPELINE", "false").strip().lower() in {"1", "true", "yes"}


def run_once(pair: str, timeframe: str) -> None:
    from src.graph.pipeline import TradingPipeline

    notifier_available = _notifications_enabled()

    if notifier_available:
        try:
            from src.telegram_notifier import send_message

            send_message("▶️ Pipeline started")
        except Exception as exc:
            LOGGER.warning("Telegram start notification skipped: %s", exc)

    try:
        pipeline = TradingPipeline(config_dir="config")
        result = pipeline.run_once(pair=pair, timeframe=timeframe)
    except Exception as exc:
        if notifier_available:
            try:
                from src.telegram_notifier import send_error

                send_error("Pipeline failed", exc)
            except Exception as notify_exc:
                LOGGER.warning("Telegram error notification skipped: %s", notify_exc)
        raise

    result_payload = result.as_dict()

    get_payload_logger().info("Run payload JSON: %s", json.dumps(result_payload, default=str))

    consensus = result_payload.get("consensus") if isinstance(result_payload.get("consensus"), dict) else {}
    execution = result_payload.get("execution") if isinstance(result_payload.get("execution"), dict) else {}
    risk = result_payload.get("risk_decision") if isinstance(result_payload.get("risk_decision"), dict) else {}

    reason = str(execution.get("reason") or risk.get("reason") or "n/a")
    LOGGER.info(
        "Run summary run_id=%s pair=%s timeframe=%s status=%s consensus_action=%s reason=%s",
        result_payload.get("run_id", "n/a"),
        result_payload.get("pair", pair),
        result_payload.get("timeframe", timeframe),
        result_payload.get("status", "NO_TRADE"),
        consensus.get("action", "n/a"),
        reason,
    )

    if notifier_available:
        try:
            from src.telegram_notifier import send_message

            send_message("✅ Pipeline finished")
        except Exception as exc:
            LOGGER.warning("Telegram finish notification skipped: %s", exc)


def run_scheduler() -> None:
    from apscheduler.schedulers.background import BackgroundScheduler

    from src.utils.config_loader import ConfigLoader

    LOGGER.info("Loaded scheduling config from: %s", SCHEDULING_CONFIG_PATH.resolve())
    loader = ConfigLoader(SCHEDULING_CONFIG_PATH.parent)
    scheduling = loader.load_yaml("scheduling.yaml")
    job_cfg = dict(scheduling.get("default_job", {}))
    cron_cfg = dict(job_cfg.get("cron", {}))

    pair = str(job_cfg.get("pair", "BTC/USDC"))
    timeframe = str(job_cfg.get("timeframe", "4h"))
    minute = str(cron_cfg.get("minute", "0"))
    hour = str(cron_cfg.get("hour", "*/4"))

    scheduler = BackgroundScheduler()
    scheduler.add_job(
        lambda: run_once(pair=pair, timeframe=timeframe),
        trigger="cron",
        minute=minute,
        hour=hour,
        id="phase1_main",
        replace_existing=True,
    )
    scheduler.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        scheduler.shutdown(wait=False)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Crypto Trading System MVP")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_once_parser = subparsers.add_parser("run-once", help="Run one trading cycle")
    run_once_parser.add_argument("--pair", required=True, help="Trading pair, e.g. BTC/USDC")
    run_once_parser.add_argument("--timeframe", required=True, help="Timeframe, e.g. 4h")

    subparsers.add_parser("scheduler", help="Run APScheduler loop")
    return parser


def main() -> None:
    setup_logging()
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "run-once":
        run_once(pair=args.pair, timeframe=args.timeframe)
        return

    if args.command == "scheduler":
        run_scheduler()
        return

    parser.error("unknown command")


if __name__ == "__main__":
    main()
