"""shelf_pack padding: pixels between items, none at the edges (spec 2026-10-22 §4.2)."""
import random

import pytest

from atlas_toolkit.domain.packing import _pack_with_width, shelf_pack


def corpus(seed, count=25):
    rng = random.Random(seed)
    return [{"name": f"i{n}", "w": rng.randint(1, 40), "h": rng.randint(1, 40)} for n in range(count)]


def separated(a, b, padding):
    return (
        a["x"] + a["pw"] + padding <= b["x"] or b["x"] + b["pw"] + padding <= a["x"]
        or a["y"] + a["ph"] + padding <= b["y"] or b["y"] + b["ph"] + padding <= a["y"]
    )


@pytest.mark.parametrize("allow_rotate", [False, True])
@pytest.mark.parametrize("padding", [1, 2, 5])
@pytest.mark.parametrize("seed", range(5))
def test_pack_with_width_spaces_items_and_adds_no_trailing_padding(seed, padding, allow_rotate):
    items = corpus(seed)
    used_w, height, placements = _pack_with_width(items, 90, allow_rotate, padding)
    assert len(placements) == len(items)
    assert len({p["y"] for p in placements}) > 1  # several shelves, so the wrap is exercised
    for index, a in enumerate(placements):
        for b in placements[index + 1:]:
            assert separated(a, b, padding), (a, b)
    assert used_w == max(p["x"] + p["pw"] for p in placements)
    assert height == max(p["y"] + p["ph"] for p in placements)


@pytest.mark.parametrize("padding", [1, 2, 5])
def test_shelf_pack_spaces_items(padding):
    for seed in range(5):
        packed = shelf_pack(corpus(seed), padding=padding)
        placements = packed["placements"]
        for index, a in enumerate(placements):
            for b in placements[index + 1:]:
                assert separated(a, b, padding), (a, b)
        raw_w = max(p["x"] + p["pw"] for p in placements)
        raw_h = max(p["y"] + p["ph"] for p in placements)
        assert raw_w <= packed["canvas_w"] < raw_w + 4
        assert raw_h <= packed["canvas_h"] < raw_h + 4
