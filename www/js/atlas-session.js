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
    this.sharedCanvas = false;  // isFullCanvas flag from _prepareModImage (NOT
                                // sharedCanvasMod — that one requires >1 region
                                // and would wrongly exclude single-region
                                // full-canvas mods); feeds fullCanvasRegions
    this.loaded = null;         // loaded HTMLImageElement/Canvas, reusable for
                                // replay (dropped Files aren't re-readable) — the
                                // multi-page merge path re-prepares from this
    this.prepared = null;       // single-page prepared mod (padded canvas + dims
                                // + sharedCanvasMod), resolved once from pristine;
                                // null for multi-page (re-prepared per page)
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

  /**
   * Merged/repacked canvas for one page, when a mod has already been applied —
   * old Python engine's `get_modify_page_image()` preferred `merged_pages[index]`
   * over the pristine source once mods existed; the JS port initially always
   * re-read the pristine page (found via parity audit, 2026-08-23), so
   * navigating the page switcher after modifying a region silently reverted to
   * the unmodified image. Index-aligned with `processor.pages`: every rebuild
   * path (`_rebuildMultiPage{Merge,Repack}`) only ever drops a page's slot when
   * that page has no loaded image at all, which the missing-page-image dialog
   * already prevents before modify mode is reachable — so page N's pristine
   * index always matches `active.pages[N]` in practice.
   */
  getActivePageCanvas(pageFilename) {
    if (!this.active || !Array.isArray(this.active.pages)) return null;
    const idx = this.processor.pages.findIndex(p => p.filename === pageFilename);
    if (idx < 0 || idx >= this.active.pages.length) return null;
    return this.active.pages[idx] || null;
  }

  // ─── Cache invalidation ─────────────────────────────────────────────────
  _invalidateMergeCache() { this.preRepack = null; }
  _invalidateRepackCache() { this.repacked = null; }

  // ─── Mod image preparation (overridable seam for tests) ─────────────────
  async _prepareSource(source) {
    return source instanceof File ? await _loadImage(source) : source;
  }

  // ─── Batch registration ─────────────────────────────────────────────────
  /**
   * Record one mod apply. Resolves + pads the mod against the PRISTINE atlas
   * (via _prepareModImage) so `moddedSprites[name]` holds the padded mod image
   * (the same pixels merge would paste) — this is what the repack overlay packs.
   * Mirrors session.py::_register_mod_batch, including the single- vs multi-page
   * split: single-page caches the prepared mod on the batch (merge reuses it,
   * never re-resolving on the evolved canvas); multi-page re-prepares per page.
   */
  async _registerModBatch(source, selectedNames) {
    if (!selectedNames || selectedNames.length === 0) return null;
    const batch = new ModBatch([...selectedNames], source);
    batch.loaded = await this._prepareSource(source);

    if (this.isMultiPage) {
      for (const page of this.processor.pages) {
        const pageImg = this.processor.getPageImage(page.filename);
        if (!pageImg) continue;
        const pageNames = this._regionsOnPage(selectedNames, page.filename);
        if (pageNames.length === 0) continue;
        const modifier = new AtlasModifier(this.atlasText, this.filename, pageImg, page.filename);
        const ordered = this._orderSelection(modifier, pageNames);
        const prep = modifier._prepareModImage(batch.loaded, ordered);
        if (prep.isFullCanvas) batch.sharedCanvas = true;
        for (const name of pageNames) this.moddedSprites[name] = prep.canvas;
      }
    } else {
      const modifier = this._freshSinglePageModifier();
      if (!modifier) return null;
      const ordered = this._orderSelection(modifier, selectedNames);
      const prep = modifier._prepareModImage(batch.loaded, ordered);
      batch.prepared = prep;
      batch.sharedCanvas = prep.isFullCanvas;
      for (const name of selectedNames) this.moddedSprites[name] = prep.canvas;
    }

    this.modBatches.push(batch);
    this.modificationsSaved = false;
    return batch;
  }

  // ─── Modify-mode overlay bounds ─────────────────────────────────────────
  /**
   * Region bounds for the modify-mode overlay: { name: [x, y, w, h, rotate] }.
   * Built per page via AtlasModifier (same construction _registerModBatch's
   * multi-page branch uses) so bounds come out scaled to each loaded page
   * image's real pixel size when it differs from the atlas's declared
   * `size:` — mirrors session.py::build_modify_view reading bounds from
   * self.modifier.regions (AtlasModifier-scaled) rather than the raw parser.
   */
  getModifyRegionBounds() {
    const bounds = {};
    for (const page of this.processor.pages) {
      const pageImg = this.processor.getPageImage(page.filename);
      if (!pageImg) continue;
      const modifier = new AtlasModifier(this.atlasText, this.filename, pageImg, page.filename);
      for (const [name, info] of Object.entries(modifier.regions)) {
        const [x, y, w, h] = info.bounds;
        bounds[name] = [x, y, w, h, info.rotate];
      }
    }
    return bounds;
  }

  // ─── Full-canvas regions (feeds single-page repack offset reset) ──────────
  /** Union of batch.names across batches whose mod filled the shared canvas. */
  _fullCanvasRegions() {
    const regions = new Set();
    for (const batch of this.modBatches) {
      if (batch.sharedCanvas) for (const name of batch.names) regions.add(name);
    }
    return regions;
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
      // Single-page replay reuses the batch's pristine-resolved prepared mod, so
      // padding never re-resolves against the (already-merged) evolving canvas.
      const res = modifier.mergeModImage(batch.loaded, ordered, batch.prepared);
      canvas = res.mergedCanvas;
      text = res.atlasText;
      // Adopt this batch's result so the NEXT batch merges onto it (sequential
      // mods) — but the whole chain still started from pristine above.
      modifier = new AtlasModifier(text, this.filename, canvas, pageName);
    }
    return { canvas, text };
  }

  async _rebuildSinglePageRepack() {
    // Port of session.py::_rebuild_single_page_repack: extract every region's
    // raw sprite from the PRISTINE base, overlay moddedSprites (padded), then
    // pack. Non-modded regions keep their pristine offsets; fullCanvasRegions
    // get default offsets — the offsets-preserved-on-repack asymmetry. (Merge,
    // by contrast, resets every touched region's offsets to default.)
    const modifier = this._freshSinglePageModifier();
    if (!modifier) throw new Error('No single-page modifier');
    const repacked = await modifier.repackWithModdedSprites(
      this.moddedSprites, this._fullCanvasRegions());
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
        // Multi-page merge re-prepares per page from the loaded mod (batch.prepared
        // is null for multi-page); mirrors session.py's per-page merge_mod_image.
        const res = modifier.mergeModImage(batch.loaded, ordered);
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
      if (name in allSprites) allSprites[name] = _toCanvas(sprite);
    }
    // fullCanvasRegions is intentionally NOT threaded here: repackMultiPage emits
    // no offsets line at all, which is identical to Python repack_multi_page's
    // result (both the in-full-canvas (0,0,w,h) and not-in branches serialize to
    // an omitted offsets line). So multi-page repack never preserves pristine
    // offsets — the offsets asymmetry is a single-page-only distinction.

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
