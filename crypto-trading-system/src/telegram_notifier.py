"""Telegram notifications with auto chat ID discovery and local caching."""

from __future__ import annotations

import os
from pathlib import Path

import httpx

_API_TIMEOUT_SECONDS = 10
_CHAT_ID_CACHE = Path(__file__).resolve().parent.parent / ".telegram_chat_id"


def _load_dotenv_if_available() -> None:
    try:
        from dotenv import load_dotenv
    except Exception:
        return

    load_dotenv(_CHAT_ID_CACHE.parent / ".env")


_load_dotenv_if_available()


def _read_token() -> str:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing. Set it in your environment or .env file.")
    return token


def _telegram_request(token: str, method: str, payload: dict[str, object] | None = None) -> dict[str, object]:
    url = f"https://api.telegram.org/bot{token}/{method}"
    try:
        with httpx.Client(timeout=_API_TIMEOUT_SECONDS) as client:
            if payload is None:
                response = client.get(url)
            else:
                response = client.post(url, json=payload)
            response.raise_for_status()
            payload_obj = response.json()
    except Exception as exc:
        raise RuntimeError(f"Telegram API request failed for {method}: {exc}") from exc

    if not isinstance(payload_obj, dict):
        raise RuntimeError(f"Telegram API returned an unexpected response for {method}.")

    if not payload_obj.get("ok", False):
        description = str(payload_obj.get("description", "unknown error"))
        raise RuntimeError(f"Telegram API error for {method}: {description}")

    return payload_obj


def _load_chat_id_from_env() -> int | None:
    raw = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError("TELEGRAM_CHAT_ID must be an integer.") from exc


def _load_chat_id_from_cache() -> int | None:
    if not _CHAT_ID_CACHE.exists():
        return None

    raw = _CHAT_ID_CACHE.read_text(encoding="utf-8").strip()
    if not raw:
        return None

    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"Invalid chat ID in cache file: {_CHAT_ID_CACHE}") from exc


def _discover_chat_id(token: str) -> int:
    response = _telegram_request(token, "getUpdates")
    updates = response.get("result", [])
    if not isinstance(updates, list):
        raise RuntimeError("Telegram getUpdates returned an unexpected payload.")

    latest_update_id = -1
    selected_chat_id: int | None = None

    for update in updates:
        if not isinstance(update, dict):
            continue

        message = update.get("message")
        if not isinstance(message, dict):
            continue

        chat = message.get("chat")
        if not isinstance(chat, dict):
            continue

        if chat.get("type") != "private":
            continue

        chat_id = chat.get("id")
        if not isinstance(chat_id, int):
            continue

        update_id = update.get("update_id")
        if not isinstance(update_id, int):
            update_id = latest_update_id + 1

        if update_id >= latest_update_id:
            latest_update_id = update_id
            selected_chat_id = chat_id

    if selected_chat_id is None:
        raise RuntimeError(
            "Could not auto-discover TELEGRAM_CHAT_ID. Open Telegram, send /start to your bot once, then rerun the test."
        )

    _CHAT_ID_CACHE.write_text(f"{selected_chat_id}\n", encoding="utf-8")
    return selected_chat_id


def get_chat_id() -> int:
    chat_id = _load_chat_id_from_env()
    if chat_id is not None:
        return chat_id

    chat_id = _load_chat_id_from_cache()
    if chat_id is not None:
        return chat_id

    token = _read_token()
    return _discover_chat_id(token)


def send_message(text: str) -> None:
    token = _read_token()
    chat_id = get_chat_id()
    _telegram_request(token, "sendMessage", {"chat_id": chat_id, "text": text})


def send_error(context: str, exc: Exception) -> None:
    message = f"❌ {context}: {exc.__class__.__name__}: {exc}"
    send_message(message[:3500])
