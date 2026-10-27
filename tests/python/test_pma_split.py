"""Exact pma composite split (spec 2026-10-26 §3.5, §5.6-§5.7)."""
import numpy as np
import pytest
from PIL import Image

from atlas_toolkit.engine.composite_split import split_composite
from atlas_toolkit.raster.pillow_backend import PillowRasterBackend
from atlas_toolkit.raster.premultiplied import premultiplied_over

QUAD = [0, 1, 2, 0, 2, 3]


def full_meta(w, h):
    return {"offsets": None, "bounds": [0, 0, w, h], "split": None, "pad": None}


def cut(arr, names, geometry=None, pma=None):
    raster = PillowRasterBackend()
    h, w = arr.shape[:2]
    source = raster.adopt(Image.fromarray(np.ascontiguousarray(arr), "RGBA"))
    metas = {n: full_meta(w, h) for n in names}
    geometry = geometry or {n: None for n in names}
    pieces = split_composite(raster, source, names, metas, geometry, set(names) if pma is None else pma)
    return {n: np.asarray(raster.export(p.image)).copy() for n, p in pieces.items()}


def restack(pieces, names):
    out = np.zeros(next(iter(pieces.values())).shape, np.float64)
    for n in reversed(names):
        out = premultiplied_over(pieces[n], out)
    return np.clip(np.rint(out), 0, 255).astype(np.uint8)


def every_c_and_a():
    """256 x 256: column = C (per channel offsets), row = A."""
    ys, xs = np.mgrid[0:256, 0:256]
    arr = np.zeros((256, 256, 4), np.uint8)
    arr[..., 0] = xs
    arr[..., 1] = 255 - xs
    arr[..., 2] = (xs * 7) % 256
    arr[..., 3] = ys
    return arr


@pytest.mark.parametrize("k", range(1, 13))
def test_every_c_and_a_restacks_exactly(k):
    # Through split_composite itself (spec §5.7), K = 1..12 full-image holders.
    arr = every_c_and_a()
    names = [f"h{i}" for i in range(k)]
    pieces = cut(arr, names)
    assert np.array_equal(restack(pieces, names), arr)
    valid = np.all(arr[..., :3] <= arr[..., 3:4], axis=-1)
    for n in names:
        assert not np.any(pieces[n][valid][:, :3] > pieces[n][valid][:, 3:4]), n


def test_alpha_zero_colour_goes_to_the_topmost_holder_only():
    arr = np.zeros((1, 2, 4), np.uint8)
    arr[0, 0] = (90, 60, 30, 0)
    pieces = cut(arr, ["T", "M", "B"])
    assert pieces["T"][0, 0].tolist() == [90, 60, 30, 0]
    assert not pieces["M"].any() and not pieces["B"].any()


def test_pieces_are_cleared_outside_their_holder():
    # C1: T's hull is the left half; an alpha-0 RGB pixel in T's rect but
    # outside its hull must not ride along in T's piece.
    arr = np.zeros((2, 8, 4), np.uint8)
    arr[...] = (40, 40, 40, 128)
    arr[0, 6] = (70, 50, 20, 0)
    geometry = {"T": {"uvs": [0, 0, 0.5, 0, 0.5, 1, 0, 1], "triangles": QUAD}, "B": None}
    pieces = cut(arr, ["T", "B"], geometry)
    assert not pieces["T"][:, 4:].any()
    assert pieces["B"][0, 6].tolist() == [70, 50, 20, 0]     # B owns it and is topmost there
    assert np.array_equal(restack(pieces, ["T", "B"]), arr)


def test_mixed_batch_keeps_share_scaling_and_straight_pieces():
    arr = np.zeros((1, 3, 4), np.uint8)
    arr[0] = [(64, 32, 16, 128), (90, 60, 30, 0), (200, 100, 50, 255)]
    pieces = cut(arr, ["T", "B"], pma={"T"})
    assert pieces["B"][0, 1].tolist() == [0, 0, 0, 0]          # straight piece: alpha-0 zeroed
    assert pieces["T"][0, 1].tolist() == [90, 60, 30, 0]       # pma topmost gets alpha-0 colour


def test_mixed_batch_drops_alpha_zero_colour_under_a_straight_top():
    # The colour goes only to the topmost holder, and only when that holder
    # is pma: a pma holder below a straight top does not pick it up.
    arr = np.zeros((1, 3, 4), np.uint8)
    arr[0] = [(64, 32, 16, 128), (90, 60, 30, 0), (200, 100, 50, 255)]
    pieces = cut(arr, ["T", "B"], pma={"B"})
    assert pieces["T"][0, 1].tolist() == [0, 0, 0, 0]
    assert pieces["B"][0, 1].tolist() == [0, 0, 0, 0]


@pytest.mark.parametrize("pma", [{"T", "B"}, {"T"}])
def test_a_pixel_no_holder_covers_is_dropped(pma):
    # C1 where no hull covers a pixel: its alpha-0 colour must not fall to
    # the first layer, on the exact path and the mixed one.
    arr = np.zeros((2, 8, 4), np.uint8)
    arr[...] = (40, 40, 40, 128)
    arr[0, 6] = (70, 50, 20, 0)
    half = {"uvs": [0, 0, 0.5, 0, 0.5, 1, 0, 1], "triangles": QUAD}
    pieces = cut(arr, ["T", "B"], {"T": half, "B": half}, pma=pma)
    assert not pieces["T"][:, 4:].any() and not pieces["B"][:, 4:].any()


def test_alpha_one_stays_with_the_top_holder():
    # As in the straight split (domain/alpha_split.py), alpha 1 has no pair:
    # the top holder is the residual holder and keeps the whole pixel.
    arr = np.zeros((1, 1, 4), np.uint8)
    arr[0, 0] = (1, 1, 0, 1)
    pieces = cut(arr, ["T", "M", "B"])
    assert pieces["T"][0, 0].tolist() == [1, 1, 0, 1]
    assert not pieces["M"].any() and not pieces["B"].any()
