# SPDX-FileCopyrightText: 2026 com55
# SPDX-License-Identifier: Apache-2.0
"""Add-only pages keep their layout, byte for byte, over the patch-mode corpus.

Spec 2026-11-18 "Engine clean-up": patch mode was `place_in_place` with no
mods and no removes, so the add-only corpus both paths were built for must
give one answer. The digest pins below are that answer: captured from the old
patch path with bleed=False, per the spec's "the old bytes win" rule. The
two-sided patched-vs-in-place comparison is gone with the patch path; the
pins are what guards the one keep-layout path now.

Bleed is off: with auto bleed a composite split bleeds, and the pins predate
that being the default. Bleed is a later pass over a packed page anyway.

Mutation sweep, 2026-11-18 (8 mutations on place_in_place, all red): gap off
by one, paste at origin, no sprite rotation, size ignores growth, base pixels
truncated, no hull cut, dedup off, and "do not clear covered pixels". The
last two live in code every path shares, so only the pins catch those, which
is why the corpus carries identical_adds and add_over_cleared_pixels.
"""
import hashlib
import dataclasses
from dataclasses import dataclass
from typing import Callable

import numpy as np
import pytest
from PIL import Image

import spine_atlas_toolkit.api as api
from spine_atlas_toolkit import PackOptions, SpineAtlas
from tests import test_api_added_mesh as added_mesh
from tests import test_api_patch_mode as patch_mode

# patch_mode's test_shelf_patch_may_rotate_180 geometry, which it builds
# inline: a 24x24 box whose lower-right triangle is free, in a 40x40 page
# with stray pixels elsewhere.
ROTATED_MESH = {"T": {"type": "Mesh", "uvs": [0, 0, 1, 0, 0, 1], "triangles": [0, 1, 2], "hull_length": 3}}
UPPER_LEFT = added_mesh.UPPER_LEFT


def _plain_add(monkeypatch):
    """An add into the free space right of A and B."""
    atlas = SpineAtlas.load(patch_mode.TEXT, {"p.png": patch_mode.page()})
    atlas.add_region("N", patch_mode.img(10, 10))
    return atlas


def _grow_page_down(monkeypatch):
    """An add too wide to fit beside A and B: the page grows downward."""
    atlas = SpineAtlas.load(patch_mode.TEXT, {"p.png": patch_mode.page()})
    atlas.add_region("N", patch_mode.img(40, 40))
    return atlas


def _grow_page_right(monkeypatch):
    """An add taller than the column below A: the page grows to the right,
    and the add is stored rotated 90 degrees."""
    text = "p.png\nsize: 32,64\nA\n bounds: 0,0,32,32\n"
    atlas = SpineAtlas.load(text, {"p.png": patch_mode.page(32, 64, ((0, 0, 32, 32),))})
    atlas.add_region("N", patch_mode.img(24, 48))
    return atlas


def _rotated_placement(monkeypatch):
    """A 30x12 add only fits the 16-wide column right of A rotated 90
    degrees."""
    text = "p.png\nsize: 48,64\nA\n bounds: 0,0,32,64\n"
    atlas = SpineAtlas.load(text, {"p.png": patch_mode.page(48, 64, ((0, 0, 32, 64),))})
    atlas.add_region("N", patch_mode.img(30, 12))
    return atlas


def _mesh_hull_add(monkeypatch):
    """An add whose label has a mesh: cut to its hull and placed by it."""
    atlas = added_mesh.load(monkeypatch)
    atlas.add_region("X", added_mesh.opaque())
    return atlas


def _mesh_hull_rotated(monkeypatch):
    """An add with mesh data landing rotated 180 degrees in the free
    triangle."""
    monkeypatch.setattr(api, "_parse_skeleton", lambda data: {"attachments": ROTATED_MESH})
    rgba = patch_mode.pattern(40, 40)
    ys, xs = np.mgrid[0:24, 0:24]
    box = rgba[8:32, 8:32]
    box[(xs + ys) >= 23] = 0       # free: the lower-right triangle of a 24x24 box
    text = "p.png\nsize: 40,40\nA\n bounds: 0,0,8,8\n"
    atlas = SpineAtlas.load(text, {"p.png": Image.fromarray(rgba, "RGBA")}, skeleton=b"x")
    atlas.add_region("T", patch_mode.img(12, 12))
    return atlas


