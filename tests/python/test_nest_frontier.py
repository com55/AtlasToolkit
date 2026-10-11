"""Domain/nest_frontier.py: valid, deterministic layouts that use cavities,
and a canvas limit that raises before the grid is allocated."""
import random

import numpy as np
import pytest

from atlas_toolkit.domain import nest_frontier
from atlas_toolkit.domain.nest_frontier import CanvasTooLargeError, nest_pack_frontier
from atlas_toolkit.domain.packing import _overlaps, _rotation_variants, _stamp_occupied


def item(name, w, h, cells=None):
    """`cells` is a set of (x, y) occupied cells; None means solid."""
    if cells is None:
        footprint = bytes([1]) * (w * h)
    else:
        footprint = bytes(1 if (x, y) in cells else 0 for y in range(h) for x in range(w))
    return {"name": name, "w": w, "h": h, "footprint": footprint}


def ring(name, size, wall):
    return item(name, size, size, {
        (x, y) for y in range(size) for x in range(size)
        if x < wall or y < wall or x >= size - wall or y >= size - wall
    })


def random_items(seed, count):
    rng = np.random.default_rng(seed)
    out = []
    for n in range(count):
        w, h = (int(v) for v in rng.integers(4, 31, size=2))
        cells = {(x, y) for y in range(h) for x in range(w) if rng.random() < 0.7}
        out.append(item(f"i{n:02d}", w, h, cells))
    return out


def assert_valid_layout(result, items, gap):
    """Re-stamp every placement in order: none may overlap an earlier one's
    gap-dilated footprint, and all must sit inside the reported canvas."""
    from atlas_toolkit.domain.packing import normalize_gap
    gap = normalize_gap(gap)
    by_name = {it["name"]: it for it in items}
    assert sorted(p["name"] for p in result["placements"]) == sorted(by_name)
    assert result["canvas_w"] % 4 == 0 and result["canvas_h"] % 4 == 0
    grid = np.zeros((result["canvas_h"] + 2 * gap, result["canvas_w"] + 2 * gap), dtype=np.uint8)
    for p in result["placements"]:
        rot = next(r for r in _rotation_variants(by_name[p["name"]], gap) if r["deg"] == p["rotate"])
        assert (rot["w"], rot["h"]) == (p["pw"], p["ph"])
        assert 0 <= p["x"] and p["x"] + rot["w"] <= result["canvas_w"]
        assert 0 <= p["y"] and p["y"] + rot["h"] <= result["canvas_h"]
        assert not _overlaps(grid, gap, rot, p["x"], p["y"]), p["name"]
        _stamp_occupied(grid, gap, rot, p["x"], p["y"])


def placement(result, name):
    return next(p for p in result["placements"] if p["name"] == name)


@pytest.mark.parametrize("gap", [1, 4, 7])
def test_random_layout_is_valid(gap):
    items = random_items(gap, 30)
    result = nest_pack_frontier(items, gap)
    assert_valid_layout(result, items, gap)
    assert len({p["rotate"] for p in result["placements"]}) >= 2


def test_layout_is_deterministic_across_runs_and_input_order():
    items = random_items(99, 25)
    first = nest_pack_frontier(items, 4)
    shuffled = items[:]
    random.Random(5).shuffle(shuffled)
    assert nest_pack_frontier(items, 4) == first
    assert nest_pack_frontier(shuffled, 4) == first


def test_small_item_goes_into_a_ring_cavity():
    items = [ring("ring", 40, 8), item("small", 12, 12)]
    result = nest_pack_frontier(items, 4, max_canvas=64)  # a missed cavity fails fast
    assert_valid_layout(result, items, 4)
    assert (result["canvas_w"], result["canvas_h"]) == (40, 40)
    small = placement(result, "small")
    assert (small["x"], small["y"]) == (12, 12)


def test_small_item_goes_into_a_u_notch():
    u_cell = {(x, y) for y in range(40) for x in range(40) if not (8 <= x < 32 and y < 32)}
    items = [item("u", 40, 40, u_cell), item("small", 12, 12)]
    result = nest_pack_frontier(items, 4, max_canvas=64)  # a missed cavity fails fast
    assert_valid_layout(result, items, 4)
    assert (result["canvas_w"], result["canvas_h"]) == (40, 40)
    small = placement(result, "small")
    assert (small["x"], small["y"]) == (12, 0)


def test_empty_input_and_bad_sizes():
    assert nest_pack_frontier([], 4) == {"canvas_w": 0, "canvas_h": 0, "placements": []}
    with pytest.raises(ValueError, match="non-positive"):
        nest_pack_frontier([item("z", 0, 5)], 4)


def test_single_item_canvas_is_the_item_rounded_up():
    result = nest_pack_frontier([item("a", 13, 7)], 4)
    assert (result["canvas_w"], result["canvas_h"]) == (16, 8)
    assert placement(result, "a")["x"] == 0 and placement(result, "a")["y"] == 0


def test_empty_footprint_item_is_placed():
    items = [item("solid", 20, 20), item("ghost", 6, 6, set())]
    result = nest_pack_frontier(items, 4)
    assert_valid_layout(result, items, 4)


def test_valid_layout_at_maximum_gap():
    items = random_items(7, 6)
    result = nest_pack_frontier(items, 256)
    assert_valid_layout(result, items, 256)


def test_oversized_item_raises_up_front(monkeypatch):
    shapes = []
    real = np.zeros
    monkeypatch.setattr(nest_frontier.np, "zeros", lambda shape, *a, **k: shapes.append(shape) or real(shape, *a, **k))
    with pytest.raises(CanvasTooLargeError) as raised:
        nest_pack_frontier([item("big", 41, 5)], 4, max_canvas=40)
    assert (raised.value.width, raised.value.height, raised.value.limit) == (41, 5, 40)
    assert shapes == []


def test_growth_past_the_limit_raises_before_allocating(monkeypatch):
    shapes = []
    real = np.zeros
    monkeypatch.setattr(nest_frontier.np, "zeros", lambda shape, *a, **k: shapes.append(shape) or real(shape, *a, **k))
    items = [item("a", 30, 30), item("b", 30, 30)]
    with pytest.raises(CanvasTooLargeError):
        nest_pack_frontier(items, 4, max_canvas=40)
    assert all(h <= 40 + 8 and w <= 40 + 8 for h, w in shapes), shapes


def test_limit_is_read_at_call_time(monkeypatch):
    monkeypatch.setattr(nest_frontier, "MAX_CANVAS", 40)
    with pytest.raises(CanvasTooLargeError):
        nest_pack_frontier([item("big", 41, 5)], 4)


def test_canvas_exactly_at_the_limit_is_allowed():
    result = nest_pack_frontier([item("a", 40, 10)], 4, max_canvas=40)
    assert (result["canvas_w"], result["canvas_h"]) == (40, 12)


def test_other_growth_direction_is_tried_before_raising():
    """After A (28x20) the canvas is 28x20 and B (8x20, cells only in its
    right half) does not fit. Growing down (28x40) is squarer, so it is
    preferred, but at max_canvas=39 it is over the limit; growing right
    (36x20) is within it and B fits at x=28."""
    b_cell = {(x, y) for y in range(20) for x in range(4, 8)}
    items = [item("a", 28, 20), item("b", 8, 20, b_cell)]
    result = nest_pack_frontier(items, 1, max_canvas=39)
    assert_valid_layout(result, items, 1)
    assert (result["canvas_w"], result["canvas_h"]) == (36, 20)
    b = placement(result, "b")
    assert (b["x"], b["y"], b["rotate"]) == (28, 0, 0)
