# SPDX-FileCopyrightText: 2026 com55
# SPDX-License-Identifier: Apache-2.0
"""SpineAtlas surfaces the packers' canvas limit as CanvasTooLarge and
leaves the atlas exactly as it was."""
import pytest
from PIL import Image

import atlas_toolkit
from atlas_toolkit.api import AtlasError, CanvasTooLarge, SpineAtlas
from atlas_toolkit.domain import packing
from atlas_toolkit.packer import PackOptions

ATLAS = (
    "page.png\nsize: 16,8\n"
    "Head\n bounds: 0,0,6,5\n"
    "Body\n bounds: 6,0,8,6\n"
    "Arm\n bounds: 0,5,5,3\n"
)


def page():
    return Image.frombytes(
        "RGBA", (16, 8), bytes(v for y in range(8) for x in range(16) for v in (x * 15, y * 30, 100, 255)),
    )


REPACK = {"try_keep_layout": False}
RECT = PackOptions(packer="rectangles", padding=4, **REPACK)


# try_keep_layout=False: a replacement that fits its own slot needs no repack,
# and the packers' canvas limit is only reached by one.
@pytest.mark.parametrize("options", [
    {"options": PackOptions(try_keep_layout=False)},
    {"options": PackOptions(packer="rectangles", padding=3, **REPACK)},
])
def test_too_large_is_a_public_error_and_changes_nothing(monkeypatch, options):
    atlas = SpineAtlas.load(ATLAS, page())
    atlas.replace({"Head": Image.new("RGBA", (6, 5), (9, 8, 7, 255))})
    good = atlas.build(options=RECT)
    cache = atlas._build_cache
    names = [r.name for r in atlas.regions]
    monkeypatch.setattr(packing, "MAX_CANVAS", 8)  # 93 px² of sprites cannot fit in 8x8
    with pytest.raises(CanvasTooLarge) as raised:
        atlas.build(**options)
    assert isinstance(raised.value, AtlasError) and isinstance(raised.value, ValueError)
    assert raised.value.limit == 8
    assert "8 px per side" in str(raised.value)
    assert atlas._build_cache is cache
    assert atlas.has_changes and [r.name for r in atlas.regions] == names
    snap = atlas._session.snapshot()
    assert (snap.nest_regions_enabled, snap.nest_gap_distance, snap.mesh_aware_repack_enabled) == (False, 4, False)
    monkeypatch.setattr(packing, "MAX_CANVAS", 10000)
    assert atlas.build(**options).atlas_text
    assert atlas.build(options=RECT).atlas_text == good.atlas_text


def test_save_raises_before_writing(tmp_path, monkeypatch):
    atlas = SpineAtlas.load(ATLAS, page())
    atlas.replace({"Head": Image.new("RGBA", (6, 5), (9, 8, 7, 255))})
    monkeypatch.setattr(packing, "MAX_CANVAS", 8)
    with pytest.raises(CanvasTooLarge):
        atlas.save(tmp_path / "out" / "a.atlas", options=PackOptions(try_keep_layout=False))
    assert not (tmp_path / "out").exists()


def test_canvas_too_large_is_exported():
    import atlas_toolkit.api as api
    assert atlas_toolkit.CanvasTooLarge is CanvasTooLarge
    assert "CanvasTooLarge" in api.__all__


def test_too_large_names_the_page_of_a_multi_page_atlas(monkeypatch):
    text = ATLAS + "\nsecond.png\nsize: 8,8\nTail\n bounds: 0,0,8,8\n"
    atlas = SpineAtlas.load(text, {"page.png": page(), "second.png": Image.new("RGBA", (8, 8), (1, 1, 1, 255))})
    atlas.replace({"Tail": Image.new("RGBA", (8, 8), (9, 8, 7, 255))})
    monkeypatch.setattr(packing, "MAX_CANVAS", 7)
    with pytest.raises(CanvasTooLarge) as raised:
        atlas.build(options=PackOptions(try_keep_layout=False))
    assert raised.value.page == "second.png"
    assert "'second.png'" in str(raised.value) and "7 px per side" in str(raised.value)


def test_single_page_too_large_has_no_page(monkeypatch):
    atlas = SpineAtlas.load(ATLAS, page())
    atlas.replace({"Head": Image.new("RGBA", (6, 5), (9, 8, 7, 255))})
    monkeypatch.setattr(packing, "MAX_CANVAS", 8)
    with pytest.raises(CanvasTooLarge) as raised:
        atlas.build(options=PackOptions(try_keep_layout=False))
    assert raised.value.page is None


@pytest.mark.parametrize("page_name", [None, "second.png"])
def test_too_large_survives_pickling(page_name):
    import pickle
    original = CanvasTooLarge(12001, 40, 10000, page_name)
    original.add_note("while building")
    error = pickle.loads(pickle.dumps(original))
    assert type(error) is CanvasTooLarge and error.__notes__ == ["while building"]
    assert (error.width, error.height, error.limit, error.page) == (12001, 40, 10000, page_name)
    assert str(error) == str(CanvasTooLarge(12001, 40, 10000, page_name))
