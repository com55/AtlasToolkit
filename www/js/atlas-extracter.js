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
   *  the mesh-mask toggle flips. `lookup` may be null (no .skel / parse
   *  failed / unsupported version). */
  setMeshMaskData(lookup, enabled) {
    this._meshLookup = lookup;
    this._maskEnabled = enabled;
  }

  /** Extract a single region as a canvas (includes offset padding). */
  extractRegion(name) {
    const region = this.regions[name];
    if (!region) return null;
    const baseImg = this._loadedImages[region.pageFilename];
    if (!baseImg) return null;
    const page = this._pageMap[region.pageFilename];
    const meshGeometry = (this._maskEnabled && this._meshLookup && !page?.pma)
      ? (this._meshLookup.get(name) ?? null)
      : null;
    return extractRegionFromPage(baseImg, region, page, meshGeometry);
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
  getPreviewDataURL(names) {
    const images = names
      .filter(n => n in this.regions)
      .map(n => this.extractRegion(n))
      .filter(Boolean);

    if (images.length === 0) return Promise.resolve(null);
    if (images.length === 1) return canvasToPreviewUrl(images[0]);

    const maxW = Math.max(...images.map(c => c.width));
    const maxH = Math.max(...images.map(c => c.height));

    const canvas = document.createElement('canvas');
    canvas.width = maxW;
    canvas.height = maxH;
    const ctx = canvas.getContext('2d');

    // Composite in reverse order (last on top matches Python's alpha_composite)
    for (const img of [...images].reverse()) {
      ctx.drawImage(img, 0, 0);
    }

    return canvasToPreviewUrl(canvas);
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

/**
 * Encode a canvas as a `blob:` URL via toBlob() — skips the PNG→base64
 * step that `toDataURL('image/png')` does on the main thread. The preview
 * <img> and getPreviewPngBlob()'s fetch(img.src) both accept blob: URLs.
 */
export function canvasToPreviewUrl(canvas) {
  if (!canvas) return Promise.resolve(null);
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (!blob) {
        reject(new Error('canvas.toBlob failed'));
        return;
      }
      resolve(URL.createObjectURL(blob));
    }, 'image/png');
  });
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
