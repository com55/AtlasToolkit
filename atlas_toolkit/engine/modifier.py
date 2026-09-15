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

from dataclasses import dataclass
from typing import Optional

from atlas_toolkit.domain.region_ops import round_half_even


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
