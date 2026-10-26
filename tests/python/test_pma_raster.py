"""Premultiplied raster primitives (spec 2026-10-26 §3.1, §3.9, §4, §5.9)."""
import numpy as np
import pytest
from PIL import Image

from atlas_toolkit.raster import lanczos
from atlas_toolkit.raster.lanczos import lanczos_resize_premultiplied, lanczos_resize_rgba
from atlas_toolkit.raster.pillow_backend import PillowRasterBackend
from atlas_toolkit.raster.premultiplied import attenuate, premultiplied_over
from tests.python.pma_cases import GLOW_IN, premultiplied_pattern


def handle(raster, arr):
    h, w = arr.shape[:2]
    return raster.from_pixels(w, h, np.ascontiguousarray(arr, np.uint8).tobytes())


def pixels(raster, h):
    w, ht = raster.size(h)
    return np.frombuffer(raster.get_pixels(h), np.uint8).reshape(ht, w, 4)


def test_premultiplied_over_matches_formula():
    src = np.array([[[10, 20, 30, 64]]], np.float64)
    under = np.array([[[100, 50, 25, 200]]], np.float64)
    want = src + under * (1 - 64 / 255)
    assert np.array_equal(premultiplied_over(src, under), want)
    assert np.array_equal(attenuate(under, src[..., 3]), under * (1 - 64 / 255))


def test_premultiplied_paste_onto_clear_is_a_byte_copy_including_alpha_zero_rgb():
    raster = PillowRasterBackend()
    src = premultiplied_pattern(6, 4, 1)
    src[1, 2] = GLOW_IN
    dest = raster.create(10, 8)
    raster.paste(dest, handle(raster, src), 3, 2, premultiplied=True)
    out = pixels(raster, dest)
    assert np.array_equal(out[2:6, 3:9], src)
    assert not out[:2].any() and not out[:, :3].any()


def test_premultiplied_paste_composites_over():
    raster = PillowRasterBackend()
    under = np.full((1, 1, 4), (100, 50, 25, 200), np.uint8)
    src = np.full((1, 1, 4), (10, 20, 30, 64), np.uint8)
    dest = handle(raster, under)
    raster.paste(dest, handle(raster, src), 0, 0, premultiplied=True)
    want = np.rint(premultiplied_over(src.astype(float), under.astype(float)))
    assert np.array_equal(pixels(raster, dest), want.astype(np.uint8))


def test_premultiplied_paste_saturates_instead_of_wrapping():
    # Additive (alpha 0) over a bright opaque pixel sums past 255; it must
    # clamp to 255, never wrap around in uint8.
    raster = PillowRasterBackend()
    under = np.full((1, 1, 4), (250, 250, 250, 255), np.uint8)
    src = np.full((1, 1, 4), GLOW_IN, np.uint8)
    dest = handle(raster, under)
    raster.paste(dest, handle(raster, src), 0, 0, premultiplied=True)
    assert pixels(raster, dest)[0, 0].tolist() == [255, 255, 255, 255]


def test_straight_paste_is_unchanged_and_still_drops_alpha_zero_rgb():
    raster = PillowRasterBackend()
    src = premultiplied_pattern(6, 4, 1)
    src[1, 2] = GLOW_IN
    a, b = raster.create(10, 8), raster.create(10, 8)
    raster.paste(a, handle(raster, src), 3, 2)
    raster.paste(b, handle(raster, src), 3, 2, premultiplied=False)
    assert np.array_equal(pixels(raster, a), pixels(raster, b))
    assert pixels(raster, a)[3, 5].tolist() == [0, 0, 0, 0]


def test_premultiplied_destination_in_scales_every_channel():
    raster = PillowRasterBackend()
    src = np.array([[(200, 100, 50, 255), (90, 60, 30, 0), (40, 40, 40, 128)]], np.uint8)
    mask = np.zeros((1, 3, 4), np.uint8)
    mask[..., 3] = [0, 255, 128]
    dest = handle(raster, src)
    raster.composite_destination_in(dest, handle(raster, mask), premultiplied=True)
    want = (src.astype(np.uint16) * mask[..., 3:4].astype(np.uint16)) // 255
    assert np.array_equal(pixels(raster, dest), want.astype(np.uint8))
    assert pixels(raster, dest)[0, 0].tolist() == [0, 0, 0, 0]
    assert pixels(raster, dest)[0, 1].tolist() == [90, 60, 30, 0]


def test_premultiplied_destination_in_alpha_equals_straight_for_every_pair():
    # Exhaustive over all 65,536 (alpha, mask) pairs: the pma path's alpha is
    # the straight path's (a*m)//255, so floor-vs-round drift cannot hide.
    raster = PillowRasterBackend()
    a, m = np.mgrid[0:256, 0:256].astype(np.uint8)
    src = np.stack([a, a, a, a], axis=-1)
    mask = np.zeros((256, 256, 4), np.uint8)
    mask[..., 3] = m
    straight, pma = handle(raster, src), handle(raster, src)
    raster.composite_destination_in(straight, handle(raster, mask))
    raster.composite_destination_in(pma, handle(raster, mask), premultiplied=True)
    assert np.array_equal(pixels(raster, pma)[..., 3], pixels(raster, straight)[..., 3])


