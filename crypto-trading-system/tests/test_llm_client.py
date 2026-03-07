from __future__ import annotations

import unittest

from src.agents.base_agent import ModelConfig
from src.agents.llm_client import MultiProviderLLMClient


class FakeSettings:
    def __init__(
        self,
        *,
        llm_provider: str = "auto",
        llm_api_key: str = "",
        llm_model: str = "",
        openai_api_key: str = "",
    ) -> None:
        self.llm_provider = llm_provider
        self.llm_api_key = llm_api_key
        self.llm_model = llm_model
        self.openai_api_key = openai_api_key

    def effective_llm_provider(self, fallback: str = "mock") -> str:
        provider = self.llm_provider.strip().lower()
        if provider and provider != "auto":
            return provider
        if self.openai_api_key.strip():
            return "openai"
        return fallback

    def effective_llm_model(self, fallback: str = "") -> str:
        model = self.llm_model.strip()
        return model or fallback

    def resolve_llm_api_key(self, provider: str | None = None) -> str:
        if self.llm_api_key.strip():
            return self.llm_api_key.strip()
        target = (provider or self.effective_llm_provider()).strip().lower()
        if target == "openai":
            return self.openai_api_key.strip()
        return ""


class LLMClientTests(unittest.TestCase):
    def test_missing_openai_key_returns_hold_with_clear_reason(self) -> None:
        client = MultiProviderLLMClient(settings=FakeSettings(llm_provider="auto"))
        model_cfg = ModelConfig(provider="openai", model_name="gpt-4o-mini", temp=0.1, max_tokens=200)

        with self.assertLogs("llm_client", level="WARNING") as captured:
            result = client.complete(
                agent_id="llm_analyst",
                model_cfg=model_cfg,
                prompt="test prompt",
                context={},
            )

        self.assertEqual(result["action"], "HOLD")
        self.assertEqual(result["confidence"], 0.50)
        self.assertEqual(result["reasoning"], "llm_disabled_missing_api_key:openai")
        self.assertTrue(any("missing credentials for provider 'openai'" in line for line in captured.output))

    def test_explicit_mock_provider_overrides_llm_model_provider(self) -> None:
        client = MultiProviderLLMClient(settings=FakeSettings(llm_provider="mock"))
        model_cfg = ModelConfig(provider="openai", model_name="gpt-4o-mini", temp=0.1, max_tokens=200)

        result = client.complete(
            agent_id="technical_analyst",
            model_cfg=model_cfg,
            prompt="test prompt",
            context={"indicators": {"ema_21": 2.0, "ema_50": 1.0, "rsi_14": 50.0}},
        )

        self.assertEqual(result["action"], "BUY")
        self.assertGreater(result["confidence"], 0.5)


if __name__ == "__main__":
    unittest.main()
