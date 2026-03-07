"""Pipeline state container."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.agents.base_agent import AgentResult
from src.graph.nodes.consensus import ConsensusDecision
from src.risk.manager import RiskDecision


@dataclass(slots=True)
class PipelineState:
    run_id: str
    pair: str
    timeframe: str
    status: str = "NO_TRADE"
    market_data: dict[str, Any] = field(default_factory=dict)
    indicators: dict[str, Any] = field(default_factory=dict)
    hypotheses: list[AgentResult] = field(default_factory=list)
    consensus: ConsensusDecision | None = None
    risk_decision: RiskDecision | None = None
    execution: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "pair": self.pair,
            "timeframe": self.timeframe,
            "status": self.status,
            "market_data": self.market_data,
            "indicators": self.indicators,
            "hypotheses": [item.as_dict() for item in self.hypotheses],
            "consensus": self.consensus.as_dict() if self.consensus else None,
            "risk_decision": self.risk_decision.as_dict() if self.risk_decision else None,
            "execution": self.execution,
            "warnings": self.warnings,
            "errors": self.errors,
        }
