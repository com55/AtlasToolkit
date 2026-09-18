# tests/python/test_modifier_mesh_masking.py
"""Pixel-content tests for mask_raw_sprite/_mask_modded_sprites and their
wiring into repack_with_modded_sprites/repack_with_effective_model.
Python-only (no JS oracle): these functions are pure composition of
already-parity-tested primitives (rasterize_mesh_mask, tested in Task 1;
composite_destination_in, tested in Task 2), so the thing worth proving
here is that the WIRING calls them correctly, via patterned sprites and
explicit alpha==0 assertions -- the class of coverage the Stage 5 ledger's
triage table explicitly deferred to this stage ("no test asserts PIXEL
content of _pack_and_emit's final composited sheet")."""
from io import BytesIO

from PIL import Image

from atlas_toolkit.document import AtlasDocument
from atlas_toolkit.engine.modifier import (
    AtlasModifier,
    _mask_modded_sprites,
    mask_raw_sprite,
)
from atlas_toolkit.raster.pillow_backend import PillowRasterBackend

# Two triangles tiling a clean axis-aligned rectangle covering the left
# half (u in [0, 0.5], full v range) of whatever width/height it's
# rasterized against -- deliberately NOT a single diagonal triangle, so
# "inside" is a plain x < w/2 comparison a reader can verify by eye rather
# than a line-equation derivation (a single diagonal triangle was used in
# an earlier draft of this plan and produced an incorrect expected pixel
# in the with-offsets case below; caught by this plan's own self-review).
LEFT_HALF_RECT = {"uvs": [0, 0, 0.5, 0, 0, 1, 0.5, 1], "triangles": [0, 1, 2, 1, 3, 2]}


def _patterned_sprite(raster, w, h):
    data = bytes(v for y in range(h) for x in range(w) for v in (x % 256, y % 256, 77, 255))
    buf = BytesIO()
    Image.frombytes("RGBA", (w, h), data).save(buf, format="PNG")
    return raster.decode_png(buf.getvalue())


def test_mask_raw_sprite_no_offsets_masks_out_right_half():
    raster = PillowRasterBackend()
    sprite = _patterned_sprite(raster, 10, 10)
    mask_raw_sprite(raster, sprite, LEFT_HALF_RECT, {"offsets": None})
    img = raster._images[sprite]
    assert img.getpixel((2, 5))[3] == 255  # left half preserved
    assert img.getpixel((2, 5))[:3] == (2, 5, 77)
    assert img.getpixel((8, 5))[3] == 0    # right half masked out


def test_mask_raw_sprite_with_offsets_crops_back_correctly():
    # A sprite that is a (10,8) sub-crop of a (20,16) original, positioned
    # at offsets [offX=3, offY=2, origW=20, origH=16]. maskCropRectForOffsets
    # derives rect = {x: 3, y: 16-2-8=6, w: 10, h: 8} -- the sub-rect of a
    # full 20x16 mesh mask that this sprite corresponds to.
    raster = PillowRasterBackend()
    sprite = _patterned_sprite(raster, 10, 8)
    region = {"offsets": [3, 2, 20, 16]}
    mask_raw_sprite(raster, sprite, LEFT_HALF_RECT, region)
    img = raster._images[sprite]
    # The crop rect starts at x=3 within a 20-wide mesh mask; the triangle
    # covers mesh-mask x in [0,10) (u in [0,0.5) of 20). So sprite-local x
    # in [0, 7) is masked in (mesh x in [3,10)), x in [7,10) masked out.
    assert img.getpixel((1, 4))[3] == 255
    assert img.getpixel((9, 4))[3] == 0


def test_mask_modded_sprites_applies_one_combined_mask_to_shared_group():
    raster = PillowRasterBackend()
    shared = _patterned_sprite(raster, 10, 10)
    sprites = {"a": shared, "b": shared}
    modded = {"a": shared, "b": shared}
    mesh_lookup_fn = lambda name: LEFT_HALF_RECT  # noqa: E731
    _mask_modded_sprites(raster, sprites, modded, mesh_lookup_fn)
    # Both names must now point at the SAME fresh (cloned+masked) handle.
    assert sprites["a"] == sprites["b"]
    assert sprites["a"] != shared  # never mutates the original
    img = raster._images[sprites["a"]]
    assert img.getpixel((2, 5))[3] == 255
    assert img.getpixel((8, 5))[3] == 0
    # The original moddedSprites handle must be untouched.
    assert raster._images[shared].getpixel((8, 5))[3] == 255


def test_mask_modded_sprites_is_noop_when_mesh_lookup_fn_is_none():
    raster = PillowRasterBackend()
    shared = _patterned_sprite(raster, 4, 4)
    sprites = {"a": shared}
    _mask_modded_sprites(raster, sprites, {"a": shared}, None)
    assert sprites["a"] == shared


PAGE_TEXT = "\n".join([
    "p.png", "size: 20,20", "format: RGBA8888", "filter: Linear, Linear", "repeat: none",
    "alpha", "  bounds: 0, 0, 10, 10",
    "beta", "  bounds: 10, 0, 10, 10",
])


def _base_page(raster, w, h):
    data = bytes(v for y in range(h) for x in range(w) for v in (x % 256, y % 256, 90, 255))
    buf = BytesIO()
    Image.frombytes("RGBA", (w, h), data).save(buf, format="PNG")
    return raster.decode_png(buf.getvalue())


