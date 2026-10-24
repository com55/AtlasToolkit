"""Forced Resizing in the multi-region preview composite (renderPreviewCanvases)."""
import io

import numpy as np
import pytest
from PIL import Image

from atlas_toolkit.document import AtlasDocument
from atlas_toolkit.engine.extraction import AtlasProcessor
from atlas_toolkit.engine.session import AtlasSession
from atlas_toolkit.raster.lanczos import lanczos_resize_rgba
from atlas_toolkit.raster.pillow_backend import PillowRasterBackend

# 12x6 page. "big" (0,0,8,4) has a transparent right half so whatever is
# underneath shows through; "small" (8,0,2,4) and "wide" (0,4,10,2) are opaque
# and patterned so a wrong source rect or a wrong resize shows up.
PAGE_W, PAGE_H = 12, 6


def _page_pixels():
    px = np.zeros((PAGE_H, PAGE_W, 4), dtype=np.uint8)
    for y in range(PAGE_H):
        for x in range(PAGE_W):
            px[y, x] = (x * 20, y * 40, 90 + x * y, 255)
    px[0:4, 4:8, 3] = 0
    px[0:4, 6:8, 3] = 0
    px[1, 2, 3] = 128
    return px


ATLAS = (
    f"p.png\nsize: {PAGE_W},{PAGE_H}\n"
    "big\n bounds: 0,0,8,4\n"
    "small\n bounds: 8,0,2,4\n"
    "wide\n bounds: 0,4,10,2\n"
    "tall\n bounds: 10,0,2,4\n"
)


def _png(px):
    buf = io.BytesIO()
    Image.frombytes("RGBA", (px.shape[1], px.shape[0]), px.tobytes()).save(buf, format="PNG")
    return buf.getvalue()


def _decode(data):
    return np.asarray(Image.open(io.BytesIO(data)).convert("RGBA"))


def _over(layers, width, height):
    """Independent source-over stack: layers bottom-first, each already frame-sized."""
    out = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    for layer in layers:
        out = Image.alpha_composite(out, Image.frombytes("RGBA", (width, height), layer.tobytes()))
    return np.asarray(out)


def _session():
    raster = PillowRasterBackend()
    return AtlasSession.load(ATLAS, {"p.png": _png(_page_pixels())}, raster), raster


def test_smaller_region_is_lanczos_resized_under_the_earlier_one():
    session, _ = _session()
    page = _page_pixels()
    big, small = page[0:4, 0:8], page[0:4, 8:10]
    got = _decode(session.get_preview_png(["big", "small"], force_resize=True))
    want = _over([lanczos_resize_rgba(small, 8, 4), big], 8, 4)
    assert got.shape == (4, 8, 4)
    assert (got == want).all()
    # The resized layer really differs from a plain paste of the 2x4 source.
    assert not (got == _decode(session.get_preview_png(["big", "small"]))).all()
    session.close()


def test_without_forced_resizing_the_frame_is_max_width_by_max_height():
    session, _ = _session()
    got = _decode(session.get_preview_png(["small", "wide"]))
    assert got.shape == (4, 10, 4)
    page = _page_pixels()
    assert (got[0:2, 0:2] == page[0:2, 8:10]).all()
    assert (got[2:4, 2:10, 3] == 0).all()
    session.close()


def test_target_can_shrink_one_axis():
    session, _ = _session()
    page = _page_pixels()
    wide, small = page[4:6, 0:10], page[0:4, 8:10]
    got = _decode(session.get_preview_png(["wide", "small"], force_resize=True))
    assert got.shape == (2, 10, 4)
    assert (got == _over([lanczos_resize_rgba(small, 10, 2), wide], 10, 2)).all()
    session.close()


def test_equal_area_tie_keeps_the_earlier_region_size():
    session, _ = _session()
    page = _page_pixels()
    tall, small = page[0:4, 10:12], page[0:4, 8:10]
    got = _decode(session.get_preview_png(["small", "tall"], force_resize=True))
    assert got.shape == (4, 2, 4)
    # Same size: neither is resized, plain stack.
    assert (got == _over([tall, small], 2, 4)).all()
    got = _decode(session.get_preview_png(["small", "big"], force_resize=True))
    assert got.shape == (4, 8, 4)
    session.close()


def test_single_region_is_returned_unscaled():
    session, _ = _session()
    assert (
        _decode(session.get_preview_png(["small"], force_resize=True))
        == _page_pixels()[0:4, 8:10]
    ).all()
    session.close()


def test_resized_scratch_images_are_disposed():
    raster = PillowRasterBackend()
    page = raster.decode_png(_png(_page_pixels()))
    processor = AtlasProcessor(AtlasDocument.parse(ATLAS), {"p.png": page}, raster)
    out = processor.get_preview_image(["big", "small", "wide"], force_resize=True)
    assert raster.size(out) == (8, 4)
    raster.dispose(out)
    assert set(raster._images) == {page}


@pytest.mark.parametrize("pma", [False, True])
def test_forced_resizing_with_a_zero_size_region(pma):
    """A 0x0 region adds nothing to a forced-resize composite (it used to
    raise ZeroDivisionError in the Lanczos kernels)."""
    from atlas_toolkit.api import SpineAtlas

    header = "page.png\nsize: 12,6\n" + ("pma: true\n" if pma else "")
    text = header + "Head\n bounds: 0,0,6,4\nDot\n bounds: 6,0,0,0\n"
    page = Image.fromarray(_page_pixels(), "RGBA")
    atlas = SpineAtlas.load(text, page)
    forced = np.asarray(atlas.extract_composite(["Head", "Dot"], force_resize=True))
    plain = np.asarray(atlas.extract_composite(["Head", "Dot"]))
    assert forced.shape == (4, 6, 4)
    assert (forced == plain).all()
    assert (forced[..., 3] == np.asarray(atlas.extract("Head"))[..., 3]).all()
