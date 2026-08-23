/**
 * core-region-ops.js — single source of truth for atlas region rotation /
 * offset / rounding math.
 *
 * Line-for-line port of `atlas_toolkit/core/region_ops.py` on the `main`
 * branch (the Python desktop app's region-ops module), which is the normative
 * reference. Previously this math was duplicated inline across
 * atlas-extracter.js (`cropAndRotate` / `extractRegion`) and atlas-modifier.js
 * (merge-rotate + repack-rotate branches); those now delegate here.
 *
 * Rotation direction (verified empirically against PIL — see
 * test/core-region-ops.test.js and task-4a-report.md):
 *   PIL ROTATE_90  = 90° CCW,  ROTATE_270 = 90° CW.
 *   Un-rotation: rotate==90 -> ROTATE_270 (90° CW),
 *                rotate==270 -> ROTATE_90 (90° CCW),
 *                rotate==180 -> ROTATE_180.
 *   Canvas 2D ctx.rotate(+θ) is CW in the y-down coordinate system, so
 *   rotate==90 maps to ctx.rotate(+PI/2) and rotate==270 to ctx.rotate(-PI/2).
 *
 * The `roundHalfEven` / `overlayRect` helpers are pure and run under plain
 * Node (used by `node --test`); the canvas helpers require a DOM/Canvas and
 * are exercised via a headless-browser pixel test.
 */

/**
 * Python `round()` parity (banker's rounding): on an exact .5 tie, round to
 * the nearest even integer, instead of JS `Math.round()`'s round-half-up.
 * e.g. roundHalfEven(2.5) === 2, roundHalfEven(3.5) === 4 (Math.round gives 3
 * and 4). Used everywhere a scale ratio can land on .5.
 * @param {number} x
 * @returns {number}
 */
export function roundHalfEven(x) {
  const floor = Math.floor(x);
  const diff = x - floor;
  if (diff < 0.5) return floor;
  if (diff > 0.5) return floor + 1;
  // Exact .5 tie: round toward the even neighbour.
  return floor % 2 === 0 ? floor : floor + 1;
}

/**
 * Round *value* up to the nearest multiple of *multiple* (default 4).
 * Used to pad packed/merged atlas page canvases to GPU texture-compression
 * block-size alignment.
 * @param {number} value
 * @param {number} [multiple]
 * @returns {number}
 */
export function roundUpToMultiple(value, multiple = 4) {
  if (value <= 0) return 0;
  const remainder = value % multiple;
  return remainder === 0 ? value : value + (multiple - remainder);
}

/**
 * Post-rotation bounding rectangle for a region. Bounds are stored
 * pre-rotation ([x, y, w, h]); when the region is stored rotated 90/270 in the
 * atlas its on-page footprint has width/height swapped.
 * @param {{bounds: number[], rotate: number}} region
 * @returns {number[]} [x, y, w, h] as laid out on the page
 */
export function overlayRect(region) {
  const [x, y, w, h] = region.bounds;
  return region.rotate === 90 || region.rotate === 270 ? [x, y, h, w] : [x, y, w, h];
}

/**
 * Crop a region from `img` (HTMLImageElement or canvas) and undo the atlas's
 * stored rotation. Returns a fresh canvas (w × h) with the sprite in its
 * original, un-rotated orientation.
 *
 * Mirrors region_ops.py::crop_and_rotate. The crop box is taken at the
 * region's on-page footprint (h × w when rotate is 90/270), then transposed
 * back to w × h.
 *
 * @param {CanvasImageSource} img
 * @param {number} x
 * @param {number} y
 * @param {number} w  pre-rotation width
 * @param {number} h  pre-rotation height
 * @param {number} rotate  0 / 90 / 180 / 270
 * @returns {HTMLCanvasElement}
 */
