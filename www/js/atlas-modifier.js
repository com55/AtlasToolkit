/**
 * atlas-modifier.js
 * Port of atlas_modifier.py using Canvas API.
 */

import { AtlasProcessor } from './atlas-extracter.js';

// ─── Parse atlas text using AtlasProcessor ──────────────────────────────────

export function parseAtlas(atlasText) {
  const processor = new AtlasProcessor(atlasText);
  const pageInfo = {};
  if (processor.pages.length > 0) {
    const p = processor.pages[0];
    pageInfo.page = p.filename;
    pageInfo.size = `${p.width},${p.height}`;
    pageInfo.format = p.format;
    pageInfo.filter = `${p.filter[0]}, ${p.filter[1]}`;
    pageInfo.repeat = p.repeat;
    pageInfo.pma = !!p.pma;
  }
  const regionNames = Object.keys(processor.regions);
  const regions = {};
  for (const [name, r] of Object.entries(processor.regions)) {
    regions[name] = {
      name,
      atlasName: r.atlasName || name,
      page: r.pageFilename,
      index: Number.isFinite(r.index) ? r.index : -1,
      bounds: [r.x, r.y, r.w, r.h],
      offsets: r.offsets,
      rotate: r.rotate,
      split: r.split,
      pad: r.pad,
      extraPairs: Array.isArray(r.extraPairs) ? r.extraPairs.map(p => ({ key: p.key, values: [...p.values] })) : [],
    };
  }
  return { pageInfo, regionNames, regions };
}

// ─── Atlas text manipulation ─────────────────────────────────────────────────

function _formatRotate(val) {
  if (val === 90) return 'true';
  if (val === 180) return '180';
  if (val === 270) return '270';
  return null;
}

function _isDefaultOffsets(offsets, bounds) {
  if (!offsets || !bounds) return true;
  return offsets[0] === 0 && offsets[1] === 0 && offsets[2] === bounds[2] && offsets[3] === bounds[3];
}

function _isDefaultPageFormat(format) {
  return String(format || '').toUpperCase() === 'RGBA8888';
}

function _isDefaultPageFilter(filter) {
  const f = String(filter || '').replace(/\s+/g, '').toLowerCase();
  return f === 'nearest,nearest';
}

function _isDefaultPageRepeat(repeat) {
  return String(repeat || '').toLowerCase() === 'none';
}

/**
 * Rebuild atlas text with updated bounds/offsets for specific regions.
 * updatedRegions: { name: [[x,y,w,h], offsets|null, rotateVal] }
 */
export function updateAtlasText(atlasText, newSize, updatedRegions, targetPage = null) {
  const lines = atlasText.split('\n');
  const result = [];
  let currentRegion = null;
  let currentPage = null;
  let inPageHeader = false;
  let rotateWritten = false;
  let offsetsWritten = false;

  function flushPendingRotate() {
    if (!currentRegion || !(currentRegion in updatedRegions) || rotateWritten) return;
    const [, , rv] = updatedRegions[currentRegion];
    const rs = _formatRotate(rv);
    if (rs !== null) result.push(`  rotate: ${rs}`);
  }

  function flushPendingOffsets() {
    if (!currentRegion || !(currentRegion in updatedRegions) || offsetsWritten) return;
    const [, off] = updatedRegions[currentRegion];
    if (off) result.push(`  offsets: ${off[0]}, ${off[1]}, ${off[2]}, ${off[3]}`);
  }

  for (const line of lines) {
    const s = line.trim();

    if (/\.png$/i.test(s)) {
      flushPendingOffsets();
      flushPendingRotate();
      result.push(line);
      currentPage = s;
      inPageHeader = true;
      currentRegion = null;
      rotateWritten = false;
      offsetsWritten = false;
      continue;
    }

    if (inPageHeader) {
      if (s.startsWith('size:')) {
        if (!targetPage || currentPage === targetPage) {
          result.push(`size: ${newSize[0]},${newSize[1]}`);
        } else {
          result.push(line);
        }
        continue;
      }
      if (!s.includes(':') && s) inPageHeader = false;
    }

    if (!s.includes(':') && s && !/\.png$/i.test(s)) {
      flushPendingOffsets();
      flushPendingRotate();
      currentRegion = s;
      rotateWritten = false;
      offsetsWritten = false;
      result.push(line);
      continue;
    }

    if (currentRegion && currentRegion in updatedRegions) {
      const [nb, no, rv] = updatedRegions[currentRegion];
      if (s.startsWith('bounds:')) {
        result.push(`  bounds: ${nb[0]}, ${nb[1]}, ${nb[2]}, ${nb[3]}`);
        continue;
      }
      if (s.startsWith('offsets:')) {
        if (no) result.push(`  offsets: ${no[0]}, ${no[1]}, ${no[2]}, ${no[3]}`);
        offsetsWritten = true;
        continue;
      }
      if (s.startsWith('rotate:')) {
        const rs = _formatRotate(rv);
        if (rs !== null) result.push(`  rotate: ${rs}`);
        rotateWritten = true;
        continue;
      }
    }

    result.push(line);
  }

  flushPendingOffsets();
  flushPendingRotate();
  return result.join('\n');
}

