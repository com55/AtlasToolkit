/**
 * atlas-extracter.js
 * Port of atlas_extracter.py using Canvas API.
 */

class AtlasPage {
  constructor(filename) {
    this.filename = filename;
    this.width = 0;
    this.height = 0;
    this.format = 'RGBA8888';
    this.filter = ['Nearest', 'Nearest'];
    this.repeat = 'none';
    this.scaleX = 1.0;
    this.scaleY = 1.0;
  }
}

class AtlasRegion {
  constructor(name, pageFilename) {
    this.name = name;
    this.pageFilename = pageFilename;
    this.index = -1;
    this.x = 0;
    this.y = 0;
    this.w = 0;
    this.h = 0;
    this.offsets = null; // [off_x, off_y, orig_w, orig_h]
    this.rotate = 0;
  }
}

export class AtlasProcessor {
  constructor(atlasContent) {
    this.atlasContent = atlasContent;
    this.pages = [];
    this.regions = {};       // name → AtlasRegion (ordered by insertion)
    this._loadedImages = {}; // pageName → HTMLImageElement
    this._pageMap = {};      // pageName → AtlasPage
    this._parse();
  }

  _parse() {
    const lines = this.atlasContent.split('\n').map(l => l.trim());
    let currentPage = null;
    let currentRegion = null;

    for (const line of lines) {
      if (!line) continue;

      if (line.endsWith('.png')) {
        currentPage = new AtlasPage(line);
        this.pages.push(currentPage);
        this._pageMap[line] = currentPage;
        currentRegion = null;
        continue;
      }

      if (line.includes(':')) {
        const idx = line.indexOf(':');
        const key = line.slice(0, idx).trim().toLowerCase();
        const vals = line.slice(idx + 1).split(',').map(v => v.trim());

        if (currentRegion) {
          if (key === 'bounds' && vals.length >= 4) {
            currentRegion.x = parseInt(vals[0]);
            currentRegion.y = parseInt(vals[1]);
            currentRegion.w = parseInt(vals[2]);
            currentRegion.h = parseInt(vals[3]);
          } else if (key === 'xy') {
            currentRegion.x = parseInt(vals[0]);
            currentRegion.y = parseInt(vals[1]);
          } else if (key === 'size' && currentRegion.w === 0) {
            // Only apply size to region if we haven't got bounds yet
            currentRegion.w = parseInt(vals[0]);
            currentRegion.h = parseInt(vals[1]);
          } else if (key === 'rotate') {
            const v = vals[0].toLowerCase();
            if (v === 'true') currentRegion.rotate = 90;
            else if (v === 'false') currentRegion.rotate = 0;
            else { const n = parseInt(v); currentRegion.rotate = isNaN(n) ? 0 : n; }
          } else if (key === 'offsets' && vals.length >= 4) {
            currentRegion.offsets = vals.map(Number);
          } else if (key === 'index') {
            currentRegion.index = parseInt(vals[0]);
          }
        } else if (currentPage) {
          if (key === 'size') {
            currentPage.width = parseInt(vals[0]);
            currentPage.height = parseInt(vals[1]);
          } else if (key === 'format') {
            currentPage.format = vals[0];
          } else if (key === 'filter') {
            currentPage.filter = [vals[0], vals[1]];
          } else if (key === 'repeat') {
            currentPage.repeat = vals[0];
          }
        }
        continue;
      }

      // No colon and not a .png line → region name
      if (!currentPage) continue;
      currentRegion = new AtlasRegion(line, currentPage.filename);
      this.regions[line] = currentRegion;
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
   */
  static cropAndRotate(img, x, y, w, h, rotate) {
    const isSwapped = rotate === 90 || rotate === 270;
    const cropW = isSwapped ? h : w;
    const cropH = isSwapped ? w : h;

    const canvas = document.createElement('canvas');
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext('2d');

    if (rotate === 0) {
      ctx.drawImage(img, x, y, cropW, cropH, 0, 0, w, h);
    } else if (rotate === 90) {
      // Stored in atlas as h×w; un-rotate 90° CW → w×h
      ctx.translate(w, 0);
      ctx.rotate(Math.PI / 2);
      ctx.drawImage(img, x, y, cropW, cropH, 0, 0, cropW, cropH);
    } else if (rotate === 270) {
      // Stored in atlas as h×w; un-rotate 90° CCW → w×h
      ctx.translate(0, h);
      ctx.rotate(-Math.PI / 2);
      ctx.drawImage(img, x, y, cropW, cropH, 0, 0, cropW, cropH);
    } else if (rotate === 180) {
      ctx.translate(w, h);
      ctx.rotate(Math.PI);
      ctx.drawImage(img, x, y, w, h, 0, 0, w, h);
    }

    return canvas;
  }

  /** Extract a single region as a canvas (includes offset padding). */
  extractRegion(name) {
    const region = this.regions[name];
    if (!region) return null;
    const baseImg = this._loadedImages[region.pageFilename];
    if (!baseImg) return null;

    let { x, y, w, h, rotate, offsets } = region;
    const page = this._pageMap[region.pageFilename];

    if (page && (page.scaleX !== 1.0 || page.scaleY !== 1.0)) {
      x = Math.round(x * page.scaleX);
      y = Math.round(y * page.scaleY);
      w = Math.round(w * page.scaleX);
      h = Math.round(h * page.scaleY);
    }

    const sprite = AtlasProcessor.cropAndRotate(baseImg, x, y, w, h, rotate);
    const currentW = sprite.width;
    const currentH = sprite.height;

    if (offsets) {
      let [offX, offY, origW, origH] = offsets;
      if (page && (page.scaleX !== 1.0 || page.scaleY !== 1.0)) {
        offX = Math.round(offX * page.scaleX);
        offY = Math.round(offY * page.scaleY);
        origW = Math.round(origW * page.scaleX);
        origH = Math.round(origH * page.scaleY);
      }
      const canvas = document.createElement('canvas');
      canvas.width = origW;
      canvas.height = origH;
      const ctx = canvas.getContext('2d');
      ctx.drawImage(sprite, offX, origH - offY - currentH);
      return canvas;
    }

    return sprite;
  }

  /** Extract a single region as a base64 PNG data URI. */
  extractRegionAsDataURL(name) {
    const canvas = this.extractRegion(name);
    return canvas ? canvas.toDataURL('image/png') : null;
  }

  /**
   * Get a composite preview of one or more region names as a data URI.
   * Regions are composited (alpha-blended) on a max-size canvas.
   */
  getPreviewDataURL(names) {
    const images = names
      .filter(n => n in this.regions)
      .map(n => this.extractRegion(n))
      .filter(Boolean);

    if (images.length === 0) return null;
    if (images.length === 1) return images[0].toDataURL('image/png');

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

    return canvas.toDataURL('image/png');
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

/** Load an image from a File object or a URL/data-URL string. */
export function _loadImage(source) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error(`Failed to load image: ${source}`));
    if (source instanceof File) {
      img.src = URL.createObjectURL(source);
    } else {
      img.src = source;
    }
  });
}
