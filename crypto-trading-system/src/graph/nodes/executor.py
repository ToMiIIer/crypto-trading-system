"""Paper execution node. Never sends live orders in Phase 1."""

from __future__ import annotations

import logging
import time
from typing import Any

from src.risk.manager import RiskDecision
from src.storage.repository import StorageRepository


class PaperExecutorNode:
    def __init__(self, repository: StorageRepository, force_trade_notifications: bool = False) -> None:
        self.repository = repository
        self.force_trade_notifications = force_trade_notifications

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
        price = self._extract_price(market_data)
        if price <= 0:
            return {
                "status": "NO_TRADE",
                "reason": "invalid_execution_price",
                "action": "HOLD",
            }

        open_position = self.repository.get_open_position(pair=pair, timeframe=timeframe)
        auto_close = self._close_on_risk_target_hit(
            run_id=run_id,
            pair=pair,
            timeframe=timeframe,
            open_position=open_position,
            price=price,
        )
        if auto_close is not None:
            return auto_close

        if not risk_decision.approved:
            self.repository.refresh_performance(run_id=run_id, pair=pair, timeframe=timeframe)
            return {
                "status": "NO_TRADE",
                "reason": risk_decision.reason,
                "action": "HOLD",
            }

        action = str(risk_decision.action).upper()
        if action == "BUY":
            if open_position is not None:
                self.repository.refresh_performance(run_id=run_id, pair=pair, timeframe=timeframe)
                return {
                    "status": "NO_TRADE",
                    "reason": "long_position_already_open",
                    "action": "HOLD",
                    "position_id": open_position.id,
                }
            return self._open_long_position(
                run_id=run_id,
                pair=pair,
                timeframe=timeframe,
                risk_decision=risk_decision,
                market_data=market_data,
                portfolio_state=portfolio_state,
                price=price,
            )

        if action == "SELL":
            if open_position is None:
                self.repository.refresh_performance(run_id=run_id, pair=pair, timeframe=timeframe)
                return {
                    "status": "NO_TRADE",
                    "reason": "sell_signal_without_open_long",
                    "action": "HOLD",
                }

            return self._close_open_position(
                run_id=run_id,
                pair=pair,
                timeframe=timeframe,
                position_id=open_position.id,
                price=price,
                reason="signal_sell_close_long",
                event_type="SELL",
            )

        self.repository.refresh_performance(run_id=run_id, pair=pair, timeframe=timeframe)
        return {
            "status": "NO_TRADE",
            "reason": "consensus_not_actionable",
            "action": "HOLD",
        }

    @staticmethod
    def _extract_price(market_data: dict[str, Any]) -> float:
        return float(
            market_data.get("ticker_24h", {}).get("last_price")
            or market_data.get("last_price")
            or 0.0
        )

    def _resolve_risk_levels(
        self,
        *,
        risk_decision: RiskDecision,
        market_data: dict[str, Any],
        price: float,
    ) -> tuple[float, float, float, float]:
        indicators = market_data.get("indicators") if isinstance(market_data.get("indicators"), dict) else {}
        risk_params = market_data.get("risk_params") if isinstance(market_data.get("risk_params"), dict) else {}

        atr_value = float(indicators.get("atr_14") or 0.0)
        atr_stop_multiplier = float(risk_params.get("atr_stop_multiplier", 1.5))
        max_sl_distance_pct = float(risk_params.get("max_sl_distance_pct", 0.02))

        stop_loss_pct = float(risk_decision.stop_loss_pct or 0.0)
        if atr_value > 0 and price > 0:
            atr_based_pct = (atr_value * atr_stop_multiplier) / price
            if max_sl_distance_pct > 0:
                stop_loss_pct = min(max_sl_distance_pct, atr_based_pct)
            else:
                stop_loss_pct = atr_based_pct
        elif stop_loss_pct <= 0:
            stop_loss_pct = max_sl_distance_pct if max_sl_distance_pct > 0 else 0.02

        take_profit_pct = float(risk_decision.take_profit_pct or 0.0)
        if take_profit_pct <= 0:
            take_profit_pct = max(stop_loss_pct * 2.0, stop_loss_pct)

        stop_loss_price = max(price * (1.0 - stop_loss_pct), 0.0)
        take_profit_price = price * (1.0 + take_profit_pct)
        return stop_loss_price, take_profit_price, stop_loss_pct, take_profit_pct

    def _open_long_position(
        self,
        *,
        run_id: str,
        pair: str,
        timeframe: str,
        risk_decision: RiskDecision,
        market_data: dict[str, Any],
        portfolio_state: dict[str, Any],
        price: float,
    ) -> dict[str, Any]:
        equity = float(portfolio_state.get("equity", 10000.0))
        notional = equity * float(risk_decision.position_pct)
        quantity = notional / price if price > 0 else 0.0
        if quantity <= 0:
            self.repository.refresh_performance(run_id=run_id, pair=pair, timeframe=timeframe)
            return {
                "status": "NO_TRADE",
                "reason": "position_size_zero",
                "action": "HOLD",
            }

        stop_loss_price, take_profit_price, stop_loss_pct, take_profit_pct = self._resolve_risk_levels(
            risk_decision=risk_decision,
            market_data=market_data,
            price=price,
        )
        position = self.repository.open_position(
            run_id=run_id,
            pair=pair,
            timeframe=timeframe,
            side="LONG",
            entry_price=price,
            size_pct=float(risk_decision.position_pct),
            quantity=quantity,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
            reason=risk_decision.reason,
        )
        self.repository.refresh_performance(run_id=run_id, pair=pair, timeframe=timeframe)
        self._notify_trade_event(
            run_id=run_id,
            event={
                "event_type": "BUY",
                "symbol": pair,
                "side": "long",
                "action": "BUY",
                "qty": quantity,
                "price": price,
                "trade_id": position.id,
                "run_id": run_id,
                "reason": risk_decision.reason,
                "stop_loss": stop_loss_price,
                "take_profit": take_profit_price,
                "stop_loss_pct": stop_loss_pct,
                "take_profit_pct": take_profit_pct,
                "size_pct": float(risk_decision.position_pct),
                "timestamp": time.time(),
            },
        )

        return {
            "status": "SIMULATED_TRADE_OPENED",
            "position_id": position.id,
            "action": "BUY",
            "quantity": quantity,
            "entry_price": price,
            "size_pct": float(risk_decision.position_pct),
            "stop_loss_price": stop_loss_price,
            "take_profit_price": take_profit_price,
            "stop_loss_pct": stop_loss_pct,
            "take_profit_pct": take_profit_pct,
            "reason": risk_decision.reason,
        }

    def _close_on_risk_target_hit(
        self,
        *,
        run_id: str,
        pair: str,
        timeframe: str,
        open_position: Any,
        price: float,
    ) -> dict[str, Any] | None:
        if open_position is None:
            return None

        stop_loss_price = float(open_position.stop_loss_price or 0.0)
        if stop_loss_price > 0 and price <= stop_loss_price:
            return self._close_open_position(
                run_id=run_id,
                pair=pair,
                timeframe=timeframe,
                position_id=int(open_position.id),
                price=price,
                reason="stop_loss_hit",
                event_type="STOP_LOSS",
            )

        take_profit_price = float(open_position.take_profit_price or 0.0)
        if take_profit_price > 0 and price >= take_profit_price:
            return self._close_open_position(
                run_id=run_id,
                pair=pair,
                timeframe=timeframe,
                position_id=int(open_position.id),
                price=price,
                reason="take_profit_hit",
                event_type="TAKE_PROFIT",
            )
        return None

    def _close_open_position(
        self,
        *,
        run_id: str,
        pair: str,
        timeframe: str,
        position_id: int,
        price: float,
        reason: str,
        event_type: str,
    ) -> dict[str, Any]:
        closed = self.repository.close_position(
            run_id=run_id,
            pair=pair,
            timeframe=timeframe,
            position_id=position_id,
            exit_price=price,
            reason=reason,
        )
        self.repository.refresh_performance(run_id=run_id, pair=pair, timeframe=timeframe)
        if closed is None:
            return {
                "status": "NO_TRADE",
                "reason": "no_open_position",
                "action": "HOLD",
            }

        self._notify_trade_event(
            run_id=run_id,
            event={
                "event_type": event_type,
                "symbol": pair,
                "side": "long",
                "action": "SELL",
                "qty": closed.quantity,
                "price": price,
                "trade_id": closed.id,
                "run_id": run_id,
                "reason": reason,
                "stop_loss": closed.stop_loss_price,
                "take_profit": closed.take_profit_price,
                "size_pct": closed.size_pct,
                "entry_price": closed.entry_price,
                "pnl_abs": closed.pnl_abs,
                "pnl_pct": closed.pnl_pct,
                "timestamp": time.time(),
            },
        )
        return {
            "status": "SIMULATED_TRADE_CLOSED",
            "position_id": closed.id,
            "action": "SELL",
            "quantity": closed.quantity,
            "entry_price": closed.entry_price,
            "exit_price": price,
            "pnl_abs": closed.pnl_abs,
            "pnl_pct": closed.pnl_pct,
            "reason": reason,
        }

    def _notify_trade_event(
        self,
        *,
        run_id: str,
        event: dict[str, Any],
    ) -> None:
        sent_ok = False
        error_message: str | None = None
        try:
            from src.telegram_notifier import notify_trade_event

            notify_trade_event(event, force=self.force_trade_notifications)
            sent_ok = True
        except Exception as exc:
            error_message = str(exc)
            logging.getLogger("executor").warning("Telegram trade notification skipped: %s", exc)
        finally:
            try:
                self.repository.record_notification(
                    run_id=run_id,
                    notification_type="TRADE",
                    sent_ok=sent_ok,
                    error_message=error_message,
                )
            except Exception as exc:
                logging.getLogger("executor").warning("Failed to persist trade notification event: %s", exc)
