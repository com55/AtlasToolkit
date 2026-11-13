# SPDX-FileCopyrightText: 2026 com55
# SPDX-License-Identifier: Apache-2.0
"""Deferred remove of an added key undoes the add (spec 2026-10-31 §3.1, R8)."""
from PIL import Image

from atlas_toolkit.engine.session import AtlasSession
from atlas_toolkit.raster.pillow_backend import PillowRasterBackend

ATLAS = "p.png\nsize: 16,8\nHead\n bounds: 0,0,6,5\nBody\n bounds: 6,0,8,6\n"


def page():
    return Image.new("RGBA", (16, 8), (10, 20, 30, 255))


def sprite(seed):
    return Image.new("RGBA", (3, 3), (seed, 0, 0, 255))


def session(defer=True):
    return AtlasSession.load_images(ATLAS, {"p.png": page()}, PillowRasterBackend(), defer_repack=defer)


def kinds(s):
    return [(b["type"], b.get("internal_key", b.get("target_key"))) for b in s._mod_batches]


def test_add_then_remove_leaves_no_edit():
    s = session()
    s.add_region(sprite(1), "N")
    s.remove_regions(["N"])
    assert not s.has_pending_edits and kinds(s) == []


def test_add_rename_remove_drops_the_rename_too():
    s = session()
    s.add_region(sprite(1), "N")
    s.rename_region("N", "M")
    s.remove_regions(["N"])
    assert kinds(s) == []


def test_add_two_remove_one_keeps_the_other():
    s = session()
    s.add_region(sprite(1), "N")
    s.add_region(sprite(2), "M")
    s.remove_regions(["N"])
    assert kinds(s) == [("add", "M")]


def test_duplicate_targets_cannot_swallow_a_later_add():
    s = session()
    s.add_region(sprite(1), "X")
    s.remove_regions(["X", "X"])
    s.add_region(sprite(2), "X")  # reuses the freed internal key "X"
    assert kinds(s) == [("add", "X")]
    assert [label for _, label in s.region_labels()] == ["Head", "Body", "X"]


def test_pristine_remove_is_recorded_once():
    s = session()
    s.remove_regions(["Head", "Head"])
    s.remove_regions(["Head"])
    assert kinds(s) == [("remove", "Head")]


def test_unknown_key_records_nothing():
    s = session()
    s.remove_regions(["Nope"])
    assert kinds(s) == []


def test_immediate_mode_still_records_the_remove():
    s = session(defer=False)
    s.add_region(sprite(1), "N")
    s.remove_regions(["N"])
    assert kinds(s) == [("add", "N"), ("remove", "N")]
