"""Persistence repository with idempotent upsert guards."""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from sqlalchemy import create_engine, desc, select
from sqlalchemy.orm import Session, sessionmaker

from src.agents.base_agent import AgentResult
from src.graph.nodes.consensus import ConsensusDecision
from src.storage.models import (
    AgentOutput,
    AgentPerformance,
    Base,
    ConsensusLog,
    Hypothesis,
    IndicatorSnapshot,
    Notification,
    PipelineRun,
    PortfolioSnapshot,
    Trade,
)


class StorageRepository:
    def __init__(self, database_url: str) -> None:
        self.engine = create_engine(database_url, future=True, pool_pre_ping=True)
        self._session_factory = sessionmaker(bind=self.engine, expire_on_commit=False, class_=Session)

    def initialize(self) -> None:
        Base.metadata.create_all(self.engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def upsert_hypotheses(self, hypotheses: list[AgentResult]) -> None:
        if not hypotheses:
            return

        with self.session() as session:
            for hypothesis in hypotheses:
                existing = session.scalar(
                    select(Hypothesis).where(
                        Hypothesis.run_id == hypothesis.run_id,
                        Hypothesis.agent_id == hypothesis.agent_id,
                    )
                )
                if existing:
                    existing.action = hypothesis.action
                    existing.confidence = hypothesis.confidence
                    existing.reasoning = hypothesis.reasoning
                    existing.risk_notes = hypothesis.risk_notes
                    continue

                session.add(
                    Hypothesis(
                        run_id=hypothesis.run_id,
                        pair=hypothesis.pair,
                        timeframe=hypothesis.timeframe,
                        agent_id=hypothesis.agent_id,
                        action=hypothesis.action,
                        confidence=hypothesis.confidence,
                        reasoning=hypothesis.reasoning,
                        risk_notes=hypothesis.risk_notes,
                    )
                )

    def upsert_consensus(self, decision: ConsensusDecision) -> None:
        with self.session() as session:
            existing = session.scalar(select(ConsensusLog).where(ConsensusLog.run_id == decision.run_id))
            if existing:
                existing.action = decision.action
                existing.weighted_confidence = decision.weighted_confidence
                existing.threshold_passed = decision.threshold_passed
                existing.scores_json = json.dumps(decision.scores, separators=(",", ":"))
                existing.weights_json = json.dumps(decision.weights_used, separators=(",", ":"))
                existing.reasoning = decision.reasoning
                return

            session.add(
                ConsensusLog(
                    run_id=decision.run_id,
                    pair=decision.pair,
                    timeframe=decision.timeframe,
                    action=decision.action,
                    weighted_confidence=decision.weighted_confidence,
                    threshold_passed=decision.threshold_passed,
                    scores_json=json.dumps(decision.scores, separators=(",", ":")),
                    weights_json=json.dumps(decision.weights_used, separators=(",", ":")),
                    reasoning=decision.reasoning,
                )
            )

    def create_simulated_trade(
        self,
        *,
        run_id: str,
        pair: str,
        timeframe: str,
        action: str,
        quantity: float,
        entry_price: float,
        reason: str,
    ) -> Trade:
        with self.session() as session:
            existing = session.scalar(
                select(Trade).where(
                    Trade.run_id == run_id,
                    Trade.pair == pair,
                    Trade.timeframe == timeframe,
                )
            )
            if existing:
                return existing

            trade = Trade(
                run_id=run_id,
                pair=pair,
                timeframe=timeframe,
                action=action,
                quantity=quantity,
                entry_price=entry_price,
                reason=reason,
                status="SIMULATED",
            )
            session.add(trade)
            session.flush()
            return trade

    def upsert_portfolio_snapshot(
        self,
        *,
        run_id: str,
        cash_balance: float,
        equity: float,
        total_exposure: float,
        open_positions: int,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        payload = json.dumps(metadata or {}, separators=(",", ":"))
        with self.session() as session:
            existing = session.scalar(
                select(PortfolioSnapshot).where(PortfolioSnapshot.run_id == run_id)
            )
            if existing:
                existing.cash_balance = cash_balance
                existing.equity = equity
                existing.total_exposure = total_exposure
                existing.open_positions = open_positions
                existing.metadata_json = payload
                return

            session.add(
                PortfolioSnapshot(
                    run_id=run_id,
                    cash_balance=cash_balance,
                    equity=equity,
                    total_exposure=total_exposure,
                    open_positions=open_positions,
                    metadata_json=payload,
                )
            )

    def upsert_agent_performance(
        self,
        *,
        run_id: str,
        agent_id: str,
        action: str,
        confidence: float,
        pnl: float = 0.0,
        outcome: str = "PENDING",
    ) -> None:
        with self.session() as session:
            existing = session.scalar(
                select(AgentPerformance).where(
                    AgentPerformance.run_id == run_id,
                    AgentPerformance.agent_id == agent_id,
                )
            )

    @staticmethod
    def _as_float(value: Any) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _as_bool(value: Any) -> bool | None:
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"1", "true", "yes", "on"}:
                return True
            if lowered in {"0", "false", "no", "off"}:
                return False
        return bool(value)

    def persist_dashboard_run(
        self,
        *,
        payload: dict[str, Any],
        started_at: datetime,
        finished_at: datetime,
    ) -> None:
        run_id = str(payload.get("run_id", "")).strip()
        if not run_id:
            return

        pair = str(payload.get("pair", ""))
        timeframe = str(payload.get("timeframe", ""))
        status = str(payload.get("status", "NO_TRADE"))
        consensus = payload.get("consensus") if isinstance(payload.get("consensus"), dict) else {}
        risk = payload.get("risk_decision") if isinstance(payload.get("risk_decision"), dict) else {}
        execution = payload.get("execution") if isinstance(payload.get("execution"), dict) else {}
        indicators = payload.get("indicators") if isinstance(payload.get("indicators"), dict) else {}
        hypotheses = payload.get("hypotheses") if isinstance(payload.get("hypotheses"), list) else []
        errors = payload.get("errors") if isinstance(payload.get("errors"), list) else []
        warnings = payload.get("warnings") if isinstance(payload.get("warnings"), list) else []

        raw_payload_json = json.dumps(payload, separators=(",", ":"), default=str)

        with self.session() as session:
            run_row = session.scalar(select(PipelineRun).where(PipelineRun.run_id == run_id))
            if not run_row:
                run_row = PipelineRun(
                    run_id=run_id,
                    started_at=started_at,
                    finished_at=finished_at,
                    pair=pair,
                    timeframe=timeframe,
                    status=status,
                    raw_payload_json=raw_payload_json,
                )
                session.add(run_row)

            run_row.started_at = started_at
            run_row.finished_at = finished_at
            run_row.pair = pair
            run_row.timeframe = timeframe
            run_row.status = status
            run_row.consensus_action = str(consensus.get("action")) if consensus.get("action") is not None else None
            run_row.consensus_confidence = self._as_float(consensus.get("weighted_confidence"))
            run_row.threshold_passed = self._as_bool(consensus.get("threshold_passed"))
            run_row.consensus_reason = str(consensus.get("reasoning")) if consensus.get("reasoning") is not None else None
            run_row.risk_approved = self._as_bool(risk.get("approved"))
            run_row.risk_reason = str(risk.get("reason")) if risk.get("reason") is not None else None
            run_row.position_pct = self._as_float(risk.get("position_pct"))
            run_row.stop_loss_pct = self._as_float(risk.get("stop_loss_pct"))
            run_row.take_profit_pct = self._as_float(risk.get("take_profit_pct"))
            run_row.execution_status = str(execution.get("status")) if execution.get("status") is not None else None
            run_row.execution_reason = str(execution.get("reason")) if execution.get("reason") is not None else None
            run_row.raw_payload_json = raw_payload_json
            agent_warning_count = 0
            for hypothesis_payload in hypotheses:
                if not isinstance(hypothesis_payload, dict):
                    continue
                if hypothesis_payload.get("error_code") or hypothesis_payload.get("error_message"):
                    agent_warning_count += 1

            run_row.warnings_count = len(warnings) + agent_warning_count
            run_row.errors_count = len(errors)

            for hypothesis_payload in hypotheses:
                if not isinstance(hypothesis_payload, dict):
                    continue

                agent_id = str(hypothesis_payload.get("agent_id", "")).strip()
                if not agent_id:
                    continue

                output_row = session.scalar(
                    select(AgentOutput).where(
                        AgentOutput.run_id == run_id,
                        AgentOutput.agent_id == agent_id,
                    )
                )
                if not output_row:
                    output_row = AgentOutput(run_id=run_id, agent_id=agent_id, action="HOLD", confidence=0.0, reasoning="", risk_notes="")
                    session.add(output_row)

                output_row.action = str(hypothesis_payload.get("action", "HOLD"))
                output_row.confidence = float(hypothesis_payload.get("confidence", 0.0))
                output_row.reasoning = str(hypothesis_payload.get("reasoning", ""))
                output_row.risk_notes = str(hypothesis_payload.get("risk_notes", ""))
                output_row.provider_used = (
                    str(hypothesis_payload.get("provider_used")) if hypothesis_payload.get("provider_used") is not None else None
                )
                output_row.error_code = (
                    str(hypothesis_payload.get("error_code")) if hypothesis_payload.get("error_code") is not None else None
                )
                output_row.error_message = (
                    str(hypothesis_payload.get("error_message")) if hypothesis_payload.get("error_message") is not None else None
                )
                output_row.raw_agent_json = json.dumps(hypothesis_payload, separators=(",", ":"), default=str)

            indicator_row = session.scalar(select(IndicatorSnapshot).where(IndicatorSnapshot.run_id == run_id))
            if not indicator_row:
                indicator_row = IndicatorSnapshot(run_id=run_id)
                session.add(indicator_row)

            macd = indicators.get("macd") if isinstance(indicators.get("macd"), dict) else {}
            bollinger = indicators.get("bollinger_20_2") if isinstance(indicators.get("bollinger_20_2"), dict) else {}

            indicator_row.rsi_14 = self._as_float(indicators.get("rsi_14"))
            indicator_row.ema_21 = self._as_float(indicators.get("ema_21"))
            indicator_row.ema_50 = self._as_float(indicators.get("ema_50"))
            indicator_row.ema_200 = self._as_float(indicators.get("ema_200"))
            indicator_row.atr_14 = self._as_float(indicators.get("atr_14"))
            indicator_row.macd_value = self._as_float(macd.get("value"))
            indicator_row.macd_signal = self._as_float(macd.get("signal"))
            indicator_row.macd_hist = self._as_float(macd.get("histogram"))
            indicator_row.bb_middle = self._as_float(bollinger.get("middle"))
            indicator_row.bb_upper = self._as_float(bollinger.get("upper"))
            indicator_row.bb_lower = self._as_float(bollinger.get("lower"))
            indicator_row.raw_indicators_json = json.dumps(indicators, separators=(",", ":"), default=str)

    def record_notification(
        self,
        *,
        run_id: str,
        notification_type: str,
        sent_ok: bool,
        error_message: str | None = None,
    ) -> None:
        with self.session() as session:
            session.add(
                Notification(
                    run_id=run_id,
                    notification_type=notification_type,
                    sent_ok=sent_ok,
                    error_message=error_message,
                )
            )

    @staticmethod
    def _dt_iso(value: datetime | None) -> str:
        if value is None:
            return ""
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()

    def list_pipeline_runs(self) -> list[dict[str, Any]]:
        with self.session() as session:
            rows = session.scalars(
                select(PipelineRun).order_by(desc(PipelineRun.finished_at), desc(PipelineRun.started_at))
            ).all()
            return [
                {
                    "run_id": row.run_id,
                    "started_at": self._dt_iso(row.started_at),
                    "finished_at": self._dt_iso(row.finished_at),
                    "pair": row.pair,
                    "timeframe": row.timeframe,
                    "status": row.status,
                    "consensus_action": row.consensus_action or "",
                    "consensus_confidence": row.consensus_confidence,
                    "threshold_passed": row.threshold_passed,
                    "consensus_reason": row.consensus_reason or "",
                    "risk_approved": row.risk_approved,
                    "risk_reason": row.risk_reason or "",
                    "position_pct": row.position_pct,
                    "stop_loss_pct": row.stop_loss_pct,
                    "take_profit_pct": row.take_profit_pct,
                    "execution_status": row.execution_status or "",
                    "execution_reason": row.execution_reason or "",
                    "raw_payload_json": row.raw_payload_json,
                    "warnings_count": row.warnings_count,
                    "errors_count": row.errors_count,
                }
                for row in rows
            ]

    def list_agent_outputs(self) -> list[dict[str, Any]]:
        with self.session() as session:
            rows = session.scalars(
                select(AgentOutput).order_by(desc(AgentOutput.run_id), AgentOutput.agent_id)
            ).all()
            return [
                {
                    "run_id": row.run_id,
                    "agent_id": row.agent_id,
                    "action": row.action,
                    "confidence": row.confidence,
                    "reasoning": row.reasoning,
                    "risk_notes": row.risk_notes,
                    "provider_used": row.provider_used or "",
                    "error_code": row.error_code or "",
                    "error_message": row.error_message or "",
                    "raw_agent_json": row.raw_agent_json or "",
                    "created_at": self._dt_iso(row.created_at),
                }
                for row in rows
            ]

    def list_indicators(self) -> list[dict[str, Any]]:
        with self.session() as session:
            rows = session.scalars(select(IndicatorSnapshot).order_by(desc(IndicatorSnapshot.run_id))).all()
            return [
                {
                    "run_id": row.run_id,
                    "rsi_14": row.rsi_14,
                    "ema_21": row.ema_21,
                    "ema_50": row.ema_50,
                    "ema_200": row.ema_200,
                    "atr_14": row.atr_14,
                    "macd_value": row.macd_value,
                    "macd_signal": row.macd_signal,
                    "macd_hist": row.macd_hist,
                    "bb_middle": row.bb_middle,
                    "bb_upper": row.bb_upper,
                    "bb_lower": row.bb_lower,
                    "raw_indicators_json": row.raw_indicators_json or "",
                    "created_at": self._dt_iso(row.created_at),
                }
                for row in rows
            ]

    def list_trades_for_dashboard(self) -> list[dict[str, Any]]:
        with self.session() as session:
            rows = session.scalars(select(Trade).order_by(desc(Trade.created_at), desc(Trade.id))).all()
            trades: list[dict[str, Any]] = []
            for row in rows:
                side = "LONG" if row.action.upper() == "BUY" else "SHORT" if row.action.upper() == "SELL" else row.action
                trades.append(
                    {
                        "trade_id": row.id,
                        "run_id": row.run_id,
                        "timestamp": self._dt_iso(row.created_at),
                        "side": side,
                        "order_type": "MARKET",
                        "entry_price": row.entry_price,
                        "size": row.quantity,
                        "leverage": None,
                        "stop_loss": None,
                        "take_profit": None,
                        "status": row.status,
                        "close_price": None,
                        "pnl_abs": None,
                        "pnl_pct": None,
                        "rationale_summary": row.reason[:200] if row.reason else "",
                        "rationale_details": row.reason,
                    }
                )
            return trades

    def list_notifications(self) -> list[dict[str, Any]]:
        with self.session() as session:
            rows = session.scalars(select(Notification).order_by(desc(Notification.created_at), desc(Notification.id))).all()
            return [
                {
                    "id": row.id,
                    "run_id": row.run_id,
                    "type": row.notification_type,
                    "sent_ok": row.sent_ok,
                    "error_message": row.error_message or "",
                    "created_at": self._dt_iso(row.created_at),
                }
                for row in rows
            ]
            if existing:
                existing.action = action
                existing.confidence = confidence
                existing.pnl = pnl
                existing.outcome = outcome
                return

            session.add(
                AgentPerformance(
                    run_id=run_id,
                    agent_id=agent_id,
                    action=action,
                    confidence=confidence,
                    pnl=pnl,
                    outcome=outcome,
                )
            )
