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

import numpy as np

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


# ─── Rotation (pure array arithmetic on 0/1 masks) ──────────────────────

def rotate90_cw(mask: bytes, w: int, h: int) -> bytearray:
    """Rotate a w x h mask 90 degrees clockwise. Output is h x w. Port of
    repack-nest.js's rotate90CW."""
    out = bytearray(w * h)
    for y in range(h):
        for x in range(w):
            out[x * h + (h - 1 - y)] = mask[y * w + x]
    return out


def rotate180(mask: bytes, w: int, h: int) -> bytearray:
    """Rotate a w x h mask 180 degrees. Output is w x h. Port of
    repack-nest.js's rotate180."""
    size = w * h
    out = bytearray(size)
    for i in range(size):
        out[size - 1 - i] = mask[i]
    return out


def rotate90_ccw(mask: bytes, w: int, h: int) -> bytearray:
    """Rotate a w x h mask 90 degrees counter-clockwise. Output is h x w.
    Port of repack-nest.js's rotate90CCW."""
    out = bytearray(w * h)
    for y in range(h):
        for x in range(w):
            out[(w - 1 - x) * h + y] = mask[y * w + x]
    return out


# ─── Dilation ───────────────────────────────────────────────────────────

def dilate(mask: bytes, w: int, h: int, gap: int) -> dict:
    """Grow every occupied cell outward by `gap` cells (Chebyshev
    distance). Returns a mask padded by `gap` on every side --
    (w + 2*gap) x (h + 2*gap) -- so the dilation margin is never clipped;
    the input's own (0, 0) is (gap, gap) in the output. Two-pass separable
    expand, O(w*h*gap) not O(w*h*gap**2). Port of repack-nest.js's
    dilate."""
    padded_w = w + 2 * gap
    padded_h = h + 2 * gap
    stage1 = bytearray(padded_w * h)
    for y in range(h):
        for x in range(w):
            if not mask[y * w + x]:
                continue
            cx = x + gap
            x0 = max(0, cx - gap)
            x1 = min(padded_w - 1, cx + gap)
            for nx in range(x0, x1 + 1):
                stage1[y * padded_w + nx] = 1

    out = bytearray(padded_w * padded_h)
    for y in range(h):
        for x in range(padded_w):
            if not stage1[y * padded_w + x]:
                continue
            cy = y + gap
            y0 = max(0, cy - gap)
            y1 = min(padded_h - 1, cy + gap)
            for ny in range(y0, y1 + 1):
                out[ny * padded_w + x] = 1
    return {"mask": out, "w": padded_w, "h": padded_h}


# ─── gap_distance normalization ─────────────────────────────────────────

def normalize_gap(value: object) -> int:
    """Normalize a gap distance to a finite integer in [1, 256], falling
    back to the default (4) on anything else -- an invalid value (corrupt
    pref, a bad direct API call) is closer to "unset" than "user chose 1".
    Port of repack-nest.js's normalizeGap, including its Math.floor: 2.9
    becomes 2, not 3.

    JS reaches the same answers by a different route: Number(None) is 0 and
    Number("abc") is NaN, both of which fail the `< 1` / `isFinite` checks
    and land on 4 -- here the equivalent conversions raise, and the except
    branch returns the same 4.

    PARITY IS NARROWED TO WHAT IS REACHABLE, not claimed in general. The two
    routes agree on every value a real caller can deliver, and on the tested
    corpus (ints, floats, bools, None, "abc", "  7  ", "1e2", "", "0",
    "5_000", NaN, Infinity, out-of-range). They do NOT agree on every
    possible string: Number("0x10")/("0o17")/("0b101") are 16/15/5 while
    float() raises and lands on 4. Those radix forms are deliberately left
    divergent because no production path can deliver a string here at all --
    atlas-extracter.js:192 substitutes 4 for any non-finite value before
    nestPack is reached, and atlas-api.js:608 normalizes on the way in.
    Underscore-grouped strings ARE handled below, because that is the one
    direction where Python would otherwise be more permissive than JS."""
    if isinstance(value, bool):
        number = float(int(value))
    else:
        if isinstance(value, str) and "_" in value:
            return 4  # PEP 515 grouping; Number() yields NaN -> 4
        try:
            number = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 4
    if math.isnan(number) or math.isinf(number):
        return 4
    n = math.floor(number)
    if n < 1:
        return 4
    return min(n, 256)


