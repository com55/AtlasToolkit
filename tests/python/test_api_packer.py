"""SpineAtlas.build/save(packer=..., packer_options=...) (spec 2026-10-22 §2, §7.2)."""
import importlib.util
from pathlib import Path

import pytest
from PIL import Image

import atlas_toolkit
import atlas_toolkit.engine.modifier as modifier_module
from atlas_toolkit import Packer, PackerOptions, SkeletonUnavailable, SpineAtlas
from atlas_toolkit.engine.session import AtlasSession


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
    assert atlas_toolkit.Packer is Packer and atlas_toolkit.PackerOptions is PackerOptions


# --- test 3b: shelf padding through the API ---------------------------------

@pytest.mark.parametrize("split", [False, True])
@pytest.mark.parametrize("padding", [1, 2, 5])
def test_shelf_padding_through_build(padding, split):
    built = golden.edited_atlas("single").build(
        packer="shelf", packer_options=PackerOptions(padding=padding), split_composite=split,
    )
    assert_spaced(built, "page.png", padding)


# --- test 4: rectangles + mesh_aware ------------------------------------------

@pytest.mark.parametrize("split", [False, True])
def test_rectangles_with_mesh_aware_masks_pixels_but_packs_solid_rects(monkeypatch, split):
    seen = []
    real = modifier_module.nest_pack

    def recording(items, gap_distance=4):
        seen.extend(items)
        return real(items, gap_distance=gap_distance)

    monkeypatch.setattr(modifier_module, "nest_pack", recording)  # the name the modifier calls
    built = golden.edited_atlas("single").build(
        packer="rectangles", packer_options=PackerOptions(mesh_aware=True), split_composite=split,
    )
    assert seen and all(set(item["footprint"]) == {1} for item in seen)
    t1 = built.extract("T1")  # upper-left triangle mesh
    assert t1.getpixel((1, 1))[3] == 255
    assert t1.getpixel((39, 39))[3] == 0
    seen.clear()
    golden.edited_atlas("single").build(packer="polygons", split_composite=split)
    assert seen and not all(set(item["footprint"]) == {1} for item in seen)  # the probe can see a mesh


# --- test 5: validation, before the unedited fast path ----------------------

@pytest.mark.parametrize("packer, options, error", [
    ("grid", None, NotImplementedError),
    ("rectangles_in_place", None, NotImplementedError),
    ("polygons_in_place", None, NotImplementedError),
    ("nest", None, ValueError),
    (3, None, TypeError),
    ("shelf", {"padding": 2}, TypeError),
    ("shelf", PackerOptions(padding=0), ValueError),
    ("shelf", PackerOptions(padding=257), ValueError),
    ("rectangles", PackerOptions(padding=True), TypeError),
    ("rectangles", PackerOptions(padding=2.5), TypeError),
    ("shelf", PackerOptions(mesh_aware="yes"), TypeError),
    ("polygons", PackerOptions(mesh_aware=False), ValueError),
])
def test_invalid_pack_settings_raise_even_unedited(packer, options, error):
    for atlas in (plain_atlas(), meshed_atlas()):
        assert not atlas.has_changes
        with pytest.raises(error):
            atlas.build(packer=packer, packer_options=options)


@pytest.mark.parametrize("packer, mesh_aware", [
    ("polygons", None), ("polygons", True), ("shelf", True), ("rectangles", True),
])
def test_mesh_settings_raise_without_mesh_even_unedited(packer, mesh_aware):
    with pytest.raises(SkeletonUnavailable):
        plain_atlas().build(packer=packer, packer_options=PackerOptions(mesh_aware=mesh_aware))


@pytest.mark.parametrize("packer", ["shelf", "rectangles"])
@pytest.mark.parametrize("mesh_aware", [None, False])
def test_optional_mesh_builds_without_mesh(packer, mesh_aware):
    built = golden.edited_atlas("single", skeleton=False).build(
        packer=packer, packer_options=PackerOptions(mesh_aware=mesh_aware),
    )
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
    atlas = golden.edited_atlas("single")  # mesh loaded: auto is on
    atlas.build()
    atlas.build(packer=Packer.SHELF, packer_options=PackerOptions(padding=2))
    atlas.build(packer_options=PackerOptions(mesh_aware=True))
    assert len(calls) == 1
    atlas.build(packer_options=PackerOptions(padding=3))
    assert len(calls) == 2
    atlas.build(packer="rectangles")
    atlas.build(packer="rectangles", split_composite=True)
    assert len(calls) == 4


# --- test 7: no carry-over between builds ---------------------------------------

def test_settings_do_not_carry_over_between_builds():
    atlas = golden.edited_atlas("single")
    sequence = [
        ("polygons", PackerOptions()),
        ("rectangles", PackerOptions(mesh_aware=False)),
        ("shelf", PackerOptions(padding=5)),
        ("polygons", PackerOptions(padding=7)),
    ]
    for packer, options in sequence:
        got = atlas.build(packer=packer, packer_options=options)
        fresh = golden.edited_atlas("single").build(packer=packer, packer_options=options)
        assert summary(got) == summary(fresh), packer


# --- test 8: save() forwarding ---------------------------------------------------

def test_save_writes_what_build_returns(tmp_path):
    atlas = golden.edited_atlas("single")
    options = PackerOptions(padding=3)
    atlas.save(tmp_path / "o.atlas", packer="rectangles", packer_options=options, split_composite=True)
    built = atlas.build(packer="rectangles", packer_options=options, split_composite=True)
    assert (tmp_path / "o.atlas").read_text(encoding="utf-8") == built.atlas_text
    with Image.open(tmp_path / "page.png") as saved:
        assert saved.convert("RGBA").tobytes() == built.pages["page.png"].convert("RGBA").tobytes()


@pytest.mark.parametrize("kwargs", [{"nest": True}, {"gap": 4}, {"mesh_aware": True}])
def test_old_pack_flags_are_gone(tmp_path, kwargs):
    atlas = golden.edited_atlas("single")
    with pytest.raises(TypeError):
        atlas.build(**kwargs)
    with pytest.raises(TypeError):
        atlas.save(tmp_path / "o.atlas", **kwargs)


# --- test 9: defaults -------------------------------------------------------------

def test_default_build_is_shelf_with_padding_two():
    assert summary(golden.edited_atlas("single").build()) == summary(
        golden.edited_atlas("single").build(packer="shelf", packer_options=PackerOptions(padding=2))
    )


def test_auto_masks_only_when_mesh_is_loaded():
    masked = golden.edited_atlas("single").build().extract("T1")
    assert masked.getpixel((1, 1))[3] == 255 and masked.getpixel((39, 39))[3] == 0
    plain = golden.edited_atlas("single", skeleton=False).build().extract("T1")
    assert plain.getpixel((39, 39))[3] == 255


def test_auto_leaves_an_untouched_page_alone():
    built = golden.edited_atlas("multi").build()  # mesh loaded, auto on; edits only on page.png
    page_two = built.pages["page2.png"].convert("RGBA")
    assert page_two.tobytes() == golden.pattern(64, 32, 2).tobytes()


def test_auto_split_uses_hull_ownership():
    auto = summary(golden.edited_atlas("single").build(split_composite=True))
    on = summary(golden.edited_atlas("single").build(
        packer_options=PackerOptions(mesh_aware=True), split_composite=True))
    off = summary(golden.edited_atlas("single").build(
        packer_options=PackerOptions(mesh_aware=False), split_composite=True))
    assert auto == on
    assert auto["pages"] != off["pages"]
