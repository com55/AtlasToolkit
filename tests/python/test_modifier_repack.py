"""Parity tests for AtlasModifier's two repack entry points against the
real www/js/atlas-modifier.js (plain Node, via run_packing_oracle), plus
pinned no-Node cases covering multi-page scoping, the rescale-then-repack
path, and the mesh-stage deferral."""
import hashlib
import io

import pytest
from PIL import Image

from atlas_toolkit.document import AtlasDocument
from atlas_toolkit.engine.modifier import AtlasModifier, js_object_key_order, region_meta
from atlas_toolkit.raster.pillow_backend import PillowRasterBackend

PAGE_TEXT = "\n".join([
    "p.png", "size: 64,64", "format: RGBA8888", "filter: Linear, Linear", "repeat: none",
    "alpha", "  bounds: 0, 0, 20, 12", "  offsets: 3, 4, 40, 30",
    "beta", "  bounds: 20, 0, 20, 12", "  offsets: 3, 4, 40, 30",
    "gamma", "  bounds: 0, 12, 9, 17",
])
TWO_PAGE_TEXT = "\n".join([
    "p1.png", "size: 64,64",
    "one", "  bounds: 0, 0, 10, 10",
    "p2.png", "size: 32,32",
    "two", "  bounds: 0, 0, 12, 6", "  offsets: 1, 2, 20, 20",
    "three", "  bounds: 12, 0, 5, 9",
])

# Integer-like region names, listed 10 BEFORE 2 on purpose. JS orders object
# keys with array-index keys ascending, so the real engine packs "2" first --
# `_pack_and_emit` trusts the order it is handed, and `_scoped_region_meta` is
# what supplies it. Without this fixture nothing on the repack path exercises
# that ordering, and dropping `js_object_key_order` passes the whole suite.
INT_TEXT = "\n".join([
    "p.png", "size: 64,64",
    "10", "  bounds: 0, 0, 20, 12",
    "2", "  bounds: 20, 0, 8, 30",
    "a", "  bounds: 0, 12, 9, 17",
])

MODDED = {"beta": {"w": 20, "h": 12, "content": "MODDED"}}
ADDED = {"add#1": {"w": 14, "h": 9, "content": "ADDED", "atlas_name": "newbie"}}

CASES = {
    "repack_plain": ("repackWithModdedSprites", [PAGE_TEXT, 64, 64, "p.png", None, None]),
    "repack_modded": ("repackWithModdedSprites", [PAGE_TEXT, 64, 64, "p.png", MODDED, ["beta"]]),
    "repack_second_page": ("repackWithModdedSprites", [TWO_PAGE_TEXT, 32, 32, "p2.png", None, None]),
    "repack_second_page_rescaled": (
        "repackWithModdedSprites", [TWO_PAGE_TEXT, 64, 64, "p2.png", None, None],
    ),
    "repack_effective_added": (
        "repackWithEffectiveModel", [PAGE_TEXT, 64, 64, "p.png", None, ADDED, None],
    ),
    "repack_effective_removed": (
        "repackWithEffectiveModel", [PAGE_TEXT, 64, 64, "p.png", ["beta"], None, None],
    ),
    "repack_integer_like_names": (
        "repackWithModdedSprites", [INT_TEXT, 64, 64, "p.png", None, None],
    ),
}


class _RecordingRaster(PillowRasterBackend):
    def __init__(self) -> None:
        super().__init__()
        self.pastes: list[list[int]] = []
        self.clones = 0

    def paste(self, dest, src, x, y):
        self.pastes.append([x, y])
        super().paste(dest, src, x, y)

    def clone(self, handle):
        # Counted, not forbidden: the no-clone rule applies to the modded
        # sprite path specifically, and counting is the only way to observe
        # it -- see test_a_modded_sprite_handle_is_used_without_cloning.
        self.clones += 1
        return super().clone(handle)


