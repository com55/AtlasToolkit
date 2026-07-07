/**
 * atlas-session.js
 * In-memory atlas modify-mode session: pristine-rebuild + ordered mod-batch
 * replay architecture. Port of atlas_toolkit/app/session.py's AtlasSession.
 *
 * The whole point of this module is correctness of sequential mod applies:
 * every merge/repack ALWAYS rebuilds starting from the pristine source atlas
 * text + original page image(s) captured once at load, and replays the full
 * ordered list of mod batches. It never compounds onto a previous merge/repack
 * result. See session.py commit 5a6f6bf.
 */

import { AtlasProcessor, _loadImage } from './atlas-extracter.js';
import { AtlasModifier, repackMultiPage } from './atlas-modifier.js';

/** One mod apply: the region names it targeted plus the (durable) mod image. */
export class ModBatch {
  constructor(names, source) {
    this.names = names;         // array of region names this batch targeted
    this.source = source;       // original mod input (File/Blob/Image/Canvas)
    this.sharedCanvas = false;  // full-canvas flag — inert until Phase B wires
                                // full_canvas_regions into the JS repacker
    this.prepared = null;       // loaded HTMLImageElement/Canvas, reusable for
                                // replay (dropped Files aren't re-readable)
  }
}

export class AtlasSession {
  /**
   * @param {AtlasProcessor} processor  holds the original page image(s); never mutated
   * @param {string} atlasText           pristine atlas text (already auto-converted)
   * @param {string} filename            base atlas filename (e.g. "hero.atlas")
   */
  constructor(processor, atlasText, filename) {
    this.processor = processor;
    this.atlasText = atlasText;
    this.filename = filename;
    this.clearModifyState();
  }

  clearModifyState() {
    this.modBatches = [];        // ordered, oldest first
    this.moddedSprites = {};     // name -> mod canvas/image (latest wins) — repack path
    this.preRepack = null;       // { canvas|null, pages|null, text }  merge result cache
    this.repacked = null;        // { canvas|null, pages|null, text }  repack result cache
    this.active = null;          // currently-displayed merged output
    this.modificationsSaved = false;
    this.modGeneration = 0;      // bumped on every processModImage / toggleRepack
  }

  get isMultiPage() {
    return !!this.processor && this.processor.pages.length > 1;
  }

  hasPendingModifications() {
    return this.modBatches.length > 0 && !this.modificationsSaved;
  }

  markSaved() {
    this.modificationsSaved = true;
  }

  hasMergedOutput() {
    if (!this.active || !this.active.text) return false;
    return !!this.active.canvas || (Array.isArray(this.active.pages) && this.active.pages.length > 0);
  }

  getMergedOutput() {
    return this.active;
  }

  // ─── Cache invalidation ─────────────────────────────────────────────────
  _invalidateMergeCache() { this.preRepack = null; }
  _invalidateRepackCache() { this.repacked = null; }

  // ─── Mod image preparation (overridable seam for tests) ─────────────────
  async _prepareSource(source) {
    return source instanceof File ? await _loadImage(source) : source;
  }

  // ─── Batch registration ─────────────────────────────────────────────────
  async _registerModBatch(source, selectedNames) {
    if (!selectedNames || selectedNames.length === 0) return null;
    const batch = new ModBatch([...selectedNames], source);
    batch.prepared = await this._prepareSource(source);
    for (const name of selectedNames) this.moddedSprites[name] = batch.prepared;
    this.modBatches.push(batch);
    this.modificationsSaved = false;
    return batch;
  }

  _orderSelection(modifier, names) {
    const preferred = names.filter(n => modifier.regions[n]);
    const rest = names.filter(n => !modifier.regions[n]);
    return [...preferred, ...rest];
  }

  _firstPageName() {
    return this.processor.pages.length > 0 ? this.processor.pages[0].filename : null;
  }

  _freshSinglePageModifier() {
    const pageName = this._firstPageName();
    const baseImg = this.processor.getPageImage(pageName);
    if (!baseImg) return null;
    return new AtlasModifier(this.atlasText, this.filename, baseImg, pageName);
  }

