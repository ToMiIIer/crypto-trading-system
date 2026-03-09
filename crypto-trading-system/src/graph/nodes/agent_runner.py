"""Graph node that executes configured agents and returns hypotheses."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
import logging
from typing import Any

from src.agents.base_agent import AgentConfig, AgentResult, VALID_ACTIONS
from src.agents.llm_client import MockLLMProvider, MultiProviderLLMClient
from src.agents.prompt_builder import build_agent_prompt
from src.data.sentiment_fetcher import SentimentFetcher


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

            prompt = build_agent_prompt(agent_cfg, context)
            try:
                if agent_cfg.uses_llm:
                    llm_calls_executed += 1
                    response = self.llm_client.complete(
                        agent_id=agent_cfg.agent_id,
                        model_cfg=agent_cfg.model,
                        prompt=prompt,
                        context=context,
                    )
                else:
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
