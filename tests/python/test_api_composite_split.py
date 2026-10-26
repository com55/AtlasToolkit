"""build(split_composite=True): replace_composite is the inverse of
extract_composite(force_resize=True) inside the mesh hulls (spec 2026-10-13).
All meshes are axis-aligned rectangles whose edges land on whole pixels, so
the expected hulls are written by hand, never taken from the product."""
import warnings

import numpy as np
import pytest
from PIL import Image

import atlas_toolkit.api as api
from atlas_toolkit.api import SpineAtlas
from atlas_toolkit.domain.alpha_split import SPLIT
from atlas_toolkit.engine.session import AtlasSession
from atlas_toolkit.packer import PackerOptions

QUAD = [0, 1, 2, 0, 2, 3]


def mesh(u0, v0, u1, v1):
    return {"type": "Mesh", "uvs": [u0, v0, u1, v0, u1, v1, u0, v1], "triangles": QUAD, "hull_length": 4}


# Same canvas 32x16. Rects (x, y, w, h): A (0,0,16,10), B (8,4,24,12) stored rotated, C (2,6,12,10).
SAME = (
    "page.png\nsize: 64,32\n"
    "A\n bounds: 0,0,16,10\n offsets: 0,6,32,16\n"
    "B\n bounds: 16,0,24,12\n offsets: 8,0,32,16\n rotate: 90\n"
    "C\n bounds: 28,0,12,10\n offsets: 2,0,32,16\n"
    "Other\n bounds: 40,0,6,6\n"
)
SAME_MESH = {
    "A": mesh(0, 0, 0.375, 0.5),            # hull x0-12, y0-8
    "B": mesh(0.25, 0.375, 0.875, 0.875),   # hull x8-28, y6-14
    "C": mesh(0.125, 0.375, 0.375, 1.0),    # hull x4-12, y6-16
}
SAME_HULLS = [(0, 0, 12, 8), (8, 6, 28, 14), (4, 6, 12, 16)]   # x0, y0, x1, y1 at 32x16

# Mixed scales: D canvas 32x16, E canvas 24x12 (scale 0.75).
MIXED = (
    "page.png\nsize: 64,32\n"
    "D\n bounds: 0,0,20,10\n offsets: 0,6,32,16\n"
    "E\n bounds: 20,0,15,9\n offsets: 3,0,24,12\n"
)
MIXED_MESH = {"D": mesh(0, 0, 0.5, 0.5), "E": mesh(0.25, 0.25, 0.75, 1.0)}
MIXED_HULLS = [(0, 0, 0.5, 0.5), (0.25, 0.25, 0.75, 1.0)]   # fractions of the input size


def page():
    rng = np.random.default_rng(7)
    arr = rng.integers(0, 256, (32, 64, 4), dtype=np.uint8)
    arr[..., 3] = 255
    return Image.fromarray(arr, "RGBA")


def load(monkeypatch, text, meshes):
    monkeypatch.setattr(api, "_parse_skeleton", lambda data: {"attachments": meshes})
    return SpineAtlas.load(text, page(), skeleton=b"x")


def pattern(w, h):
    """Each pixel encodes its position; alpha cycles through edge values."""
    ys, xs = np.mgrid[0:h, 0:w]
    arr = np.zeros((h, w, 4), np.uint8)
    arr[..., 0] = xs * 255 // max(1, w - 1)
    arr[..., 1] = ys * 255 // max(1, h - 1)
    arr[..., 2] = (xs * 7 + ys * 13) % 256
    arr[..., 3] = np.array([255, 128, 1, 2, 254, 0, 200], np.uint8)[(xs + 2 * ys) % 7]
    return arr


def union(w, h, hulls):
    m = np.zeros((h, w), bool)
    for x0, y0, x1, y1 in hulls:
        m[y0:y1, x0:x1] = True
    return m


def assert_round_trip(got_img, want, inside):
    got = np.asarray(got_img.convert("RGBA")).astype(int)
    want = want.astype(int)
    assert got.shape == want.shape
    visible = inside & (want[..., 3] > 0)
    assert np.array_equal(got[..., 3][inside], want[..., 3][inside])
    assert np.array_equal(got[visible][:, :3], want[visible][:, :3])
    assert np.all(got[..., 3][~inside] == 0)


