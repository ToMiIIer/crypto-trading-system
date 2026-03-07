"""Runtime settings loaded from environment and local .env file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class AppSettings(BaseSettings):
    """Canonical settings container for credentials and runtime flags."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    env: str = Field(default="dev", validation_alias="ENV")
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")

    telegram_enabled: bool = Field(default=False, validation_alias="TELEGRAM_ENABLED")
    telegram_bot_token: str = Field(default="", validation_alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str = Field(default="", validation_alias="TELEGRAM_CHAT_ID")
    telegram_notify_pipeline: bool = Field(default=True, validation_alias="TELEGRAM_NOTIFY_PIPELINE")
    telegram_notify_pipeline_finish: bool = Field(default=True, validation_alias="TELEGRAM_NOTIFY_PIPELINE_FINISH")
    telegram_notify_trades: bool = Field(default=True, validation_alias="TELEGRAM_NOTIFY_TRADES")
    telegram_notify_include_run_stats: bool = Field(default=True, validation_alias="TELEGRAM_NOTIFY_INCLUDE_RUN_STATS")

    llm_provider: str = Field(default="mock", validation_alias="LLM_PROVIDER")
    llm_model: str = Field(default="", validation_alias="LLM_MODEL")
    llm_api_key: str = Field(default="", validation_alias="LLM_API_KEY")
    openai_api_key: str = Field(default="", validation_alias="OPENAI_API_KEY")
    anthropic_api_key: str = Field(default="", validation_alias="ANTHROPIC_API_KEY")
    google_api_key: str = Field(default="", validation_alias="GOOGLE_API_KEY")

    sentiment_api_key: str = Field(default="", validation_alias="SENTIMENT_API_KEY")
    news_api_key: str = Field(default="", validation_alias="NEWS_API_KEY")

    trading_enabled: bool = Field(default=False, validation_alias="TRADING_ENABLED")
    execution_mode: str = Field(default="disabled", validation_alias="EXECUTION_MODE")

    def effective_llm_provider(self, fallback: str = "mock") -> str:
        provider = self.llm_provider.strip().lower()
        return provider or fallback

    def effective_llm_model(self, fallback: str = "") -> str:
        model = self.llm_model.strip()
        return model or fallback

    def resolve_llm_api_key(self, provider: str | None = None) -> str:
        if self.llm_api_key.strip():
            return self.llm_api_key.strip()

        target = (provider or self.effective_llm_provider()).strip().lower()
        provider_keys = {
            "openai": self.openai_api_key.strip(),
            "anthropic": self.anthropic_api_key.strip(),
            "google": self.google_api_key.strip(),
        }
        return provider_keys.get(target, "")

    def execution_enabled(self) -> bool:
        if self.trading_enabled:
            return True

        mode = self.execution_mode.strip().lower()
        if mode in {"enabled", "paper", "paper_trading", "paper-trading"}:
            return True
        if mode in {"disabled", "off", "none", "analysis", "analysis_only", "analysis-only"}:
            return False
        return False


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    return AppSettings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
