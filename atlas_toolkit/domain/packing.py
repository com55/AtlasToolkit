"""domain/packing.py -- pure bin-packing decision math (architecture spec
§1/§2): port of www/js/atlas-modifier.js's _shelfPack and all of
www/js/repack-nest.js's packing geometry, plus footprintForCanonical's
dependency-injected shell.

PACKAGE RULE (spec §1, promoted to package level in rev 6): nothing in
this module may import from atlas_toolkit.engine at module level -- that
is what keeps the real domain/<->engine cycle (packing needs modifier's
identity/offset helpers; modifier needs packing's placement algorithm)
resolved by injection rather than by convention. The same shape
repack-nest.js already uses, and for the same reason. Enforced by
tests/python/test_packing_import_boundary.py, not by review attention.

Placements are plain dicts, matching the JS objects one-for-one: the
shelf packer emits a `rotated` boolean and the nest packer a `rotate`
integer (0/90/180/270); engine/modifier.py's _pack_and_emit is the single
point that normalizes the two into one integer.
"""
from __future__ import annotations

import functools
import math

from atlas_toolkit.domain.region_ops import round_up_to_multiple

# Deliberate deviation from repacker.py, inherited from the JS (see
# NOTES.md "Deviation: repack packing diverges from repacker.py"): spend
# up to 15% more area than the minimum in exchange for a squarer canvas.
_SQUARENESS_AREA_TOLERANCE = 1.15


def _js_or_chain_cmp(a: dict, b: dict, keys: tuple[str, ...]) -> int:
    """Port of JS's `(a.k1 - b.k1) || (a.k2 - b.k2) || ...` comparator
    chain. JS treats BOTH 0 and NaN as falsy and falls through to the next
    term; `diff == diff` is False exactly for NaN, so this reproduces both
    fall-through cases. A NaN aspect is reachable: _shelfPack, unlike
    nest_pack, does not reject zero-dimension items, and an all-zero-dim
    input divides 0 by 0 (confirmed against real JS -- it returns a 0x0
    result rather than throwing). A Python tuple-key sort cannot express
    this: computing the key would raise ZeroDivisionError, and a NaN inside
    a sort key orders unpredictably."""
    for key in keys:
        diff = a[key] - b[key]
        if diff == diff and diff != 0:
            return -1 if diff < 0 else 1
    return 0


def _aspect(w: int, h: int) -> float:
    """max/min, or NaN when either dimension is 0 -- JS's 0/0. See
    _js_or_chain_cmp for why NaN must survive rather than raise."""
    smaller = min(w, h)
    if smaller == 0:
        return math.nan
    return max(w, h) / smaller


def _pack_with_width(items: list[dict], strip_w: int, allow_rotate: bool) -> tuple[int, int, list[dict]]:
    """One shelf-packing attempt at a fixed strip width. Port of
    _shelfPack's inner packWithWidth."""
    ordered = sorted(items, key=lambda item: -max(item["w"], item["h"]))
    placements: list[dict] = []
    shelf_y = 0
    shelf_h = 0
    cursor_x = 0
    used_w = 0

    for item in ordered:
        w, h = item["w"], item["h"]
        pw, ph, rotated = w, h, False
        if allow_rotate:
            if shelf_h > 0:
                waste_a = h - shelf_h if h > shelf_h else 0
                waste_b = w - shelf_h if w > shelf_h else 0
                if waste_b < waste_a:
                    pw, ph, rotated = h, w, True
            elif h > w:
                pw, ph, rotated = h, w, True
        if cursor_x + pw > strip_w and cursor_x > 0:
            shelf_y += shelf_h
            cursor_x = 0
            shelf_h = 0
        placements.append(
            {"name": item["name"], "x": cursor_x, "y": shelf_y, "pw": pw, "ph": ph, "rotated": rotated}
        )
        cursor_x += pw
        used_w = max(used_w, cursor_x)
        shelf_h = max(shelf_h, ph)

    return used_w, shelf_y + shelf_h, placements


def shelf_pack(items: list[dict]) -> dict:
    """Shelf bin-packer: try a fixed set of candidate strip widths with and
    without rotation, then pick the squarest layout whose area is within
    15% of the best area found, tie-broken by smaller area then smaller
    width. Port of atlas-modifier.js's _shelfPack.

    All scoring uses RAW (pre-rounding) dimensions -- round_up_to_multiple
    is applied once, to the winner only, so a small-canvas rounding delta
    can never flip which candidate looks squarest."""
    if not items:
        return {"canvas_w": 0, "canvas_h": 0, "placements": []}

    max_single = max(max(item["w"], item["h"]) for item in items)
    total_area = sum(item["w"] * item["h"] for item in items)
    sqrt_area = math.floor(math.sqrt(total_area))
    total_w = sum(max(item["w"], item["h"]) for item in items)

    candidate_widths = {
        max_single,
        total_w,
        max(max_single, sqrt_area),
        max(max_single, math.floor(sqrt_area * 0.8)),
        max(max_single, math.floor(sqrt_area * 1.2)),
        max(max_single, math.floor(sqrt_area * 1.5)),
        max(max_single, math.floor(sqrt_area * 2.0)),
    }
    candidate_widths.update(max_single * (i + 1) for i in range(5))

    candidates: list[dict] = []
    for strip_w in sorted(candidate_widths):
        for allow_rotate in (False, True):
            used_w, canvas_h, placements = _pack_with_width(items, strip_w, allow_rotate)
            candidates.append({
                "canvas_w": used_w,
                "canvas_h": canvas_h,
                "placements": placements,
                "area": used_w * canvas_h,
                "aspect": _aspect(used_w, canvas_h),
            })

    best_area = min(candidate["area"] for candidate in candidates)
    within_budget = [c for c in candidates if c["area"] <= best_area * _SQUARENESS_AREA_TOLERANCE]
    within_budget.sort(
        key=functools.cmp_to_key(
            lambda a, b: _js_or_chain_cmp(a, b, ("aspect", "area", "canvas_w"))
        )
    )
    best = within_budget[0]

    return {
        "canvas_w": round_up_to_multiple(best["canvas_w"]),
        "canvas_h": round_up_to_multiple(best["canvas_h"]),
        "placements": best["placements"],
    }
