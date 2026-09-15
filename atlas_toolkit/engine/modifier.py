"""engine/modifier.py -- port of www/js/atlas-modifier.js's
production-live surface (architecture spec §1/§2, Phase 3 "modifier"
stage).

Ported: the pure mod-canvas decision functions, the pure mesh/identity
helpers spec §2 homes here for later dependency injection into
domain/packing.py, the declared-vs-real page-size reconciliation
(_scaleAtlasText), and AtlasModifier's page-scoped construction +
_prepareModImage.

Deliberately NOT ported:
- mergeModImage / findBestPlacement / repack(mergedCanvas, text) /
  repackMultiPage: no production caller since repack became always-on --
  dead code, not carried over.
- parseAtlas / updateAtlasText / _parseScoped: convenience re-parses of
  document.py's model (spec §2 "do not port"); this module works on
  AtlasDocument directly.
- _packAndEmit / _shelfPack / _canvasHash / repackWithModdedSprites /
  repackWithEffectiveModel / rebuildAtlasText / _rotateSpriteForPack: the
  next ("repack/smart packing") stage.
- maskRawSprite / _maskModdedSprites: the "mesh" stage.

JS truthiness, binding for every function here: "missing" offsets, mesh
geometry, or region means `is None`, never Python falsiness -- an empty JS
array/object is truthy, its Python equivalent is falsy."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Optional

from atlas_toolkit.domain.region_ops import round_half_even
from atlas_toolkit.raster.base import ImageHandle


def canvas_size_match(
    mod_w: int, mod_h: int, canvas_w: int, canvas_h: int, tolerance: float = 0.02,
) -> bool:
    """True when mod dimensions match a canvas within rounding tolerance:
    max(2, round_half_even(canvas_dim * tolerance)) pixels per axis.
    Port of canvasSizeMatch."""
    if canvas_w <= 0 or canvas_h <= 0:
        return False
    dw = abs(mod_w - canvas_w)
    dh = abs(mod_h - canvas_h)
    return (
        dw <= max(2, round_half_even(canvas_w * tolerance))
        and dh <= max(2, round_half_even(canvas_h * tolerance))
    )


def selected_share_canvas(selected_offsets: list[Optional[list[int]]]) -> bool:
    """Strict shared-canvas check: False if ANY selected region lacks
    offsets; otherwise True iff every region declares the same
    (orig_w, orig_h) and more than one region is selected. Differs from the
    loose `shared_canvas` inside resolve_mod_canvas, which only considers
    regions that have offsets. Port of selectedShareCanvas."""
    sizes: set[tuple[int, int]] = set()
    for offsets in selected_offsets:
        if offsets is None:
            return False
        sizes.add((offsets[2], offsets[3]))
    return len(sizes) == 1 and len(selected_offsets) > 1


def pick_anchor_offsets(offsets_with_values: list[list[int]]) -> list[int]:
    """[off_x, off_y] of the offsets minimising the tuple
    (off_x + off_y, off_x, off_y); the first of equal minima wins. Requires
    a non-empty list. Port of pickAnchorOffsets."""
    anchor = offsets_with_values[0]
    for offsets in offsets_with_values:
        candidate_sum = offsets[0] + offsets[1]
        anchor_sum = anchor[0] + anchor[1]
        if candidate_sum < anchor_sum or (
            candidate_sum == anchor_sum
            and (offsets[0] < anchor[0] or (offsets[0] == anchor[0] and offsets[1] < anchor[1]))
        ):
            anchor = offsets
    return [anchor[0], anchor[1]]


@dataclass(frozen=True)
class ModCanvas:
    """resolve_mod_canvas's result -- the JS object's fields, snake_cased."""
    orig_canvas_w: int
    orig_canvas_h: int
    base_orig_w: int
    base_orig_h: int
    off_x: int
    off_y: int
    is_full_canvas: bool


def resolve_mod_canvas(
    selected_offsets: list[Optional[list[int]]], mod_w: int, mod_h: int,
) -> ModCanvas:
    """Resolve the logical canvas size + padding anchor for a mod image.
    Port of resolveModCanvas, same step order:
      1. canvas size from the FIRST selected region with offsets (JS's
         deterministic deviation from Python's hash-order pick); anchor
         from pick_anchor_offsets across all of them
      2. proportional-scale detection (ratios within 0.05 of each other and
         not within (0.95, 1.05))
      3. is_full_canvas = loose shared canvas or canvas_size_match on the
         post-scale size
      4. full canvas -> canvas becomes the mod size and the anchor zeroes
    `selected_offsets` has one entry per selected region, in selection
    order; None marks a region without offsets."""
    with_offsets = [offsets for offsets in selected_offsets if offsets is not None]
    num_selected = len(selected_offsets)

    if with_offsets:
        base_orig_w = with_offsets[0][2]
        base_orig_h = with_offsets[0][3]
        off_x, off_y = pick_anchor_offsets(with_offsets)
        orig_canvas_w, orig_canvas_h = base_orig_w, base_orig_h
    else:
        base_orig_w, base_orig_h = mod_w, mod_h
        off_x, off_y = 0, 0
        orig_canvas_w, orig_canvas_h = mod_w, mod_h

    size_set = {(offsets[2], offsets[3]) for offsets in with_offsets}
    shared_canvas = len(size_set) == 1 and num_selected > 1

    if orig_canvas_w > 0 and orig_canvas_h > 0 and (mod_w != orig_canvas_w or mod_h != orig_canvas_h):
        ratio_w = mod_w / orig_canvas_w
        ratio_h = mod_h / orig_canvas_h
        if abs(ratio_w - ratio_h) < 0.05 and not (0.95 < ratio_w < 1.05):
            scale = (ratio_w + ratio_h) / 2
            orig_canvas_w = round_half_even(orig_canvas_w * scale)
            orig_canvas_h = round_half_even(orig_canvas_h * scale)
        else:
            orig_canvas_w, orig_canvas_h = mod_w, mod_h

    is_full_canvas = shared_canvas or canvas_size_match(mod_w, mod_h, orig_canvas_w, orig_canvas_h)
    if is_full_canvas:
        orig_canvas_w, orig_canvas_h = mod_w, mod_h
        off_x, off_y = 0, 0

    return ModCanvas(orig_canvas_w, orig_canvas_h, base_orig_w, base_orig_h, off_x, off_y, is_full_canvas)


def repack_offsets_for_region(
    name: str,
    full_canvas_regions: Optional[set[str]],
    pristine_offsets: Optional[list[int]],
    packed_w: int,
    packed_h: int,
) -> Optional[list[int]]:
    """Offsets emitted for a region in a repacked page: a full-canvas
    region resets to [0, 0, packed_w, packed_h] (the serializer then omits
    it); every other region keeps its pristine offsets verbatim. Port of
    repackOffsetsForRegion."""
    if full_canvas_regions is not None and name in full_canvas_regions:
        return [0, 0, packed_w, packed_h]
    return pristine_offsets


def mask_crop_rect_for_offsets(offsets: list[int], sprite_w: int, sprite_h: int) -> dict[str, int]:
    """The sub-rect, within a full-size (orig_w x orig_h) mesh mask, that a
    raw trimmed (sprite_w x sprite_h) crop corresponds to, given the
    region's offsets [off_x, off_y, orig_w, orig_h]. Exact inverse of
    extraction's paste position (paste_y = orig_h - off_y - h). Returns a
    dict shaped like the JS object ({x, y, w, h}) because the repack stage
    injects this function into domain/packing.py, which reads those keys.
    Port of maskCropRectForOffsets."""
    off_x, off_y, _orig_w, orig_h = offsets
    return {"x": off_x, "y": orig_h - off_y - sprite_h, "w": sprite_w, "h": sprite_h}


def _triangle_signed_area(uvs: list[float], ia: int, ib: int, ic: int) -> float:
    """Signed area (UV space) of one triangle, given the uvs-array indices
    of its three u components: positive = CCW in u/v, negative = reversed.
    Port of _triangleSignedArea."""
    ax, ay = uvs[ia], uvs[ia + 1]
    bx, by = uvs[ib], uvs[ib + 1]
    cx, cy = uvs[ic], uvs[ic + 1]
    return (bx - ax) * (cy - ay) - (cx - ax) * (by - ay)


def combine_mesh_geometry(
    names: list[str], mesh_lookup_fn: Callable[[str], Optional[dict]],
) -> Optional[dict]:
    """Union the mesh geometry of every name into one
    {"uvs": [...], "triangles": [...]}, or None if ANY name lacks mesh
    data (the whole group bails -- masking a mesh-less region to a
    neighbour's silhouette would silently clip it) or if `names` is empty.
    Each triangle's winding is normalized to positive signed area before
    appending, so a nonzero-winding fill unions overlapping,
    independently-authored meshes instead of cancelling them. Never mutates
    the looked-up geometry. Port of _combineMeshGeometry."""
    uvs: list[float] = []
    triangles: list[int] = []
    for name in names:
        geometry = mesh_lookup_fn(name)
        if geometry is None:
            return None
        base = len(uvs) // 2
        geometry_uvs = geometry["uvs"]
        geometry_triangles = geometry["triangles"]
        uvs.extend(geometry_uvs)
        for i in range(0, len(geometry_triangles), 3):
            ia = geometry_triangles[i]
            ib = geometry_triangles[i + 1]
            ic = geometry_triangles[i + 2]
            if _triangle_signed_area(geometry_uvs, ia * 2, ib * 2, ic * 2) < 0:
                ib, ic = ic, ib
            triangles.extend((ia + base, ib + base, ic + base))
    return {"uvs": uvs, "triangles": triangles} if names else None


def group_names_by_sprite_identity(
    sprites: dict[str, ImageHandle], modded_sprites: Optional[dict[str, ImageHandle]],
) -> list[list[str]]:
    """Group the names in `modded_sprites` (skipping any absent from
    `sprites`) by which ones hold the SAME image handle, in first-seen
    order; a name with no counterpart is its own group of one. Port of
    _groupNamesBySpriteIdentity.

    Identity translation: JS groups by canvas OBJECT identity (a
    shared-canvas mod batch stores the literal same canvas under every one
    of its names). Here that is ImageHandle VALUE equality, which is
    equivalent because a RasterBackend never hands out one handle value for
    two live images. INVARIANT carried over from the JS comment: two
    logically independent mod registrations must never produce the same
    handle, or their regions get wrongly unioned into one mask group.
    AtlasModifier.prepare_mod_image upholds this by always returning a
    fresh handle."""
    groups: dict[ImageHandle, list[str]] = {}
    for name, handle in (modded_sprites or {}).items():
        if name not in sprites:
            continue
        groups.setdefault(handle, []).append(name)
    return list(groups.values())
