"""SpineAtlas.build() patches pages whose only edits are adds and renames (spec 2026-10-31)."""
import time

import numpy as np
import pytest
from PIL import Image

import atlas_toolkit.api as api
from atlas_toolkit import CanvasTooLarge, PackerOptions, SpineAtlas
from atlas_toolkit.document import AtlasDocument
from atlas_toolkit.domain.converter import auto_convert_atlas

TEXT = "p.png\nsize: 64,32\nA\n bounds: 0,0,16,16\nB\n bounds: 16,0,16,16\n"


def pattern(w, h, seed=1):
    ys, xs = np.mgrid[0:h, 0:w]
    rgba = np.zeros((h, w, 4), np.uint8)
    rgba[..., 0] = (xs * 5 + seed) % 256
    rgba[..., 1] = (ys * 7 + seed * 3) % 256
    rgba[..., 2] = (xs * 11 + ys * 13 + seed) % 256
    rgba[..., 3] = 255
    return rgba


def page(w=64, h=32, rects=((0, 0, 16, 16), (16, 0, 16, 16)), seed=1):
    rgba = pattern(w, h, seed)
    inside = np.zeros((h, w), bool)
    for x, y, rw, rh in rects:
        inside[y:y + rh, x:x + rw] = True
    rgba[~inside] = 0
    return Image.fromarray(rgba, "RGBA")


def img(w, h, seed=5):
    return Image.fromarray(pattern(w, h, seed), "RGBA")


def bounds(built, name):
    return next(tuple(r.bounds) + (r.rotate,) for r in built.regions if r.name == name)


def patched(built, filename="p.png", size=(64, 32)):
    return built.pages[filename].size == size and bounds(built, "B")[:2] == (16, 0)


def test_adds_only_patches():
    atlas = SpineAtlas.load(TEXT, {"p.png": page()})
    atlas.add_region("N", img(10, 10))
    built = atlas.build()
    assert patched(built) and bounds(built, "N") == (36, 0, 10, 10, 0)


def test_always_repacks():
    atlas = SpineAtlas.load(TEXT, {"p.png": page()})
    atlas.add_region("N", img(10, 10))
    assert not patched(atlas.build(packer_options=PackerOptions(repack="always")))


@pytest.mark.parametrize("packer", ["shelf", "rectangles"])
def test_every_packer_patches(packer):
    atlas = SpineAtlas.load(TEXT, {"p.png": page()})
    atlas.add_region("N", img(10, 10))
    assert patched(atlas.build(packer=packer))


def test_add_and_rename_patches():
    atlas = SpineAtlas.load(TEXT, {"p.png": page()})
    atlas.add_region("N", img(10, 10))
    atlas.rename_region("A", "A2")
    built = atlas.build()
    assert patched(built) and bounds(built, "A2") == (0, 0, 16, 16, 0)


def test_add_and_replace_pristine_repacks():
    atlas = SpineAtlas.load(TEXT, {"p.png": page()})
    atlas.add_region("N", img(10, 10))
    atlas.replace({"A": img(16, 16, 9)})
    assert not patched(atlas.build())


def test_add_and_remove_pristine_repacks():
    atlas = SpineAtlas.load(TEXT + "C\n bounds: 32,0,8,8\n", {"p.png": page(rects=((0, 0, 16, 16), (16, 0, 16, 16), (32, 0, 8, 8)))})
    atlas.add_region("N", img(10, 10))
    atlas.remove_region("C")
    assert not patched(atlas.build())


def test_add_two_remove_one_patches_the_survivor():
    atlas = SpineAtlas.load(TEXT, {"p.png": page()})
    atlas.add_region("N", img(10, 10))
    atlas.add_region("M", img(6, 6))
    atlas.remove_region("N")
    built = atlas.build()
    assert patched(built) and "N" not in {r.name for r in built.regions}


def test_renames_only_keep_the_single_page_image():
    atlas = SpineAtlas.load(TEXT, {"p.png": page()})
    atlas.rename_region("A", "A2")
    built = atlas.build()
    assert built.pages["p.png"].tobytes() == atlas.pages["p.png"].tobytes()
    assert not patched(atlas.build(packer_options=PackerOptions(repack="always")))


def test_three_pages_patch_repack_and_keep():
    text = (TEXT + "\nq.png\nsize: 64,32\nC\n bounds: 0,0,16,16\nD\n bounds: 16,0,16,16\n"
            "\nr.png\nsize: 64,32\nE\n bounds: 0,0,16,16\n")
    pages = {"p.png": page(), "q.png": page(seed=2), "r.png": page(rects=((0, 0, 16, 16),), seed=3)}
    atlas = SpineAtlas.load(text, pages)
    atlas.add_region("N", img(10, 10), page="p.png")
    atlas.replace({"C": img(16, 16, 9)})
    built = atlas.build()
    assert patched(built)
    assert bounds(built, "D")[:2] != (16, 0)
    assert built.pages["r.png"].tobytes() == pages["r.png"].tobytes()


