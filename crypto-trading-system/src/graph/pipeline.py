"""Main Phase 1 paper-trading pipeline orchestration."""

from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime, timezone
from typing import Any

from src.agents.base_agent import AgentResult
from src.alerts.telegram import TelegramAlerter
from src.data.price_fetcher import BinancePriceFetcher, OHLCV
from src.graph.nodes.agent_runner import AgentRunnerNode
from src.graph.nodes.consensus import WeightedConsensusEngine
from src.graph.nodes.data_collector import DataCollectorNode
from src.graph.nodes.executor import PaperExecutorNode
from src.graph.nodes.risk_check import RiskCheckNode
from src.graph.state import PipelineState
from src.indicators.atr import calculate_atr
from src.indicators.bollinger import calculate_bollinger_bands
from src.indicators.ema import calculate_ema
from src.indicators.macd import calculate_macd
from src.indicators.rsi import calculate_rsi
from src.risk.manager import RiskManager
from src.storage.repository import StorageRepository
from src.utils.config_loader import ConfigLoader
from src.utils.logger import get_logger
from src.utils.settings import get_settings


class TradingPipeline:
    """Coordinates all Phase 1 components with fail-safe NO_TRADE behavior."""

    def __init__(self, config_dir: str = "config") -> None:
        self.logger = get_logger("pipeline")
        self.config_loader = ConfigLoader(config_dir)
        self.settings = get_settings()
        self.alerter = TelegramAlerter.from_env()
        database_url = self.settings.effective_database_url()
        self.repository = StorageRepository(database_url)
        self.repository.initialize()

    def run_once(
        self,
        *,
        pair: str,
        timeframe: str,
        run_id: str | None = None,
    ) -> PipelineState:
        started_at = datetime.now(timezone.utc)
        provisional_id = run_id or self._provisional_run_id(pair=pair, timeframe=timeframe)
        state = PipelineState(run_id=provisional_id, pair=pair, timeframe=timeframe)

        portfolio_snapshot = self._default_portfolio_snapshot()
        try:
            configs = self.config_loader.load_all()
            data_source_cfg = dict(configs["data_sources"]["binance"])

            collector = DataCollectorNode(
                fetcher=BinancePriceFetcher(
                    base_url=str(data_source_cfg["base_url"]),
                    timeout_seconds=int(data_source_cfg.get("timeout_seconds", 10)),
                ),
                source_cfg=data_source_cfg,
            )
            bundle = collector.run(pair=pair, timeframe=timeframe)

            if run_id is None:
                state.run_id = self._stable_run_id(pair=pair, timeframe=timeframe, candles=bundle.ohlcv)

            state.market_data = {
                "pair": pair,
                "timeframe": timeframe,
                "ohlcv": [self._candle_to_dict(candle) for candle in bundle.ohlcv],
                "ticker_24h": bundle.ticker_24h,
            }

            indicators = self._compute_indicators(bundle.ohlcv)
            self._assert_indicator_completeness(indicators)
            state.indicators = indicators

            agent_runner = AgentRunnerNode(agent_configs=configs["agents"])
            hypotheses = agent_runner.run(
                run_id=state.run_id,
                pair=pair,
                timeframe=timeframe,
                market_context={
                    "pair": pair,
                    "timeframe": timeframe,
                    "ticker_24h": bundle.ticker_24h,
                    "indicators": indicators,
                },
            )
            state.hypotheses = hypotheses

            self.repository.upsert_hypotheses(hypotheses)
            self._persist_agent_performance(hypotheses)

            consensus_engine = WeightedConsensusEngine(configs["consensus"])
            consensus = consensus_engine.run(
                run_id=state.run_id,
                pair=pair,
                timeframe=timeframe,
                hypotheses=hypotheses,
            )
            state.consensus = consensus
            self.repository.upsert_consensus(consensus)

            risk_manager = RiskManager(configs["risk"])
            risk_node = RiskCheckNode(risk_manager)
            risk_decision = risk_node.run(
                consensus=consensus,
                portfolio_state=portfolio_snapshot,
                market_state={
                    "price": float(bundle.ticker_24h.get("last_price", 0.0)),
                    "atr": float(indicators.get("atr_14", 0.0) or 0.0),
                },
            )
            state.risk_decision = risk_decision

            if self.settings.execution_enabled():
                executor = PaperExecutorNode(self.repository)
                execution = executor.run(
                    run_id=state.run_id,
                    pair=pair,
                    timeframe=timeframe,
                    risk_decision=risk_decision,
                    market_data={
                        "ticker_24h": bundle.ticker_24h,
                        "indicators": indicators,
                        "risk_params": configs["risk"],
                    },
                    portfolio_state=portfolio_snapshot,
                )
            else:
                self.logger.info("execution disabled (analysis-only mode)", extra={"run_id": state.run_id})
                execution = {
                    "status": "NO_TRADE",
                    "reason": "execution_disabled_analysis_only",
                    "action": "HOLD",
                }
            state.execution = execution
            state.status = str(execution.get("status", "NO_TRADE"))

            try:
                portfolio_snapshot["open_positions"] = len(self.repository.list_active_positions())
            except Exception:
                self.logger.exception("Failed to refresh open positions count", extra={"run_id": state.run_id})

            if not state.status.startswith("SIMULATED_TRADE"):
                self.logger.info("Run finished with NO_TRADE", extra={"run_id": state.run_id, "reason": execution.get("reason")})

        except Exception as exc:
            error_message = f"pipeline_error:{exc}"
            state.errors.append(error_message)
            state.status = "NO_TRADE"
            state.execution = {
                "status": "NO_TRADE",
                "reason": error_message,
            }
            self.logger.exception("Pipeline failed", extra={"run_id": state.run_id})

        finally:
            finished_at = datetime.now(timezone.utc)
            try:
                self.repository.upsert_portfolio_snapshot(
                    run_id=state.run_id,
                    cash_balance=float(portfolio_snapshot["cash_balance"]),
                    equity=float(portfolio_snapshot["equity"]),
                    total_exposure=float(portfolio_snapshot["total_exposure"]),
                    open_positions=int(portfolio_snapshot["open_positions"]),
                    metadata={
                        "status": state.status,
                        "pair": pair,
                        "timeframe": timeframe,
                        "errors": state.errors,
                    },
                )
            except Exception:
                self.logger.exception("Failed to persist portfolio snapshot", extra={"run_id": state.run_id})

            try:
                self.repository.persist_dashboard_run(
                    payload=state.as_dict(),
                    started_at=started_at,
                    finished_at=finished_at,
                )
            except Exception:
                self.logger.exception("Failed to persist dashboard run", extra={"run_id": state.run_id})

            self._send_alert(state)

        return state

    def _persist_agent_performance(self, hypotheses: list[AgentResult]) -> None:
        for hypothesis in hypotheses:
            self.repository.upsert_agent_performance(
                run_id=hypothesis.run_id,
                agent_id=hypothesis.agent_id,
                action=hypothesis.action,
                confidence=hypothesis.confidence,
            )

    @staticmethod
    def _candle_to_dict(candle: OHLCV) -> dict[str, Any]:
        return {
            "open_time": candle.open_time,
            "open": candle.open,
            "high": candle.high,
            "low": candle.low,
            "close": candle.close,
            "volume": candle.volume,
            "close_time": candle.close_time,
        }

    def _compute_indicators(self, candles: list[OHLCV]) -> dict[str, Any]:
        closes = [item.close for item in candles]
        highs = [item.high for item in candles]
        lows = [item.low for item in candles]

        rsi = calculate_rsi(closes, 14)
        ema_21 = calculate_ema(closes, 21)
        ema_50 = calculate_ema(closes, 50)
        ema_200 = calculate_ema(closes, 200)
        macd = calculate_macd(closes)
        atr = calculate_atr(highs, lows, closes, 14)
        bollinger = calculate_bollinger_bands(closes, 20, 2.0)

        return {
            "rsi_14": self._last_value(rsi),
            "ema_21": self._last_value(ema_21),
            "ema_50": self._last_value(ema_50),
            "ema_200": self._last_value(ema_200),
            "macd": {
                "value": self._last_value(macd["macd"]),
                "signal": self._last_value(macd["signal"]),
                "histogram": self._last_value(macd["histogram"]),
            },
            "atr_14": self._last_value(atr),
            "bollinger_20_2": {
                "middle": self._last_value(bollinger["middle"]),
                "upper": self._last_value(bollinger["upper"]),
                "lower": self._last_value(bollinger["lower"]),
            },
        }

    @staticmethod
    def _last_value(values: list[float | None]) -> float | None:
        for value in reversed(values):
            if value is not None:
                return float(value)
        return None

    @staticmethod
    def _assert_indicator_completeness(indicators: dict[str, Any]) -> None:
        required = ["rsi_14", "ema_21", "ema_50", "ema_200", "atr_14"]
        for key in required:
            if indicators.get(key) is None:
                raise ValueError(f"missing_indicator:{key}")

    @staticmethod
    def _default_portfolio_snapshot() -> dict[str, float | int]:
        return {
            "cash_balance": 10000.0,
            "equity": 10000.0,
            "total_exposure": 0.0,
            "open_positions": 0,
            "daily_pnl_pct": 0.0,
            "symbol_exposure_pct": 0.0,
            "cash_buffer_pct": 1.0,
        }

    @staticmethod
    def _stable_run_id(pair: str, timeframe: str, candles: list[OHLCV]) -> str:
        if not candles:
            return TradingPipeline._provisional_run_id(pair, timeframe)

        last = candles[-1]
        digest = hashlib.sha256(
            f"{pair}|{timeframe}|{last.close_time}|{last.close:.8f}".encode("utf-8")
        ).hexdigest()
        return digest[:24]

    @staticmethod
    def _provisional_run_id(pair: str, timeframe: str) -> str:
        base = f"{pair}|{timeframe}|{int(time.time())}"
        return hashlib.sha256(base.encode("utf-8")).hexdigest()[:24]

    def _send_alert(self, state: PipelineState) -> None:
        if not self.settings.telegram_notify_pipeline:
            try:
                self.repository.record_notification(
                    run_id=state.run_id,
                    notification_type="PIPELINE_FINISH",
                    sent_ok=False,
                    error_message="disabled_by_TELEGRAM_NOTIFY_PIPELINE",
                )
            except Exception:
                self.logger.exception("Failed to persist notification event", extra={"run_id": state.run_id})
            return

        summary = (
            f"run_id={state.run_id}\n"
            f"pair={state.pair} timeframe={state.timeframe}\n"
            f"status={state.status}\n"
            f"consensus_action={state.consensus.action if state.consensus else 'N/A'}\n"
            f"weighted_confidence={state.consensus.weighted_confidence if state.consensus else 'N/A'}\n"
            f"risk_reason={state.risk_decision.reason if state.risk_decision else 'N/A'}\n"
            f"execution_reason={state.execution.get('reason', 'N/A')}\n"
            f"errors={'; '.join(state.errors) if state.errors else 'none'}"
        )
        sent, error = self.alerter.send(summary)
        try:
            self.repository.record_notification(
                run_id=state.run_id,
                notification_type="PIPELINE_FINISH",
                sent_ok=sent,
                error_message=error,
            )
        except Exception:
            self.logger.exception("Failed to persist notification event", extra={"run_id": state.run_id})

        if not sent and error:
            logging.getLogger("pipeline").info(
                "telegram alert not sent",
                extra={"run_id": state.run_id, "reason": error},
            )
