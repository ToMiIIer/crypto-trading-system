"""YAML config loading utilities for artifact files."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional in constrained environments
    def load_dotenv(*_args: Any, **_kwargs: Any) -> bool:
        return False


class ConfigLoader:
    def __init__(self, config_dir: str | Path = "config") -> None:
        self.config_dir = Path(config_dir)

    def load_env(self, env_file: str | Path = ".env") -> None:
        load_dotenv(Path(env_file))

    def load_yaml(self, relative_path: str) -> dict[str, Any]:
        file_path = self.config_dir / relative_path
        if not file_path.exists():
            raise FileNotFoundError(f"missing config file: {file_path}")

        with file_path.open("r", encoding="utf-8") as handle:
            payload = yaml.safe_load(handle) or {}

        if not isinstance(payload, dict):
            raise ValueError(f"invalid config shape in {file_path}")
        return payload

    def load_agent_configs(self) -> list[dict[str, Any]]:
        agents_dir = self.config_dir / "agents"
        if not agents_dir.exists():
            raise FileNotFoundError(f"missing agents directory: {agents_dir}")

        configs: list[dict[str, Any]] = []
        for file_path in sorted(agents_dir.glob("*.yaml")):
            with file_path.open("r", encoding="utf-8") as handle:
                payload = yaml.safe_load(handle) or {}
            if not isinstance(payload, dict):
                raise ValueError(f"invalid agent config shape in {file_path}")
            configs.append(payload)
        return configs

    def load_all(self) -> dict[str, Any]:
        return {
            "pairs": self.load_yaml("pairs.yaml"),
            "data_sources": self.load_yaml("data_sources.yaml"),
            "consensus": self.load_yaml("consensus_config.yaml"),
            "risk": self.load_yaml("risk_params.yaml"),
            "ta": self.load_yaml("ta/deterministic_ta.yaml"),
            "alerts": self.load_yaml("alerts_config.yaml"),
            "scheduling": self.load_yaml("scheduling.yaml"),
            "agents": self.load_agent_configs(),
        }
