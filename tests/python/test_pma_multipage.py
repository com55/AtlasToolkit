"""Multi-page pma coverage (spec 2026-10-26 §5.8) and the mixed warning (§3.8)."""
import numpy as np
import pytest
from PIL import Image

from atlas_toolkit.api import SpineAtlas
from atlas_toolkit.packer import PackerOptions
from tests.python.pma_cases import GLOW_IN, PACKERS, fake_skeleton, mesh, premultiplied_pattern, with_glow

TWO = (
    "pma.png\nsize: 32,8\npma: true\nG\n bounds: 0,0,16,8\nN\n bounds: 16,0,16,8\n"
    "\nst.png\nsize: 32,8\nS1\n bounds: 0,0,16,8\nS2\n bounds: 16,0,16,8\n"
)
MESHES = {n: mesh(0, 0, 0.5, 1) for n in ("G", "N", "S1", "S2")}


def img(arr):
    return Image.fromarray(arr, "RGBA")


def pages():
    g = with_glow(premultiplied_pattern(32, 8, 1), 2, 3, GLOW_IN)
    return {"pma.png": img(g), "st.png": img(premultiplied_pattern(32, 8, 2))}


@pytest.mark.parametrize("packer, options", PACKERS)
def test_pma_page_keeps_glow_next_to_a_straight_page(monkeypatch, packer, options):
    fake_skeleton(monkeypatch, MESHES)
    atlas = SpineAtlas.load(TWO, pages(), skeleton=b"x")
    # Each mask on its own: the extract mask before any build, then the
    # repack mask through a plain extract (mesh_crop would hide it).
    assert not np.asarray(atlas.extract("G", mesh_crop=True))[:, 8:].any()
    atlas.replace({"N": img(premultiplied_pattern(16, 8, 3)), "S1": img(premultiplied_pattern(16, 8, 4))})
    built = atlas.build(packer=packer, packer_options=options)
    for crop in (False, True):
        g = np.asarray(built.extract("G", mesh_crop=crop))
        assert g[3, 2].tolist() == list(GLOW_IN), crop
        assert not g[:, 8:].any(), crop


def test_cross_page_all_pma_composite_is_exact():
    text = "a.png\nsize: 16,8\npma: true\nP\n bounds: 0,0,16,8\n\nb.png\nsize: 16,8\npma: true\nQ\n bounds: 0,0,16,8\n"
    atlas = SpineAtlas.load(text, {"a.png": Image.new("RGBA", (16, 8)), "b.png": Image.new("RGBA", (16, 8))})
    want = with_glow(premultiplied_pattern(16, 8, 5), 4, 4, GLOW_IN)
    image = img(want)
    for _ in range(6):
        atlas.replace_composite(["P", "Q"], image)
        atlas = atlas.build(split_composite=True, packer_options=PackerOptions(mesh_aware=False))
        image = atlas.extract_composite(["P", "Q"])
        assert np.array_equal(np.asarray(image), want)


def test_mixed_composite_warns_in_extract_and_replace():
    text = "a.png\nsize: 16,8\npma: true\nP\n bounds: 0,0,16,8\n\nb.png\nsize: 16,8\nS\n bounds: 0,0,16,8\n"
    atlas = SpineAtlas.load(text, {"a.png": Image.new("RGBA", (16, 8)), "b.png": Image.new("RGBA", (16, 8))})
    with pytest.warns(UserWarning, match="pma and non-pma"):
        atlas.extract_composite(["P", "S"])
    with pytest.warns(UserWarning, match="pma and non-pma"):
        atlas.replace_composite(["P", "S"], Image.new("RGBA", (16, 8)))


def test_single_kind_extract_composite_does_not_warn(recwarn):
    text = "a.png\nsize: 16,8\npma: true\nP\n bounds: 0,0,16,8\nQ\n bounds: 0,0,8,8\n"
    SpineAtlas.load(text, Image.new("RGBA", (16, 8))).extract_composite(["P", "Q"])
    assert not [w for w in recwarn if issubclass(w.category, UserWarning)]
