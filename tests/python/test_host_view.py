import json

import pytest

from atlas_toolkit import api
from atlas_toolkit.api import AtlasError, MixedAlphaGroup, SpineAtlas
from atlas_toolkit.host import AppHost, wire
from .host_fixtures import all_pma, decode, mixed_pma, one_page, pixel, two_pages


def loaded(factory=one_page):
    text, pages = factory()
    host = AppHost()
    state = host.load("sample.atlas", text, pages, None, None)
    return host, state


def test_preview_returns_raw_rgba_of_the_composite():
    host, _ = loaded()
    result = host.preview(["a"], False, False, False, "straight")
    assert (result["width"], result["height"]) == (3, 2)
    assert len(result["rgba"]) == 3 * 2 * 4
    assert result["rgba"] == bytes(v for y in range(2) for x in range(3) for v in pixel(x + 1, y + 2))
    assert result["hulls"] is None


def test_preview_converts_pma_to_straight_only_when_every_page_is_pma():
    host, state = loaded(all_pma)
    assert state["allPma"] is True
    raw = host.preview(["a"], False, False, False, "premultiplied")["rgba"]
    straight = host.preview(["a"], False, False, False, "straight")["rgba"]
    assert tuple(raw[:4]) == pixel(1, 2) == (14, 28, 33, 128)
    assert tuple(straight[:4]) == (28, 56, 66, 128)
    assert raw != straight
    assert decode(host.extract(["a"], False, "straight")[0]["png"]).getpixel((0, 0)) == (28, 56, 66, 128)
    assert decode(host.save_image(["a"], False, False, "straight")).getpixel((0, 0)) == (28, 56, 66, 128)


def test_a_mixed_atlas_requests_no_conversion():
    host, state = loaded(mixed_pma)
    assert state["allPma"] is False
    text, pages = mixed_pma()
    atlas = SpineAtlas.load(text, {name: decode(data) for name, data in pages.items()})
    with pytest.raises(MixedAlphaGroup):
        atlas.extract_composite(["a", "c"], alpha_mode="straight")
    with pytest.warns(UserWarning, match="spans pma"):
        result = host.preview(["a", "c"], False, False, False, "straight")
    with pytest.warns(UserWarning, match="spans pma"):
        expected = atlas.extract_composite(["a", "c"])
    assert result["rgba"] == expected.tobytes()
    assert decode(host.extract(["a"], False, "straight")[0]["png"]).getpixel((0, 0)) == pixel(1, 2)


def test_mesh_crop_is_clamped_off_without_mesh():
    host, _ = loaded()
    assert host.preview(["a"], True, False, True, "straight")["hulls"] == []
    assert host.preview(["a"], True, False, False, "straight")["rgba"] == host.preview(["a"], False, False, False, "straight")["rgba"]
    assert decode(host.extract(["a"], True, "straight")[0]["png"]).getpixel((0, 0)) == pixel(1, 2)
    assert decode(host.save_image(["a"], True, False, "straight")).getpixel((0, 0)) == pixel(1, 2)


def test_load_refuses_repeated_names_with_DuplicateRegionName():
    text, pages = one_page()
    text += "a\n bounds: 0,0,1,1\n"
    result, buffers = wire.call(AppHost(), "load", json.dumps(["sample.atlas", text, {"p.png": {"$buf": 0}}, None, None]), [pages["p.png"]])
    assert json.loads(result)["error"]["type"] == "DuplicateRegionName"
    assert buffers == []


def test_load_reports_state():
    _, state = loaded(two_pages)
    regions = [{"key": n, "label": n} for n in ["a", "b", "c"]]
    assert state == {
        "atlasName": "sample.atlas", "pages": ["p.png", "q.png"], "isMultiPage": True,
        "regions": regions, "pristineRegions": regions,
        "regionPages": {"a": "p.png", "b": "p.png", "c": "q.png"},
        "modifiedKeys": [], "hasChanges": False, "hasPending": False, "allPma": False,
        "mesh": {"available": False, "reason": None, "skelName": None},
        "settings": {
            "packer": "shelf", "padding": 2, "meshAware": False,
            "meshAwareLocked": True, "splitComposite": False,
            "splitCompositeLocked": False, "keepLayout": True,
            "keepLayoutLocked": False, "polygonsAllowed": False,
            "alpha": "straight", "alphaVisible": False, "fallbackFrom": None,
        },
    }


