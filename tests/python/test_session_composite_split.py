"""Session wiring for composite split (spec 2026-10-13 §3.1, §3.5)."""
from PIL import Image

import atlas_toolkit.api as api
from atlas_toolkit.api import SpineAtlas

ATLAS = (
    "page.png\nsize: 64,32\n"
    "A\n bounds: 0,0,16,10\n offsets: 0,6,32,16\n"
    "C\n bounds: 28,0,12,10\n offsets: 2,0,32,16\n"
    "Other\n bounds: 40,0,6,6\n"
)
QUAD = [0, 1, 2, 0, 2, 3]


def mesh(u0, v0, u1, v1):
    return {"type": "Mesh", "uvs": [u0, v0, u1, v0, u1, v1, u0, v1], "triangles": QUAD, "hull_length": 4}


MESHES = {"A": mesh(0, 0, 0.375, 0.5), "C": mesh(0.125, 0.375, 0.375, 1.0)}


def page():
    return Image.new("RGBA", (64, 32), (50, 60, 70, 255))


def loaded(monkeypatch, meshes=MESHES):
    monkeypatch.setattr(api, "_parse_skeleton", lambda data: {"attachments": meshes})
    return SpineAtlas.load(ATLAS, page(), skeleton=b"x")


def composite():
    return Image.new("RGBA", (32, 16), (255, 0, 0, 255))


def pack(atlas, split):
    session = atlas._session
    session.set_mesh_aware_repack_enabled(True)
    try:
        text, pages = session.pack_pending(split_composite=split) if split else session.pack_pending()
    finally:
        session.set_mesh_aware_repack_enabled(False)
    return SpineAtlas.load(text, pages)


def test_replace_composite_marks_its_batch_and_replace_does_not(monkeypatch):
    atlas = loaded(monkeypatch)
    atlas.replace_composite(["A", "C"], composite())
    atlas.replace({"Other": Image.new("RGBA", (6, 6), (0, 0, 255, 255))})
    marks = [b.get("composite", False) for b in atlas._session._mod_batches]
    assert marks == [True, False]
    assert atlas._session._mod_batches[0]["names"] == ["A", "C"]


def test_split_pieces_keep_their_own_offsets_and_size(monkeypatch):
    atlas = loaded(monkeypatch)
    atlas.replace_composite(["A", "C"], composite())
    built = pack(atlas, True)
    assert built.region("A").offsets == (0, 6, 32, 16)
    assert built.region("A").bounds[2:] == (16, 10)
    assert built.region("C").offsets == (2, 0, 32, 16)


def test_replace_batches_are_never_split(monkeypatch):
    # replace() of one region with a canvas-sized image: is_full_canvas, but
    # not composite-marked, so the switch changes nothing.
    atlas = loaded(monkeypatch)
    atlas.replace({"A": composite()})
    on, off = pack(atlas, True), pack(atlas, False)
    assert on.atlas_text == off.atlas_text
    assert on.pages["page.png"].tobytes() == off.pages["page.png"].tobytes()


def test_group_with_a_member_without_mesh_falls_back(monkeypatch):
    atlas = loaded(monkeypatch, {"A": MESHES["A"]})
    atlas.replace_composite(["A", "C"], composite())
    on, off = pack(atlas, True), pack(atlas, False)
    assert on.atlas_text == off.atlas_text


def test_nine_patch_member_falls_back(monkeypatch):
    monkeypatch.setattr(api, "_parse_skeleton", lambda data: {"attachments": MESHES})
    text = ATLAS.replace(" offsets: 2,0,32,16\n", " offsets: 2,0,32,16\n split: 1,1,1,1\n")
    atlas = SpineAtlas.load(text, page(), skeleton=b"x")
    atlas.replace_composite(["A", "C"], composite())
    assert pack(atlas, True).atlas_text == pack(atlas, False).atlas_text


def test_repeated_names_are_recorded_as_given_and_split_once(monkeypatch):
    atlas = loaded(monkeypatch)
    atlas.replace_composite(["A", "A", "C"], composite())
    assert atlas._session._mod_batches[0]["names"] == ["A", "A", "C"]
    built = pack(atlas, True)
    assert built.region("A").offsets == (0, 6, 32, 16)


def test_split_build_leaves_session_sprites_untouched(monkeypatch):
    atlas = loaded(monkeypatch)
    atlas.replace_composite(["A", "C"], composite())
    before = dict(atlas._session._modded_sprites)
    pack(atlas, True)
    assert atlas._session._modded_sprites == before
    fresh = loaded(monkeypatch)
    fresh.replace_composite(["A", "C"], composite())
    assert pack(atlas, False).atlas_text == pack(fresh, False).atlas_text


def test_member_with_a_zero_size_canvas_falls_back(monkeypatch):
    # Malformed offsets (canvas 0x0) cannot be scaled: the group keeps the
    # old behaviour instead of raising ZeroDivisionError.
    monkeypatch.setattr(api, "_parse_skeleton", lambda data: {"attachments": MESHES})
    text = ATLAS.replace(" offsets: 2,0,32,16\n", " offsets: 2,0,0,0\n")
    atlas = SpineAtlas.load(text, page(), skeleton=b"x")
    atlas.replace_composite(["A", "C"], composite())
    assert pack(atlas, True).atlas_text == pack(atlas, False).atlas_text
