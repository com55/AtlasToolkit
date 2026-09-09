/**
 * repack-nest.js — the "Nest Regions" packer: a growing, largest-first
 * bin-packer over one shared occupied-pixel-grid mask, adapted from Jake
 * Gordon's binary-tree growing bin-packer
 * (https://jakesgordon.com/writing/bin-packing/). See
 * docs/superpowers/specs/2026-09-08-mesh-silhouette-nesting-design.md §3-4.
 *
 * This file touches no DOM/Canvas at module load -- every function here
 * is pure array arithmetic, exercised directly by node --test. (It imports
 * roundUpToMultiple from core-region-ops.js, which itself only touches the
 * DOM inside function bodies, so the import is load-safe under plain Node.) The
 * Canvas-touching half (footprintForCanonical, which derives a packed
 * item's occupied footprint from mesh geometry) is added in a later commit
 * to this same file and is Browser-harness-tested instead, matching this
 * project's established pure/DOM-touching split (see atlas-modifier.js's
 * own maskCropRectForOffsets vs. maskRawSprite for the same pattern).
 */

import { roundUpToMultiple } from './core-region-ops.js';

// ─── Rotation (pure array arithmetic on w*h Uint8Array masks) ────────────────

/** Rotate a w x h mask 90 deg CW. Output is h x w. */
export function rotate90CW(mask, w, h) {
  const out = new Uint8Array(w * h);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      out[x * h + (h - 1 - y)] = mask[y * w + x];
    }
  }
  return out;
}

/** Rotate a w x h mask 180 deg. Output is w x h. */
export function rotate180(mask, w, h) {
  const out = new Uint8Array(w * h);
  for (let i = 0; i < w * h; i++) out[w * h - 1 - i] = mask[i];
  return out;
}

/** Rotate a w x h mask 90 deg CCW. Output is h x w. */
export function rotate90CCW(mask, w, h) {
  const out = new Uint8Array(w * h);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      out[(w - 1 - x) * h + y] = mask[y * w + x];
    }
  }
  return out;
}

// ─── Dilation (separable box-expand, O(w*h*gap) not O(w*h*gap^2)) ───────────

/**
 * Grow every occupied cell outward by `gap` cells (Chebyshev distance).
 * Returns a mask padded by `gap` on every side -- (w+2*gap) x (h+2*gap) --
 * so the true dilation margin (which extends beyond the item's own
 * bounding box) is never clipped; the input mask's own (0,0) corresponds
 * to (gap,gap) in the returned, padded mask. Two-pass separable expand
 * (horizontal then vertical) rather than a naive O(gap^2)-per-cell scan.
 * @returns {{mask: Uint8Array, w: number, h: number}}
 */
export function dilate(mask, w, h, gap) {
  const pw = w + 2 * gap, ph = h + 2 * gap;
  const stage1 = new Uint8Array(pw * h); // padded width, original height
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      if (!mask[y * w + x]) continue;
      const cx = x + gap;
      const x0 = Math.max(0, cx - gap), x1 = Math.min(pw - 1, cx + gap);
      for (let nx = x0; nx <= x1; nx++) stage1[y * pw + nx] = 1;
    }
  }
  const out = new Uint8Array(pw * ph);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < pw; x++) {
      if (!stage1[y * pw + x]) continue;
      const cy = y + gap;
      const y0 = Math.max(0, cy - gap), y1 = Math.min(ph - 1, cy + gap);
      for (let ny = y0; ny <= y1; ny++) out[ny * pw + x] = 1;
    }
  }
  return { mask: out, w: pw, h: ph };
}

// ─── gapDistance normalization ────────────────────────────────────────────────

/** Normalize a gapDistance value to a finite integer >= 1, falling back to
 *  the default (4) on anything else -- an invalid value (corrupt pref, a
 *  bad direct API call) is closer to "unset" than "user chose 1". */
export function normalizeGap(v) {
  const n = Math.floor(Number(v));
  if (!Number.isFinite(n) || n < 1) return 4;
  return Math.min(n, 256);
}

// ─── The packer ───────────────────────────────────────────────────────────────

const SCAN_STEP = 4; // px -- fixed-step raster scan, not per-pixel (performance)

/** Build the 4 rotation variants of one item's footprint, each carrying an
 *  undilated mask (for fit-testing) and a separately-dilated mask (for
 *  stamping once placed) -- see the spec §4 for why these must be two
 *  distinct arrays, not one shared "pre-dilated" mask.
 *  deg -> helper mapping matches this codebase's existing atlas-rotation
 *  convention (core-region-ops.js's cropAndRotate): rotate:90 is stored
 *  90 CCW, rotate:270 is stored 90 CW. */
