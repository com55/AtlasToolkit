"""Parity tests for domain/packing.py's nest packer against the real
www/js/repack-nest.js (plain Node, via conftest.py's run_packing_oracle
fixture), plus pinned no-Node cases."""
import pytest

from atlas_toolkit.domain.packing import (
    dilate,
    footprint_for_canonical,
    grow_canvas,
    nest_pack,
    normalize_gap,
    rotate90_ccw,
    rotate90_cw,
    rotate180,
    solid_mask,
)

# 2x3 asymmetric pattern, deliberately NOT 180-degree-symmetric so the two
# 90-degree rotations must differ:
#   col: 0 1
# row0:  1 0
# row1:  1 0
# row2:  0 1
SRC_W, SRC_H = 2, 3
SRC = [1, 0, 1, 0, 0, 1]

CENTER_MASK = [0] * 25
CENTER_MASK[2 * 5 + 2] = 1
CORNER_MASK = [0] * 9
CORNER_MASK[0] = 1

GAP_VALUES = [1, 4, 7, 256, 0, -3, 2.9, 300, "abc", None, "5_000"]


def _solid(w, h):
    return [1] * (w * h)


# A gap-shaped host: only its LEFT 10 of 20 columns are occupied, so the
# gap distance decides where (and whether) the small item nests.
def _gap_shaped_host():
    mask = [0] * (20 * 20)
    for y in range(20):
        for x in range(10):
            mask[y * 20 + x] = 1
    return mask


TWO_10x10 = [
    {"name": "a", "w": 10, "h": 10, "footprint": _solid(10, 10)},
    {"name": "b", "w": 10, "h": 10, "footprint": _solid(10, 10)},
]
HOST_AND_SMALL = [
    {"name": "host", "w": 20, "h": 20, "footprint": _gap_shaped_host()},
    {"name": "small", "w": 6, "h": 6, "footprint": _solid(6, 6)},
]

CASES = {
    "rot_cw": ("rotateMask", ["cw", SRC, SRC_W, SRC_H]),
    "rot_ccw": ("rotateMask", ["ccw", SRC, SRC_W, SRC_H]),
    "rot_180": ("rotateMask", ["half", SRC, SRC_W, SRC_H]),
    "dilate_center": ("dilate", [CENTER_MASK, 5, 5, 1]),
    "dilate_corner": ("dilate", [CORNER_MASK, 3, 3, 2]),
    "grow_right": ("growCanvas", [2, 10, {"w": 8, "h": 1}, 1]),
    "grow_down": ("growCanvas", [10, 2, {"w": 1, "h": 8}, 1]),
    "grow_tie": ("growCanvas", [4, 8, {"w": 8, "h": 4}, 1]),
    "grow_from_zero": ("growCanvas", [0, 0, {"w": 10, "h": 6}, 1]),
    "nest_single": ("nestPack", [[{"name": "a", "w": 10, "h": 6, "footprint": _solid(10, 6)}], {"gapDistance": 1}]),
    "nest_empty": ("nestPack", [[], {}]),
    "nest_two_10x10_gap1": ("nestPack", [TWO_10x10, {"gapDistance": 1}]),
    "nest_two_10x10_gap4": ("nestPack", [TWO_10x10, {"gapDistance": 4}]),
    "nest_two_10x10_gap8": ("nestPack", [TWO_10x10, {"gapDistance": 8}]),
    "nest_gapshaped_gap1": ("nestPack", [HOST_AND_SMALL, {"gapDistance": 1}]),
    "nest_gapshaped_gap6": ("nestPack", [HOST_AND_SMALL, {"gapDistance": 6}]),
}

PY_CALLS = {
    "rot_cw": lambda: list(rotate90_cw(bytes(SRC), SRC_W, SRC_H)),
    "rot_ccw": lambda: list(rotate90_ccw(bytes(SRC), SRC_W, SRC_H)),
    "rot_180": lambda: list(rotate180(bytes(SRC), SRC_W, SRC_H)),
    "dilate_center": lambda: _dilate_shape(CENTER_MASK, 5, 5, 1),
    "dilate_corner": lambda: _dilate_shape(CORNER_MASK, 3, 3, 2),
    "grow_right": lambda: _grow_shape(2, 10, {"w": 8, "h": 1}, 1),
    "grow_down": lambda: _grow_shape(10, 2, {"w": 1, "h": 8}, 1),
    "grow_tie": lambda: _grow_shape(4, 8, {"w": 8, "h": 4}, 1),
    "grow_from_zero": lambda: _grow_shape(0, 0, {"w": 10, "h": 6}, 1),
    "nest_single": lambda: _nest_shape([{"name": "a", "w": 10, "h": 6, "footprint": _solid(10, 6)}], 1),
    "nest_empty": lambda: _nest_shape([], 4),
    "nest_two_10x10_gap1": lambda: _nest_shape(TWO_10x10, 1),
    "nest_two_10x10_gap4": lambda: _nest_shape(TWO_10x10, 4),
    "nest_two_10x10_gap8": lambda: _nest_shape(TWO_10x10, 8),
    "nest_gapshaped_gap1": lambda: _nest_shape(HOST_AND_SMALL, 1),
    "nest_gapshaped_gap6": lambda: _nest_shape(HOST_AND_SMALL, 6),
}


