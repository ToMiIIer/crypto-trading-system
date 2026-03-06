# Crypto Trading System (Phase 1 MVP)

Paper-trading multi-agent system with deterministic risk gating and fail-safe `NO_TRADE` behavior.

## Core Principles
- Engine vs Artifact boundary: trading control logic stays in Python; YAML controls parameters/config.
- Any missing data or exception => `NO_TRADE`.
- All decisions are logged and persisted.
- Idempotent side effects via deterministic `run_id` + repository upsert guards.

## Stack
- Python 3.12+
- SQLAlchemy + PostgreSQL
- APScheduler
- httpx
- PyYAML + pydantic-compatible config style

## Project Layout
- `config/`: Artifact YAML files for agents, consensus, risk, alerts, schedule.
- `src/graph/pipeline.py`: end-to-end orchestration.
- `src/risk/manager.py`: hardcoded risk logic (engine-owned).
- `src/graph/nodes/executor.py`: paper-only trade creation.
- `src/storage/models.py`: `trades`, `hypotheses`, `consensus_log`, `portfolio_snapshots`, `agent_performance`.

## Quick Start
1. Copy `.env.example` to `.env` and fill values.
2. Start infra:
```bash
docker compose up -d
```
3. Install dependencies (example):
```bash
python -m pip install -e ".[dev]"
```

## CLI
Run one cycle:
```bash
python -m src.main run-once --pair BTC/USDC --timeframe 4h
```

Run scheduler (reads `config/scheduling.yaml`, default every 4 hours):
```bash
python -m src.main scheduler
```

## Runtime Flow
1. Load configs.
2. Collect Binance OHLCV + 24h ticker.
3. Compute indicators (RSI, MACD, EMA 21/50/200, ATR, Bollinger).
4. Run technical + sentiment agents (MockLLM default).
5. Persist hypotheses.
6. Run weighted consensus and persist.
7. Apply risk manager.
8. Paper executor creates simulated trade only if approved.
9. Persist portfolio snapshot and send Telegram summary.

## Telegram Notifications Setup
1. Create your bot token with BotFather.
2. Open your bot in Telegram and send `/start` once.
3. In VS Code, run launch config: `Telegram: Send test notification`.

Notes:
- The test script is `scripts/telegram_test_notify.py`.
- Trade-event test script is `scripts/telegram_test_trade_notify.py`.
- `TELEGRAM_CHAT_ID` is auto-discovered via Telegram `getUpdates` and cached in `.telegram_chat_id`.
- You can override cached chat ID by setting `TELEGRAM_CHAT_ID` explicitly in your environment.
- Notification flags (defaults are enabled for testing):
  - `TELEGRAM_NOTIFY_PIPELINE_FINISH=1`
  - `TELEGRAM_NOTIFY_TRADES=1`
  - `TELEGRAM_NOTIFY_INCLUDE_RUN_STATS=1`

## Notes
- This MVP does not place real orders.
- Telegram alerts are optional and controlled by env vars.
- Tests are designed to run without network calls.
