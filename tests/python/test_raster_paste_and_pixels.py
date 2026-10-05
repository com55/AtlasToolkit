import io
import json
from pathlib import Path

import pytest
from PIL import Image

from atlas_toolkit.raster.pillow_backend import PillowRasterBackend

FIXTURES = Path(__file__).parent / "fixtures"
ORACLE_PATH = FIXTURES / "raster_js_oracle.json"


def _load_oracle():
    return json.loads(ORACLE_PATH.read_text())


def test_paste_alpha_composites_overlapping_semi_transparent_regions():
    """The discriminating case: dest has an opaque red pixel at (0,0); src
    is a 1x1 fully-covering canvas with a 50%-alpha blue pixel, pasted at
    (0,0) so it overlaps dest exactly. A raw overwrite (Image.paste(src,
    box) with no mask) would leave dest as pure blue with alpha=128 --
    silently dropping the red entirely. Correct Porter-Duff "source-over"
    must blend: out_a = src_a + dst_a*(1-src_a) = 128 + 255*(127/255) = 255
    (fully opaque result, dst was already opaque), out_rgb weighted toward
    src by src_a/out_a. This is exactly the failure mode a non-overlapping,
    fully-opaque-sprites test would never catch (CLAUDE.md's documented
    alpha===0-discriminator trap, same class of bug)."""
    backend = PillowRasterBackend()
    dest = backend.create(1, 1)
    backend._images[dest].putpixel((0, 0), (255, 0, 0, 255))  # opaque red
    src = backend.create(1, 1)
    backend._images[src].putpixel((0, 0), (0, 0, 255, 128))  # 50% blue

    backend.paste(dest, src, 0, 0)

    r, g, b, a = backend._images[dest].getpixel((0, 0))
    assert a == 255, "opaque destination stays opaque after compositing over it"
    assert r > 100, f"red must still show through a 50%-alpha overlay, got r={r}"
    assert b > 100, f"blue from src must show, got b={b}"
    assert r != 255 or b != 0, "must not be an unmodified pure-red no-op"
    assert not (r == 0 and b == 255), "must not be a raw overwrite (pure src blue, red dropped)"


def test_paste_onto_transparent_ground_places_src_unchanged():
    backend = PillowRasterBackend()
    dest = backend.create(3, 3)
    src = backend.create(1, 1)
    backend._images[src].putpixel((0, 0), (10, 20, 30, 200))

    backend.paste(dest, src, 1, 1)

    assert backend._images[dest].getpixel((1, 1)) == (10, 20, 30, 200)
    assert backend._images[dest].getpixel((0, 0)) == (0, 0, 0, 0)


def test_paste_fully_opaque_src_replaces_dest_entirely():
    """Porter-Duff "over" with src_a=1 reduces algebraically to
    result=src (top alpha=1 means full replace, dest contributes nothing) --
    the degenerate case the overlap test above doesn't cover on its own
    (that one uses a 50%-alpha src specifically to catch the overwrite-vs-
    blend distinction; this one confirms full opacity still gives the
    exact src color, not some blended remainder)."""
    backend = PillowRasterBackend()
    dest = backend.create(1, 1)
    backend._images[dest].putpixel((0, 0), (255, 0, 0, 255))
    src = backend.create(1, 1)
    backend._images[src].putpixel((0, 0), (0, 255, 0, 255))

    backend.paste(dest, src, 0, 0)

    assert backend._images[dest].getpixel((0, 0)) == (0, 255, 0, 255)


def test_paste_fully_transparent_src_is_a_true_no_op():
    """Porter-Duff "over" with src_a=0 reduces algebraically to result=dest
    exactly -- dest must be byte-identical to before the paste call."""
    backend = PillowRasterBackend()
    dest = backend.create(1, 1)
    backend._images[dest].putpixel((0, 0), (10, 20, 30, 200))
    src = backend.create(1, 1)
    backend._images[src].putpixel((0, 0), (255, 255, 255, 0))

    backend.paste(dest, src, 0, 0)

    assert backend._images[dest].getpixel((0, 0)) == (10, 20, 30, 200)