def _add_and_rename(monkeypatch):
    """An add plus a rename on one page."""
    atlas = SpineAtlas.load(patch_mode.TEXT, {"p.png": patch_mode.page()})
    atlas.add_region("N", patch_mode.img(10, 10))
    atlas.rename_region("A", "A2")
    return atlas


def _two_pages_one_add(monkeypatch):
    """Two pages, the add on the second one only."""
    text = patch_mode.TEXT + "\nq.png\nsize: 64,32\nC\n bounds: 0,0,16,16\nD\n bounds: 16,0,16,16\n"
    atlas = SpineAtlas.load(text, {"p.png": patch_mode.page(), "q.png": patch_mode.page(seed=2)})
    atlas.add_region("N", patch_mode.img(10, 10), page="q.png")
    return atlas


def _many_adds(monkeypatch):
    """Several adds placed by one search."""
    atlas = SpineAtlas.load(patch_mode.TEXT, {"p.png": patch_mode.page()})
    for i in range(4):
        atlas.add_region(f"M{i}", patch_mode.img(3 + i, 5, seed=i))
    return atlas


def _identical_adds(monkeypatch):
    """Two adds of identical pixels share one placement (repack's dedup)."""
    atlas = SpineAtlas.load(patch_mode.TEXT, {"p.png": patch_mode.page()})
    atlas.add_region("N1", patch_mode.img(10, 10, seed=7))
    atlas.add_region("N2", patch_mode.img(10, 10, seed=7))
    return atlas


def _add_over_cleared_pixels(monkeypatch):
    """The clear-under-placement step, made visible: a pristine mesh region
    claims the upper-left triangle of a fully opaque page, so the lower-right
    half is free for adds though it holds pixels; the add is cut to the same
    triangle, leaving transparent pixels inside its placed rect for the
    cleared (or not) page pixels to show through."""
    monkeypatch.setattr(api, "_parse_skeleton", lambda data: {"attachments": {"M": UPPER_LEFT, "T": UPPER_LEFT}})
    text = "p.png\nsize: 40,40\nM\n bounds: 0,0,40,40\n"
    atlas = SpineAtlas.load(text, {"p.png": Image.fromarray(patch_mode.pattern(40, 40), "RGBA")}, skeleton=b"x")
    atlas.add_region("T", patch_mode.img(20, 20, seed=9))
    return atlas


def _add_two_remove_one(monkeypatch):
    """One add survives, the other is removed again before the build."""
    atlas = SpineAtlas.load(patch_mode.TEXT, {"p.png": patch_mode.page()})
    atlas.add_region("N", patch_mode.img(10, 10))
    atlas.add_region("M", patch_mode.img(6, 6))
    atlas.remove_region("N")
    return atlas


def _pma_page_add(monkeypatch):
    """An add on a premultiplied page (the add enters in that space)."""
    text = patch_mode.TEXT.replace("size: 64,32\n", "size: 64,32\npma: true\n")
    atlas = SpineAtlas.load(text, {"p.png": patch_mode.page()})
    atlas.add_region("N", patch_mode.img(10, 10, seed=3), alpha_mode="premultiplied")
    return atlas


def _mesh_atlas_mesh_aware_off(monkeypatch):
    """A .skel is loaded but mesh-aware is off: the add is not cut."""
    atlas = added_mesh.load(monkeypatch)
    atlas.add_region("X", added_mesh.opaque())
    return atlas



@dataclass(frozen=True)
class Case:
    build: Callable
    padding: int = 2
    mesh: bool = False   # also run the polygons family
    options: tuple = ()  # extra PackOptions fields, e.g. (("mesh_aware", False),)


CASES = {
    "plain_add": Case(_plain_add),
    "grow_page_down": Case(_grow_page_down),
    "grow_page_right": Case(_grow_page_right),
    "rotated_placement": Case(_rotated_placement),
    "mesh_hull_add": Case(_mesh_hull_add, mesh=True),
    "mesh_hull_rotated": Case(_mesh_hull_rotated, padding=1, mesh=True),
    "add_and_rename": Case(_add_and_rename),
    "two_pages_one_add": Case(_two_pages_one_add),
    "many_adds": Case(_many_adds),
    "identical_adds": Case(_identical_adds),
    "add_over_cleared_pixels": Case(_add_over_cleared_pixels, mesh=True),
    "add_two_remove_one": Case(_add_two_remove_one),
    "pma_page_add": Case(_pma_page_add),
    # mesh_aware=False forbids polygons, so only the rectangles family applies.
    "mesh_atlas_mesh_aware_off": Case(_mesh_atlas_mesh_aware_off, options=(("mesh_aware", False),)),
}