def test_fixture_hulls_are_whole_pixels(monkeypatch):
    atlas = load(monkeypatch, SAME, SAME_MESH)
    # Pristine regions on an opaque page: the mesh-cropped extract is opaque
    # exactly on the hand-written hull, so the hand values match the rasterizer.
    for name, hull in zip("ABC", SAME_HULLS):
        alpha = np.asarray(atlas.extract(name, mesh_crop=True).convert("RGBA"))[..., 3] > 0
        assert np.array_equal(alpha, union(32, 16, [hull])), name


@pytest.mark.parametrize("packer, options", [
    ("shelf", PackerOptions(mesh_aware=True)),
    ("polygons", PackerOptions(padding=4)),
])
def test_same_canvas_round_trip(monkeypatch, packer, options):
    atlas = load(monkeypatch, SAME, SAME_MESH)
    want = pattern(32, 16)
    atlas.replace_composite(["A", "B", "C"], Image.fromarray(want, "RGBA"))
    # polygons padding=4 is the old nest gap; at <=2 polygons nests a neighbour inside
    # a rect and the rect-only composite below picks up its pixels.
    built = atlas.build(packer=packer, packer_options=options, split_composite=True)
    inside = union(32, 16, SAME_HULLS)
    assert_round_trip(built.extract_composite(["A", "B", "C"], force_resize=True, mesh_crop=True), want, inside)
    assert_round_trip(built.extract_composite(["A", "B", "C"], force_resize=True), want, inside)
    assert built.region("B").bounds[2:] == (24, 12)        # stored at rect size, not 32x16
    assert built.region("A").offsets == (0, 6, 32, 16)
    # The round trip alone cannot see the split (all alpha on the top holder
    # composites the same), so pin each piece: (9, 10) is alpha 128 and only
    # B's and C's hulls hold it, B on top.
    assert want[10, 9, 3] == 128
    b_alpha = built.extract("B").getpixel((9, 10))[3]
    c_alpha = built.extract("C").getpixel((9, 10))[3]
    assert (b_alpha, c_alpha) == SPLIT[128]


def _mixed_round_trip(monkeypatch, w, h):
    atlas = load(monkeypatch, MIXED, MIXED_MESH)
    want = pattern(w, h)
    atlas.replace_composite(["D", "E"], Image.fromarray(want, "RGBA"))
    built = atlas.build(packer_options=PackerOptions(mesh_aware=True), split_composite=True)
    hulls = [(int(u0 * w), int(v0 * h), int(u1 * w), int(v1 * h)) for u0, v0, u1, v1 in MIXED_HULLS]
    assert_round_trip(built.extract_composite(["D", "E"], force_resize=True, mesh_crop=True), want, union(w, h, hulls))
    assert built.region("D").offsets[2:] == (w, h) and built.region("E").offsets[2:] == (w, h)


def test_mixed_scales_round_trip(monkeypatch):
    _mixed_round_trip(monkeypatch, 32, 16)


def test_mixed_scales_upscaled_input_round_trips(monkeypatch):
    _mixed_round_trip(monkeypatch, 64, 32)


def test_later_replace_on_one_member(monkeypatch):
    atlas = load(monkeypatch, SAME, SAME_MESH)
    atlas.replace_composite(["A", "B", "C"], Image.fromarray(pattern(32, 16), "RGBA"))
    own = Image.new("RGBA", (16, 10), (9, 9, 9, 255))
    atlas.replace({"A": own})
    built = atlas.build(packer_options=PackerOptions(mesh_aware=True), split_composite=True)
    assert built.region("B").offsets == (8, 0, 32, 16)      # B, C still split
    assert built.region("C").bounds[2:] == (12, 10)
    a = np.asarray(built.extract("A").convert("RGBA"))
    assert a[0, 0].tolist() == [9, 9, 9, 255]


