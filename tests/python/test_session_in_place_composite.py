# SPDX-FileCopyrightText: 2026 com55
# SPDX-License-Identifier: Apache-2.0
"""Keeping the layout with a shared composite image (spec 2026-11-18).

`share_composite_image=True` gives every region of a `replace_composite`
group the whole image, so a page holding such a group cannot keep its
layout: `place_in_place`'s composite fit rule assumes one piece per region.
That page is repacked unsplit instead, while every other page still tries to
keep its layout.

Since spec 2026-11-18 the API no longer forces a split when it keeps the
layout, so these drive the unsplit build directly.
"""
import numpy as np
from PIL import Image

from atlas_toolkit import PackOptions, SpineAtlas
from tests.python import test_api_in_place as in_place

# Aliased survivors: A and B name one stored 10x8 rect with one offsets
# canvas, so the shared 16x16 image is exactly what `place_in_place` accepts
# when it only looks at the sprite inside the region's window. That is why
# nothing but the new page-level rule can make this page repack.
ALIASED = ("p.png\nsize: 48,16\nA\n bounds: 0,0,10,8\n offsets: 2,3,16,16\n"
           "B\n bounds: 0,0,10,8\n offsets: 2,3,16,16\nC\n bounds: 32,0,12,12\n")
TWO_PAGES = ALIASED + "\nq.png\nsize: 64,32\nD\n bounds: 8,8,12,12\n offsets: 4,4,20,20\nE\n bounds: 0,0,6,6\n"

# Distinct slots sharing one 16x16 offsets canvas: a split put each piece back
# into its own slot (test_api_in_place's composite-at-canvas-size case).
PLAIN = ("p.png\nsize: 48,16\nA\n bounds: 0,0,10,8\n offsets: 2,3,16,16\n"
         "B\n bounds: 16,0,6,12\n offsets: 8,1,16,16\nC\n bounds: 32,0,12,12\n")


def kept_unsplit(**fields):
    """Keep the layout, every region of a composite group uses the whole image
    unless a test overrides it."""
    return PackOptions(**{"try_keep_layout": True, "share_composite_image": True, **fields})


def repacked_unsplit(**fields):
    """The same pack with every edited page repacked."""
    return PackOptions(**{"try_keep_layout": False, "share_composite_image": True, **fields})


def aliased_rects():
    return [(0, 0, 10, 8), (32, 0, 12, 12)]


def plain_rects():
    return [(0, 0, 10, 8), (16, 0, 6, 12), (32, 0, 12, 12)]


def aliased():
    """A replace_composite over the A/B aliases whose content sits inside
    their shared window: `place_in_place` would paste it into that slot."""
    atlas = SpineAtlas.load(ALIASED, {"p.png": in_place.page(48, 16, aliased_rects())})
    arr = np.zeros((16, 16, 4), np.uint8)
    arr[5:13, 2:12] = in_place.pattern(10, 8, 9)
    atlas.replace_composite(["A", "B"], Image.fromarray(arr, "RGBA"))
    return atlas


def two_pages():
    atlas = SpineAtlas.load(TWO_PAGES, {
        "p.png": in_place.page(48, 16, aliased_rects()),
        "q.png": in_place.page(64, 32, [(8, 8, 12, 12), (0, 0, 6, 6)], seed=2),
    })
    arr = np.zeros((16, 16, 4), np.uint8)
    arr[5:13, 2:12] = in_place.pattern(10, 8, 9)
    atlas.replace_composite(["A", "B"], Image.fromarray(arr, "RGBA"))
    atlas.add_region("N", in_place.img(6, 6, seed=7), page="q.png")
    return atlas


def plain():
    """Distinct slots, one shared image: the split builds the image back into
    A's and B's own windows."""
    atlas = SpineAtlas.load(PLAIN, {"p.png": in_place.page(48, 16, plain_rects())})
    arr = np.asarray(atlas.extract_composite(["A", "B"])).copy()
    assert arr.shape == (16, 16, 4)
    arr[6:12, 3:14] = in_place.pattern(11, 6, 9)      # straddles both windows
    atlas.replace_composite(["A", "B"], Image.fromarray(arr, "RGBA"))
    return atlas


