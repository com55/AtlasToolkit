"""Filesystem paths for bundled resources (www/, etc.)."""

from __future__ import annotations

import sys
from pathlib import Path


def app_root() -> Path:
    """Project / install directory containing ``www/``."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def is_source_run() -> bool:
    """True when launched via Python from the repo (not a packaged executable)."""
    if getattr(sys, "frozen", False):
        return False
    main = sys.modules.get("__main__")
    if main is not None and getattr(main, "__compiled__", None) is not None:
        return False
    return True


def resource_path(relative: str) -> Path:
    return app_root() / relative
