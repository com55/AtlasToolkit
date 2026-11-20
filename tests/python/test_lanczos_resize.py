# SPDX-FileCopyrightText: 2026 com55
# SPDX-License-Identifier: Apache-2.0
"""Lanczos-3 resize (the CPU path ported from the JS app)."""

import numpy as np
import pytest

from spine_atlas_toolkit.raster.lanczos import lanczos_resize_rgba


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
