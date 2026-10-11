"""Domain/nest_frontier.py -- the fast silhouette packer for nest +
mesh-aware repacks (docs/superpowers/specs/2026-10-12-fast-mesh-nest-design.md).

DELIBERATE DEVIATION FROM JS: the app's repack-nest.js has only the
first-fit packer, which packing.nest_pack ports exactly and keeps as the
parity reference. This packer instead places each item at the best of a
small set of bounding-box anchors and falls back to the exhaustive
first-fit only when no anchor fits, so its layouts differ from the app's.
No JS-parity case may run through it.

Same package rule as packing.py: nothing here may import
atlas_toolkit.engine.
"""
from __future__ import annotations

import numpy as np

from atlas_toolkit.domain.packing import (
    _SCAN_STEP,
    _find_first_fit,
    _grid,
    _grow_dims,
    _overlaps,
    _rotation_variants,
    _stamp_occupied,
    normalize_gap,
)
from atlas_toolkit.domain.region_ops import round_up_to_multiple

MAX_CANVAS = 10000  # px per side the frontier packer may grow its canvas to
_ANCHOR_BUDGET = 384  # best-ranked anchors exact-tested per rotation variant


class CanvasTooLargeError(ValueError):
    """Packing would need a canvas side over the limit. Raised before the
    oversized grid is allocated."""

    def __init__(self, width: int, height: int, limit: int) -> None:
        super().__init__(f"packing needs a {width}x{height} canvas, over the {limit} px per side limit")
        self.width = width
        self.height = height
        self.limit = limit


