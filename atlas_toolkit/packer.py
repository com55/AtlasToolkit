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


_NOT_IMPLEMENTED = frozenset({Packer.GRID})
_IN_PLACE_BASE = {
    Packer.RECTANGLES_IN_PLACE: Packer.RECTANGLES,
    Packer.POLYGONS_IN_PLACE: Packer.POLYGONS,
}


@dataclass(frozen=True, slots=True, kw_only=True)
class PackerOptions:
    """`padding`: pixels between packed images, the same on X and Y, not at
    the page edges (Spine's Padding X/Y); None means 2.

    `mesh_aware`: cut each region's pixels outside its mesh hull before
    packing. None is auto: on when the atlas has usable mesh data.
    "polygons" always uses it.

    `repack`: "auto" (None) patches a page whose only edits are additions
    and renames -- the new regions go into its free space and nothing else
    moves; "always" repacks every edited page.

    Validated by build() / save(), not here."""

    padding: int | None = None
    mesh_aware: bool | None = None
    repack: str | None = None


@dataclass(frozen=True, slots=True)
class ResolvedPack:
    """The values that reach the engine; also the build cache key.

    `in_place`: pages whose edits fit keep their layout (spec 2026-11-05)."""

    nest: bool
    padding: int
    mesh_aware: bool
    mesh_footprint: bool
    patch_adds: bool
    in_place: bool = False


def _type_name(value: object) -> str:
    kind = type(value)
    return kind.__qualname__ if kind.__module__ == "builtins" else f"{kind.__module__}.{kind.__qualname__}"


def _mode(value: object) -> Packer:
    if isinstance(value, Packer):
        return value
    if not isinstance(value, str):
        raise TypeError(f"packer must be a Packer or str, not {_type_name(value)}")
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
        raise TypeError(f"padding must be an integer, not {_type_name(value)}") from None
    if not 1 <= number <= MAX_PADDING:
        raise ValueError(f"padding must be between 1 and {MAX_PADDING}, got {number}")
    return number


_REPACK_MODES = ("auto", "always")


def _patch_adds(value: object) -> bool:
    if value is None:
        return True
    if not isinstance(value, str):
        raise TypeError(f"repack must be 'auto', 'always' or None, not {_type_name(value)}")
    if value not in _REPACK_MODES:
        raise ValueError(f"repack must be 'auto' or 'always', got {value!r}")
    return value == "auto"


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
        raise TypeError(f"packer_options must be a PackerOptions, not {_type_name(options)}")
    padding = _padding(options.padding)
    patch_adds = _patch_adds(options.repack)
    in_place = mode in _IN_PLACE_BASE
    base = _IN_PLACE_BASE.get(mode, mode)
    if in_place and options.repack == "always":
        raise ValueError(f"packer {mode.value!r} keeps the layout; use packer={base.value!r} to always repack")
    mesh_aware = options.mesh_aware
    if mesh_aware is not None and not isinstance(mesh_aware, bool):
        raise TypeError(f"mesh_aware must be True, False or None, not {_type_name(mesh_aware)}")
    if base is Packer.POLYGONS:
        if mesh_aware is False:
            raise ValueError(f"packer {mode.value!r} always uses the mesh; mesh_aware cannot be False")
        return ResolvedPack(
            nest=True, padding=padding, mesh_aware=True, mesh_footprint=True,
            patch_adds=patch_adds, in_place=in_place,
        ), True
    resolved = ResolvedPack(
        nest=base is Packer.RECTANGLES,
        padding=padding,
        mesh_aware=mesh_ready if mesh_aware is None else mesh_aware,
        mesh_footprint=False,
        patch_adds=patch_adds,
        in_place=in_place,
    )
    return resolved, mesh_aware is True
