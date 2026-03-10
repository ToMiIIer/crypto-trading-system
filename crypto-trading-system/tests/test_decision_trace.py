from __future__ import annotations

import unittest

from src.agents.base_agent import AgentResult
from src.graph.nodes.consensus import ConsensusDecision
from src.graph.pipeline import format_decision_trace
from src.risk.manager import RiskDecision


class DecisionTraceFormatTests(unittest.TestCase):
    def test_format_decision_trace_includes_agents_consensus_and_risk_fields(self) -> None:
        trace = format_decision_trace(
            run_id="run-123",
            pair="BTC/USDC",
            timeframe="4h",
            execution_mode="paper",
            hypotheses=[
                AgentResult("run-123", "BTC/USDC", "4h", "llm_analyst", "BUY", 0.61234, "r", "n"),
                AgentResult("run-123", "BTC/USDC", "4h", "technical_analyst", "HOLD", 0.5, "r", "n"),
                AgentResult("run-123", "BTC/USDC", "4h", "sentiment_analyst", "HOLD", 0.5, "r", "n"),
            ],
            consensus=ConsensusDecision(
                run_id="run-123",
                pair="BTC/USDC",
                timeframe="4h",
                action="HOLD",
                weighted_confidence=0.44,
                threshold_passed=False,
                scores={"BUY": 0.44, "HOLD": 0.18, "SELL": 0.0},
                weights_used={"llm_analyst": 0.5},
                reasoning="winner=BUY",
            ),
            consensus_threshold=0.55,
            risk_decision=RiskDecision(
                approved=False,
                action="HOLD",
                reason="consensus_not_actionable",
                position_pct=0.0,
                stop_loss_pct=0.0,
                take_profit_pct=0.0,
            ),
        )

        self.assertIn("DECISION_TRACE run_id=run-123 mode=paper pair=BTC/USDC tf=4h", trace)
        self.assertIn("llm_analyst:BUY@0.6123", trace)
        self.assertIn("technical_analyst:HOLD@0.5000", trace)
        self.assertIn("sentiment_analyst:HOLD@0.5000", trace)
        self.assertIn("consensus=HOLD wc=0.4400 thr=0.5500 pass=false", trace)
        self.assertIn("scores={BUY:0.4400,HOLD:0.1800,SELL:0.0000}", trace)
        self.assertIn("risk=approved=false action=HOLD reason=consensus_not_actionable", trace)
        self.assertIn("pos=0.0000 sl=0.0000 tp=0.0000", trace)


if __name__ == "__main__":
    unittest.main()
