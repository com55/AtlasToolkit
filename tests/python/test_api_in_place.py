# SPDX-FileCopyrightText: 2026 com55
# SPDX-License-Identifier: Apache-2.0
"""SpineAtlas.build(packer="*_in_place") keeps a page's layout when its edits fit (spec 2026-11-05)."""
import numpy as np
import pytest
from PIL import Image

import atlas_toolkit.api as api
from atlas_toolkit import PackerOptions, SpineAtlas


def pattern(w, h, seed=1):
    ys, xs = np.mgrid[0:h, 0:w]
    rgba = np.zeros((h, w, 4), np.uint8)
    rgba[..., 0] = (xs * 5 + seed) % 256
    rgba[..., 1] = (ys * 7 + seed * 3) % 256
    rgba[..., 2] = (xs * 11 + ys * 13 + seed) % 256
    rgba[..., 3] = 255
    return rgba


def page(w, h, rects, seed=1):
    """Opaque position pattern inside each stored (x, y, w, h) rect, (0,0,0,0) elsewhere."""
    rgba = pattern(w, h, seed)
    inside = np.zeros((h, w), bool)
    for x, y, rw, rh in rects:
        inside[y:y + rh, x:x + rw] = True
    rgba[~inside] = 0
    return Image.fromarray(rgba, "RGBA")


def img(w, h, seed=5):
    return Image.fromarray(pattern(w, h, seed), "RGBA")


def layout(built):
    return {r.name: (r.page, r.bounds, r.rotate, r.offsets) for r in built.regions}


def same_build(a, b):
    assert a.atlas_text == b.atlas_text
    assert a.pages.keys() == b.pages.keys()
    for name in a.pages:
        assert a.pages[name].tobytes() == b.pages[name].tobytes(), name


def base_of(packer):
    return packer.removesuffix("_in_place")


def refused(atlas, packer="rectangles_in_place", options=None):
    """The in-place build equals the base packer's build with split forced."""
    same_build(atlas.build(packer=packer, packer_options=options),
               atlas.build(packer=base_of(packer), packer_options=options, split_composite=True))


def mesh(monkeypatch, attachments):
    monkeypatch.setattr(api, "_parse_skeleton", lambda data: {"attachments": {
        name: {"type": "Mesh", "hull_length": 3, **geometry} for name, geometry in attachments.items()}})


UPPER_LEFT = {"uvs": [0, 0, 1, 0, 0, 1], "triangles": [0, 1, 2]}
LOWER_RIGHT = {"uvs": [1, 0, 1, 1, 0, 1], "triangles": [0, 1, 2]}

TWO = "p.png\nsize: 32,16\nA\n bounds: 0,0,16,16\nB\n bounds: 16,0,16,16\n"


def two():
    return SpineAtlas.load(TWO, {"p.png": page(32, 16, [(0, 0, 16, 16), (16, 0, 16, 16)])})



def test_split_composite_is_forced_and_shares_the_cache(monkeypatch):
    from atlas_toolkit.engine.session import AtlasSession
    calls = []
    original = AtlasSession.pack_pending
    monkeypatch.setattr(AtlasSession, "pack_pending", lambda self, **kw: calls.append(kw) or original(self, **kw))
    atlas = two()
    atlas.replace({"A": img(16, 16, 9)})
    atlas.build(packer="rectangles_in_place", split_composite=False)
    atlas.build(packer="rectangles_in_place", split_composite=True)
    assert len(calls) == 1
    assert calls[0]["split_composite"] is True and calls[0]["in_place"] is True


def test_in_place_and_base_do_not_share_the_cache(monkeypatch):
    from atlas_toolkit.engine.session import AtlasSession
    calls = []
    original = AtlasSession.pack_pending
    monkeypatch.setattr(AtlasSession, "pack_pending", lambda self, **kw: calls.append(kw) or original(self, **kw))
    atlas = two()
    atlas.replace({"A": img(16, 16, 9)})
    atlas.build(packer="rectangles", split_composite=True)
    atlas.build(packer="rectangles_in_place")
    assert [kw["in_place"] for kw in calls] == [False, True]


def test_polygons_in_place_needs_mesh():
    atlas = two()
    atlas.replace({"A": img(16, 16, 9)})
    with pytest.raises(api.SkeletonUnavailable):
        atlas.build(packer="polygons_in_place")


