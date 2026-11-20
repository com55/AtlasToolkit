# SPDX-FileCopyrightText: 2026 com55
# SPDX-License-Identifier: Apache-2.0
"""Pinned tests for engine/modifier.py's page-size reconciliation and
AtlasModifier's page-scoped construction, including the object-identity /
non-mutation guarantees."""

from atlas_toolkit.document import AtlasDocument
from atlas_toolkit.engine.modifier import AtlasModifier
from atlas_toolkit.raster.pillow_backend import PillowRasterBackend

TWO_PAGE = (
    "a.png\nsize: 10,10\n"
    "arm\n  bounds: 0, 0, 4, 4\n  offsets: 0, 0, 4, 4\n"
    "leg\n  rotate: 45\n  bounds: 0, 0, 2, 2\n  offsets: 1, 1, 3, 3\n"
    "b.png\nsize: 10,10\n"
    "arm\n  bounds: 1, 1, 2, 2"
)
NON_UNIFORM = "a.png\nsize: 32,32\nhero\n  rotate: true\n  bounds: 2, 3, 6, 5\n  offsets: 1, 3, 10, 13"
def _modifier(text: str, real_w: int, real_h: int, target_page: str) -> AtlasModifier:
    raster = PillowRasterBackend()
    return AtlasModifier(AtlasDocument.parse(text), raster.create(real_w, real_h), raster, target_page)


def test_noop_returns_the_same_document_object():
    raster = PillowRasterBackend()
    document = AtlasDocument.parse(TWO_PAGE)
    modifier = AtlasModifier(document, raster.create(10, 10), raster, "a.png")
    assert modifier.document is document
    assert modifier.regions["arm"].offsets == [0, 0, 4, 4]


def test_scaling_does_not_mutate_the_input_document():
    raster = PillowRasterBackend()
    document = AtlasDocument.parse(TWO_PAGE)
    AtlasModifier(document, raster.create(20, 20), raster, "a.png")
    assert document.pages[0].size == [10, 10]
    assert document.pages[0].regions[0].bounds == [0, 0, 4, 4]
    assert document.pages[0].regions[0].offsets == [0, 0, 4, 4]
    assert document.pages[0].regions[1].rotate == 45


def test_non_uniform_scale_pinned():
    # sx = 2, sy = 1.5: y 3 -> 4.5 -> 4, h 5 -> 7.5 -> 8, off_y 3 -> 4.5 -> 4,
    # orig_h 13 -> 19.5 -> 20 (banker's rounding on every tie).
    modifier = _modifier(NON_UNIFORM, 64, 48, "a.png")
    hero = modifier.regions["hero"]
    assert hero.bounds == [4, 4, 12, 8]
    assert hero.offsets == [2, 4, 20, 20]
    assert hero.rotate == 90
    assert modifier.document.pages[0].size == [64, 48]
