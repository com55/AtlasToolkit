"""Public reads used by the Python app host; synthetic atlas and mesh data."""
from dataclasses import FrozenInstanceError

import pytest
from PIL import Image

import atlas_toolkit
import atlas_toolkit.api as api
from atlas_toolkit.api import SpineAtlas

TEXT_DECLARED_256 = "p.png\nsize: 256,256\na\n bounds: 10,10,20,20\n"
TEXT = (
    "p.png\nsize: 64,32\na\n bounds: 3,5,12,8\n"
    "b\n bounds: 20,2,6,10\n"
)
IMG = Image.new("RGBA", (12, 8), (100, 80, 60, 255))
MESH = {
    "type": "Mesh", "uvs": [0.25, 0, 0.75, 0, 0.75, 1, 0.25, 1],
    "triangles": [0, 1, 2, 0, 2, 3], "hull_length": 4,
}


def two_region_atlas():
    return SpineAtlas.load(TEXT, Image.new("RGBA", (64, 32)))


def test_region_views_pristine_bounds_are_scaled_to_the_real_page():
    atlas = SpineAtlas.load(TEXT_DECLARED_256, Image.new("RGBA", (512, 512)))
    view = atlas.region_views()[0]
    assert (view.x, view.y, view.w, view.h) == (20, 20, 40, 40)
    # Unequal page sides and unequal axis scales expose swapped coordinates.
    atlas = SpineAtlas.load(TEXT, Image.new("RGBA", (128, 96)))
    view = atlas.region_views()[0]
    assert (view.x, view.y, view.w, view.h) == (6, 15, 24, 24)


def test_region_views_keep_the_key_after_a_rename_and_pack():
    atlas = two_region_atlas()
    atlas.rename_region("a", "z")
    text = atlas.build().atlas_text
    views = {v.key: v for v in atlas.region_views(packed_text=text)}
    assert views["a"].label == "z" and views["a"].page == "p.png"
    assert list(views) == ["a", "b"]
    assert (views["a"].x, views["a"].y, views["a"].w, views["a"].h) == (3, 5, 12, 8)


def test_region_views_mark_modified_and_added():
    atlas = two_region_atlas()
    atlas.replace({"a": IMG})
    atlas.add_region("n", IMG, page="p.png")
    views = atlas.region_views()
    assert isinstance(views, tuple)
    assert [v.label for v in views] == ["a", "b", "n"]
    a, b, n = views
    assert a.modified and not a.added
    assert not b.modified and not b.added
    assert n.added and n.page == "p.png"
    assert (n.x, n.y, n.w, n.h, n.rotate) == (0, 0, 0, 0, 0)
    packed = {v.label: v for v in atlas.region_views(atlas.build().atlas_text)}
    assert packed["a"].modified and packed["n"].added
    assert (packed["n"].w, packed["n"].h) == IMG.size
    atlas.remove_region("b")
    assert [v.label for v in atlas.region_views()] == ["a", "n"]


def test_region_views_packed_literal_names_rotation_and_missing_region():
    atlas = two_region_atlas()
    atlas.rename_region("a", "z_2")
    views = atlas.region_views("out.png\nsize: 80,40\nz_2\n bounds: 7,9,14,6\n rotate: 90\n")
    assert (views[0].key, views[0].label, views[0].page) == ("a", "z_2", "out.png")
    assert (views[0].x, views[0].y, views[0].w, views[0].h, views[0].rotate) == (7, 9, 14, 6, 90)
    assert (views[1].x, views[1].y, views[1].w, views[1].h, views[1].rotate) == (0, 0, 0, 0, 0)


def test_region_view_is_exported_and_frozen():
    view = two_region_atlas().region_views()[0]
    assert isinstance(view, api.RegionView)
    assert atlas_toolkit.RegionView is api.RegionView
    assert "RegionView" in api.__all__ and "RegionView" in atlas_toolkit.__all__
    with pytest.raises(FrozenInstanceError):
        view.x = 99


def test_set_skeleton_changes_mesh_state_and_keeps_edits():
    atlas = two_region_atlas()
    atlas.replace({"a": IMG})
    atlas.set_skeleton(b"not a skeleton")
    assert atlas.mesh_unavailable_reason == "unsupported-version" and atlas.has_changes
    assert atlas.region_views()[0].modified


