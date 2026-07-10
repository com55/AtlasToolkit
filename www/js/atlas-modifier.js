/**
 * atlas-modifier.js
 * Port of atlas_modifier.py using Canvas API.
 */

import { AtlasProcessor } from './atlas-extracter.js';
import { AtlasDocument } from './atlas-document.js';
import { cropAndRotate, roundHalfEven, roundUpToMultiple } from './core-region-ops.js';

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
 *
 * Re-homed onto AtlasDocument: parse -> apply the new page size to the target
 * page(s) -> withUpdates(regions) -> serialize. The target-page size scoping
 * (only the matching page, or every page when targetPage is null) replicates
 * the original line-patcher's `!targetPage || currentPage === targetPage`
 * rule; the updatedRegions map shape ({name: [bounds, offsets|null, rotate]})
 * already matches AtlasDocument.withUpdates. Output is canonically serialized
 * (see AtlasDocument.serialize) rather than byte-preserved — the text is only
 * ever re-parsed downstream, never diffed.
 */
export function updateAtlasText(atlasText, newSize, updatedRegions, targetPage = null) {
  const doc = AtlasDocument.parse(atlasText);
  for (const page of doc.pages) {
    if (!targetPage || page.filename === targetPage) {
      page.size = [newSize[0], newSize[1]];
    }
  }
  return doc.withUpdates(updatedRegions).serialize();
}

/**
 * Build a complete atlas text from scratch.
 * Re-homed onto AtlasDocument.fromRebuildArgs + serialize. The only bridging
 * needed is extraPairs shape: this pipeline carries them as {key, values}
 * objects, while fromRebuildArgs expects [key, values] tuples.
 */
export function rebuildAtlasText(pageInfo, newSize, regionNames, regionData) {
  const normalized = {};
  for (const name of regionNames) {
    if (!(name in regionData)) continue;
    const entry = regionData[name];
    const [bounds, offsets, rv, meta = {}] = Array.isArray(entry)
      ? entry
      : [entry.bounds, entry.offsets, entry.rotate, entry.meta || {}];

    const extraPairs = Array.isArray(meta.extraPairs)
      ? meta.extraPairs
          .filter(pair => pair && pair.key)
          .map(pair => [pair.key, Array.isArray(pair.values) ? pair.values : []])
      : [];

    normalized[name] = [
      bounds,
      offsets,
      rv,
      {
        atlasName: meta.atlasName || meta.name || name,
        index: meta.index,
        split: meta.split,
        pad: meta.pad,
        extraPairs,
      },
    ];
  }
  return AtlasDocument.fromRebuildArgs(pageInfo, [newSize[0], newSize[1]], regionNames, normalized).serialize();
}

// ─── Placement strategy ──────────────────────────────────────────────────────

/**
 * Choose the smallest-area placement of a mod image against the base canvas.
 * Port of modifier.py::_find_best_placement (allow_rotate keyword). When
 * allowRotate is false (shared-canvas mods), rotated candidates are dropped so
 * a full-canvas replacement is never stored rotated.
 */
export function findBestPlacement(baseW, baseH, modW, modH, allowRotate = true) {
  const rotW = modH, rotH = modW;
  let candidates = [
    { label: 'right',         canvasW: baseW + modW,         canvasH: Math.max(baseH, modH), pasteX: baseW, pasteY: 0,     rotated: false },
    { label: 'right+rotated', canvasW: baseW + rotW,         canvasH: Math.max(baseH, rotH), pasteX: baseW, pasteY: 0,     rotated: true  },
    { label: 'below',         canvasW: Math.max(baseW, modW), canvasH: baseH + modH,          pasteX: 0,     pasteY: baseH, rotated: false },
    { label: 'below+rotated', canvasW: Math.max(baseW, rotW), canvasH: baseH + rotH,          pasteX: 0,     pasteY: baseH, rotated: true  },
  ];
  if (!allowRotate) candidates = candidates.filter(c => !c.rotated);
  const best = candidates.reduce((best, c) => c.canvasW * c.canvasH < best.canvasW * best.canvasH ? c : best);
  return { ...best, canvasW: roundUpToMultiple(best.canvasW), canvasH: roundUpToMultiple(best.canvasH) };
}