def test_repack_always_with_in_place_raises_even_unedited():
    with pytest.raises(ValueError, match="keeps the layout"):
        two().build(packer="rectangles_in_place", packer_options=PackerOptions(repack="always"))


def test_remove_only_keeps_the_layout():
    atlas = two()
    atlas.remove_region("B")
    built = atlas.build(packer="rectangles_in_place")
    assert layout(built)["A"] == layout(atlas)["A"]
    assert built.pages["p.png"].size == (32, 16)
    assert built.extract("A").tobytes() == atlas.extract("A").tobytes()


def test_remove_then_add_reuses_the_space():
    atlas = two()
    atlas.remove_region("B")
    atlas.add_region("N", img(6, 6))
    built = atlas.build(packer="rectangles_in_place", packer_options=PackerOptions(padding=1))
    assert built.pages["p.png"].size == (32, 16)
    assert layout(built)["A"] == layout(atlas)["A"]



def test_every_region_removed_gives_the_empty_page():
    text = TWO + "\nq.png\nsize: 8,8\nC\n bounds: 0,0,8,8\n"
    atlas = SpineAtlas.load(text, {"p.png": page(32, 16, [(0, 0, 16, 16), (16, 0, 16, 16)]),
                                   "q.png": page(8, 8, [(0, 0, 8, 8)])})
    atlas.remove_region("C")
    built = atlas.build(packer="rectangles_in_place")
    assert built.pages["q.png"].size == (10, 10)
    assert built.pages["p.png"].tobytes() == atlas.pages["p.png"].tobytes()


def test_multi_page_one_in_place_one_refused():
    text = TWO + "\nq.png\nsize: 32,16\nC\n bounds: 0,0,16,16\nD\n bounds: 16,0,16,16\n"
    atlas = SpineAtlas.load(text, {"p.png": page(32, 16, [(0, 0, 16, 16), (16, 0, 16, 16)]),
                                   "q.png": page(32, 16, [(0, 0, 16, 16), (16, 0, 16, 16)], seed=2)})
    atlas.remove_region("B")
    atlas.replace({"C": img(20, 20, 9)})          # wrong size: q.png refuses (any mod refuses in Task 2)
    built = atlas.build(packer="rectangles_in_place")
    base = atlas.build(packer="rectangles", split_composite=True)
    assert layout(built)["A"] == layout(atlas)["A"]
    q_built = [line for line in built.atlas_text.split("\n\n") if line.startswith("q.png")]
    q_base = [line for line in base.atlas_text.split("\n\n") if line.startswith("q.png")]
    assert q_built == q_base
    assert built.pages["q.png"].tobytes() == base.pages["q.png"].tobytes()


def test_session_in_place_defaults_off():
    import inspect
    from atlas_toolkit.engine.session import AtlasSession
    for fn in (AtlasSession.pack_pending, AtlasSession._build_pages):
        assert inspect.signature(fn).parameters["in_place"].default is False


TRIM = ("p.png\nsize: 48,16\nA\n bounds: 0,0,10,8\n offsets: 2,3,16,16\n{rot}"
        "B\n bounds: 16,0,12,12\n")


def trimmed(rotate=0, pma=False):
    rot = f" rotate: {rotate}\n" if rotate else ""
    text = TRIM.format(rot=rot)
    if pma:
        text = text.replace("size: 48,16\n", "size: 48,16\npma: true\n")
    a_stored = (0, 0, 8, 10) if rotate in (90, 270) else (0, 0, 10, 8)
    rects = [a_stored, (16, 0, 12, 12)]
    h = 16
    return SpineAtlas.load(text, {"p.png": page(48, h, rects)})


def edited_a(atlas, seed=9):
    """A's extract (16x16 canvas, window x 2..12, y 5..13) with new pixels inside the window."""
    arr = np.asarray(atlas.extract("A")).copy()
    arr[5:13, 2:12] = pattern(10, 8, seed)
    return arr


@pytest.mark.parametrize("rotate", [0, 90, 180, 270])
def test_extract_edit_replace_goes_in_place(rotate):
    atlas = trimmed(rotate)
    want = edited_a(atlas)
    atlas.replace({"A": Image.fromarray(want, "RGBA")})
    built = atlas.build(packer="rectangles_in_place")
    assert layout(built) == layout(atlas)
    assert built.pages["p.png"].size == (48, 16)
    assert (np.asarray(built.extract("A")) == want).all()
    assert built.extract("B").tobytes() == atlas.extract("B").tobytes()