def test_repack_with_modded_sprites_masks_pristine_regions_with_geom():
    # End-to-end: a pristine (non-modded) region with real mesh geometry
    # gets masked during repack_with_modded_sprites, closing the Stage-5
    # ledger's deferred "no pixel-content assertion on _pack_and_emit's
    # sheet" gap for the mesh-aware path specifically.
    raster = PillowRasterBackend()
    base = _base_page(raster, 20, 20)
    modifier = AtlasModifier(AtlasDocument.parse(PAGE_TEXT), base, raster, "p.png")
    mesh_lookup_fn = lambda name: LEFT_HALF_RECT if name == "alpha" else None  # noqa: E731
    result = modifier.repack_with_modded_sprites(None, None, mesh_lookup_fn, None)
    sheet = raster._images[result.image]
    alpha_bounds = result.region_bounds["alpha"]
    ax, ay, aw, ah = alpha_bounds[0], alpha_bounds[1], alpha_bounds[2], alpha_bounds[3]
    # Left half of the packed 'alpha' region: masked in, alpha==255.
    left_px = sheet.getpixel((ax + aw // 4, ay + ah // 2))
    assert left_px[3] == 255
    # Right half: masked out, alpha==0 -- the discriminator this test
    # exists to provide.
    right_px = sheet.getpixel((ax + 3 * aw // 4, ay + ah // 2))
    assert right_px[3] == 0
    # 'beta' has no mesh geometry (mesh_lookup_fn returns None for it) --
    # fully preserved, no masking.
    beta_bounds = result.region_bounds["beta"]
    bx, by, bw, bh = beta_bounds[0], beta_bounds[1], beta_bounds[2], beta_bounds[3]
    assert sheet.getpixel((bx + bw // 2, by + bh // 2))[3] == 255


def test_repack_with_modded_sprites_skips_masking_for_modded_names():
    # A region present in moddedSprites must NOT be looked up in
    # meshLookupFn during the pristine-extraction pass (atlas-modifier.js:
    # `!(name in (moddedSprites || {}))` guard) -- it gets the modded
    # sprite's own content, unmasked here (only _maskModdedSprites masks
    # modded content, and only when the modded sprite itself has mesh
    # data via the SAME name/group).
    raster = PillowRasterBackend()
    base = _base_page(raster, 20, 20)
    modifier = AtlasModifier(AtlasDocument.parse(PAGE_TEXT), base, raster, "p.png")
    calls = []

    def tracking_lookup(name):
        calls.append(name)
        return LEFT_HALF_RECT if name == "alpha" else None

    modded_sprite = _patterned_sprite(raster, 10, 10)
    modifier.repack_with_modded_sprites({"alpha": modded_sprite}, None, tracking_lookup, None)
    assert "alpha" not in calls


def _find_uncovered_pixel(canvas_w, canvas_h, bounds_by_name):
    """First (x, y) on the canvas not inside any region's bounds rect, or
    None if the packed sheet is fully covered."""
    rects = [(b[0], b[1], b[0] + b[2], b[1] + b[3]) for b in bounds_by_name.values()]
    for y in range(canvas_h):
        for x in range(canvas_w):
            if not any(x0 <= x < x1 and y0 <= y < y1 for x0, y0, x1, y1 in rects):
                return x, y
    return None


def test_repack_with_modded_sprites_no_mesh_preserves_content_and_shows_gutter_transparency():
    # Closes the Stage 5 ledger's deferred general pixel-content coverage
    # gap on _pack_and_emit's sheet ("no test asserts PIXEL content...
    # two distinguishable patterned sprites, a pixel asserted at each
    # placement offset, plus one alpha == 0 gutter pixel") for the
    # ordinary mesh_lookup_fn=None path -- every other test in this file
    # exercises real mesh geometry; none exercises this far more common
    # path. `alpha`/`beta` are both 10x10 (not a multiple of 4), so
    # shelf_pack's round_up_to_multiple padding is expected to leave an
    # uncovered strip on the packed sheet regardless of which layout the
    # squarest-fit search picks -- the test does not hardcode where.
    raster = PillowRasterBackend()
    base = _base_page(raster, 20, 20)
    modifier = AtlasModifier(AtlasDocument.parse(PAGE_TEXT), base, raster, "p.png")
    result = modifier.repack_with_modded_sprites(None, None, None, None)
    canvas_w, canvas_h = raster.size(result.image)
    sheet = raster._images[result.image]

    for name in ("alpha", "beta"):
        # region_bounds values are [x, y, w, h, rotate] -- index, don't
        # unpack the whole list (a bare 4-name unpack raises ValueError
        # against the real 5-element shape; this plan's own Task 4 test
        # just above this one already indexes it correctly).
        bx, by, bw, bh = result.region_bounds[name][:4]
        px, py = bx + bw // 2, by + bh // 2
        # Content is position-unique in the SOURCE page, but repacking
        # relocates each region on the packed sheet -- this asserts the
        # placement is opaque (real content pasted, not skipped), not the
        # exact source colour at this new coordinate.
        assert sheet.getpixel((px, py))[3] == 255

    gutter = _find_uncovered_pixel(canvas_w, canvas_h, result.region_bounds)
    assert gutter is not None, (
        "packed canvas has no uncovered pixel to assert alpha==0 on -- "
        "widen a region size so round_up_to_multiple's padding leaves a gap"
    )
    assert sheet.getpixel(gutter)[3] == 0