// ─── Mod-canvas resolution (pure, testable without a DOM) ─────────────────────

/**
 * True when mod dimensions match a canvas within rounding tolerance.
 * Port of modifier.py::_canvas_size_match — tolerance is
 * max(2, roundHalfEven(canvasDim * 0.02)) pixels per axis.
 */
export function canvasSizeMatch(modW, modH, canvasW, canvasH, tolerance = 0.02) {
  if (canvasW <= 0 || canvasH <= 0) return false;
  const dw = Math.abs(modW - canvasW);
  const dh = Math.abs(modH - canvasH);
  return dw <= Math.max(2, roundHalfEven(canvasW * tolerance))
      && dh <= Math.max(2, roundHalfEven(canvasH * tolerance));
}

/**
 * Strict shared-canvas check. Port of modifier.py::_selected_share_canvas.
 * @param {Array<?number[]>} selectedOffsets  per selected region, its offsets
 *   [left,bottom,origW,origH] or null — in selection order.
 * Returns false if ANY selected region lacks offsets (differs from the loose
 * `sharedCanvas` inside resolveModCanvas, which only considers regions that
 * happen to have offsets).
 */
export function selectedShareCanvas(selectedOffsets) {
  const sizes = new Set();
  for (const o of selectedOffsets) {
    if (!o) return false;
    sizes.add(`${o[2]},${o[3]}`);
  }
  return sizes.size === 1 && selectedOffsets.length > 1;
}

/**
 * Resolve the logical canvas size + padding anchor for a mod image.
 * Port of modifier.py::_resolve_mod_canvas. Steps, in Python's exact order:
 *   1. collect canvas sizes + anchor across ALL selected regions with offsets
 *   2. proportional-scale detection (5% ratio tolerance)
 *   3. is_full_canvas = sharedCanvas || canvasSizeMatch(...)  (on post-scale dims)
 *   4. if full-canvas: override canvas to mod dims and zero the anchor
 *
 * @param {Array<?number[]>} selectedOffsets  per selected region, offsets or null
 *   (in selection order); length is the FULL selection count.
 * @returns {{origCanvasW,origCanvasH,baseOrigW,baseOrigH,offX,offY,isFullCanvas}}
 */
/**
 * Pick the padding-anchor offsets: the [offX,offY] of the region minimising the
 * lexicographic tuple (offX+offY, offX, offY). Port of Python's
 * `min(regions_with_offsets, key=lambda r: (o[0]+o[1], o[0], o[1]))`.
 * @param {number[][]} offsetsWithValues  non-null offsets arrays, selection order
 * @returns {[number,number]}
 */
export function pickAnchorOffsets(offsetsWithValues) {
  let anchor = offsetsWithValues[0];
  for (const o of offsetsWithValues) {
    const ax = o[0] + o[1], bx = anchor[0] + anchor[1];
    if (ax < bx
      || (ax === bx && (o[0] < anchor[0]
      || (o[0] === anchor[0] && o[1] < anchor[1])))) {
      anchor = o;
    }
  }
  return [anchor[0], anchor[1]];
}

