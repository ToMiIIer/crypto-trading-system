"""Telegram notifications with auto chat ID discovery and local caching."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from src.utils.settings import get_settings

_API_TIMEOUT_SECONDS = 10
_CHAT_ID_CACHE = Path(__file__).resolve().parent.parent / ".telegram_chat_id"
_EVENT_DEDUP_TTL_SECONDS = 6 * 60 * 60
_RECENT_EVENT_KEYS: dict[str, float] = {}


def _flag_enabled(name: str, default: bool = True) -> bool:
    settings = get_settings()
    values = {
        "TELEGRAM_NOTIFY_PIPELINE": settings.telegram_notify_pipeline,
        "TELEGRAM_NOTIFY_PIPELINE_FINISH": settings.telegram_notify_pipeline_finish,
        "TELEGRAM_NOTIFY_TRADES": settings.telegram_notify_trades,
        "TELEGRAM_NOTIFY_INCLUDE_RUN_STATS": settings.telegram_notify_include_run_stats,
    }

    if name in {"TELEGRAM_NOTIFY_PIPELINE_FINISH", "TELEGRAM_NOTIFY_TRADES"}:
        return bool(values.get(name, default)) and bool(values.get("TELEGRAM_NOTIFY_PIPELINE", True))
    return bool(values.get(name, default))


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _format_event_timestamp(value: Any) -> str:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return _utc_now_iso()


def _format_number(value: Any) -> str:
    if isinstance(value, (int, float)):
        return f"{float(value):.8f}".rstrip("0").rstrip(".")
    return str(value)


def _purge_old_event_keys(now: float) -> None:
    stale = [key for key, seen_at in _RECENT_EVENT_KEYS.items() if now - seen_at > _EVENT_DEDUP_TTL_SECONDS]
    for key in stale:
        _RECENT_EVENT_KEYS.pop(key, None)


def _event_dedup_key(event: dict[str, Any]) -> str:
    order_id = event.get("order_id") or event.get("trade_id")
    if order_id:
        return f"id:{order_id}"

    timestamp_value = event.get("timestamp")
    if isinstance(timestamp_value, (int, float)):
        minute_bucket = int(float(timestamp_value) // 60)
    else:
        minute_bucket = int(time.time() // 60)

    stable = "|".join(
        str(item)
        for item in (
            event.get("event_type"),
            event.get("symbol"),
            event.get("side"),
            event.get("qty"),
            event.get("price") or event.get("limit_price") or event.get("trigger_price"),
            minute_bucket,
        )
    )
    return f"hash:{hashlib.sha256(stable.encode('utf-8')).hexdigest()[:24]}"


def _should_skip_duplicate_event(event: dict[str, Any]) -> bool:
    now = time.time()
    _purge_old_event_keys(now)
    key = _event_dedup_key(event)
    if key in _RECENT_EVENT_KEYS:
        return True
    _RECENT_EVENT_KEYS[key] = now
    return False


def _read_token() -> str:
    token = get_settings().telegram_bot_token.strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing. Set it in your environment or .env file.")
    return token


def _telegram_request(token: str, method: str, payload: dict[str, object] | None = None) -> dict[str, object]:
    url = f"https://api.telegram.org/bot{token}/{method}"
    httpx_logger = logging.getLogger("httpx")
    previous_level = httpx_logger.level
    httpx_logger.setLevel(max(previous_level, logging.WARNING))
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
    finally:
        httpx_logger.setLevel(previous_level)

    if not isinstance(payload_obj, dict):
        raise RuntimeError(f"Telegram API returned an unexpected response for {method}.")

    if not payload_obj.get("ok", False):
        description = str(payload_obj.get("description", "unknown error"))
        raise RuntimeError(f"Telegram API error for {method}: {description}")

    return payload_obj


def _load_chat_id_from_env() -> int | None:
    raw = get_settings().telegram_chat_id.strip()
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


def notify_pipeline_finished(run_summary: dict[str, Any] | None = None) -> None:
    if not _flag_enabled("TELEGRAM_NOTIFY_PIPELINE_FINISH", default=True):
        return

    message_lines = ["✅ Pipeline finished"]
    summary = run_summary or {}

    if _flag_enabled("TELEGRAM_NOTIFY_INCLUDE_RUN_STATS", default=True):
        message_lines.append(f"time: {_utc_now_iso()}")
        run_id = summary.get("run_id")
        if run_id:
            message_lines.append(f"run_id: {run_id}")
        pair = summary.get("pair")
        timeframe = summary.get("timeframe")
        if pair and timeframe:
            message_lines.append(f"pair: {pair} timeframe: {timeframe}")
        status = summary.get("status")
        if status:
            message_lines.append(f"status: {status}")
        duration = summary.get("duration_seconds")
        if duration is not None:
            message_lines.append(f"duration_s: {duration}")

    send_message("\n".join(message_lines))


def notify_trade_event(event: dict[str, Any]) -> None:
    if not _flag_enabled("TELEGRAM_NOTIFY_TRADES", default=True):
        return
    if _should_skip_duplicate_event(event):
        return

    event_type = str(event.get("event_type", "TRADE")).upper()
    title_map = {
        "BUY": "📈 TRADE: BUY",
        "LIMIT_BUY_LONG": "📈 TRADE: LIMIT BUY (LONG)",
        "LIMIT_BUY_SHORT": "📈 TRADE: LIMIT BUY (SHORT)",
        "STOP_LOSS": "🛑 STOP-LOSS",
        "TAKE_PROFIT": "🎯 TAKE PROFIT",
    }
    title = title_map.get(event_type, f"📈 TRADE: {event_type}")

    timestamp = _format_event_timestamp(event.get("timestamp"))
    symbol = event.get("symbol", "N/A")
    side = event.get("side", "N/A")
    qty = _format_number(event.get("qty", "N/A"))
    price = event.get("price")
    if price is None:
        price = event.get("limit_price")
    if price is None:
        price = event.get("trigger_price")
    order_ref = event.get("order_id") or event.get("trade_id") or "N/A"
    run_id = event.get("run_id")

    lines = [
        title,
        f"time: {timestamp}",
        f"symbol: {symbol}",
        f"side: {side}",
        f"qty: {qty}",
        f"price: {_format_number(price) if price is not None else 'N/A'}",
        f"order_ref: {order_ref}",
    ]
    if run_id:
        lines.append(f"run_id: {run_id}")

    send_message("\n".join(lines))
