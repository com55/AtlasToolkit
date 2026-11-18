# SPDX-FileCopyrightText: 2026 com55
# SPDX-License-Identifier: Apache-2.0
"""resolve_packer: the one place pack settings are validated (spec 2026-11-18)."""
import ast
from pathlib import Path

import numpy as np
import pytest

import atlas_toolkit.packer as packer_module
from atlas_toolkit.packer import PackOptions, Packer, ResolvedPack, resolve_packer


def resolve_in(options, *, mesh_ready=True):
    return resolve_packer(options, mesh_ready=mesh_ready)


def test_defaults():
    resolved, needs_mesh = resolve_in(None, mesh_ready=True)
    assert resolved == ResolvedPack(
        nest=True, padding=2, mesh_aware=True, mesh_footprint=True,
        keep_layout=True, split=True, bleed=True,
    )
    assert needs_mesh is False


def test_auto_without_mesh():
    resolved, needs_mesh = resolve_in(None, mesh_ready=False)
    assert (resolved.nest, resolved.mesh_aware, resolved.mesh_footprint) == (True, False, False)
    assert needs_mesh is False


def test_auto_mesh_aware_false():
    resolved, _ = resolve_in(PackOptions(mesh_aware=False), mesh_ready=True)
    assert (resolved.nest, resolved.mesh_aware, resolved.mesh_footprint) == (True, False, False)


def test_auto_mesh_aware_true_without_mesh():
    resolved, needs_mesh = resolve_in(PackOptions(mesh_aware=True), mesh_ready=False)
    assert resolved.mesh_aware is True
    assert needs_mesh is True


@pytest.mark.parametrize("packer, nest, footprint", [
    ("shelf", False, False), ("rectangles", True, False), ("polygons", True, True),
    (Packer.SHELF, False, False), (Packer.POLYGONS, True, True),
])
def test_modes_map_to_engine_switches(packer, nest, footprint):
    resolved, _ = resolve_in(PackOptions(packer=packer), mesh_ready=True)
    assert (resolved.nest, resolved.mesh_footprint, resolved.padding) == (nest, footprint, 2)


def test_auto_with_mesh_maps_to_polygons():
    resolved, _ = resolve_in(PackOptions(packer=Packer.AUTO), mesh_ready=True)
    assert (resolved.nest, resolved.mesh_footprint) == (True, True)


def test_polygons_refuses_mesh_aware_false():
    with pytest.raises(ValueError, match="polygons"):
        resolve_in(PackOptions(packer="polygons", mesh_aware=False))


def test_unknown_packer_lists_the_valid_names():
    with pytest.raises(ValueError, match="auto, shelf, rectangles, polygons"):
        resolve_in(PackOptions(packer="nest"))


@pytest.mark.parametrize("packer", ["rectangles_in_place", "polygons_in_place", "grid"])
def test_removed_packers_are_unknown(packer):
    with pytest.raises(ValueError, match="auto, shelf, rectangles, polygons"):
        resolve_in(PackOptions(packer=packer))


@pytest.mark.parametrize("packer", [3, None, b"shelf"])
def test_packer_of_wrong_type(packer):
    with pytest.raises(TypeError, match="packer"):
        resolve_in(PackOptions(packer=packer))


@pytest.mark.parametrize("options", [{"packer": "shelf"}, 2, "shelf"])
def test_not_pack_options(options):
    with pytest.raises(TypeError, match="PackOptions"):
        resolve_in(options)


def test_none_options_are_the_defaults():
    assert resolve_in(None, mesh_ready=False) == resolve_in(PackOptions(), mesh_ready=False)


@pytest.mark.parametrize("padding, error", [(0, ValueError), (257, ValueError), (-1, ValueError),
                                            (True, TypeError), (2.5, TypeError), ("3", TypeError)])
def test_bad_padding(padding, error):
    for packer in ("shelf", "rectangles", "polygons"):
        with pytest.raises(error):
            resolve_in(PackOptions(packer=packer, padding=padding))


@pytest.mark.parametrize("packer", ["shelf", "rectangles", "polygons"])
@pytest.mark.parametrize("padding", [1, 2, 256])
def test_padding_range_ends(packer, padding):
    assert resolve_in(PackOptions(packer=packer, padding=padding))[0].padding == padding


def test_padding_none_is_the_default():
    assert resolve_in(PackOptions(padding=None))[0].padding == 2


