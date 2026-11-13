# SPDX-FileCopyrightText: 2026 com55
# SPDX-License-Identifier: Apache-2.0
"""Added regions with mesh data: cut to the hull, hull footprint, kept in allowed (spec 2026-10-31 §4)."""
import numpy as np
import pytest
from PIL import Image

import atlas_toolkit.api as api
import atlas_toolkit.engine.modifier as modifier_module
from atlas_toolkit import PackerOptions, SpineAtlas

UPPER_LEFT = {"type": "Mesh", "uvs": [0, 0, 1, 0, 0, 1], "triangles": [0, 1, 2], "hull_length": 3}
MESH = {"X": UPPER_LEFT}
TEXT = "p.png\nsize: 64,32\nA\n bounds: 0,0,16,16\nB\n bounds: 16,0,16,16\n"
TEXT_WITH_X = TEXT + "X\n bounds: 32,0,12,12\n"


def page(w=64, h=32):
    rgba = np.zeros((h, w, 4), np.uint8)
    rgba[..., 0] = 200
    rgba[..., 3] = 255
    return Image.fromarray(rgba, "RGBA")


def opaque(w=12, h=12, colour=(30, 60, 90, 255)):
    return Image.new("RGBA", (w, h), colour)


def load(monkeypatch, text=TEXT, mesh=MESH, pma=False):
    monkeypatch.setattr(api, "_parse_skeleton", lambda data: {"attachments": mesh})
    if pma:
        text = text.replace("size: 64,32\n", "size: 64,32\npma: true\n")
    return SpineAtlas.load(text, {"p.png": page()}, skeleton=b"x")


def cut(image):
    """Upper-left triangle kept, far corner cleared."""
    return image.getpixel((1, 1))[3] == 255 and image.getpixel((11, 11))[3] == 0


@pytest.mark.parametrize("packer", ["shelf", "rectangles", "polygons"])
def test_added_mesh_region_is_cut_in_a_repack(monkeypatch, packer):
    atlas = load(monkeypatch)
    atlas.add_region("X", opaque())
    built = atlas.build(packer=packer, packer_options=PackerOptions(repack="always"))
    assert cut(built.extract("X"))


def test_mesh_aware_off_keeps_the_added_region_whole(monkeypatch):
    atlas = load(monkeypatch)
    atlas.add_region("X", opaque())
    built = atlas.build(packer_options=PackerOptions(mesh_aware=False, repack="always"))
    assert built.extract("X").getpixel((11, 11))[3] == 255


def test_a_cut_never_touches_the_added_source(monkeypatch):
    atlas = load(monkeypatch)
    atlas.add_region("X", opaque())
    first = atlas.build(packer_options=PackerOptions(repack="always"))
    assert cut(first.extract("X"))
    second = atlas.build(packer_options=PackerOptions(mesh_aware=False, repack="always"))
    assert second.extract("X").getpixel((11, 11))[3] == 255


def test_a_child_atlas_finds_the_mesh_of_a_new_add(monkeypatch):
    atlas = load(monkeypatch, TEXT_WITH_X)
    atlas.rename_region("X", "Old")
    child = atlas.build(packer_options=PackerOptions(repack="always"))
    assert child._allowed is not None and "X" not in child._allowed
    child.add_region("X", opaque())
    grandchild = child.build(packer_options=PackerOptions(repack="always"))
    assert cut(grandchild.extract("X"))
    assert "X" in grandchild._allowed


def test_looked_up_by_label_when_the_key_differs(monkeypatch):
    atlas = load(monkeypatch, TEXT_WITH_X)
    atlas.rename_region("X", "Old")
    atlas.add_region("X", opaque())  # internal key "X_2", label "X"
    built = atlas.build(packer_options=PackerOptions(repack="always"))
    assert cut(built.extract("X"))


def test_looked_up_by_label_after_renaming_the_added_key(monkeypatch):
    atlas = load(monkeypatch)
    atlas.add_region("Y", opaque())
    atlas.rename_region("Y", "X")
    built = atlas.build(packer_options=PackerOptions(repack="always"))
    assert cut(built.extract("X"))


def test_polygons_nests_the_added_region_by_its_hull(monkeypatch):
    seen = []
    real = modifier_module.nest_pack

    def recording(items, gap_distance=4):
        seen.extend(items)
        return real(items, gap_distance=gap_distance)

    monkeypatch.setattr(modifier_module, "nest_pack", recording)
    atlas = load(monkeypatch)
    atlas.add_region("X", opaque())
    atlas.build(packer="polygons", packer_options=PackerOptions(repack="always"))
    added = [item for item in seen if item["name"] == "X"]
    assert added and 0 in set(added[0]["footprint"])


@pytest.mark.parametrize("mesh_aware", [None, False])
def test_added_mesh_label_joins_allowed(monkeypatch, mesh_aware):
    atlas = load(monkeypatch)
    atlas.add_region("X", opaque())
    built = atlas.build(packer_options=PackerOptions(mesh_aware=mesh_aware, repack="always"))
    assert "X" in built._allowed
    assert cut(built.extract("X", mesh_crop=True))


def test_label_collision_masks_both_but_only_the_added_one_is_allowed(monkeypatch):
    atlas = load(monkeypatch, TEXT_WITH_X)
    atlas.rename_region("X", "Old")
    atlas.add_region("X", opaque())
    built = atlas.build(packer_options=PackerOptions(repack="always"))
    assert cut(built.extract("Old")) and cut(built.extract("X"))
    assert "X" in built._allowed and "Old" not in built._allowed


def test_added_mesh_region_is_cut_on_a_pma_page(monkeypatch):
    atlas = load(monkeypatch, pma=True)
    atlas.add_region("X", opaque(colour=(100, 80, 60, 255)), alpha_mode="premultiplied")
    built = atlas.build(packer_options=PackerOptions(repack="always"))
    assert built.extract("X").getpixel((11, 11)) == (0, 0, 0, 0)
