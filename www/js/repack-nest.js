/**
 * repack-nest.js — the "Nest Regions" packer: a growing, largest-first
 * bin-packer over one shared occupied-pixel-grid mask, adapted from Jake
 * Gordon's binary-tree growing bin-packer
 * (https://jakesgordon.com/writing/bin-packing/). See
 * docs/superpowers/specs/2026-09-08-mesh-silhouette-nesting-design.md §3-4.
 *
 * This file has NO imports and touches no DOM/Canvas -- every function here
 * is pure array arithmetic, exercised directly by node --test. The
 * Canvas-touching half (footprintForCanonical, which derives a packed
 * item's occupied footprint from mesh geometry) is added in a later commit
 * to this same file and is Browser-harness-tested instead, matching this
 * project's established pure/DOM-touching split (see atlas-modifier.js's
 * own maskCropRectForOffsets vs. maskRawSprite for the same pattern).
 */

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
  return Number.isFinite(n) && n >= 1 ? n : 4;
}
