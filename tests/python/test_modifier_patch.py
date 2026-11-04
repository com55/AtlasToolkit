"""AtlasModifier.patch: place adds into a page's free space (spec 2026-10-31 §5)."""
import numpy as np
import pytest
from PIL import Image

from atlas_toolkit.document import AtlasDocument
from atlas_toolkit.domain.packing import CanvasTooLargeError
from atlas_toolkit.engine.modifier import AtlasModifier
from atlas_toolkit.raster.pillow_backend import PillowRasterBackend

UPPER_LEFT = {"uvs": [0, 0, 1, 0, 0, 1], "triangles": [0, 1, 2]}
LOWER_RIGHT = {"uvs": [1, 0, 1, 1, 0, 1], "triangles": [0, 1, 2]}


def pattern(w, h, seed=1):
    ys, xs = np.mgrid[0:h, 0:w]
    rgba = np.zeros((h, w, 4), np.uint8)
    rgba[..., 0] = (xs * 5 + seed) % 256
    rgba[..., 1] = (ys * 7 + seed * 3) % 256
    rgba[..., 2] = (xs * 11 + ys * 13 + seed) % 256
    rgba[..., 3] = 255
    return rgba


def page_rgba(w, h, rects, seed=1):
    """Opaque position pattern inside each (x, y, w, h) rect, (0,0,0,0) elsewhere."""
    rgba = pattern(w, h, seed)
    inside = np.zeros((h, w), bool)
    for x, y, rw, rh in rects:
        inside[y:y + rh, x:x + rw] = True
    rgba[~inside] = 0
    return rgba


def setup(text, rgba):
    raster = PillowRasterBackend()
    document = AtlasDocument.parse(text)
    filename = document.pages[0].filename
    modifier = AtlasModifier(document, raster.adopt(Image.fromarray(rgba, "RGBA")), raster, filename)
    return modifier, raster, filename


def add(raster, filename, name, rgba, key=None):
    return {
        "type": "add", "internal_key": key or name, "atlas_name": name,
        "source": raster.adopt(Image.fromarray(rgba, "RGBA")), "page": filename,
    }


def pixels(raster, result):
    w, h = raster.size(result.image)
    return np.frombuffer(raster.get_pixels(result.image), np.uint8).reshape(h, w, 4)


def regions(result):
    return {r.name: (r.x, r.y, r.w, r.h, r.rotate) for r in result.document.pages[0].regions}


TWO = "p.png\nsize: 64,32\nA\n bounds: 0,0,16,16\nB\n bounds: 16,0,16,16\n"


def test_places_into_free_space_and_keeps_every_other_byte():
    rgba = page_rgba(64, 32, [(0, 0, 16, 16), (16, 0, 16, 16)])
    rgba[30, 60] = (9, 9, 9, 0)    # alpha-0 colour in free space
    rgba[1, 1] = (7, 7, 7, 0)      # alpha-0 colour inside a rect
    modifier, raster, f = setup(TWO, rgba)
    sprite = pattern(10, 10, 5)
    result = modifier.patch(batches=[add(raster, f, "N", sprite)], mesh_lookup_fn=None, gap=2)
    assert result.document.pages[0].size == [64, 32]
    assert regions(result) == {"A": (0, 0, 16, 16, 0), "B": (16, 0, 16, 16, 0), "N": (36, 0, 10, 10, 0)}
    expected = rgba.copy()
    expected[0:10, 36:46] = sprite
    assert np.array_equal(pixels(raster, result), expected)


def test_clears_non_live_pixels_under_a_placement_before_pasting():
    rgba = page_rgba(64, 32, [(0, 0, 16, 16), (16, 0, 16, 16)])
    rgba[5, 40] = (9, 9, 9, 0)     # under N's spot
    modifier, raster, f = setup(TWO, rgba)
    sprite = pattern(10, 10, 5)
    sprite[5, 4] = (50, 60, 70, 0)  # transparent pixel over that spot
    result = modifier.patch(batches=[add(raster, f, "N", sprite)], mesh_lookup_fn=None, gap=2)
    assert tuple(pixels(raster, result)[5, 40]) == (0, 0, 0, 0)


