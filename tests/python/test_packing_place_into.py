"""nest_place_into: nest's first-fit on a canvas seeded with live pixels (spec 2026-10-31 §5.2)."""
import numpy as np
import pytest

from atlas_toolkit.domain.packing import (
    CanvasTooLargeError, MAX_CANVAS, nest_place_into, rotate_grid_for_pack,
)
from atlas_toolkit.raster.pillow_backend import PillowRasterBackend


def solid(name, w, h):
    return {"name": name, "w": w, "h": h, "footprint": bytes([1]) * (w * h)}


def test_an_over_cap_page_still_takes_adds_that_fit(monkeypatch):
    import atlas_toolkit.domain.packing as packing
    monkeypatch.setattr(packing, "MAX_CANVAS", 16)
    live = np.ones((20, 20), bool)
    live[0:8, 0:8] = False
    out = packing.nest_place_into([solid("N", 2, 2)], 20, 20, live, gap_distance=1)
    assert out["placements"][0]["x"] == 0 and out["placements"][0]["y"] == 0
    assert (out["canvas_w"], out["canvas_h"]) == (20, 20)


def test_live_cells_count_toward_the_cell_bound(monkeypatch):
    import atlas_toolkit.domain.packing as packing
    monkeypatch.setattr(packing, "MAX_CANVAS", 16)
    # 64 live + 225 item cells = 289 > 16*16; the item alone (225) is under it.
    with pytest.raises(CanvasTooLargeError) as caught:
        packing.nest_place_into([solid("N", 15, 15)], 8, 8, np.ones((8, 8), bool), gap_distance=1)
    assert (caught.value.width, caught.value.height) == (16, 19)  # the early-out's estimate


def triangle(name, uvs, w, h):
    raster = PillowRasterBackend()
    mask = raster.read_alpha_mask(raster.rasterize_mesh_mask(uvs, [0, 1, 2], w, h))
    return {"name": name, "w": w, "h": h, "footprint": np.asarray(mask, dtype=np.uint8).tobytes()}


UPPER_LEFT = [0, 0, 1, 0, 0, 1]
LOWER_RIGHT = [1, 0, 1, 1, 0, 1]


def test_places_into_free_space_keeping_the_gap():
    live = np.zeros((32, 64), bool)
    live[0:16, 0:32] = True
    out = nest_place_into([solid("N", 10, 10)], 64, 32, live, gap_distance=2)
    assert (out["canvas_w"], out["canvas_h"]) == (64, 32)
    assert out["placements"] == [{"name": "N", "x": 36, "y": 0, "pw": 10, "ph": 10, "rotate": 0}]


def test_gap_changes_the_spot():
    live = np.zeros((32, 64), bool)
    live[0:16, 0:32] = True
    out = nest_place_into([solid("N", 10, 10)], 64, 32, live, gap_distance=8)
    assert out["placements"][0]["x"] == 40


def test_grows_when_nothing_fits_and_trims_to_the_placement():
    live = np.ones((18, 30), bool)
    out = nest_place_into([solid("N", 10, 10)], 30, 18, live, gap_distance=2)
    # The search grows 30x18 -> 30x28 -> 30x38, but the output is trimmed to
    # the placement (y 20 + 10 = 30) and rounded on both axes.
    assert out["placements"] == [{"name": "N", "x": 0, "y": 20, "pw": 10, "ph": 10, "rotate": 0}]
    assert (out["canvas_w"], out["canvas_h"]) == (32, 32)


def test_rounds_both_axes_even_without_growth():
    live = np.zeros((18, 30), bool)
    live[0:8, 0:8] = True
    out = nest_place_into([solid("N", 4, 4)], 30, 18, live, gap_distance=1)
    assert out["placements"] == [{"name": "N", "x": 12, "y": 0, "pw": 4, "ph": 4, "rotate": 0}]
    assert (out["canvas_w"], out["canvas_h"]) == (32, 20)


def test_hull_footprint_takes_a_rotation_only_it_fits():
    live = np.ones((40, 40), bool)
    ys, xs = np.mgrid[0:24, 0:24]
    live[8:32, 8:32] = ~((xs + ys) >= 23)  # free: the lower-right triangle of a 24x24 box
    out = nest_place_into([triangle("T", UPPER_LEFT, 12, 12)], 40, 40, live, gap_distance=1)
    assert out["placements"] == [{"name": "T", "x": 16, "y": 16, "pw": 12, "ph": 12, "rotate": 180}]
    assert (out["canvas_w"], out["canvas_h"]) == (40, 40)


def test_hull_footprints_interlock():
    live = np.zeros((20, 20), bool)
    live[16:20, 16:20] = True
    items = [triangle("T1", UPPER_LEFT, 12, 12), triangle("T2", LOWER_RIGHT, 12, 12)]
    out = nest_place_into(items, 20, 20, live, gap_distance=1)
    assert (out["canvas_w"], out["canvas_h"]) == (20, 20)
    t1, t2 = out["placements"]
    assert (t1["x"], t1["y"], t2["x"], t2["y"]) == (0, 0, 4, 0)


def test_never_covers_a_live_cell():
    rng = np.random.default_rng(7)
    live = rng.random((48, 48)) < 0.05
    items = [solid(f"N{i}", 5, 3) for i in range(12)]
    out = nest_place_into(items, 48, 48, live, gap_distance=1)
    grid = np.zeros((out["canvas_h"], out["canvas_w"]), int)
    grid[:48, :48] = live
    for p in out["placements"]:
        grid[p["y"]:p["y"] + p["ph"], p["x"]:p["x"] + p["pw"]] += 1
    assert grid.max() == 1


def test_item_too_big_in_every_rotation_raises():
    with pytest.raises(CanvasTooLargeError):
        nest_place_into([solid("W", MAX_CANVAS + 1, 1)], 8, 8, np.zeros((8, 8), bool))


def test_live_shape_must_match_the_canvas():
    with pytest.raises(ValueError, match="live"):
        nest_place_into([solid("N", 2, 2)], 8, 8, np.zeros((4, 8), bool))


@pytest.mark.parametrize("degrees, k", [(0, 0), (90, 1), (180, 2), (270, -1)])
def test_rotate_grid_for_pack_matches_the_stored_orientation(degrees, k):
    grid = np.arange(6).reshape(2, 3)
    assert np.array_equal(rotate_grid_for_pack(grid, degrees), np.rot90(grid, k))


def test_a_page_past_the_cap_that_must_grow_raises_canvas_too_large():
    live = np.ones((4, MAX_CANVAS + 2), bool)
    with pytest.raises(CanvasTooLargeError):
        nest_place_into([solid("N", 2, 2)], MAX_CANVAS + 2, 4, live, gap_distance=2)


def test_growth_is_trimmed_on_the_width_too():
    out = nest_place_into([solid("N", 10, 10)], 10, 30, np.ones((30, 10), bool), gap_distance=2)
    assert out["placements"][0]["x"] == 12
    assert (out["canvas_w"], out["canvas_h"]) == (24, 32)


def test_rotate_grid_for_pack_never_returns_its_input():
    grid = np.zeros((2, 3), bool)
    for degrees in (0, 90, 180, 270):
        assert not np.shares_memory(rotate_grid_for_pack(grid, degrees), grid)


@pytest.mark.parametrize("w, h", [(0, 4), (4, 0)])
def test_non_positive_canvas_raises(w, h):
    with pytest.raises(ValueError, match="positive"):
        nest_place_into([solid("N", 1, 1)], w, h, np.zeros((h, w), bool))
