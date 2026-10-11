"""_find_first_fit against a brute-force reference: the first (row-major
origin, then rotation) whose exact overlap test passes. The banded search
must give exactly that answer at every band height."""
import numpy as np
import pytest

from atlas_toolkit.domain import packing
from atlas_toolkit.domain.packing import (
    _SCAN_STEP, _find_first_fit, _overlaps, _rotation_variants, _stamp_occupied,
)


def brute_force(variants, occupied, canvas_w, canvas_h, gap):
    for y in range(0, canvas_h, _SCAN_STEP):
        for x in range(0, canvas_w, _SCAN_STEP):
            for rot in variants:
                fits_canvas = x + rot["w"] <= canvas_w and y + rot["h"] <= canvas_h
                if fits_canvas and not _overlaps(occupied, gap, rot, x, y):
                    return {"rot": rot, "x": x, "y": y}
    return None


def mask(rng, w, h, kind):
    if kind == "empty":
        return bytes(w * h)
    if kind == "solid":
        return bytes([1]) * (w * h)
    return (rng.random(w * h) < 0.6).astype(np.uint8).tobytes()


def random_case(rng):
    gap = int(rng.choice([1, 2, 4, 7]))
    canvas_w, canvas_h = (int(v) for v in rng.integers(8, 61, size=2))
    occupied = np.zeros((canvas_h + 2 * gap, canvas_w + 2 * gap), dtype=np.uint8)
    for _ in range(int(rng.integers(0, 6))):
        w, h = (int(v) for v in rng.integers(2, 16, size=2))
        rot = _rotation_variants({"name": "o", "w": w, "h": h, "footprint": mask(rng, w, h, "random")}, gap)[0]
        if rot["w"] <= canvas_w and rot["h"] <= canvas_h:
            x = int(rng.integers(0, canvas_w - rot["w"] + 1))
            y = int(rng.integers(0, canvas_h - rot["h"] + 1))
            _stamp_occupied(occupied, gap, rot, x, y)
    kind = str(rng.choice(["empty", "solid", "random", "random"]))
    w, h = (int(v) for v in rng.integers(1, 21, size=2))
    variants = _rotation_variants({"name": "n", "w": w, "h": h, "footprint": mask(rng, w, h, kind)}, gap)
    return variants, occupied, canvas_w, canvas_h, gap


def summary(spot):
    return None if spot is None else (spot["rot"]["deg"], spot["x"], spot["y"])


@pytest.mark.parametrize("rows", [1, 2, 64])
def test_matches_brute_force_at_every_band_height(monkeypatch, rows):
    monkeypatch.setattr(packing, "_FIRST_FIT_CHUNK_ROWS", rows)
    rng = np.random.default_rng(20261012)
    seen = {"later_band": 0, "none": 0, "not_first_variant": 0, "variant_skipped": 0}
    band_px = rows * _SCAN_STEP
    for _ in range(600):
        variants, occupied, canvas_w, canvas_h, gap = random_case(rng)
        want = brute_force(variants, occupied, canvas_w, canvas_h, gap)
        got = _find_first_fit(variants, occupied, canvas_w, canvas_h, gap)
        assert summary(got) == summary(want)
        if want is None:
            seen["none"] += 1
        else:
            seen["later_band"] += want["y"] >= band_px
            seen["not_first_variant"] += want["rot"] is not variants[0]
        seen["variant_skipped"] += any(r["w"] > canvas_w or r["h"] > canvas_h for r in variants)
    # The corpus must really reach every branch, or a pass proves little.
    assert seen["none"] >= 20, seen
    assert seen["not_first_variant"] >= 20, seen
    assert seen["variant_skipped"] >= 20, seen
    if rows < 64:
        assert seen["later_band"] >= 20, seen


def tall_case():
    """Canvas 8x600 whose logical rows 0..499 are full: a 4x4 item first
    fits at y=500, inside the second 256 px band."""
    gap = 1
    occupied = np.zeros((600 + 2 * gap, 8 + 2 * gap), dtype=np.uint8)
    occupied[gap:gap + 500, :] = 1
    variants = _rotation_variants({"name": "n", "w": 4, "h": 4, "footprint": bytes([1]) * 16}, gap)
    return variants, occupied, 8, 600, gap


def test_answer_in_a_later_band_at_the_default_height():
    assert summary(_find_first_fit(*tall_case())) == (0, 0, 500)


def test_band_height_is_read_at_call_time(monkeypatch):
    calls = []
    real = packing._prune_by_samples
    monkeypatch.setattr(packing, "_prune_by_samples", lambda *args: calls.append(1) or real(*args))
    counts = {}
    for rows in (64, 1):
        monkeypatch.setattr(packing, "_FIRST_FIT_CHUNK_ROWS", rows)
        calls.clear()
        assert summary(_find_first_fit(*tall_case())) == (0, 0, 500)
        counts[rows] = len(calls)
    assert counts[1] > 10 * counts[64], counts


def test_full_canvas_returns_none():
    gap = 1
    occupied = np.ones((20 + 2 * gap, 20 + 2 * gap), dtype=np.uint8)
    variants = _rotation_variants({"name": "n", "w": 4, "h": 4, "footprint": bytes([1]) * 16}, gap)
    assert _find_first_fit(variants, occupied, 20, 20, gap) is None


def test_empty_mask_takes_the_first_origin_even_on_a_full_canvas():
    gap = 1
    occupied = np.ones((20 + 2 * gap, 20 + 2 * gap), dtype=np.uint8)
    variants = _rotation_variants({"name": "n", "w": 4, "h": 4, "footprint": bytes(16)}, gap)
    assert summary(_find_first_fit(variants, occupied, 20, 20, gap)) == (0, 0, 0)