def test_validation_order():
    # The padding is checked before the polygons / mesh_aware conflict.
    with pytest.raises(ValueError, match="padding"):
        resolve_in(PackOptions(packer="polygons", padding=0, mesh_aware=False))


def test_packer_names_are_case_sensitive():
    with pytest.raises(ValueError):
        resolve_in(PackOptions(packer="SHELF"))


def test_numpy_integer_padding_resolves_to_plain_int():
    resolved, _ = resolve_in(PackOptions(padding=np.int64(3)))
    assert resolved.padding == 3 and type(resolved.padding) is int
    assert resolved == resolve_in(PackOptions(padding=3))[0]


@pytest.mark.parametrize("mesh_aware", ["yes", 1, 0])
def test_mesh_aware_of_wrong_type(mesh_aware):
    with pytest.raises(TypeError, match="mesh_aware"):
        resolve_in(PackOptions(mesh_aware=mesh_aware))


@pytest.mark.parametrize("name, value", [("try_keep_layout", 1), ("try_keep_layout", None),
                                         ("share_composite_image", "yes"), ("share_composite_image", None)])
def test_bool_only_flags_reject_non_bool(name, value):
    with pytest.raises(TypeError, match=name):
        resolve_in(PackOptions(**{name: value}))


@pytest.mark.parametrize("name", ["bleed", "mesh_aware"])
def test_auto_flags_reject_non_bool(name):
    with pytest.raises(TypeError, match=name):
        resolve_in(PackOptions(**{name: 1}))


def test_options_do_not_validate_themselves():
    PackOptions(padding=0, mesh_aware="nope")  # only resolve_packer raises


def test_resolved_pack_is_hashable_and_equal_by_value():
    assert {ResolvedPack(False, 2, True, False, True, True, True)} == {
        resolve_in(PackOptions(packer="shelf"))[0]
    }


def test_packer_module_imports_only_the_standard_library():
    tree = ast.parse(Path(packer_module.__file__).read_text())
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            roots.add(node.module.split(".")[0])
    assert roots <= {"__future__", "dataclasses", "enum", "operator"}


# --- try_keep_layout --------------------------------------------------------

@pytest.mark.parametrize("value", [True, False])
def test_try_keep_layout_resolves(value):
    assert resolve_in(PackOptions(try_keep_layout=value))[0].keep_layout is value


# --- share_composite_image --------------------------------------------------

def test_share_composite_image():
    resolved, _ = resolve_in(PackOptions(share_composite_image=True))
    assert resolved.split is False
    assert resolved.bleed is False  # auto follows split


def test_share_composite_image_keeps_explicit_bleed():
    resolved, _ = resolve_in(PackOptions(share_composite_image=True, bleed=True))
    assert (resolved.split, resolved.bleed) == (False, True)


def test_split_bleeds_by_default():
    assert resolve_in(PackOptions())[0].bleed is True


# --- bleed (Spine's packer Bleed) -------------------------------------------

@pytest.mark.parametrize("packer", ["shelf", "rectangles", "polygons"])
@pytest.mark.parametrize("value, expected", [(False, False), (True, True)])
def test_bleed_resolves(packer, value, expected):
    resolved, _ = resolve_in(PackOptions(packer=packer, bleed=value))
    assert resolved.bleed is expected


@pytest.mark.parametrize("value", [1, "yes"])
def test_bleed_of_wrong_type(value):
    with pytest.raises(TypeError, match="bleed"):
        resolve_in(PackOptions(bleed=value))


def test_bleed_is_part_of_the_cache_key():
    on, _ = resolve_in(PackOptions(bleed=True))
    off, _ = resolve_in(PackOptions(bleed=False))
    assert on != off


# --- cache-key identity -----------------------------------------------------

def test_auto_resolves_to_the_packer_it_picks():
    with_mesh, _ = resolve_in(None, mesh_ready=True)
    assert with_mesh == resolve_in(PackOptions(packer="polygons"))[0]
    without_mesh, _ = resolve_in(None, mesh_ready=False)
    assert without_mesh == resolve_in(PackOptions(packer="rectangles"), mesh_ready=False)[0]


def test_keep_layout_and_share_image_are_different_cache_keys():
    base, _ = resolve_in(None)
    assert base != resolve_in(PackOptions(try_keep_layout=False))[0]
    assert base != resolve_in(PackOptions(share_composite_image=True))[0]
