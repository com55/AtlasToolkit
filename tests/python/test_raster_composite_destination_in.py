# SPDX-FileCopyrightText: 2026 com55
# SPDX-License-Identifier: Apache-2.0
# tests/python/test_raster_composite_destination_in.py
import pytest

from atlas_toolkit.raster.pillow_backend import PillowRasterBackend


def _solid(backend, w, h, rgba):
    from io import BytesIO
    from PIL import Image
    buf = BytesIO()
    Image.new("RGBA", (w, h), rgba).save(buf, format="PNG")
    return backend.decode_png(buf.getvalue())


def _patterned(backend, w, h):
    # Pixel (x,y) = (x%256, y%256, 200, 255) -- every pixel content-unique,
    # so masking-vs-not is observable at any coordinate, not just "is it
    # opaque" (this project's own CLAUDE.md: never a solid-colour fixture
    # for a masking test).
    from io import BytesIO
    from PIL import Image
    data = bytes(v for y in range(h) for x in range(w) for v in (x % 256, y % 256, 200, 255))
    buf = BytesIO()
    Image.frombytes("RGBA", (w, h), data).save(buf, format="PNG")
    return backend.decode_png(buf.getvalue())


def _mask_half(backend, w, h):
    # Opaque on the left half (x < w/2), transparent on the right --
    # built directly via rasterize_mesh_mask with a rectangle-as-two-
    # triangles covering only the left half.
    half = 0.5  # normalized u fraction -- opaque for pixel x < w/2, any w/h
    uvs = [0, 0, half, 0, 0, 1, half, 0, half, 1, 0, 1]
    triangles = [0, 1, 2, 3, 4, 5]
    return backend.rasterize_mesh_mask(uvs, triangles, w, h)


def test_masks_out_the_unmasked_half_leaves_masked_half_intact():
    backend = PillowRasterBackend()
    dest = _patterned(backend, 10, 10)
    mask = _mask_half(backend, 10, 10)
    backend.composite_destination_in(dest, mask)
    img = backend._images[dest]
    # Left half (masked-in) keeps its original patterned pixel + alpha.
    assert img.getpixel((2, 5)) == (2, 5, 200, 255)
    # Right half (masked-out) must be alpha == 0 -- the discriminator that
    # actually proves masking happened, not just "some pixels changed."
    assert img.getpixel((8, 5))[3] == 0


def test_fully_opaque_mask_is_a_no_op_on_rgb_and_alpha():
    backend = PillowRasterBackend()
    dest = _patterned(backend, 4, 4)
    mask = _solid(backend, 4, 4, (255, 255, 255, 255))
    before = backend._images[dest].copy()
    backend.composite_destination_in(dest, mask)
    assert list(backend._images[dest].getdata()) == list(before.getdata())


def test_fully_transparent_mask_zeroes_every_alpha_but_keeps_rgb():
    backend = PillowRasterBackend()
    dest = _patterned(backend, 4, 4)
    before_rgb = [(p[0], p[1], p[2]) for p in backend._images[dest].getdata()]
    mask = _solid(backend, 4, 4, (0, 0, 0, 0))
    backend.composite_destination_in(dest, mask)
    after = list(backend._images[dest].getdata())
    assert all(p[3] == 0 for p in after)
    assert [(p[0], p[1], p[2]) for p in after] == before_rgb


def test_src_rect_crops_the_mask_before_compositing():
    backend = PillowRasterBackend()
    dest = _patterned(backend, 4, 4)
    # A wider mask; the (2,0,4,4) sub-rect we crop out happens to be the
    # right half of a 6x4 half-opaque-left mask -- i.e. entirely
    # transparent -- so cropping must fully mask dest out.
    big_mask = _mask_half(backend, 8, 4)  # opaque for x<4, transparent x>=4
    backend.composite_destination_in(dest, big_mask, (4, 0, 4, 4))
    img = backend._images[dest]
    assert all(p[3] == 0 for p in img.getdata())