function rotationVariants(item, gap) {
  const variants = [
    { deg: 0, w: item.w, h: item.h, mask: item.footprint },
    { deg: 90, w: item.h, h: item.w, mask: rotate90CCW(item.footprint, item.w, item.h) },
    { deg: 180, w: item.w, h: item.h, mask: rotate180(item.footprint, item.w, item.h) },
    { deg: 270, w: item.h, h: item.w, mask: rotate90CW(item.footprint, item.w, item.h) },
  ];
  for (const v of variants) v.dilatedMask = dilate(v.mask, v.w, v.h, gap);
  return variants;
}

/** Does rotation `rot` fit at canvas-relative origin (x, y) without
 *  exceeding canvas bounds or overlapping the (already-dilated) occupied
 *  grid? `occupied` is a (canvasW+2*gap) x (canvasH+2*gap) grid, logical
 *  position (lx, ly) at physical index (ly+gap)*(canvasW+2*gap)+(lx+gap). */
function fits(occupied, canvasW, canvasH, gap, rot, x, y) {
  if (x + rot.w > canvasW || y + rot.h > canvasH) return false;
  const paddedW = canvasW + 2 * gap;
  for (let my = 0; my < rot.h; my++) {
    for (let mx = 0; mx < rot.w; mx++) {
      if (!rot.mask[my * rot.w + mx]) continue;
      const py = y + my + gap, px = x + mx + gap;
      if (occupied[py * paddedW + px]) return false;
    }
  }
  return true;
}

/** First-fit scan: for each candidate origin (row-major, step SCAN_STEP),
 *  try every rotation in order; the first that fits wins. Position is the
 *  primary axis (not rotation) so the result stays close to a natural
 *  top-to-bottom, left-to-right fill. */
function findFirstFit(variants, occupied, canvasW, canvasH, gap) {
  for (let y = 0; y <= canvasH - 1; y += SCAN_STEP) {
    for (let x = 0; x <= canvasW - 1; x += SCAN_STEP) {
      for (const rot of variants) {
        if (fits(occupied, canvasW, canvasH, gap, rot, x, y)) return { rot, x, y };
      }
    }
  }
  return null;
}

/** Grow the canvas in whichever direction (right or down) keeps the result
 *  closer to square, sized to at least fit `seedRot`'s own unrotated
 *  dimensions -- adapts _shelfPack's own squareness tie-break (aspect,
 *  then area, then width) to Jake Gordon's incremental-growth heuristic.
 *  At canvasW===0 both candidates come out identical, so the stable sort
 *  naturally keeps "grow right" (listed first) without a special case. */
export function growCanvas(canvasW, canvasH, occupied, seedRot, gap) {
  const candidates = [
    { goRight: true, w: canvasW + seedRot.w, h: Math.max(canvasH, seedRot.h) },
    { goRight: false, w: Math.max(canvasW, seedRot.w), h: canvasH + seedRot.h },
  ];
  for (const c of candidates) {
    c.aspect = Math.max(c.w, c.h) / Math.min(c.w, c.h);
    c.area = c.w * c.h;
  }
  candidates.sort((a, b) => (a.aspect - b.aspect) || (a.area - b.area) || (a.w - b.w));
  const { w: newW, h: newH } = candidates[0];

  const newPaddedW = newW + 2 * gap, newPaddedH = newH + 2 * gap;
  const next = new Uint8Array(newPaddedW * newPaddedH);
  if (canvasW > 0 || canvasH > 0) {
    const oldPaddedW = canvasW + 2 * gap;
    for (let y = 0; y < canvasH + 2 * gap; y++) {
      for (let x = 0; x < oldPaddedW; x++) {
        next[y * newPaddedW + x] = occupied[y * oldPaddedW + x];
      }
    }
  }
  return { canvasW: newW, canvasH: newH, occupied: next };
}

/** OR a placed item's dilated (rotated + grown) mask into `occupied` at its
 *  chosen canvas-relative origin (x, y). Physical-index derivation: the
 *  dilated mask's own (0,0) corresponds to logical (x-gap, y-gap), and
 *  logical-to-physical always adds +gap -- the two cancel, so this is
 *  simply occupied[(y+dy)*paddedW + (x+dx)] for the dilated mask's own
 *  (dx, dy). */
function stampOccupied(occupied, canvasW, gap, rot, x, y) {
  const paddedW = canvasW + 2 * gap;
  const { mask: dm, w: dw, h: dh } = rot.dilatedMask;
  for (let dy = 0; dy < dh; dy++) {
    for (let dx = 0; dx < dw; dx++) {
      if (!dm[dy * dw + dx]) continue;
      occupied[(y + dy) * paddedW + (x + dx)] = 1;
    }
  }
}

