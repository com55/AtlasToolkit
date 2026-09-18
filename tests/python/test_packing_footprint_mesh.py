# tests/python/test_packing_footprint_mesh.py
import json
from pathlib import Path

import pytest

from atlas_toolkit.domain.packing import footprint_for_canonical, solid_mask, union_masks
from atlas_toolkit.engine.modifier import (
    combine_mesh_geometry,
    group_names_by_sprite_identity,
    mask_crop_rect_for_offsets,
)
from atlas_toolkit.raster.pillow_backend import PillowRasterBackend

FIXTURES = Path(__file__).parent / "fixtures"
ORACLE_PATH = FIXTURES / "footprint_mesh_js_oracle.json"

_needs_oracle = pytest.mark.skipif(
    not ORACLE_PATH.exists(),
    reason="footprint_mesh_js_oracle.json not generated -- run tests/python/gen_js_reference_footprint_mesh.mjs",
)

# Axis-aligned rectangles, matching the generator's RECT_A/RECT_B exactly
# (see that file's comment for why -- diagonal-edged triangles disagree
# with real antialiased Chromium output on edge pixels, which this test's
# byte-exact assertions cannot tolerate).
RECT_A = {"uvs": [0, 0, 0.5, 0, 0, 1, 0.5, 1], "triangles": [0, 1, 2, 1, 3, 2]}  # opaque for u < 0.5
RECT_B = {"uvs": [0, 0.5, 1, 0.5, 0, 1, 1, 1], "triangles": [0, 1, 2, 1, 3, 2]}  # opaque for v >= 0.5


def _load_oracle():
    return json.loads(ORACLE_PATH.read_text())


def _ctx(raster, sprites, regions, mesh_lookup_fn, modded_sprites=None, added_sprites=None):
    return {
        "sprites": sprites,
        "raster": raster,
        "modded_sprites": modded_sprites,
        "added_sprites": added_sprites,
        "regions": regions,
        "mesh_lookup_fn": mesh_lookup_fn,
        "combine_mesh_geometry": combine_mesh_geometry,
        "group_names_by_sprite_identity": group_names_by_sprite_identity,
        "mask_crop_rect_for_offsets": mask_crop_rect_for_offsets,
    }


@_needs_oracle
def test_single_sprite_no_offsets_matches_js_oracle():
    oracle = _load_oracle()["single_no_offsets"]
    raster = PillowRasterBackend()
    sprites = {"a": raster.create(20, 20)}
    regions = {"a": {"offsets": None}}
    mesh_lookup_fn = lambda name: RECT_A if name == "a" else None  # noqa: E731
    result = footprint_for_canonical(["a"], _ctx(raster, sprites, regions, mesh_lookup_fn))
    assert list(result) == oracle["mask"]


@_needs_oracle
def test_single_sprite_with_offsets_matches_js_oracle():
    oracle = _load_oracle()["single_with_offsets"]
    raster = PillowRasterBackend()
    sprites = {"a": raster.create(10, 8)}
    regions = {"a": {"offsets": [3, 2, 20, 16]}}
    mesh_lookup_fn = lambda name: RECT_B if name == "a" else None  # noqa: E731
    result = footprint_for_canonical(["a"], _ctx(raster, sprites, regions, mesh_lookup_fn))
    assert list(result) == oracle["mask"]


@_needs_oracle
def test_shared_canvas_mod_group_matches_js_oracle():
    oracle = _load_oracle()["shared_canvas_mod_group"]
    raster = PillowRasterBackend()
    shared = raster.create(20, 20)
    sprites = {"a": shared, "b": shared}
    modded = {"a": shared, "b": shared}
    regions = {"a": {"offsets": None}, "b": {"offsets": None}}
    mesh_lookup_fn = lambda name: RECT_A if name == "a" else RECT_B if name == "b" else None  # noqa: E731
    result = footprint_for_canonical(
        ["a", "b"], _ctx(raster, sprites, regions, mesh_lookup_fn, modded_sprites=modded),
    )
    assert list(result) == oracle["mask"]


@_needs_oracle
def test_dimension_incompatible_member_excluded_matches_js_oracle():
    oracle = _load_oracle()["dimension_incompatible_member_excluded"]
    raster = PillowRasterBackend()
    sprites = {"a": raster.create(20, 20), "b": raster.create(10, 10)}
    regions = {"a": {"offsets": None}, "b": {"offsets": None}}
    mesh_lookup_fn = lambda name: RECT_A if name == "a" else RECT_B  # noqa: E731
    result = footprint_for_canonical(["a", "b"], _ctx(raster, sprites, regions, mesh_lookup_fn))
    assert list(result) == oracle["mask"]


@_needs_oracle
def test_member_without_mesh_falls_back_solid_matches_js_oracle():
    oracle = _load_oracle()["member_without_mesh_falls_back_solid"]
    assert all(b == 1 for b in oracle["mask"]), "oracle fixture is not solid -- regenerate or fix generator"
    raster = PillowRasterBackend()
    sprites = {"a": raster.create(20, 20), "b": raster.create(20, 20)}
    regions = {"a": {"offsets": None}, "b": {"offsets": None}}
    mesh_lookup_fn = lambda name: RECT_A if name == "a" else None  # noqa: E731
    result = footprint_for_canonical(["a", "b"], _ctx(raster, sprites, regions, mesh_lookup_fn))
    assert list(result) == oracle["mask"]
    assert all(b == 1 for b in result)


def test_no_mesh_lookup_fn_still_returns_solid_mask():
    # Pre-existing Stage-5 behaviour, must not regress.
    raster = PillowRasterBackend()
    sprites = {"a": raster.create(5, 5)}
    regions = {"a": {"offsets": None}}
    result = footprint_for_canonical(["a"], _ctx(raster, sprites, regions, None))
    assert result == solid_mask(5, 5)


def test_union_masks_ors_every_mask_together():
    m1 = bytearray([1, 0, 0, 0])
    m2 = bytearray([0, 1, 0, 0])
    assert union_masks([m1, m2], 2, 2) == bytearray([1, 1, 0, 0])


def test_union_masks_of_empty_list_returns_solid():
    assert union_masks([], 2, 2) == solid_mask(2, 2)