def test_pma_page_pastes_the_sprite_byte_for_byte():
    rgba = page_rgba(64, 32, [(0, 0, 16, 16), (16, 0, 16, 16)])
    rgba[5, 40] = (9, 9, 9, 0)     # additive colour under N's spot
    text = TWO.replace("size: 64,32\n", "size: 64,32\npma: true\n")
    modifier, raster, f = setup(text, rgba)
    sprite = pattern(10, 10, 5)
    sprite[2, 2] = (40, 30, 20, 128)  # a valid premultiplied partial pixel
    sprite[3, 3] = (40, 0, 0, 0)
    result = modifier.patch(batches=[add(raster, f, "N", sprite)], mesh_lookup_fn=None, gap=2)
    assert np.array_equal(pixels(raster, result)[0:10, 36:46], sprite)
    assert result.document.pages[0].pma is True


def test_grows_and_trims_and_rounds():
    text = "p.png\nsize: 30,18\nA\n bounds: 0,0,30,18\n"
    rgba = page_rgba(30, 18, [(0, 0, 30, 18)])
    modifier, raster, f = setup(text, rgba)
    sprite = pattern(10, 10, 5)
    result = modifier.patch(batches=[add(raster, f, "N", sprite)], mesh_lookup_fn=None, gap=2)
    assert result.document.pages[0].size == [32, 32]
    assert regions(result)["N"] == (0, 20, 10, 10, 0)
    out = pixels(raster, result)
    assert np.array_equal(out[:18, :30], rgba)
    expected_tail = np.zeros((32, 32, 4), np.uint8)
    expected_tail[20:30, 0:10] = sprite
    expected_tail[:18, :30] = rgba
    assert np.array_equal(out, expected_tail)


def test_rounds_both_axes_without_growth():
    text = "p.png\nsize: 30,18\nA\n bounds: 0,0,8,8\n"
    rgba = page_rgba(30, 18, [(0, 0, 8, 8)])
    modifier, raster, f = setup(text, rgba)
    result = modifier.patch(batches=[add(raster, f, "N", pattern(4, 4, 5))], mesh_lookup_fn=None, gap=1)
    assert result.document.pages[0].size == [32, 20]
    assert regions(result)["N"] == (12, 0, 4, 4, 0)
    assert not pixels(raster, result)[18:, :].any() and not pixels(raster, result)[:, 30:].any()


def test_grows_both_axes_for_an_image_bigger_than_the_page():
    text = "p.png\nsize: 16,16\nA\n bounds: 0,0,16,16\n"
    rgba = page_rgba(16, 16, [(0, 0, 16, 16)])
    modifier, raster, f = setup(text, rgba)
    result = modifier.patch(batches=[add(raster, f, "N", pattern(40, 40, 5))], mesh_lookup_fn=None, gap=2)
    w, h = result.document.pages[0].size
    assert w > 16 and h > 16 and w % 4 == 0 and h % 4 == 0
    assert np.array_equal(pixels(raster, result)[:16, :16], rgba)


def test_existing_hull_lets_a_placement_into_its_empty_corner():
    text = "p.png\nsize: 32,32\nM\n bounds: 0,0,32,32\n"
    rgba = pattern(32, 32)  # fully opaque, hull and corner alike
    modifier, raster, f = setup(text, rgba)
    lookup = {"M": UPPER_LEFT}.get
    result = modifier.patch(batches=[add(raster, f, "N", pattern(6, 6, 5))], mesh_lookup_fn=lookup, gap=1)
    assert result.document.pages[0].size == [32, 32]
    x, y, w, h, _ = regions(result)["N"]
    hull = np.asarray(raster.read_alpha_mask(raster.rasterize_mesh_mask(UPPER_LEFT["uvs"], [0, 1, 2], 32, 32)), bool).reshape(32, 32)
    out = pixels(raster, result)
    assert np.array_equal(out[hull], rgba[hull])           # every hull pixel kept
    outside = np.ones((32, 32), bool)
    outside[y:y + h, x:x + w] = False
    assert np.array_equal(out[outside], rgba[outside])     # never-rendered pixels kept unless covered


