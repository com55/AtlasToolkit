# SPDX-FileCopyrightText: 2026 com55
# SPDX-License-Identifier: Apache-2.0
# tests/python/test_packing_footprint_mesh.py


from atlas_toolkit.domain.packing import footprint_for_canonical, solid_mask, union_masks
from atlas_toolkit.engine.modifier import (
    combine_mesh_geometry,
    group_names_by_sprite_identity,
    mask_crop_rect_for_offsets,
)
from atlas_toolkit.raster.pillow_backend import PillowRasterBackend

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