# ─── The packer ─────────────────────────────────────────────────────────

_SCAN_STEP = 4  # px -- fixed-step raster scan, not per-pixel (performance)


def _rotation_variants(item: dict, gap: int) -> list[dict]:
    """The 4 rotation variants of one item's footprint, each carrying an
    UNDILATED mask (for fit-testing) and a separately-dilated mask (for
    stamping once placed) -- two distinct arrays, never one shared
    pre-dilated mask. The deg -> helper mapping matches this codebase's
    atlas-rotation convention (rotate:90 is stored 90 CCW, rotate:270 is
    stored 90 CW). Port of repack-nest.js's rotationVariants."""
    variants = [
        {"deg": 0, "w": item["w"], "h": item["h"], "mask": item["footprint"]},
        {"deg": 90, "w": item["h"], "h": item["w"],
         "mask": rotate90_ccw(item["footprint"], item["w"], item["h"])},
        {"deg": 180, "w": item["w"], "h": item["h"],
         "mask": rotate180(item["footprint"], item["w"], item["h"])},
        {"deg": 270, "w": item["h"], "h": item["w"],
         "mask": rotate90_cw(item["footprint"], item["w"], item["h"])},
    ]
    for variant in variants:
        variant["dilated_mask"] = dilate(variant["mask"], variant["w"], variant["h"], gap)
    return variants


def _fits(occupied: bytes, canvas_w: int, canvas_h: int, gap: int, rot: dict, x: int, y: int) -> bool:
    """Does `rot` fit at canvas-relative origin (x, y) without exceeding
    canvas bounds or overlapping the already-dilated occupied grid?
    `occupied` is (canvas_w + 2*gap) x (canvas_h + 2*gap); logical (lx, ly)
    sits at physical (ly + gap) * padded_w + (lx + gap). Port of
    repack-nest.js's fits."""
    if x + rot["w"] > canvas_w or y + rot["h"] > canvas_h:
        return False
    padded_w = canvas_w + 2 * gap
    mask = rot["mask"]
    for my in range(rot["h"]):
        for mx in range(rot["w"]):
            if not mask[my * rot["w"] + mx]:
                continue
            if occupied[(y + my + gap) * padded_w + (x + mx + gap)]:
                return False
    return True


def _find_first_fit(variants: list[dict], occupied: bytes, canvas_w: int, canvas_h: int, gap: int):
    """First-fit scan: for each candidate origin (row-major, step
    _SCAN_STEP), try every rotation in order; the first that fits wins.
    Position is the primary axis so the result stays close to a natural
    top-to-bottom, left-to-right fill. Port of findFirstFit."""
    for y in range(0, canvas_h, _SCAN_STEP):
        for x in range(0, canvas_w, _SCAN_STEP):
            for rot in variants:
                if _fits(occupied, canvas_w, canvas_h, gap, rot, x, y):
                    return {"rot": rot, "x": x, "y": y}
    return None


def grow_canvas(canvas_w: int, canvas_h: int, occupied: bytes, seed_rot: dict, gap: int) -> dict:
    """Grow the canvas right or down, whichever keeps the result closer to
    square, sized to at least fit `seed_rot`'s own dimensions. At
    canvas_w == 0 both candidates come out identical, and the stable sort
    keeps "grow right" (listed first) without a special case -- which is
    why this uses a stable sort with the JS comparator chain rather than a
    key tuple. Port of repack-nest.js's growCanvas."""
    candidates = [
        {"go_right": True, "w": canvas_w + seed_rot["w"], "h": max(canvas_h, seed_rot["h"])},
        {"go_right": False, "w": max(canvas_w, seed_rot["w"]), "h": canvas_h + seed_rot["h"]},
    ]
    for candidate in candidates:
        candidate["aspect"] = _aspect(candidate["w"], candidate["h"])
        candidate["area"] = candidate["w"] * candidate["h"]
    candidates.sort(
        key=functools.cmp_to_key(lambda a, b: _js_or_chain_cmp(a, b, ("aspect", "area", "w")))
    )
    new_w = candidates[0]["w"]
    new_h = candidates[0]["h"]

    new_padded_w = new_w + 2 * gap
    new_padded_h = new_h + 2 * gap
    nxt = bytearray(new_padded_w * new_padded_h)
    if canvas_w > 0 or canvas_h > 0:
        old_padded_w = canvas_w + 2 * gap
        for y in range(canvas_h + 2 * gap):
            row_start = y * old_padded_w
            nxt[y * new_padded_w:y * new_padded_w + old_padded_w] = occupied[row_start:row_start + old_padded_w]
    return {"canvas_w": new_w, "canvas_h": new_h, "occupied": nxt}


