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
- maskRawSprite / _maskModdedSprites: the "mesh" stage (they need the
  RasterBackend mesh primitives Stage 3 stubbed).
- rebuild()'s multi-page fan-out: the session stage -- deciding which
  pages to rewrite needs modBatches/moddedSprites/_fullCanvasRegions,
  which are atlas-session.js state, and the maintainer has the multi-page
  repack slated for redesign (pool-and-rebalance across pages).

JS truthiness, binding for every function here: "missing" offsets, mesh
geometry, or region means `is None`, never Python falsiness -- an empty JS
array/object is truthy, its Python equivalent is falsy."""
from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Optional

from atlas_toolkit.document import AtlasDocument, Region
from atlas_toolkit.domain.packing import (
    footprint_for_canonical,
    nest_pack,
    shelf_pack,
)
from atlas_toolkit.domain.region_ops import round_half_even
from atlas_toolkit.raster.base import ImageHandle, RasterBackend


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


def mask_raw_sprite(
    raster: RasterBackend, sprite: ImageHandle, mesh_geometry: dict, region: dict,
) -> None:
    """Masks a freshly-extracted pristine sprite IN PLACE to its mesh
    silhouette. Mesh UVs are normalized to the region's raw crop size when
    it has no offsets, or to the full original (orig_w, orig_h) when it
    does -- the crop-back rect recovers just the sub-rect this raw crop
    occupies within that full-size space. Port of maskRawSprite."""
    offsets = region["offsets"]
    if offsets is None:
        mask = raster.rasterize_mesh_mask(
            mesh_geometry["uvs"], mesh_geometry["triangles"], *raster.size(sprite),
        )
        raster.composite_destination_in(sprite, mask)
        return
    orig_w, orig_h = offsets[2], offsets[3]
    rect = mask_crop_rect_for_offsets(offsets, *raster.size(sprite))
    full_mask = raster.rasterize_mesh_mask(mesh_geometry["uvs"], mesh_geometry["triangles"], orig_w, orig_h)
    raster.composite_destination_in(sprite, full_mask, (rect["x"], rect["y"], rect["w"], rect["h"]))


def _mask_modded_sprites(
    raster: RasterBackend,
    sprites: dict[str, ImageHandle],
    modded_sprites: Optional[dict[str, ImageHandle]],
    mesh_lookup_fn: Optional[Callable[[str], Optional[dict]]],
) -> None:
    """Masks the overlay entries of `sprites` (mutating the dict, never
    `modded_sprites`' own values or handles) to their mesh silhouette.
    Each group from group_names_by_sprite_identity gets ONE combined mask
    applied to ONE fresh clone, reassigned to every name in the group --
    preserving the "identical handle across the group" property dedup
    relies on, while never touching the caller's own handle. Port of
    _maskModdedSprites."""
    if mesh_lookup_fn is None:
        return
    for names in group_names_by_sprite_identity(sprites, modded_sprites):
        combined = combine_mesh_geometry(names, mesh_lookup_fn)
        if combined is None:
            continue  # no mesh data for anyone in this group -- leave as-is
        masked = raster.clone(sprites[names[0]])
        mask = raster.rasterize_mesh_mask(combined["uvs"], combined["triangles"], *raster.size(masked))
        raster.composite_destination_in(masked, mask)
        for name in names:
            sprites[name] = masked


def scale_document_to_page_image(
    document: AtlasDocument, target_page: str, real_w: int, real_h: int,
) -> AtlasDocument:
    """Reconcile `target_page`'s declared `size:` with its real loaded
    image size (real_w x real_h). When they differ, every region on that
    page gets x/y/w/h and offsets rescaled by (real_w/declared_w,
    real_h/declared_h) with banker's rounding, and that page's size becomes
    [real_w, real_h]; other pages are untouched. Port of
    AtlasModifier._scaleAtlasText, taking a pre-parsed document instead of
    text (spec §1 rev 7). Spec §2 names `rebuild()` as this step's home;
    `rebuild()` itself belongs to the repack stage and will call this
    function (via AtlasModifier) rather than contain the logic -- it is a
    standalone function now because AtlasModifier's constructor already
    needs it, exactly as JS runs _scaleAtlasText inside its constructor.

    Returns `document` itself (the same object, unmodified) when no
    rescale applies: target page absent, declared size has a zero
    dimension, or sizes already match. Otherwise returns a new document;
    the input is never mutated.

    JS round-trip fidelity: the JS scaled branch serializes the updated
    text and AtlasModifier re-parses it, so two canonicalizing-serializer
    effects reach every region on the target page -- offsets equal to the
    default (0, 0, w, h) come back as null, and a rotate outside
    {90, 180, 270} comes back as 0 (confirmed by Node probe, 2026-09-15).
    Both are reproduced explicitly below rather than by re-parsing text.
    The no-op branches keep explicit default offsets, as JS does by
    returning its original text. (The JS round-trip also nulls default
    offsets on OTHER pages; AtlasModifier scopes everything to the target
    page, so that difference is not observable through this module.)"""
    target_pages = [page for page in document.pages if page.filename == target_page]
    if not target_pages:
        return document
    declared_w, declared_h = target_pages[0].size
    if declared_w == 0 or declared_h == 0:
        return document
    if real_w == declared_w and real_h == declared_h:
        return document

    sx = real_w / declared_w
    sy = real_h / declared_h
    updated: dict[str, tuple[list[int], Optional[list[int]], int]] = {}
    for page in target_pages:
        for region in page.regions:
            bounds = [
                round_half_even(region.x * sx),
                round_half_even(region.y * sy),
                round_half_even(region.w * sx),
                round_half_even(region.h * sy),
            ]
            offsets = None
            if region.offsets is not None:
                off_x, off_y, orig_w, orig_h = region.offsets
                offsets = [
                    round_half_even(off_x * sx),
                    round_half_even(off_y * sy),
                    round_half_even(orig_w * sx),
                    round_half_even(orig_h * sy),
                ]
                if offsets[0] == 0 and offsets[1] == 0 and offsets[2] == bounds[2] and offsets[3] == bounds[3]:
                    offsets = None
            rotate = region.rotate if region.rotate in (90, 180, 270) else 0
            updated[region.name] = (bounds, offsets, rotate)

    scaled = document.with_updates(updated)
    for page in scaled.pages:
        if page.filename == target_page:
            page.size = [real_w, real_h]
    return scaled


_MAX_ARRAY_INDEX = 2 ** 32 - 1


def _is_array_index(name: str) -> bool:
    """True for a canonical JS array-index key: a non-negative integer in
    [0, 2**32 - 1) written without a sign, leading zeros, or surrounding
    whitespace. Verified against real Object.keys: "0", "2", "10" and
    "4294967294" are indices; "01", "00", "007", "-1", "1.5",
    "4294967295", "4294967296", " 2", "+2" and "2e3" are not.

    The `isascii()` guard is load-bearing, not defensive padding.
    str.isdigit() is True for non-ASCII digits, and they split two ways:
    int("٢") is 2 and int("߅") is 5 (so the round-trip rejects them
    correctly), but int("²") raises ValueError -- an uncaught crash on a
    region name that JS would simply treat as a non-index string. A JS
    array index is ASCII-digits-only, so checking that first is both
    faithful and total."""
    return (
        name.isascii()
        and name.isdigit()
        and str(int(name)) == name
        and int(name) < _MAX_ARRAY_INDEX
    )


def js_object_key_order(names: list[str]) -> list[str]:
    """Reorder `names` the way a JS object orders its keys: canonical
    array-index keys first, ascending numerically, then every other key in
    insertion order.

    This is load-bearing, not cosmetic. atlas-modifier.js's parseAtlas
    builds its region list as Object.keys(processor.regions)
    (atlas-modifier.js:26), so a region literally named "2" is packed
    BEFORE one named "10" even when the atlas text lists 10 first --
    confirmed against real JS, which returns ["2", "10", "a"] for text
    order 10, 2, a and places "2" at (0, 0). AtlasDocument.parse preserves
    text order, so without this the packer diverges in both placement and
    emitted region order.

    CARRY-FORWARD to the session stage: domain/effective_model.py's
    derive_effective_model reads its region order straight off its
    argument's dict keys. That is free in JS (an object's keys are always
    canonically ordered) but not in Python, so the session stage must pass
    it a mapping already ordered by this function, or the structural
    (Add/Remove/Rename) repack path diverges the same way."""
    indices = sorted((name for name in names if _is_array_index(name)), key=int)
    rest = [name for name in names if not _is_array_index(name)]
    return [*indices, *rest]


def region_meta(region: Region) -> dict:
    """A Region rendered in the JS `_parseScoped` region-object shape --
    camelCase keys, because this is the shape domain/effective_model.py
    already produces and consumes (its own docstring calls the values
    "JS-shaped"), so the structural repack path needs no translation.
    `_pack_and_emit` reads regions in exactly this shape on both paths."""
    return {
        "name": region.name,
        "atlasName": region.atlas_name or region.name,
        "page": region.page_filename,
        "index": region.index,
        "bounds": [region.x, region.y, region.w, region.h],
        "offsets": region.offsets,
        "rotate": region.rotate,
        "split": region.split,
        "pad": region.pad,
        "extraPairs": [(key, list(values)) for key, values in region.extra_pairs],
    }


def canvas_hash(raster: RasterBackend, handle: ImageHandle) -> str:
    """SHA-256 of a sprite's raw RGBA bytes -- the dedup identity
    _packAndEmit groups by. Port of _canvasHash.

    Two parts of the JS are deliberately not ported. Its double
    getImageData "priming read" works around a documented Chromium
    GPU-readback bug that does not exist for Pillow (see
    raster/base.py's get_pixels docstring). Its djb2 fallback exists only
    for a missing crypto.subtle. Neither changes which sprites group
    together, which is the only property parity tests assert -- digest
    STRINGS are never compared across the two runtimes."""
    return hashlib.sha256(raster.get_pixels(handle)).hexdigest()


def extract_raw_sprite(raster: RasterBackend, image: ImageHandle, region: dict) -> ImageHandle:
    """Crop one region's raw sprite out of a page image, undoing any
    stored rotation. Port of AtlasModifier._extractRawSprite -- the single
    rotation seam, same as JS routing through cropAndRotate."""
    x, y, w, h = region["bounds"]
    return raster.crop_rotate(image, x, y, w, h, region["rotate"])


def rotate_sprite_for_pack(
    raster: RasterBackend, sprite: ImageHandle, degrees: int
) -> ImageHandle:
    """Rotate a sprite by a PACK degree (0/90/180/270) before pasting it
    onto the packed canvas. Port of _rotateSpriteForPack.

    THE DEGREE IS INVERTED ON PURPOSE, and getting this wrong is
    undetectable by dimensions alone (90 and 270 both swap w/h). JS's
    _rotateSpriteForPack(sprite, 90) calls _rotate90CCW, which is
    cropAndRotate(src, 0, 0, h, w, 270) -- a pack rotation of 90 is
    executed as the STORED-rotation value 270, and vice versa, because
    crop_rotate's parameter is "the stored atlas rotation to undo", not a
    rotation to apply. 180 is its own inverse, and any other value
    (including 0) returns the input handle unchanged -- never a clone, so
    the handle-identity invariant group_names_by_sprite_identity relies on
    is preserved."""
    if degrees == 90:
        return raster.rotate_image(sprite, 270)
    if degrees == 180:
        return raster.rotate_image(sprite, 180)
    if degrees == 270:
        return raster.rotate_image(sprite, 90)
    return sprite


@dataclass(frozen=True)
class PreparedMod:
    """prepare_mod_image's result. `image` is always a handle distinct
    from the input mod (see prepare_mod_image). Two distinct flags, never
    to be conflated:
    - shared_canvas_mod: several selected regions share this one image, so
      placement must never rotate it (would break their shared coordinate
      space); implies more than one selected region.
    - is_full_canvas: the image needs no offset/trim, however many regions
      are selected -- feeds the session's full-canvas-region set, so repack
      resets those regions' offsets instead of preserving stale ones."""
    image: ImageHandle
    mod_w: int
    mod_h: int
    shared_canvas_mod: bool
    is_full_canvas: bool


@dataclass(frozen=True)
class PackResult:
    """_pack_and_emit's result. `document` is a fresh single-page
    AtlasDocument -- never serialized text (spec §1 rev 7), so a caller
    that needs text calls .serialize() itself and one that needs the model
    pays no reparse. `region_bounds` maps every emitted region to
    [x, y, w, h, rotate], exposing the placement data directly: JS added
    it because recovering bounds by reparsing the serialized output
    silently lost identity on Rename."""
    image: ImageHandle
    document: AtlasDocument
    region_bounds: dict[str, list[int]]


class AtlasModifier:
    """Port of atlas-modifier.js's AtlasModifier, production-live surface
    only (see the module docstring). One instance per (document, page
    image) pair, the way atlas-session.js constructs one per page per
    operation.

    Deliberate departures from the JS constructor
    (atlasText, atlasFilename, baseImage, targetPage = null):
    - takes a pre-parsed AtlasDocument, never text (spec §1 rev 7);
    - no atlas_filename: JS stores it but never reads it;
    - target_page is required: every production construction passes a
      page filename, and JS's targetPage = null branch is production-dead;
    - raster: the RasterBackend that owns base_image and every handle this
      object creates.

    `regions` values may be the very Region objects of the caller's
    document (no-op scale) -- treat them as read-only."""

    def __init__(
        self, document: AtlasDocument, base_image: ImageHandle, raster: RasterBackend, target_page: str,
    ) -> None:
        self.raster = raster
        self.base_image = base_image
        self.target_page = target_page
        real_w, real_h = raster.size(base_image)
        self.document = scale_document_to_page_image(document, target_page, real_w, real_h)
        scoped = [
            region
            for page in self.document.pages
            if page.filename == target_page
            for region in page.regions
        ]
        self.region_names: list[str] = [region.name for region in scoped]
        self.regions: dict[str, Region] = {region.name: region for region in scoped}

    def _selected_offsets(self, selected_regions: list[str]) -> list[Optional[list[int]]]:
        """Per selected name, in selection order: its offsets, or None when
        the name is outside this modifier's page scope or has no offsets."""
        return [
            self.regions[name].offsets if name in self.regions else None
            for name in selected_regions
        ]

    def prepare_mod_image(self, mod_image: ImageHandle, selected_regions: list[str]) -> PreparedMod:
        """Resolve the selection's logical canvas and, unless the mod is
        full-canvas, pad it onto that canvas at the Spine offset position
        (left, orig_h - bottom - sprite_h), scaled from the base canvas to
        the resolved canvas. Resolves against this modifier's (scaled)
        regions -- the session runs it on a fresh modifier. Port of
        AtlasModifier._prepareModImage.

        Always returns a handle distinct from `mod_image`: a fresh padded
        image, or raster.clone(mod_image) when no padding is needed. JS
        gets that property from _toCanvas drawing each loaded Image into a
        fresh canvas; group_names_by_sprite_identity depends on it. The
        input handle is never modified or disposed."""
        mod_w, mod_h = self.raster.size(mod_image)
        selected_offsets = self._selected_offsets(selected_regions)
        canvas = resolve_mod_canvas(selected_offsets, mod_w, mod_h)

        needs_padding = not canvas.is_full_canvas and (
            mod_w != canvas.orig_canvas_w or mod_h != canvas.orig_canvas_h
        )
        if needs_padding:
            scale_x = canvas.orig_canvas_w / canvas.base_orig_w if canvas.base_orig_w > 0 else 1
            scale_y = canvas.orig_canvas_h / canvas.base_orig_h if canvas.base_orig_h > 0 else 1
            paste_x = round_half_even(canvas.off_x * scale_x)
            paste_y = canvas.orig_canvas_h - mod_h - round_half_even(canvas.off_y * scale_y)
            image = self.raster.create(canvas.orig_canvas_w, canvas.orig_canvas_h)
            self.raster.paste(image, mod_image, paste_x, paste_y)
            mod_w, mod_h = canvas.orig_canvas_w, canvas.orig_canvas_h
        else:
            image = self.raster.clone(mod_image)

        shared_canvas_mod = canvas.is_full_canvas and selected_share_canvas(selected_offsets)
        return PreparedMod(image, mod_w, mod_h, shared_canvas_mod, canvas.is_full_canvas)

    def _page_info(self) -> dict:
        """The target page's header fields in from_rebuild_args's input
        shape. `filter` is a two-element list on Page but a
        "Linear, Linear" string here, matching JS's _parseScoped.

        DELIBERATE DEVIATION on `pma` (maintainer's ruling): JS's
        _parseScoped forwards `pma` only when targetPage is falsy -- never
        in production -- so fromRebuildArgs gets undefined and every
        repacked page comes out `pma: false`. Verified by Node probe: a
        `pma: true` page loses the line entirely through
        repackWithModdedSprites. That is a JS bug, and this port does not
        reproduce it; the page's real value is carried through. Because of
        this, no parity case may use a `pma: true` page -- it would fail
        against the oracle for the right reason. A non-parity test pins the
        behaviour instead."""
        page = next(page for page in self.document.pages if page.filename == self.target_page)
        return {
            "page": page.filename,
            "format": page.format,
            "filter": f"{page.filter[0]}, {page.filter[1]}",
            "repeat": page.repeat,
            "pma": page.pma,
        }

    def _pack_and_emit(
        self,
        sprites: dict[str, ImageHandle],
        region_names: list[str],
        regions: dict[str, dict],
        full_canvas_regions: Optional[set[str]],
        nest_options: Optional[dict] = None,
        modded_sprites: Optional[dict[str, ImageHandle]] = None,
        added_sprites: Optional[dict[str, ImageHandle]] = None,
        mesh_lookup_fn: Optional[Callable[[str], Optional[dict]]] = None,
    ) -> PackResult:
        """Deduplicate by pixel hash, bin-pack, paste, and emit a
        single-page document from an already-built sprite map. Port of
        AtlasModifier._packAndEmit.

        `regions` values are in region_meta()'s JS shape (camelCase), the
        same shape domain/effective_model.py emits. `nest_options` is
        {"enabled": bool, "gap_distance": int} -- snake_case, unlike the
        JS options bag. A name in `region_names` with no sprite is dropped
        from the output entirely, as in JS.

        Departures from the JS signature: `page_info` is derived from this
        modifier's own target page rather than passed in (JS passes it
        only because _parseScoped had just produced it), and the result
        carries a document instead of atlas text.

        CALLER PRECONDITION -- `region_names` MUST ALREADY BE IN JS
        OBJECT-KEY ORDER. This function does not re-derive it. Real JS gets
        that ordering for free because its region list comes from
        Object.keys() (atlas-modifier.js:26), which puts canonical
        array-index keys first in ascending order; Python dicts and
        AtlasDocument.parse preserve text order instead. Pass names through
        js_object_key_order() first -- `_scoped_region_meta()` does exactly
        that. Skip it and a region literally named "2" packs after one named
        "10", silently, and every test that avoids integer-like names still
        passes."""
        # 1. Deduplicate by pixel hash (single-page repack keeps dedup ON).
        hash_to_canonical: dict[str, str] = {}
        canonical_map: dict[str, str] = {}
        for name in region_names:
            if name not in sprites:
                continue
            digest = canvas_hash(self.raster, sprites[name])
            if digest in hash_to_canonical:
                canonical_map[name] = hash_to_canonical[digest]
            else:
                hash_to_canonical[digest] = name
                canonical_map[name] = name
        unique_names = list(hash_to_canonical.values())

        # 2. Bin-pack the unique sprites. The Smart Packing toggle swaps
        #    the packer entirely; shelf_pack is never touched, so
        #    toggle-off output stays identical.
        using_nest = bool(nest_options and nest_options.get("enabled"))
        if using_nest:
            dedup_groups: dict[str, list[str]] = {}
            for name, canonical in canonical_map.items():
                dedup_groups.setdefault(canonical, []).append(name)
            items = []
            for name in unique_names:
                width, height = self.raster.size(sprites[name])
                items.append({
                    "name": name, "w": width, "h": height,
                    "footprint": footprint_for_canonical(dedup_groups[name], {
                        "sprites": sprites,
                        "raster": self.raster,
                        "modded_sprites": modded_sprites,
                        "added_sprites": added_sprites,
                        "regions": regions,
                        "mesh_lookup_fn": mesh_lookup_fn,
                        "combine_mesh_geometry": combine_mesh_geometry,
                        "group_names_by_sprite_identity": group_names_by_sprite_identity,
                        "mask_crop_rect_for_offsets": mask_crop_rect_for_offsets,
                    }),
                })
            packed = nest_pack(items, gap_distance=nest_options.get("gap_distance", 4))
        else:
            items = []
            for name in unique_names:
                width, height = self.raster.size(sprites[name])
                items.append({"name": name, "w": width, "h": height})
            packed = shelf_pack(items)

        canvas_w, canvas_h = packed["canvas_w"], packed["canvas_h"]
        placement_map = {p["name"]: p for p in packed["placements"]}

        def rotate_of(canonical: str) -> int:
            """Normalizes both packers' placement shape to one
            0/90/180/270 value -- shelf_pack's `rotated` boolean never
            reaches downstream code past this point."""
            placement = placement_map[canonical]
            if using_nest:
                return placement["rotate"]
            return 90 if placement["rotated"] else 0

        # 3. Paste sprites onto the new canvas.
        canvas = self.raster.create(canvas_w, canvas_h)
        for name in unique_names:
            placement = placement_map[name]
            rotated = rotate_sprite_for_pack(self.raster, sprites[name], rotate_of(name))
            self.raster.paste(canvas, rotated, placement["x"], placement["y"])

        # 4. Build region data. Offsets: full-canvas regions reset to
        #    default, everyone else preserves their pristine offsets
        #    verbatim. bounds always uses the sprite's own PRE-ROTATION
        #    dimensions, which is rotation-invariant by construction.
        region_data: dict[str, tuple] = {}
        region_bounds: dict[str, list[int]] = {}
        for name in region_names:
            if name not in canonical_map:
                continue
            canonical = canonical_map[name]
            placement = placement_map[canonical]
            width, height = self.raster.size(sprites[name])
            bounds = [placement["x"], placement["y"], width, height]
            meta = regions[name]
            offsets = repack_offsets_for_region(
                name, full_canvas_regions, meta["offsets"], width, height
            )
            region_data[name] = (
                bounds,
                # Copy rather than alias: from_rebuild_args stores the
                # offsets list BY REFERENCE (unlike split/pad/extraPairs,
                # which it copies), so without this the repacked document
                # would share a mutable list with its input. Values are
                # unchanged; only the aliasing hazard goes away.
                list(offsets) if offsets is not None else None,
                rotate_of(canonical),
                {
                    "atlasName": meta.get("atlasName") or meta.get("name"),
                    "index": meta.get("index"),
                    "split": meta.get("split"),
                    "pad": meta.get("pad"),
                    "extraPairs": meta.get("extraPairs"),
                },
            )
            region_bounds[name] = [*bounds, rotate_of(canonical)]

        document = AtlasDocument.from_rebuild_args(
            self._page_info(), [canvas_w, canvas_h], region_names, region_data
        )
        return PackResult(canvas, document, region_bounds)

    def _scoped_region_meta(self) -> tuple[list[str], dict[str, dict]]:
        """This page's region names in JS key order, with their metadata in
        region_meta()'s shape."""
        names = js_object_key_order(self.region_names)
        return names, {name: region_meta(self.regions[name]) for name in names}

    def repack_with_modded_sprites(
        self,
        modded_sprites: Optional[dict[str, ImageHandle]],
        full_canvas_regions: Optional[set[str]] = None,
        mesh_lookup_fn: Optional[Callable[[str], Optional[dict]]] = None,
        nest_options: Optional[dict] = None,
    ) -> PackResult:
        """Repack from this modifier's pristine base image, overlaid with
        modded sprites. Non-modded regions carry their pristine offsets
        into the output; regions in `full_canvas_regions` get default
        offsets -- the offsets-preserved-on-repack asymmetry that merge
        (which resets every touched region) cannot express. Port of
        repackWithModdedSprites.

        A modded sprite handle is used as-is, never cloned: JS's _toCanvas
        deliberately preserves canvas identity here, and
        group_names_by_sprite_identity groups by handle value.

        When `mesh_lookup_fn` is given, every pristine (non-modded) region
        with real mesh geometry gets masked via mask_raw_sprite before
        packing, and every modded-sprite identity group with real mesh
        geometry gets masked via _mask_modded_sprites -- mirroring
        atlas-modifier.js:845-862 exactly. Pass `mesh_lookup_fn=None` when
        mesh masking is not wanted."""
        region_names, regions = self._scoped_region_meta()
        sprites = {
            name: extract_raw_sprite(self.raster, self.base_image, regions[name])
            for name in region_names
        }
        if mesh_lookup_fn is not None:
            for name in region_names:
                if name in (modded_sprites or {}):
                    continue
                geom = mesh_lookup_fn(name)
                if geom is not None:
                    mask_raw_sprite(self.raster, sprites[name], geom, regions[name])
        for name, sprite in (modded_sprites or {}).items():
            if name in sprites:
                sprites[name] = sprite
        _mask_modded_sprites(self.raster, sprites, modded_sprites, mesh_lookup_fn)
        return self._pack_and_emit(
            sprites, region_names, regions, full_canvas_regions,
            nest_options, modded_sprites, None, mesh_lookup_fn,
        )

    def repack_with_effective_model(
        self,
        effective_region_names: list[str],
        effective_regions: dict[str, dict],
        added_sprites: Optional[dict[str, ImageHandle]],
        modded_sprites: Optional[dict[str, ImageHandle]],
        full_canvas_regions: Optional[set[str]] = None,
        mesh_lookup_fn: Optional[Callable[[str], Optional[dict]]] = None,
        nest_options: Optional[dict] = None,
    ) -> PackResult:
        """Repack from the effective (pristine union structural-batch)
        region model -- the Add/Remove/Rename path. Mirrors
        repack_with_modded_sprites's own extraction rather than making the
        caller pre-extract, so no caller needs to know about scaling,
        extract_raw_sprite, or base_image. Port of
        repackWithEffectiveModel.

        `effective_regions` is keyed by internal key and shaped exactly as
        domain/effective_model.py's output (camelCase). A pristine region
        absent from it was dropped by a RemoveBatch; a key present only in
        `added_sprites` is an AddBatch source that never existed in the
        pristine parse.

        TWO CALLER PRECONDITIONS:

        1. `effective_region_names` MUST ALREADY BE IN JS OBJECT-KEY ORDER
           (see repack_with_modded_sprites' sibling note -- unchanged from
           Stage 5).
        2. When `mesh_lookup_fn` is given, masking mirrors
           repack_with_modded_sprites, with one addition: a name present
           in `added_sprites` is also excluded from the pristine-masking
           pass (mirrors atlas-modifier.js:871-889's third guard clause --
           an AddBatch sprite never had a pristine region to look mesh
           geometry up for)."""
        _pristine_names, pristine_regions = self._scoped_region_meta()
        sprites: dict[str, ImageHandle] = {}
        for name, meta in pristine_regions.items():
            if name not in effective_regions:
                continue  # dropped by a RemoveBatch
            sprites[name] = extract_raw_sprite(self.raster, self.base_image, meta)
            if mesh_lookup_fn is not None and name not in (modded_sprites or {}) and name not in (added_sprites or {}):
                geom = mesh_lookup_fn(name)
                if geom is not None:
                    mask_raw_sprite(self.raster, sprites[name], geom, meta)
        for name, sprite in (modded_sprites or {}).items():
            if name in sprites:
                sprites[name] = sprite
        _mask_modded_sprites(self.raster, sprites, modded_sprites, mesh_lookup_fn)
        for key, sprite in (added_sprites or {}).items():
            sprites[key] = sprite  # AddBatch -- a new key, never in the pristine parse
        return self._pack_and_emit(
            sprites, effective_region_names, effective_regions, full_canvas_regions,
            nest_options, modded_sprites, added_sprites, mesh_lookup_fn,
        )