def page_block(built, filename):
    return [block for block in built.atlas_text.split("\n\n") if block.startswith(filename)]


def test_shared_composite_page_repacks_unsplit():
    atlas = aliased()
    kept = atlas.build(options=kept_unsplit())
    repacked = aliased().build(options=repacked_unsplit())
    in_place.same_build(kept, repacked)                    # text and pixels, unsplit
    layout = in_place.layout(kept)
    assert layout["A"] == layout["B"]                      # one sprite, one rect: shared image
    assert layout["A"] != ("p.png", (0, 0, 10, 8), 0, (2, 3, 16, 16))   # not the pristine slot


def test_shared_composite_other_page_keeps_layout():
    # Review Focus 2: the composite is on page 1 only; page 2 keeps its layout.
    atlas = two_pages()
    kept = atlas.build(options=kept_unsplit())
    assert page_block(kept, "p.png") == page_block(
        two_pages().build(options=repacked_unsplit()), "p.png")
    assert in_place.layout(kept)["A"] == in_place.layout(kept)["B"]

    layout = in_place.layout(kept)
    for name in ("D", "E"):
        assert layout[name] == in_place.layout(atlas)[name], name
    assert kept.pages["q.png"].size == (64, 32)
    before = np.asarray(atlas.pages["q.png"].convert("RGBA"))
    after = np.asarray(kept.pages["q.png"].convert("RGBA"))
    for x, y, w, h in [(8, 8, 12, 12), (0, 0, 6, 6)]:
        assert (before[y:y + h, x:x + w] == after[y:y + h, x:x + w]).all()


def test_shared_composite_exclusion_does_not_affect_split_builds():
    # The shared-composite exclusion leaves split builds eligible to keep
    # both regions in their own slots.
    kept = plain().build(options=kept_unsplit(share_composite_image=False))
    repacked = plain().build(options=repacked_unsplit(share_composite_image=False))
    assert kept.atlas_text != repacked.atlas_text
    assert in_place.layout(kept) == in_place.layout(plain())
    assert kept.pages["p.png"].size == (48, 16)


def test_single_survivor_composite_still_in_place():
    # A one-region group is a plain mod: its page still tries in place.
    atlas = SpineAtlas.load(PLAIN, {"p.png": in_place.page(48, 16, plain_rects())})
    arr = np.asarray(atlas.extract("A")).copy()
    arr[5:13, 2:12] = in_place.pattern(10, 8, 9)
    atlas.replace_composite(["A"], Image.fromarray(arr, "RGBA"))
    kept = atlas.build(options=kept_unsplit())
    assert in_place.layout(kept) == in_place.layout(atlas)
    assert kept.pages["p.png"].size == (48, 16)
    assert (np.asarray(kept.extract("A")) == arr).all()


def test_cross_page_shared_composite_repacks_both_pages():
    text = ("p.png\nsize: 48,16\nA\n bounds: 0,0,10,8\n offsets: 2,3,16,16\n"
            "C\n bounds: 32,0,12,12\n\n"
            "q.png\nsize: 48,16\nD\n bounds: 0,0,10,8\n offsets: 2,3,16,16\n"
            "E\n bounds: 32,0,12,12\n")
    atlas = SpineAtlas.load(text, {
        "p.png": in_place.page(48, 16, aliased_rects()),
        "q.png": in_place.page(48, 16, aliased_rects(), seed=2),
    })
    # The image fits both survivors' slots, so only the shared-image exclusion
    # makes both pages repack.
    arr = np.zeros((16, 16, 4), np.uint8)
    arr[5:13, 2:12] = in_place.pattern(10, 8, 9)
    atlas.replace_composite(["A", "D"], Image.fromarray(arr, "RGBA"))
    kept = atlas.build(options=kept_unsplit())
    repacked = atlas.build(options=repacked_unsplit())
    assert set(kept.pages) == {"p.png", "q.png"}
    in_place.same_build(kept, repacked)
