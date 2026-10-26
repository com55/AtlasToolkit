"""Tests for AtlasModifier.prepare_mod_image: geometric parity (padded size,
paste position, flags) against the real www/js/atlas-modifier.js (plain
Node, via conftest.py's run_modifier_oracle fixture), plus pinned pixel
tests. Pixel parity with Canvas needs no browser here: padding is a single
paste onto a fully transparent canvas, and Stage 3 already proved paste
bit-exact against real Chromium."""
import io

import pytest
from PIL import Image

from atlas_toolkit.document import AtlasDocument
from atlas_toolkit.engine.modifier import AtlasModifier
from atlas_toolkit.raster.pillow_backend import PillowRasterBackend

PAD_TEXT = "p.png\nsize: 200,200\nhero\n  bounds: 0, 0, 100, 108\n  offsets: 10, 20, 200, 200"
HALF_DECLARED_TEXT = "p.png\nsize: 100,100\nhero\n  bounds: 0, 0, 50, 54\n  offsets: 5, 10, 100, 100"
THREE_REGION_TEXT = (
    "p.png\nsize: 100,100\n"
    "a\n  bounds: 0, 0, 40, 40\n  offsets: 1, 2, 100, 100\n"
    "b\n  bounds: 40, 0, 40, 40\n  offsets: 3, 4, 100, 100\n"
    "c\n  bounds: 0, 40, 10, 10"
)
ONE_REGION_TEXT = "p.png\nsize: 100,100\na\n  bounds: 0, 0, 40, 40"
# Non-square offsets canvas (20x119): the resolved canvas is 10x62, so
# scale_x = 0.5 and scale_y = 62/119 differ -- a swapped scale_x/scale_y
# pastes at (2, -5) instead of the real JS's (2, -6). Every other case uses
# a square offsets canvas, where the two scales are equal and a swap is
# invisible (Task 4 scrutinize finding).
NON_SQUARE_TEXT = "p.png\nsize: 20,119\nhero\n  bounds: 0, 0, 10, 65\n  offsets: 3, 5, 20, 119"
NEGATIVE_X_TEXT = "p.png\nsize: 200,200\nhero\n  bounds: 0, 0, 100, 108\n  offsets: -10, 20, 200, 200"

CASES = {
    "pmi_padding_non_square_scales": ("prepareModImage", [NON_SQUARE_TEXT, 20, 119, "p.png", 10, 65, ["hero"]]),
    "pmi_padding_negative_x_and_y": ("prepareModImage", [NEGATIVE_X_TEXT, 200, 200, "p.png", 100, 108, ["hero"]]),
    "pmi_padding_negative_y": ("prepareModImage", [PAD_TEXT, 200, 200, "p.png", 100, 108, ["hero"]]),
    "pmi_padding_after_page_rescale": ("prepareModImage", [HALF_DECLARED_TEXT, 200, 200, "p.png", 100, 108, ["hero"]]),
    "pmi_full_canvas_single": ("prepareModImage", [PAD_TEXT, 200, 200, "p.png", 200, 200, ["hero"]]),
    "pmi_shared_canvas_pair": ("prepareModImage", [THREE_REGION_TEXT, 100, 100, "p.png", 100, 100, ["a", "b"]]),
    "pmi_loose_shared_but_strict_false": ("prepareModImage", [THREE_REGION_TEXT, 100, 100, "p.png", 100, 100, ["a", "c"]]),
    "pmi_selection_outside_scope": ("prepareModImage", [ONE_REGION_TEXT, 100, 100, "p.png", 30, 20, ["zzz"]]),
}


class _RecordingRaster(PillowRasterBackend):
    """Records paste positions, comparable with the JS oracle's recorded
    drawImage positions."""

    def __init__(self) -> None:
        super().__init__()
        self.pastes: list[list[int]] = []

    def paste(self, dest, src, x, y, *, premultiplied=False):
        self.pastes.append([x, y])
        super().paste(dest, src, x, y, premultiplied=premultiplied)


def _python_result(op: str, args: list):
    if op == "prepareModImage":
        text, real_w, real_h, target_page, mod_w, mod_h, selected = args
        raster = _RecordingRaster()
        modifier = AtlasModifier(AtlasDocument.parse(text), raster.create(real_w, real_h), raster, target_page)
        prepared = modifier.prepare_mod_image(raster.create(mod_w, mod_h), selected)
        canvas_w, canvas_h = raster.size(prepared.image)
        return {
            "canvas_w": canvas_w,
            "canvas_h": canvas_h,
            "draws": raster.pastes,
            "mod_w": prepared.mod_w,
            "mod_h": prepared.mod_h,
            "shared_canvas_mod": prepared.shared_canvas_mod,
            "is_full_canvas": prepared.is_full_canvas,
        }
    raise ValueError(f"unknown op: {op}")


@pytest.fixture(scope="module")
def js_results(run_modifier_oracle):
    case_ids = list(CASES)
    return dict(zip(case_ids, run_modifier_oracle([CASES[c] for c in case_ids]), strict=True))


@pytest.mark.parametrize("case_id", list(CASES))
def test_matches_real_js(case_id, js_results):
    op, args = CASES[case_id]
    assert _python_result(op, args) == js_results[case_id]


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
