"""resolve_packer: the one place pack settings are validated (spec 2026-10-22 §2-3)."""
import ast
from pathlib import Path

import numpy as np
import pytest

import atlas_toolkit.packer as packer_module
from atlas_toolkit.packer import Packer, PackerOptions, ResolvedPack, resolve_packer


def resolve(packer, mesh_aware=None, padding=None, mesh_ready=True):
    return resolve_packer(packer, PackerOptions(padding=padding, mesh_aware=mesh_aware), mesh_ready=mesh_ready)


@pytest.mark.parametrize("packer, mesh_aware, mesh_ready, expected", [
    # (resolved mesh_aware, needs_mesh), spec §2.2's table
    ("polygons", None, True, (True, True)),
    ("polygons", None, False, (True, True)),
    ("polygons", True, False, (True, True)),
    ("shelf", None, True, (True, False)),
    ("shelf", None, False, (False, False)),
    ("shelf", True, False, (True, True)),
    ("shelf", False, True, (False, False)),
    ("rectangles", None, True, (True, False)),
    ("rectangles", None, False, (False, False)),
    ("rectangles", True, False, (True, True)),
    ("rectangles", False, True, (False, False)),
])
def test_mesh_aware_table(packer, mesh_aware, mesh_ready, expected):
    resolved, needs_mesh = resolve(packer, mesh_aware=mesh_aware, mesh_ready=mesh_ready)
    assert (resolved.mesh_aware, needs_mesh) == expected


def test_polygons_refuses_mesh_aware_false():
    with pytest.raises(ValueError, match="polygons"):
        resolve("polygons", mesh_aware=False)


@pytest.mark.parametrize("packer, nest, footprint", [
    ("shelf", False, False), ("rectangles", True, False), ("polygons", True, True),
    (Packer.SHELF, False, False), (Packer.POLYGONS, True, True),
])
def test_modes_map_to_engine_switches(packer, nest, footprint):
    resolved, _ = resolve(packer)
    assert (resolved.nest, resolved.mesh_footprint, resolved.padding) == (nest, footprint, 2)


@pytest.mark.parametrize("packer", ["grid", "rectangles_in_place", "polygons_in_place", Packer.GRID])
def test_future_modes_are_not_implemented(packer):
    with pytest.raises(NotImplementedError, match=str(Packer(packer).value)):
        resolve(packer)


def test_unknown_packer_lists_the_valid_names():
    with pytest.raises(ValueError, match="shelf"):
        resolve("nest")


@pytest.mark.parametrize("packer", [3, None, b"shelf"])
def test_packer_of_wrong_type(packer):
    with pytest.raises(TypeError):
        resolve(packer)


@pytest.mark.parametrize("options", [{"padding": 2}, 2, "shelf"])
def test_options_of_wrong_type(options):
    with pytest.raises(TypeError):
        resolve_packer("shelf", options, mesh_ready=True)


def test_none_options_are_the_defaults():
    assert resolve_packer("shelf", None, mesh_ready=False) == resolve("shelf", mesh_ready=False)


@pytest.mark.parametrize("padding, error", [(0, ValueError), (257, ValueError), (-1, ValueError),
                                            (True, TypeError), (2.5, TypeError), ("3", TypeError)])
def test_bad_padding(padding, error):
    for packer in ("shelf", "rectangles", "polygons"):
        with pytest.raises(error):
            resolve(packer, padding=padding)


@pytest.mark.parametrize("packer", ["shelf", "rectangles", "polygons"])
@pytest.mark.parametrize("padding", [1, 2, 256])
def test_padding_range_ends(packer, padding):
    assert resolve(packer, padding=padding)[0].padding == padding


def test_validation_order():
    with pytest.raises(NotImplementedError):
        resolve_packer("grid", {"padding": 2}, mesh_ready=True)
    with pytest.raises(ValueError, match="padding"):
        resolve("polygons", padding=0, mesh_aware=False)


def test_packer_names_are_case_sensitive():
    with pytest.raises(ValueError):
        resolve("SHELF")


def test_numpy_integer_padding_resolves_to_plain_int():
    resolved, _ = resolve("shelf", padding=np.int64(3))
    assert resolved.padding == 3 and type(resolved.padding) is int
    assert resolved == resolve("shelf", padding=3)[0]


@pytest.mark.parametrize("mesh_aware", ["yes", 1, 0])
def test_mesh_aware_of_wrong_type(mesh_aware):
    with pytest.raises(TypeError):
        resolve("shelf", mesh_aware=mesh_aware)


def test_options_do_not_validate_themselves():
    PackerOptions(padding=0, mesh_aware="nope")  # only resolve_packer raises


def test_resolved_pack_is_hashable_and_equal_by_value():
    assert {ResolvedPack(False, 2, True, False)} == {resolve("shelf")[0]}


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
