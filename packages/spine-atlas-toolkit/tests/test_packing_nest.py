# SPDX-FileCopyrightText: 2026 com55
# SPDX-License-Identifier: Apache-2.0
"""Pinned tests for domain/packing.py's nest packer."""
import pytest

from spine_atlas_toolkit.domain.packing import (
    dilate,
    footprint_for_canonical,
    nest_pack,
    normalize_gap,
    rotate90_ccw,
    rotate90_cw,
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

def _nest_shape(items, gap_distance):
    built = [{**item, "footprint": bytes(item["footprint"])} for item in items]
    result = nest_pack(built, gap_distance=gap_distance)
    return {
        "canvasW": result["canvas_w"],
        "canvasH": result["canvas_h"],
        "placements": result["placements"],
    }


def test_normalize_gap_falls_back_on_an_int_too_big_for_a_float():
    # JS Number(10 ** 400) is Infinity, which normalizeGap maps to 4.
    assert normalize_gap(10**400) == 4


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


def test_footprint_for_canonical_with_mesh_lookup_returning_none_gives_solid():
    # When mesh_lookup_fn is provided but returns None for a name, that
    # name's mesh is treated as solid (no mesh data available). This is the
    # "mesh-aware mode off" path on a per-name basis (not just a per-mode
    # lookup function).
    class _Raster:
        def size(self, handle):
            return handle

    ctx = {
        "sprites": {"a": (4, 3)},
        "raster": _Raster(),
        "regions": {"a": {"offsets": None}},
        "mesh_lookup_fn": lambda name: None,
        "combine_mesh_geometry": lambda names, lookup: None,
        "group_names_by_sprite_identity": lambda sprites, modded: [],
        "mask_crop_rect_for_offsets": lambda offsets, w, h: None,
    }
    # When mesh_lookup_fn returns None (no mesh data), the result is solid
    # (all opaque).
    assert footprint_for_canonical(["a"], ctx) == solid_mask(4, 3)


# --- trim_canvas: nest growth adds whole item strips; the final canvas is cut
# back to the placements' extent, rounded up to 4 (Python-only, deliberate
# divergence from repack-nest.js) ---------------------------------------------

def test_trim_canvas_cuts_unused_growth_strip():
    from spine_atlas_toolkit.domain.packing import trim_canvas
    items = [
        {"name": "a", "w": 40, "h": 40, "footprint": bytes(_solid(40, 40))},
        {"name": "b", "w": 30, "h": 10, "footprint": bytes(_solid(30, 10))},
        {"name": "c", "w": 10, "h": 10, "footprint": bytes(_solid(10, 10))},
    ]
    packed = nest_pack(items, gap_distance=4)
    assert (packed["canvas_w"], packed["canvas_h"]) == (72, 52)  # control: slack below
    trimmed = trim_canvas(packed)
    assert (trimmed["canvas_w"], trimmed["canvas_h"]) == (72, 40)
    assert trimmed["placements"] == packed["placements"]


def test_trim_canvas_rounds_up_to_four_and_keeps_empty():
    from spine_atlas_toolkit.domain.packing import trim_canvas
    packed = {"canvas_w": 100, "canvas_h": 100, "placements": [
        {"name": "a", "x": 0, "y": 0, "pw": 13, "ph": 5, "rotate": 0},
        {"name": "b", "x": 20, "y": 30, "pw": 7, "ph": 9, "rotate": 90},
    ]}
    assert (trim_canvas(packed)["canvas_w"], trim_canvas(packed)["canvas_h"]) == (28, 40)
    empty = {"canvas_w": 0, "canvas_h": 0, "placements": []}
    assert trim_canvas(empty) == empty
