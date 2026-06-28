"""Filesystem paths for bundled resources (ui/, etc.)."""

from __future__ import annotations

import sys
from pathlib import Path


def app_root() -> Path:
    """Project / install directory containing ``ui/``."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_path(relative: str) -> Path:
    return app_root() / relative