def test_trimmed_size_mod_refuses():
    atlas = trimmed()
    atlas.replace({"A": img(10, 8, 9)})
    refused(atlas)


def test_pixel_outside_the_window_refuses():
    atlas = trimmed()
    arr = edited_a(atlas)
    arr[0, 0] = (1, 2, 3, 255)
    atlas.replace({"A": Image.fromarray(arr, "RGBA")})
    refused(atlas)


def test_window_outside_the_canvas_refuses():
    text = "p.png\nsize: 48,16\nA\n bounds: 0,0,10,8\n offsets: 2,12,16,16\nB\n bounds: 16,0,12,12\n"
    atlas = SpineAtlas.load(text, {"p.png": page(48, 16, [(0, 0, 10, 8), (16, 0, 12, 12)])})
    atlas.replace({"A": img(16, 16, 9)})
    refused(atlas)


ALIASES = ("p.png\nsize: 48,16\nA\n bounds: 0,0,10,8\n offsets: 2,3,16,16\n"
           "A2\n bounds: 0,0,10,8\n offsets: {o2}\nB\n bounds: 16,0,12,12\n")


def aliases(o2="2,3,16,16"):
    return SpineAtlas.load(ALIASES.format(o2=o2), {"p.png": page(48, 16, [(0, 0, 10, 8), (16, 0, 12, 12)])})


def test_partially_modded_alias_group_refuses():
    atlas = aliases()
    atlas.replace({"A": Image.fromarray(edited_a(atlas), "RGBA")})
    refused(atlas)


def test_aliases_with_the_same_window_go_in_place():
    atlas = aliases()
    want = edited_a(atlas)
    atlas.replace({"A": Image.fromarray(want, "RGBA"), "A2": Image.fromarray(want, "RGBA")})
    built = atlas.build(packer="rectangles_in_place")
    assert layout(built) == layout(atlas)
    assert (np.asarray(built.extract("A2")) == want).all()


def test_aliases_with_different_offsets_and_one_image_refuse():
    # Spec I-3: same sprite bytes, different windows -> different blocks.
    atlas = aliases(o2="5,6,16,16")
    full = Image.fromarray(pattern(16, 16, 7), "RGBA")
    atlas.replace({"A": full, "A2": full})
    refused(atlas)


def test_mod_then_remove_equals_a_remove_only_build():
    atlas = trimmed()
    atlas.replace({"A": Image.fromarray(edited_a(atlas), "RGBA")})
    atlas.remove_region("A")
    other = trimmed()
    other.remove_region("A")
    same_build(atlas.build(packer="rectangles_in_place"), other.build(packer="rectangles_in_place"))
    assert layout(atlas.build(packer="rectangles_in_place"))["B"] == layout(trimmed())["B"]


def test_modded_alias_with_its_partner_removed_goes_in_place():
    atlas = aliases()
    want = edited_a(atlas)
    atlas.replace({"A": Image.fromarray(want, "RGBA")})
    atlas.remove_region("A2")
    built = atlas.build(packer="rectangles_in_place")
    assert layout(built)["A"] == layout(atlas)["A"]
    assert (np.asarray(built.extract("A")) == want).all()


def test_transparent_part_of_an_unhulled_mod_is_not_free():
    # Spec C-1: mesh off, A's right half transparent; the add must not land inside A's rect.
    atlas = two()
    arr = pattern(16, 16, 9)
    arr[:, 8:] = 0
    atlas.replace({"A": Image.fromarray(arr, "RGBA")})
    atlas.add_region("N", img(3, 3))
    built = atlas.build(packer="rectangles_in_place", packer_options=PackerOptions(padding=1))
    assert layout(built)["A"] == layout(atlas)["A"]
    nx, ny = next(r.bounds[:2] for r in built.regions if r.name == "N")
    assert not (nx < 16 and ny < 16)


def test_pma_round_trip_is_byte_exact():
    atlas = trimmed(pma=True)
    want = edited_a(atlas)
    atlas.replace({"A": Image.fromarray(want, "RGBA")})
    built = atlas.build(packer="rectangles_in_place")
    assert layout(built) == layout(atlas)
    assert (np.asarray(built.extract("A")) == want).all()


