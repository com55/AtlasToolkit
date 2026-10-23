"""pack_pending's Python-only switches reach the packer (spec 2026-10-22 §4)."""
import importlib.util
from pathlib import Path

import pytest

import atlas_toolkit.engine.modifier as modifier_module
from atlas_toolkit.api import SpineAtlas


def _load_golden():
    path = Path(__file__).with_name("gen_packer_refactor_golden.py")
    spec = importlib.util.spec_from_file_location("gen_packer_refactor_golden", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


golden = _load_golden()


def record_nest_items(monkeypatch):
    """Patch the name the modifier calls, not domain.packing.nest_pack."""
    seen = []
    real = modifier_module.nest_pack

    def recording(items, gap_distance=4):
        seen.extend(items)
        return real(items, gap_distance=gap_distance)

    monkeypatch.setattr(modifier_module, "nest_pack", recording)
    return seen


def is_solid(item):
    return set(item["footprint"]) == {1}


def footprints(region):
    """(w, h) the region occupies on its page."""
    x, y, w, h = region.bounds
    return (x, y, h, w) if region.rotate in (90, 270) else (x, y, w, h)


def assert_spaced(built, page, padding):
    rects = sorted({footprints(r) for r in built.regions if r.page == page})  # dedup shares a rect
    assert len(rects) > 3
    for index, (ax, ay, aw, ah) in enumerate(rects):
        for bx, by, bw, bh in rects[index + 1:]:
            assert (
                ax + aw + padding <= bx or bx + bw + padding <= ax
                or ay + ah + padding <= by or by + bh + padding <= ay
            ), ((ax, ay, aw, ah), (bx, by, bw, bh))


@pytest.mark.parametrize("split", [False, True])
def test_shelf_padding_reaches_the_shelf_packer(split):
    text, pages = golden.session_pack(golden.edited_atlas("single"), False, None, False, split, shelf_padding=3)
    assert_spaced(SpineAtlas.load(text, pages), "page.png", 3)


@pytest.mark.parametrize("split", [False, True])
def test_mesh_footprint_off_gives_solid_footprints_but_keeps_masking(monkeypatch, split):
    seen = record_nest_items(monkeypatch)
    atlas = golden.edited_atlas("single")
    atlas.replace({"T2": golden.pattern(40, 40, 7)})  # a modded region with mesh (lower-right triangle)
    text, pages = golden.session_pack(atlas, True, 4, True, split, mesh_footprint=False)
    assert seen and all(is_solid(item) for item in seen)
    built = SpineAtlas.load(text, pages)
    t1 = built.extract("T1")  # pristine, upper-left triangle mesh
    assert t1.getpixel((1, 1))[3] == 255
    assert t1.getpixel((39, 39))[3] == 0  # outside the hull: definitely removed
    t2 = built.extract("T2")  # modded, lower-right triangle mesh
    assert t2.getpixel((38, 38))[3] == 255
    assert t2.getpixel((1, 1))[3] == 0
    if split:
        ca = built.extract("CA")  # composite piece, upper-left triangle mesh
        assert ca.getpixel((1, 1))[3] > 0
        assert ca.getpixel((38, 28))[3] == 0


def test_default_mesh_footprint_is_still_the_mesh(monkeypatch):
    seen = record_nest_items(monkeypatch)
    golden.session_pack(golden.edited_atlas("single"), True, 4, True, False)
    assert seen and not all(is_solid(item) for item in seen)


def test_shelf_padding_is_exact_between_shelf_neighbours():
    text, pages = golden.session_pack(golden.edited_atlas("single"), False, None, False, False, shelf_padding=3)
    rects = sorted({footprints(r) for r in SpineAtlas.load(text, pages).regions if r.page == "page.png"})
    gaps = {
        bx - (ax + aw)
        for (ax, ay, aw, ah) in rects
        for (bx, by, bw, bh) in rects
        if ay == by and bx > ax
    }
    assert 3 in gaps
