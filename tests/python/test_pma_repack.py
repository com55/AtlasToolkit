"""Modifier-level pma plumbing (spec 2026-10-26 §3.3, §5.2a, §5.3, §5.5)."""
import numpy as np
import pytest
from PIL import Image

from atlas_toolkit.document import AtlasDocument
from atlas_toolkit.engine.modifier import AtlasModifier, canvas_hash
from atlas_toolkit.raster.pillow_backend import PillowRasterBackend
from tests.python.pma_cases import GLOW_IN, GLOW_OUT, mesh, premultiplied_pattern, with_glow

LEFT = mesh(0, 0, 0.5, 1)


def sheet_text(pma):
    return (
        "page.png\nsize: 32,8\n" + ("pma: true\n" if pma else "")
        + "G\n bounds: 0,0,16,8\nN\n bounds: 16,0,16,8\n"
    )


def page_arr():
    arr = np.concatenate([premultiplied_pattern(16, 8, 1), premultiplied_pattern(16, 8, 2)], axis=1)
    arr = with_glow(arr, 2, 3, GLOW_IN)      # G, inside its left-half hull
    arr = with_glow(arr, 12, 3, GLOW_OUT)    # G, outside its hull
    arr = with_glow(arr, 16 + 2, 4, GLOW_IN) # N (no mesh lookup in some tests)
    return arr


def modifier(pma, raster):
    doc = AtlasDocument.parse(sheet_text(pma))
    return AtlasModifier(doc, raster.adopt(Image.fromarray(page_arr(), "RGBA")), raster, "page.png")


def sprite_on_sheet(raster, result, name):
    """The region's sprite read back from the packed sheet, rotation undone
    (the same crop_rotate extract_raw_sprite uses)."""
    x, y, w, h, rotate = result.region_bounds[name]
    sprite = raster.crop_rotate(result.image, x, y, w, h, rotate)
    return np.frombuffer(raster.get_pixels(sprite), np.uint8).reshape(h, w, 4)


def test_modifier_knows_its_page_pma():
    raster = PillowRasterBackend()
    assert modifier(True, raster).pma is True
    assert modifier(False, raster).pma is False


def test_pma_repack_masks_premultiplied_and_keeps_glow_inside_hull():
    raster = PillowRasterBackend()
    mod = modifier(True, raster)
    result = mod.repack(mesh_lookup_fn=lambda name: LEFT if name == "G" else None)
    g = sprite_on_sheet(raster, result, "G")
    assert g[3, 2].tolist() == list(GLOW_IN)          # inside hull: kept
    assert g[3, 12].tolist() == [0, 0, 0, 0]          # outside hull: all four cleared
    assert not g[:, 8:].any()
    n = sprite_on_sheet(raster, result, "N")          # no mesh: whole rect owned
    assert n[4, 2].tolist() == list(GLOW_IN)


def test_pma_modded_sprite_is_masked_premultiplied():
    raster = PillowRasterBackend()
    mod = modifier(True, raster)
    modded = with_glow(with_glow(premultiplied_pattern(16, 8, 5), 1, 1, GLOW_IN), 14, 6, GLOW_OUT)
    handle = raster.adopt(Image.fromarray(modded, "RGBA"))
    result = mod.repack({"G": handle}, mesh_lookup_fn=lambda name: LEFT if name == "G" else None)
    g = sprite_on_sheet(raster, result, "G")
    assert g[1, 1].tolist() == list(GLOW_IN)
    assert g[6, 14].tolist() == [0, 0, 0, 0]


def test_straight_repack_still_zeroes_alpha_zero_rgb():
    raster = PillowRasterBackend()
    result = modifier(False, raster).repack()
    assert sprite_on_sheet(raster, result, "G")[3, 2].tolist() == [0, 0, 0, 0]


def test_canvas_hash_keeps_alpha_zero_rgb_only_when_premultiplied():
    raster = PillowRasterBackend()
    a = premultiplied_pattern(4, 4, 3)
    b = with_glow(a, 1, 1, GLOW_IN)
    a = with_glow(a, 1, 1, (0, 0, 0, 0))
    ha, hb = (raster.adopt(Image.fromarray(x, "RGBA")) for x in (a, b))
    assert canvas_hash(raster, ha) == canvas_hash(raster, hb)                       # JS parity
    assert canvas_hash(raster, ha, premultiplied=True) != canvas_hash(raster, hb, premultiplied=True)


@pytest.mark.parametrize("pma, merged", [(True, False), (False, True)])
def test_dedup_respects_alpha_zero_rgb_only_on_pma_pages(pma, merged):
    raster = PillowRasterBackend()
    twin = premultiplied_pattern(16, 8, 4)
    arr = np.concatenate([twin, with_glow(twin, 5, 5, GLOW_IN)], axis=1)
    arr[5, 5] = (0, 0, 0, 0)
    doc = AtlasDocument.parse(sheet_text(pma))
    mod = AtlasModifier(doc, raster.adopt(Image.fromarray(arr, "RGBA")), raster, "page.png")
    result = mod.repack()
    same = result.region_bounds["G"][:2] == result.region_bounds["N"][:2]
    assert same is merged


TRIMMED = "page.png\nsize: 32,8\npma: true\nG\n bounds: 0,0,16,8\n offsets: 2,1,20,10\nN\n bounds: 16,0,16,8\n"


def test_pma_repack_masks_a_trimmed_region_premultiplied():
    # offsets branch of mask_raw_sprite: the hull lives in the 20x10
    # original frame; LEFT covers original x < 10, i.e. sprite x < 8.
    raster = PillowRasterBackend()
    doc = AtlasDocument.parse(TRIMMED)
    mod = AtlasModifier(doc, raster.adopt(Image.fromarray(page_arr(), "RGBA")), raster, "page.png")
    result = mod.repack(mesh_lookup_fn=lambda name: LEFT if name == "G" else None)
    g = sprite_on_sheet(raster, result, "G")
    assert g[3, 2].tolist() == list(GLOW_IN)          # inside hull: kept
    assert g[3, 12].tolist() == [0, 0, 0, 0]          # outside hull: all four cleared
    assert not g[:, 8:].any()


def test_pma_prepare_mod_image_pads_premultiplied():
    # The padding case of test_modifier_prepare_mod_image (a 100x108 mod on
    # a 104x104 canvas, pasted at (5, -14)) on a pma page: the additive
    # pixel must survive the paste.
    raster = PillowRasterBackend()
    text = "p.png\nsize: 200,200\npma: true\nhero\n  bounds: 0, 0, 100, 108\n  offsets: 10, 20, 200, 200"
    mod = AtlasModifier(AtlasDocument.parse(text), raster.create(200, 200), raster, "p.png")
    sprite = with_glow(premultiplied_pattern(100, 108, 6), 10, 20, GLOW_IN)
    prepared = mod.prepare_mod_image(raster.adopt(Image.fromarray(sprite, "RGBA")), ["hero"])
    assert raster.size(prepared.image) == (104, 104)
    out = np.frombuffer(raster.get_pixels(prepared.image), np.uint8).reshape(104, 104, 4)
    assert out[6, 15].tolist() == list(GLOW_IN)
    assert np.array_equal(out[:94, 5:], sprite[14:, :99])