@pytest.mark.parametrize("packer, options", [
    ("shelf", PackerOptions(mesh_aware=True)),
    ("polygons", PackerOptions(padding=4)),
])
def test_empty_piece_keeps_its_rect_and_samples_nothing(monkeypatch, packer, options):
    # In game the mesh maps the whole canvas around the packed rect via the
    # offsets, so an empty piece must keep its full rect: a shrunk rect would
    # leave C's hull sampling whatever sprite nest packed next to it.
    atlas = load(monkeypatch, SAME, SAME_MESH)
    want = pattern(32, 16)
    want[6:16, 4:12, 3] = 0                                   # C's whole hull is transparent
    atlas.replace_composite(["A", "B", "C"], Image.fromarray(want, "RGBA"))
    # polygons padding=4: the old nest default gap.
    built = atlas.build(packer=packer, packer_options=options, split_composite=True)
    assert built.region("C").bounds[2:] == (12, 10)
    assert built.region("C").offsets == (2, 0, 32, 16)
    # C's hull lies inside its rect, so every texel it samples is in its bounds.
    assert not np.any(np.asarray(built.extract("C").convert("RGBA"))[..., 3])


@pytest.mark.parametrize("edit", ["rename", "add", "remove"])
def test_unrelated_structural_edit_keeps_the_group(monkeypatch, edit):
    names = ["A", "B", "C"]
    want = pattern(32, 16)
    plain = load(monkeypatch, SAME, SAME_MESH)
    plain.replace_composite(names, Image.fromarray(want, "RGBA"))
    ref = plain.build(packer_options=PackerOptions(mesh_aware=True), split_composite=True)
    other = load(monkeypatch, SAME, SAME_MESH)
    other.replace_composite(names, Image.fromarray(want, "RGBA"))
    if edit == "rename":
        other.rename_region("Other", "Renamed")
    elif edit == "add":
        other.add_region("New", Image.new("RGBA", (3, 3), (1, 2, 3, 255)))
    else:
        other.remove_region("Other")
    got = other.build(packer_options=PackerOptions(mesh_aware=True), split_composite=True)
    for name in names:
        assert got.region(name).offsets == ref.region(name).offsets
    a = np.asarray(got.extract_composite(names, force_resize=True, mesh_crop=True))
    b = np.asarray(ref.extract_composite(names, force_resize=True, mesh_crop=True))
    assert np.array_equal(a, b)


def test_member_removed_or_renamed(monkeypatch):
    atlas = load(monkeypatch, SAME, SAME_MESH)
    atlas.replace_composite(["A", "B", "C"], Image.fromarray(pattern(32, 16), "RGBA"))
    atlas.rename_region("B", "B2")
    atlas.remove_region("C")
    built = atlas.build(packer_options=PackerOptions(mesh_aware=True), split_composite=True)
    assert built.region("B2").offsets == (8, 0, 32, 16)
    assert built.region("B2").bounds[2:] == (24, 12)
    assert "C" not in [r.name for r in built.regions]


# Rects (x0, y0, x1, y1) of SAME at 32x16, used when a region has no mesh.
SAME_RECTS = [(0, 0, 16, 10), (8, 4, 32, 16), (2, 6, 14, 16)]


@pytest.mark.parametrize("packer, options", [("shelf", None), ("rectangles", PackerOptions(padding=4))])
def test_split_without_mesh_cuts_rects(packer, options):
    atlas = SpineAtlas.load(SAME, page())
    assert atlas.build(split_composite=True).atlas_text == atlas.atlas_text   # unedited: no error
    want = pattern(32, 16)
    atlas.replace_composite(["A", "B", "C"], Image.fromarray(want, "RGBA"))
    built = atlas.build(packer=packer, packer_options=options, split_composite=True)
    assert_round_trip(built.extract_composite(["A", "B", "C"], force_resize=True), want, union(32, 16, SAME_RECTS))
    assert built.region("A").offsets == (0, 6, 32, 16)
    assert built.region("B").offsets == (8, 0, 32, 16)
    assert built.region("B").bounds[2:] == (24, 12)
    # (14, 4) is alpha 128, inside A's and B's rects only, A on top.
    assert want[4, 14, 3] == 128
    assert (built.extract("A").getpixel((14, 4))[3], built.extract("B").getpixel((14, 4))[3]) == SPLIT[128]