def test_paste_clips_when_box_extends_past_dest_bounds():
    """Mirrors Canvas drawImage's silent clipping when the destination
    position would place part of src off-canvas -- Pillow's paste()
    documented behavior (verified via Context7 against Pillow 12.1.0's
    release notes) already allows the box to extend beyond the target
    image's boundaries without raising."""
    backend = PillowRasterBackend()
    dest = backend.create(2, 2)
    src = backend.create(2, 2)
    for px in range(2):
        for py in range(2):
            backend._images[src].putpixel((px, py), (5, 5, 5, 255))

    backend.paste(dest, src, 1, 1)  # half of src falls outside dest

    assert backend._images[dest].getpixel((1, 1)) == (5, 5, 5, 255)
    assert backend.size(dest) == (2, 2)  # dest itself never resizes


@pytest.mark.skipif(
    not ORACLE_PATH.exists(),
    reason="raster_js_oracle.json not generated -- run tests/python/gen_js_reference_raster.mjs",
)
def test_paste_partial_over_partial_matches_js_oracle():
    """The one alpha-compositing regime scrutinize round 1 (Task 3) found
    completely uncovered: BOTH dest and src alpha strictly between 0 and
    255 -- the only case where the unpremultiply division in Porter-Duff
    "over" actually executes, and where a Pillow-vs-Canvas fixed-point
    rounding difference could silently surface (every other test in this
    file has at least one side fully opaque or fully transparent, where
    that division either doesn't run or is trivial). dest=(255,0,0,128),
    src=(0,0,255,128) -- composited for real in Chromium via putImageData
    (exact straight-alpha input, bypassing any fillStyle-parsing rounding)
    + ctx.drawImage's default source-over, recorded in
    raster_js_oracle.json's paste_partial_over_partial case. Empirically:
    Pillow and real Canvas agree bit-for-bit here, (85, 0, 170, 192) both
    sides -- this test pins that confirmed value, it does not re-derive
    it from theory."""
    oracle = _load_oracle()["paste_partial_over_partial"]
    backend = PillowRasterBackend()
    dest = backend.create(1, 1)
    backend._images[dest].putpixel((0, 0), (255, 0, 0, 128))
    src = backend.create(1, 1)
    backend._images[src].putpixel((0, 0), (0, 0, 255, 128))

    backend.paste(dest, src, 0, 0)

    assert list(backend._images[dest].getpixel((0, 0))) == oracle["rgba"]


def test_get_pixels_returns_row_major_rgba_bytes():
    backend = PillowRasterBackend()
    handle = backend.create(2, 1)
    backend._images[handle].putpixel((0, 0), (1, 2, 3, 4))
    backend._images[handle].putpixel((1, 0), (5, 6, 7, 8))

    data = backend.get_pixels(handle)

    assert data == bytes([1, 2, 3, 4, 5, 6, 7, 8])


def test_get_pixels_and_read_alpha_mask_agree_on_row_major_layout():
    """The existing get_pixels/read_alpha_mask tests are both single-ROW
    images (2x1, 3x1) -- row-major and column-major byte order are
    indistinguishable for a single row, so neither test could actually
    catch a transposed layout. read_alpha_mask's real consumer
    (domain/packing.py, a later stage, per base.py's docstring) indexes
    it as mask[y*w+x] -- a transposed mask would silently misplace
    sprites in packing, not crash. This 2x2, four-distinct-value case is
    the smallest one that can actually discriminate row-major from
    column-major, and additionally cross-checks that get_pixels's 4-byte
    stride and read_alpha_mask's 1-byte stride stay index-aligned
    (alpha[i] corresponds to the same pixel as pixels[i*4:i*4+4])."""
    backend = PillowRasterBackend()
    handle = backend.create(2, 2)
    # Row 0: (10,y),(20,y)=20  Row 1: (30,y),(40,y)=40 -- each pixel's alpha
    # is a distinct multiple of 10 so a transposed read is unambiguous.
    backend._images[handle].putpixel((0, 0), (1, 1, 1, 10))
    backend._images[handle].putpixel((1, 0), (2, 2, 2, 20))
    backend._images[handle].putpixel((0, 1), (3, 3, 3, 30))
    backend._images[handle].putpixel((1, 1), (4, 4, 4, 40))

    pixels = backend.get_pixels(handle)
    mask = backend.read_alpha_mask(handle)

    assert pixels == bytes([1, 1, 1, 10, 2, 2, 2, 20, 3, 3, 3, 30, 4, 4, 4, 40])
    assert mask.tolist() == [True, True, True, True]  # all four alphas are nonzero
    for i in range(4):
        assert mask[i] == (pixels[i * 4 + 3] != 0)