def test_pma_additive_pixel_outside_the_window_refuses():
    atlas = trimmed(pma=True)
    arr = edited_a(atlas)
    arr[0, 0] = (40, 0, 0, 0)       # alpha 0 but visible on a pma page
    atlas.replace({"A": Image.fromarray(arr, "RGBA")})
    refused(atlas)


def test_straight_alpha_zero_pixel_outside_the_window_is_ignored():
    atlas = trimmed()
    arr = edited_a(atlas)
    arr[0, 0] = (40, 0, 0, 0)
    atlas.replace({"A": Image.fromarray(arr, "RGBA")})
    assert layout(atlas.build(packer="rectangles_in_place")) == layout(atlas)


OVERLAP = "p.png\nsize: 24,24\nA\n bounds: 0,0,16,16\nB\n bounds: 4,4,16,16\n"


def overlap(monkeypatch, b_mesh, pma=False):
    mesh(monkeypatch, {"A": UPPER_LEFT, "B": b_mesh})
    text = OVERLAP.replace("size: 24,24\n", "size: 24,24\npma: true\n") if pma else OVERLAP
    return SpineAtlas.load(text, {"p.png": page(24, 24, [(0, 0, 20, 20)])}, skeleton=b"x")


def test_mod_inside_its_hull_keeps_the_neighbour(monkeypatch):
    atlas = overlap(monkeypatch, LOWER_RIGHT)
    before_b = np.asarray(atlas.extract("B", mesh_crop=True)).copy()
    atlas.replace({"A": img(16, 16, 9)})
    built = atlas.build(packer="polygons_in_place")
    assert layout(built) == layout(atlas)
    after_b = np.asarray(built.extract("B", mesh_crop=True))
    visible = before_b[..., 3] > 0
    assert visible.any()
    assert (after_b[..., 3] == before_b[..., 3]).all()
    assert (after_b[visible] == before_b[visible]).all()


def test_mod_onto_a_neighbours_hull_refuses(monkeypatch):
    atlas = overlap(monkeypatch, UPPER_LEFT)    # B's hull reaches into A's hull
    atlas.replace({"A": img(16, 16, 9)})
    refused(atlas, packer="polygons_in_place")


def test_pma_additive_pixel_onto_a_neighbours_hull_refuses(monkeypatch):
    # A has no mesh (unhulled), B's hull covers part of A's rect. The page pixel at (10, 10),
    # inside B's hull, is empty; A's mod is its own extract plus an alpha-0 additive pixel
    # there, which a pma page shows: it would change a pixel B shows, so the page refuses.
    mesh(monkeypatch, {"B": UPPER_LEFT})
    text = OVERLAP.replace("size: 24,24\n", "size: 24,24\npma: true\n")
    rgba = np.asarray(page(24, 24, [(0, 0, 20, 20)])).copy()
    rgba[10, 10] = 0
    atlas = SpineAtlas.load(text, {"p.png": Image.fromarray(rgba, "RGBA")}, skeleton=b"x")
    arr = np.asarray(atlas.extract("A")).copy()
    arr[10, 10] = (40, 0, 0, 0)
    atlas.replace({"A": Image.fromarray(arr, "RGBA")})
    refused(atlas)


def test_refused_attempt_leaves_nothing_behind():
    text = TWO + "\nq.png\nsize: 32,16\nC\n bounds: 0,0,16,16\nD\n bounds: 16,0,16,16\n"
    atlas = SpineAtlas.load(text, {"p.png": page(32, 16, [(0, 0, 16, 16), (16, 0, 16, 16)]),
                                   "q.png": page(32, 16, [(0, 0, 16, 16), (16, 0, 16, 16)], seed=2)})
    atlas.replace({"A": img(16, 16, 9), "C": img(20, 20, 9)})
    first = atlas.build(packer="rectangles_in_place")
    atlas.build(packer="rectangles")                       # evicts the one-slot cache
    same_build(first, atlas.build(packer="rectangles_in_place"))
    assert layout(first)["A"] == layout(atlas)["A"]


def test_page_image_not_its_declared_size_is_not_tried_in_place():
    text = TWO.replace("size: 32,16", "size: 64,32")
    atlas = SpineAtlas.load(text, {"p.png": page(32, 16, [(0, 0, 16, 16), (16, 0, 16, 16)])})
    atlas.remove_region("B")
    refused(atlas)


