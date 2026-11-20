# SPDX-FileCopyrightText: 2026 com55
# SPDX-License-Identifier: Apache-2.0
# tests/python/test_raster_crop_rotate.py
from spine_atlas_toolkit.raster.pillow_backend import PillowRasterBackend


def _patterned(backend, w=21, h=21):
    """A w x h image where every pixel is different."""
    data = bytes(v for y in range(h) for x in range(w) for v in (x * 11 % 256, y * 7 % 256, (x + y) % 256, 255))
    return backend.from_pixels(w, h, data)


def test_rotate_image_zero_degrees_is_identity_sized():
    backend = PillowRasterBackend()
    handle = _patterned(backend)
    result = backend.rotate_image(handle, 0)
    assert backend.size(result) == backend.size(handle)


def test_rotate_image_swaps_dimensions_on_non_square_input():
    """rotate_image's degrees=90/270 branch passes (h, w) -- swapped -- to
    crop_rotate, exactly like crop_rotate's own is_swapped branch. The
    fixture used by every other test in this file is square (21x21), so
    NONE of them can tell a correct swap apart from a regression that
    dropped it (both produce a 21x21 result either way) -- this is the
    identical class of gap scrutinize round 1 already found and fixed for
    crop_rotate (crop_rotate_subrect_*), but the fix was never applied to
    rotate_image specifically, and rotate_image is one of the two names
    later stages call by exact name (base.py). No oracle/pixel data is
    needed here -- crop_rotate's own pixel correctness is already verified
    above; this test only needs to confirm rotate_image requests the RIGHT
    SIZE crop, which a dropped swap gets wrong before any pixel is even
    compared: a correct swap on a 10x15 source rotated 90/270 must return
    15x10, not 10x15."""
    backend = PillowRasterBackend()
    handle = _patterned(backend)
    nonsquare = backend.crop_rotate(handle, 3, 2, 10, 15, 0)  # real 10x15 crop
    assert backend.size(nonsquare) == (10, 15)
    assert backend.size(backend.rotate_image(nonsquare, 90)) == (15, 10)
    assert backend.size(backend.rotate_image(nonsquare, 270)) == (15, 10)
    assert backend.size(backend.rotate_image(nonsquare, 180)) == (10, 15)