export function cropAndRotate(img, x, y, w, h, rotate) {
  const isSwapped = rotate === 90 || rotate === 270;
  const cropW = isSwapped ? h : w;
  const cropH = isSwapped ? w : h;

  const canvas = document.createElement('canvas');
  canvas.width = w;
  canvas.height = h;
  // willReadFrequently: every sprite this produces gets getImageData'd at
  // least once downstream (repack dedup hashing -- atlas-modifier.js's
  // _canvasHash), often twice (its documented priming-read workaround).
  // Without this hint Chromium keeps the canvas GPU-backed and each
  // getImageData call forces a GPU->CPU readback sync; measured ~4ms/call
  // fixed overhead regardless of these sprites' small size, dominating a
  // 68-region repack (perf fix, 2026-08-23). CPU-backed via this hint
  // measured ~2.5x faster per read in the same scenario.
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  ctx.imageSmoothingEnabled = false;

  if (rotate === 90) {
    // Stored in atlas as h×w; un-rotate 90° CW (PIL ROTATE_270) → w×h.
    ctx.translate(w, 0);
    ctx.rotate(Math.PI / 2);
    ctx.drawImage(img, x, y, cropW, cropH, 0, 0, cropW, cropH);
  } else if (rotate === 270) {
    // Stored in atlas as h×w; un-rotate 90° CCW (PIL ROTATE_90) → w×h.
    ctx.translate(0, h);
    ctx.rotate(-Math.PI / 2);
    ctx.drawImage(img, x, y, cropW, cropH, 0, 0, cropW, cropH);
  } else if (rotate === 180) {
    ctx.translate(w, h);
    ctx.rotate(Math.PI);
    ctx.drawImage(img, x, y, w, h, 0, 0, w, h);
  } else {
    ctx.drawImage(img, x, y, cropW, cropH, 0, 0, w, h);
  }

  return canvas;
}

/**
 * Extract a region from its page image: apply page scale factors, crop and
 * un-rotate, then (if the region has offsets) paste onto an original-size
 * transparent canvas using the Spine bottom-edge offset convention.
 *
 * Mirrors region_ops.py::extract_region_from_page exactly, including the
 * banker's-rounding of scaled coordinates and the Y-flip paste
 * (paste_y = origH - offY - spriteH).
 *
 * @param {CanvasImageSource} pageImage
 * @param {{x:number,y:number,w:number,h:number,rotate:number,offsets:?number[]}} region
 * @param {?{scaleX:number,scaleY:number}} [page]
 * @returns {HTMLCanvasElement}
 */
export function extractRegionFromPage(pageImage, region, page = null) {
  let { x, y, w: rawW, h: rawH } = region;
  const rot = region.rotate;

  if (page && (page.scaleX !== 1.0 || page.scaleY !== 1.0)) {
    const sx = page.scaleX;
    const sy = page.scaleY;
    x = roundHalfEven(x * sx);
    y = roundHalfEven(y * sy);
    rawW = roundHalfEven(rawW * sx);
    rawH = roundHalfEven(rawH * sy);
  }

  const sprite = cropAndRotate(pageImage, x, y, rawW, rawH, rot);
  const currentW = sprite.width;
  const currentH = sprite.height;

  if (!region.offsets) return sprite;

  let [offX, offY, origW, origH] = region.offsets;
  if (page && (page.scaleX !== 1.0 || page.scaleY !== 1.0)) {
    const sx = page.scaleX;
    const sy = page.scaleY;
    offX = roundHalfEven(offX * sx);
    offY = roundHalfEven(offY * sy);
    origW = roundHalfEven(origW * sx);
    origH = roundHalfEven(origH * sy);
  }

  const canvas = document.createElement('canvas');
  canvas.width = origW;
  canvas.height = origH;
  const ctx = canvas.getContext('2d', { willReadFrequently: true }); // see cropAndRotate's comment
  ctx.imageSmoothingEnabled = false;
  const pasteX = offX;
  const pasteY = origH - offY - currentH;
  ctx.drawImage(sprite, pasteX, pasteY);
  return canvas;
}