def _options(case: Case) -> PackOptions:
    return PackOptions(try_keep_layout=True, padding=case.padding, bleed=False, **dict(case.options))


def _packers(case: Case) -> tuple[str, ...]:
    """One entry per packer family the case can run."""
    return ("rectangles", "polygons") if case.mesh else ("rectangles",)


PARAMS = [
    pytest.param(name, packer, id=f"{name}-{packer}")
    for name, case in CASES.items()
    for packer in _packers(case)
]


def digests(built):
    """SHA-256 of the atlas text and of every page's decoded RGBA."""
    return {
        "atlas": hashlib.sha256(built.atlas_text.encode("utf-8")).hexdigest(),
        "pages": {
            name: hashlib.sha256(np.asarray(image.convert("RGBA")).tobytes()).hexdigest()
            for name, image in built.pages.items()
        },
    }


def assert_layout_kept(atlas, built):
    """Every pristine region is where it was, so this case really took the
    keep-the-layout path -- a page that fell back to a repack would place
    them again and silently make the two-sided comparison vacuous."""
    wanted = {r.name: (tuple(r.bounds), r.rotate) for r in atlas.regions if r.original_name is not None}
    assert wanted, "no pristine region in this case -- it cannot show the layout was kept"
    for name, (bounds, rotate) in wanted.items():
        region = built.region(name)
        assert (tuple(region.bounds), region.rotate) == (bounds, rotate), name


