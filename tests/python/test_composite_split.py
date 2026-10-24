"""engine/composite_split.py: scaled rects and mesh-owned pieces (spec 2026-10-13 §3.2-3.4)."""
import numpy as np
from PIL import Image

from atlas_toolkit.domain.alpha_split import SPLIT
from atlas_toolkit.engine.composite_split import Piece, scaled_rect, split_composite
from atlas_toolkit.raster.pillow_backend import PillowRasterBackend

QUAD = [0, 1, 2, 0, 2, 3]


def rect_mesh(u0, v0, u1, v1):
    return {"uvs": [u0, v0, u1, v0, u1, v1, u0, v1], "triangles": QUAD}


def meta(offsets, w, h):
    return {"offsets": offsets, "bounds": [0, 0, w, h], "split": None, "pad": None}


def test_scaled_rect_identity_at_scale_one():
    # canvas 32x16, sprite 16x10 at left 0, bottom 6 -> top 0
    assert scaled_rect(meta([0, 6, 32, 16], 16, 10), 32, 16) == (0, 0, 16, 10)


def test_scaled_rect_scales_each_axis_on_its_own():
    # canvas 24x12 -> image 32x16 (x and y both 4/3); left 3, top 3, 15x9
    assert scaled_rect(meta([3, 0, 24, 12], 15, 9), 32, 16) == (4, 4, 20, 12)
    # non-uniform: canvas 20x10 -> image 40x15 (x2, y1.5); left 5, top 2, 10x6
    assert scaled_rect(meta([5, 2, 20, 10], 10, 6), 40, 15) == (10, 3, 20, 9)


def test_scaled_rect_clamps_into_the_image_and_is_never_empty():
    # left == canvas width: x0 clamps to W-1 and the rect is 1 wide
    assert scaled_rect(meta([32, 0, 32, 16], 4, 4), 32, 16)[0::2] == (31, 1)
    # malformed offsets (bottom + h > canvas): top < 0 clamps to 0
    x0, y0, w, h = scaled_rect(meta([0, 14, 32, 16], 4, 4), 32, 16)
    assert y0 == 0 and h >= 1


def test_scaled_rect_without_offsets_uses_the_sprite_as_canvas():
    assert scaled_rect(meta(None, 8, 4), 16, 8) == (0, 0, 16, 8)


def _image(arr):
    return Image.fromarray(np.ascontiguousarray(arr), "RGBA")


def _cut(arr, survivors, metas, geometry, pma_names=frozenset()):
    raster = PillowRasterBackend()
    source = raster.adopt(_image(arr))
    pieces = split_composite(raster, source, survivors, metas, geometry, pma_names)
    return {n: (p.offsets, np.asarray(raster.export(p.image))) for n, p in pieces.items()}


def test_two_holders_share_semi_alpha_and_each_keeps_only_its_hull():
    # 8x4 canvas. TOP hull x0-6, BOT hull x2-8; both rects are the whole canvas.
    arr = np.zeros((4, 8, 4), np.uint8)
    arr[..., 0] = np.arange(8)[None, :] * 30
    arr[..., 3] = 255
    arr[1, 3, 3] = 128                       # semi pixel inside both hulls
    arr[1, 0, 3] = 128                       # semi pixel only in TOP's hull
    metas = {"TOP": meta([0, 0, 8, 4], 8, 4), "BOT": meta([0, 0, 8, 4], 8, 4)}
    geometry = {"TOP": rect_mesh(0, 0, 0.75, 1), "BOT": rect_mesh(0.25, 0, 1, 1)}
    out = _cut(arr, ["TOP", "BOT"], metas, geometry)
    top, bot = out["TOP"][1], out["BOT"][1]
    t, b = SPLIT[128]
    assert (top[1, 3, 3], bot[1, 3, 3]) == (t, b)
    assert top[1, 3, 0] == bot[1, 3, 0] == arr[1, 3, 0]
    assert (top[1, 0, 3], bot[1, 0, 3]) == (128, 0)
    assert np.all(top[:, 6:, 3] == 0)        # outside TOP's hull: cleared at cut time
    assert np.all(bot[:, :2, 3] == 0)
    assert np.all(top[:, 2:6, 3][arr[:, 2:6, 3] == 255] == 255)
    assert np.all(bot[:, 2:6, 3][arr[:, 2:6, 3] == 255] == 255)


def test_piece_offsets_put_the_canvas_at_the_image_size():
    arr = np.full((16, 32, 4), 255, np.uint8)
    metas = {"E": meta([3, 0, 24, 12], 15, 9)}
    out = _cut(arr, ["E"], metas, {"E": rect_mesh(0.25, 0.25, 0.75, 1)})
    offsets, pixels = out["E"]
    assert offsets == [4, 0, 32, 16]                 # x0=4, bottom = 16 - y1(16)
    assert pixels.shape[:2] == (12, 20)              # rect (4, 4, 20, 12)
    # hull at 32x16 = x8-24, y4-16 -> piece-local x4-20, all rows
    assert np.all(pixels[:, 4:20, 3] == 255) and np.all(pixels[:, :4, 3] == 0)


