/**
 * atlas-extracter.js
 * Port of atlas_extracter.py using Canvas API.
 *
 * Parsing is re-homed onto AtlasDocument (the single parse/serialize seam,
 * ported from the Python document.py) — the AtlasPage/AtlasRegion classes
 * below are now thin data-holders populated from AtlasDocument.parse(), kept
 * so the rest of the JS pipeline sees the same object shape it always has
 * (page.width/height, region.pageFilename, extraPairs as {key, values}).
 * Rotation/offset math is re-homed onto core-region-ops.js.
 */

import { AtlasDocument } from './atlas-document.js';
import { cropAndRotate as coreCropAndRotate, extractRegionFromPage } from './core-region-ops.js';
import { resizeCanvasLanczos } from './lanczos-resize.js';
import { canvasToPngBlob, createCanvas } from './canvas-surface.js';

class AtlasPage {
  constructor(filename) {
    this.filename = filename;
    this.width = 0;
    this.height = 0;
    this.format = 'RGBA8888';
    this.filter = ['Nearest', 'Nearest'];
    this.repeat = 'none';
    this.pma = false;
    this.scaleX = 1.0;
    this.scaleY = 1.0;
  }
}

class AtlasRegion {
  constructor(name, atlasName, pageFilename) {
    this.name = name;
    this.atlasName = atlasName;
    this.pageFilename = pageFilename;
    this.index = -1;
    this.x = 0;
    this.y = 0;
    this.w = 0;
    this.h = 0;
    this.offsets = null; // [off_x, off_y, orig_w, orig_h]
    this.rotate = 0;
    this.split = null;
    this.pad = null;
    this.extraPairs = [];
  }
}

export class AtlasProcessor {
  constructor(atlasContent) {
    this.atlasContent = atlasContent;
    this.pages = [];
    this.regions = {};       // name → AtlasRegion (ordered by insertion)
    this._loadedImages = {}; // pageName → HTMLImageElement
    this._pageMap = {};      // pageName → AtlasPage
    this._meshLookup = null;   // Map<regionName, {uvs, triangles}> | null
    this._maskEnabled = false;
    this._nestEnabled = false;
    this._nestGapDistance = 4;
    this._parse();
  }

  _parse() {
    // Delegate to the single parse seam, then adapt AtlasDocument's Page/Region
    // into the AtlasPage/AtlasRegion shapes the rest of this module exposes.
    const doc = AtlasDocument.parse(this.atlasContent);
    for (const dp of doc.pages) {
      const page = new AtlasPage(dp.filename);
      page.width = dp.size[0];
      page.height = dp.size[1];
      page.format = dp.format;
      page.filter = [dp.filter[0], dp.filter[1]];
      page.repeat = dp.repeat;
      page.pma = dp.pma;
      this.pages.push(page);
      this._pageMap[dp.filename] = page;

      for (const dr of dp.regions) {
        const region = new AtlasRegion(dr.name, dr.atlasName, dr.pageFilename);
        region.index = dr.index;
        region.x = dr.x;
        region.y = dr.y;
        region.w = dr.w;
        region.h = dr.h;
        region.offsets = dr.offsets ? [...dr.offsets] : null;
        region.rotate = dr.rotate;
        region.split = dr.split ? [...dr.split] : null;
        region.pad = dr.pad ? [...dr.pad] : null;
        // Document stores extraPairs as [key, values] tuples; this module has
        // always exposed them as {key, values} objects — keep that contract.
        region.extraPairs = dr.extraPairs.map(([key, values]) => ({ key, values: [...values] }));
        this.regions[dr.name] = region;
      }
    }
  }

  /**
   * Load images from a map of { pageName: File | string(dataURL|url) }.
   * Must be called before extracting regions.
   */
  async loadImages(imageFileMap) {
    for (const [pageName, source] of Object.entries(imageFileMap)) {
      try {
        const img = await _loadImage(source);
        const page = this._pageMap[pageName];
        if (page && page.width !== 0 && page.height !== 0) {
          if (img.naturalWidth !== page.width || img.naturalHeight !== page.height) {
            page.scaleX = img.naturalWidth / page.width;
            page.scaleY = img.naturalHeight / page.height;
          }
        }
        this._loadedImages[pageName] = img;
      } catch (e) {
        console.error(`Failed to load image ${pageName}:`, e);
      }
    }
  }

  getPageImage(pageName) {
    if (pageName) return this._loadedImages[pageName] || null;
    const keys = Object.keys(this._loadedImages);
    return keys.length > 0 ? this._loadedImages[keys[0]] : null;
  }

