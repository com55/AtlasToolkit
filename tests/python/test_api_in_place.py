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


@pytest.mark.parametrize("packer", ["rectangles_in_place", "polygons_in_place"])
def test_in_place_builds_like_the_base_packer_for_now(monkeypatch, packer):
    # Task 1 only plumbs the mode: a page with a mod still repacks.
    mesh(monkeypatch, {"A": UPPER_LEFT, "B": LOWER_RIGHT})
    atlas = SpineAtlas.load(TWO, {"p.png": page(32, 16, [(0, 0, 16, 16), (16, 0, 16, 16)])}, skeleton=b"x")
    atlas.replace({"A": img(16, 16, 9)})
    refused(atlas, packer)


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


def test_a_replace_still_repacks_in_task_2():
    atlas = two()
    atlas.replace({"A": img(16, 16, 9)})
    refused(atlas)


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
