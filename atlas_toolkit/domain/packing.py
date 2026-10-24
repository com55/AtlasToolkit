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

# Largest canvas side shelf_pack and nest_pack may produce. DELIBERATE
# DEVIATION from the JS app, which has no cap of its own (only the
# browser's canvas limit, somewhere past 16384 px, eventually stops it):
# without one, an oversized input keeps growing the canvas until the process
# runs out of memory. Every layout that fits inside it is unchanged; only
# inputs that would otherwise exceed it behave differently from JS. Read at
# call time, so tests can lower it. Keep it a multiple of 4: canvases are
# rounded up to one, and 10000 rounds to itself.
MAX_CANVAS = 10000


class CanvasTooLargeError(ValueError):
    """Packing would need a canvas side over MAX_CANVAS. Raised before the
    oversized canvas or occupancy grid is allocated. `width`/`height` are
    the packer's estimate of the layout it could not fit, not a minimum."""

    def __init__(self, width: int, height: int, limit: int) -> None:
        super().__init__(f"the packed layout would be about {width}x{height}, over the {limit} px per side limit")
        self.width = width
        self.height = height
        self.limit = limit

    def __reduce__(self):
        # args holds only the message; rebuild from the fields (state keeps a tagged `page`).
        return type(self), (self.width, self.height, self.limit), self.__dict__


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


def _pack_with_width(items: list[dict], strip_w: int, allow_rotate: bool, padding: int = 0) -> tuple[int, int, list[dict]]:
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
            shelf_y += shelf_h + padding
            cursor_x = 0
            shelf_h = 0
        placements.append(
            {"name": item["name"], "x": cursor_x, "y": shelf_y, "pw": pw, "ph": ph, "rotated": rotated}
        )
        # Measured before the padding is added: no trailing padding.
        used_w = max(used_w, cursor_x + pw)
        cursor_x += pw + padding
        shelf_h = max(shelf_h, ph)

    return used_w, shelf_y + shelf_h, placements


def _squarest_within_budget(candidates: list[dict]) -> dict:
    """The squarest candidate whose area is within _SQUARENESS_AREA_TOLERANCE
    of the smallest, tie-broken by smaller area then smaller width -- the
    app's choice rule."""
    best_area = min(candidate["area"] for candidate in candidates)
    within_budget = [c for c in candidates if c["area"] <= best_area * _SQUARENESS_AREA_TOLERANCE]
    within_budget.sort(
        key=functools.cmp_to_key(
            lambda a, b: _js_or_chain_cmp(a, b, ("aspect", "area", "canvas_w"))
        )
    )
    return within_budget[0]


def shelf_pack(items: list[dict], padding: int = 0) -> dict:
    """Shelf bin-packer: try a fixed set of candidate strip widths with and
    without rotation, then pick the squarest layout whose area is within
    15% of the best area found, tie-broken by smaller area then smaller
    width. Port of atlas-modifier.js's _shelfPack.

    All scoring uses RAW (pre-rounding) dimensions -- round_up_to_multiple
    is applied once, to the winner only, so a small-canvas rounding delta
    can never flip which candidate looks squarest.

    `padding` (Python-only, default 0 = the app's layout) is the number of
    pixels left between items, on both axes, never at the edges."""
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

    def attempt(strip_w: int, allow_rotate: bool) -> dict:
        used_w, canvas_h, placements = _pack_with_width(items, strip_w, allow_rotate, padding)
        return {
            "canvas_w": used_w,
            "canvas_h": canvas_h,
            "placements": placements,
            "area": used_w * canvas_h,
            "aspect": _aspect(used_w, canvas_h),
        }

    candidates = [
        attempt(strip_w, allow_rotate)
        for strip_w in sorted(candidate_widths)
        for allow_rotate in (False, True)
    ]
    best = _squarest_within_budget(candidates)
    limit = MAX_CANVAS
    if best["canvas_w"] > limit or best["canvas_h"] > limit:
        # Only reached when the app's own choice is too big (see MAX_CANVAS).
        # The fixed widths can all overflow together, so also try strips
        # exactly as wide as the limit, then apply the app's own rule to
        # the candidates that fit.
        candidates += [attempt(limit, allow_rotate) for allow_rotate in (False, True)]
        fitting = [c for c in candidates if c["canvas_w"] <= limit and c["canvas_h"] <= limit]
        if not fitting:
            raise CanvasTooLargeError(best["canvas_w"], best["canvas_h"], limit)
        best = _squarest_within_budget(fitting)

    return {
        "canvas_w": round_up_to_multiple(best["canvas_w"]),
        "canvas_h": round_up_to_multiple(best["canvas_h"]),
        "placements": best["placements"],
    }