/** Build a complete atlas text from scratch. */
export function rebuildAtlasText(pageInfo, newSize, regionNames, regionData) {
  const lines = [pageInfo.page || 'atlas.png', `size: ${newSize[0]},${newSize[1]}`];
  if (!_isDefaultPageFormat(pageInfo.format)) lines.push(`format: ${pageInfo.format}`);
  if (!_isDefaultPageFilter(pageInfo.filter)) lines.push(`filter: ${pageInfo.filter}`);
  if (!_isDefaultPageRepeat(pageInfo.repeat)) lines.push(`repeat: ${pageInfo.repeat}`);
  if (pageInfo.pma === true) lines.push('pma: true');

  for (const name of regionNames) {
    if (!(name in regionData)) continue;
    const entry = regionData[name];
    const [bounds, offsets, rv, meta = {}] = Array.isArray(entry)
      ? entry
      : [entry.bounds, entry.offsets, entry.rotate, entry.meta || {}];

    const atlasName = meta.atlasName || meta.name || name;
    lines.push(atlasName);
    if (Number.isFinite(meta.index) && meta.index !== -1) lines.push(`  index: ${meta.index}`);
    const rs = _formatRotate(rv);
    if (rs) lines.push(`  rotate: ${rs}`);
    lines.push(`  bounds: ${bounds[0]}, ${bounds[1]}, ${bounds[2]}, ${bounds[3]}`);
    if (offsets && !_isDefaultOffsets(offsets, bounds)) {
      lines.push(`  offsets: ${offsets[0]}, ${offsets[1]}, ${offsets[2]}, ${offsets[3]}`);
    }
    if (Array.isArray(meta.split) && meta.split.length >= 4) lines.push(`  split: ${meta.split.join(', ')}`);
    if (Array.isArray(meta.pad) && meta.pad.length >= 4) lines.push(`  pad: ${meta.pad.join(', ')}`);
    if (Array.isArray(meta.extraPairs)) {
      for (const pair of meta.extraPairs) {
        if (!pair || !pair.key) continue;
        const vals = Array.isArray(pair.values) ? pair.values : [];
        lines.push(`  ${pair.key}: ${vals.join(', ')}`);
      }
    }
  }
  return lines.join('\n');
}

// ─── Placement strategy ──────────────────────────────────────────────────────

function _findBestPlacement(baseW, baseH, modW, modH) {
  const rotW = modH, rotH = modW;
  const candidates = [
    { label: 'right',         canvasW: baseW + modW,         canvasH: Math.max(baseH, modH), pasteX: baseW, pasteY: 0,     rotated: false },
    { label: 'right+rotated', canvasW: baseW + rotW,         canvasH: Math.max(baseH, rotH), pasteX: baseW, pasteY: 0,     rotated: true  },
    { label: 'below',         canvasW: Math.max(baseW, modW), canvasH: baseH + modH,          pasteX: 0,     pasteY: baseH, rotated: false },
    { label: 'below+rotated', canvasW: Math.max(baseW, rotW), canvasH: baseH + rotH,          pasteX: 0,     pasteY: baseH, rotated: true  },
  ];
  return candidates.reduce((best, c) => c.canvasW * c.canvasH < best.canvasW * best.canvasH ? c : best);
}

// ─── Shelf packing ───────────────────────────────────────────────────────────