def test_empty_piece_keeps_its_full_rect_transparent():
    arr = np.zeros((16, 32, 4), np.uint8)
    arr[:, :8, 3] = 255                              # opaque only where E's hull is not
    metas = {"E": meta([3, 0, 24, 12], 15, 9)}
    out = _cut(arr, ["E"], metas, {"E": rect_mesh(0.25, 0.25, 0.75, 1)})
    offsets, pixels = out["E"]
    assert pixels.shape[:2] == (12, 20) and not pixels[..., 3].any()
    assert offsets == [4, 0, 32, 16]


def test_region_without_geometry_holds_its_whole_rect():
    # TOP has a hull x0-6; BOT has no mesh, so it holds its whole rect x2-8
    # (here the whole canvas) and the semi pixel both hold is still split.
    arr = np.zeros((4, 8, 4), np.uint8)
    arr[..., 0] = np.arange(8)[None, :] * 30
    arr[..., 3] = 255
    arr[1, 3, 3] = 128                       # semi pixel in TOP's hull and BOT's rect
    arr[2, 7, 3] = 128                       # semi pixel only in BOT's rect
    metas = {"TOP": meta([0, 0, 8, 4], 8, 4), "BOT": meta([2, 0, 8, 4], 6, 4)}
    out = _cut(arr, ["TOP", "BOT"], metas, {"TOP": rect_mesh(0, 0, 0.75, 1), "BOT": None})
    top, bot = out["TOP"][1], out["BOT"][1]
    assert bot.shape[:2] == (4, 6) and out["BOT"][0] == [2, 0, 8, 4]
    t, b = SPLIT[128]
    assert (top[1, 3, 3], bot[1, 1, 3]) == (t, b)
    assert bot[2, 5, 3] == 128
    assert np.all(bot[..., 3][arr[:, 2:, 3] == 255] == 255)   # every opaque pixel of its rect
    assert np.all(top[:, 6:, 3] == 0)


def _two_holders_with_one_semi_pixel():
    # 4x2 canvas, T on top, both holders cover all of it. Pixel (x1, y0) is
    # valid premultiplied (RGB <= alpha): (64, 32, 16) at alpha 128.
    arr = np.zeros((2, 4, 4), np.uint8)
    arr[...] = (200, 100, 50, 255)
    arr[0, 1] = (64, 32, 16, 128)
    metas = {"T": meta(None, 4, 2), "B": meta(None, 4, 2)}
    return arr, metas, {"T": None, "B": None}


def test_pma_pieces_keep_rgb_times_their_alpha_share():
    arr, metas, geometry = _two_holders_with_one_semi_pixel()
    straight = _cut(arr, ["T", "B"], metas, geometry)
    pma = _cut(arr, ["T", "B"], metas, geometry, pma_names={"T", "B"})
    top, bottom = SPLIT[128]
    assert straight["T"][1][0, 1].tolist() == [64, 32, 16, top]
    assert straight["B"][1][0, 1].tolist() == [64, 32, 16, bottom]
    assert pma["T"][1][0, 1].tolist() == [int(np.rint(v * top / 128)) for v in (64, 32, 16)] + [top]
    assert pma["B"][1][0, 1].tolist() == [int(np.rint(v * bottom / 128)) for v in (64, 32, 16)] + [bottom]
    assert pma["T"][1][1, 3].tolist() == [200, 100, 50, 255]      # opaque: share 1, unscaled
    t, b = pma["T"][1][0, 1].astype(float), pma["B"][1][0, 1].astype(float)
    out = t + b * (1 - t[3] / 255)                                 # premultiplied over, bottom first
    assert np.all(np.abs(out - [64, 32, 16, 128]) <= 1)


def test_pma_scaling_follows_each_survivors_own_page():
    arr, metas, geometry = _two_holders_with_one_semi_pixel()
    pieces = _cut(arr, ["T", "B"], metas, geometry, pma_names={"B"})
    top, bottom = SPLIT[128]
    assert pieces["T"][1][0, 1].tolist() == [64, 32, 16, top]
    assert pieces["B"][1][0, 1].tolist() == [int(np.rint(v * bottom / 128)) for v in (64, 32, 16)] + [bottom]


def test_pma_three_holders_scale_rgb_by_share_and_restack_within_two():
    # Every semi alpha on a pma page, three whole-canvas holders (T on top):
    # each piece is RGB * its share, stays valid premultiplied (RGB <= alpha),
    # and premultiplied over (bottom first, rounded once) gives back the input.
    names = ["T", "M", "B"]
    metas = {n: meta(None, 4, 2) for n in names}
    for colour in [(64, 32, 16), (128, 0, 1), (255, 255, 255), (3, 90, 128)]:
        for alpha in range(2, 255):
            rgb = [min(v, alpha) for v in colour]
            arr = np.zeros((2, 4, 4), np.uint8)
            arr[...] = (200, 100, 50, 255)
            arr[0, 1] = (*rgb, alpha)
            pieces = _cut(arr, names, metas, {n: None for n in names}, pma_names=set(names))
            out = np.zeros(4)
            for name in reversed(names):
                px = pieces[name][1][0, 1].astype(float)
                assert px[:3].tolist() == [np.rint(v * (px[3] / alpha)) for v in rgb]
                assert np.all(px[:3] <= px[3])
                out = px + out * (1 - px[3] / 255)
            assert np.all(np.abs(np.rint(out) - [*rgb, alpha]) <= 2), (rgb, alpha)