# ─── Rotation (pure array arithmetic on 0/1 masks) ──────────────────────

def _grid(mask: bytes, w: int, h: int) -> np.ndarray:
    """View a flat row-major mask as an (h, w) uint8 array, no copy."""
    return np.frombuffer(mask, dtype=np.uint8).reshape(h, w)


def _rot_cw_grid(grid: np.ndarray) -> np.ndarray:
    return np.ascontiguousarray(grid.T[:, ::-1])


def _rot_ccw_grid(grid: np.ndarray) -> np.ndarray:
    return np.ascontiguousarray(grid.T[::-1, :])


def _rot_180_grid(grid: np.ndarray) -> np.ndarray:
    return np.ascontiguousarray(grid[::-1, ::-1])


def rotate90_cw(mask: bytes, w: int, h: int) -> bytearray:
    """Rotate a w x h mask 90 degrees clockwise. Output is h x w. Port of
    repack-nest.js's rotate90CW."""
    return bytearray(_rot_cw_grid(_grid(mask, w, h)).tobytes())


def rotate180(mask: bytes, w: int, h: int) -> bytearray:
    """Rotate a w x h mask 180 degrees. Output is w x h. Port of
    repack-nest.js's rotate180."""
    return bytearray(_rot_180_grid(_grid(mask, w, h)).tobytes())


def rotate90_ccw(mask: bytes, w: int, h: int) -> bytearray:
    """Rotate a w x h mask 90 degrees counter-clockwise. Output is h x w.
    Port of repack-nest.js's rotate90CCW."""
    return bytearray(_rot_ccw_grid(_grid(mask, w, h)).tobytes())


# ─── Dilation ───────────────────────────────────────────────────────────

def _dilate_grid(grid: np.ndarray, gap: int) -> np.ndarray:
    """Chebyshev dilation of an (h, w) mask by `gap`, returned as an
    (h + 2*gap, w + 2*gap) uint8 array. The input sits at offset (gap, gap)
    in the output; cells grown past the padded border are clipped, which
    cannot lose anything because the padding already holds the full
    margin. A (2*gap+1)-square window sum via a 2-D cumulative sum, so the
    cost is O(area) regardless of `gap`."""
    h, w = grid.shape
    padded_h, padded_w = h + 2 * gap, w + 2 * gap
    window = 2 * gap + 1
    source = np.zeros((padded_h + 2 * gap, padded_w + 2 * gap), dtype=np.int32)
    source[2 * gap:2 * gap + h, 2 * gap:2 * gap + w] = grid != 0
    summed = np.zeros((source.shape[0] + 1, source.shape[1] + 1), dtype=np.int32)
    np.cumsum(np.cumsum(source, axis=0, dtype=np.int32), axis=1, dtype=np.int32, out=summed[1:, 1:])
    counts = (
        summed[window:window + padded_h, window:window + padded_w]
        - summed[:padded_h, window:window + padded_w]
        - summed[window:window + padded_h, :padded_w]
        + summed[:padded_h, :padded_w]
    )
    return (counts > 0).astype(np.uint8)


