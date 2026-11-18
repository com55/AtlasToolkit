# SPDX-FileCopyrightText: 2026 com55
# SPDX-License-Identifier: Apache-2.0
"""Keeping the layout with a shared composite image (spec 2026-11-18).

`split_composite=False` gives every region of a `replace_composite` group the
whole image, so a page holding such a group cannot keep its layout:
`place_in_place`'s composite fit rule assumes one piece per region. That page
is repacked unsplit instead, while every other page still tries to keep its
layout.

The public API still forces a split whenever it keeps the layout (spec
2026-11-05 R5; Task 3 removes that), so these tests drop the forcing and drive
the engine through `SpineAtlas.build`, as the app will once the setting is its
own.
"""
import dataclasses
from contextlib import contextmanager

import numpy as np
from PIL import Image

import atlas_toolkit.api as api
from atlas_toolkit import SpineAtlas
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


def _unforced(resolved, split_composite):
    """Today's `_pack_options` forces a split whenever the layout is kept;
    this is the same cache key and auto-bleed rule without the forcing."""
    split = bool(split_composite)
    if resolved.bleed is None:
        resolved = dataclasses.replace(resolved, bleed=api._auto_bleed(split))
    return resolved, split


@contextmanager
def unforced_split(monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(api, "_pack_options", _unforced)
        yield


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


def test_shared_composite_page_repacks_unsplit(monkeypatch):
    atlas = aliased()
    with unforced_split(monkeypatch):
        kept = atlas.build(packer="rectangles_in_place", split_composite=False)
    repacked = aliased().build(packer="rectangles", split_composite=False)
    in_place.same_build(kept, repacked)                    # text and pixels, unsplit
    layout = in_place.layout(kept)
    assert layout["A"] == layout["B"]                      # one sprite, one rect: shared image
    assert layout["A"] != ("p.png", (0, 0, 10, 8), 0, (2, 3, 16, 16))   # not the pristine slot


def test_shared_composite_other_page_keeps_layout(monkeypatch):
    # Review Focus 2: the composite is on page 1 only; page 2 keeps its layout.
    atlas = two_pages()
    with unforced_split(monkeypatch):
        kept = atlas.build(packer="rectangles_in_place", split_composite=False)
    assert page_block(kept, "p.png") == page_block(
        two_pages().build(packer="rectangles", split_composite=False), "p.png")
    assert in_place.layout(kept)["A"] == in_place.layout(kept)["B"]

    layout = in_place.layout(kept)
    for name in ("D", "E"):
        assert layout[name] == in_place.layout(atlas)[name], name
    assert kept.pages["q.png"].size == (64, 32)
    before = np.asarray(atlas.pages["q.png"].convert("RGBA"))
    after = np.asarray(kept.pages["q.png"].convert("RGBA"))
    for x, y, w, h in [(8, 8, 12, 12), (0, 0, 6, 6)]:
        assert (before[y:y + h, x:x + w] == after[y:y + h, x:x + w]).all()


def test_split_composite_in_place_unchanged(monkeypatch):
    # Control: with the image split, both regions still go back into their
    # own slots, i.e. the layout is kept exactly as today.
    today = plain().build(packer="rectangles_in_place")    # the API still forces the split
    with unforced_split(monkeypatch):
        explicit = plain().build(packer="rectangles_in_place", split_composite=True)
    in_place.same_build(today, explicit)
    assert in_place.layout(explicit) == in_place.layout(plain())
    assert explicit.pages["p.png"].size == (48, 16)


def test_single_survivor_composite_still_in_place(monkeypatch):
    # A one-region group is a plain mod: its page still tries in place.
    atlas = SpineAtlas.load(PLAIN, {"p.png": in_place.page(48, 16, plain_rects())})
    arr = np.asarray(atlas.extract("A")).copy()
    arr[5:13, 2:12] = in_place.pattern(10, 8, 9)
    atlas.replace_composite(["A"], Image.fromarray(arr, "RGBA"))
    with unforced_split(monkeypatch):
        kept = atlas.build(packer="rectangles_in_place", split_composite=False)
    assert in_place.layout(kept) == in_place.layout(atlas)
    assert kept.pages["p.png"].size == (48, 16)
    assert (np.asarray(kept.extract("A")) == arr).all()