function _shelfPack(items) {
  // items: [{ name, w, h }]
  if (items.length === 0) return { canvasW: 0, canvasH: 0, placements: [] };

  function packWithWidth(rects, stripW, allowRotate) {
    const sorted = [...rects].sort((a, b) => Math.max(b.w, b.h) - Math.max(a.w, a.h));
    const placements = [];
    let shelfY = 0, shelfH = 0, cursorX = 0, usedW = 0;

    for (const { name, w, h } of sorted) {
      let pw = w, ph = h, rotated = false;
      if (allowRotate) {
        if (shelfH > 0) {
          const wasteA = h > shelfH ? h - shelfH : 0;
          const wasteB = w > shelfH ? w - shelfH : 0;
          if (wasteB < wasteA) { pw = h; ph = w; rotated = true; }
        } else if (h > w) { pw = h; ph = w; rotated = true; }
      }
      if (cursorX + pw > stripW && cursorX > 0) { shelfY += shelfH; cursorX = 0; shelfH = 0; }
      placements.push({ name, x: cursorX, y: shelfY, pw, ph, rotated });
      cursorX += pw;
      usedW = Math.max(usedW, cursorX);
      shelfH = Math.max(shelfH, ph);
    }
    return { usedW, canvasH: shelfY + shelfH, placements };
  }

  const maxSingle = Math.max(...items.map(i => Math.max(i.w, i.h)));
  const totalArea = items.reduce((s, i) => s + i.w * i.h, 0);
  const sqrtArea = Math.floor(Math.sqrt(totalArea));
  const totalW = items.reduce((s, i) => s + Math.max(i.w, i.h), 0);

  const candidateWidths = new Set([
    maxSingle, totalW,
    Math.max(maxSingle, sqrtArea),
    Math.max(maxSingle, Math.floor(sqrtArea * 0.8)),
    Math.max(maxSingle, Math.floor(sqrtArea * 1.2)),
    Math.max(maxSingle, Math.floor(sqrtArea * 1.5)),
    Math.max(maxSingle, Math.floor(sqrtArea * 2.0)),
    ...Array.from({ length: 5 }, (_, i) => maxSingle * (i + 1)),
  ]);

  let best = null, bestArea = Infinity;
  for (const stripW of [...candidateWidths].sort((a, b) => a - b)) {
    for (const allowRot of [false, true]) {
      const { usedW, canvasH, placements } = packWithWidth(items, stripW, allowRot);
      const area = usedW * canvasH;
      if (area < bestArea) { bestArea = area; best = { canvasW: usedW, canvasH, placements }; }
    }
  }
  return best;
}

// ─── Pixel hashing ───────────────────────────────────────────────────────────

async function _canvasHash(canvas) {
  const ctx = canvas.getContext('2d');
  const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
  try {
    const buf = await crypto.subtle.digest('SHA-256', data.buffer);
    return Array.from(new Uint8Array(buf)).map(b => b.toString(16).padStart(2, '0')).join('');
  } catch (_) {
    // Fallback: djb2
    let h = 5381;
    for (let i = 0; i < data.length; i++) h = (Math.imul(h, 31) + data[i]) | 0;
    return `${canvas.width}x${canvas.height}_${(h >>> 0).toString(16)}`;
  }
}

// ─── AtlasModifier class ─────────────────────────────────────────────────────

export class AtlasModifier {
  /**
   * @param {string} atlasText  Spine-format atlas text (already auto-converted)
   * @param {string} atlasFilename  Base filename of the atlas (e.g. "hero.atlas")
   * @param {HTMLImageElement|HTMLCanvasElement} baseImage  First page image
   */
  constructor(atlasText, atlasFilename, baseImage, targetPage = null) {
    this.atlasFilename = atlasFilename;
    this.baseCanvas = _toCanvas(baseImage);
    this.targetPage = targetPage;
    this.atlasText = this._scaleAtlasText(atlasText);
    const { regionNames, regions } = parseAtlas(this.atlasText);
    if (this.targetPage) {
      this.regionNames = regionNames.filter(name => regions[name] && regions[name].page === this.targetPage);
      this.regions = {};
      for (const name of this.regionNames) {
        this.regions[name] = regions[name];
      }
    } else {
      this.regionNames = regionNames;
      this.regions = regions;
    }
  }

