"""LLM client abstraction with a deterministic mock provider for MVP."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from src.agents.base_agent import ModelConfig, VALID_ACTIONS
from src.utils.settings import AppSettings, get_settings


@dataclass(slots=True)
class LLMCompletion:
    action: str
    confidence: float
    reasoning: str
    risk_notes: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "risk_notes": self.risk_notes,
        }


class MockLLMProvider:
    """Deterministic provider used for tests and offline development."""

    def complete(self, agent_id: str, _prompt: str, context: dict[str, Any]) -> LLMCompletion:
        if "technical" in agent_id:
            return self._technical(context)
        if "sentiment" in agent_id:
            return self._sentiment(context)
        return LLMCompletion(
            action="HOLD",
            confidence=0.50,
            reasoning="Unknown agent type, defaulting to HOLD.",
            risk_notes="Fallback behavior.",
        )

    def _technical(self, context: dict[str, Any]) -> LLMCompletion:
        indicators = context.get("indicators", {})
        ema_21 = indicators.get("ema_21")
        ema_50 = indicators.get("ema_50")
        rsi_14 = indicators.get("rsi_14")

        if ema_21 is None or ema_50 is None or rsi_14 is None:
            return LLMCompletion(
                action="HOLD",
                confidence=0.45,
                reasoning="Missing technical inputs.",
                risk_notes="Indicator completeness required.",
            )

        if ema_21 > ema_50 and rsi_14 < 70:
            confidence = min(0.90, 0.55 + ((70 - rsi_14) / 100.0))
            return LLMCompletion(
                action="BUY",
                confidence=round(confidence, 4),
                reasoning="Trend is constructive and RSI is below overbought.",
                risk_notes="Watch volatility around resistance.",
            )

        if ema_21 < ema_50 and rsi_14 > 30:
            confidence = min(0.90, 0.55 + ((rsi_14 - 30) / 100.0))
            return LLMCompletion(
                action="SELL",
                confidence=round(confidence, 4),
                reasoning="Downtrend bias with weakening momentum.",
                risk_notes="Short signals are sensitive to sharp reversals.",
            )

        return LLMCompletion(
            action="HOLD",
            confidence=0.50,
            reasoning="Mixed technical conditions.",
            risk_notes="Await clearer setup.",
        )

    def _sentiment(self, context: dict[str, Any]) -> LLMCompletion:
        sentiment = context.get("sentiment", {})
        score = float(sentiment.get("score", 0.0))

        if score > 0.2:
            return LLMCompletion(
                action="BUY",
                confidence=round(min(0.90, 0.50 + (score / 2.0)), 4),
                reasoning="Positive sentiment tilt.",
                risk_notes="Sentiment can invert quickly on headlines.",
            )

        if score < -0.2:
            return LLMCompletion(
                action="SELL",
                confidence=round(min(0.90, 0.50 + (abs(score) / 2.0)), 4),
                reasoning="Negative sentiment pressure.",
                risk_notes="Bearish signals may squeeze on short covering.",
            )

        return LLMCompletion(
            action="HOLD",
            confidence=0.50,
            reasoning="Neutral sentiment profile.",
            risk_notes="No directional edge from sentiment.",
        )


class MultiProviderLLMClient:
    """Selects provider, defaulting to mock for MVP fail-safe operation."""

    def __init__(self, settings: AppSettings | None = None) -> None:
        self.mock = MockLLMProvider()
        self.settings = settings or get_settings()
        self.logger = logging.getLogger("llm_client")

    def _resolve_provider(self, model_cfg: ModelConfig) -> tuple[str, str]:
        env_provider = self.settings.effective_llm_provider(fallback="").lower()
        provider = model_cfg.provider.lower().strip()
        if env_provider:
            provider = env_provider
        if not provider:
            provider = "mock"

        model_name = model_cfg.model_name.strip()
        if provider != "mock" and self.settings.effective_llm_model():
            model_name = self.settings.effective_llm_model()
        return provider, model_name

    def _provider_available(self, provider: str) -> bool:
        return bool(self.settings.resolve_llm_api_key(provider))

    @staticmethod
    def _disabled_completion(
        reason: str,
        *,
        error_code: str,
        error_message: str,
        provider_used: str = "disabled",
    ) -> dict[str, Any]:
        return {
            "action": "HOLD",
            "confidence": 0.50,
            "reasoning": reason,
            "risk_notes": "LLM disabled; using neutral HOLD fallback.",
            "provider_used": provider_used,
            "error_code": error_code,
            "error_message": error_message,
        }

    @staticmethod
    def _extract_json_object(content: str) -> dict[str, Any]:
        if not content.strip():
            return {}

        try:
            payload = json.loads(content)
            return payload if isinstance(payload, dict) else {}
        except json.JSONDecodeError:
            pass

        start = content.find("{")
        end = content.rfind("}")
        if start == -1 or end <= start:
            return {}

        try:
            payload = json.loads(content[start : end + 1])
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _openai_complete(self, model_name: str, prompt: str, model_cfg: ModelConfig) -> dict[str, Any]:
        api_key = self.settings.resolve_llm_api_key("openai")
        if not api_key:
            raise RuntimeError("missing_openai_api_key")

        payload = {
            "model": model_name,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a trading analysis assistant. "
                        "Respond with JSON only using keys: action, confidence, reasoning, risk_notes."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": model_cfg.temp,
            "max_tokens": model_cfg.max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        with httpx.Client(timeout=20) as client:
            response = client.post("https://api.openai.com/v1/chat/completions", json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()

        choices = data.get("choices", [])
        if not isinstance(choices, list) or not choices:
            raise RuntimeError("openai_missing_choices")
        first_choice = choices[0] if isinstance(choices[0], dict) else {}
        message = first_choice.get("message", {}) if isinstance(first_choice, dict) else {}
        content = message.get("content", "") if isinstance(message, dict) else ""
        if not isinstance(content, str):
            content = ""

        parsed = self._extract_json_object(content)
        action = str(parsed.get("action", "HOLD")).upper()
        if action not in VALID_ACTIONS:
            action = "HOLD"

        raw_confidence = parsed.get("confidence", 0.5)
        try:
            confidence = float(raw_confidence)
        except (TypeError, ValueError):
            confidence = 0.5
        confidence = max(0.0, min(1.0, confidence))

        reasoning = str(parsed.get("reasoning") or content.strip() or "No reasoning provided.")
        risk_notes = str(parsed.get("risk_notes", ""))
        return {
            "action": action,
            "confidence": confidence,
            "reasoning": reasoning,
            "risk_notes": risk_notes,
            "provider_used": "openai",
            "error_code": None,
            "error_message": None,
        }

    def complete(
        self,
        agent_id: str,
        model_cfg: ModelConfig,
        prompt: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        provider, model_name = self._resolve_provider(model_cfg)

        if provider != "mock" and not self._provider_available(provider):
            self.logger.warning(
                "LLM disabled for agent '%s': missing credentials for provider '%s'; returning HOLD fallback",
                agent_id,
                provider,
            )
            return self._disabled_completion(
                f"llm_disabled_missing_api_key:{provider}",
                error_code="llm_disabled_missing_api_key",
                error_message=f"missing credentials for provider '{provider}'",
                provider_used="disabled",
            )

        if provider == "openai" and self._provider_available(provider):
            try:
                return self._openai_complete(model_name=model_name, prompt=prompt, model_cfg=model_cfg)
            except Exception as exc:
                self.logger.warning(
                    "OpenAI completion failed for agent '%s'; returning HOLD fallback: %s",
                    agent_id,
                    exc,
                )
                return self._disabled_completion(
                    "llm_request_failed:openai",
                    error_code="llm_request_failed",
                    error_message=str(exc),
                    provider_used="fallback",
                )

        if provider == "mock":
            payload = self.mock.complete(agent_id=agent_id, _prompt=prompt, context=context).as_dict()
            payload["provider_used"] = "mock"
            payload["error_code"] = None
            payload["error_message"] = None
            return payload

        self.logger.warning("provider '%s' is not implemented; returning HOLD fallback", provider)
        return self._disabled_completion(
            f"llm_provider_not_supported:{provider}",
            error_code="llm_provider_not_supported",
            error_message=f"provider '{provider}' not implemented",
            provider_used="disabled",
        )