  /**
   * Crop a region from img (HTMLImageElement or canvas) and undo atlas rotation.
   * Returns a canvas element (w × h) with the sprite in its original orientation.
   * Delegates to the single rotation seam in core-region-ops.js.
   */
  static cropAndRotate(img, x, y, w, h, rotate) {
    return coreCropAndRotate(img, x, y, w, h, rotate);
  }

  /** Wired from atlas-api.js whenever the sibling .skel is (re)parsed or
   *  either mesh toggle flips. `lookup` may be null (no .skel / parse
   *  failed / unsupported version). `repackEnabled` gates ONLY
   *  getRepackMeshGeometry() (the "Mesh-Aware Repack" toggle) -- it has no
   *  default, so a caller that forgets it gets masking-during-repack
   *  silently OFF, never a silent revert to "on" that would override an
   *  explicit user preference. */
  setMeshMaskData(lookup, enabled, repackEnabled) {
    this._meshLookup = lookup;
    this._maskEnabled = enabled;
    this._repackMaskEnabled = !!repackEnabled;
  }

  /** Mesh availability for a region: lookup entry exists, page is not PMA.
   *  Shared by both toggle-specific gates below -- neither toggle on/off
   *  state, just whether there is usable mesh data at all. */
  _meshAvailableFor(name) {
    const region = this.regions[name];
    if (!region) return null;
    const page = this._pageMap[region.pageFilename];
    if (!(this._meshLookup && !page?.pma)) return null;
    return this._meshLookup.get(name) ?? null;
  }

  /** Extraction gate: mesh availability + the Mesh Cropping toggle. Used by
   *  extractRegion() (View mode preview/export) -- independent of the
   *  repack-only toggle below (confirmed with user: the two toggles are
   *  not coupled, they only share the underlying .skel data). */
  getMeshGeometry(name) {
    if (!this._maskEnabled) return null;
    return this._meshAvailableFor(name);
  }

  /** Repack-specific gate: mesh availability + the Mesh-Aware Repack
   *  toggle. Used by AtlasSession repack call sites -- independent of
   *  whether View mode extraction is masked. */
  getRepackMeshGeometry(name) {
    if (!this._repackMaskEnabled) return null;
    return this._meshAvailableFor(name);
  }

  /** Wired from atlas-api.js whenever the Nest Regions toggle or its
   *  gap-distance changes. Independent of setMeshMaskData -- Nest Regions
   *  is not gated by either mesh toggle (design spec's Context section,
   *  "Toggle independence": reusing getRepackMeshGeometry inside
   *  footprintForCanonical is what makes Gap B silently disappear when
   *  Mesh-Aware Repack is off, with zero extra gating code needed here). */
  setNestOptions(enabled, gapDistance) {
    this._nestEnabled = !!enabled;
    this._nestGapDistance = gapDistance;
  }

  /** { enabled, gapDistance } for AtlasSession's repack call sites. Falls
   *  back to the class defaults (off, 4px) if gapDistance is somehow not a
   *  finite number -- nestPack's own normalizeGap is the final backstop,
   *  this is belt-and-suspenders at the source. */
  getNestOptions() {
    return {
      enabled: !!this._nestEnabled,
      gapDistance: Number.isFinite(this._nestGapDistance) ? this._nestGapDistance : 4,
    };
  }

  /** Extract a single region as a canvas (includes offset padding). */
  extractRegion(name) {
    const region = this.regions[name];
    if (!region) return null;
    const baseImg = this._loadedImages[region.pageFilename];
    if (!baseImg) return null;
    const page = this._pageMap[region.pageFilename];
    return extractRegionFromPage(baseImg, region, page, this.getMeshGeometry(name));
  }

  /** Extract a single region as a blob: URL (no base64). */
  extractRegionAsDataURL(name) {
    const canvas = this.extractRegion(name);
    return canvasToPreviewUrl(canvas);
  }

  /**
   * Get a composite preview of one or more region names as a blob: URL.
   * Regions are composited (alpha-blended) on a max-size canvas.
   * Avoids canvas.toDataURL() — encoding a full atlas page to a PNG data
   * URI is the remaining desktop-slowness after the pywebview base64-bridge
   * fix (2026-08-23): a 2k–4k page can take hundreds of ms just to
   * base64-encode, then the <img> has to decode it again.
   */
  getPreviewDataURL(names, { forceResize = false } = {}) {
    const images = names
      .filter(n => n in this.regions)
      .map(n => this.extractRegion(n))
      .filter(Boolean);
    const canvas = renderPreviewCanvases(images, forceResize);
    return canvasToPreviewUrl(canvas);
  }