  _scaleAtlasText(atlasText) {
    const processor = new AtlasProcessor(atlasText);
    const page = this.targetPage
      ? processor.pages.find(p => p.filename === this.targetPage)
      : processor.pages[0];
    if (!page) return atlasText;
    const atlasW = page.width;
    const atlasH = page.height;
    if (atlasW === 0 || atlasH === 0) return atlasText;
    const realW = this.baseCanvas.width;
    const realH = this.baseCanvas.height;
    if (realW === atlasW && realH === atlasH) return atlasText;

    const sx = realW / atlasW, sy = realH / atlasH;
    const updated = {};
    for (const [name, info] of Object.entries(processor.regions)) {
      if (this.targetPage && info.pageFilename !== this.targetPage) continue;
      const x = info.x;
      const y = info.y;
      const w = info.w;
      const h = info.h;
      const nb = [Math.round(x * sx), Math.round(y * sy), Math.round(w * sx), Math.round(h * sy)];
      let no = null;
      if (info.offsets) {
        const [ox, oy, ow, oh] = info.offsets;
        no = [Math.round(ox * sx), Math.round(oy * sy), Math.round(ow * sx), Math.round(oh * sy)];
      }
      updated[name] = [nb, no, info.rotate];
    }
    return updateAtlasText(atlasText, [realW, realH], updated, this.targetPage);
  }

  /**
   * Merge a mod image (canvas/img) into the atlas for the selected regions.
   * Returns { mergedCanvas, atlasText }.
   */
  mergeModImage(modImage, selectedRegions) {
    if (!selectedRegions || selectedRegions.length === 0)
      throw new Error('No regions selected for modification');

    const modCanvas = _toCanvas(modImage);
    const baseW = this.baseCanvas.width, baseH = this.baseCanvas.height;
    let modW = modCanvas.width, modH = modCanvas.height;

    // Determine original canvas dimensions from offsets of first region
    let origCanvasW = modW, origCanvasH = modH;
    const firstRegion = this.regions[selectedRegions[0]];
    if (firstRegion && firstRegion.offsets) {
      origCanvasW = firstRegion.offsets[2];
      origCanvasH = firstRegion.offsets[3];
    }

    // Detect proportional scale (e.g. user supplied 2× mod)
    if (origCanvasW > 0 && origCanvasH > 0 && (modW !== origCanvasW || modH !== origCanvasH)) {
      const ratioW = modW / origCanvasW, ratioH = modH / origCanvasH;
      if (Math.abs(ratioW - ratioH) < 0.05 && !(0.95 < ratioW && ratioW < 1.05)) {
        const scale = (ratioW + ratioH) / 2;
        origCanvasW = Math.round(origCanvasW * scale);
        origCanvasH = Math.round(origCanvasH * scale);
      }
    }

    // Pad mod image to canvas size if needed
    let finalMod = modCanvas;
    if (modW !== origCanvasW || modH !== origCanvasH) {
      finalMod = document.createElement('canvas');
      finalMod.width = origCanvasW;
      finalMod.height = origCanvasH;
      finalMod.getContext('2d').drawImage(modCanvas, 0, origCanvasH - modH);
      modW = origCanvasW; modH = origCanvasH;
    }

    const best = _findBestPlacement(baseW, baseH, modW, modH);

    // Rotate mod if best strategy requires it (PIL ROTATE_90 = 90° CCW)
    let pastedMod = finalMod;
    if (best.rotated) {
      pastedMod = document.createElement('canvas');
      pastedMod.width = modH; // after 90° CCW: width=oldHeight
      pastedMod.height = modW;
      const rCtx = pastedMod.getContext('2d');
      rCtx.translate(0, modW);
      rCtx.rotate(-Math.PI / 2);
      rCtx.drawImage(finalMod, 0, 0);
    }

    // Create merged canvas
    const merged = document.createElement('canvas');
    merged.width = best.canvasW; merged.height = best.canvasH;
    const ctx = merged.getContext('2d');
    ctx.drawImage(this.baseCanvas, 0, 0);
    ctx.drawImage(pastedMod, best.pasteX, best.pasteY);

    // Build atlas update data
    // Bounds always store ORIGINAL (pre-rotation) dimensions
    const rotateVal = best.rotated ? 90 : 0;
    const updatedRegions = {};
    for (const name of selectedRegions) {
      updatedRegions[name] = [[best.pasteX, best.pasteY, modW, modH], null, rotateVal];
    }
    const newAtlasText = updateAtlasText(this.atlasText, [best.canvasW, best.canvasH], updatedRegions, this.targetPage);

    return { mergedCanvas: merged, atlasText: newAtlasText };
  }

