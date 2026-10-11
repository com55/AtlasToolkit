"""SpineAtlas.build/save: one pack, fresh objects, app-identical output, mesh across builds."""
import io

import pytest
from PIL import Image

import atlas_toolkit.api as api
from atlas_toolkit.api import SkeletonUnavailable, SpineAtlas
from atlas_toolkit.engine.session import AtlasSession
from atlas_toolkit.raster.pillow_backend import PillowRasterBackend

ATLAS = (
    "page.png\nsize: 16,8\nscale: 0.5\n"
    "Head\n bounds: 0,0,6,5\n"
    "Body\n bounds: 6,0,8,6\n"
    "Arm\n bounds: 0,5,5,3\n"
)
LEFT = {"type": "Mesh", "uvs": [0, 0, 0.5, 0, 0.5, 1, 0, 1], "triangles": [0, 1, 2, 0, 2, 3], "hull_length": 4}
RIGHT = {"type": "Mesh", "uvs": [0.5, 0, 1, 0, 1, 1, 0.5, 1], "triangles": [0, 1, 2, 0, 2, 3], "hull_length": 4}


def page():
    return Image.frombytes(
        "RGBA", (16, 8), bytes(v for y in range(8) for x in range(16) for v in (x * 15, y * 30, 100, 255)),
    )


def sprite(width, height, seed):
    return Image.frombytes(
        "RGBA", (width, height),
        bytes(v for y in range(height) for x in range(width) for v in ((x + seed) * 20 % 256, y * 40, seed, 255)),
    )


def pixels(img):
    return img.size, img.convert("RGBA").tobytes()


def with_mesh(monkeypatch, attachments):
    monkeypatch.setattr(api, "_parse_skeleton", lambda data: {"attachments": attachments})
    return SpineAtlas.load(ATLAS, page(), skeleton=b"x")


def test_build_matches_an_immediate_session():
    atlas = SpineAtlas.load(ATLAS, page())
    atlas.replace({"Head": sprite(6, 5, 1)})
    atlas.rename_region("Body", "X")
    atlas.add_region("New", sprite(3, 4, 2))
    atlas.remove_region("Arm")
    out = atlas.build(nest=True, gap=3)
    ref = AtlasSession.load_images(ATLAS, {"page.png": page()}, PillowRasterBackend())
    ref.set_nest_regions_enabled(True)
    ref.set_nest_gap_distance(3)
    ref.apply_mod_image(sprite(6, 5, 1), ["Head"])
    ref.rename_region("Body", "X")
    ref.add_region(sprite(3, 4, 2), "New")
    ref.remove_regions(["Arm"])
    text, pages = ref.save()
    assert out.atlas_text == text
    assert pixels(out.pages["page.png"]) == pixels(Image.open(io.BytesIO(pages["page.png"])))
    assert "scale: 0.5" in out.atlas_text
    assert sorted(r.name for r in out.regions) == ["Head", "New", "X"]
    assert all(not r.renamed and not r.added for r in out.regions)
    assert not out.has_changes and atlas.has_changes


def test_round_two_sequence_keeps_the_rename():
    atlas = SpineAtlas.load(ATLAS, page())
    atlas.rename_region("Head", "H2")
    atlas.add_region("Head", sprite(3, 3, 1))
    atlas.add_region("Head_2", sprite(2, 4, 2))
    atlas.remove_region("Head")
    atlas.rename_region("Head_2", "Z")
    assert sorted(r.name for r in atlas.build().regions) == ["Arm", "Body", "H2", "Z"]


def test_build_cache_returns_fresh_objects(monkeypatch):
    atlas = SpineAtlas.load(ATLAS, page())
    atlas.rename_region("Head", "H2")
    calls = []
    original = AtlasSession.pack_pending
    monkeypatch.setattr(AtlasSession, "pack_pending", lambda self: calls.append(1) or original(self))
    first = atlas.build()
    first.rename_region("Body", "Y")
    second = atlas.build(gap=9)  # gap ignored without nest
    assert len(calls) == 1
    assert second is not first and not second.has_changes
    assert sorted(r.name for r in second.regions) == ["Arm", "Body", "H2"]
    atlas.build(nest=True)
    atlas.build(nest=True, gap=4.5)  # normalize_gap(4.5) == 4
    assert len(calls) == 2
    atlas.rename_region("Arm", "A2")
    atlas.build(nest=True)
    assert len(calls) == 3


