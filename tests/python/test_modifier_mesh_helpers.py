"""Parity tests for engine/modifier.py's pure mesh/identity helpers against
the real www/js/atlas-modifier.js (plain Node, via conftest.py's
run_modifier_oracle fixture). Inputs mined from
tests/atlas-modifier.test.js plus: a float-UV clockwise triangle (exact
flipped output, not just "area >= 0"), empty names, a null moddedSprites
map, and interleaved group order."""
import pytest

from atlas_toolkit.engine.modifier import (
    combine_mesh_geometry,
    group_names_by_sprite_identity,
    mask_crop_rect_for_offsets,
)
from atlas_toolkit.raster.base import ImageHandle
from atlas_toolkit.raster.pillow_backend import PillowRasterBackend

TRI_POSITIVE = {"uvs": [0, 0, 1, 0, 0, 1], "triangles": [0, 1, 2]}
TRI_SECOND = {"uvs": [1, 1, 0, 1, 1, 0], "triangles": [0, 1, 2]}
TRI_REVERSED = {"uvs": [0, 0, 1, 0, 0, 1], "triangles": [0, 2, 1]}
TRI_FLOAT_CLOCKWISE = {"uvs": [0.5, 0.25, 0.25, 0.75, 0.75, 0.75], "triangles": [0, 1, 2]}

CASES = {
    "mcr_zero_offsets": ("maskCropRectForOffsets", [[0, 0, 100, 100], 40, 30]),
    "mcr_nonzero_offsets": ("maskCropRectForOffsets", [[20, 10, 200, 150], 50, 40]),
    "mcr_full_height_sprite": ("maskCropRectForOffsets", [[5, 0, 80, 60], 80, 60]),
    "cmg_union_rebases_indices": ("combineMeshGeometry", [["a", "b"], {"a": TRI_POSITIVE, "b": TRI_SECOND}]),
    "cmg_nobody_has_mesh": ("combineMeshGeometry", [["a", "b"], {}]),
    "cmg_one_missing_bails_group": ("combineMeshGeometry", [["a", "missing"], {"a": TRI_POSITIVE}]),
    "cmg_reversed_winding_normalized": ("combineMeshGeometry", [["a", "b"], {"a": TRI_POSITIVE, "b": TRI_REVERSED}]),
    "cmg_float_clockwise_flipped": ("combineMeshGeometry", [["a"], {"a": TRI_FLOAT_CLOCKWISE}]),
    "cmg_empty_names": ("combineMeshGeometry", [[], {}]),
    "gnb_shared_pair": ("groupNamesBySpriteIdentity", [{"a": 1, "b": 1, "c": 2}, {"a": 1, "b": 1, "c": 2}]),
    "gnb_absent_from_sprites": ("groupNamesBySpriteIdentity", [{"a": 1}, {"a": 1, "b": 3}]),
    "gnb_null_modded": ("groupNamesBySpriteIdentity", [{"a": 1}, None]),
    "gnb_interleaved_order": ("groupNamesBySpriteIdentity", [{"a": 1, "b": 2, "c": 1}, {"a": 1, "b": 2, "c": 1}]),
}


def _handles(ids: dict) -> dict:
    return {name: ImageHandle(handle_id) for name, handle_id in ids.items()}


def _python_result(op: str, args: list):
    if op == "maskCropRectForOffsets":
        return mask_crop_rect_for_offsets(*args)
    if op == "combineMeshGeometry":
        names, lookup = args
        return combine_mesh_geometry(names, lookup.get)
    if op == "groupNamesBySpriteIdentity":
        sprite_ids, modded_ids = args
        return group_names_by_sprite_identity(
            _handles(sprite_ids), None if modded_ids is None else _handles(modded_ids),
        )
    raise ValueError(f"unknown op: {op}")


@pytest.fixture(scope="module")
def js_results(run_modifier_oracle):
    case_ids = list(CASES)
    return dict(zip(case_ids, run_modifier_oracle([CASES[c] for c in case_ids]), strict=True))


@pytest.mark.parametrize("case_id", list(CASES))
def test_matches_real_js(case_id, js_results):
    op, args = CASES[case_id]
    assert _python_result(op, args) == js_results[case_id]


def test_group_names_by_sprite_identity_groups_real_backend_handles_by_value():
    # A clone has identical pixels but a distinct handle -- it must NOT
    # join the shared handle's group (identity, not pixel equality).
    raster = PillowRasterBackend()
    shared = raster.create(4, 4)
    lookalike = raster.clone(shared)
    sprites = {"a": shared, "b": shared, "c": lookalike}
    assert group_names_by_sprite_identity(sprites, dict(sprites)) == [["a", "b"], ["c"]]


def test_combine_mesh_geometry_does_not_mutate_lookup_geometry():
    geometry = {"uvs": [0, 0, 1, 0, 0, 1], "triangles": [0, 2, 1]}
    combined = combine_mesh_geometry(["a"], {"a": geometry}.get)
    assert combined == {"uvs": [0, 0, 1, 0, 0, 1], "triangles": [0, 1, 2]}
    assert geometry == {"uvs": [0, 0, 1, 0, 0, 1], "triangles": [0, 2, 1]}
    assert combined["uvs"] is not geometry["uvs"]