export function resolveModCanvas(selectedOffsets, modW, modH) {
  const withOffsets = selectedOffsets.filter(Boolean); // in selection order
  const numSelected = selectedOffsets.length;

  let baseOrigW, baseOrigH, offX, offY, origCanvasW, origCanvasH;
  if (withOffsets.length > 0) {
    // Canvas-size tie-break: DEVIATION from Python's `next(iter(canvas_sizes))`,
    // which is hash-order-accidental. When the selection disagrees on canvas
    // size we resolve deterministically to the FIRST-selected-region-with-
    // offsets' canvas size (a resolved product decision, not a faithful port).
    baseOrigW = withOffsets[0][2];
    baseOrigH = withOffsets[0][3];
    // Anchor: minimal (offX+offY, offX, offY) tuple. NOTE this may be a
    // DIFFERENT region than the canvas-size source above — Python decouples them.
    // (In practice isFullCanvas below is always true, so these get zeroed — the
    // anchor/padding math is inert, matching the real Python's behaviour.)
    [offX, offY] = pickAnchorOffsets(withOffsets);
    origCanvasW = baseOrigW; origCanvasH = baseOrigH;
  } else {
    baseOrigW = modW; baseOrigH = modH;
    offX = 0; offY = 0;
    origCanvasW = modW; origCanvasH = modH;
  }

  // sharedCanvas (loose): only considers regions that HAVE offsets.
  const sizeSet = new Set(withOffsets.map(o => `${o[2]},${o[3]}`));
  const sharedCanvas = sizeSet.size === 1 && numSelected > 1;

  // Proportional-scale detection (e.g. user supplied a 2× mod).
  if (origCanvasW > 0 && origCanvasH > 0 && (modW !== origCanvasW || modH !== origCanvasH)) {
    const ratioW = modW / origCanvasW, ratioH = modH / origCanvasH;
    if (Math.abs(ratioW - ratioH) < 0.05 && !(0.95 < ratioW && ratioW < 1.05)) {
      const scale = (ratioW + ratioH) / 2;
      origCanvasW = roundHalfEven(origCanvasW * scale);
      origCanvasH = roundHalfEven(origCanvasH * scale);
    } else {
      origCanvasW = modW;
      origCanvasH = modH;
    }
  }

  const isFullCanvas = sharedCanvas || canvasSizeMatch(modW, modH, origCanvasW, origCanvasH);
  if (isFullCanvas) {
    origCanvasW = modW; origCanvasH = modH;
    offX = 0; offY = 0;
  }

  return { origCanvasW, origCanvasH, baseOrigW, baseOrigH, offX, offY, isFullCanvas };
}

/**
 * Offset value emitted for a region in a repacked single-page atlas.
 * Port of the offset branch in repacker.py::repack_from_sprites:
 *   full-canvas region → (0,0,packedW,packedH) (default; serializer omits it)
 *   otherwise          → its pristine offsets, preserved verbatim.
 */
