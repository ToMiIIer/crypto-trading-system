"""Historical candle downloader with deterministic CSV cache updates."""

from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from src.utils.settings import PROJECT_ROOT

BINANCE_BASE_URL = "https://api.binance.com"
KLINES_ENDPOINT = "/api/v3/klines"
DEFAULT_LIMIT = 1000
CSV_HEADERS = ["open_time", "open", "high", "low", "close", "volume", "close_time"]
INTERVAL_MS = {
    "1h": 60 * 60 * 1000,
    "4h": 4 * 60 * 60 * 1000,
    "1d": 24 * 60 * 60 * 1000,
}


class CandleDownloadError(RuntimeError):
    """Raised when public historical candle download fails."""


def default_history_csv_path(symbol: str, interval: str) -> Path:
    return PROJECT_ROOT / "data" / "history" / f"{symbol.upper()}_{interval}.csv"


def parse_date_to_utc_ms(value: str, *, end_of_day: bool = False) -> int:
    base = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    if end_of_day:
        base = base + timedelta(days=1)
    return int(base.timestamp() * 1000)


def resolve_period_bounds(
    *,
    years: int,
    start: str | None,
    end: str | None,
) -> tuple[int, int]:
    end_ms = parse_date_to_utc_ms(end, end_of_day=True) if end else int(datetime.now(timezone.utc).timestamp() * 1000)
    if start:
        start_ms = parse_date_to_utc_ms(start)
    else:
        start_ms = end_ms - (max(years, 1) * 365 * 24 * 60 * 60 * 1000)
    if start_ms >= end_ms:
        raise CandleDownloadError("start must be earlier than end")
    return start_ms, end_ms


def _kline_to_row(item: list[Any]) -> dict[str, Any]:
    return {
        "open_time": int(item[0]),
        "open": float(item[1]),
        "high": float(item[2]),
        "low": float(item[3]),
        "close": float(item[4]),
        "volume": float(item[5]),
        "close_time": int(item[6]),
    }


def _load_existing_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows: list[dict[str, Any]] = []
        for row in reader:
            rows.append(
                {
                    "open_time": int(row["open_time"]),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row["volume"]),
                    "close_time": int(row["close_time"]),
                }
            )
        return rows


def _write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_HEADERS)
        writer.writeheader()
        for row in rows:
            writer.writerow({header: row[header] for header in CSV_HEADERS})


def download_candles(
    *,
    symbol: str,
    interval: str,
    years: int = 6,
    start: str | None = None,
    end: str | None = None,
    out: str | None = None,
    base_url: str = BINANCE_BASE_URL,
) -> dict[str, Any]:
    interval_ms = INTERVAL_MS.get(interval)
    if interval_ms is None:
        raise CandleDownloadError(f"unsupported interval: {interval}")

    start_ms, end_ms = resolve_period_bounds(years=years, start=start, end=end)
    output_path = Path(out).expanduser().resolve() if out else default_history_csv_path(symbol, interval).resolve()

    existing_rows = _load_existing_rows(output_path)
    existing_map = {int(row["open_time"]): row for row in existing_rows}

    downloaded_rows: list[dict[str, Any]] = []
    cursor = start_ms
    with httpx.Client(base_url=base_url.rstrip("/"), timeout=20.0) as client:
        while cursor < end_ms:
            response = client.get(
                KLINES_ENDPOINT,
                params={
                    "symbol": symbol.upper(),
                    "interval": interval,
                    "limit": DEFAULT_LIMIT,
                    "startTime": cursor,
                    "endTime": end_ms,
                },
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list) or not payload:
                break

            batch = [_kline_to_row(item) for item in payload if isinstance(item, list) and len(item) >= 7]
            if not batch:
                break
            downloaded_rows.extend(batch)

            next_cursor = int(batch[-1]["open_time"]) + interval_ms
            if next_cursor <= cursor:
                break
            cursor = next_cursor

            if len(batch) < DEFAULT_LIMIT:
                break

    merged = dict(existing_map)
    for row in downloaded_rows:
        merged[int(row["open_time"])] = row

    sorted_rows = sorted(merged.values(), key=lambda item: int(item["open_time"]))
    _write_rows(output_path, sorted_rows)

    return {
        "symbol": symbol.upper(),
        "interval": interval,
        "path": str(output_path),
        "rows_written": len(sorted_rows),
        "rows_downloaded": len(downloaded_rows),
        "rows_appended": max(0, len(sorted_rows) - len(existing_rows)),
        "start": datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).isoformat(),
        "end": datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc).isoformat(),
    }