def test_split_mixes_hulls_and_rects(monkeypatch):
    # A has a mesh (hull x0-12, y0-8); B and C have none and hold their rects.
    atlas = load(monkeypatch, SAME, {"A": SAME_MESH["A"]})
    want = pattern(32, 16)
    atlas.replace_composite(["A", "B", "C"], Image.fromarray(want, "RGBA"))
    built = atlas.build(packer_options=PackerOptions(mesh_aware=True), split_composite=True)
    inside = union(32, 16, [SAME_HULLS[0], *SAME_RECTS[1:]])
    assert_round_trip(built.extract_composite(["A", "B", "C"], force_resize=True), want, inside)
    assert want[2, 13, 3] > 0 and built.extract("A").getpixel((13, 2))[3] == 0   # in A's rect, outside its hull


def test_split_ignores_meshes_without_mesh_aware(monkeypatch):
    atlas = load(monkeypatch, SAME, SAME_MESH)
    want = pattern(32, 16)
    atlas.replace_composite(["A", "B", "C"], Image.fromarray(want, "RGBA"))
    # No-mask control: the old default was mesh_aware off; auto is on when a mesh is loaded.
    built = atlas.build(packer_options=PackerOptions(mesh_aware=False), split_composite=True)
    assert_round_trip(built.extract_composite(["A", "B", "C"], force_resize=True), want, union(32, 16, SAME_RECTS))
    assert built.extract("A").getpixel((13, 2))[3] == want[2, 13, 3] > 0


def test_switch_is_part_of_the_build_cache(monkeypatch):
    atlas = load(monkeypatch, SAME, SAME_MESH)
    atlas.replace_composite(["A", "B", "C"], Image.fromarray(pattern(32, 16), "RGBA"))
    calls = []
    original = AtlasSession.pack_pending
    monkeypatch.setattr(AtlasSession, "pack_pending", lambda self, **kw: calls.append(kw) or original(self, **kw))
    off = atlas.build(packer_options=PackerOptions(mesh_aware=True))
    on = atlas.build(packer_options=PackerOptions(mesh_aware=True), split_composite=True)
    atlas.build(packer_options=PackerOptions(mesh_aware=True), split_composite=True)
    assert len(calls) == 2
    assert off.atlas_text != on.atlas_text


def test_save_passes_the_switch(monkeypatch, tmp_path):
    atlas = load(monkeypatch, SAME, SAME_MESH)
    atlas.replace_composite(["A", "B", "C"], Image.fromarray(pattern(32, 16), "RGBA"))
    atlas.save(tmp_path / "o.atlas", packer_options=PackerOptions(mesh_aware=True), split_composite=True)
    text = (tmp_path / "o.atlas").read_text(encoding="utf-8")
    assert text == atlas.build(packer_options=PackerOptions(mesh_aware=True), split_composite=True).atlas_text


def test_force_resize_masks_at_the_target_size(monkeypatch):
    # A region stored at a lower scale is resized first and masked with its
    # mesh at the target size, so nothing spills past its hull (masking
    # first and then resizing let Lanczos smear the edge outside it), and
    # the composite round-trips through split_composite with no loss.
    atlas = load(monkeypatch, MIXED, MIXED_MESH)
    comp = atlas.extract_composite(["D", "E"], force_resize=True, mesh_crop=True)
    inside = union(32, 16, [(0, 0, 16, 8), (8, 4, 24, 16)])
    assert np.array_equal(np.asarray(comp)[..., 3] > 0, inside)
    atlas.replace_composite(["D", "E"], comp)
    built = atlas.build(packer_options=PackerOptions(mesh_aware=True), split_composite=True)
    assert_round_trip(built.extract_composite(["D", "E"], force_resize=True, mesh_crop=True), np.asarray(comp), inside)


SAME_PMA = SAME.replace("size: 64,32\n", "size: 64,32\npma: true\n", 1)


def premultiplied(arr):
    out = arr.astype(np.uint16)
    out[..., :3] = out[..., :3] * out[..., 3:4] // 255
    return out.astype(np.uint8)