def test_src_rect_selecting_the_opaque_side_preserves_dest():
    backend = PillowRasterBackend()
    dest = _patterned(backend, 4, 4)
    before = backend._images[dest].copy()
    big_mask = _mask_half(backend, 8, 4)  # opaque for x<4
    backend.composite_destination_in(dest, big_mask, (0, 0, 4, 4))
    assert list(backend._images[dest].getdata()) == list(before.getdata())


def _reference_destination_in(dest, mask, premultiplied):
    """The whole-image formula (straight: alpha only; pma: every channel)."""
    import numpy as np

    m = mask[..., 3:4].astype(np.uint16)
    out = dest.astype(np.uint16).copy()
    if premultiplied:
        out = out * m // 255
    else:
        out[..., 3:4] = out[..., 3:4] * m // 255
    return out.astype(np.uint8)


@pytest.mark.parametrize("premultiplied", [False, True])
@pytest.mark.parametrize("src_rect", [None, (3, 2, 30, 20)])
def test_masking_only_the_non_zero_box_gives_the_whole_image_result(premultiplied, src_rect):
    """dest is clear except a patterned sprite window holding RGB under a
    mask-0 area (kept by straight), alpha-0 RGB (pma additive colour) and
    partial alpha; the result must equal masking the whole image."""
    import numpy as np
    from PIL import Image

    backend = PillowRasterBackend()
    w, h = 30, 20
    rng = np.random.default_rng(7)
    canvas = np.zeros((h, w, 4), np.uint8)
    canvas[5:14, 8:21] = rng.integers(0, 256, (9, 13, 4), dtype=np.uint8)
    canvas[6, 9] = (40, 50, 60, 0)  # RGB under alpha 0
    canvas[0, 29] = (0, 0, 7, 0)  # a lone non-zero RGB byte widens the box
    dest = backend._store(Image.fromarray(canvas, "RGBA"))
    mask_w, mask_h = (w, h) if src_rect is None else (36, 25)
    full = backend.rasterize_mesh_mask([0.0, 0.0, 0.7, 0.0, 0.1, 1.0, 0.9, 0.2, 1.0, 1.0], [0, 1, 2, 1, 3, 4], mask_w, mask_h)
    mask_rgba = np.asarray(backend.export(full))
    x, y = (0, 0) if src_rect is None else src_rect[:2]
    window = mask_rgba[y:y + h, x:x + w]
    assert (window[5:14, 8:21, 3] == 0).any() and (window[5:14, 8:21, 3] == 255).any()
    backend.composite_destination_in(dest, full, src_rect, premultiplied=premultiplied)
    got = np.asarray(backend.export(dest))
    assert np.array_equal(got, _reference_destination_in(canvas, window, premultiplied))


def test_an_all_clear_dest_stays_clear_and_a_size_mismatch_still_raises():
    import numpy as np
    from PIL import Image

    backend = PillowRasterBackend()
    dest = backend.create(6, 4)
    backend.composite_destination_in(dest, backend.rasterize_mesh_mask([0, 0, 1, 0, 0, 1], [0, 1, 2], 6, 4))
    assert not np.asarray(backend.export(dest)).any()
    with pytest.raises(ValueError):
        backend.composite_destination_in(dest, backend.rasterize_mesh_mask([0, 0, 1, 0, 0, 1], [0, 1, 2], 5, 4))
    other = backend._store(Image.new("RGBA", (6, 4), (1, 2, 3, 4)))
    with pytest.raises(ValueError):  # a larger mask used to be truncated silently
        backend.composite_destination_in(other, backend.rasterize_mesh_mask([0, 0, 1, 0, 0, 1], [0, 1, 2], 7, 5))
    with pytest.raises(ValueError):
        backend.composite_destination_in(other, backend.rasterize_mesh_mask([0, 0, 1, 0, 0, 1], [0, 1, 2], 6, 5), premultiplied=True)