def test_straight_destination_in_is_unchanged():
    raster = PillowRasterBackend()
    src = premultiplied_pattern(5, 3, 2)
    mask = np.zeros((3, 5, 4), np.uint8)
    mask[:, :2, 3] = 255
    a, b = handle(raster, src), handle(raster, src)
    raster.composite_destination_in(a, handle(raster, mask))
    raster.composite_destination_in(b, handle(raster, mask), premultiplied=False)
    assert np.array_equal(pixels(raster, a), pixels(raster, b))
    assert pixels(raster, a)[0, 4, :3].tolist() == src[0, 4, :3].tolist()   # straight keeps RGB


def test_straight_lanczos_still_byte_identical_after_refactor():
    src = premultiplied_pattern(7, 5, 3)
    raster = PillowRasterBackend()
    out = pixels(raster, raster.resize_lanczos(handle(raster, src), 13, 9))
    assert np.array_equal(out, lanczos_resize_rgba(src, 13, 9))


def _reference_premultiplied_lanczos(src, dst_w, dst_h):
    """Independent float64 reference (spec §4 (a)): reuses only the pinned
    kernels; the passes, normalisation and rounding are written here."""
    src = src.astype(np.float64)
    src_h, src_w = src.shape[:2]
    xi, xw = lanczos._build_kernels(src_w, dst_w)
    yi, yw = lanczos._build_kernels(src_h, dst_h)
    rows = np.zeros((src_h, dst_w, 4))
    for d in range(dst_w):
        for t in range(xi.shape[1]):
            rows[:, d] += src[:, xi[d, t]] * float(xw[d, t])
    out = np.zeros((dst_h, dst_w, 4))
    for d in range(dst_h):
        for t in range(yi.shape[1]):
            out[d] += rows[yi[d, t]] * float(yw[d, t])
    a = out[..., 3]
    over = a > 255
    out[over] *= (255 / a[over])[:, None]
    f = np.floor(out)
    return np.clip(np.where(out - f >= 0.5, f + 1, f), 0, 255).astype(np.uint8)


@pytest.mark.parametrize("size", [(13, 9), (4, 3), (16, 16)])
def test_premultiplied_lanczos_matches_independent_reference(size):
    src = premultiplied_pattern(7, 5, 4)
    src[2, 3] = GLOW_IN
    got = lanczos_resize_premultiplied(src, *size).astype(int)
    want = _reference_premultiplied_lanczos(src, *size).astype(int)
    # <= 1: the implementation keeps JS's float32 horizontal rows (parity).
    assert np.max(np.abs(got - want)) <= 1


def test_premultiplied_lanczos_keeps_additive_colour():
    src = np.zeros((4, 4, 4), np.uint8)
    src[...] = GLOW_IN
    out = lanczos_resize_premultiplied(src, 8, 8)
    assert np.all(out[..., 3] == 0)
    assert np.all(np.abs(out[..., :3].astype(int) - GLOW_IN[:3]) <= 1)


def test_premultiplied_lanczos_normalises_alpha_overshoot():
    # Hard opaque edge: Lanczos-3 rings the filtered alpha above 255.
    src = np.zeros((1, 8, 4), np.uint8)
    src[:, 4:] = (240, 120, 60, 255)
    v = lanczos._filter(src.astype(np.float64), 8, 1, 24, 1)
    over = v[..., 3] > 255
    assert over.any(), "fixture must overshoot"
    out = lanczos_resize_premultiplied(src, 24, 1)
    scaled = v[over] * (255 / v[over][:, 3:4])
    f = np.floor(scaled)
    want = np.clip(np.where(scaled - f >= 0.5, f + 1, f), 0, 255)
    assert np.array_equal(out[over], want.astype(np.uint8))
    assert np.all(out[over][:, 3] == 255)
    assert np.all(out[over][:, :3] <= out[over][:, 3:4])


def test_premultiplied_lanczos_halo_stays_inside_the_kernel_support():
    # Accepted ringing (spec §5.9): an output pixel whose taps all read
    # (0,0,0,0) source pixels is exactly (0,0,0,0).
    src = np.zeros((12, 12, 4), np.uint8)
    src[5:7, 5:7] = (200, 150, 100, 255)
    out = lanczos_resize_premultiplied(src, 36, 36)
    xi, _ = lanczos._build_kernels(12, 36)
    yi, _ = lanczos._build_kernels(12, 36)
    nonzero = src.any(axis=-1)
    for y in range(36):
        for x in range(36):
            if not nonzero[np.ix_(yi[y], xi[x])].any():
                assert out[y, x].tolist() == [0, 0, 0, 0], (x, y)


def test_premultiplied_lanczos_empty_and_same_size():
    assert lanczos_resize_premultiplied(np.zeros((0, 3, 4), np.uint8), 5, 4).shape == (4, 5, 4)
    src = premultiplied_pattern(3, 2, 1)
    out = lanczos_resize_premultiplied(src, 3, 2)
    assert out is not src and np.array_equal(out, src)


def test_backend_resize_selects_premultiplied_path():
    raster = PillowRasterBackend()
    src = premultiplied_pattern(5, 5, 6)
    src[2, 2] = GLOW_IN
    out = pixels(raster, raster.resize_lanczos(handle(raster, src), 9, 9, premultiplied=True))
    assert np.array_equal(out, lanczos_resize_premultiplied(src, 9, 9))