def dilate(mask: bytes, w: int, h: int, gap: int) -> dict:
    """Grow every occupied cell outward by `gap` cells (Chebyshev
    distance). Returns a mask padded by `gap` on every side --
    (w + 2*gap) x (h + 2*gap) -- so the dilation margin is never clipped;
    the input's own (0, 0) is (gap, gap) in the output. Port of
    repack-nest.js's dilate (vectorized; same result)."""
    out = _dilate_grid(_grid(mask, w, h), gap)
    return {"mask": bytearray(out.tobytes()), "w": w + 2 * gap, "h": h + 2 * gap}

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
_OVERLAP_ROW_BLOCK = 64  # rows tested per numpy call in the exact overlap check
_PRUNE_SAMPLES = 64  # mask cells probed across ALL candidate origins before the exact test
_FIRST_FIT_CHUNK_ROWS = 64  # grid rows (x _SCAN_STEP px) per band of the first-fit search


def _rotation_variants(item: dict, gap: int) -> list[dict]:
    """The 4 rotation variants of one item's footprint, each carrying an
    UNDILATED mask (for fit-testing) and a separately-dilated mask (for
    stamping once placed) -- two distinct arrays, never one shared
    pre-dilated mask. Masks are (h, w) uint8 arrays. The deg -> helper
    mapping matches this codebase's atlas-rotation convention (rotate:90
    is stored 90 CCW, rotate:270 is stored 90 CW). Port of
    repack-nest.js's rotationVariants."""
    base = (_grid(item["footprint"], item["w"], item["h"]) != 0).astype(np.uint8)
    masks = [base, _rot_ccw_grid(base), _rot_180_grid(base), _rot_cw_grid(base)]
    variants = []
    for deg, mask in zip((0, 90, 180, 270), masks):
        height, width = mask.shape
        cells_y, cells_x = np.nonzero(mask)
        # Evenly spaced occupied cells of the mask, in row-major order: each
        # one landing on an occupied grid cell proves an overlap on its own.
        picks = np.linspace(0, cells_y.size - 1, num=min(cells_y.size, _PRUNE_SAMPLES)).astype(np.intp) \
            if cells_y.size else np.zeros(0, dtype=np.intp)
        variants.append({
            "deg": deg, "w": width, "h": height, "mask": mask,
            "solid": bool(mask.all()), "empty": not mask.any(),
            "samples": (cells_y[picks], cells_x[picks]),
            "dilated_mask": _dilate_grid(mask, gap),
        })
    return variants


def _overlaps(occupied: np.ndarray, gap: int, rot: dict, x: int, y: int) -> bool:
    """Does any occupied cell of `rot`'s mask land on an occupied cell of
    the grid at canvas-relative origin (x, y)? `occupied` is the padded
    (canvas_h + 2*gap) x (canvas_w + 2*gap) grid; logical (lx, ly) sits at
    physical (ly + gap, lx + gap). Tested a block of rows at a time so a
    collision near the top exits without touching the rest."""
    mask = rot["mask"]
    window = occupied[y + gap:y + gap + rot["h"], x + gap:x + gap + rot["w"]]
    for row in range(0, rot["h"], _OVERLAP_ROW_BLOCK):
        if (window[row:row + _OVERLAP_ROW_BLOCK] & mask[row:row + _OVERLAP_ROW_BLOCK]).any():
            return True
    return False


def _prune_by_samples(
    occupied: np.ndarray, gap: int, rot: dict, keys: np.ndarray, ys: np.ndarray, xs: np.ndarray,
):
    """Drop every candidate origin where one of `rot`'s sampled mask cells
    lands on an occupied cell -- a proven overlap, so it can never be the
    answer. Only ever removes certain failures; whatever survives still
    needs the exact test. Vectorized over all candidates at once, shrinking
    the set after each probe."""
    sample_y, sample_x = rot["samples"]
    for dy, dx in zip(sample_y.tolist(), sample_x.tolist()):
        keep = occupied[ys + (gap + dy), xs + (gap + dx)] == 0
        keys, ys, xs = keys[keep], ys[keep], xs[keep]
        if not keys.size:
            break
    return keys, ys, xs


