"""CLI entrypoint for Phase 1 MVP trading system."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.utils.config_loader import ConfigLoader
from src.utils.logger import get_logger, get_payload_logger, setup_logging
from src.utils.settings import get_settings

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCHEDULING_CONFIG_PATH = PROJECT_ROOT / "config" / "scheduling.yaml"
LOGGER = get_logger("main")


def _paper_smoke_market_data(*, price: float, sl_pct: float) -> dict[str, Any]:
    return {
        "ticker_24h": {"last_price": price},
        "indicators": {"atr_14": 0.0},
        "risk_params": {
            "max_sl_distance_pct": sl_pct,
            "atr_stop_multiplier": 1.0,
        },
    }


def execute_paper_smoke(
    *,
    pair: str,
    timeframe: str,
    buy_price: float,
    sell_price: float,
    size_pct: float,
    sl_pct: float,
    tp_pct: float,
    notify: bool,
) -> dict[str, Any]:
    from src.graph.nodes.executor import PaperExecutorNode
    from src.risk.manager import RiskDecision
    from src.storage.repository import StorageRepository

    settings = get_settings()
    repository = StorageRepository(settings.effective_database_url())
    repository.initialize()

    now = int(time.time())
    preclose_run_id = f"paper-smoke-preclose-{now}"
    open_run_id = f"paper-smoke-open-{now}"
    close_run_id = f"paper-smoke-close-{now + 1}"

    existing_open = repository.get_open_position(pair=pair, timeframe=timeframe)
    if existing_open is not None:
        repository.close_position(
            run_id=preclose_run_id,
            pair=pair,
            timeframe=timeframe,
            position_id=existing_open.id,
            exit_price=buy_price,
            reason="paper_smoke_preclose_existing",
        )
        repository.refresh_performance(run_id=preclose_run_id, pair=pair, timeframe=timeframe)
        LOGGER.info("paper-smoke preclosed existing position id=%s pair=%s timeframe=%s", existing_open.id, pair, timeframe)

    portfolio_state = {
        "cash_balance": 10000.0,
        "equity": 10000.0,
        "total_exposure": 0.0,
        "open_positions": 0,
        "daily_pnl_pct": 0.0,
        "symbol_exposure_pct": 0.0,
        "cash_buffer_pct": 1.0,
    }
    executor = PaperExecutorNode(repository=repository, force_trade_notifications=notify)

    open_result = executor.run(
        run_id=open_run_id,
        pair=pair,
        timeframe=timeframe,
        risk_decision=RiskDecision(
            approved=True,
            action="BUY",
            reason="paper_smoke_force_buy",
            position_pct=size_pct,
            stop_loss_pct=sl_pct,
            take_profit_pct=tp_pct,
        ),
        market_data=_paper_smoke_market_data(price=buy_price, sl_pct=sl_pct),
        portfolio_state=portfolio_state,
    )
    if str(open_result.get("status", "")).upper() != "SIMULATED_TRADE_OPENED":
        raise RuntimeError(f"paper_smoke_open_failed:{open_result.get('reason', 'unknown')}")

    portfolio_state["open_positions"] = len(repository.list_active_positions())
    close_result = executor.run(
        run_id=close_run_id,
        pair=pair,
        timeframe=timeframe,
        risk_decision=RiskDecision(
            approved=True,
            action="SELL",
            reason="paper_smoke_force_sell",
            position_pct=size_pct,
            stop_loss_pct=sl_pct,
            take_profit_pct=tp_pct,
        ),
        market_data=_paper_smoke_market_data(price=sell_price, sl_pct=sl_pct),
        portfolio_state=portfolio_state,
    )
    if str(close_result.get("status", "")).upper() != "SIMULATED_TRADE_CLOSED":
        raise RuntimeError(f"paper_smoke_close_failed:{close_result.get('reason', 'unknown')}")

    run_ids = {open_run_id, close_run_id}
    smoke_trades = [row for row in repository.list_trades_for_dashboard() if str(row.get("run_id", "")) in run_ids]
    active_positions = [
        row
        for row in repository.list_active_positions()
        if str(row.get("pair", "")) == pair and str(row.get("timeframe", "")) == timeframe
    ]
    perf_rows = [row for row in repository.list_performance_snapshots() if str(row.get("run_id", "")) == close_run_id]
    notifications = [
        row
        for row in repository.list_notifications()
        if str(row.get("run_id", "")) in run_ids and str(row.get("type", "")) == "TRADE"
    ]

    if len(smoke_trades) < 2:
        raise RuntimeError(f"paper_smoke_trades_too_few:{len(smoke_trades)}")
    if active_positions:
        raise RuntimeError("paper_smoke_active_position_not_closed")
    if not perf_rows:
        raise RuntimeError("paper_smoke_missing_performance_snapshot")

    return {
        "mode": "paper_smoke",
        "pair": pair,
        "timeframe": timeframe,
        "buy_price": buy_price,
        "sell_price": sell_price,
        "size_pct": size_pct,
        "sl_pct": sl_pct,
        "tp_pct": tp_pct,
        "run_id_open": open_run_id,
        "run_id_close": close_run_id,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "trade_events_count": len(smoke_trades),
        "active_positions_after_close": len(active_positions),
        "realized_pnl_abs": close_result.get("pnl_abs"),
        "realized_pnl_pct": close_result.get("pnl_pct"),
        "trade_notifications_recorded": len(notifications),
    }


def export_dashboard() -> int:
    from src.storage.dashboard_exporter import DashboardExporter
    from src.storage.repository import StorageRepository

    settings = get_settings()
    repository = StorageRepository(settings.effective_database_url())
    repository.initialize()

    exporter = DashboardExporter(repository=repository, reports_dir=settings.reports_dir_path())
    paths = exporter.export()
    LOGGER.info("Dashboard export completed: %s", paths["xlsx"])
    return 0


def run_once(pair: str, timeframe: str) -> None:
    from src.graph.pipeline import TradingPipeline

    started_at = time.perf_counter()
    settings = get_settings()

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

    if settings.export_dashboard_on_finish:
        try:
            export_dashboard()
        except Exception as exc:
            LOGGER.warning("Dashboard auto-export skipped: %s", exc)


def paper_smoke(
    *,
    pair: str,
    timeframe: str,
    buy_price: float,
    sell_price: float,
    size_pct: float,
    sl_pct: float,
    tp_pct: float,
    notify: bool,
) -> int:
    try:
        summary = execute_paper_smoke(
            pair=pair,
            timeframe=timeframe,
            buy_price=buy_price,
            sell_price=sell_price,
            size_pct=size_pct,
            sl_pct=sl_pct,
            tp_pct=tp_pct,
            notify=notify,
        )
    except Exception as exc:
        LOGGER.error("Paper smoke failed: %s", exc)
        return 2

    print(json.dumps(summary, indent=2))
    settings = get_settings()
    if settings.export_dashboard_on_finish:
        try:
            export_dashboard()
        except Exception as exc:
            LOGGER.warning("Dashboard auto-export skipped: %s", exc)
    return 0


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
    loader = ConfigLoader(PROJECT_ROOT / "config")

    sentiment_provider = "stub"
    llm_agents: list[tuple[str, str]] = []
    sentiment_agent_enabled = False
    technical_agent_enabled = False
    llm_agent_enabled = False
    try:
        data_source_cfg = loader.load_yaml("data_sources.yaml")
        sentiment_provider = str(dict(data_source_cfg.get("sentiment", {})).get("provider", "stub")).strip().lower()
    except Exception as exc:
        LOGGER.warning("Could not load sentiment source config: %s", exc)
    try:
        agent_cfgs = loader.load_agent_configs()
        for agent_cfg in agent_cfgs:
            agent_id = str(agent_cfg.get("agent_id", "")).strip().lower()
            enabled = bool(agent_cfg.get("enabled", True))
            if agent_id == "sentiment_analyst" and enabled:
                sentiment_agent_enabled = True
            if agent_id == "technical_analyst" and enabled:
                technical_agent_enabled = True
            if agent_id == "llm_analyst" and enabled:
                llm_agent_enabled = True

            if not enabled:
                continue
            model_cfg = dict(agent_cfg.get("model", {}))
            provider = str(model_cfg.get("provider", "mock")).strip().lower()
            model_name = str(model_cfg.get("model_name", "")).strip()
            uses_llm_raw = agent_cfg.get("uses_llm")
            uses_llm = bool(uses_llm_raw) if uses_llm_raw is not None else provider != "mock"
            if uses_llm:
                llm_agents.append((provider or "mock", model_name))
    except Exception as exc:
        LOGGER.warning("Could not load agent config for LLM validation: %s", exc)

    telegram_enabled = settings.telegram_enabled
    telegram_configured = bool(settings.telegram_bot_token.strip())

    env_provider = settings.effective_llm_provider(fallback="").lower()
    llm_provider = env_provider or (llm_agents[0][0] if llm_agents else "mock")
    llm_enabled = bool(llm_agents) or bool(env_provider and env_provider != "mock")
    llm_api_key_present = bool(settings.resolve_llm_api_key(llm_provider))
    llm_model_present = bool(settings.effective_llm_model()) or any(model_name for _provider, model_name in llm_agents)
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
        "llm_agent_enabled": llm_agent_enabled,
        "sentiment_agent_enabled": sentiment_agent_enabled,
        "technical_agent_enabled": technical_agent_enabled,
        "sentiment_source_enabled": sentiment_source_enabled,
        "sentiment_source_configured": sentiment_source_configured,
    }
    print(json.dumps(status, indent=2))

    missing: list[str] = []
    if telegram_enabled and not telegram_configured:
        missing.append("TELEGRAM_BOT_TOKEN")
    if llm_enabled and not llm_api_key_present:
        LOGGER.warning("LLM disabled: missing credentials for provider '%s'", llm_provider)
        provider_missing_map = {
            "openai": "OPENAI_API_KEY or LLM_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY or LLM_API_KEY",
            "google": "GOOGLE_API_KEY or LLM_API_KEY",
        }
        if settings.llm_strict_validation:
            missing.append(provider_missing_map.get(llm_provider, "LLM_API_KEY"))
    if llm_enabled and not llm_model_present:
        missing.append("LLM_MODEL or model_name in config/agents/*.yaml")
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
    subparsers.add_parser("export-dashboard", help="Export dashboard XLSX/CSV reports")

    paper_smoke_parser = subparsers.add_parser("paper-smoke", help="Run deterministic offline paper BUY->SELL smoke test")
    paper_smoke_parser.add_argument("--pair", default="BTC/USDC", help="Trading pair, e.g. BTC/USDC")
    paper_smoke_parser.add_argument("--timeframe", default="4h", help="Timeframe, e.g. 4h")
    paper_smoke_parser.add_argument("--buy-price", type=float, default=100.0, help="Forced BUY execution price")
    paper_smoke_parser.add_argument("--sell-price", type=float, default=101.0, help="Forced SELL execution price")
    paper_smoke_parser.add_argument("--size-pct", type=float, default=0.10, help="Position size pct, e.g. 0.10")
    paper_smoke_parser.add_argument("--sl-pct", type=float, default=0.01, help="Stop-loss pct, e.g. 0.01")
    paper_smoke_parser.add_argument("--tp-pct", type=float, default=0.02, help="Take-profit pct, e.g. 0.02")
    paper_smoke_parser.add_argument(
        "--notify",
        action="store_true",
        help="Force Telegram trade notifications even when TELEGRAM_NOTIFY_TRADES is false",
    )
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

    if args.command == "export-dashboard":
        raise SystemExit(export_dashboard())

    if args.command == "paper-smoke":
        raise SystemExit(
            paper_smoke(
                pair=args.pair,
                timeframe=args.timeframe,
                buy_price=args.buy_price,
                sell_price=args.sell_price,
                size_pct=args.size_pct,
                sl_pct=args.sl_pct,
                tp_pct=args.tp_pct,
                notify=args.notify,
            )
        )

    parser.error("unknown command")


if __name__ == "__main__":
    main()
