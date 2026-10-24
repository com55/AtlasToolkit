"""Lanczos-3 resize: byte parity with www/js/lanczos-resize.js's CPU path."""
import random

import numpy as np
import pytest

from atlas_toolkit.raster.lanczos import lanczos_resize_rgba


def _source(rng, w, h, transparent_rate):
    """Patterned straight RGBA: colours vary per pixel, some alpha 0, some partial."""
    data = []
    for y in range(h):
        for x in range(w):
            roll = rng.random()
            alpha = 0 if roll < transparent_rate else 255 if roll < 0.75 else rng.randint(1, 254)
            data += [(x * 37 + y * 11) % 256, rng.randint(0, 255), (x * y * 7) % 256, alpha]
    return data


SIZES = [
    ((5, 3), (12, 7)), ((3, 5), (4, 11)), ((8, 6), (5, 9)), ((9, 4), (3, 2)),
    ((1, 1), (4, 3)), ((2, 7), (9, 7)), ((6, 6), (6, 6)), ((13, 10), (31, 17)),
    ((4, 2), (2, 4)), ((7, 7), (16, 3)),
]


def test_byte_identical_to_js_cpu_path(run_preview_oracle):
    rng = random.Random(9031)
    cases = []
    for (sw, sh), (dw, dh) in SIZES:
        for rate in (0.0, 0.3, 1.0):
            cases.append((_source(rng, sw, sh, rate), sw, sh, dw, dh))
    # Larger random sizes: the float32 row storage only flips a handful of
    # values, so a small corpus alone cannot tell it apart from float64.
    for _ in range(60):
        sw, sh, dw, dh = (rng.randint(1, 48), rng.randint(1, 48), rng.randint(1, 96), rng.randint(1, 96))
        cases.append((_source(rng, sw, sh, rng.choice([0, 0.2, 0.5])), sw, sh, dw, dh))
    expected = run_preview_oracle([("lanczosResizeRGBA", list(case)) for case in cases])
    for (src, sw, sh, dw, dh), want in zip(cases, expected):
        got = lanczos_resize_rgba(np.array(src, dtype=np.uint8).reshape(sh, sw, 4), dw, dh)
        assert got.shape == (dh, dw, 4)
        assert got.ravel().tolist() == want, ((sw, sh), (dw, dh))


def test_transparent_neighbours_stay_transparent_and_do_not_darken():
    src = np.zeros((4, 8, 4), dtype=np.uint8)
    src[:, :2] = (200, 100, 50, 255)
    out = lanczos_resize_rgba(src, 16, 8)
    assert (out[:, -4:, 3] == 0).all()
    opaque = out[..., 3] == 255
    assert opaque.any()
    assert (out[opaque][:, 0] >= 190).all()


def test_same_size_is_a_copy():
    src = np.arange(2 * 3 * 4, dtype=np.uint8).reshape(2, 3, 4)
    out = lanczos_resize_rgba(src, 3, 2)
    assert out is not src and (out == src).all()


@pytest.mark.parametrize("shape", [(0, 0), (0, 3), (3, 0)])
def test_empty_source_resizes_to_transparent(shape):
    """A zero-size region has no pixels to scale: the result is a fully
    transparent frame, not a ZeroDivisionError."""
    out = lanczos_resize_rgba(np.zeros((*shape, 4), dtype=np.uint8), 5, 4)
    assert out.shape == (4, 5, 4) and out.dtype == np.uint8
    assert (out == 0).all()