def _find_first_fit(variants: list[dict], occupied: np.ndarray, canvas_w: int, canvas_h: int, gap: int):
    """First-fit scan: for each candidate origin (row-major, step
    _SCAN_STEP), try every rotation in order; the first that fits wins.
    Position is the primary axis so the result stays close to a natural
    top-to-bottom, left-to-right fill. Port of findFirstFit.

    Same answer as testing every (origin, rotation) pair in order with an
    exact overlap check, reached cheaper: origins are walked in horizontal
    bands of _FIRST_FIT_CHUNK_ROWS grid rows. Within a band every
    rotation's origins are built at once, the ones where a sampled mask
    cell lands on an occupied cell are dropped (a proven overlap), and the
    survivors of all rotations are exact-tested in ordering-key order.
    Keys only grow from one band to the next, so the first fit found is
    the global first fit, and the search stops there instead of
    materializing every origin of a large canvas.

    Known trade-off: the old whole-canvas integral rejected a SOLID mask
    over any occupied cell in one step; the samples here can miss small
    obstacles between them, leaving survivors for the exact test. A large
    solid item facing a field of tiny obstacles can therefore be slower
    than before (a 300x300 item among 3000 dots: 3 s -> 10 s), while every
    measured real and synthetic atlas got faster (1.3-4.5x)."""
    count = len(variants)
    band_px = _FIRST_FIT_CHUNK_ROWS * _SCAN_STEP  # read per call: tests shrink it
    for band_top in range(0, canvas_h, band_px):
        pending = []  # (keys, variant index, ys, xs) that survived pruning
        for index, rot in enumerate(variants):
            width, height = rot["w"], rot["h"]
            if width > canvas_w or band_top > canvas_h - height:
                continue
            band_ys = np.arange(band_top, min(band_top + band_px, canvas_h - height + 1), _SCAN_STEP)
            band_xs = np.arange(0, canvas_w - width + 1, _SCAN_STEP)
            ys = np.repeat(band_ys, band_xs.size)
            xs = np.tile(band_xs, band_ys.size)
            keys = (ys * canvas_w + xs) * count + index
            keys, ys, xs = _prune_by_samples(occupied, gap, rot, keys, ys, xs)
            if keys.size:
                pending.append((keys, np.full(keys.size, index), ys, xs))
        if not pending:
            continue
        keys = np.concatenate([p[0] for p in pending])
        order = np.argsort(keys, kind="stable")
        indexes = np.concatenate([p[1] for p in pending])[order]
        all_y = np.concatenate([p[2] for p in pending])[order]
        all_x = np.concatenate([p[3] for p in pending])[order]
        for index, y, x in zip(indexes.tolist(), all_y.tolist(), all_x.tolist()):
            if not _overlaps(occupied, gap, variants[index], x, y):
                return {"rot": variants[index], "x": x, "y": y}
    return None


def _grow_dims(canvas_w: int, canvas_h: int, seed_rot: dict) -> tuple[int, int]:
    """Pick the grown canvas size: right or down, whichever keeps the
    result closer to square, sized to at least fit `seed_rot`'s own
    dimensions. At canvas_w == 0 both candidates come out identical, and
    the stable sort keeps "grow right" (listed first) without a special
    case -- which is why this uses a stable sort with the JS comparator
    chain rather than a key tuple."""
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
    return candidates[0]["w"], candidates[0]["h"]


def _grow_dims_within(canvas_w: int, canvas_h: int, seed_rot: dict, limit: int) -> tuple[int, int]:
    """_grow_dims kept inside `limit` (see MAX_CANVAS). While the usual,
    squarer choice fits, it is returned unchanged, so every layout that fits
    stays identical to the app's. Past the limit, try the other direction,
    then each choice clamped to the limit -- growing by a whole item side
    can overshoot a canvas that would still have fit -- and raise only when
    none of them grows the canvas any more."""
    preferred = _grow_dims(canvas_w, canvas_h, seed_rot)
    right = (canvas_w + seed_rot["w"], max(canvas_h, seed_rot["h"]))
    down = (max(canvas_w, seed_rot["w"]), canvas_h + seed_rot["h"])
    other = down if preferred == right else right
    clamped = [(min(w, limit), min(h, limit)) for w, h in (preferred, other)]
    for new_w, new_h in (preferred, other, *clamped):
        if new_w <= limit and new_h <= limit and (new_w, new_h) != (canvas_w, canvas_h):
            return new_w, new_h
    raise CanvasTooLargeError(preferred[0], preferred[1], limit)


