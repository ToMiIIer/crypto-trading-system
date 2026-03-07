"""Dashboard export helpers for CSV and XLSX outputs."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from openpyxl import Workbook

from src.storage.repository import StorageRepository

RUN_HEADERS = [
    "run_id",
    "started_at",
    "finished_at",
    "pair",
    "timeframe",
    "status",
    "consensus_action",
    "consensus_confidence",
    "threshold_passed",
    "consensus_reason",
    "risk_approved",
    "risk_reason",
    "position_pct",
    "stop_loss_pct",
    "take_profit_pct",
    "execution_status",
    "execution_reason",
    "warnings_count",
    "errors_count",
]

AGENT_HEADERS = [
    "run_id",
    "agent_id",
    "action",
    "confidence",
    "reasoning",
    "risk_notes",
    "provider_used",
    "error_code",
    "error_message",
]

INDICATOR_HEADERS = [
    "run_id",
    "rsi_14",
    "ema_21",
    "ema_50",
    "ema_200",
    "atr_14",
    "macd_value",
    "macd_signal",
    "macd_hist",
    "bb_middle",
    "bb_upper",
    "bb_lower",
]

TRADE_HEADERS = [
    "trade_id",
    "run_id",
    "timestamp",
    "side",
    "order_type",
    "entry_price",
    "size",
    "leverage",
    "stop_loss",
    "take_profit",
    "status",
    "close_price",
    "pnl_abs",
    "pnl_pct",
    "rationale_summary",
    "rationale_details",
]

ACTIVE_POSITION_HEADERS = [
    "trade_id",
    "run_id",
    "timestamp",
    "side",
    "entry_price",
    "size",
    "status",
]

ERROR_WARNING_HEADERS = [
    "run_id",
    "agent_id",
    "level",
    "message",
    "code",
    "timestamp",
]


def _excel_safe(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, str):
        # Excel cell limit is 32,767 characters.
        return value[:32767]
    return value


def _write_csv(path: Path, headers: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow({header: row.get(header, "") for header in headers})


def _write_sheet(workbook: Workbook, title: str, headers: list[str], rows: list[dict[str, Any]]) -> None:
    sheet = workbook.create_sheet(title=title)
    sheet.append(headers)
    for row in rows:
        sheet.append([_excel_safe(row.get(header, "")) for header in headers])


def _build_error_warning_rows(
    runs: list[dict[str, Any]],
    agents: list[dict[str, Any]],
    notifications: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for run in runs:
        run_id = str(run.get("run_id", ""))
        payload_json = run.get("raw_payload_json")
        if not isinstance(payload_json, str) or not payload_json:
            continue
        try:
            payload = json.loads(payload_json)
        except json.JSONDecodeError:
            continue
        errors = payload.get("errors") if isinstance(payload, dict) else []
        warnings = payload.get("warnings") if isinstance(payload, dict) else []
        if isinstance(errors, list):
            for message in errors:
                rows.append(
                    {
                        "run_id": run_id,
                        "agent_id": "",
                        "level": "ERROR",
                        "message": str(message),
                        "code": "",
                        "timestamp": str(run.get("finished_at", "")),
                    }
                )
        if isinstance(warnings, list):
            for message in warnings:
                rows.append(
                    {
                        "run_id": run_id,
                        "agent_id": "",
                        "level": "WARNING",
                        "message": str(message),
                        "code": "",
                        "timestamp": str(run.get("finished_at", "")),
                    }
                )

    for agent in agents:
        error_code = str(agent.get("error_code", "") or "")
        error_message = str(agent.get("error_message", "") or "")
        if not error_code and not error_message:
            continue
        rows.append(
            {
                "run_id": str(agent.get("run_id", "")),
                "agent_id": str(agent.get("agent_id", "")),
                "level": "WARNING",
                "message": error_message,
                "code": error_code,
                "timestamp": str(agent.get("created_at", "")),
            }
        )

    for notification in notifications:
        if bool(notification.get("sent_ok", False)):
            continue
        rows.append(
            {
                "run_id": str(notification.get("run_id", "")),
                "agent_id": "",
                "level": "WARNING",
                "message": str(notification.get("error_message", "") or ""),
                "code": f"notification:{notification.get('type', '')}",
                "timestamp": str(notification.get("created_at", "")),
            }
        )

    rows.sort(key=lambda item: (item.get("timestamp", ""), item.get("run_id", "")), reverse=True)
    return rows


class DashboardExporter:
    def __init__(self, repository: StorageRepository, reports_dir: Path) -> None:
        self.repository = repository
        self.reports_dir = reports_dir

    def export(self) -> dict[str, Path]:
        runs = self.repository.list_pipeline_runs()
        agents = self.repository.list_agent_outputs()
        indicators = self.repository.list_indicators()
        trades = self.repository.list_trades_for_dashboard()
        active_positions = [row for row in trades if str(row.get("status", "")).upper() == "OPEN"]
        notifications = self.repository.list_notifications()
        errors_warnings = _build_error_warning_rows(runs, agents, notifications)

        pipeline_runs_csv = self.reports_dir / "pipeline_runs.csv"
        agent_outputs_csv = self.reports_dir / "agent_outputs.csv"
        trades_csv = self.reports_dir / "trades.csv"
        active_positions_csv = self.reports_dir / "active_positions.csv"
        indicators_csv = self.reports_dir / "indicators.csv"

        _write_csv(pipeline_runs_csv, RUN_HEADERS, runs)
        _write_csv(agent_outputs_csv, AGENT_HEADERS, agents)
        _write_csv(trades_csv, TRADE_HEADERS, trades)
        _write_csv(active_positions_csv, ACTIVE_POSITION_HEADERS, active_positions)
        _write_csv(indicators_csv, INDICATOR_HEADERS, indicators)

        workbook = Workbook()
        default_sheet = workbook.active
        workbook.remove(default_sheet)
        _write_sheet(workbook, "Runs", RUN_HEADERS, runs)
        _write_sheet(workbook, "Agents", AGENT_HEADERS, agents)
        _write_sheet(workbook, "Indicators", INDICATOR_HEADERS, indicators)
        _write_sheet(workbook, "Trades", TRADE_HEADERS, trades)
        _write_sheet(workbook, "ActivePositions", ACTIVE_POSITION_HEADERS, active_positions)
        _write_sheet(workbook, "ErrorsWarnings", ERROR_WARNING_HEADERS, errors_warnings)

        xlsx_path = self.reports_dir / "dashboard.xlsx"
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        workbook.save(xlsx_path)

        return {
            "xlsx": xlsx_path,
            "pipeline_runs_csv": pipeline_runs_csv,
            "agent_outputs_csv": agent_outputs_csv,
            "trades_csv": trades_csv,
            "active_positions_csv": active_positions_csv,
            "indicators_csv": indicators_csv,
        }