def test_hull_layout_points_are_in_composite_pixels(monkeypatch):
    monkeypatch.setattr(api, "_parse_skeleton", lambda data: {"attachments": {"a": MESH, "b": MESH}})
    atlas = two_region_atlas()
    assert atlas.mesh_unavailable_reason == "none"
    assert atlas.mesh_hull_layout(["a", "b"]) == []
    atlas.set_skeleton(b"mesh")
    assert atlas.mesh_unavailable_reason is None
    atlas.rename_region("a", "z")
    assert atlas.mesh_hull_layout(["b", "z"]) == [
        {"name": "b", "points": [(1.5, 0.0), (4.5, 0.0), (4.5, 10.0), (1.5, 10.0)]},
        {"name": "z", "points": [(3.0, 0.0), (9.0, 0.0), (9.0, 8.0), (3.0, 8.0)]},
    ]
    assert atlas.mesh_hull_layout(["b", "z"], force_resize=True) == [
        {"name": "b", "points": [(3.0, 0.0), (9.0, 0.0), (9.0, 8.0), (3.0, 8.0)]},
        {"name": "z", "points": [(3.0, 0.0), (9.0, 0.0), (9.0, 8.0), (3.0, 8.0)]},
    ]
    atlas.set_skeleton(None)
    assert atlas.mesh_unavailable_reason == "none"
    assert atlas.mesh_hull_layout(["z", "b"]) == []


def test_hull_layout_omits_regions_without_a_polyline(monkeypatch):
    monkeypatch.setattr(api, "_parse_skeleton", lambda data: {"attachments": {"b": MESH}})
    atlas = two_region_atlas()
    atlas.set_skeleton(b"mesh")
    assert atlas.mesh_hull_layout(["a", "b"]) == [
        {"name": "b", "points": [(1.5, 0.0), (4.5, 0.0), (4.5, 10.0), (1.5, 10.0)]},
    ]


def test_set_skeleton_clears_build_cache_and_preserves_allowed_filter(monkeypatch):
    monkeypatch.setattr(api, "_parse_skeleton", lambda data: {"attachments": {"a": MESH, "b": MESH}})
    atlas = two_region_atlas()
    atlas.replace({"a": IMG})
    atlas.build()
    assert atlas._build_cache is not None
    atlas.set_skeleton(b"mesh")
    assert atlas._build_cache is None and atlas.has_changes
    atlas._allowed = frozenset({"b"})
    atlas.set_skeleton(b"mesh")
    assert [item["name"] for item in atlas.mesh_hull_layout(["a", "b"])] == ["b"]
    atlas.set_skeleton(None)
    assert atlas.mesh_hull_layout(["a", "b"]) == []


def test_page_is_pma():
    text = TEXT + "\nq.png\nsize: 10,6\npma: true\nc\n bounds: 0,0,4,2\n"
    atlas = SpineAtlas.load(text, {"p.png": Image.new("RGBA", (64, 32)), "q.png": Image.new("RGBA", (10, 6))})
    assert atlas.page_is_pma("p.png") is False
    assert atlas.page_is_pma("q.png") is True
    with pytest.raises(ValueError):
        atlas.page_is_pma("missing.png")


def test_packed_views_scale_a_page_kept_at_its_real_size():
    text = TEXT + "\nq.png\nsize: 100,50\nc\n bounds: 10,10,20,10\n"
    atlas = SpineAtlas.load(text, {
        "p.png": Image.new("RGBA", (64, 32)),
        "q.png": Image.new("RGBA", (200, 150)),
    })
    atlas.replace({"a": IMG})
    built = atlas.build()
    sizes = {name: image.size for name, image in built.pages.items()}
    views = {v.label: v for v in atlas.region_views(built.atlas_text, sizes)}
    c = views["c"]
    assert (c.x, c.y, c.w, c.h) == (20, 30, 40, 30)
    unscaled = {v.label: v for v in atlas.region_views(built.atlas_text)}["c"]
    assert (unscaled.x, unscaled.y, unscaled.w, unscaled.h) == (10, 10, 20, 10)


def test_packed_views_leave_a_page_matching_its_declared_size_alone():
    atlas = two_region_atlas()
    packed = "p.png\nsize: 80,40\na\n bounds: 7,9,14,6\n rotate: 90\nb\n bounds: 30,4,8,12\n"
    views = atlas.region_views(packed, {"p.png": (80, 40)})
    assert [(v.x, v.y, v.w, v.h) for v in views] == [(7, 9, 14, 6), (30, 4, 8, 12)]
    assert views[0].rotate == 90 and views[0].page == "p.png"


def test_late_skeleton_is_used_by_a_later_build(monkeypatch):
    monkeypatch.setattr(api, "_parse_skeleton", lambda data: {"attachments": {"n": MESH}})
    atlas = two_region_atlas()
    atlas.set_skeleton(b"mesh")
    patterned = Image.frombytes("RGBA", (12, 8), bytes(
        value for y in range(8) for x in range(12)
        for value in (x * 17, y * 29, 91, 255)
    ))
    atlas.add_region("n", patterned)
    built = atlas.build()
    result = built.extract("n")
    assert result.getpixel((0, 4))[3] == 0
    assert result.getpixel((5, 4)) == patterned.getpixel((5, 4))


def test_hull_layout_refuses_a_pending_add():
    atlas = two_region_atlas()
    atlas.add_region("n", IMG)
    with pytest.raises(api.AtlasError):
        atlas.mesh_hull_layout(["n"])