  /** Plain data for a preview Worker. Page pixels stay on the caller. */
  previewJobSpec(names, forceResize) {
    const regions = [];
    const pageIds = [];
    for (const name of names) {
      const region = this.regions[name];
      if (!region) continue;
      if (!pageIds.includes(region.pageFilename)) pageIds.push(region.pageFilename);
      const page = this._pageMap[region.pageFilename];
      const mesh = this.getMeshGeometry(name);
      regions.push({
        pageId: region.pageFilename,
        x: region.x,
        y: region.y,
        w: region.w,
        h: region.h,
        rotate: region.rotate,
        offsets: region.offsets ? [...region.offsets] : null,
        page: page ? { scaleX: page.scaleX, scaleY: page.scaleY } : null,
        mesh: mesh ? { uvs: [...mesh.uvs], triangles: [...mesh.triangles] } : null,
      });
    }
    return { forceResize: !!forceResize, regions, pageIds };
  }

  /**
   * Extract all regions. Returns { name: canvas }.
   */
  extractAll() {
    const results = {};
    for (const name of Object.keys(this.regions)) {
      try {
        const canvas = this.extractRegion(name);
        if (canvas) results[name] = canvas;
      } catch (e) {
        console.error(`Failed to extract ${name}:`, e);
      }
    }
    return results;
  }
}

// ─── Helpers ────────────────────────────────────────────────────────────────

/** Greatest extracted-canvas area, in name order. An equal area keeps the
 *  earlier entry. */
export function pickForceResizeTarget(sizes) {
  let best = sizes[0];
  let bestArea = best.width * best.height;
  for (let i = 1; i < sizes.length; i++) {
    const area = sizes[i].width * sizes[i].height;
    if (area > bestArea) {
      best = sizes[i];
      bestArea = area;
    }
  }
  return { width: best.width, height: best.height };
}

/** Stack extracted canvases. One image is returned as-is. */
export function renderPreviewCanvases(images, forceResize) {
  if (!images || images.length === 0) return null;
  if (images.length === 1) return images[0];
  const frame = previewCompositeFrame(
    images.map((c) => ({ width: c.width, height: c.height })),
    forceResize,
  );
  const canvas = createCanvas(frame.width, frame.height);
  const ctx = canvas.getContext('2d');
  ctx.imageSmoothingEnabled = false;
  // Reverse order: the earlier list entry is painted last (on top).
  // Forced resizing enlarges each extracted canvas with Lanczos-3.
  for (const img of [...images].reverse()) {
    if (!frame.scale || (img.width === frame.width && img.height === frame.height)) {
      ctx.drawImage(img, 0, 0);
      continue;
    }
    ctx.drawImage(resizeCanvasLanczos(img, frame.width, frame.height), 0, 0);
  }
  return canvas;
}

/** Frame for the View Mode multi-image composite. Forced resizing scales
 *  every canvas to the single largest area; otherwise the frame is
 *  max(width) × max(height) and images are drawn unscaled. */
export function previewCompositeFrame(sizes, forceResize) {
  const width = Math.max(...sizes.map((s) => s.width));
  const height = Math.max(...sizes.map((s) => s.height));
  if (!forceResize || sizes.length < 2) {
    return { width, height, scale: false };
  }
  const target = pickForceResizeTarget(sizes);
  return { width: target.width, height: target.height, scale: true };
}

/**
 * Encode a canvas as a `blob:` URL via toBlob() — skips the PNG→base64
 * step that `toDataURL('image/png')` does on the main thread. The preview
 * <img> and getPreviewPngBlob()'s fetch(img.src) both accept blob: URLs.
 */
export async function canvasToPreviewUrl(canvas) {
  if (!canvas) return null;
  const blob = await canvasToPngBlob(canvas);
  if (!blob) return null;
  return URL.createObjectURL(blob);
}

/** Load an image from a File object or a URL/data-URL string. */
export function _loadImage(source) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    let objectUrl = null;
    img.onload = () => {
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      resolve(img);
    };
    img.onerror = () => {
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      reject(new Error(`Failed to load image: ${source}`));
    };
    if (source instanceof File) {
      objectUrl = URL.createObjectURL(source);
      img.src = objectUrl;
    } else {
      img.src = source;
    }
  });
}
