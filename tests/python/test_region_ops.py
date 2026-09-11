from atlas_toolkit.domain.region_ops import (
    round_half_even,
    round_up_to_multiple,
    overlay_rect,
)


# Ground-truth cross-checked against `python3 -c "print(round(x))"` for each
# value (mirrors tests/core-region-ops.test.js's own cross-check note):
# 2.5->2, 3.5->4, 0.5->0, 1.5->2, 10.5->10, 7.5->8, 2.4->2, 2.6->3.

def test_round_half_even_even_base_tie_rounds_down():
    assert round_half_even(2.5) == 2
    assert round_half_even(0.5) == 0
    assert round_half_even(10.5) == 10


def test_round_half_even_odd_base_tie_rounds_up():
    assert round_half_even(3.5) == 4
    assert round_half_even(1.5) == 2
    assert round_half_even(7.5) == 8


def test_round_half_even_non_tie_matches_plain_rounding():
    assert round_half_even(2.4) == 2
    assert round_half_even(2.6) == 3
    assert round_half_even(5) == 5
    assert round_half_even(0) == 0
    assert round_half_even(9.9) == 10


def test_round_up_to_multiple_default_4():
    assert round_up_to_multiple(0) == 0
    assert round_up_to_multiple(-3) == 0
    assert round_up_to_multiple(4) == 4
    assert round_up_to_multiple(5) == 8
    assert round_up_to_multiple(1) == 4


def test_round_up_to_multiple_custom_multiple():
    assert round_up_to_multiple(10, multiple=8) == 16
    assert round_up_to_multiple(16, multiple=8) == 16


def test_overlay_rect_swaps_wh_only_for_rotate_90_and_270():
    bounds = [3, 4, 20, 8]
    assert overlay_rect(bounds, 0) == [3, 4, 20, 8]
    assert overlay_rect(bounds, 180) == [3, 4, 20, 8]
    assert overlay_rect(bounds, 90) == [3, 4, 8, 20]
    assert overlay_rect(bounds, 270) == [3, 4, 8, 20]