def test_existing_rect_is_live_when_mesh_aware_is_off():
    text = "p.png\nsize: 32,32\nM\n bounds: 0,0,32,32\n"
    modifier, raster, f = setup(text, pattern(32, 32))
    result = modifier.patch(batches=[add(raster, f, "N", pattern(6, 6, 5))], mesh_lookup_fn=None, gap=1)
    x, y, _, _, _ = regions(result)["N"]
    assert result.document.pages[0].size != [32, 32] and (x >= 32 or y >= 32)


@pytest.mark.parametrize("rotate, k", [(90, 1), (180, 2), (270, -1)])
def test_rotated_region_keeps_its_hull_in_the_stored_orientation(rotate, k):
    w, h = 24, 12                            # unrotated size
    sw, sh = (h, w) if rotate in (90, 270) else (w, h)
    text = f"p.png\nsize: {sw},{sh}\nR\n bounds: 0,0,{w},{h}\n rotate: {rotate}\n"
    rgba = pattern(sw, sh)
    modifier, raster, f = setup(text, rgba)
    lookup = {"R": UPPER_LEFT}.get
    result = modifier.patch(batches=[add(raster, f, "N", pattern(3, 3, 5))], mesh_lookup_fn=lookup, gap=1)
    assert result.document.pages[0].size == [sw, sh]
    unrotated = np.asarray(raster.read_alpha_mask(raster.rasterize_mesh_mask(UPPER_LEFT["uvs"], [0, 1, 2], w, h)), bool).reshape(h, w)
    stored = np.rot90(unrotated, k)
    assert np.array_equal(pixels(raster, result)[stored], rgba[stored])


def test_trimmed_rotated_region_keeps_its_hull():
    # unrotated sprite 20x10, trimmed out of an original 24x12 at offsets (2, 1); rotate 90 -> stored 10x20
    text = "p.png\nsize: 12,20\nR\n bounds: 0,0,20,10\n offsets: 2,1,24,12\n rotate: 90\n"
    rgba = pattern(12, 20)
    modifier, raster, f = setup(text, rgba)
    result = modifier.patch(batches=[add(raster, f, "N", pattern(3, 3, 5))], mesh_lookup_fn={"R": UPPER_LEFT}.get, gap=1)
    assert result.document.pages[0].size == [12, 20]
    full = np.asarray(raster.read_alpha_mask(raster.rasterize_mesh_mask(UPPER_LEFT["uvs"], [0, 1, 2], 24, 12)), bool).reshape(12, 24)
    # crop back the 20x10 sprite: x from offsets.left, y = orig_h - bottom - h
    unrotated = full[12 - 1 - 10:12 - 1, 2:22]
    stored = np.rot90(unrotated, 1)  # 20 rows x 10 cols: the region's stored rect at (0, 0)
    assert np.array_equal(pixels(raster, result)[:20, :10][stored], rgba[:20, :10][stored])


def test_region_partly_off_the_left_edge_keeps_its_hull():
    text = "p.png\nsize: 16,16\nR\n bounds: -8,0,24,16\n"
    rgba = pattern(16, 16)
    modifier, raster, f = setup(text, rgba)
    result = modifier.patch(batches=[add(raster, f, "N", pattern(3, 3, 5))], mesh_lookup_fn={"R": UPPER_LEFT}.get, gap=1)
    hull = np.asarray(raster.read_alpha_mask(raster.rasterize_mesh_mask(UPPER_LEFT["uvs"], [0, 1, 2], 24, 16)), bool).reshape(16, 24)[:, 8:]
    assert np.array_equal(pixels(raster, result)[:16, :16][hull], rgba[hull])


def test_malformed_offsets_do_not_raise():
    text = "p.png\nsize: 32,16\nR\n bounds: 0,0,20,12\n offsets: 0,0,10,10\n"
    modifier, raster, f = setup(text, pattern(32, 16))
    result = modifier.patch(batches=[add(raster, f, "N", pattern(3, 3, 5))], mesh_lookup_fn={"R": UPPER_LEFT}.get, gap=1)
    assert "N" in regions(result)


