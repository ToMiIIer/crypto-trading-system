"""CLI entrypoint for Phase 1 MVP trading system."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from src.utils.config_loader import ConfigLoader
from src.utils.logger import get_logger, get_payload_logger, setup_logging
from src.utils.settings import get_settings

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCHEDULING_CONFIG_PATH = PROJECT_ROOT / "config" / "scheduling.yaml"
LOGGER = get_logger("main")


def run_once(pair: str, timeframe: str) -> None:
    from src.graph.pipeline import TradingPipeline

    started_at = time.perf_counter()

    try:
        pipeline = TradingPipeline(config_dir="config")
        result = pipeline.run_once(pair=pair, timeframe=timeframe)
    except Exception as exc:
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

    if not result_payload.get("errors"):
        try:
            from src.telegram_notifier import notify_pipeline_finished

            notify_pipeline_finished(
                {
                    "run_id": result_payload.get("run_id"),
                    "pair": result_payload.get("pair"),
                    "timeframe": result_payload.get("timeframe"),
                    "status": result_payload.get("status"),
                    "duration_seconds": round(time.perf_counter() - started_at, 3),
                }
            )
        except Exception as exc:
            LOGGER.warning("Telegram finish notification skipped: %s", exc)


def run_scheduler() -> None:
    from apscheduler.schedulers.background import BackgroundScheduler

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


def validate_config() -> int:
    settings = get_settings()

    sentiment_provider = "stub"
    try:
        data_source_cfg = ConfigLoader(PROJECT_ROOT / "config").load_yaml("data_sources.yaml")
        sentiment_provider = str(dict(data_source_cfg.get("sentiment", {})).get("provider", "stub")).strip().lower()
    except Exception as exc:
        LOGGER.warning("Could not load sentiment source config: %s", exc)

    telegram_enabled = settings.telegram_enabled
    telegram_configured = bool(settings.telegram_bot_token.strip())

    llm_provider = settings.effective_llm_provider().lower()
    llm_enabled = llm_provider != "mock"
    llm_api_key_present = bool(settings.resolve_llm_api_key(llm_provider))
    llm_model_present = bool(settings.effective_llm_model())
    llm_configured = (not llm_enabled) or (llm_api_key_present and llm_model_present)

    sentiment_source_enabled = sentiment_provider not in {"", "stub", "mock", "mvp_stub", "disabled", "none"}
    sentiment_source_configured = (not sentiment_source_enabled) or bool(
        settings.sentiment_api_key.strip() or settings.news_api_key.strip()
    )

    status = {
        "telegram_enabled": telegram_enabled,
        "telegram_configured": telegram_configured,
        "llm_enabled": llm_enabled,
        "llm_configured": llm_configured,
        "sentiment_source_enabled": sentiment_source_enabled,
        "sentiment_source_configured": sentiment_source_configured,
    }
    print(json.dumps(status, indent=2))

    missing: list[str] = []
    if telegram_enabled and not telegram_configured:
        missing.append("TELEGRAM_BOT_TOKEN")
    if llm_enabled and not llm_api_key_present:
        missing.append("LLM_API_KEY")
    if llm_enabled and not llm_model_present:
        missing.append("LLM_MODEL")
    if sentiment_source_enabled and not sentiment_source_configured:
        missing.append("SENTIMENT_API_KEY or NEWS_API_KEY")

    if missing:
        LOGGER.error("Config validation failed. Missing required keys for enabled integrations: %s", ", ".join(missing))
        return 2

    LOGGER.info("Config validation passed.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Crypto Trading System MVP")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_once_parser = subparsers.add_parser("run-once", help="Run one trading cycle")
    run_once_parser.add_argument("--pair", required=True, help="Trading pair, e.g. BTC/USDC")
    run_once_parser.add_argument("--timeframe", required=True, help="Timeframe, e.g. 4h")

    subparsers.add_parser("scheduler", help="Run APScheduler loop")
    subparsers.add_parser("validate-config", help="Validate required integration config")
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

    if args.command == "validate-config":
        raise SystemExit(validate_config())

    parser.error("unknown command")


if __name__ == "__main__":
    main()