def _base_image(raster: PillowRasterBackend, w: int, h: int):
    """A patterned page: pixel (x, y) is (x % 256, y % 256, 90, 255), so
    every extracted sprite gets content unique to its own bounds -- which
    is what makes dedup behave the way it does on a real page."""
    data = bytes(v for y in range(h) for x in range(w) for v in (x % 256, y % 256, 90, 255))
    buf = io.BytesIO()
    Image.frombytes("RGBA", (w, h), data).save(buf, format="PNG")
    return raster.decode_png(buf.getvalue())


def _sprite(raster: PillowRasterBackend, w: int, h: int, content: str):
    # Hashed, not summed: an additive checksum is anagram-invariant, so two
    # future content tags that are permutations of each other would produce
    # byte-identical sprites and dedup together by accident.
    digest = hashlib.sha256(content.encode()).digest()
    colour = (digest[0], digest[1], digest[2], 255)
    buf = io.BytesIO()
    Image.new("RGBA", (w, h), colour).save(buf, format="PNG")
    return raster.decode_png(buf.getvalue())


def _modifier(raster, text, real_w, real_h, target_page):
    return AtlasModifier(
        AtlasDocument.parse(text), _base_image(raster, real_w, real_h), raster, target_page
    )


def _shape(raster, result):
    canvas_w, canvas_h = raster.size(result.image)
    return {
        "canvas_w": canvas_w,
        "canvas_h": canvas_h,
        "draws": raster.pastes,
        "region_bounds": result.region_bounds,
        "atlas_text": result.document.serialize(),
    }


def _python_result(case_id):
    op, args = CASES[case_id]
    raster = _RecordingRaster()
    if op == "repackWithModdedSprites":
        text, real_w, real_h, page, modded_specs, full_names = args
        modifier = _modifier(raster, text, real_w, real_h, page)
        modded = None
        if modded_specs is not None:
            modded = {
                name: _sprite(raster, spec["w"], spec["h"], spec["content"])
                for name, spec in modded_specs.items()
            }
        result = modifier.repack_with_modded_sprites(
            modded, None if full_names is None else set(full_names)
        )
        shaped = _shape(raster, result)
        # The JS oracle reports `m.regionNames`, which is ALREADY in object-key
        # order. Its faithful Python analogue is the ordered list, not the raw
        # text-order one -- comparing the raw list would fail on correct code
        # for any integer-like name. The real discriminator for a dropped
        # reordering is region_bounds/atlas_text, which this case also compares.
        shaped["scoped_names"] = js_object_key_order(modifier.region_names)
        return shaped

    text, real_w, real_h, page, drop_names, added_specs, modded_specs = args
    modifier = _modifier(raster, text, real_w, real_h, page)
    dropped = set(drop_names or [])
    names = [n for n in js_object_key_order(modifier.region_names) if n not in dropped]
    effective_regions = {name: region_meta(modifier.regions[name]) for name in names}
    added = {}
    for key, spec in (added_specs or {}).items():
        effective_regions[key] = {
            "name": key, "atlasName": spec["atlas_name"], "page": page,
            "bounds": [0, 0, spec["w"], spec["h"]], "offsets": None, "rotate": 0,
            "index": -1, "split": None, "pad": None, "extraPairs": [],
        }
        names.append(key)
        added[key] = _sprite(raster, spec["w"], spec["h"], spec["content"])
    modded = None
    if modded_specs is not None:
        modded = {
            name: _sprite(raster, spec["w"], spec["h"], spec["content"])
            for name, spec in modded_specs.items()
        }
    result = modifier.repack_with_effective_model(names, effective_regions, added, modded)
    return _shape(raster, result)


@pytest.fixture(scope="module")
def js_results(run_packing_oracle):
    case_ids = list(CASES)
    return dict(zip(case_ids, run_packing_oracle([CASES[c] for c in case_ids]), strict=True))


