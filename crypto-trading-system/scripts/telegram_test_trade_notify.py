"""Send sample Telegram trade notifications for all supported event types."""

from __future__ import annotations

from pathlib import Path
import sys
import time

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.telegram_notifier import notify_trade_event


def _load_dotenv_if_available() -> None:
    try:
        from dotenv import load_dotenv
    except Exception:
        return

    load_dotenv(PROJECT_ROOT / ".env")


def main() -> None:
    _load_dotenv_if_available()
    now = time.time()
    events = [
        {
            "event_type": "BUY",
            "symbol": "BTC/USDC",
            "side": "buy",
            "qty": 0.01,
            "price": 68000.0,
            "trade_id": "test-buy-1",
            "timestamp": now,
        },
        {
            "event_type": "LIMIT_BUY_LONG",
            "symbol": "ETH/USDC",
            "side": "long",
            "qty": 0.2,
            "limit_price": 3500.0,
            "order_id": "test-limit-long-1",
            "timestamp": now + 1,
        },
        {
            "event_type": "LIMIT_BUY_SHORT",
            "symbol": "SOL/USDC",
            "side": "short",
            "qty": 1.0,
            "limit_price": 140.0,
            "order_id": "test-limit-short-1",
            "timestamp": now + 2,
        },
        {
            "event_type": "STOP_LOSS",
            "symbol": "BTC/USDC",
            "side": "long",
            "qty": 0.01,
            "trigger_price": 65000.0,
            "order_id": "test-stop-1",
            "timestamp": now + 3,
        },
        {
            "event_type": "TAKE_PROFIT",
            "symbol": "BTC/USDC",
            "side": "long",
            "qty": 0.01,
            "trigger_price": 72000.0,
            "order_id": "test-tp-1",
            "timestamp": now + 4,
        },
    ]

    for event in events:
        notify_trade_event(event)

    print("Telegram trade notification test messages sent.")


if __name__ == "__main__":
    main()
