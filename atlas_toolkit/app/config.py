"""Persistent user preferences for AtlasToolkit."""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def get_config_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    d = base / "AtlasToolkit"
    d.mkdir(parents=True, exist_ok=True)
    return d


CONFIG_PATH = get_config_dir() / "config.json"


class AppConfig:
    def __init__(self) -> None:
        self._data: dict[str, Any] = self._load()

    @staticmethod
    def _load() -> dict[str, Any]:
        try:
            if CONFIG_PATH.exists():
                return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value
        try:
            CONFIG_PATH.write_text(json.dumps(self._data), encoding="utf-8")
        except Exception as e:
            log.warning("Failed to save config: %s", e)