@pytest.mark.parametrize("case_id", list(CASES))
def test_matches_real_js(case_id, js_results):
    expected = dict(js_results[case_id])
    actual = _python_result(case_id)
    # The oracle reports scoped_names only for the modded-sprite op.
    if "scoped_names" not in expected:
        actual.pop("scoped_names", None)
    assert actual == expected


def test_repack_scopes_to_the_target_page():
    result = _python_result("repack_second_page")
    assert result["scoped_names"] == ["two", "three"]
    assert set(result["region_bounds"]) == {"two", "three"}
    assert result["atlas_text"].startswith("p2.png\n")
    assert "one" not in result["atlas_text"]


def test_a_page_whose_real_image_is_larger_is_rescaled_before_packing():
    # Declared 32x32, real 64x64 -> every region and its offsets double.
    result = _python_result("repack_second_page_rescaled")
    assert result["region_bounds"]["two"][2:4] == [24, 12]
    assert "offsets: 2, 4, 40, 40" in result["atlas_text"]


def test_full_canvas_modded_region_loses_its_offsets():
    result = _python_result("repack_modded")
    assert "beta\n  bounds: 0, 12, 20, 12\ngamma" in result["atlas_text"]


def test_added_sprites_are_emitted_under_their_atlas_name():
    result = _python_result("repack_effective_added")
    assert "add#1" in result["region_bounds"]
    assert "newbie\n  bounds:" in result["atlas_text"]
    assert "add#1" not in result["atlas_text"]


def test_removed_regions_are_neither_packed_nor_emitted():
    result = _python_result("repack_effective_removed")
    assert set(result["region_bounds"]) == {"alpha", "gamma"}
    assert "beta" not in result["atlas_text"]
    assert len(result["draws"]) == 2


def test_a_modded_sprite_handle_is_used_without_cloning():
    # JS's _toCanvas preserves canvas identity here, and
    # group_names_by_sprite_identity groups by handle value -- a clone would
    # silently split a shared-canvas mod group.
    #
    # An earlier version of this test asserted only that the input sprite
    # still measured 20x12, which stays true after a clone -- it passed on
    # broken code. Cloning has no observable effect through this stage's
    # public surface (handle-identity grouping is consumed only by the
    # not-yet-ported mesh path), so the invariant is pinned directly by
    # counting clone calls: nothing on the repack path may clone at all.
    raster = _RecordingRaster()
    modifier = _modifier(raster, PAGE_TEXT, 64, 64, "p.png")
    shared = _sprite(raster, 20, 12, "SHARED")
    before = raster.clones
    modifier.repack_with_modded_sprites({"alpha": shared, "beta": shared})
    assert raster.clones == before


def test_repack_orders_integer_like_names_the_way_js_does():
    # Verified against the real JS oracle: text order 10, 2, a comes back as
    # ["2", "10", "a"] with "2" packed at (0, 0) on a 40x20 canvas. Without
    # js_object_key_order in _scoped_region_meta, "10" packs first and the
    # ENTIRE suite still passes -- no other fixture on the repack path uses
    # an integer-like name.
    result = _python_result("repack_integer_like_names")
    assert result["scoped_names"] == ["2", "10", "a"]
    assert result["region_bounds"]["2"][:2] == [0, 0]
    assert (result["canvas_w"], result["canvas_h"]) == (40, 20)
    assert result["atlas_text"].index("\n2\n") < result["atlas_text"].index("\n10\n")


@pytest.mark.parametrize(
    "call",
    [
        lambda m, fn: m.repack_with_modded_sprites(None, None, fn),
        lambda m, fn: m.repack_with_effective_model([], {}, None, None, None, fn),
    ],
)
def test_mesh_aware_repack_defers_to_the_mesh_stage(call):
    raster = PillowRasterBackend()
    modifier = _modifier(raster, PAGE_TEXT, 64, 64, "p.png")
    with pytest.raises(NotImplementedError, match="mesh"):
        call(modifier, lambda name: None)