def test_extract_returns_png_named_by_the_key_rule():
    host, _ = loaded(lambda: one_page(first="folder/head"))
    result = host.extract(["b", "folder/head", "b"], False, "straight")
    assert [r["filename"] for r in result] == ["b.png", "folder_head.png", "b.png"]
    assert decode(result[0]["png"]).size == (2, 3)
    assert decode(result[0]["png"]).getpixel((1, 2)) == pixel(6, 3)
    assert decode(result[1]["png"]).getpixel((2, 1)) == pixel(3, 3)
    assert [r["filename"] for r in host.extract(None, False, "straight")] == ["folder_head.png", "b.png"]


def test_required_pages_unique_order_and_empty():
    text, _ = two_pages()
    assert AppHost.required_pages(text + "\np.png\nsize: 9,7\n") == ["p.png", "q.png"]
    assert AppHost.required_pages("") == []


@pytest.mark.parametrize("method,args", [
    ("preview", [["a"], False, False, False, "straight"]),
    ("extract", [None, False, "straight"]),
    ("save_image", [["a"], False, False, "straight"]),
    ("set_skeleton", ["mesh.skel", b"synthetic"]),
])
def test_view_requires_load(method, args):
    with pytest.raises(AtlasError, match="^no atlas loaded$"):
        getattr(AppHost(), method)(*args)


@pytest.mark.parametrize("factory", [one_page, all_pma, mixed_pma])
def test_invalid_alpha_is_rejected_on_every_output(factory):
    host, _ = loaded(factory)
    for method, args in [(host.preview, [["a"], False, False, False]), (host.extract, [["a"], False]), (host.save_image, [["a"], False, False])]:
        with pytest.raises(ValueError):
            method(*args, "invalid")


def test_empty_preview_is_rejected():
    host, _ = loaded()
    with pytest.raises(ValueError):
        host.preview([], False, False, False, "straight")


def test_selection_order_force_resize_and_png_match_engine():
    host, _ = loaded()
    text, pages = one_page()
    atlas = SpineAtlas.load(text, {n: decode(b) for n, b in pages.items()})
    for resize in [False, True]:
        expected = atlas.extract_composite(["b", "a"], force_resize=resize)
        preview = host.preview(["b", "a"], False, resize, True, "straight")
        assert (preview["width"], preview["height"]) == expected.size
        assert preview["rgba"] == expected.tobytes()
        saved = decode(host.save_image(["b", "a"], False, resize, "straight"))
        assert saved.size == expected.size and saved.tobytes() == expected.tobytes()


def test_skeleton_state_hulls_and_load_replacement(monkeypatch):
    mesh = {"type": "Mesh", "uvs": [0, 0, 1, 0, 1, 1, 0, 1], "triangles": [0, 1, 2, 0, 2, 3], "hull_length": 4}
    monkeypatch.setattr(api, "_parse_skeleton", lambda data: {"attachments": {"a": mesh}})
    host = AppHost()
    text, pages = one_page()
    state = host.load("first.atlas", text, pages, "first.skel", b"synthetic")
    assert state["mesh"] == {"available": True, "reason": None, "skelName": "first.skel"}
    pristine = state["pristineRegions"]
    state = host.set_skeleton("second.skel", b"synthetic")
    assert state["mesh"]["skelName"] == "second.skel"
    assert state["pristineRegions"] == pristine
    result, buffers = wire.call(host, "preview", '[["a"],true,false,true,"straight"]', [])
    value = json.loads(result)["value"]
    assert value["hulls"] == [{"name": "a", "points": [[0.0, 0.0], [3.0, 0.0], [3.0, 2.0], [0.0, 2.0]]}]
    assert len(buffers) == 1
    text, pages = one_page(first="new")
    state = host.load("second.atlas", text, pages, None, None)
    assert state["atlasName"] == "second.atlas"
    assert state["pristineRegions"] == [{"key": n, "label": n} for n in ["new", "b"]]
    assert state["mesh"] == {"available": False, "reason": None, "skelName": None}


def test_preview_does_not_copy_the_pages(monkeypatch):
    host, _ = loaded(all_pma)

    def refuse_page_export(self):
        raise AssertionError("View operations must not export page copies")

    monkeypatch.setattr(SpineAtlas, "pages", property(refuse_page_export))
    result = host.preview(["a"], False, False, False, "straight")
    assert tuple(result["rgba"][:4]) == (28, 56, 66, 128)
    extracted = host.extract(["a"], False, "straight")
    assert decode(extracted[0]["png"]).getpixel((0, 0)) == (28, 56, 66, 128)
    assert decode(host.save_image(["a"], False, False, "straight")).getpixel((0, 0)) == (28, 56, 66, 128)
    state = host.set_skeleton("sample.skel", b"synthetic")
    assert state["pages"] == ["p.png", "q.png"]
    assert state["allPma"] is True
    assert state["mesh"]["skelName"] == "sample.skel"


