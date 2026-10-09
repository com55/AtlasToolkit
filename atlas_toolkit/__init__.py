"""AtlasToolkit — Spine atlas extract, modify, and repack."""

from __future__ import annotations


def __getattr__(name: str):
    if name == "__version__":
        from atlas_toolkit.update.updater import get_current_version
        return get_current_version()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