def test_unedited_build_is_a_fresh_copy_without_packing(monkeypatch):
    atlas = SpineAtlas.load(ATLAS, page())
    monkeypatch.setattr(AtlasSession, "pack_pending", lambda self: pytest.fail("packed"))
    one = atlas.build(nest=True)
    one.rename_region("Head", "H")
    two = atlas.build(nest=True)
    assert [r.name for r in two.regions] == ["Head", "Body", "Arm"]
    assert pixels(two.pages["page.png"]) == pixels(page())


def test_failed_build_keeps_edits_cache_and_settings(monkeypatch):
    atlas = SpineAtlas.load(ATLAS, page())
    atlas.rename_region("Head", "H2")
    good = atlas.build()
    original = AtlasSession.pack_pending
    monkeypatch.setattr(AtlasSession, "pack_pending", lambda self: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        atlas.build(nest=True, gap=7)
    snap = atlas._session.snapshot()
    assert (snap.nest_regions_enabled, snap.nest_gap_distance, snap.mesh_aware_repack_enabled) == (False, 4, False)
    assert atlas.has_changes and [r.name for r in atlas.regions][0] == "H2"
    monkeypatch.setattr(AtlasSession, "pack_pending", lambda self: pytest.fail("cache not used"))
    assert atlas.build().atlas_text == good.atlas_text
    monkeypatch.setattr(AtlasSession, "pack_pending", original)


def test_save_writes_canonical_text_and_pages(tmp_path):
    atlas = SpineAtlas.load("page.png\nsize:16,8\nHead\nbounds:0,0,6,5\n", page())
    atlas.save(tmp_path / "out" / "a.atlas")
    assert (tmp_path / "out" / "a.atlas").read_text(encoding="utf-8") == atlas._session.save()[0]
    reopened = SpineAtlas.open(tmp_path / "out" / "a.atlas")
    assert reopened.atlas_text == atlas._session.save()[0]
    assert pixels(reopened.pages["page.png"]) == pixels(page())
    edited = SpineAtlas.load(ATLAS, page())
    edited.replace({"Head": sprite(6, 5, 3)})
    edited.save(tmp_path / "e.atlas", nest=True)
    again = SpineAtlas.open(tmp_path / "e.atlas")
    built = edited.build(nest=True)
    assert again.atlas_text == built.atlas_text
    assert pixels(again.extract("Head")) == pixels(built.extract("Head"))


def test_no_png_work_from_memory(monkeypatch):
    counts = {"png": 0}
    for method in ("encode_png", "decode_png"):
        original = getattr(PillowRasterBackend, method)

        def counted(self, value, _original=original):
            counts["png"] += 1
            return _original(self, value)

        monkeypatch.setattr(PillowRasterBackend, method, counted)
    atlas = SpineAtlas.load(ATLAS, page())
    atlas.extract_all()
    atlas.replace({"Head": sprite(6, 5, 1)})
    atlas.add_region("New", sprite(2, 2, 1))
    atlas.build(nest=True).extract("New")
    assert counts["png"] == 0


def test_mesh_aware_requires_mesh_even_unedited():
    with pytest.raises(SkeletonUnavailable):
        SpineAtlas.load(ATLAS, page()).build(mesh_aware=True)


def test_renamed_region_keeps_mesh_in_its_own_build(monkeypatch):
    atlas = with_mesh(monkeypatch, {"Head": LEFT})
    atlas.rename_region("Head", "H2")
    built = atlas.build(mesh_aware=True)
    h2 = built.extract("H2")
    # mesh-aware repack masks the sprite with Head's LEFT mesh (key unchanged by the rename)
    assert h2.getpixel((1, 2))[3] == 255
    assert h2.getchannel("A").getextrema()[0] == 0
    plain = atlas.build().extract("H2")
    assert plain.getchannel("A").getextrema()[0] == 255


def test_swap_rename_gets_no_mesh_after_build(monkeypatch):
    atlas = with_mesh(monkeypatch, {"Head": LEFT, "Body": RIGHT})
    atlas.rename_region("Body", "X")
    atlas.rename_region("Head", "Body")
    built = atlas.build()
    body = built.extract("Body", mesh_crop=True)  # former Head pixels; RIGHT would erase x < 3
    assert body.getpixel((0, 2))[3] == 255 and body.getpixel((5, 2))[3] == 255
    again = built.build()
    assert again.extract("Body", mesh_crop=True).getpixel((0, 2))[3] == 255
    # control: an unrenamed region on the built atlas is still masked
    plain = with_mesh(monkeypatch, {"Arm": LEFT})
    plain.rename_region("Head", "H")
    arm = plain.build().extract("Arm", mesh_crop=True)
    assert arm.getpixel((0, 1))[3] == 255 and arm.getpixel((4, 1))[3] == 0


def test_rename_back_keeps_mesh_and_added_lookalike_does_not(monkeypatch):
    atlas = with_mesh(monkeypatch, {"Head": LEFT, "Arm": LEFT})
    atlas.rename_region("Head", "H2")
    atlas.rename_region("H2", "Head")
    atlas.rename_region("Arm", "A2")
    atlas.add_region("Arm", sprite(4, 4, 9))
    built = atlas.build()
    head = built.extract("Head", mesh_crop=True)
    assert head.getpixel((1, 2))[3] == 255 and head.getpixel((5, 2))[3] == 0
    added = built.extract("Arm", mesh_crop=True)
    assert added.getpixel((3, 1))[3] == 255


def test_every_region_renamed_masks_nothing_without_raising(monkeypatch):
    atlas = with_mesh(monkeypatch, {"Head": LEFT, "Body": RIGHT, "Arm": LEFT})
    for old, new in (("Head", "a"), ("Body", "b"), ("Arm", "c")):
        atlas.rename_region(old, new)
    built = atlas.build()
    assert built.extract("a", mesh_crop=True).getpixel((5, 2))[3] == 255
    built.build(mesh_aware=True)


def test_save_keeps_page_files_inside_the_atlas_folder(tmp_path):
    for name in ("../escape.png", "/abs/escape.png"):
        text = ATLAS.replace("page.png", name, 1)
        with pytest.raises(api.AtlasError):
            SpineAtlas.load(text, page()).save(tmp_path / "out" / "a.atlas")
    assert not (tmp_path / "out").exists() and not (tmp_path / "escape.png").exists()
    nested = SpineAtlas.load(ATLAS.replace("page.png", "sub/page.png", 1), page())
    nested.save(tmp_path / "n" / "a.atlas")
    reopened = SpineAtlas.open(tmp_path / "n" / "a.atlas")
    assert pixels(reopened.pages["sub/page.png"]) == pixels(page())


def test_edited_second_build_still_withholds_a_swapped_mesh(monkeypatch):
    # The parent-allowed clause of _child_allowed: generation 2 is built from an
    # EDITED generation 1, so it goes through _child_allowed, not the pass-through.
    atlas = with_mesh(monkeypatch, {"Head": LEFT, "Body": RIGHT, "Arm": LEFT})
    atlas.rename_region("Body", "X")
    atlas.rename_region("Head", "Body")
    first = atlas.build()
    first.replace({"Arm": sprite(5, 3, 4)})
    second = first.build()
    assert second.extract("Body", mesh_crop=True).getpixel((0, 2))[3] == 255
    arm = second.extract("Arm", mesh_crop=True)
    assert arm.getpixel((0, 1))[3] == 255 and arm.getpixel((4, 1))[3] == 0


def test_added_region_named_like_a_skeleton_mesh_gets_no_mesh(monkeypatch):
    # The pristine-key clause of _child_allowed: "New" is not in the atlas, so its
    # key equals its name, and only the pristine check keeps the mesh away.
    atlas = with_mesh(monkeypatch, {"New": LEFT, "Arm": LEFT})
    atlas.add_region("New", sprite(4, 4, 9))
    built = atlas.build()
    assert built.extract("New", mesh_crop=True).getpixel((3, 1))[3] == 255
    arm = built.extract("Arm", mesh_crop=True)
    assert arm.getpixel((0, 1))[3] == 255 and arm.getpixel((4, 1))[3] == 0