  // ─── Single-page rebuilds ────────────────────────────────────────────────
  _rebuildSinglePageMerge() {
    let modifier = this._freshSinglePageModifier();
    if (!modifier) throw new Error('No single-page modifier');
    const pageName = this._firstPageName();
    let canvas = modifier.baseCanvas;
    let text = modifier.atlasText;
    for (const batch of this.modBatches) {
      const ordered = this._orderSelection(modifier, batch.names);
      const res = modifier.mergeModImage(batch.prepared, ordered);
      canvas = res.mergedCanvas;
      text = res.atlasText;
      // Adopt this batch's result so the NEXT batch merges onto it (sequential
      // mods) — but the whole chain still started from pristine above.
      modifier = new AtlasModifier(text, this.filename, canvas, pageName);
    }
    return { canvas, text };
  }

  async _rebuildSinglePageRepack() {
    // Repack the freshly-replayed merge result. Mirrors the existing JS
    // merge->repack pipeline (repack extracts raw sprites from the merged
    // canvas). This differs from session.py's repack_with_modded_sprites but
    // matches the shipped JS AtlasModifier.repack contract, which this task
    // must not change (Phase B territory).
    const { canvas, text } = this._rebuildSinglePageMerge();
    const modifier = new AtlasModifier(text, this.filename, canvas, this._firstPageName());
    const repacked = await modifier.repack(canvas, text);
    return { canvas: repacked.canvas, text: repacked.atlasText };
  }

  // ─── Multi-page rebuilds ─────────────────────────────────────────────────
  _originalPageCanvases() {
    const map = {};
    for (const page of this.processor.pages) {
      const img = this.processor.getPageImage(page.filename);
      map[page.filename] = img ? _toCanvas(img) : null;
    }
    return map;
  }

  _regionsOnPage(names, pageName) {
    return names.filter(n => {
      const r = this.processor.regions[n];
      return r && r.pageFilename === pageName;
    });
  }

  _rebuildMultiPageMerge() {
    // Append-only: for each page touched by a batch, replay only that batch's
    // regions belonging to the page, in order. Untouched pages stay pristine.
    const pageOrder = this.processor.pages.map(p => p.filename);
    const pageImages = this._originalPageCanvases();
    let text = this.atlasText;

    for (const batch of this.modBatches) {
      for (const pageName of pageOrder) {
        if (!pageImages[pageName]) continue;
        const pageNames = this._regionsOnPage(batch.names, pageName);
        if (pageNames.length === 0) continue;
        const modifier = new AtlasModifier(text, this.filename, pageImages[pageName], pageName);
        const ordered = this._orderSelection(modifier, pageNames);
        const res = modifier.mergeModImage(batch.prepared, ordered);
        // mergeModImage with a targetPage only rewrites that page's size line
        // and its regions' bounds in the full text; other pages are untouched.
        text = res.atlasText;
        pageImages[pageName] = res.mergedCanvas;
      }
    }

    const pages = pageOrder.map(p => pageImages[p]).filter(Boolean);
    return { pages, text };
  }

  async _rebuildMultiPageRepack() {
    // Extract every region's raw sprite from the pristine pages, overlay the
    // flattened moddedSprites (latest-mod-per-region wins), then repack across
    // all pages. Mirrors session.py's _rebuild_multi_page_repack.
    const allSprites = {};
    for (const name of Object.keys(this.processor.regions)) {
      const c = this.processor.extractRegion(name);
      if (c) allSprites[name] = c;
    }
    for (const [name, sprite] of Object.entries(this.moddedSprites)) {
      if (name in allSprites) allSprites[name] = sprite;
    }

    const numPages = this.processor.pages.length;
    const pageInfos = this.processor.pages.map(p => ({
      page: p.filename,
      format: p.format,
      filter: `${p.filter[0]}, ${p.filter[1]}`,
      repeat: p.repeat,
      pma: p.pma,
    }));
    const regionMetas = {};
    for (const [name, r] of Object.entries(this.processor.regions)) {
      regionMetas[name] = {
        atlasName: r.atlasName || r.name || name,
        index: Number.isFinite(r.index) ? r.index : -1,
        split: r.split,
        pad: r.pad,
        extraPairs: Array.isArray(r.extraPairs) ? r.extraPairs : [],
      };
    }

    const { pages, atlasText } = await repackMultiPage(allSprites, numPages, pageInfos, regionMetas);
    return { pages, text: atlasText };
  }

  // ─── Active result / payload ─────────────────────────────────────────────
  _setActiveSingle(canvas, text) {
    this.active = { canvas, pages: null, text };
  }

  _setActiveMulti(pages, text) {
    this.active = { canvas: null, pages, text };
  }

