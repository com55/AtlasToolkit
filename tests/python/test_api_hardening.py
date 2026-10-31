"""Load and export hardening (backlog sweep 2026-10-31): conflicting pma on
one page file, oversized crops, the skeleton parsed once per lineage, and
the package's lazy exports."""
import pickle

import numpy as np
import pytest
from PIL import Image

import atlas_toolkit
import atlas_toolkit.api as api
from atlas_toolkit.api import ConflictingPagePma, SpineAtlas
from atlas_toolkit.raster.pillow_backend import PillowRasterBackend
from tests.python.pma_cases import mesh

CONFLICT = "p.png\nsize: 8,8\npma: true\nA\n bounds: 0,0,4,4\n\np.png\nsize: 8,8\nB\n bounds: 4,4,4,4\n"
SAME = "p.png\nsize: 8,8\npma: true\nA\n bounds: 0,0,4,4\n\np.png\nsize: 8,8\npma: true\nB\n bounds: 4,4,4,4\n"


def page(w=8, h=8):
    return Image.fromarray(np.arange(w * h * 4, dtype=np.uint8).reshape(h, w, 4))


def test_conflicting_pma_on_one_page_file_is_refused_on_load():
    with pytest.raises(ConflictingPagePma, match="'p.png'") as caught:
        SpineAtlas.load(CONFLICT, {"p.png": page()})
    assert caught.value.page == "p.png"
    assert isinstance(caught.value, ValueError)


def test_repeated_page_file_with_the_same_pma_still_loads():
    atlas = SpineAtlas.load(SAME, {"p.png": page()})
    assert atlas.extract("B").size == (4, 4)


def test_conflicting_page_pma_pickles_with_its_page():
    error = pickle.loads(pickle.dumps(ConflictingPagePma("p.png")))
    assert error.page == "p.png" and str(error) == str(ConflictingPagePma("p.png"))


@pytest.mark.parametrize("bounds, rotate", [("0,0,9,4", ""), ("0,0,4,9", ""), ("0,0,9,4", " rotate: 90\n")])
def test_region_larger_than_its_page_image_is_refused(bounds, rotate):
    # rotate 90 stores the region 4 wide and 9 tall, past the 8 px page.
    text = f"p.png\nsize: 8,8\nA\n bounds: {bounds}\n{rotate}"
    with pytest.raises(ValueError, match="larger than its 8x8 source image"):
        SpineAtlas.load(text, {"p.png": page()}).extract("A")


def test_crop_as_large_as_the_source_is_allowed():
    backend = PillowRasterBackend()
    handle = backend.adopt(page(6, 3))
    assert backend.size(backend.crop_rotate(handle, 0, 0, 6, 3, 0)) == (6, 3)
    # rotate 90: w x h is the region unrotated, stored h wide and w tall.
    assert backend.size(backend.crop_rotate(handle, 0, 0, 3, 6, 90)) == (3, 6)
    with pytest.raises(ValueError):
        backend.crop_rotate(handle, 0, 0, 7, 3, 0)
    with pytest.raises(ValueError):
        backend.crop_rotate(handle, 0, 0, 3, 7, 90)


def test_built_atlases_reuse_the_parsed_skeleton(monkeypatch):
    calls = []

    def parse(data):
        calls.append(data)
        return {"attachments": {"A": mesh(0, 0, 1, 1)}}

    monkeypatch.setattr(api, "_parse_skeleton", parse)
    text = "p.png\nsize: 8,8\nA\n bounds: 0,0,4,4\nB\n bounds: 4,4,4,4\n"
    atlas = SpineAtlas.load(text, {"p.png": page()}, skeleton=b"x")
    unchanged = atlas.build()
    atlas.replace({"B": page(4, 4)})
    built = atlas.build(packer="polygons")
    assert len(calls) == 1
    for child in (unchanged, built):
        assert child.extract("A", mesh_crop=True).size == (4, 4)
        assert child.build(packer="polygons") is not None


def test_package_lists_its_lazy_api_names():
    assert "ConflictingPagePma" in api.__all__
    assert atlas_toolkit.__all__ == sorted(atlas_toolkit._API_NAMES)
    assert set(atlas_toolkit.__all__) <= set(api.__all__) | {"Packer", "PackerOptions", "AlphaMode"}
    listed = dir(atlas_toolkit)
    assert "SpineAtlas" in listed and "ConflictingPagePma" in listed and "__getattr__" in listed
    assert atlas_toolkit.ConflictingPagePma is ConflictingPagePma
