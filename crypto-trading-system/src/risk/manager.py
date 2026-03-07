"""Engine-side risk manager (hardcoded logic, numeric params from YAML)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.risk.position_sizer import calculate_position_pct


@dataclass(slots=True)
class RiskDecision:
    approved: bool
    action: str
    reason: str
    position_pct: float
    stop_loss_pct: float
    take_profit_pct: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "approved": self.approved,
            "action": self.action,
            "reason": self.reason,
            "position_pct": self.position_pct,
            "stop_loss_pct": self.stop_loss_pct,
            "take_profit_pct": self.take_profit_pct,
        }


class RiskManager:
    """Fail-safe risk gate. Any uncertainty results in NO_TRADE behavior."""

    def __init__(self, risk_params: dict[str, Any]) -> None:
        self.max_position_pct = float(risk_params["max_position_pct"])
        self.daily_loss_limit_pct = float(risk_params["daily_loss_limit_pct"])
        self.max_sl_distance_pct = float(risk_params["max_sl_distance_pct"])
        self.min_conviction = float(risk_params["min_conviction"])
        self.max_open_positions = int(risk_params["max_open_positions"])
        self.max_symbol_exposure_pct = float(risk_params["max_symbol_exposure_pct"])
        self.min_cash_buffer_pct = float(risk_params["min_cash_buffer_pct"])
        self.atr_stop_multiplier = float(risk_params["atr_stop_multiplier"])
        self.risk_per_trade_pct = float(risk_params["risk_per_trade_pct"])
        self.take_profit_multiplier = float(risk_params.get("take_profit_multiplier", 2.0))

    def evaluate(
        self,
        *,
        action: str,
        weighted_confidence: float,
        portfolio_state: dict[str, Any],
        market_state: dict[str, Any],
    ) -> RiskDecision:
        try:
            return self._evaluate_internal(
                action=action,
                weighted_confidence=weighted_confidence,
                portfolio_state=portfolio_state,
                market_state=market_state,
            )
        except Exception as exc:
            return RiskDecision(
                approved=False,
                action="HOLD",
                reason=f"risk_error:{exc}",
                position_pct=0.0,
                stop_loss_pct=0.0,
                take_profit_pct=0.0,
            )

    def _evaluate_internal(
        self,
        *,
        action: str,
        weighted_confidence: float,
        portfolio_state: dict[str, Any],
        market_state: dict[str, Any],
    ) -> RiskDecision:
        if action not in {"BUY", "SELL"}:
            return RiskDecision(False, "HOLD", "consensus_not_actionable", 0.0, 0.0, 0.0)

        if weighted_confidence < self.min_conviction:
            return RiskDecision(False, "HOLD", "below_min_conviction", 0.0, 0.0, 0.0)

        daily_pnl_pct = float(portfolio_state.get("daily_pnl_pct", 0.0))
        if daily_pnl_pct <= (-1 * self.daily_loss_limit_pct):
            return RiskDecision(False, "HOLD", "daily_loss_limit_reached", 0.0, 0.0, 0.0)

        open_positions = int(portfolio_state.get("open_positions", 0))
        if open_positions >= self.max_open_positions:
            return RiskDecision(False, "HOLD", "max_open_positions_reached", 0.0, 0.0, 0.0)

        symbol_exposure = float(portfolio_state.get("symbol_exposure_pct", 0.0))
        if symbol_exposure >= self.max_symbol_exposure_pct:
            return RiskDecision(False, "HOLD", "symbol_exposure_limit_reached", 0.0, 0.0, 0.0)

        cash_buffer_pct = float(portfolio_state.get("cash_buffer_pct", 1.0))
        if action == "BUY" and cash_buffer_pct < self.min_cash_buffer_pct:
            return RiskDecision(False, "HOLD", "cash_buffer_too_low", 0.0, 0.0, 0.0)

        price = float(market_state.get("price", 0.0))
        atr = float(market_state.get("atr", 0.0))
        if price <= 0:
            return RiskDecision(False, "HOLD", "invalid_price", 0.0, 0.0, 0.0)

        atr_stop_distance = (atr * self.atr_stop_multiplier / price) if atr > 0 else 0.0
        stop_loss_pct = self.max_sl_distance_pct
        if atr_stop_distance > 0:
            stop_loss_pct = min(self.max_sl_distance_pct, atr_stop_distance)

        position_pct = calculate_position_pct(
            max_position_pct=self.max_position_pct,
            risk_per_trade_pct=self.risk_per_trade_pct,
            stop_distance_pct=stop_loss_pct,
            max_symbol_exposure_pct=self.max_symbol_exposure_pct,
            current_symbol_exposure_pct=symbol_exposure,
        )
        if position_pct <= 0:
            return RiskDecision(False, "HOLD", "position_size_zero", 0.0, 0.0, 0.0)

        take_profit_pct = max(stop_loss_pct * self.take_profit_multiplier, stop_loss_pct)

        return RiskDecision(
            approved=True,
            action=action,
            reason="approved",
            position_pct=position_pct,
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
        )