def _stamp_occupied(occupied: bytearray, canvas_w: int, gap: int, rot: dict, x: int, y: int) -> None:
    """OR a placed item's dilated mask into `occupied` at its chosen
    origin. The dilated mask's own (0, 0) is logical (x - gap, y - gap) and
    logical-to-physical adds +gap, so the two cancel. Port of
    stampOccupied."""
    padded_w = canvas_w + 2 * gap
    dilated = rot["dilated_mask"]
    mask, dw, dh = dilated["mask"], dilated["w"], dilated["h"]
    for dy in range(dh):
        for dx in range(dw):
            if mask[dy * dw + dx]:
                occupied[(y + dy) * padded_w + (x + dx)] = 1


def nest_pack(items: list[dict], gap_distance: int = 4) -> dict:
    """The silhouette-aware packer ("Smart Packing"): sort items
    largest-first, place each greedily against one shared occupied-pixel
    grid (growing the canvas when nothing fits), trying all 4 rotations per
    item. `footprint` is UNDILATED, w*h, row-major, 1 = occupied.
    Placements carry `rotate` as 0/90/180/270, never a boolean. Port of
    repack-nest.js's nestPack.

    `gap_distance` is a plain keyword here, not JS's `{gapDistance}`
    options object -- the JS shape exists only because that call site
    passes an options bag."""
    gap = normalize_gap(gap_distance)
    if not items:
        return {"canvas_w": 0, "canvas_h": 0, "placements": []}
    for item in items:
        if item["w"] <= 0 or item["h"] <= 0:
            raise ValueError(
                f'nest_pack: item "{item["name"]}" has non-positive dimensions '
                f'({item["w"]}x{item["h"]})'
            )

    ordered = sorted(items, key=lambda item: -max(item["w"], item["h"]))

    canvas_w = 0
    canvas_h = 0
    occupied = bytearray(0)
    placements: list[dict] = []

    for item in ordered:
        variants = _rotation_variants(item, gap)
        spot = _find_first_fit(variants, occupied, canvas_w, canvas_h, gap) if canvas_w > 0 else None
        # Growth is monotonic (each call strictly increases canvas_w or
        # canvas_h by at least the unrotated variant's own dimension), so
        # this terminates.
        while spot is None:
            grown = grow_canvas(canvas_w, canvas_h, occupied, variants[0], gap)
            canvas_w, canvas_h, occupied = grown["canvas_w"], grown["canvas_h"], grown["occupied"]
            spot = _find_first_fit(variants, occupied, canvas_w, canvas_h, gap)
        _stamp_occupied(occupied, canvas_w, gap, spot["rot"], spot["x"], spot["y"])
        placements.append({
            "name": item["name"], "x": spot["x"], "y": spot["y"],
            "pw": spot["rot"]["w"], "ph": spot["rot"]["h"], "rotate": spot["rot"]["deg"],
        })

    return {
        "canvas_w": round_up_to_multiple(canvas_w),
        "canvas_h": round_up_to_multiple(canvas_h),
        "placements": placements,
    }


# ─── Occupied footprint from mesh geometry ──────────────────────────────

def solid_mask(w: int, h: int) -> bytearray:
    """All-1 mask -- the "no usable mesh, protect the whole rect"
    fallback. Port of repack-nest.js's solidMask."""
    return bytearray(b"\x01" * (w * h))


def union_masks(masks: list[bytearray], w: int, h: int) -> bytearray:
    """OR every mask in `masks` together. Empty input returns a solid
    mask, matching the "no mesh data -> unmasked" convention used
    throughout this codebase. Port of unionMasks."""
    if not masks:
        return solid_mask(w, h)
    out = bytearray(w * h)
    for mask in masks:
        for i, value in enumerate(mask):
            if value:
                out[i] = 1
    return out


