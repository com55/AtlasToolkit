"""Pure offset/rotation math -- port of www/js/core-region-ops.js's
non-Canvas exports (roundHalfEven, roundUpToMultiple, overlayRect). The
raster-touching functions in that JS file (cropAndRotate,
extractRegionFromPage, maskInPlace) are NOT ported here -- they move to
raster/pillow_backend.py in a later stage (see the architecture spec's §2
domain-boundary table)."""
from __future__ import annotations

import math


def round_half_even(x: float) -> int:
    """Python round() parity (banker's rounding): on an exact .5 tie,
    round to the nearest even integer. Mirrors core-region-ops.js's
    roundHalfEven's own floor+diff-comparison algorithm exactly, rather
    than relying on Python's builtin round() being equivalent, so this
    parity-critical function stays auditable line-for-line against the
    JS source."""
    floor = math.floor(x)
    diff = x - floor
    if diff < 0.5:
        return floor
    if diff > 0.5:
        return floor + 1
    return floor if floor % 2 == 0 else floor + 1


def round_up_to_multiple(value: int, multiple: int = 4) -> int:
    """Round value up to the nearest multiple of `multiple` (default 4).
    Used to pad packed/merged atlas page canvases to GPU
    texture-compression block-size alignment. Mirrors
    core-region-ops.js's roundUpToMultiple."""
    if value <= 0:
        return 0
    remainder = value % multiple
    return value if remainder == 0 else value + (multiple - remainder)


def overlay_rect(bounds: list[int], rotate: int) -> list[int]:
    """Post-rotation bounding rectangle for a region actually placed on a
    page. `bounds` is stored pre-rotation as [x, y, w, h]; when a region
    is stored rotated 90/270 in the atlas, its on-page footprint has
    width/height swapped. Mirrors core-region-ops.js's overlayRect, but
    takes bounds/rotate as two plain arguments instead of a
    {bounds, rotate}-shaped object -- this module has no dependency on
    document.py's Region class."""
    x, y, w, h = bounds
    return [x, y, h, w] if rotate in (90, 270) else [x, y, w, h]