# Captured once from the patch path of the code as of 2026-11-18
# (bleed=False, padding as in CASES). Spec: if the keep-layout path ever
# disagrees, these old bytes win -- fix place_in_place, do not re-pin.
PINS = {
    ("plain_add", "rectangles"): {
        "atlas": "74a26c0bc8b0dcdde3afde5a3b1f6841aa89d8d4060b93afea1cb1a41d7b2077",
        "pages": {
            "p.png": "d842811822d03df9e26165a35dff06b6ffbd74ba422cc735f65047526b442c8b",
        },
    },
    ("grow_page_down", "rectangles"): {
        "atlas": "22c7ae0bc05c655879c6513fdf69f3310f916adf8d9607b763c7888fb7701a81",
        "pages": {
            "p.png": "c5fc08590c38baa9d22f424a66efc0b37d2c8626f38e5e43948ebc031df80ed5",
        },
    },
    ("grow_page_right", "rectangles"): {
        "atlas": "d425140d3a70d781c468d5d6367123374c29ada902c042ef62f1b16cc81b3c33",
        "pages": {
            "p.png": "322c0d0be48173f03e2a5c0bae6bd31457335ec4a627b21c33a6eb738b40e136",
        },
    },
    ("rotated_placement", "rectangles"): {
        "atlas": "b0d9f879e667deec5d3350feb794e22c095d1dc50093d0f1e60c63f0aeef6340",
        "pages": {
            "p.png": "13462ddb5cb1395b4ec067f8f274df046f19ad424e177ae056c42fcfd4ef673e",
        },
    },
    ("mesh_hull_add", "rectangles"): {
        "atlas": "67f5526718bd0b106bb4b29c9aea47de839a626b3587466a8ee843e37a77bf70",
        "pages": {
            "p.png": "a3adc1244ee55635d5eb280439abbeb5b7f97ef3291ea311c96f56450fef8f3d",
        },
    },
    ("mesh_hull_add", "polygons"): {
        "atlas": "67f5526718bd0b106bb4b29c9aea47de839a626b3587466a8ee843e37a77bf70",
        "pages": {
            "p.png": "a3adc1244ee55635d5eb280439abbeb5b7f97ef3291ea311c96f56450fef8f3d",
        },
    },
    ("mesh_hull_rotated", "rectangles"): {
        "atlas": "5c586e1f6078eb71eaa89e1232df421a5cefed4b0567021bbffdf22fb19af47a",
        "pages": {
            "p.png": "346f61e3c0e9b30e9fc04100ce7dd13184e10797ef8c52487e58d852c59a397c",
        },
    },
    ("mesh_hull_rotated", "polygons"): {
        "atlas": "5c586e1f6078eb71eaa89e1232df421a5cefed4b0567021bbffdf22fb19af47a",
        "pages": {
            "p.png": "346f61e3c0e9b30e9fc04100ce7dd13184e10797ef8c52487e58d852c59a397c",
        },
    },
    ("add_and_rename", "rectangles"): {
        "atlas": "fa8b4c57af42194a4e30fa973fe93344932902aa3bc3b6c630ce81ff20f259d7",
        "pages": {
            "p.png": "d842811822d03df9e26165a35dff06b6ffbd74ba422cc735f65047526b442c8b",
        },
    },
    ("two_pages_one_add", "rectangles"): {
        "atlas": "21b5e709aa5169ab5a79ca522e15ae904430a29b73ac0d0b822b822c8ad07142",
        "pages": {
            "p.png": "62da4e3ac52c2021274c0245e4df441e2f019de053c91da5a55c069f8ca3774d",
            "q.png": "89ca73e66f82a9da62b8ef45ef930cca99d7a1339fd4a841bb5299f52aa9556e",
        },
    },
    ("many_adds", "rectangles"): {
        "atlas": "c20afe12d1f71d45dda6437dc156ae9a042ad63060a759be7aae1378a3fd0303",
        "pages": {
            "p.png": "f37a0e199f5d5b96e668eb2c0faafff8a04d2f15a10adfa1f68a768196d5f1fd",
        },
    },
    ("identical_adds", "rectangles"): {
        "atlas": "4ea8aeeecc85b7e8b83617c3f7571fa685caf4c4b4c478ceddec7b48cdeacd7a",
        "pages": {
            "p.png": "c1d1af94dcc70ebd58e935377518eceeb47f00dc09093ed8de6578b60f31a721",
        },
    },
    ("add_over_cleared_pixels", "rectangles"): {
        "atlas": "44ddb2c580ce45c409ffc3207981a1b0e2f33f92dc6cdaa4f9cb7ca1f0d57c25",
        "pages": {
            "p.png": "142a55b2a2e52db94f3bd86592de25d67c5e1e5678c6e535c9ecd3fdeaa701eb",
        },
    },
    ("add_over_cleared_pixels", "polygons"): {
        "atlas": "44ddb2c580ce45c409ffc3207981a1b0e2f33f92dc6cdaa4f9cb7ca1f0d57c25",
        "pages": {
            "p.png": "142a55b2a2e52db94f3bd86592de25d67c5e1e5678c6e535c9ecd3fdeaa701eb",
        },
    },
    ("add_two_remove_one", "rectangles"): {
        "atlas": "5c1db46256b5e8ffc15933bf61970886b8656d654a25ca1f373c6a44758c704c",
        "pages": {
            "p.png": "839977ca5e951bc0440fad940173c03391323d56cd43861a9192da1f96cc4f55",
        },
    },
    ("pma_page_add", "rectangles"): {
        "atlas": "afca883f28c2ba94f6b7713c66bd7a42a1b96a2d16476ae6701ca5aae6afddc3",
        "pages": {
            "p.png": "6612a74d35359a0047d5aab1634262ba2d723a898fbedd24c833167a38594ea2",
        },
    },
    ("mesh_atlas_mesh_aware_off", "rectangles"): {
        "atlas": "67f5526718bd0b106bb4b29c9aea47de839a626b3587466a8ee843e37a77bf70",
        "pages": {
            "p.png": "dca9cf1c8d09e06abd0d6568a293bff8f1cde0acdd459afc1e304b4b4f4dd430",
        },
    },
}


@pytest.mark.parametrize("case, packer", PARAMS)
def test_add_only_bytes_pinned(monkeypatch, case, packer):
    spec = CASES[case]
    atlas = spec.build(monkeypatch)
    built = atlas.build(options=dataclasses.replace(_options(spec), packer=packer))
    assert digests(built) == PINS[(case, packer)]
    assert_layout_kept(atlas, built)