def footprint_for_canonical(dedup_names: list[str], ctx: dict) -> bytearray:
    """The occupied-footprint mask for ONE canonical (post-dedup) packed
    item, in the same (w x h) space as the sprite that will be packed.
    Never reads a sprite's rendered pixel alpha -- always derived from mesh
    GEOMETRY. Port of repack-nest.js's footprintForCanonical.

    `ctx` carries the injected callables that keep this module free of any
    engine/ import (spec §2 point 2): `sprites`, `raster`,
    `mesh_lookup_fn`, `modded_sprites`, `added_sprites`, `regions`,
    `combine_mesh_geometry`, `group_names_by_sprite_identity`,
    `mask_crop_rect_for_offsets`."""
    sprites = ctx["sprites"]
    raster = ctx["raster"]
    mesh_lookup_fn = ctx.get("mesh_lookup_fn")

    canonical_w, canonical_h = raster.size(sprites[dedup_names[0]])
    if mesh_lookup_fn is None:
        return solid_mask(canonical_w, canonical_h)

    modded_sprites = ctx.get("modded_sprites")
    added_sprites = ctx.get("added_sprites")
    regions = ctx["regions"]
    combine_mesh_geometry_fn = ctx["combine_mesh_geometry"]
    group_names_by_sprite_identity_fn = ctx["group_names_by_sprite_identity"]
    mask_crop_rect_for_offsets_fn = ctx["mask_crop_rect_for_offsets"]

    w, h = canonical_w, canonical_h
    mod_groups = group_names_by_sprite_identity_fn(sprites, modded_sprites)

    # Dedup does not guarantee every name in this group shares the
    # canonical's own dimensions (a pixel-hash collision between
    # differently-shaped sprites is possible) -- resolved once, for the
    # whole group, before any branching below.
    dedup_set = set(dedup_names)
    # dedup_names[0] is always the canonical name (its size was already
    # read above as canonical_w/canonical_h) -- Stage 5's ledger flagged
    # the redundant second raster.size call here as harmless but asked
    # this stage to fold it away rather than carry it forward again.
    compatible: dict[str, bool] = {dedup_names[0]: True}
    for name in dedup_names[1:]:
        sprite_w, sprite_h = raster.size(sprites[name])
        compatible[name] = sprite_w == w and sprite_h == h

    def alpha_mask(handle) -> bytearray:
        return bytearray(np.asarray(raster.read_alpha_mask(handle), dtype=np.uint8).tobytes())

    masks: list[bytearray] = []
    handled: set[str] = set()
    for name in dedup_names:
        if name in handled:
            continue
        if not compatible[name]:
            handled.add(name)
            continue
        mod_group = next((g for g in mod_groups if name in g), None)
        if mod_group is not None:
            usable_group = [n for n in mod_group if n in dedup_set and compatible.get(n)]
            combined = combine_mesh_geometry_fn(usable_group, mesh_lookup_fn) if usable_group else None
            if combined is not None:
                mask_handle = raster.rasterize_mesh_mask(combined["uvs"], combined["triangles"], w, h)
                masks.append(alpha_mask(mask_handle))
            else:
                masks.append(solid_mask(w, h))
            handled.update(mod_group)
            continue
        handled.add(name)
        geom = mesh_lookup_fn(name)
        if geom is None:
            masks.append(solid_mask(w, h))
            continue
        is_modded_or_added = (
            (modded_sprites is not None and name in modded_sprites)
            or (added_sprites is not None and name in added_sprites)
        )
        region = regions[name]
        if is_modded_or_added or region["offsets"] is None:
            mask_handle = raster.rasterize_mesh_mask(geom["uvs"], geom["triangles"], w, h)
            masks.append(alpha_mask(mask_handle))
        else:
            rect = mask_crop_rect_for_offsets_fn(region["offsets"], w, h)
            orig_w, orig_h = region["offsets"][2], region["offsets"][3]
            full_handle = raster.rasterize_mesh_mask(geom["uvs"], geom["triangles"], orig_w, orig_h)
            cropped_handle = raster.crop_rotate(full_handle, rect["x"], rect["y"], rect["w"], rect["h"], 0)
            masks.append(alpha_mask(cropped_handle))
    return union_masks(masks, w, h)