export function repackOffsetsForRegion(name, fullCanvasRegions, pristineOffsets, packedW, packedH) {
  return (fullCanvasRegions && fullCanvasRegions.has(name))
    ? [0, 0, packedW, packedH]
    : pristineOffsets;
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
  return { ...best, canvasW: roundUpToMultiple(best.canvasW), canvasH: roundUpToMultiple(best.canvasH) };
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
      const nb = [roundHalfEven(x * sx), roundHalfEven(y * sy), roundHalfEven(w * sx), roundHalfEven(h * sy)];
      let no = null;
      if (info.offsets) {
        const [ox, oy, ow, oh] = info.offsets;
        no = [roundHalfEven(ox * sx), roundHalfEven(oy * sy), roundHalfEven(ow * sx), roundHalfEven(oh * sy)];
      }
      updated[name] = [nb, no, info.rotate];
    }
    return updateAtlasText(atlasText, [realW, realH], updated, this.targetPage);
  }

  // ─── Mod-image resolution/preparation (per-region offset math) ─────────────

  /** Build the selection's offsets list (in order) and resolve its canvas. */
  _resolveModCanvas(selectedRegions, modW, modH) {
    const list = selectedRegions.map(n => {
      const r = this.regions[n];
      return r && r.offsets ? r.offsets : null;
    });
    return resolveModCanvas(list, modW, modH);
  }

  /** Strict shared-canvas check across the selection (any missing offsets → false). */
  _selectedShareCanvas(selectedRegions) {
    const list = selectedRegions.map(n => {
      const r = this.regions[n];
      return r && r.offsets ? r.offsets : null;
    });
    return selectedShareCanvas(list);
  }

  /**
   * Load + pad a mod image for the selection's logical canvas.
   * Port of modifier.py::_prepare_mod_image. Returns the padded mod canvas, its
   * (possibly padded) dimensions, and `sharedCanvasMod` — the STRICT flag that
   * both disables rotation on placement and is recorded on the ModBatch to feed
   * fullCanvasRegions. Computed against `this.regions`, so callers must run it
   * on a modifier whose regions carry the offsets they want resolved (the
   * session runs it on a fresh, pristine modifier).
   * @returns {{canvas: HTMLCanvasElement, modW: number, modH: number, sharedCanvasMod: boolean}}
   */
  _prepareModImage(modImage, selectedRegions) {
    const modCanvas = _toCanvas(modImage);
    let modW = modCanvas.width, modH = modCanvas.height;
    const { origCanvasW, origCanvasH, baseOrigW, baseOrigH, offX, offY, isFullCanvas } =
      this._resolveModCanvas(selectedRegions, modW, modH);

    // Pad mod image to canvas size if needed (skipped for full-canvas mods).
    // Place sprite at (left, origH - bottom - spriteH) per Spine offset convention.
    let finalMod = modCanvas;
    if (!isFullCanvas && (modW !== origCanvasW || modH !== origCanvasH)) {
      const scaleX = baseOrigW > 0 ? origCanvasW / baseOrigW : 1;
      const scaleY = baseOrigH > 0 ? origCanvasH / baseOrigH : 1;
      const pasteX = roundHalfEven(offX * scaleX);
      const pasteY = origCanvasH - modH - roundHalfEven(offY * scaleY);
      finalMod = document.createElement('canvas');
      finalMod.width = origCanvasW;
      finalMod.height = origCanvasH;
      finalMod.getContext('2d').drawImage(modCanvas, pasteX, pasteY);
      modW = origCanvasW; modH = origCanvasH;
    }

    const sharedCanvasMod = isFullCanvas && this._selectedShareCanvas(selectedRegions);
    return { canvas: finalMod, modW, modH, sharedCanvasMod };
  }

  /**
   * Merge a mod image (canvas/img) into the atlas for the selected regions.
   * Port of modifier.py::merge_mod_image. Pass a `preparedMod` (from
   * _prepareModImage) to reuse a pristine-resolved padding instead of
   * re-resolving against this modifier's (possibly evolved) regions — the
   * single-page replay path does this; the multi-page path re-prepares.
   * Returns { mergedCanvas, atlasText }.
   */
  mergeModImage(modImage, selectedRegions, preparedMod = null) {
    if (!selectedRegions || selectedRegions.length === 0)
      throw new Error('No regions selected for modification');

    const prep = preparedMod || this._prepareModImage(modImage, selectedRegions);
    const finalMod = prep.canvas;
    const modW = prep.modW, modH = prep.modH;

    const baseW = this.baseCanvas.width, baseH = this.baseCanvas.height;
    const best = findBestPlacement(baseW, baseH, modW, modH, !prep.sharedCanvasMod);

    // Rotate mod if best strategy requires it (PIL ROTATE_90 = 90° CCW)
    let pastedMod = finalMod;
    if (best.rotated) {
      pastedMod = _rotate90CCW(finalMod);
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
      // offsets → null: packed size equals stored size, so the canonical
      // serializer omits the offsets line (Python writes (0,0,w,h), same result).
      updatedRegions[name] = [[best.pasteX, best.pasteY, modW, modH], null, rotateVal];
    }
    const newAtlasText = updateAtlasText(this.atlasText, [best.canvasW, best.canvasH], updatedRegions, this.targetPage);

    return { mergedCanvas: merged, atlasText: newAtlasText };
  }

  _extractRawSprite(imageCanvas, region) {
    const [x, y, w, h] = region.bounds;
    return AtlasProcessor.cropAndRotate(imageCanvas, x, y, w, h, region.rotate);
  }

  /** Parse an atlas text and scope page-info/regions to this.targetPage. */
  _parseScoped(atlasText) {
    let { pageInfo, regionNames, regions } = parseAtlas(atlasText);
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
    return { pageInfo, regionNames, regions };
  }

  /**
   * Deduplicate (always on), shelf-pack, paste, and emit a single-page atlas
   * from an already-built sprite map. Port of repacker.py::repack_from_sprites.
   * @param {Set<string>|null} fullCanvasRegions  regions whose offsets should be
   *   reset to default (0,0,w,h); all others keep their pristine `offsets`.
   */
  async _packAndEmit(sprites, pageInfo, regionNames, regions, fullCanvasRegions) {
    // 1. Deduplicate by pixel hash (single-page repack keeps dedup ON)
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

    // 2. Bin-pack unique sprites
    const packItems = uniqueNames.map(n => ({ name: n, w: sprites[n].width, h: sprites[n].height }));
    const { canvasW, canvasH, placements } = _shelfPack(packItems);

    const placementMap = {};
    for (const p of placements) placementMap[p.name] = p;

    // 3. Paste sprites onto new canvas
    const canvas = document.createElement('canvas');
    canvas.width = canvasW; canvas.height = canvasH;
    const ctx = canvas.getContext('2d');
    for (const name of uniqueNames) {
      const { x, y, rotated } = placementMap[name];
      const sprite = sprites[name];
      if (rotated) {
        ctx.drawImage(_rotate90CCW(sprite), x, y); // PIL ROTATE_90 (90° CCW)
      } else {
        ctx.drawImage(sprite, x, y);
      }
    }

    // 4. Build region data. Offsets: full-canvas regions reset to default,
    //    everyone else preserves their pristine offsets verbatim.
    const full = fullCanvasRegions || null;
    const regionData = {};
    for (const name of regionNames) {
      if (!(name in canonicalMap)) continue;
      const canonical = canonicalMap[name];
      const { x, y, rotated } = placementMap[canonical];
      const orig = sprites[name];
      const bounds = [x, y, orig.width, orig.height];
      regionData[name] = [
        bounds,
        repackOffsetsForRegion(name, full, regions[name].offsets, orig.width, orig.height),
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

  /**
   * Repack all regions from an already-merged canvas into a compact atlas
   * (legacy merge→repack helper). Preserves every region's offsets.
   * Returns { canvas, atlasText }.
   */
  async repack(mergedCanvas, atlasText) {
    const { pageInfo, regionNames, regions } = this._parseScoped(atlasText);
    const sprites = {};
    for (const [name, info] of Object.entries(regions)) {
      sprites[name] = this._extractRawSprite(mergedCanvas, info);
    }
    return this._packAndEmit(sprites, pageInfo, regionNames, regions, null);
  }

  /**
   * Repack from pristine base sprites overlaid with modded sprites.
   * Port of modifier.py::repack_with_modded_sprites: extract every region's raw
   * sprite from this modifier's (pristine) base image, overwrite the modded
   * ones, then pack. Non-modded regions therefore carry their pristine offsets
   * into the output; regions in `fullCanvasRegions` get default offsets. This
   * is what makes the offsets-reset-on-merge vs preserved-on-repack asymmetry
   * expressible — impossible when repacking an already-merged canvas.
   * Returns { canvas, atlasText }.
   */
  async repackWithModdedSprites(moddedSprites, fullCanvasRegions = null) {
    const { pageInfo, regionNames, regions } = this._parseScoped(this.atlasText);
    const sprites = {};
    for (const [name, info] of Object.entries(regions)) {
      sprites[name] = this._extractRawSprite(this.baseCanvas, info);
    }
    for (const [name, sprite] of Object.entries(moddedSprites || {})) {
      if (name in sprites) sprites[name] = _toCanvas(sprite);
    }
    return this._packAndEmit(sprites, pageInfo, regionNames, regions, fullCanvasRegions);
  }
}

// ─── Multi-page repack ───────────────────────────────────────────────────────

/**
 * Repack all sprites across numPages pages using greedy bin-packing.
 * @param {{ [name: string]: HTMLCanvasElement }} allSprites
 * @param {number} numPages
 * @param {Array<{ page: string, format: string, filter: string, repeat: string, pma: boolean }>} pageInfos
 * @param {{ [name: string]: { atlasName: string, index: number, split: number[]|null, pad: number[]|null, extraPairs: Array } }} regionMetas
 * @returns {{ pages: HTMLCanvasElement[], atlasText: string }}
 */
export async function repackMultiPage(allSprites, numPages, pageInfos, regionMetas) {
  const spriteNames = Object.keys(allSprites);
  if (spriteNames.length === 0 || numPages === 0) return { pages: [], atlasText: '' };

  // Greedy first-fit-decreasing: sort by area desc, assign to least-filled group
  const sorted = [...spriteNames].sort((a, b) => {
    const sa = allSprites[a], sb = allSprites[b];
    return sb.width * sb.height - sa.width * sa.height;
  });
  const groups = Array.from({ length: numPages }, () => ({ names: [], area: 0 }));
  for (const name of sorted) {
    const s = allSprites[name];
    const g = groups.reduce((min, cur) => cur.area < min.area ? cur : min);
    g.names.push(name);
    g.area += s.width * s.height;
  }

  const resultPages = [];
  const atlasLines = [];

  for (let i = 0; i < numPages; i++) {
    const group = groups[i];
    const pi = pageInfos[i] || pageInfos[0];

    if (i > 0) atlasLines.push('');
    atlasLines.push(pi.page);

    if (group.names.length === 0) {
      atlasLines.push('size: 1,1');
      if (!_isDefaultPageFormat(pi.format)) atlasLines.push(`format: ${pi.format}`);
      if (!_isDefaultPageFilter(pi.filter)) atlasLines.push(`filter: ${pi.filter}`);
      if (!_isDefaultPageRepeat(pi.repeat)) atlasLines.push(`repeat: ${pi.repeat}`);
      if (pi.pma === true) atlasLines.push('pma: true');
      const blank = document.createElement('canvas');
      blank.width = 1; blank.height = 1;
      resultPages.push(blank);
      continue;
    }

    const items = group.names.map(n => ({ name: n, w: allSprites[n].width, h: allSprites[n].height }));
    const { canvasW, canvasH, placements } = _shelfPack(items);

    const canvas = document.createElement('canvas');
    canvas.width = canvasW; canvas.height = canvasH;
    const ctx = canvas.getContext('2d');

    const placementMap = {};
    for (const p of placements) placementMap[p.name] = p;

    for (const name of group.names) {
      const p = placementMap[name];
      if (!p) continue;
      const sprite = allSprites[name];
      if (p.rotated) {
        ctx.drawImage(_rotate90CCW(sprite), p.x, p.y); // PIL ROTATE_90 (90° CCW)
      } else {
        ctx.drawImage(sprite, p.x, p.y);
      }
    }
    resultPages.push(canvas);

    atlasLines.push(`size: ${canvasW},${canvasH}`);
    if (!_isDefaultPageFormat(pi.format)) atlasLines.push(`format: ${pi.format}`);
    if (!_isDefaultPageFilter(pi.filter)) atlasLines.push(`filter: ${pi.filter}`);
    if (!_isDefaultPageRepeat(pi.repeat)) atlasLines.push(`repeat: ${pi.repeat}`);
    if (pi.pma === true) atlasLines.push('pma: true');

    for (const name of group.names) {
      const p = placementMap[name];
      if (!p) continue;
      const sprite = allSprites[name];
      const meta = regionMetas[name] || {};
      const atlasName = meta.atlasName || name;

      atlasLines.push(atlasName);
      if (Number.isFinite(meta.index) && meta.index !== -1) atlasLines.push(`  index: ${meta.index}`);
      const rs = _formatRotate(p.rotated ? 90 : 0);
      if (rs) atlasLines.push(`  rotate: ${rs}`);
      atlasLines.push(`  bounds: ${p.x}, ${p.y}, ${sprite.width}, ${sprite.height}`);
      if (Array.isArray(meta.split) && meta.split.length >= 4) atlasLines.push(`  split: ${meta.split.join(', ')}`);
      if (Array.isArray(meta.pad) && meta.pad.length >= 4) atlasLines.push(`  pad: ${meta.pad.join(', ')}`);
      if (Array.isArray(meta.extraPairs)) {
        for (const pair of meta.extraPairs) {
          if (!pair || !pair.key) continue;
          atlasLines.push(`  ${pair.key}: ${(pair.values || []).join(', ')}`);
        }
      }
    }
  }

  return { pages: resultPages, atlasText: atlasLines.join('\n') };
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

/**
 * Rotate a whole canvas 90° CCW (PIL ROTATE_90) for packing, via the single
 * rotation seam in core-region-ops. Equivalent to un-rotating a rotate==270
 * region whose footprint is the source canvas.
 */
function _rotate90CCW(src) {
  return cropAndRotate(src, 0, 0, src.height, src.width, 270);
}

function _toCanvas(img) {
  if (img instanceof HTMLCanvasElement) return img;
  const canvas = document.createElement('canvas');
  canvas.width = img.naturalWidth || img.width;
  canvas.height = img.naturalHeight || img.height;
  canvas.getContext('2d').drawImage(img, 0, 0);
  return canvas;
}
