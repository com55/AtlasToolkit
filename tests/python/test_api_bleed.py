# SPDX-FileCopyrightText: 2026 com55
# SPDX-License-Identifier: Apache-2.0
"""build/pack(packer_options=PackerOptions(bleed=True)): Spine's packer Bleed
on every page the pack writes that is not pma."""
import numpy as np
import pytest
from PIL import Image

from atlas_toolkit import PackerOptions, SpineAtlas
from atlas_toolkit.domain.bleed import color_bleed

TEXT = (
    "p.png\nsize: 24,12\npma: {pma}\nA\n bounds: 0,0,8,6\nB\n bounds: 10,0,6,8\n"
    "\nq.png\nsize: 8,8\nC\n bounds: 0,0,8,8\n"
)


def pattern(w, h, seed):
    ys, xs = np.mgrid[0:h, 0:w]
    rgba = np.zeros((h, w, 4), np.uint8)
    rgba[..., 0] = (xs * 31 + seed) % 256
    rgba[..., 1] = (ys * 17 + seed * 5) % 256
    rgba[..., 2] = 90
    rgba[..., 3] = 255
    return rgba


def atlas(pma=False):
    p = np.zeros((12, 24, 4), np.uint8)
    p[0:6, 0:8] = pattern(8, 6, 1)
    p[0:8, 10:16] = pattern(6, 8, 2)
    q = pattern(8, 8, 3)
    q[0, 0] = (5, 6, 7, 0)  # an untouched page keeps even its alpha-0 colour
    built = SpineAtlas.load(TEXT.format(pma=str(pma).lower()), {
        "p.png": Image.fromarray(p, "RGBA"), "q.png": Image.fromarray(q, "RGBA"),
    })
    built.replace({"A": Image.fromarray(pattern(9, 5, 7), "RGBA")})  # repacks p.png
    return built


@pytest.mark.parametrize("packer", ["shelf", "rectangles", "rectangles_in_place"])
def test_bleed_colours_only_transparent_pixels_of_written_straight_pages(packer):
    plain = atlas().build(packer=packer, packer_options=PackerOptions(bleed=False))
    bled = atlas().build(packer=packer, packer_options=PackerOptions(bleed=True))
    assert bled.atlas_text == plain.atlas_text
    before = np.asarray(plain.pages["p.png"])
    after = np.asarray(bled.pages["p.png"])
    assert (before[..., 3] == 0).any()
    assert (after == color_bleed(before)).all()
    assert (after != before).any()  # control: something was bled
    assert np.asarray(bled.pages["q.png"]).tobytes() == np.asarray(plain.pages["q.png"]).tobytes()


def test_bleed_skips_pma_pages():
    plain = atlas(pma=True).build()
    bled = atlas(pma=True).build(packer_options=PackerOptions(bleed=True))
    assert np.asarray(bled.pages["p.png"]).tobytes() == np.asarray(plain.pages["p.png"]).tobytes()


@pytest.mark.parametrize("packer, split, expected", [
    ("shelf", False, False), ("shelf", True, True), ("rectangles", True, True),
    ("rectangles_in_place", False, True),  # in-place always splits
])
def test_default_bleed_follows_composite_splitting(packer, split, expected):
    a = atlas()
    auto = a.pack(packer=packer, split_composite=split)
    explicit = a.pack(packer=packer, packer_options=PackerOptions(bleed=expected), split_composite=split)
    assert np.asarray(auto[1]["p.png"]).tobytes() == np.asarray(explicit[1]["p.png"]).tobytes()
    other = a.pack(packer=packer, packer_options=PackerOptions(bleed=not expected), split_composite=split)
    assert np.asarray(other[1]["p.png"]).tobytes() != np.asarray(auto[1]["p.png"]).tobytes()


def test_bleed_defaults_off_and_is_cached_separately():
    a = atlas()
    off = a.pack()
    on = a.pack(packer_options=PackerOptions(bleed=True))
    assert a.pack(packer_options=PackerOptions(bleed=False))[1] is off[1]
    assert np.asarray(on[1]["p.png"]).tobytes() != np.asarray(off[1]["p.png"]).tobytes()


def test_bleed_survives_save(tmp_path):
    a = atlas()
    a.save(tmp_path / "out.atlas", packer_options=PackerOptions(bleed=True))
    saved = np.asarray(Image.open(tmp_path / "p.png").convert("RGBA"))
    assert (saved == np.asarray(a.build(packer_options=PackerOptions(bleed=True)).pages["p.png"])).all()
