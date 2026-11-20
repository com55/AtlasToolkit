# SPDX-FileCopyrightText: 2026 com55
# SPDX-License-Identifier: Apache-2.0
"""Pack settings for SpineAtlas.build / save.

PackOptions is everything the caller chooses about a pack; resolve_packer
validates it and resolves it to the switches the session already has
(ResolvedPack), which doubles as the build cache key (spec 2026-11-18)."""

from __future__ import annotations

import operator
from dataclasses import dataclass
from enum import StrEnum

DEFAULT_PADDING = 2  # Spine Texture Packer's default
MAX_PADDING = 256


class Packer(StrEnum):
    AUTO = "auto"
    SHELF = "shelf"
    RECTANGLES = "rectangles"
    POLYGONS = "polygons"


@dataclass(frozen=True, slots=True, kw_only=True)
class PackOptions:
    """One pack's settings.

    `packer`: the packing algorithm. "auto" picks polygons when the atlas
    has usable mesh data, otherwise rectangles; "shelf" (one row per image,
    no nesting), "rectangles" (nest solid rects), "polygons" (nest mesh
    silhouettes; needs mesh data).

    `padding`: pixels between packed images, the same on X and Y, not at
    the page edges (Spine's Padding X/Y).

    `mesh_aware`: cut each region's pixels outside its mesh hull before
    packing. None is auto: on when the atlas has usable mesh data.
    "polygons" always uses it.

    `try_keep_layout`: an edited page first tries to keep its layout --
    removed regions leave empty space, a replacement is drawn into its old
    slot when it fits without touching a neighbour's pixels, additions go
    into free space and may grow the page right/down. A page that cannot
    (or that holds a shared-image composite replace) is repacked with the
    packer. False repacks every edited page.

    `bleed`: Spine's packer Bleed -- on every page the pack writes that is
    not pma, transparent pixels within 2 px of an image take its colour
    (alpha stays 0), so linear filtering draws no dark edges. None is auto:
    on when the pack splits composites, where cut pieces meet edge to edge.

    `share_composite_image`: affects replace_composite edits only. False
    cuts the image into one piece per region, the inverse of
    extract_composite(force_resize=True); True makes every region the whole
    image. A page holding such a replace is repacked rather than kept.

    Validated by build() / save(), not here."""

    packer: Packer | str = Packer.AUTO
    padding: int = DEFAULT_PADDING
    mesh_aware: bool | None = None
    try_keep_layout: bool = True
    bleed: bool | None = None
    share_composite_image: bool = False


@dataclass(frozen=True, slots=True)
class ResolvedPack:
    """The values that reach the engine; also the build cache key.

    Every field is fully resolved, so two settings that mean the same thing
    (`auto` and the packer it picks, a bleed left auto) are one entry."""

    nest: bool
    padding: int
    mesh_aware: bool
    mesh_footprint: bool
    keep_layout: bool
    split: bool
    bleed: bool


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


def _flag(name: str, value: object, *, none_means_auto: bool) -> bool | None:
    if value is None and none_means_auto:
        return None
    if not isinstance(value, bool):
        expected = "True, False or None" if none_means_auto else "True or False"
        raise TypeError(f"{name} must be {expected}, not {_type_name(value)}")
    return value


def _auto_bleed(split: bool) -> bool:
    """PackOptions(bleed=None): bleed when the pack splits composites,
    where cut pieces meet edge to edge."""
    return split


def resolve_packer(options: object, *, mesh_ready: bool) -> tuple[ResolvedPack, bool]:
    """Validate the pack settings and resolve them. Returns (resolved,
    needs_mesh); the caller raises SkeletonUnavailable when needs_mesh and
    the atlas has no usable mesh. "auto" never needs one: it resolves around
    a missing mesh, so only an explicit `mesh_aware=True` (or the "polygons"
    packer) reports needs_mesh under it."""
    if options is None:
        options = PackOptions()
    elif not isinstance(options, PackOptions):
        raise TypeError(f"options must be a PackOptions, not {_type_name(options)}")
    mode = _mode(options.packer)
    padding = _padding(options.padding)
    mesh_aware = _flag("mesh_aware", options.mesh_aware, none_means_auto=True)
    try_keep_layout = _flag("try_keep_layout", options.try_keep_layout, none_means_auto=False)
    bleed = _flag("bleed", options.bleed, none_means_auto=True)
    share_image = _flag("share_composite_image", options.share_composite_image, none_means_auto=False)
    auto = mode is Packer.AUTO
    if auto:
        # TODO: size-aware auto comes later: Polygons > Rectangles > Shelf by region count and each region's size (benchmark first; see spec 2026-11-18 "Later").
        mode = Packer.POLYGONS if mesh_ready and mesh_aware is not False else Packer.RECTANGLES
    if mode is Packer.POLYGONS:
        if mesh_aware is False:
            raise ValueError("packer 'polygons' always uses the mesh; mesh_aware cannot be False")
        resolved = ResolvedPack(
            nest=True, padding=padding, mesh_aware=True, mesh_footprint=True,
            keep_layout=try_keep_layout, split=not share_image,
            bleed=_auto_bleed(not share_image) if bleed is None else bleed,
        )
        return resolved, not auto
    resolved = ResolvedPack(
        nest=mode is Packer.RECTANGLES,
        padding=padding,
        mesh_aware=mesh_ready if mesh_aware is None else mesh_aware,
        mesh_footprint=False,
        keep_layout=try_keep_layout,
        split=not share_image,
        bleed=_auto_bleed(not share_image) if bleed is None else bleed,
    )
    return resolved, mesh_aware is True