def test_image_size_unlike_declared_size_repacks():
    atlas = SpineAtlas.load(TEXT, {"p.png": page(128, 64, ((0, 0, 32, 32), (32, 0, 32, 32)))})
    atlas.add_region("N", img(10, 10))
    built = atlas.build()
    assert built.pages["p.png"].size != (128, 64)


def test_renames_only_with_unlike_size_repacks():
    atlas = SpineAtlas.load(TEXT, {"p.png": page(128, 64, ((0, 0, 32, 32), (32, 0, 32, 32)))})
    atlas.rename_region("A", "A2")
    assert atlas.build().pages["p.png"].size != (128, 64)


def test_undeclared_size_repacks():
    atlas = SpineAtlas.load(TEXT.replace("size: 64,32\n", ""), {"p.png": page()})
    atlas.add_region("N", img(10, 10))
    assert bounds(atlas.build(), "B")[:2] != (16, 0)


LEGACY = (
    "\np.png\nsize: 64,32\nformat: RGBA8888\nfilter: Linear,Linear\nrepeat: none\n"
    "A\n  rotate: false\n  xy: 0, 0\n  size: 16, 16\n  orig: 18, 18\n  offset: 1, 1\n  index: -1\n"
    "B\n  rotate: false\n  xy: 16, 0\n  size: 16, 16\n  orig: 16, 16\n  offset: 0, 0\n  index: -1\n"
)


def test_pristine_lines_equal_the_canonical_form():
    canonical = AtlasDocument.parse(auto_convert_atlas(LEGACY)).serialize()
    assert canonical != LEGACY
    atlas = SpineAtlas.load(LEGACY, {"p.png": page()})
    atlas.add_region("N", img(10, 10))
    built = atlas.build()
    assert built.atlas_text.startswith(canonical) and "\nN\n" in built.atlas_text


@pytest.mark.parametrize("padding, x", [(1, 36), (8, 40)])
@pytest.mark.parametrize("packer", ("shelf", "rectangles"))
def test_padding_reaches_the_patch(padding, x, packer):
    atlas = SpineAtlas.load(TEXT, {"p.png": page()})
    atlas.add_region("N", img(10, 10))
    assert bounds(atlas.build(packer=packer, packer_options=PackerOptions(padding=padding)), "N")[0] == x


def test_many_adds_keep_padding_between_everything():
    atlas = SpineAtlas.load(TEXT, {"p.png": page()})
    for i in range(20):
        atlas.add_region(f"N{i}", img(5 + i % 3, 4, seed=i))
    built = atlas.build(packer_options=PackerOptions(padding=2))
    assert bounds(built, "B")[:2] == (16, 0)

    def rect(reg):
        x, y, w, h = reg.bounds
        return (x, y, h, w) if reg.rotate in (90, 270) else (x, y, w, h)

    added = [rect(reg) for reg in built.regions if reg.name.startswith("N")]
    pristine = [rect(reg) for reg in built.regions if reg.name in ("A", "B")]
    assert len(added) == 20
    for i, (ax, ay, aw, ah) in enumerate(added):  # pristine A and B touch each other on purpose
        for bx, by, bw, bh in added[i + 1:] + pristine:
            assert ax + aw + 2 <= bx or bx + bw + 2 <= ax or ay + ah + 2 <= by or by + bh + 2 <= ay


def test_shelf_patch_may_rotate_180(monkeypatch):
    mesh = {"T": {"type": "Mesh", "uvs": [0, 0, 1, 0, 0, 1], "triangles": [0, 1, 2], "hull_length": 3}}
    monkeypatch.setattr(api, "_parse_skeleton", lambda data: {"attachments": mesh})
    rgba = pattern(40, 40)
    ys, xs = np.mgrid[0:24, 0:24]
    box = rgba[8:32, 8:32]
    box[(xs + ys) >= 23] = 0       # free: the lower-right triangle of a 24x24 box; stray pixels elsewhere
    text = "p.png\nsize: 40,40\nA\n bounds: 0,0,8,8\n"
    atlas = SpineAtlas.load(text, {"p.png": Image.fromarray(rgba, "RGBA")}, skeleton=b"x")
    atlas.add_region("T", img(12, 12))
    built = atlas.build(packer_options=PackerOptions(padding=1))
    assert bounds(built, "T") == (16, 16, 12, 12, 180)


