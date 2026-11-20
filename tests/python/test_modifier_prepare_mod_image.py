# SPDX-FileCopyrightText: 2026 com55
# SPDX-License-Identifier: Apache-2.0
"""Tests for AtlasModifier.prepare_mod_image: padded size, paste position,
flags and pixels."""
import io

from PIL import Image

from spine_atlas_toolkit.document import AtlasDocument
from spine_atlas_toolkit.engine.modifier import AtlasModifier
from spine_atlas_toolkit.raster.pillow_backend import PillowRasterBackend

PAD_TEXT = "p.png\nsize: 200,200\nhero\n  bounds: 0, 0, 100, 108\n  offsets: 10, 20, 200, 200"
NEGATIVE_X_TEXT = "p.png\nsize: 200,200\nhero\n  bounds: 0, 0, 100, 108\n  offsets: -10, 20, 200, 200"

def _patterned(raster: PillowRasterBackend, w: int, h: int):
    """Opaque image whose pixel (x, y) is (x, y, 200, 255), so every pixel
    identifies its own source position (requires w, h <= 256)."""
    data = bytes(v for y in range(h) for x in range(w) for v in (x, y, 200, 255))
    buf = io.BytesIO()
    Image.frombytes("RGBA", (w, h), data).save(buf, format="PNG")
    return raster.decode_png(buf.getvalue())


def _pixel(raster: PillowRasterBackend, handle, x: int, y: int) -> tuple[int, int, int, int]:
    w, _h = raster.size(handle)
    i = (y * w + x) * 4
    return tuple(raster.get_pixels(handle)[i:i + 4])


def _pad_modifier(raster: PillowRasterBackend) -> AtlasModifier:
    return AtlasModifier(AtlasDocument.parse(PAD_TEXT), raster.create(200, 200), raster, "p.png")


def test_padding_places_mod_at_resolved_position_with_clipping_and_transparent_fill():
    raster = PillowRasterBackend()
    mod = _patterned(raster, 100, 108)
    prepared = _pad_modifier(raster).prepare_mod_image(mod, ["hero"])
    assert raster.size(prepared.image) == (104, 104)
    assert (prepared.mod_w, prepared.mod_h) == (104, 104)
    assert prepared.is_full_canvas is False
    assert prepared.shared_canvas_mod is False
    # Pasted at (5, -14): the mod's top 14 rows and its last column fall
    # outside the 104x104 canvas.
    assert _pixel(raster, prepared.image, 5, 0) == (0, 14, 200, 255)
    assert _pixel(raster, prepared.image, 103, 93) == (98, 107, 200, 255)
    # Padding stays fully transparent: left of the paste, below the mod.
    assert _pixel(raster, prepared.image, 4, 50) == (0, 0, 0, 0)
    assert _pixel(raster, prepared.image, 50, 94) == (0, 0, 0, 0)


def test_padding_leaves_the_input_mod_untouched():
    raster = PillowRasterBackend()
    mod = _patterned(raster, 100, 108)
    before = raster.get_pixels(mod)
    prepared = _pad_modifier(raster).prepare_mod_image(mod, ["hero"])
    assert prepared.image != mod
    assert raster.size(mod) == (100, 108)
    assert raster.get_pixels(mod) == before


def test_unpadded_returns_fresh_handle_with_identical_pixels():
    raster = PillowRasterBackend()
    mod = _patterned(raster, 200, 200)
    prepared = _pad_modifier(raster).prepare_mod_image(mod, ["hero"])
    assert prepared.is_full_canvas is True
    assert prepared.image != mod
    assert raster.size(prepared.image) == (200, 200)
    assert raster.get_pixels(prepared.image) == raster.get_pixels(mod)


def test_each_call_yields_a_distinct_handle():
    # The handle-identity invariant group_names_by_sprite_identity relies
    # on: two independent registrations of the same source never share a
    # handle.
    raster = PillowRasterBackend()
    modifier = _pad_modifier(raster)
    mod = _patterned(raster, 200, 200)
    first = modifier.prepare_mod_image(mod, ["hero"])
    second = modifier.prepare_mod_image(mod, ["hero"])
    assert first.image != second.image


def test_padding_clips_negative_x_with_transparent_fill():
    # Offsets (-10, 20) -> paste at (-5, -14) (value confirmed against the
    # real JS): the mod's first 5 columns and top 14 rows fall outside the
    # 104x104 canvas, and the mod ends at column 94.
    raster = PillowRasterBackend()
    modifier = AtlasModifier(AtlasDocument.parse(NEGATIVE_X_TEXT), raster.create(200, 200), raster, "p.png")
    prepared = modifier.prepare_mod_image(_patterned(raster, 100, 108), ["hero"])
    assert raster.size(prepared.image) == (104, 104)
    assert _pixel(raster, prepared.image, 0, 0) == (5, 14, 200, 255)
    assert _pixel(raster, prepared.image, 94, 93) == (99, 107, 200, 255)
    # Transparent: right of the mod's last column, and below its last row.
    assert _pixel(raster, prepared.image, 95, 50) == (0, 0, 0, 0)
    assert _pixel(raster, prepared.image, 50, 94) == (0, 0, 0, 0)