def test_wrong_size_sprite_transparent_outside_the_window_refuses():
    # 2a: a 17x16 sprite whose content is only inside A's window still refuses.
    atlas = trimmed()
    arr = np.zeros((16, 17, 4), np.uint8)
    arr[5:13, 2:12] = pattern(10, 8, 9)
    atlas.replace({"A": Image.fromarray(arr, "RGBA")})
    refused(atlas)


def test_window_past_the_canvas_edge_refuses():
    # The trimmed window (x 8..18) leaves the 16-wide canvas; content only in the on-canvas part.
    text = "p.png\nsize: 48,16\nA\n bounds: 0,0,10,8\n offsets: 8,3,16,16\nB\n bounds: 16,0,12,12\n"
    atlas = SpineAtlas.load(text, {"p.png": page(48, 16, [(0, 0, 10, 8), (16, 0, 12, 12)])})
    arr = np.zeros((16, 16, 4), np.uint8)
    arr[5:13, 8:16] = pattern(8, 8, 9)
    atlas.replace({"A": Image.fromarray(arr, "RGBA")})
    refused(atlas)


def test_edited_rect_past_the_page_edge_refuses():
    text = "p.png\nsize: 32,16\nA\n bounds: 0,0,16,16\nB\n bounds: 24,0,16,16\n"
    atlas = SpineAtlas.load(text, {"p.png": page(32, 16, [(0, 0, 16, 16), (24, 0, 8, 16)])})
    atlas.replace({"B": img(16, 16, 9)})
    refused(atlas)


def test_aliases_with_content_inside_both_windows_refuse():
    # 2c: one image, content only where both aliases' windows overlap: each passes 2b alone,
    # but their blocks differ.
    atlas = aliases(o2="5,6,16,16")
    arr = np.zeros((16, 16, 4), np.uint8)
    arr[5:10, 5:12] = pattern(7, 5, 7)
    full = Image.fromarray(arr, "RGBA")
    atlas.replace({"A": full, "A2": full})
    refused(atlas)


def test_pma_mod_inside_its_hull_keeps_the_neighbour_byte_exact(monkeypatch):
    atlas = overlap(monkeypatch, LOWER_RIGHT, pma=True)
    before_b = np.asarray(atlas.extract("B", mesh_crop=True)).copy()
    atlas.replace({"A": img(16, 16, 9)})
    built = atlas.build(packer="polygons_in_place")
    assert layout(built) == layout(atlas)
    assert (np.asarray(built.extract("B", mesh_crop=True)) == before_b).all()


COMP = ("p.png\nsize: 48,16\nA\n bounds: 0,0,10,8\n offsets: 2,3,16,16\n"
        "B\n bounds: 16,0,6,12\n offsets: 8,1,16,16\nC\n bounds: 32,0,12,12\n")


def comp():
    return SpineAtlas.load(COMP, {"p.png": page(48, 16, [(0, 0, 10, 8), (16, 0, 6, 12), (32, 0, 12, 12)])})


def same_extracts(a, b, names):
    for name in names:
        x, y = np.asarray(a.extract(name)), np.asarray(b.extract(name))
        assert x.shape == y.shape, name
        ex, ey = x[..., 3] > 0, y[..., 3] > 0
        assert (ex == ey).all() and (x[ex] == y[ey]).all(), name


def test_composite_at_canvas_size_goes_in_place():
    atlas = comp()
    arr = np.asarray(atlas.extract_composite(["A", "B"])).copy()
    assert arr.shape == (16, 16, 4)
    arr[6:12, 3:14] = pattern(11, 6, 9)            # straddles both windows
    atlas.replace_composite(["A", "B"], Image.fromarray(arr, "RGBA"))
    built = atlas.build(packer="rectangles_in_place", split_composite=False)
    assert layout(built) == layout(atlas)
    same_extracts(built, atlas.build(packer="rectangles", split_composite=True), ["A", "B", "C"])
    assert built.extract("C").tobytes() == atlas.extract("C").tobytes()


def test_composite_at_another_scale_refuses():
    atlas = comp()
    atlas.replace_composite(["A", "B"], img(32, 32, 9))
    refused(atlas)