  /** Externally-driven active override (used by the "repack all pages" path). */
  setActiveOverride(canvas, text) {
    this._setActiveSingle(canvas, text);
  }

  _buildResult() {
    const a = this.active;
    if (!a) return null;
    const proc = new AtlasProcessor(a.text);
    const regionBounds = {};
    for (const [name, info] of Object.entries(proc.regions)) {
      regionBounds[name] = [info.x, info.y, info.w, info.h, info.rotate];
    }
    if (a.pages) {
      const previewPage = proc.pages.length > 0 ? proc.pages[0].filename : null;
      const image = a.pages.length > 0 ? a.pages[0].toDataURL('image/png') : null;
      return { image, regions: regionBounds, pageCount: a.pages.length, previewPage };
    }
    return { image: a.canvas.toDataURL('image/png'), regions: regionBounds };
  }

  // ─── Public: apply a mod image ───────────────────────────────────────────
  /**
   * Register a new mod batch and rebuild the merged/repacked output from the
   * pristine source, replaying the full ordered batch list. Transactional:
   * a throw restores the pre-call batch/sprite state.
   * @returns {Promise<object|null>} preview payload for the UI, or null.
   */
  async processModImage(source, selectedNames, repack = false) {
    const prevBatches = [...this.modBatches];
    const prevSprites = { ...this.moddedSprites };
    try {
      if ((await this._registerModBatch(source, selectedNames)) === null) return null;
      this.modGeneration++;

      if (this.isMultiPage) {
        if (repack) {
          this._invalidateMergeCache();
          const r = await this._rebuildMultiPageRepack();
          this.repacked = { canvas: null, pages: r.pages, text: r.text };
          this._setActiveMulti(r.pages, r.text);
        } else {
          this._invalidateRepackCache();
          const r = this._rebuildMultiPageMerge();
          this.preRepack = { canvas: null, pages: r.pages, text: r.text };
          this._setActiveMulti(r.pages, r.text);
        }
      } else if (repack) {
        this._invalidateMergeCache();
        const r = await this._rebuildSinglePageRepack();
        this.repacked = { canvas: r.canvas, pages: null, text: r.text };
        this._setActiveSingle(r.canvas, r.text);
      } else {
        this._invalidateRepackCache();
        const r = this._rebuildSinglePageMerge();
        this.preRepack = { canvas: r.canvas, pages: null, text: r.text };
        this._setActiveSingle(r.canvas, r.text);
      }

      return this._buildResult();
    } catch (e) {
      this.modBatches = prevBatches;
      this.moddedSprites = prevSprites;
      throw e;
    }
  }

  // ─── Public: toggle repack on/off ────────────────────────────────────────
  /**
   * Switch the active output between the merge and repack results, lazily
   * rebuilding whichever cache is stale and reusing the other. Does NOT
   * invalidate the opposite cache (both stay valid across a toggle).
   */
  async toggleRepack(repack) {
    if (this.modBatches.length === 0) return null;
    this.modGeneration++;

    if (this.isMultiPage) {
      if (repack) {
        if (!this.repacked) {
          const r = await this._rebuildMultiPageRepack();
          this.repacked = { canvas: null, pages: r.pages, text: r.text };
        }
        this._setActiveMulti(this.repacked.pages, this.repacked.text);
      } else {
        if (!this.preRepack) {
          const r = this._rebuildMultiPageMerge();
          this.preRepack = { canvas: null, pages: r.pages, text: r.text };
        }
        this._setActiveMulti(this.preRepack.pages, this.preRepack.text);
      }
    } else if (repack) {
      if (!this.repacked) {
        const r = await this._rebuildSinglePageRepack();
        this.repacked = { canvas: r.canvas, pages: null, text: r.text };
      }
      this._setActiveSingle(this.repacked.canvas, this.repacked.text);
    } else {
      if (!this.preRepack) {
        const r = this._rebuildSinglePageMerge();
        this.preRepack = { canvas: r.canvas, pages: null, text: r.text };
      }
      this._setActiveSingle(this.preRepack.canvas, this.preRepack.text);
    }

    return this._buildResult();
  }
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

function _toCanvas(img) {
  if (typeof HTMLCanvasElement !== 'undefined' && img instanceof HTMLCanvasElement) return img;
  const canvas = document.createElement('canvas');
  canvas.width = img.naturalWidth || img.width;
  canvas.height = img.naturalHeight || img.height;
  canvas.getContext('2d').drawImage(img, 0, 0);
  return canvas;
}