def zero_area_atlas():
    text, pages = one_page()
    text = text[:text.index("b\n bounds:")]
    return text + "z\n bounds: 0,0,0,0\n", pages


def test_extract_skips_a_zero_area_region():
    host, _ = loaded(zero_area_atlas)
    result = host.extract(None, False, "straight")
    assert [item["filename"] for item in result] == ["a.png"]
    image = decode(result[0]["png"])
    assert image.size == (3, 2)
    assert image.tobytes() == bytes(v for y in range(2) for x in range(3) for v in pixel(x + 1, y + 2))


def test_extract_of_only_zero_area_regions_is_empty():
    host, _ = loaded(zero_area_atlas)
    assert host.extract(["z"], False, "straight") == []


def test_preview_of_a_zero_area_region_is_none():
    host, _ = loaded(zero_area_atlas)
    assert host.preview(["z"], False, False, True, "straight") is None


def test_save_image_of_a_zero_area_region_is_none():
    host, _ = loaded(zero_area_atlas)
    assert host.save_image(["z"], False, False, "straight") is None


def test_extract_addresses_regions_by_key_after_a_rename():
    host, _ = loaded()
    host._atlas.rename_region("a", "new")
    result = host.extract(["a"], False, "straight")
    image = decode(result[0]["png"])
    assert image.size == (3, 2)
    assert image.tobytes() == bytes(v for y in range(2) for x in range(3) for v in pixel(x + 1, y + 2))


def test_extract_names_files_by_key_after_a_rename():
    host, _ = loaded()
    host._atlas.rename_region("a", "new")
    assert host.extract(["a"], False, "straight")[0]["filename"] == "a.png"


def test_a_failed_load_keeps_the_previous_atlas():
    host, state = loaded(all_pma)
    before = host.preview(["a"], False, False, False, "straight")
    text, _ = one_page(page="other.png", first="other")
    with pytest.raises(Exception):
        host.load("other.atlas", text, {"other.png": b"corrupt"}, "other.skel", b"corrupt")
    assert host.preview(["a"], False, False, False, "straight") == before
    assert host._state() == state


def test_forced_resizing_reaches_the_hulls(monkeypatch):
    mesh = {"type": "Mesh", "uvs": [0.25, 0, 0.75, 0, 0.75, 1, 0.25, 1], "triangles": [0, 1, 2, 0, 2, 3], "hull_length": 4}
    monkeypatch.setattr(api, "_parse_skeleton", lambda data: {"attachments": {"a": mesh, "b": mesh}})
    host, _ = loaded()
    host.set_skeleton("sample.skel", b"synthetic")
    natural = host.preview(["a", "b"], False, False, True, "straight")["hulls"]
    resized = host.preview(["a", "b"], False, True, True, "straight")["hulls"]
    assert natural == [
        {"name": "a", "points": [(0.75, 0.0), (2.25, 0.0), (2.25, 2.0), (0.75, 2.0)]},
        {"name": "b", "points": [(0.5, 0.0), (1.5, 0.0), (1.5, 3.0), (0.5, 3.0)]},
    ]
    assert resized == [
        {"name": name, "points": [(0.75, 0.0), (2.25, 0.0), (2.25, 2.0), (0.75, 2.0)]}
        for name in ["a", "b"]
    ]
    assert natural != resized


def test_pages_come_from_the_atlas_text():
    text, pages = two_pages()
    host = AppHost()
    state = host.load("sample.atlas", text, {
        "extra.png": pages["p.png"], "q.png": pages["q.png"], "p.png": pages["p.png"],
    }, None, None)
    assert state["pages"] == ["p.png", "q.png"]


@pytest.mark.parametrize('method,args', [
    ('preview', [False, False, False, 'straight']),
    ('save_image', [False, False, 'straight']),
])
def test_view_keys_after_rename(method, args):
    host, _ = loaded()
    before = getattr(host, method)(['a'], *args)
    host._atlas.rename_region('a', 'renamed')
    assert getattr(host, method)(['a'], *args) == before
    with pytest.raises(api.RegionNotFound):
        getattr(host, method)(['missing'], *args)