def test_composite_with_other_offsets_only_refuses():
    # Review-2 M-1: the piece is w x h but its offsets describe a 31x16 canvas.
    text = "p.png\nsize: 32,16\nA\n bounds: 0,0,10,8\n offsets: 0,8,32,16\nB\n bounds: 16,0,12,12\n"
    atlas = SpineAtlas.load(text, {"p.png": page(32, 16, [(0, 0, 10, 8), (16, 0, 12, 12)])})
    atlas.replace_composite(["A"], img(31, 16, 9))
    refused(atlas)


def test_single_nine_patch_composite_is_a_plain_mod():
    text = COMP.replace(" offsets: 2,3,16,16\n", " offsets: 2,3,16,16\n split: 1,1,1,1\n")
    atlas = SpineAtlas.load(text, {"p.png": page(48, 16, [(0, 0, 10, 8), (16, 0, 6, 12), (32, 0, 12, 12)])})
    arr = np.asarray(atlas.extract("A")).copy()
    arr[5:13, 2:12] = pattern(10, 8, 9)
    atlas.replace_composite(["A"], Image.fromarray(arr, "RGBA"))
    built = atlas.build(packer="rectangles_in_place")
    assert layout(built) == layout(atlas)
    assert "split" in built.atlas_text
    assert (np.asarray(built.extract("A")) == arr).all()


def test_multi_name_nine_patch_composite_refuses():
    text = COMP.replace(" offsets: 2,3,16,16\n", " offsets: 2,3,16,16\n split: 1,1,1,1\n")
    atlas = SpineAtlas.load(text, {"p.png": page(48, 16, [(0, 0, 10, 8), (16, 0, 6, 12), (32, 0, 12, 12)])})
    atlas.replace_composite(["A", "B"], Image.fromarray(np.asarray(comp().extract_composite(["A", "B"])), "RGBA"))
    refused(atlas)


def test_composite_over_two_pages_goes_in_place_only_where_it_fits():
    text = ("p.png\nsize: 32,16\nA\n bounds: 0,0,10,8\n offsets: 2,3,16,16\n\n"
            "q.png\nsize: 32,32\nB\n bounds: 0,0,12,12\n offsets: 4,4,20,20\n")
    atlas = SpineAtlas.load(text, {"p.png": page(32, 16, [(0, 0, 10, 8)]),
                                   "q.png": page(32, 32, [(0, 0, 12, 12)], seed=2)})
    atlas.replace_composite(["A", "B"], img(16, 16, 9))
    built = atlas.build(packer="rectangles_in_place")
    base = atlas.build(packer="rectangles", split_composite=True)
    assert layout(built)["A"] == layout(atlas)["A"]
    assert built.pages["p.png"].size == (32, 16)
    same_extracts(built, base, ["A", "B"])
    assert built.pages["q.png"].tobytes() == base.pages["q.png"].tobytes()
    assert [r for r in built.regions if r.page == "q.png"] == [r for r in base.regions if r.page == "q.png"]


ROT_COMP = ("p.png\nsize: 48,16\nA\n bounds: 0,0,10,8\n offsets: 2,3,16,16\n{rot}"
            "B\n bounds: 16,0,12,12\n offsets: 2,2,16,16\n")


@pytest.mark.parametrize("rotate", [90, 180, 270])
def test_rotated_composite_piece_goes_in_place_in_stored_orientation(rotate):
    a_stored = (0, 0, 8, 10) if rotate in (90, 270) else (0, 0, 10, 8)
    atlas = SpineAtlas.load(ROT_COMP.format(rot=f" rotate: {rotate}\n"),
                            {"p.png": page(48, 16, [a_stored, (16, 0, 12, 12)])})
    arr = np.asarray(atlas.extract_composite(["A", "B"])).copy()
    assert arr.shape == (16, 16, 4)
    arr[4:13, 3:13] = pattern(10, 9, 9)            # straddles A's window (x 2..12, y 5..13) and B's
    atlas.replace_composite(["A", "B"], Image.fromarray(arr, "RGBA"))
    built = atlas.build(packer="rectangles_in_place")
    assert layout(built) == layout(atlas)
    want_a = np.zeros((16, 16, 4), np.uint8)
    want_a[5:13, 2:12] = arr[5:13, 2:12]
    assert (np.asarray(built.extract("A")) == want_a).all()
    same_extracts(built, atlas.build(packer="rectangles", split_composite=True), ["A", "B"])