def _grow_grid(
    canvas_w: int, canvas_h: int, occupied: np.ndarray, seed_rot: dict, gap: int, limit: int | None = None,
):
    """Grow the canvas (and its padded occupancy grid) for `seed_rot`;
    `limit` None means uncapped, as grow_canvas's JS port needs."""
    if limit is None:
        new_w, new_h = _grow_dims(canvas_w, canvas_h, seed_rot)
    else:
        new_w, new_h = _grow_dims_within(canvas_w, canvas_h, seed_rot, limit)
    nxt = np.zeros((new_h + 2 * gap, new_w + 2 * gap), dtype=np.uint8)
    if canvas_w > 0 or canvas_h > 0:
        nxt[:occupied.shape[0], :occupied.shape[1]] = occupied
    return new_w, new_h, nxt


def grow_canvas(canvas_w: int, canvas_h: int, occupied: bytes, seed_rot: dict, gap: int) -> dict:
    """Grow the canvas right or down, whichever keeps the result closer to
    square, sized to at least fit `seed_rot`'s own dimensions. `occupied`
    is the flat row-major padded grid; the result's `occupied` is the
    grown one, flat as well. Port of repack-nest.js's growCanvas."""
    old = _grid(occupied, canvas_w + 2 * gap, canvas_h + 2 * gap)
    new_w, new_h, nxt = _grow_grid(canvas_w, canvas_h, old, seed_rot, gap)
    return {"canvas_w": new_w, "canvas_h": new_h, "occupied": bytearray(nxt.tobytes())}


def _stamp_occupied(occupied: np.ndarray, gap: int, rot: dict, x: int, y: int) -> None:
    """OR a placed item's dilated mask into `occupied` at its chosen
    origin. The dilated mask's own (0, 0) is logical (x - gap, y - gap) and
    logical-to-physical adds +gap, so the two cancel. Port of
    stampOccupied."""
    dilated = rot["dilated_mask"]
    occupied[y:y + dilated.shape[0], x:x + dilated.shape[1]] |= dilated


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
    limit = MAX_CANVAS
    for item in items:
        if max(item["w"], item["h"]) > limit:  # too big in every rotation
            raise CanvasTooLargeError(item["w"], item["h"], limit)
    # Silhouettes may interlock but never overlap, so more occupied cells
    # than limit^2 can never fit: fail now instead of growing to the limit.
    cells = sum(int(np.count_nonzero(_grid(item["footprint"], item["w"], item["h"]))) for item in items)
    if cells > limit * limit:
        raise CanvasTooLargeError(limit, -(-cells // limit), limit)

    ordered = sorted(items, key=lambda item: -max(item["w"], item["h"]))

    canvas_w = 0
    canvas_h = 0
    occupied = np.zeros((0, 0), dtype=np.uint8)
    placements: list[dict] = []

    for item in ordered:
        variants = _rotation_variants(item, gap)
        spot = _find_first_fit(variants, occupied, canvas_w, canvas_h, gap) if canvas_w > 0 else None
        # Growth is monotonic (each call strictly increases canvas_w or
        # canvas_h) and capped at `limit` per side, after which it raises,
        # so this terminates.
        while spot is None:
            canvas_w, canvas_h, occupied = _grow_grid(canvas_w, canvas_h, occupied, variants[0], gap, limit)
            spot = _find_first_fit(variants, occupied, canvas_w, canvas_h, gap)
        _stamp_occupied(occupied, gap, spot["rot"], spot["x"], spot["y"])
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
    out = np.zeros(w * h, dtype=bool)
    for mask in masks:
        out |= np.frombuffer(mask, dtype=np.uint8) != 0
    return bytearray(out.astype(np.uint8).tobytes())


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