def test_read_alpha_mask_thresholds_at_zero():
    """Matches repack-nest.js's thresholdAlpha exactly: alpha > 0 -> True,
    including partially-transparent pixels (alpha=1 is still True) --
    not a >= 128 or similar midpoint threshold."""
    backend = PillowRasterBackend()
    handle = backend.create(3, 1)
    backend._images[handle].putpixel((0, 0), (0, 0, 0, 0))
    backend._images[handle].putpixel((1, 0), (0, 0, 0, 1))
    backend._images[handle].putpixel((2, 0), (0, 0, 0, 255))

    mask = backend.read_alpha_mask(handle)

    assert mask.tolist() == [False, True, True]


def test_paste_entirely_outside_the_destination_is_a_no_op():
    raster = PillowRasterBackend()
    dest = raster.create(8, 8)
    before = raster.get_pixels(dest)
    src = raster.create(4, 4)
    raster.paste(dest, src, 20, 20)
    raster.paste(dest, src, -10, 0)
    raster.paste(dest, src, 0, -10)
    assert raster.get_pixels(dest) == before


def test_paste_clipped_on_two_edges_composites_only_the_overlap():
    # The source must be PATTERNED, not solid. With a solid source every
    # pixel is identical, so computing the clipped source origin as (0, 0)
    # instead of (left - x, top - y) produces identical output and the test
    # passes on broken code -- confirmed by mutation during planning. This
    # is the same blindness a square-only corpus caused in Stage 4.
    raster = PillowRasterBackend()
    dest = raster.create(6, 6)
    src = _patterned_src(raster, 4, 4)
    # Top-left corner clipped: only src's bottom-right 2x2 lands, at (0, 0),
    # so dest (0, 0) must carry src's pixel (2, 2) -- never src's (0, 0).
    raster.paste(dest, src, -2, -2)
    assert _pixel(raster, dest, 0, 0) == (80, 80, 200, 255)
    assert _pixel(raster, dest, 1, 1) == (120, 120, 200, 255)
    assert _pixel(raster, dest, 2, 2) == (0, 0, 0, 0)


def test_paste_still_blends_rather_than_overwriting_alpha():
    # The regression this backend's alpha gotcha exists for: a raw memcpy
    # would clobber the destination's opaque pixel with the source's
    # half-transparent one instead of blending toward it.
    raster = PillowRasterBackend()
    dest = _solid(raster, 4, 4, (0, 0, 255, 255))
    src = _solid(raster, 2, 2, (255, 0, 0, 128))
    raster.paste(dest, src, 0, 0)
    r, g, b, a = _pixel(raster, dest, 0, 0)
    assert a == 255
    assert r > 0 and b > 0  # blended, not replaced
    assert _pixel(raster, dest, 3, 3) == (0, 0, 255, 255)  # untouched


def test_paste_mutates_the_destination_handle_in_place():
    raster = PillowRasterBackend()
    dest = raster.create(4, 4)
    src = _solid(raster, 2, 2, (0, 255, 0, 255))
    raster.paste(dest, src, 1, 1)
    assert _pixel(raster, dest, 1, 1) == (0, 255, 0, 255)


def _solid(raster: PillowRasterBackend, w: int, h: int, colour: tuple[int, int, int, int]):
    buf = io.BytesIO()
    Image.new("RGBA", (w, h), colour).save(buf, format="PNG")
    return raster.decode_png(buf.getvalue())


def _patterned_src(raster: PillowRasterBackend, w: int, h: int):
    """Source whose pixel (x, y) is (x*40, y*40, 200, 255), so every pixel
    identifies its own position. A solid source cannot discriminate a wrong
    clipped-source origin."""
    data = bytes(v for y in range(h) for x in range(w) for v in (x * 40, y * 40, 200, 255))
    buf = io.BytesIO()
    Image.frombytes("RGBA", (w, h), data).save(buf, format="PNG")
    return raster.decode_png(buf.getvalue())


def _pixel(raster: PillowRasterBackend, handle, x: int, y: int) -> tuple[int, int, int, int]:
    w, _h = raster.size(handle)
    i = (y * w + x) * 4
    return tuple(raster.get_pixels(handle)[i:i + 4])