  _extractRawSprite(imageCanvas, region) {
    const [x, y, w, h] = region.bounds;
    return AtlasProcessor.cropAndRotate(imageCanvas, x, y, w, h, region.rotate);
  }

  /**
   * Repack all regions from mergedCanvas into a compact atlas.
   * Returns { canvas, atlasText }.
   */
  async repack(mergedCanvas, atlasText) {
    const parsed = parseAtlas(atlasText);
    let { pageInfo, regionNames, regions } = parsed;

    if (this.targetPage) {
      const page = (new AtlasProcessor(atlasText)).pages.find(p => p.filename === this.targetPage);
      if (page) {
        pageInfo = {
          page: page.filename,
          size: `${page.width},${page.height}`,
          format: page.format,
          filter: `${page.filter[0]}, ${page.filter[1]}`,
          repeat: page.repeat,
        };
      }
      regionNames = regionNames.filter(name => regions[name] && regions[name].page === this.targetPage);
      const filteredRegions = {};
      for (const name of regionNames) filteredRegions[name] = regions[name];
      regions = filteredRegions;
    }

    // 1. Extract raw sprites
    const sprites = {};
    for (const [name, info] of Object.entries(regions)) {
      sprites[name] = this._extractRawSprite(mergedCanvas, info);
    }

    // 2. Deduplicate by pixel hash
    const hashToCanonical = {}, canonicalMap = {};
    for (const name of regionNames) {
      if (!(name in sprites)) continue;
      const hash = await _canvasHash(sprites[name]);
      if (hash in hashToCanonical) {
        canonicalMap[name] = hashToCanonical[hash];
      } else {
        hashToCanonical[hash] = name;
        canonicalMap[name] = name;
      }
    }
    const uniqueNames = Object.values(hashToCanonical);

    // 3. Bin-pack unique sprites
    const packItems = uniqueNames.map(n => ({ name: n, w: sprites[n].width, h: sprites[n].height }));
    const { canvasW, canvasH, placements } = _shelfPack(packItems);

    // 4. Build placement lookup
    const placementMap = {};
    for (const p of placements) placementMap[p.name] = p;

    // 5. Paste sprites onto new canvas
    const canvas = document.createElement('canvas');
    canvas.width = canvasW; canvas.height = canvasH;
    const ctx = canvas.getContext('2d');

    for (const name of uniqueNames) {
      const { x, y, rotated } = placementMap[name];
      const sprite = sprites[name];
      if (rotated) {
        // PIL ROTATE_90 = 90° CCW
        const rotCanvas = document.createElement('canvas');
        rotCanvas.width = sprite.height; rotCanvas.height = sprite.width;
        const rCtx = rotCanvas.getContext('2d');
        rCtx.translate(0, sprite.width);
        rCtx.rotate(-Math.PI / 2);
        rCtx.drawImage(sprite, 0, 0);
        ctx.drawImage(rotCanvas, x, y);
      } else {
        ctx.drawImage(sprite, x, y);
      }
    }

    // 6. Build region data for atlas text
    const regionData = {};
    for (const name of regionNames) {
      if (!(name in canonicalMap)) continue;
      const canonical = canonicalMap[name];
      const { x, y, rotated } = placementMap[canonical];
      const orig = sprites[name];
      const bounds = [x, y, orig.width, orig.height];
      regionData[name] = [
        bounds,
        regions[name].offsets,
        rotated ? 90 : 0,
        {
          atlasName: regions[name].atlasName || regions[name].name,
          index: regions[name].index,
          split: regions[name].split,
          pad: regions[name].pad,
          extraPairs: regions[name].extraPairs,
        },
      ];
    }

    const newAtlasText = rebuildAtlasText(pageInfo, [canvasW, canvasH], regionNames, regionData);
    return { canvas, atlasText: newAtlasText };
  }
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

function _toCanvas(img) {
  if (img instanceof HTMLCanvasElement) return img;
  const canvas = document.createElement('canvas');
  canvas.width = img.naturalWidth || img.width;
  canvas.height = img.naturalHeight || img.height;
  canvas.getContext('2d').drawImage(img, 0, 0);
  return canvas;
}
