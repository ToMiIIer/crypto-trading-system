"""Shared agent config and result primitives."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Action = Literal["BUY", "SELL", "HOLD"]
VALID_ACTIONS: set[str] = {"BUY", "SELL", "HOLD"}


@dataclass(slots=True)
class ModelConfig:
    provider: str
    model_name: str
    temp: float
    max_tokens: int


@dataclass(slots=True)
class AgentConfig:
    agent_id: str
    enabled: bool
    model: ModelConfig
    system_prompt: str
    required_data: list[str]
    indicators: list[str]
    output_schema: dict[str, Any]

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "AgentConfig":
        model = payload.get("model", {})
        return cls(
            agent_id=str(payload["agent_id"]),
            enabled=bool(payload.get("enabled", True)),
            model=ModelConfig(
                provider=str(model.get("provider", "mock")),
                model_name=str(model.get("model_name", "mock-default")),
                temp=float(model.get("temp", 0.0)),
                max_tokens=int(model.get("max_tokens", 512)),
            ),
            system_prompt=str(payload.get("system_prompt", "")),
            required_data=[str(item) for item in payload.get("required_data", [])],
            indicators=[str(item) for item in payload.get("indicators", [])],
            output_schema=dict(payload.get("output_schema", {})),
        )


@dataclass(slots=True)
class AgentResult:
    run_id: str
    pair: str
    timeframe: str
    agent_id: str
    action: Action
    confidence: float
    reasoning: str
    risk_notes: str
    provider_used: str = ""
    error_code: str | None = None
    error_message: str | None = None
    raw_agent_json: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "pair": self.pair,
            "timeframe": self.timeframe,
            "agent_id": self.agent_id,
            "action": self.action,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "risk_notes": self.risk_notes,
            "provider_used": self.provider_used,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "raw_agent_json": self.raw_agent_json,
        }
