# SPDX-FileCopyrightText: 2026 com55
# SPDX-License-Identifier: Apache-2.0
"""Pinned tests for engine/modifier.py's pure mesh/identity helpers."""

from atlas_toolkit.engine.modifier import (
    combine_mesh_geometry,
    group_names_by_sprite_identity,
)
from atlas_toolkit.raster.pillow_backend import PillowRasterBackend

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
