"""The numpy nest packer must place exactly where a plain per-pixel one does.

domain/packing.py's rotate/dilate/first-fit/stamp are vectorized (integral
image + exact overlap test). This file keeps a deliberately naive reference
-- brute-force loops, no numpy -- and requires identical output on random
masks (solid, empty, rings with holes, noise), so a speed-up that moves a
single placement fails here rather than in a user's repacked atlas.
"""
import random

import pytest

from atlas_toolkit.domain import packing
from atlas_toolkit.domain.packing import dilate, nest_pack, rotate180, rotate90_ccw, rotate90_cw
from atlas_toolkit.domain.region_ops import round_up_to_multiple


def _ref_rot_cw(mask, w, h):
    out = [0] * (w * h)
    for y in range(h):
        for x in range(w):
            out[x * h + (h - 1 - y)] = mask[y * w + x]
    return out


def _ref_rot_ccw(mask, w, h):
    out = [0] * (w * h)
    for y in range(h):
        for x in range(w):
            out[(w - 1 - x) * h + y] = mask[y * w + x]
    return out


def _ref_rot_180(mask, w, h):
    return list(reversed(list(mask)))


def _ref_dilate(mask, w, h, gap):
    padded_w, padded_h = w + 2 * gap, h + 2 * gap
    out = [0] * (padded_w * padded_h)
    for y in range(h):
        for x in range(w):
            if not mask[y * w + x]:
                continue
            for ny in range(max(0, y), min(padded_h, y + 2 * gap + 1)):
                for nx in range(max(0, x), min(padded_w, x + 2 * gap + 1)):
                    out[ny * padded_w + nx] = 1
    return out


def _ref_nest_pack(items, gap_distance):
    gap = packing.normalize_gap(gap_distance)
    ordered = sorted(items, key=lambda item: -max(item["w"], item["h"]))
    canvas_w = canvas_h = 0
    occupied = {}  # (padded_x, padded_y) -> 1
    placements = []
    for item in ordered:
        w, h, footprint = item["w"], item["h"], list(item["footprint"])
        variants = [
            (0, w, h, footprint),
            (90, h, w, _ref_rot_ccw(footprint, w, h)),
            (180, w, h, _ref_rot_180(footprint, w, h)),
            (270, h, w, _ref_rot_cw(footprint, w, h)),
        ]

        def fits(variant, x, y):
            _, vw, vh, mask = variant
            if x + vw > canvas_w or y + vh > canvas_h:
                return False
            return not any(
                mask[my * vw + mx] and (x + mx + gap, y + my + gap) in occupied
                for my in range(vh) for mx in range(vw)
            )

        def first_fit():
            for y in range(0, canvas_h, 4):
                for x in range(0, canvas_w, 4):
                    for variant in variants:
                        if fits(variant, x, y):
                            return variant, x, y
            return None

        spot = first_fit() if canvas_w > 0 else None
        while spot is None:
            canvas_w, canvas_h = packing._grow_dims(
                canvas_w, canvas_h, {"w": variants[0][1], "h": variants[0][2]},
            )
            spot = first_fit()
        (deg, vw, vh, mask), x, y = spot
        dilated = _ref_dilate(mask, vw, vh, gap)
        dilated_w = vw + 2 * gap
        for index, value in enumerate(dilated):
            if value:
                occupied[(x + index % dilated_w, y + index // dilated_w)] = 1
        placements.append({"name": item["name"], "x": x, "y": y, "pw": vw, "ph": vh, "rotate": deg})
    return {
        "canvas_w": round_up_to_multiple(canvas_w),
        "canvas_h": round_up_to_multiple(canvas_h),
        "placements": placements,
    }


def _random_mask(rng, w, h):
    kind = rng.choice(["solid", "empty", "ring", "corner", "noise"])
    cells = []
    for y in range(h):
        for x in range(w):
            if kind == "solid":
                cells.append(1)
            elif kind == "empty":
                cells.append(0)
            elif kind == "ring":
                cx, cy = (w - 1) / 2, (h - 1) / 2
                radius = ((x - cx) / (w / 2)) ** 2 + ((y - cy) / (h / 2)) ** 2
                cells.append(1 if 0.3 <= radius <= 1 else 0)
            elif kind == "corner":
                cells.append(1 if x < w // 3 or y >= h * 2 // 3 else 0)
            else:
                cells.append(1 if rng.random() < 0.5 else 0)
    return bytes(cells)


@pytest.mark.parametrize("seed", range(6))
def test_rotations_and_dilation_match_brute_force(seed):
    rng = random.Random(seed)
    for _ in range(12):
        w, h, gap = rng.randint(1, 9), rng.randint(1, 9), rng.randint(1, 5)
        mask = _random_mask(rng, w, h)
        assert list(rotate90_cw(mask, w, h)) == _ref_rot_cw(mask, w, h)
        assert list(rotate90_ccw(mask, w, h)) == _ref_rot_ccw(mask, w, h)
        assert list(rotate180(mask, w, h)) == _ref_rot_180(mask, w, h)
        result = dilate(mask, w, h, gap)
        assert (result["w"], result["h"]) == (w + 2 * gap, h + 2 * gap)
        assert list(result["mask"]) == _ref_dilate(mask, w, h, gap)


@pytest.mark.parametrize("seed", range(10))
def test_nest_pack_places_exactly_like_the_brute_force_reference(seed):
    rng = random.Random(1000 + seed)
    gap = rng.choice([1, 2, 4, 6])
    items = []
    for index in range(rng.randint(2, 7)):
        w, h = rng.randint(1, 14), rng.randint(1, 14)
        items.append({"name": f"i{index}", "w": w, "h": h, "footprint": _random_mask(rng, w, h)})
    assert nest_pack(items, gap_distance=gap) == _ref_nest_pack(items, gap)


@pytest.mark.parametrize("seed", range(4))
def test_nest_pack_with_masks_larger_than_the_probe_sample_matches_reference(seed):
    """Masks with more occupied cells than _PRUNE_SAMPLES, so the sampled
    pre-filter (not just the exact test) decides which origins survive."""
    rng = random.Random(2000 + seed)
    items = []
    for index in range(4):
        w, h = rng.randint(20, 34), rng.randint(20, 34)
        items.append({"name": f"i{index}", "w": w, "h": h, "footprint": _random_mask(rng, w, h)})
    gap = rng.choice([2, 4])
    assert nest_pack(items, gap_distance=gap) == _ref_nest_pack(items, gap)