@pytest.mark.parametrize("offsets", ["30,0,32,16", "0,10,32,16"])
def test_composite_piece_clamped_to_its_canvas_refuses(offsets):
    # A's rect overflows its canvas (right / top): the split clamps the piece to the
    # canvas, so the piece's offsets equal A's but it is smaller than 10x8.
    text = f"p.png\nsize: 32,16\nA\n bounds: 0,0,10,8\n offsets: {offsets}\nB\n bounds: 16,0,12,12\n"
    atlas = SpineAtlas.load(text, {"p.png": page(32, 16, [(0, 0, 10, 8), (16, 0, 12, 12)])})
    atlas.replace_composite(["A"], img(32, 16, 9))
    refused(atlas)


def test_mod_that_erases_pixels_shared_with_a_neighbours_hull_refuses(monkeypatch):
    # A's and B's hulls overlap: B shows the shared pixels too, so clearing them from A
    # cannot be done in place (they would stay on A).
    atlas = overlap(monkeypatch, UPPER_LEFT)
    arr = np.asarray(atlas.extract("A")).copy()
    arr[:3, :10] = (200, 10, 10, 255)          # inside A's hull only
    arr[4:, 4:] = 0                             # everything over B's rect
    atlas.replace({"A": Image.fromarray(arr, "RGBA")})
    refused(atlas, packer="polygons_in_place")


def test_mod_that_keeps_pixels_shared_with_a_neighbours_hull_goes_in_place(monkeypatch):
    atlas = overlap(monkeypatch, UPPER_LEFT)
    before_b = np.asarray(atlas.extract("B", mesh_crop=True)).copy()
    arr = np.asarray(atlas.extract("A")).copy()
    arr[:3, :10] = (200, 10, 10, 255)          # inside A's hull only; the shared pixels are kept
    atlas.replace({"A": Image.fromarray(arr, "RGBA")})
    built = atlas.build(packer="polygons_in_place")
    assert layout(built) == layout(atlas)
    base = atlas.build(packer="polygons", split_composite=True)
    got, want = np.asarray(built.extract("A", mesh_crop=True)), np.asarray(base.extract("A", mesh_crop=True))
    assert (got[..., 3] == want[..., 3]).all()
    assert (got[got[..., 3] > 0] == want[want[..., 3] > 0]).all()
    after_b = np.asarray(built.extract("B", mesh_crop=True))
    visible = before_b[..., 3] > 0
    assert (after_b[..., 3] == before_b[..., 3]).all() and (after_b[visible] == before_b[visible]).all()


def test_refused_meshed_attempt_releases_its_handles(monkeypatch):
    # Spec M-5: what place_in_place allocated (mesh hull rasters) is released before the fallback repack.
    from atlas_toolkit.engine.modifier import AtlasModifier
    from atlas_toolkit.engine.session import AtlasSession
    atlas = overlap(monkeypatch, UPPER_LEFT)      # B's hull reaches into A's: A's mod refuses
    atlas.replace({"A": img(16, 16, 9)})
    seen = {}
    place, pack = AtlasModifier.place_in_place, AtlasSession._pack_page

    def spy_place(self, **kw):
        seen["before"] = self.raster.checkpoint()
        result = place(self, **kw)
        seen["refused"] = result is None
        return result

    def spy_pack(self, *args, **kw):
        seen["fallback"] = self._raster.checkpoint()
        return pack(self, *args, **kw)

    monkeypatch.setattr(AtlasModifier, "place_in_place", spy_place)
    monkeypatch.setattr(AtlasSession, "_pack_page", spy_pack)
    atlas.build(packer="polygons_in_place")
    assert seen["refused"]
    assert seen["fallback"] == seen["before"]


def test_semi_transparent_composite_goes_in_place_alpha_split():
    # Spec §7: semi-transparent pixels where A's and B's windows overlap (x 8..12, y 6..12) are
    # split between the two pieces; stacking the slots gives the edit back.
    atlas = comp()
    arr = np.asarray(atlas.extract_composite(["A", "B"])).copy()
    semi = pattern(11, 6, 9)
    semi[..., 3] = 120
    arr[6:12, 3:14] = semi
    atlas.replace_composite(["A", "B"], Image.fromarray(arr, "RGBA"))
    built = atlas.build(packer="rectangles_in_place")
    assert layout(built) == layout(atlas)
    same_extracts(built, atlas.build(packer="rectangles", split_composite=True), ["A", "B", "C"])
    a, b = np.asarray(built.extract("A")), np.asarray(built.extract("B"))
    assert (a[6:12, 8:12, 3] < 120).all() and (b[6:12, 8:12, 3] < 120).all()     # split, not copied
    back = np.asarray(built.extract_composite(["A", "B"]))
    assert (np.abs(back[6:12, 3:14].astype(int) - semi.astype(int)) <= 1).all()