def test_pma_split_renders_back_premultiplied():
    # A pma page has no repack mesh, so every survivor holds its rect. The
    # straight-alpha round trip cannot see a pma bug; render premultiplied.
    atlas = SpineAtlas.load(SAME_PMA, page())
    want = premultiplied(pattern(32, 16))
    atlas.replace_composite(["A", "B", "C"], Image.fromarray(want, "RGBA"))
    built = atlas.build(split_composite=True)
    assert "pma: true" in built.atlas_text
    out = np.zeros((16, 32, 4))
    for name in "CBA":                                              # A is the top holder
        piece = np.asarray(built.extract(name).convert("RGBA")).astype(float)
        out = piece + out * (1 - piece[..., 3:4] / 255)
    inside = union(32, 16, SAME_RECTS)
    assert np.all(np.abs(out[inside] - want[inside]) <= 2)
    assert np.all(out[~inside] == 0)


def test_pma_extract_composite_round_trips():
    # Stacking straight would darken every overlapping semi pixel on a pma
    # page, so extract -> replace -> build would drift further each cycle.
    atlas = SpineAtlas.load(SAME_PMA, page())
    want = premultiplied(pattern(32, 16))
    inside = union(32, 16, SAME_RECTS)
    image = Image.fromarray(want, "RGBA")
    for _ in range(2):
        atlas.replace_composite(["A", "B", "C"], image)
        image = atlas.build(split_composite=True).extract_composite(["A", "B", "C"])
        got = np.asarray(image).astype(int)
        assert np.all(np.abs(got[inside] - want[inside]) <= 2)
        assert np.all(got[~inside] == 0)


MIXED_PMA = (
    "one.png\nsize: 8,8\npma: true\nP\n bounds: 0,0,4,4\n"
    "\ntwo.png\nsize: 8,8\nS\n bounds: 0,0,4,4\n"
)


def test_mixed_pma_composite_stays_straight():
    semi = Image.new("RGBA", (8, 8), (40, 20, 10, 100))
    atlas = SpineAtlas.load(MIXED_PMA, {"one.png": semi, "two.png": semi})
    p, s = atlas.extract("P"), atlas.extract("S")
    assert np.array_equal(np.asarray(atlas.extract_composite(["P", "S"])), np.asarray(Image.alpha_composite(s, p)))


SIZES = "page.png\nsize: 16,16\nL\n bounds: 0,0,8,6\nM\n bounds: 8,0,4,4\n"


@pytest.mark.parametrize("force_resize", [False, True])
def test_pma_composite_of_opaque_regions_matches_straight(force_resize):
    # Opaque pixels composite the same either way, which pins the pma
    # stack's frame, placement and resizing against the straight one.
    arr = np.asarray(page())[:16, :16]
    straight = SpineAtlas.load(SIZES, Image.fromarray(arr, "RGBA"))
    pma = SpineAtlas.load(SIZES.replace("16,16\n", "16,16\npma: true\n"), Image.fromarray(arr, "RGBA"))
    want = straight.extract_composite(["M", "L"], force_resize=force_resize)
    got = pma.extract_composite(["M", "L"], force_resize=force_resize)
    assert np.array_equal(np.asarray(got), np.asarray(want))


def test_pma_stack_clips_additive_colour_instead_of_wrapping():
    # An alpha-0 pixel with colour (additive) on top of an opaque one: over
    # gives 200 + 200 > 255, which must clip, not wrap.
    arr = np.zeros((4, 8, 4), np.uint8)
    arr[:, :4] = (200, 200, 200, 0)
    arr[:, 4:] = (200, 100, 50, 255)
    text = "page.png\nsize: 8,4\npma: true\nT\n bounds: 0,0,4,4\nB\n bounds: 4,0,4,4\n"
    atlas = SpineAtlas.load(text, Image.fromarray(arr, "RGBA"))
    got = np.asarray(atlas.extract_composite(["T", "B"]))
    assert np.all(got == [255, 255, 250, 255])


# Force-resize on pma: see test_pma_mesh.py and test_pma_raster.py (spec 2026-10-26 §4).


# Cross-page: A and C on page one, B on a second page; same 32x16 canvas.
TWO_PAGE_SAME = (
    "page.png\nsize: 64,32\n"
    "A\n bounds: 0,0,16,10\n offsets: 0,6,32,16\n"
    "C\n bounds: 28,0,12,10\n offsets: 2,0,32,16\n"
    "\nsecond.png\nsize: 64,32\n"
    "B\n bounds: 16,0,24,12\n offsets: 8,0,32,16\n rotate: 90\n"
)