def _anchors(placements: list[dict], w: int, h: int, canvas_w: int, canvas_h: int) -> set[tuple[int, int]]:
    """Candidate origins for a w x h box: the canvas corner plus, for every
    placed box, the spots that put the new box flush against one of its
    sides or corners. Out-of-canvas spots are dropped BEFORE snapping to
    the scan grid (snapping first would let a few overhanging spots back
    in, and the research measurements used this order)."""
    raw = {(0, 0)}
    for p in placements:
        x, y, pw, ph = p["x"], p["y"], p["pw"], p["ph"]
        raw.update((
            (x + pw, y), (x, y + ph), (x - w, y), (x, y - h),
            (x + pw - w, y), (x, y + ph - h),
        ))
    return {
        (x // _SCAN_STEP * _SCAN_STEP, y // _SCAN_STEP * _SCAN_STEP)
        for x, y in raw
        if 0 <= x <= canvas_w - w and 0 <= y <= canvas_h - h
    }


def _best_anchor(
    variants: list[dict], occupied: np.ndarray, placements: list[dict],
    canvas_w: int, canvas_h: int, used_w: int, used_h: int, gap: int,
) -> dict | None:
    """The fitting anchor that grows the used extent least. Ties go to the
    squarer extent, then to more contact with occupied cells along the
    box's left and top edges, then top-left first, then lower rotation.
    Each rotation exact-tests only its _ANCHOR_BUDGET best-ranked anchors."""
    best_score = None
    best = None
    for rot in variants:
        w, h = rot["w"], rot["h"]
        ranked = sorted(
            _anchors(placements, w, h, canvas_w, canvas_h),
            key=lambda a: (max(used_w, a[0] + w) * max(used_h, a[1] + h), a[1], a[0]),
        )[:_ANCHOR_BUDGET]
        for x, y in ranked:
            if _overlaps(occupied, gap, rot, x, y):
                continue
            extent_w = max(used_w, x + w)
            extent_h = max(used_h, y + h)
            # gap >= 1 always (normalize_gap), so the column left of the
            # box and the row above it are inside the padded grid.
            contact = int(occupied[y + gap:y + gap + h, x + gap - 1].sum())
            contact += int(occupied[y + gap - 1, x + gap:x + gap + w].sum())
            score = (extent_w * extent_h, max(extent_w, extent_h), -contact, y, x, rot["deg"])
            if best_score is None or score < best_score:
                best_score = score
                best = {"rot": rot, "x": x, "y": y}
    return best


def _grow(
    canvas_w: int, canvas_h: int, occupied: np.ndarray, seed_rot: dict, gap: int, limit: int,
) -> tuple[int, int, np.ndarray]:
    """Grow right or down by seed_rot's size: the squarer choice
    (packing._grow_dims) when it is within the limit, else the other one,
    else raise -- always before the grown grid is allocated."""
    right = (canvas_w + seed_rot["w"], max(canvas_h, seed_rot["h"]))
    down = (max(canvas_w, seed_rot["w"]), canvas_h + seed_rot["h"])
    preferred = _grow_dims(canvas_w, canvas_h, seed_rot)
    other = down if preferred == right else right
    for new_w, new_h in (preferred, other):
        if new_w <= limit and new_h <= limit:
            grown = np.zeros((new_h + 2 * gap, new_w + 2 * gap), dtype=np.uint8)
            grown[:occupied.shape[0], :occupied.shape[1]] = occupied
            return new_w, new_h, grown
    raise CanvasTooLargeError(preferred[0], preferred[1], limit)


def nest_pack_frontier(items: list[dict], gap_distance: int = 4, max_canvas: int | None = None) -> dict:
    """Silhouette-aware packing, faster than packing.nest_pack: items go
    largest footprint first, each to the best bounding-box anchor
    (_best_anchor); when no anchor fits, the exhaustive first-fit search
    finds cavities the anchors cannot reach; when nothing fits at all, the
    canvas grows. Same item and result shapes as nest_pack. The result is
    the used extent rounded up to a multiple of 4, not the grown canvas.

    `max_canvas` (default: MAX_CANVAS, read at call time) caps both sides
    of the packing canvas; going over raises CanvasTooLargeError before
    the oversized grid exists. It does not bound total memory: footprints,
    mesh masks and per-item rotation buffers are sized by the items."""
    limit = MAX_CANVAS if max_canvas is None else max_canvas
    gap = normalize_gap(gap_distance)
    if not items:
        return {"canvas_w": 0, "canvas_h": 0, "placements": []}
    for item in items:
        if item["w"] <= 0 or item["h"] <= 0:
            raise ValueError(
                f'nest_pack_frontier: item "{item["name"]}" has non-positive dimensions '
                f'({item["w"]}x{item["h"]})'
            )
        if max(item["w"], item["h"]) > limit:
            raise CanvasTooLargeError(item["w"], item["h"], limit)

    def order(item: dict) -> tuple:
        cells = int(np.count_nonzero(_grid(item["footprint"], item["w"], item["h"])))
        return (-cells, -max(item["w"], item["h"]), item["name"])

    canvas_w = canvas_h = used_w = used_h = 0
    occupied = np.zeros((0, 0), dtype=np.uint8)
    placements: list[dict] = []
    for item in sorted(items, key=order):
        variants = _rotation_variants(item, gap)
        spot = None
        while spot is None:
            if canvas_w > 0:
                spot = _best_anchor(variants, occupied, placements, canvas_w, canvas_h, used_w, used_h, gap)
                if spot is None:
                    spot = _find_first_fit(variants, occupied, canvas_w, canvas_h, gap)
            if spot is None:
                canvas_w, canvas_h, occupied = _grow(canvas_w, canvas_h, occupied, variants[0], gap, limit)
        rot, x, y = spot["rot"], spot["x"], spot["y"]
        _stamp_occupied(occupied, gap, rot, x, y)
        placements.append({"name": item["name"], "x": x, "y": y, "pw": rot["w"], "ph": rot["h"], "rotate": rot["deg"]})
        used_w = max(used_w, x + rot["w"])
        used_h = max(used_h, y + rot["h"])
    return {
        "canvas_w": round_up_to_multiple(used_w),
        "canvas_h": round_up_to_multiple(used_h),
        "placements": placements,
    }
