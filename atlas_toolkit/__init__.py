"""AtlasToolkit — Spine atlas extract, modify, and repack."""

from __future__ import annotations


_API_NAMES = frozenset({
    "SpineAtlas", "Region", "AtlasError", "RegionNotFound", "DuplicateRegionName",
    "InvalidRegionName", "MissingPageImage", "SkeletonUnavailable",
    "CanvasTooLarge", "Packer", "PackerOptions", "AlphaMode", "MixedAlphaGroup",
    "ConflictingPagePma",
})
__all__ = sorted(_API_NAMES)


def __dir__() -> list[str]:
    # The API names load lazily, so list them here for dir() and completion.
    return sorted(set(globals()) | _API_NAMES)


def __getattr__(name: str):
    if name in _API_NAMES:
        from atlas_toolkit import api
        return getattr(api, name)
    if name == "__version__":
        from atlas_toolkit.update.updater import get_current_version
        return get_current_version()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
