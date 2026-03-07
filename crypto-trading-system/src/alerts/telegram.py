"""Telegram alert client for run summaries and failures."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from src.utils.settings import get_settings


@dataclass(slots=True)
class TelegramConfig:
    enabled: bool
    bot_token: str
    chat_id: str
    timeout_seconds: int = 8


class TelegramAlerter:
    def __init__(self, config: TelegramConfig) -> None:
        self.config = config

    @classmethod
    def from_env(cls) -> "TelegramAlerter":
        settings = get_settings()
        return cls(
            TelegramConfig(
                enabled=settings.telegram_enabled,
                bot_token=settings.telegram_bot_token,
                chat_id=settings.telegram_chat_id,
            )
        )

    def send(self, message: str) -> tuple[bool, str | None]:
        if not self.config.enabled:
            return False, "telegram_disabled"

        if not self.config.bot_token or not self.config.chat_id:
            return False, "telegram_missing_credentials"

        url = f"https://api.telegram.org/bot{self.config.bot_token}/sendMessage"
        payload = {"chat_id": self.config.chat_id, "text": message}

        try:
            with httpx.Client(timeout=self.config.timeout_seconds) as client:
                response = client.post(url, json=payload)
                response.raise_for_status()
        except Exception as exc:  # pragma: no cover - network integration
            return False, f"telegram_send_failed:{exc}"

        return True, None
