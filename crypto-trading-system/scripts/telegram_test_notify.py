"""Send a one-shot Telegram notification to verify local bot configuration."""

from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.telegram_notifier import send_message


def _load_dotenv_if_available() -> None:
    try:
        from dotenv import load_dotenv
    except Exception:
        return

    env_path = PROJECT_ROOT / ".env"
    load_dotenv(dotenv_path=env_path)


def main() -> None:
    _load_dotenv_if_available()
    send_message("✅ Telegram notifications connected successfully.")
    print("Telegram test notification sent.")


if __name__ == "__main__":
    main()