def test_renamed_pristine_region_keeps_its_hull(monkeypatch):
    mesh = {"M": {"type": "Mesh", "uvs": [0, 0, 1, 0, 0, 1], "triangles": [0, 1, 2], "hull_length": 3}}
    monkeypatch.setattr(api, "_parse_skeleton", lambda data: {"attachments": mesh})
    atlas = SpineAtlas.load("p.png\nsize: 32,32\nM\n bounds: 0,0,32,32\n",
                            {"p.png": Image.fromarray(pattern(32, 32), "RGBA")}, skeleton=b"x")
    atlas.rename_region("M", "Z")
    atlas.add_region("N", img(6, 6))
    assert atlas.build(packer_options=PackerOptions(padding=1)).pages["p.png"].size == (32, 32)


def test_renamed_pristine_region_keeps_its_hull_under_polygons(monkeypatch):
    mesh = {"M": {"type": "Mesh", "uvs": [0, 0, 1, 0, 0, 1], "triangles": [0, 1, 2], "hull_length": 3}}
    monkeypatch.setattr(api, "_parse_skeleton", lambda data: {"attachments": mesh})
    atlas = SpineAtlas.load("p.png\nsize: 32,32\nM\n bounds: 0,0,32,32\n",
                            {"p.png": Image.fromarray(pattern(32, 32), "RGBA")}, skeleton=b"x")
    atlas.rename_region("M", "Z")
    atlas.add_region("N", img(6, 6))
    assert atlas.build(packer="polygons", packer_options=PackerOptions(padding=1)).pages["p.png"].size == (32, 32)


def test_pma_pristine_regions_extract_unchanged():
    text = TEXT.replace("size: 64,32\n", "size: 64,32\npma: true\n")
    atlas = SpineAtlas.load(text, {"p.png": page()})
    atlas.add_region("N", img(10, 10), alpha_mode="premultiplied")
    built = atlas.build()
    for name in ("A", "B"):
        assert built.extract(name).tobytes() == atlas.extract(name).tobytes()


def test_too_large_names_the_page():
    text = TEXT + "\nq.png\nsize: 8,8\nC\n bounds: 0,0,8,8\n"
    atlas = SpineAtlas.load(text, {"p.png": page(), "q.png": Image.fromarray(pattern(8, 8), "RGBA")})
    atlas.add_region("W", Image.new("RGBA", (10001, 1), (1, 1, 1, 255)), page="q.png")
    with pytest.raises(CanvasTooLarge) as info:
        atlas.build()
    assert info.value.page == "q.png"


def test_unedited_build_still_validates_repack():
    with pytest.raises(ValueError):
        SpineAtlas.load(TEXT, {"p.png": page()}).build(packer_options=PackerOptions(repack="never"))


def test_auto_and_always_do_not_share_the_cache():
    atlas = SpineAtlas.load(TEXT, {"p.png": page()})
    atlas.add_region("N", img(10, 10))
    assert atlas.build().atlas_text != atlas.build(packer_options=PackerOptions(repack="always")).atlas_text


def test_patch_on_a_large_page_is_not_slow():
    rng = np.random.default_rng(1)
    rgba = np.zeros((4096, 4096, 4), np.uint8)
    rgba[:2048, :, 3] = 255
    ys, xs = rng.integers(2048, 4096, 3000), rng.integers(0, 4096, 3000)
    rgba[ys, xs, 3] = 255           # stray dots in the free half
    atlas = SpineAtlas.load("p.png\nsize: 4096,4096\nA\n bounds: 0,0,4096,2048\n",
                            {"p.png": Image.fromarray(rgba, "RGBA")})
    for i in range(4):
        atlas.add_region(f"N{i}", img(64, 64, seed=i))
    start = time.monotonic()
    atlas.build()
    assert time.monotonic() - start < 120  # ~12 s for the search alone on the Pi when planned


def test_always_after_auto_packs_again(monkeypatch):
    from atlas_toolkit.engine.session import AtlasSession
    calls = []
    original = AtlasSession.pack_pending
    monkeypatch.setattr(AtlasSession, "pack_pending", lambda self, **kw: calls.append(kw) or original(self, **kw))
    atlas = SpineAtlas.load(TEXT, {"p.png": page()})
    atlas.add_region("N", img(10, 10))
    atlas.build()
    atlas.build(packer_options=PackerOptions(repack="always"))
    atlas.build()
    # auto and always are separate cache entries; the second auto is a hit.
    assert [kw["patch_adds"] for kw in calls] == [True, False]