def test_regions_sharing_a_rect_union_their_hulls():
    text = "p.png\nsize: 16,16\nA\n bounds: 0,0,16,16\nA2\n bounds: 0,0,16,16\n"
    modifier, raster, f = setup(text, pattern(16, 16))
    lookup = {"A": UPPER_LEFT, "A2": LOWER_RIGHT}.get
    result = modifier.patch(batches=[add(raster, f, "N", pattern(3, 3, 5))], mesh_lookup_fn=lookup, gap=1)
    assert result.document.pages[0].size != [16, 16]


def test_look_alike_regions_at_different_rects_keep_their_own_hulls():
    left = pattern(16, 16)
    rgba = np.concatenate([left, left], axis=1)  # identical pixels in both rects
    text = "p.png\nsize: 32,16\nA\n bounds: 0,0,16,16\nB\n bounds: 16,0,16,16\n"
    modifier, raster, f = setup(text, rgba)
    lookup = {"A": UPPER_LEFT, "B": LOWER_RIGHT}.get
    result = modifier.patch(batches=[add(raster, f, "N", pattern(3, 3, 5))], mesh_lookup_fn=lookup, gap=1)
    assert result.document.pages[0].size == [32, 16]


def test_added_sprite_with_mesh_is_cut_and_nested_by_its_hull():
    text = "p.png\nsize: 20,20\nA\n bounds: 16,16,4,4\n"
    modifier, raster, f = setup(text, page_rgba(20, 20, [(16, 16, 4, 4)]))
    lookup = {"T1": UPPER_LEFT, "T2": LOWER_RIGHT}.get
    batches = [add(raster, f, "T1", pattern(12, 12, 3)), add(raster, f, "T2", pattern(12, 12, 4))]
    result = modifier.patch(batches=batches, mesh_lookup_fn=lookup, gap=1)
    assert result.document.pages[0].size == [20, 20]
    assert regions(result)["T1"][:2] == (0, 0) and regions(result)["T2"][:2] == (4, 0)
    out = pixels(raster, result)
    for x, geometry, expected in [(0, UPPER_LEFT, pattern(12, 12, 3)), (4, LOWER_RIGHT, pattern(12, 12, 4))]:
        hull = np.asarray(raster.read_alpha_mask(raster.rasterize_mesh_mask(geometry["uvs"], [0, 1, 2], 12, 12)), bool).reshape(12, 12)
        assert np.array_equal(out[:12, x:x + 12][hull], expected[hull])


def test_added_sprite_with_mesh_is_cut_to_its_hull():
    text = "p.png\nsize: 20,20\nA\n bounds: 16,16,4,4\n"
    modifier, raster, f = setup(text, page_rgba(20, 20, [(16, 16, 4, 4)]))
    result = modifier.patch(batches=[add(raster, f, "T1", pattern(12, 12, 3))], mesh_lookup_fn={"T1": UPPER_LEFT}.get, gap=1)
    x, y, _, _, _ = regions(result)["T1"]
    out = pixels(raster, result)
    assert out[y + 1, x + 1, 3] == 255 and out[y + 11, x + 11, 3] == 0


def test_live_pixel_inside_a_placed_rect_but_outside_its_hull_is_kept():
    text = "p.png\nsize: 20,20\nA\n bounds: 16,16,4,4\n"
    rgba = page_rgba(20, 20, [(16, 16, 4, 4)])
    rgba[11, 11] = (60, 50, 40, 128)  # stray partial-alpha pixel: live
    modifier, raster, f = setup(text, rgba)
    result = modifier.patch(batches=[add(raster, f, "T1", pattern(12, 12, 3))], mesh_lookup_fn={"T1": UPPER_LEFT}.get, gap=1)
    x, y, w, h, _ = regions(result)["T1"]
    assert x <= 11 < x + w and y <= 11 < y + h  # the pixel sits inside T1's rect...
    assert tuple(pixels(raster, result)[11, 11]) == (60, 50, 40, 128)  # ...and survives byte for byte


