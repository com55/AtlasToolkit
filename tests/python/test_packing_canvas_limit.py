"""MAX_CANVAS: both packers stay inside it or raise CanvasTooLargeError
before allocating, and every layout that already fits is unchanged."""
import numpy as np
import pytest

from atlas_toolkit.domain import packing
from atlas_toolkit.domain.packing import (
    CanvasTooLargeError, _overlaps, _rotation_variants, _stamp_occupied, nest_pack, shelf_pack,
)


def solid(name, w, h):
    return {"name": name, "w": w, "h": h, "footprint": bytes([1]) * (w * h)}


def random_items(seed, count):
    rng = np.random.default_rng(seed)
    return [solid(f"i{n:02d}", int(w), int(h)) for n, (w, h) in enumerate(rng.integers(4, 40, size=(count, 2)))]


def placement(result, name):
    return next(p for p in result["placements"] if p["name"] == name)


@pytest.fixture
def allocations(monkeypatch):
    """Every 2-D np.zeros shape packing.py allocates during the test."""
    shapes = []
    real = np.zeros
    monkeypatch.setattr(packing.np, "zeros", lambda shape, *a, **k: shapes.append(shape) or real(shape, *a, **k))
    return shapes


def test_default_limit():
    assert packing.MAX_CANVAS == 10000


@pytest.mark.parametrize("packer", ["nest", "shelf"])
def test_layouts_that_fit_are_unchanged(monkeypatch, packer):
    items = random_items(3, 25)
    pack = (lambda: nest_pack(items, 4)) if packer == "nest" else (lambda: shelf_pack(items))
    free = pack()
    monkeypatch.setattr(packing, "MAX_CANVAS", max(free["canvas_w"], free["canvas_h"]))
    assert pack() == free


def test_nest_oversized_item_raises_before_allocating(monkeypatch, allocations):
    monkeypatch.setattr(packing, "MAX_CANVAS", 40)
    with pytest.raises(CanvasTooLargeError) as raised:
        nest_pack([solid("big", 41, 5)], 4)
    assert (raised.value.width, raised.value.height, raised.value.limit) == (41, 5, 40)
    assert allocations == []


def test_nest_growth_never_allocates_past_the_limit(monkeypatch, allocations):
    monkeypatch.setattr(packing, "MAX_CANVAS", 39)
    with pytest.raises(CanvasTooLargeError):
        nest_pack([solid("a", 20, 20), solid("b", 20, 20)], 4)
    grids = [shape for shape in allocations if isinstance(shape, tuple) and len(shape) == 2]
    assert grids and all(h <= 39 + 8 and w <= 39 + 8 for h, w in grids), grids


def test_nest_clamps_the_last_growth_to_the_limit(monkeypatch):
    """Uncapped, two 20x20 squares at gap 4 end on a 40x60 canvas: growing
    by a whole item side overshoots. At a 44 px limit the growth is clamped
    to 40x44, where the second square still fits at y=24."""
    items = [solid("a", 20, 20), solid("b", 20, 20)]
    assert (nest_pack(items, 4)["canvas_w"], nest_pack(items, 4)["canvas_h"]) == (40, 60)  # control
    monkeypatch.setattr(packing, "MAX_CANVAS", 44)
    result = nest_pack(items, 4)
    assert (result["canvas_w"], result["canvas_h"]) == (40, 44)
    b = placement(result, "b")
    assert (b["x"], b["y"]) == (0, 24)


def test_nest_tries_the_other_direction_before_clamping(monkeypatch):
    """After A (28x20) nothing fits B (8x20, cells only in its right half).
    Growing down to 28x40 is squarer, so it is the usual choice, but at a
    39 px limit it is too tall; growing right to 36x20 fits B at x=28."""
    b_cells = bytes(1 if x >= 4 else 0 for y in range(20) for x in range(8))
    items = [solid("a", 28, 20), {"name": "b", "w": 8, "h": 20, "footprint": b_cells}]
    assert nest_pack(items, 1)["canvas_h"] == 40  # control: uncapped grows down
    monkeypatch.setattr(packing, "MAX_CANVAS", 39)
    result = nest_pack(items, 1)
    assert (result["canvas_w"], result["canvas_h"]) == (36, 20)
    b = placement(result, "b")
    assert (b["x"], b["y"], b["rotate"]) == (28, 0, 0)


def test_nest_canvas_exactly_at_the_limit_is_allowed(monkeypatch):
    monkeypatch.setattr(packing, "MAX_CANVAS", 40)
    result = nest_pack([solid("a", 40, 10)], 4)
    assert (result["canvas_w"], result["canvas_h"]) == (40, 12)


def test_nest_layout_under_a_limit_is_valid(monkeypatch):
    """Seed 2 packs uncapped to 172x156; capped at 156 it must still find a
    valid layout (156x156 when this test was written)."""
    items = random_items(2, 30)
    free = nest_pack(items, 4)
    assert (free["canvas_w"], free["canvas_h"]) == (172, 156)  # control
    limit = 156
    monkeypatch.setattr(packing, "MAX_CANVAS", limit)
    result = nest_pack(items, 4)
    assert result["canvas_w"] <= limit and result["canvas_h"] <= limit
    grid = np.zeros((result["canvas_h"] + 8, result["canvas_w"] + 8), dtype=np.uint8)
    by_name = {item["name"]: item for item in items}
    for p in result["placements"]:
        rot = next(r for r in _rotation_variants(by_name[p["name"]], 4) if r["deg"] == p["rotate"])
        assert p["x"] + rot["w"] <= result["canvas_w"] and p["y"] + rot["h"] <= result["canvas_h"]
        assert not _overlaps(grid, 4, rot, p["x"], p["y"]), p["name"]
        _stamp_occupied(grid, 4, rot, p["x"], p["y"])


def test_shelf_falls_back_to_a_candidate_that_fits(monkeypatch):
    items = random_items(5, 12)
    free = shelf_pack(items)
    limit = max(free["canvas_w"], free["canvas_h"]) - 4
    monkeypatch.setattr(packing, "MAX_CANVAS", limit)
    result = shelf_pack(items)
    assert result["canvas_w"] <= limit and result["canvas_h"] <= limit
    boxes = [(p["x"], p["y"], p["pw"], p["ph"]) for p in result["placements"]]
    assert len(boxes) == len(items)
    for i, (x, y, w, h) in enumerate(boxes):
        assert x + w <= result["canvas_w"] and y + h <= result["canvas_h"]
        for x2, y2, w2, h2 in boxes[i + 1:]:
            assert x + w <= x2 or x2 + w2 <= x or y + h <= y2 or y2 + h2 <= y


def test_shelf_raises_when_no_candidate_fits(monkeypatch):
    monkeypatch.setattr(packing, "MAX_CANVAS", 40)
    with pytest.raises(CanvasTooLargeError) as raised:
        shelf_pack([solid("big", 41, 5)])
    assert raised.value.limit == 40
