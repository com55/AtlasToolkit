# SPDX-FileCopyrightText: 2026 com55
# SPDX-License-Identifier: Apache-2.0
"""Pinned tests for domain/packing.py's shelf_pack."""

from spine_atlas_toolkit.domain.packing import shelf_pack

SIX_SQUARES = [{"name": f"s{i}", "w": 20, "h": 20} for i in range(6)]
SEVEN_SQUARES = [{"name": f"s{i}", "w": 20, "h": 20} for i in range(7)]
THREE_STRIPS = [
    {"name": "a", "w": 60, "h": 10},
    {"name": "b", "w": 60, "h": 10},
    {"name": "c", "w": 60, "h": 10},
]
# Non-square, mixed-aspect: the only fixture here where the packer actually
# rotates an item (`tall` comes back pw=33, ph=7, rotated=True). A
# square-only corpus hides per-axis bugs -- the Stage 4 lesson.
MIXED = [
    {"name": "wide", "w": 40, "h": 9},
    {"name": "tall", "w": 7, "h": 33},
    {"name": "small", "w": 11, "h": 5},
    {"name": "big", "w": 25, "h": 18},
]
# _shelfPack does NOT reject zero-dimension items (nest_pack does): an
# all-zero input divides 0 by 0, which is NaN in JS and must not raise in
# Python. Verified against real JS.
ZERO_ONLY = [{"name": "z", "w": 0, "h": 0}]
ZERO_AND_REAL = [{"name": "z", "w": 0, "h": 0}, {"name": "real", "w": 10, "h": 10}]

def test_six_squares_prefers_the_squarer_of_two_equal_area_layouts():
    # 1x6, 2x3, 3x2 and 6x1 all tile six 20x20 squares at the identical
    # minimum area (2400); the squarest (40x60) must win.
    result = shelf_pack(SIX_SQUARES)
    assert (result["canvas_w"], result["canvas_h"]) == (40, 60)
    assert result["canvas_w"] * result["canvas_h"] == 2400


def test_seven_squares_spends_the_area_budget_but_not_past_it():
    # 140x20 is the minimum area (2800, aspect 7.0); 40x80 costs 14.29%
    # more (inside the 15% budget) for aspect 2.0; 60x60 costs 28.57% more
    # (outside it) and must NOT be chosen despite being the squarest.
    result = shelf_pack(SEVEN_SQUARES)
    assert (result["canvas_w"], result["canvas_h"]) == (40, 80)
    assert result["canvas_w"] * result["canvas_h"] == 3200


def test_canvas_dimensions_are_rounded_up_to_a_multiple_of_four():
    # Raw 60x30 -> height rounds to 32; the raw values are what the
    # squareness scoring saw.
    result = shelf_pack(THREE_STRIPS)
    assert (result["canvas_w"], result["canvas_h"]) == (60, 32)


def test_mixed_aspect_items_rotate_and_keep_pre_rotation_names():
    result = shelf_pack(MIXED)
    assert (result["canvas_w"], result["canvas_h"]) == (40, 36)
    by_name = {p["name"]: p for p in result["placements"]}
    assert by_name["tall"]["rotated"] is True
    assert (by_name["tall"]["pw"], by_name["tall"]["ph"]) == (33, 7)
    assert by_name["wide"]["rotated"] is False


def test_empty_input_returns_a_zero_size_result():
    assert shelf_pack([]) == {"canvas_w": 0, "canvas_h": 0, "placements": []}


def test_zero_dimension_items_do_not_raise():
    # The NaN-aspect path: JS divides 0 by 0 here rather than throwing, so
    # a Python tuple-key sort (ZeroDivisionError) would be a new failure
    # mode JS never had.
    result = shelf_pack(ZERO_ONLY)
    assert (result["canvas_w"], result["canvas_h"]) == (0, 0)
    assert [p["name"] for p in result["placements"]] == ["z"]


def test_a_zero_dimension_item_alongside_a_real_one_still_packs_the_real_one():
    result = shelf_pack(ZERO_AND_REAL)
    assert (result["canvas_w"], result["canvas_h"]) == (12, 12)
    by_name = {p["name"]: p for p in result["placements"]}
    assert (by_name["real"]["x"], by_name["real"]["y"]) == (0, 0)
    assert (by_name["z"]["pw"], by_name["z"]["ph"]) == (0, 0)