def test_text_keeps_pristine_regions_and_applies_renames():
    text = "p.png\nsize: 64,32\nfilter: Linear,Linear\nA\n bounds: 0,0,16,16\n offsets: 1,1,18,18\nB\n bounds: 16,0,16,16\n"
    modifier, raster, f = setup(text, page_rgba(64, 32, [(0, 0, 16, 16), (16, 0, 16, 16)]))
    batches = [add(raster, f, "N", pattern(10, 10, 5)), {"type": "rename", "target_key": "B", "new_atlas_name": "B2"}]
    result = modifier.patch(batches=batches, mesh_lookup_fn=None, gap=2)
    page = result.document.pages[0]
    a, b, n = page.regions
    assert (a.name, a.atlas_name, a.offsets) == ("A", "A", [1, 1, 18, 18])
    assert (b.name, b.atlas_name) == ("B", "B2")
    assert (n.name, n.atlas_name, n.offsets, n.index) == ("N", "N", None, -1)
    assert page.filter == ["Linear", "Linear"]


@pytest.mark.parametrize("batch", [
    {"type": "remove", "target_key": "A"},
    {"type": "mod", "names": ["A"], "source": None, "is_full_canvas": False, "composite": False},
])
def test_refuses_a_page_with_a_pristine_edit(batch):
    modifier, raster, f = setup(TWO, page_rgba(64, 32, [(0, 0, 16, 16), (16, 0, 16, 16)]))
    with pytest.raises(ValueError, match="patch"):
        modifier.patch(batches=[add(raster, f, "N", pattern(10, 10, 5)), batch], mesh_lookup_fn=None, gap=2)


def test_a_mod_naming_another_pages_key_is_fine():
    modifier, raster, f = setup(TWO, page_rgba(64, 32, [(0, 0, 16, 16), (16, 0, 16, 16)]))
    other = {"type": "mod", "names": ["ElsewhereKey"], "source": None, "is_full_canvas": False, "composite": False}
    result = modifier.patch(batches=[add(raster, f, "N", pattern(10, 10, 5)), other], mesh_lookup_fn=None, gap=2)
    assert "N" in regions(result)


def test_identical_adds_share_one_rect():
    modifier, raster, f = setup(TWO, page_rgba(64, 32, [(0, 0, 16, 16), (16, 0, 16, 16)]))
    sprite = pattern(10, 10, 5)
    result = modifier.patch(batches=[add(raster, f, "N", sprite), add(raster, f, "M", sprite)], mesh_lookup_fn=None, gap=2)
    assert regions(result)["N"][:2] == regions(result)["M"][:2]


def test_a_fully_transparent_add_still_gets_a_rect():
    modifier, raster, f = setup(TWO, page_rgba(64, 32, [(0, 0, 16, 16), (16, 0, 16, 16)]))
    result = modifier.patch(batches=[add(raster, f, "N", np.zeros((4, 4, 4), np.uint8))], mesh_lookup_fn=None, gap=2)
    assert regions(result)["N"][2:4] == (4, 4)


def test_a_rect_past_the_page_edge_is_clipped_not_fatal():
    text = "p.png\nsize: 32,32\nA\n bounds: 24,24,16,16\n"
    modifier, raster, f = setup(text, page_rgba(32, 32, [(24, 24, 8, 8)]))
    result = modifier.patch(batches=[add(raster, f, "N", pattern(4, 4, 5))], mesh_lookup_fn=None, gap=1)
    assert regions(result)["N"][:2] == (0, 0)


def test_too_large_raises():
    text = "p.png\nsize: 8,8\nA\n bounds: 0,0,8,8\n"
    modifier, raster, f = setup(text, pattern(8, 8))
    with pytest.raises(CanvasTooLargeError):
        modifier.patch(batches=[add(raster, f, "W", pattern(10001, 1, 5))], mesh_lookup_fn=None, gap=1)
