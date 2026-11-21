# SPDX-FileCopyrightText: 2026 com55
# SPDX-License-Identifier: Apache-2.0
"""SpineAtlas.build/save(options=...) (spec 2026-11-18).

Tests about a repack pin `try_keep_layout=False` (the old always-repack
behaviour); `share_composite_image=True` is the old non-splitting default.
"""
import importlib.util
from pathlib import Path

import pytest
from PIL import Image

import spine_atlas_toolkit
import spine_atlas_toolkit.engine.modifier as modifier_module
from spine_atlas_toolkit import PackOptions, Packer, SkeletonUnavailable, SpineAtlas
from spine_atlas_toolkit.engine.session import AtlasSession

REPACK = {"try_keep_layout": False, "share_composite_image": True}


def _load_golden():
    path = Path(__file__).with_name("gen_packer_refactor_golden.py")
    spec = importlib.util.spec_from_file_location("gen_packer_refactor_golden", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


golden = _load_golden()


def plain_atlas():
    return SpineAtlas.load(golden.PAGE_ONE, {"page.png": golden.pattern(256, 128, 1)})


def meshed_atlas():
    with golden.fake_skeleton():
        return SpineAtlas.load(golden.PAGE_ONE, {"page.png": golden.pattern(256, 128, 1)}, skeleton=b"x")


def summary(built):
    return golden.summarize(built.atlas_text, built.pages)


def footprint(region):
    x, y, w, h = region.bounds
    return (x, y, h, w) if region.rotate in (90, 270) else (x, y, w, h)


def assert_spaced(built, page, padding):
    rects = sorted({footprint(r) for r in built.regions if r.page == page})  # dedup shares a rect
    assert len(rects) > 3
    for index, (ax, ay, aw, ah) in enumerate(rects):
        for bx, by, bw, bh in rects[index + 1:]:
            assert (
                ax + aw + padding <= bx or bx + bw + padding <= ax
                or ay + ah + padding <= by or by + bh + padding <= ay
            ), ((ax, ay, aw, ah), (bx, by, bw, bh))


def test_public_names():
    assert spine_atlas_toolkit.Packer is Packer and spine_atlas_toolkit.PackOptions is PackOptions


# --- test 3b: shelf padding through the API ---------------------------------

@pytest.mark.parametrize("split", [False, True])
@pytest.mark.parametrize("padding", [1, 2, 5])
def test_shelf_padding_through_build(padding, split):
    built = golden.edited_atlas("single").build(options=PackOptions(
        packer="shelf", padding=padding, share_composite_image=not split, try_keep_layout=False,
    ))
    assert_spaced(built, "page.png", padding)
    rects = sorted({footprint(r) for r in built.regions if r.page == "page.png"})
    gaps = {bx - (ax + aw) for (ax, ay, aw, ah) in rects for (bx, by, bw, bh) in rects if ay == by and bx > ax}
    assert min(gaps) == padding, gaps


# --- test 4: rectangles + mesh_aware ------------------------------------------

@pytest.mark.parametrize("split", [False, True])
def test_rectangles_with_mesh_aware_masks_pixels_but_packs_solid_rects(monkeypatch, split):
    seen = []
    real = modifier_module.nest_pack

    def recording(items, gap_distance=4):
        seen.extend(items)
        return real(items, gap_distance=gap_distance)

    monkeypatch.setattr(modifier_module, "nest_pack", recording)  # the name the modifier calls
    built = golden.edited_atlas("single").build(options=PackOptions(
        packer="rectangles", mesh_aware=True, share_composite_image=not split, try_keep_layout=False,
    ))
    assert seen and all(set(item["footprint"]) == {1} for item in seen)
    t1 = built.extract("T1")  # upper-left triangle mesh
    assert t1.getpixel((1, 1))[3] == 255
    assert t1.getpixel((39, 39))[3] == 0
    seen.clear()
    golden.edited_atlas("single").build(options=PackOptions(
        packer="polygons", share_composite_image=not split, try_keep_layout=False,
    ))
    assert seen and not all(set(item["footprint"]) == {1} for item in seen)  # the probe can see a mesh


# --- test 5: validation, before the unedited fast path ----------------------

@pytest.mark.parametrize("options, error", [
    (PackOptions(packer="grid"), ValueError),
    (PackOptions(packer="rectangles_in_place"), ValueError),
    (PackOptions(packer="polygons", mesh_aware=False), ValueError),
    (PackOptions(packer="nest"), ValueError),
    (PackOptions(packer=3), TypeError),
    ({"padding": 2}, TypeError),
    (PackOptions(padding=0), ValueError),
    (PackOptions(padding=257), ValueError),
    (PackOptions(padding=True), TypeError),
    (PackOptions(padding=2.5), TypeError),
    (PackOptions(mesh_aware="yes"), TypeError),
])
def test_invalid_pack_settings_raise_even_unedited(options, error):
    for atlas in (plain_atlas(), meshed_atlas()):
        assert not atlas.has_changes
        with pytest.raises(error):
            atlas.build(options=options)


@pytest.mark.parametrize("packer, mesh_aware", [
    ("polygons", None), ("polygons", True), ("shelf", True), ("rectangles", True),
])
def test_mesh_settings_raise_without_mesh_even_unedited(packer, mesh_aware):
    with pytest.raises(SkeletonUnavailable):
        plain_atlas().build(options=PackOptions(packer=packer, mesh_aware=mesh_aware))


@pytest.mark.parametrize("packer", ["shelf", "rectangles"])
@pytest.mark.parametrize("mesh_aware", [None, False])
def test_optional_mesh_builds_without_mesh(packer, mesh_aware):
    built = golden.edited_atlas("single", skeleton=False).build(options=PackOptions(
        packer=packer, mesh_aware=mesh_aware, try_keep_layout=False, share_composite_image=True,
    ))
    assert built.extract("T1").getpixel((39, 39))[3] == 255  # nothing masked


# --- test 6: cache -------------------------------------------------------------

def count_packs(monkeypatch):
    calls = []
    original = AtlasSession.pack_pending

    def counted(self, *args, **kwargs):
        calls.append(kwargs)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(AtlasSession, "pack_pending", counted)
    return calls


def test_equivalent_settings_share_the_build_cache(monkeypatch):
    calls = count_packs(monkeypatch)
    atlas = golden.edited_atlas("single")  # mesh loaded: auto resolves to polygons
    atlas.build()
    atlas.build(options=PackOptions(packer="polygons"))
    atlas.build(options=PackOptions(packer=Packer.POLYGONS, mesh_aware=True))  # another spelling
    assert len(calls) == 1
    atlas.build(options=PackOptions(packer="polygons", padding=3))
    assert len(calls) == 2
    atlas.build(options=PackOptions(packer="rectangles"))
    atlas.build(options=PackOptions(packer="rectangles", share_composite_image=True))
    assert len(calls) == 4


def test_auto_shares_the_cache_with_the_packer_it_picks(monkeypatch):
    calls = count_packs(monkeypatch)
    atlas = golden.edited_atlas("single")  # .skel: auto is polygons
    atlas._app_pack()
    assert atlas._app_pack_is_cached(options=PackOptions(packer="polygons"))
    assert len(calls) == 1
    plain = golden.edited_atlas("single", skeleton=False)  # no mesh: auto is rectangles
    plain._app_pack()
    assert plain._app_pack_is_cached(options=PackOptions(packer="rectangles"))
    assert len(calls) == 2


# --- test 7: no carry-over between builds ---------------------------------------

def test_settings_do_not_carry_over_between_builds():
    atlas = golden.edited_atlas("single")
    sequence = [
        PackOptions(packer="polygons"),
        PackOptions(packer="rectangles", mesh_aware=False),
        PackOptions(packer="shelf", padding=5),
        PackOptions(packer="polygons", padding=7),
    ]
    for options in sequence:
        got = atlas.build(options=options)
        fresh = golden.edited_atlas("single").build(options=options)
        assert summary(got) == summary(fresh), options.packer


# --- test 8: save() forwarding ---------------------------------------------------

def test_save_writes_what_build_returns(tmp_path):
    atlas = golden.edited_atlas("single")
    options = PackOptions(packer="rectangles", padding=3, **REPACK)
    atlas.save(tmp_path / "o.atlas", options=options)
    built = atlas.build(options=options)
    assert (tmp_path / "o.atlas").read_text(encoding="utf-8") == built.atlas_text
    with Image.open(tmp_path / "page.png") as saved:
        assert saved.convert("RGBA").tobytes() == built.pages["page.png"].convert("RGBA").tobytes()


@pytest.mark.parametrize("kwargs", [
    {"nest": True}, {"gap": 4}, {"mesh_aware": True},
    # the pre-PackOptions keywords (spec 2026-11-18)
    {"packer": "shelf"}, {"packer_options": None}, {"split_composite": True},
])
def test_old_pack_flags_are_gone(tmp_path, kwargs):
    atlas = golden.edited_atlas("single")
    with pytest.raises(TypeError):
        atlas.build(**kwargs)
    with pytest.raises(TypeError):
        atlas.save(tmp_path / "o.atlas", **kwargs)


# --- test 9: defaults -------------------------------------------------------------

def test_default_build_is_auto():
    assert summary(golden.edited_atlas("single").build()) == summary(
        golden.edited_atlas("single").build(options=PackOptions())
    )
    # With a .skel auto is polygons; without one it is rectangles.
    assert summary(golden.edited_atlas("single").build()) == summary(
        golden.edited_atlas("single").build(options=PackOptions(packer="polygons"))
    )
    assert summary(golden.edited_atlas("single", skeleton=False).build()) == summary(
        golden.edited_atlas("single", skeleton=False).build(options=PackOptions(packer="rectangles"))
    )


def test_auto_masks_only_when_mesh_is_loaded():
    # shelf pins the geometry (a nest pack rotates T1, moving these pixels);
    # mesh_aware stays auto, which is what this pins.
    masked = golden.edited_atlas("single").build(options=PackOptions(
        packer="shelf", try_keep_layout=False)).extract("T1")
    assert masked.getpixel((1, 1))[3] == 255 and masked.getpixel((39, 39))[3] == 0
    plain = golden.edited_atlas("single", skeleton=False).build(options=PackOptions(
        packer="shelf", try_keep_layout=False)).extract("T1")
    assert plain.getpixel((39, 39))[3] == 255


def test_auto_leaves_an_untouched_page_alone():
    built = golden.edited_atlas("multi").build()  # mesh loaded, auto on; edits only on page.png
    page_two = built.pages["page2.png"].convert("RGBA")
    assert page_two.tobytes() == golden.pattern(64, 32, 2).tobytes()


def test_auto_split_uses_hull_ownership():
    auto = summary(golden.edited_atlas("single").build())  # split is the default
    on = summary(golden.edited_atlas("single").build(options=PackOptions(mesh_aware=True)))
    off = summary(golden.edited_atlas("single").build(options=PackOptions(mesh_aware=False)))
    assert auto == on
    assert auto["pages"] != off["pages"]


def test_default_build_splits_a_composite():
    split = summary(golden.edited_atlas("single").build())
    explicit = summary(golden.edited_atlas("single").build(
        options=PackOptions(share_composite_image=False)))
    shared = summary(golden.edited_atlas("single").build(
        options=PackOptions(share_composite_image=True)))
    assert split == explicit
    assert split["pages"] != shared["pages"]


def test_try_keep_layout_false_repacks_an_add_only_page(monkeypatch):
    from spine_atlas_toolkit.engine.modifier import AtlasModifier

    calls = count_packs(monkeypatch)
    placed = []
    real_place = AtlasModifier.place_in_place

    def spy_place(self, **kwargs):
        placed.append(self.target_page)
        return real_place(self, **kwargs)

    monkeypatch.setattr(AtlasModifier, "place_in_place", spy_place)
    atlas = SpineAtlas.load(
        "page.png\nsize: 16,8\nHead\n bounds: 0,0,6,5\n",
        {"page.png": golden.pattern(16, 8, 1)},
    )
    atlas.add_region("New", golden.pattern(3, 4, 2))
    kept = atlas.build(options=PackOptions(packer="rectangles"))
    assert placed == ["page.png"]                  # the add went into free space
    assert atlas.region("Head").bounds == kept.region("Head").bounds
    assert kept.pages["page.png"].size == (16, 16)  # grown to hold the add plus its padding
    placed.clear()
    calls.clear()
    repacked = atlas.build(options=PackOptions(packer="rectangles", try_keep_layout=False))
    assert placed == []                            # never offered to place_in_place
    assert [kw["in_place"] for kw in calls] == [False]
    assert repacked.pages["page.png"].size != kept.pages["page.png"].size


# --- nest pages are trimmed to their regions' extent -------------------------
@pytest.mark.parametrize("packer", ["rectangles", "polygons"])
def test_nest_page_is_trimmed_to_region_extent(packer):
    from spine_atlas_toolkit.domain.region_ops import round_up_to_multiple
    built = golden.edited_atlas("single").build(options=PackOptions(
        packer=packer, padding=8, try_keep_layout=False,
    ))
    rects = [footprint(r) for r in built.regions if r.page == "page.png"]
    extent = (
        round_up_to_multiple(max(x + w for x, _, w, _ in rects)),
        round_up_to_multiple(max(y + h for _, y, _, h in rects)),
    )
    assert built.pages["page.png"].size == extent
