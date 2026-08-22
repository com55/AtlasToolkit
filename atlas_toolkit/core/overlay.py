"""Pre-computed overlay rectangles for modify-mode canvas rendering."""

from __future__ import annotations

from typing import Dict, List, Tuple

from atlas_toolkit.core.document import Region


def overlay_rect(region: Region) -> Tuple[int, int, int, int]:
    """Return (x, y, w, h) for canvas overlay using stored packed dimensions.

    Spine stores original (unrotated) bounds in the atlas file; the page image
    stores pixels with rotation applied. The overlay must match the stored
    packed footprint on the canvas (swap w/h when rotate is 90° or 270°).
    """
    x, y, w, h = region.bounds
    if region.rotate in (90, 270):
        return (x, y, h, w)
    return (x, y, w, h)


def overlay_rects_for_regions(regions: Dict[str, Region]) -> Dict[str, List[int]]:
    return {name: list(overlay_rect(region)) for name, region in regions.items()}


def overlay_rects_from_bounds(
    bounds_by_name: Dict[str, List[int]],
) -> Dict[str, List[int]]:
    """Build overlay rects from ``[x, y, w, h, rotate]`` payloads."""
    out: Dict[str, List[int]] = {}
    for name, values in bounds_by_name.items():
        if len(values) < 5:
            continue
        x, y, w, h, rotate = values[0], values[1], values[2], values[3], values[4]
        if rotate in (90, 270):
            out[name] = [x, y, h, w]
        else:
            out[name] = [x, y, w, h]
    return out