def load_two(monkeypatch, text, meshes):
    monkeypatch.setattr(api, "_parse_skeleton", lambda data: {"attachments": meshes})
    return SpineAtlas.load(text, {"page.png": page(), "second.png": page()}, skeleton=b"x")


@pytest.mark.parametrize("mesh_aware", [False, True])
def test_cross_page_split_round_trips(monkeypatch, mesh_aware):
    atlas = load_two(monkeypatch, TWO_PAGE_SAME, SAME_MESH)
    want = pattern(32, 16)
    atlas.replace_composite(["A", "B", "C"], Image.fromarray(want, "RGBA"))
    built = atlas.build(packer_options=PackerOptions(mesh_aware=mesh_aware), split_composite=True)
    assert built.region("B").page == "second.png"
    inside = union(32, 16, SAME_HULLS if mesh_aware else SAME_RECTS)
    # mesh_crop must match mesh_aware (spec §3.7, r2 I2)
    got = built.extract_composite(["A", "B", "C"], force_resize=True, mesh_crop=mesh_aware)
    assert_round_trip(got, want, inside)


def test_mixed_pma_composite_warns_and_still_splits(monkeypatch):
    text = TWO_PAGE_SAME.replace("second.png\nsize: 64,32\n", "second.png\nsize: 64,32\npma: true\n")
    atlas = load_two(monkeypatch, text, SAME_MESH)
    with pytest.warns(UserWarning, match="pma"):
        atlas.replace_composite(["A", "B", "C"], Image.fromarray(pattern(32, 16), "RGBA"))
    built = atlas.build(split_composite=True)
    assert built.region("B").offsets == (8, 0, 32, 16)


@pytest.mark.parametrize("pma", [False, True])
def test_single_kind_composite_does_not_warn(monkeypatch, pma):
    text = TWO_PAGE_SAME.replace("size: 64,32\n", "size: 64,32\npma: true\n") if pma else TWO_PAGE_SAME
    atlas = load_two(monkeypatch, text, SAME_MESH)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        atlas.replace_composite(["A", "B", "C"], Image.fromarray(pattern(32, 16), "RGBA"))


def test_cross_page_split_mixes_hulls_and_rects(monkeypatch):
    # Spec 2026-10-21 §4: B (second.png) has no mesh and holds its whole rect,
    # while A and C (page.png) are cut to their hulls.
    atlas = load_two(monkeypatch, TWO_PAGE_SAME, {"A": SAME_MESH["A"], "C": SAME_MESH["C"]})
    want = pattern(32, 16)
    atlas.replace_composite(["A", "B", "C"], Image.fromarray(want, "RGBA"))
    built = atlas.build(packer_options=PackerOptions(mesh_aware=True), split_composite=True)
    inside = union(32, 16, [SAME_HULLS[0], SAME_RECTS[1], SAME_HULLS[2]])
    assert_round_trip(built.extract_composite(["A", "B", "C"], force_resize=True), want, inside)
    assert want[2, 13, 3] > 0 and built.extract("A").getpixel((13, 2))[3] == 0     # in A's rect, outside its hull
    assert built.extract("B").getpixel((30, 10))[3] == want[10, 30, 3] > 0         # in B's rect, outside B's mesh hull


def test_cross_page_nine_patch_member_falls_back(monkeypatch):
    # Spec 2026-10-21 §4: a nine-patch survivor on second.png makes the whole
    # batch fall back, page.png's A and C included.
    text = TWO_PAGE_SAME.replace(" rotate: 90\n", " rotate: 90\n split: 1,1,1,1\n")
    atlas = load_two(monkeypatch, text, SAME_MESH)
    atlas.replace_composite(["A", "B", "C"], Image.fromarray(pattern(32, 16), "RGBA"))
    split, plain = atlas.build(split_composite=True), atlas.build()
    assert split.atlas_text == plain.atlas_text
    for name in ("A", "B", "C"):
        assert split.extract(name).tobytes() == plain.extract(name).tobytes(), name