/**
 * The packer. Sorts items largest-first, places each one greedily against
 * a single shared occupied-pixel-grid mask (growing the canvas when
 * nothing fits), trying all 4 rotations per item.
 * @param {Array<{name, w, h, footprint: Uint8Array}>} items  footprint is
 *   UNDILATED, w*h, row-major, 1 = occupied / 0 = free, in this item's own
 *   unrotated orientation. Built per-item by footprintForCanonical (Task 3)
 *   -- nestPack itself never touches mesh/sprite data.
 * @param {{gapDistance?: number}} opts
 * @returns {{canvasW, canvasH, placements}}  same shape as _shelfPack's
 *   return, except placements[i].rotate is 0/90/180/270 (never a boolean).
 */
export function nestPack(items, { gapDistance = 4 } = {}) {
  const gap = normalizeGap(gapDistance);
  if (items.length === 0) return { canvasW: 0, canvasH: 0, placements: [] };
  for (const item of items) {
    if (item.w <= 0 || item.h <= 0) {
      throw new Error(`nestPack: item "${item.name}" has non-positive dimensions (${item.w}x${item.h})`);
    }
  }

  const sorted = [...items].sort((a, b) => Math.max(b.w, b.h) - Math.max(a.w, a.h));

  let canvasW = 0, canvasH = 0, occupied = new Uint8Array(0);
  const placements = [];

  for (const item of sorted) {
    const variants = rotationVariants(item, gap);
    let spot = canvasW > 0 ? findFirstFit(variants, occupied, canvasW, canvasH, gap) : null;
    // Growth is monotonic (each call strictly increases canvasW or canvasH
    // by at least the unrotated variant's own dimension), so this loop
    // always terminates -- worst case, the canvas eventually becomes large
    // enough that a completely untouched region exists far from every
    // previously-placed item's dilated footprint.
    while (!spot) {
      ({ canvasW, canvasH, occupied } = growCanvas(canvasW, canvasH, occupied, variants[0], gap));
      spot = findFirstFit(variants, occupied, canvasW, canvasH, gap);
    }
    stampOccupied(occupied, canvasW, gap, spot.rot, spot.x, spot.y);
    placements.push({
      name: item.name, x: spot.x, y: spot.y,
      pw: spot.rot.w, ph: spot.rot.h, rotate: spot.rot.deg,
    });
  }
  return { canvasW: roundUpToMultiple(canvasW), canvasH: roundUpToMultiple(canvasH), placements };
}

import { rasterizeMeshMask } from './region-mesh-mask.js';

// ─── Occupied footprint from mesh geometry (Canvas-touching — see file header) ─

/** Alpha-channel canvas -> Uint8Array mask (1 where alpha > 0, else 0). Only
 *  ever called on the OUTPUT of rasterizeMeshMask (pure geometry) below --
 *  never on a sprite's own rendered pixels (see the spec §1's central
 *  correctness rule). */
function thresholdAlpha(canvas) {
  const { width: w, height: h } = canvas;
  const data = canvas.getContext('2d').getImageData(0, 0, w, h).data;
  const mask = new Uint8Array(w * h);
  for (let i = 0; i < mask.length; i++) mask[i] = data[i * 4 + 3] > 0 ? 1 : 0;
  return mask;
}

/** All-1 mask, w*h -- the "no usable mesh, protect the whole rect" fallback. */
function solidMask(w, h) {
  return new Uint8Array(w * h).fill(1);
}

/** Crop a {x,y,w,h} sub-rect out of a full-size canvas into a fresh
 *  w x h canvas -- mirrors maskRawSprite's own crop-back pattern
 *  (atlas-modifier.js), but as a plain copy since there's no sprite to
 *  composite onto here, only the geometry mask itself. */
function cropCanvas(src, rect) {
  const c = document.createElement('canvas');
  c.width = rect.w; c.height = rect.h;
  c.getContext('2d').drawImage(src, rect.x, rect.y, rect.w, rect.h, 0, 0, rect.w, rect.h);
  return c;
}

/** OR every mask in `masks` together. Empty input (no contributing member
 *  at all) returns a solid mask, matching the "no mesh data -> unmasked"
 *  convention used throughout this codebase. */
function unionMasks(masks, w, h) {
  if (masks.length === 0) return solidMask(w, h);
  const out = new Uint8Array(w * h);
  for (const m of masks) for (let i = 0; i < out.length; i++) if (m[i]) out[i] = 1;
  return out;
}

