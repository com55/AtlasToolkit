"""Pack modes for SpineAtlas.build / save.

A pack mode is what the caller chooses; the algorithm behind it is internal
and may change. resolve_packer turns a mode and its options into the
switches the session already has (approach A of spec 2026-10-22)."""

from __future__ import annotations

import operator
from dataclasses import dataclass
from enum import StrEnum

DEFAULT_PADDING = 2  # Spine Texture Packer's default
MAX_PADDING = 256


class Packer(StrEnum):
    GRID = "grid"
    SHELF = "shelf"
    RECTANGLES = "rectangles"
    POLYGONS = "polygons"
    RECTANGLES_IN_PLACE = "rectangles_in_place"
    POLYGONS_IN_PLACE = "polygons_in_place"


_NOT_IMPLEMENTED = frozenset({Packer.GRID, Packer.RECTANGLES_IN_PLACE, Packer.POLYGONS_IN_PLACE})


@dataclass(frozen=True, slots=True, kw_only=True)
class PackerOptions:
    """`padding`: pixels between packed images, the same on X and Y, not at
    the page edges (Spine's Padding X/Y); None means 2.

    `mesh_aware`: cut each region's pixels outside its mesh hull before
    packing. None is auto: on when the atlas has usable mesh data.
    "polygons" always uses it.

    Validated by build() / save(), not here."""

    padding: int | None = None
    mesh_aware: bool | None = None


@dataclass(frozen=True, slots=True)
class ResolvedPack:
    """The values that reach the engine; also the build cache key."""

    nest: bool
    padding: int
    mesh_aware: bool
    mesh_footprint: bool


def _mode(value: object) -> Packer:
    if isinstance(value, Packer):
        return value
    if not isinstance(value, str):
        raise TypeError(f"packer must be a Packer or str, not {type(value).__name__}")
    try:
        return Packer(value)
    except ValueError:
        names = ", ".join(mode.value for mode in Packer)
        raise ValueError(f"unknown packer {value!r}; expected one of: {names}") from None


def _padding(value: object) -> int:
    if value is None:
        return DEFAULT_PADDING
    if isinstance(value, bool):
        raise TypeError("padding must be an integer, not bool")
    try:
        number = int(operator.index(value))
    except TypeError:
        raise TypeError(f"padding must be an integer, not {type(value).__name__}") from None
    if not 1 <= number <= MAX_PADDING:
        raise ValueError(f"padding must be between 1 and {MAX_PADDING}, got {number}")
    return number


def resolve_packer(packer: object, options: object, *, mesh_ready: bool) -> tuple[ResolvedPack, bool]:
    """Validate the pack settings and resolve them. Returns (resolved,
    needs_mesh); the caller raises SkeletonUnavailable when needs_mesh and
    the atlas has no usable mesh."""
    mode = _mode(packer)
    if mode in _NOT_IMPLEMENTED:
        raise NotImplementedError(f"packer {mode.value!r} is not implemented yet")
    if options is None:
        options = PackerOptions()
    elif not isinstance(options, PackerOptions):
        raise TypeError(f"packer_options must be a PackerOptions, not {type(options).__name__}")
    padding = _padding(options.padding)
    mesh_aware = options.mesh_aware
    if mesh_aware is not None and not isinstance(mesh_aware, bool):
        raise TypeError(f"mesh_aware must be True, False or None, not {type(mesh_aware).__name__}")
    if mode is Packer.POLYGONS:
        if mesh_aware is False:
            raise ValueError("packer 'polygons' always uses the mesh; mesh_aware cannot be False")
        return ResolvedPack(nest=True, padding=padding, mesh_aware=True, mesh_footprint=True), True
    resolved = ResolvedPack(
        nest=mode is Packer.RECTANGLES,
        padding=padding,
        mesh_aware=mesh_ready if mesh_aware is None else mesh_aware,
        mesh_footprint=False,
    )
    return resolved, mesh_aware is True
