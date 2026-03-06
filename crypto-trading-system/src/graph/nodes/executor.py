"""Paper execution node. Never sends live orders in Phase 1."""

from __future__ import annotations

import logging
import time
from typing import Any

from src.risk.manager import RiskDecision
from src.storage.repository import StorageRepository


class PaperExecutorNode:
    def __init__(self, repository: StorageRepository) -> None:
        self.repository = repository

    def run(
        self,
        *,
        run_id: str,
        pair: str,
        timeframe: str,
        risk_decision: RiskDecision,
        market_data: dict[str, Any],
        portfolio_state: dict[str, Any],
    ) -> dict[str, Any]:
        if not risk_decision.approved:
            return {
                "status": "NO_TRADE",
                "reason": risk_decision.reason,
                "action": "HOLD",
            }

        price = float(
            market_data.get("ticker_24h", {}).get("last_price")
            or market_data.get("last_price")
            or 0.0
        )
        if price <= 0:
            return {
                "status": "NO_TRADE",
                "reason": "invalid_execution_price",
                "action": "HOLD",
            }

        equity = float(portfolio_state.get("equity", 10000.0))
        notional = equity * risk_decision.position_pct
        quantity = notional / price

        trade = self.repository.create_simulated_trade(
            run_id=run_id,
            pair=pair,
            timeframe=timeframe,
            action=risk_decision.action,
            quantity=quantity,
            entry_price=price,
            reason=risk_decision.reason,
        )
        self._notify_trade_event(
            run_id=run_id,
            pair=pair,
            trade_id=trade.id,
            action=trade.action,
            quantity=trade.quantity,
            entry_price=trade.entry_price,
            reason=trade.reason,
        )

        return {
            "status": "SIMULATED_TRADE",
            "trade_id": trade.id,
            "action": trade.action,
            "quantity": trade.quantity,
            "entry_price": trade.entry_price,
            "reason": trade.reason,
        }

    @staticmethod
    def _event_type(action: str, reason: str) -> str:
        reason_text = reason.strip().lower()
        if "stop_loss" in reason_text or "stop-loss" in reason_text:
            return "STOP_LOSS"
        if "take_profit" in reason_text or "take-profit" in reason_text:
            return "TAKE_PROFIT"
        if action.upper() == "BUY":
            return "BUY"
        if action.upper() == "SELL":
            return "LIMIT_BUY_SHORT"
        return action.upper()

    def _notify_trade_event(
        self,
        *,
        run_id: str,
        pair: str,
        trade_id: int,
        action: str,
        quantity: float,
        entry_price: float,
        reason: str,
    ) -> None:
        try:
            from src.telegram_notifier import notify_trade_event

            notify_trade_event(
                {
                    "event_type": self._event_type(action, reason),
                    "symbol": pair,
                    "side": action.lower(),
                    "qty": quantity,
                    "price": entry_price,
                    "trade_id": trade_id,
                    "run_id": run_id,
                    "reason": reason,
                    "timestamp": time.time(),
                }
            )
        except Exception as exc:
            logging.getLogger("executor").warning("Telegram trade notification skipped: %s", exc)