/**
 * The occupied-footprint mask for ONE canonical (post-dedup) packed item,
 * in the SAME (w x h) coordinate space as the sprite that will actually be
 * packed. Never reads any sprite's rendered pixel alpha -- always derived
 * from mesh GEOMETRY, reusing exactly the masking decision spec 2 already
 * makes for each contributing sprite (never independently re-derived). See
 * the design spec's §1 for the full reasoning and the two verified
 * counterexamples this implementation exists to avoid.
 *
 * @param {string[]} dedupNames  every region name _packAndEmit's
 *   canonicalMap mapped to this one canonical placement -- NOT
 *   groupNamesBySpriteIdentity's narrower shared-canvas-mod grouping (a
 *   dedup group can legitimately contain several independent mod-canvas
 *   groups, or singletons, that only coincide by final rendered
 *   appearance).
 * @param {object} ctx
 * @param {{[name:string]: HTMLCanvasElement}} ctx.sprites
 * @param {{[name:string]: HTMLCanvasElement}|null} ctx.moddedSprites
 * @param {{[name:string]: HTMLCanvasElement}|null} ctx.addedSprites
 * @param {{[name:string]: object}} ctx.regions
 * @param {((name: string) => {uvs, triangles}|null)|null} ctx.meshLookupFn
 * @param {(names: string[], meshLookupFn) => {uvs,triangles}|null} ctx.combineMeshGeometry
 *   injected (atlas-modifier.js's _combineMeshGeometry) -- never imported
 *   here, to avoid a circular import.
 * @param {(sprites, moddedSprites) => string[][]} ctx.groupNamesBySpriteIdentity
 *   injected (atlas-modifier.js's _groupNamesBySpriteIdentity).
 * @param {(offsets, w, h) => {x,y,w,h}} ctx.maskCropRectForOffsets
 *   injected (atlas-modifier.js's maskCropRectForOffsets).
 * @returns {Uint8Array}
 */
export function footprintForCanonical(dedupNames, ctx) {
  const { sprites, moddedSprites, addedSprites, regions, meshLookupFn,
    combineMeshGeometry, groupNamesBySpriteIdentity, maskCropRectForOffsets } = ctx;
  const canonicalSprite = sprites[dedupNames[0]];
  const { width: w, height: h } = canonicalSprite;
  if (!meshLookupFn) return solidMask(w, h); // covers every path below, including
    // the shared-canvas-mod branch -- combineMeshGeometry itself calls
    // meshLookupFn unconditionally and would throw otherwise.

  const modGroups = groupNamesBySpriteIdentity(sprites, moddedSprites);

  // Dedup does NOT guarantee every name in this group shares the
  // canonical's own dimensions -- a pixel-hash collision between
  // differently-shaped sprites (same total byte count, matching content)
  // is possible with this codebase's current _canvasHash (its primary
  // SHA-256 path hashes only RGBA bytes, never width/height). This does
  // not fix that pre-existing gap (out of scope here); it only keeps this
  // function's own footprint from being corrupted by it. Resolved once,
  // for the whole dedup group, before any branching below -- a
  // shared-canvas-mod group's contribution must only ever include the
  // subset of its members that are BOTH part of this dedup group AND
  // dimension-compatible, never the group's full, unfiltered membership.
  const dedupSet = new Set(dedupNames);
  const compatible = new Map();
  for (const name of dedupNames) {
    compatible.set(name, sprites[name].width === w && sprites[name].height === h);
  }

  const masks = [];
  const handled = new Set();
  for (const name of dedupNames) {
    if (handled.has(name)) continue;
    if (!compatible.get(name)) { handled.add(name); continue; }
    const modGroup = modGroups.find(g => g.includes(name));
    if (modGroup) {
      const usableGroup = modGroup.filter(n => dedupSet.has(n) && compatible.get(n));
      const combined = usableGroup.length > 0 ? combineMeshGeometry(usableGroup, meshLookupFn) : null;
      masks.push(combined
        ? thresholdAlpha(rasterizeMeshMask(combined.uvs, combined.triangles, w, h))
        : solidMask(w, h));
      for (const n of modGroup) handled.add(n);
      continue;
    }
    handled.add(name);
    const geom = meshLookupFn(name);
    if (!geom) { masks.push(solidMask(w, h)); continue; }
    const isModdedOrAdded = (moddedSprites && name in moddedSprites)
      || (addedSprites && name in addedSprites);
    const region = regions[name];
    if (isModdedOrAdded || !region.offsets) {
      masks.push(thresholdAlpha(rasterizeMeshMask(geom.uvs, geom.triangles, w, h)));
    } else {
      const rect = maskCropRectForOffsets(region.offsets, w, h);
      const [, , origW, origH] = region.offsets;
      const full = rasterizeMeshMask(geom.uvs, geom.triangles, origW, origH);
      masks.push(thresholdAlpha(cropCanvas(full, rect)));
    }
  }
  return unionMasks(masks, w, h);
}