def test_clearing_rgb_under_a_shared_transparent_pixel_goes_in_place(monkeypatch):
    # Spec 2d, straight page: (6, 6) is inside both hulls with alpha 0 and leftover RGB. Clearing
    # it (image editors drop RGB under alpha 0) changes nothing Spine shows, so the page stays.
    mesh(monkeypatch, {"A": UPPER_LEFT, "B": UPPER_LEFT})
    rgba = np.asarray(page(24, 24, [(0, 0, 20, 20)])).copy()
    rgba[6, 6] = (77, 88, 99, 0)
    atlas = SpineAtlas.load(OVERLAP, {"p.png": Image.fromarray(rgba, "RGBA")}, skeleton=b"x")
    arr = np.asarray(atlas.extract("A")).copy()
    arr[6, 6] = 0
    atlas.replace({"A": Image.fromarray(arr, "RGBA")})
    assert layout(atlas.build(packer="polygons_in_place")) == layout(atlas)


# --- padding that the pack never used shares its cache entry ------------------

PAD = "p.png\nsize: 32,16\nA\n bounds: 0,0,10,8\nB\n bounds: 16,0,12,12\n"


def _pad_atlas(monkeypatch):
    atlas = SpineAtlas.load(PAD, {"p.png": page(32, 16, [(0, 0, 10, 8), (16, 0, 12, 12)])})
    from atlas_toolkit.engine.session import AtlasSession
    calls = []
    original = AtlasSession.pack_pending
    monkeypatch.setattr(AtlasSession, "pack_pending", lambda self, **kw: calls.append(kw) or original(self, **kw))
    return atlas, calls


def test_in_place_fit_ignores_padding(monkeypatch):
    atlas, calls = _pad_atlas(monkeypatch)
    atlas.replace({"A": img(10, 8)})  # fits its slot: no repack, no add
    first = atlas.pack(packer="rectangles_in_place", packer_options=PackerOptions(padding=2))
    again = atlas.pack(packer="rectangles_in_place", packer_options=PackerOptions(padding=9))
    assert len(calls) == 1 and again[0] is first[0] and again[1] is first[1]
    assert atlas.pack_is_cached(packer="rectangles_in_place", packer_options=PackerOptions(padding=30))
    assert not atlas.pack_is_cached(packer="rectangles", packer_options=PackerOptions(padding=30))


def test_in_place_add_or_repack_keeps_padding_in_the_key(monkeypatch):
    atlas, calls = _pad_atlas(monkeypatch)
    atlas.add_region("N", img(3, 3))  # placed into free space with the padding as gap
    atlas.pack(packer="rectangles_in_place", packer_options=PackerOptions(padding=2))
    atlas.pack(packer="rectangles_in_place", packer_options=PackerOptions(padding=3))
    assert len(calls) == 2
    atlas, calls = _pad_atlas(monkeypatch)
    atlas.replace({"A": img(20, 14)})  # does not fit: the page repacks
    atlas.pack(packer="rectangles_in_place", packer_options=PackerOptions(padding=2))
    atlas.pack(packer="rectangles_in_place", packer_options=PackerOptions(padding=3))
    assert len(calls) == 2


def test_pack_is_cached_without_edits_and_after_a_pack(monkeypatch):
    atlas, calls = _pad_atlas(monkeypatch)
    assert atlas.pack_is_cached(packer="rectangles", packer_options=PackerOptions(padding=7))
    atlas.replace({"A": img(20, 14)})
    assert not atlas.pack_is_cached()
    atlas.pack()
    assert atlas.pack_is_cached() and not atlas.pack_is_cached(packer="rectangles")
    assert len(calls) == 1
    with pytest.raises(ValueError):
        atlas.pack_is_cached(packer="rectangles_in_place", packer_options=PackerOptions(repack="always"))
