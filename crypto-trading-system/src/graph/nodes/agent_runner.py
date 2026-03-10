"""Graph node that executes configured agents and returns hypotheses."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
import logging
from typing import Any

from src.agents.base_agent import AgentConfig, AgentResult, VALID_ACTIONS
from src.agents.llm_client import MockLLMProvider, MultiProviderLLMClient
from src.agents.prompt_builder import build_agent_prompt
from src.data.sentiment_fetcher import SentimentFetcher
from src.ta.deterministic_ta import combine_vote_score, compute_indicators, compute_signals


class AgentRunnerError(RuntimeError):
    """Raised for agent execution failures."""


def _serialize_snapshot(snapshot: Any) -> Any:
    """Convert sentiment-like objects into prompt-safe primitives."""
    if hasattr(snapshot, "model_dump"):
        return snapshot.model_dump()
    if is_dataclass(snapshot):
        return asdict(snapshot)
    if hasattr(snapshot, "__dict__"):
        return snapshot.__dict__
    return str(snapshot)


class AgentRunnerNode:
    def __init__(
        self,
        agent_configs: list[dict[str, Any]],
        llm_client: MultiProviderLLMClient | None = None,
        sentiment_fetcher: SentimentFetcher | None = None,
    ) -> None:
        self.agent_configs = [AgentConfig.from_dict(config) for config in agent_configs]
        self.llm_client = llm_client or MultiProviderLLMClient()
        self.deterministic_provider = MockLLMProvider()
        self.sentiment_fetcher = sentiment_fetcher or SentimentFetcher()
        self.logger = logging.getLogger("agent_runner")

    @staticmethod
    def _run_deterministic_technical_agent(context: dict[str, Any]) -> dict[str, Any]:
        ohlcv = context.get("ohlcv")
        ta_config = context.get("ta_config")
        if not isinstance(ohlcv, list) or not ohlcv:
            return {
                "action": "HOLD",
                "confidence": 0.0,
                "reasoning": "technical_ta_missing_ohlcv",
                "risk_notes": "Technical TA skipped: missing OHLCV context.",
                "provider_used": "deterministic_ta",
                "error_code": "technical_ta_missing_ohlcv",
                "error_message": "OHLCV context missing for technical analysis",
            }
        if not isinstance(ta_config, dict):
            return {
                "action": "HOLD",
                "confidence": 0.0,
                "reasoning": "technical_ta_missing_config",
                "risk_notes": "Technical TA skipped: missing TA config.",
                "provider_used": "deterministic_ta",
                "error_code": "technical_ta_missing_config",
                "error_message": "TA config missing for technical analysis",
            }

        indicator_values = compute_indicators(ohlcv, ta_config)
        indicator_signals = compute_signals(indicator_values, ohlcv, ta_config)
        vote_score, confidence, breakdown = combine_vote_score(indicator_signals, ta_config)

        thresholds = dict(breakdown.get("thresholds", {}))
        buy_threshold = float(thresholds.get("buy_threshold", 0.34))
        sell_threshold = float(thresholds.get("sell_threshold", -0.34))
        hold_band = float(thresholds.get("hold_band", 0.15))

        action = "HOLD"
        if vote_score >= buy_threshold:
            action = "BUY"
        elif vote_score <= sell_threshold:
            action = "SELL"
        elif abs(vote_score) <= hold_band:
            action = "HOLD"

        non_zero = [
            f"{name}:{int(payload.get('signal', 0)):+d}"
            for name, payload in indicator_signals.items()
            if int(payload.get("signal", 0)) != 0 and bool(payload.get("enabled", True))
        ]
        contributors = ", ".join(non_zero[:4]) if non_zero else "none"
        atr_values = dict(indicator_values.get("atr", {}))
        atr_pct = float(atr_values.get("atr_pct", 0.0) or 0.0)
        volatility_note = "elevated" if atr_pct >= 0.03 else "contained" if atr_pct <= 0.01 else "moderate"

        return {
            "action": action,
            "confidence": round(confidence, 4),
            "reasoning": f"vote_score={vote_score:.4f} contributors={contributors}",
            "risk_notes": f"atr_pct={atr_pct:.4f} volatility={volatility_note}",
            "provider_used": "deterministic_ta",
            "error_code": None,
            "error_message": None,
            "indicator_values": indicator_values,
            "indicator_signals": {
                name: int(payload.get("signal", 0))
                for name, payload in indicator_signals.items()
            },
            "vote_score": vote_score,
            "thresholds_used": thresholds,
            "enabled_indicators": list(breakdown.get("enabled_indicators", [])),
            "vote_breakdown": breakdown,
        }

    def run(
        self,
        run_id: str,
        pair: str,
        timeframe: str,
        market_context: dict[str, Any],
    ) -> list[AgentResult]:
        results: list[AgentResult] = []
        llm_calls_executed = 0
        for agent_cfg in self.agent_configs:
            if not agent_cfg.enabled:
                continue

            context = dict(market_context)
            if "sentiment" in agent_cfg.required_data and "sentiment" not in context:
                context["sentiment"] = _serialize_snapshot(self.sentiment_fetcher.fetch(pair))

            try:
                if agent_cfg.uses_llm:
                    prompt = build_agent_prompt(agent_cfg, context)
                    llm_calls_executed += 1
                    response = self.llm_client.complete(
                        agent_id=agent_cfg.agent_id,
                        model_cfg=agent_cfg.model,
                        prompt=prompt,
                        context=context,
                    )
                elif agent_cfg.agent_id == "technical_analyst":
                    response = self._run_deterministic_technical_agent(context)
                else:
                    prompt = build_agent_prompt(agent_cfg, context)
                    response = self.deterministic_provider.complete(
                        agent_id=agent_cfg.agent_id,
                        _prompt=prompt,
                        context=context,
                    ).as_dict()
                    response["provider_used"] = "deterministic"
                    response["error_code"] = None
                    response["error_message"] = None
            except Exception as exc:
                raise AgentRunnerError(f"agent {agent_cfg.agent_id} failed: {exc}") from exc

            action = str(response.get("action", "HOLD")).upper()
            if action not in VALID_ACTIONS:
                action = "HOLD"

            raw_confidence = response.get("confidence", 0.0)
            confidence = max(0.0, min(1.0, float(raw_confidence)))

            results.append(
                AgentResult(
                    run_id=run_id,
                    pair=pair,
                    timeframe=timeframe,
                    agent_id=agent_cfg.agent_id,
                    action=action,  # type: ignore[arg-type]
                    confidence=confidence,
                    reasoning=str(response.get("reasoning", "")),
                    risk_notes=str(response.get("risk_notes", "")),
                    provider_used=str(response.get("provider_used", "")),
                    error_code=str(response.get("error_code")) if response.get("error_code") is not None else None,
                    error_message=(
                        str(response.get("error_message")) if response.get("error_message") is not None else None
                    ),
                    raw_agent_json=dict(response),
                )
            )

        if not results:
            raise AgentRunnerError("no enabled agents produced output")

        self.logger.info("run_id=%s llm_calls_executed=%d", run_id, llm_calls_executed)
        return results