def _dilate_shape(mask, w, h, gap):
    result = dilate(bytes(mask), w, h, gap)
    return {"mask": list(result["mask"]), "w": result["w"], "h": result["h"]}


def _grow_shape(canvas_w, canvas_h, seed_rot, gap):
    occupied = bytes((canvas_w + 2 * gap) * (canvas_h + 2 * gap))
    result = grow_canvas(canvas_w, canvas_h, occupied, seed_rot, gap)
    return {
        "canvas_w": result["canvas_w"],
        "canvas_h": result["canvas_h"],
        "occupied_len": len(result["occupied"]),
    }


def _nest_shape(items, gap_distance):
    built = [{**item, "footprint": bytes(item["footprint"])} for item in items]
    result = nest_pack(built, gap_distance=gap_distance)
    return {
        "canvasW": result["canvas_w"],
        "canvasH": result["canvas_h"],
        "placements": result["placements"],
    }


@pytest.fixture(scope="module")
def js_results(run_packing_oracle):
    case_ids = list(CASES)
    return dict(zip(case_ids, run_packing_oracle([CASES[c] for c in case_ids]), strict=True))


@pytest.mark.parametrize("case_id", list(CASES))
def test_matches_real_js(case_id, js_results):
    assert PY_CALLS[case_id]() == js_results[case_id]


@pytest.mark.parametrize("value", GAP_VALUES)
def test_normalize_gap_matches_real_js(value, run_packing_oracle):
    (expected,) = run_packing_oracle([("normalizeGap", [value])])
    assert normalize_gap(value) == expected


def test_rotations_are_not_interchangeable():
    # Guards against a future edit silently reintroducing 180-degree
    # symmetry, which would make the CW/CCW distinction untestable.
    assert rotate90_cw(bytes(SRC), SRC_W, SRC_H) != rotate90_ccw(bytes(SRC), SRC_W, SRC_H)


def test_rotate_cw_then_ccw_round_trips():
    cw = rotate90_cw(bytes(SRC), SRC_W, SRC_H)
    assert list(rotate90_ccw(bytes(cw), SRC_H, SRC_W)) == SRC


def test_dilate_pads_so_the_margin_is_never_clipped():
    result = dilate(bytes(CORNER_MASK), 3, 3, 2)
    assert (result["w"], result["h"]) == (7, 7)
    # (0,0) maps to padded (2,2); dilated by 2 -> a full 5x5 block.
    for y in range(5):
        for x in range(5):
            assert result["mask"][y * 7 + x] == 1
    assert result["mask"][5 * 7 + 5] == 0


def test_normalize_gap_floors_rather_than_rounds():
    assert normalize_gap(2.9) == 2
    assert normalize_gap(0) == 4
    assert normalize_gap(300) == 256


def test_nest_pack_rejects_non_positive_dimensions_instead_of_hanging():
    with pytest.raises(ValueError, match="non-positive dimensions"):
        nest_pack([{"name": "z", "w": 0, "h": 0, "footprint": b""}], gap_distance=1)


def test_gap_distance_actually_moves_the_second_item():
    # The discriminating property: a wider gap pushes `b` further down.
    # Without it, a gap bug is invisible (gap 1 and 2 happen to agree).
    ys = []
    for gap in (1, 4, 8):
        result = _nest_shape(TWO_10x10, gap)
        ys.append({p["name"]: p["y"] for p in result["placements"]}["b"])
    assert ys == [12, 16, 20]


def test_small_item_nests_into_the_hosts_free_region():
    result = _nest_shape(HOST_AND_SMALL, 1)
    assert (result["canvasW"], result["canvasH"]) == (20, 20)
    small = next(p for p in result["placements"] if p["name"] == "small")
    assert small["x"] >= 10  # inside the host's own bounding box, right half


def test_footprint_for_canonical_without_mesh_is_solid():
    # The live no-mesh production path (Smart Packing on, no .skel loaded).
    class _Raster:
        def size(self, handle):
            return handle

    ctx = {"sprites": {"a": (4, 3)}, "raster": _Raster(), "mesh_lookup_fn": None}
    assert footprint_for_canonical(["a"], ctx) == solid_mask(4, 3)


def test_footprint_for_canonical_with_mesh_defers_to_the_mesh_stage():
    class _Raster:
        def size(self, handle):
            return handle

    ctx = {"sprites": {"a": (4, 3)}, "raster": _Raster(), "mesh_lookup_fn": lambda name: None}
    with pytest.raises(NotImplementedError, match="mesh"):
        footprint_for_canonical(["a"], ctx)
