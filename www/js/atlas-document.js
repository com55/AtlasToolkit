/**
 * AtlasDocument — single seam for Spine atlas parse -> model -> canonical
 * serialize.
 *
 * This is a line-for-line JS port of `atlas_toolkit/core/document.py` on the
 * `main` branch of this repo (the Python desktop app's document model). That
 * Python file is the normative reference for exact parsing/serialization
 * behavior; see the doc comments below for notes on where this port had to
 * make a JS-specific call.
 *
 * No DOM dependency: usable both via `<script type="module">` in the browser
 * and under plain Node (e.g. for `node --test`).
 */

// Page-level keys with dedicated handling; anything else at page scope goes
// into Page#extraPairs for round-tripping (mirrors Python's
// `_PAGE_KNOWN_KEYS` set).
const PAGE_KNOWN_KEYS = new Set(['size', 'format', 'filter', 'repeat', 'pma']);

/** One sprite entry within a page. */
export class Region {
  /**
   * @param {string} name - deduped internal key (e.g. "arm#2")
   * @param {string} atlasName - literal region name text for output
   * @param {string} pageFilename
   * @param {object} [opts]
   */
  constructor(name, atlasName, pageFilename, opts = {}) {
    this.name = name;
    this.atlasName = atlasName;
    this.pageFilename = pageFilename;
    this.x = opts.x ?? 0;
    this.y = opts.y ?? 0;
    this.w = opts.w ?? 0;
    this.h = opts.h ?? 0;
    this.index = opts.index ?? -1;
    // Raw [left, bottom, originalWidth, originalHeight] or null.
    this.offsets = opts.offsets ?? null;
    // Integer degrees: 0 / 90 / 180 / 270 (not a boolean).
    this.rotate = opts.rotate ?? 0;
    this.split = opts.split ?? null;
    this.pad = opts.pad ?? null;
    // Unknown region keys, preserved verbatim for round-tripping:
    // Array<[key: string, values: string[]]>
    this.extraPairs = opts.extraPairs ?? [];
  }

  /** Packed pre-rotation bounds as [x, y, w, h]. */
  get bounds() {
    return [this.x, this.y, this.w, this.h];
  }

  /** Page filename alias (matches Python's `Region.page` property). */
  get page() {
    return this.pageFilename;
  }

  boundsWithRotate() {
    return [this.x, this.y, this.w, this.h, this.rotate];
  }

  /** Metadata dict for repack / rebuild helpers. */
  toMetaDict() {
    return {
      atlasName: this.atlasName || this.name,
      index: this.index,
      split: this.split,
      pad: this.pad,
      extraPairs: this.extraPairs,
    };
  }

  /** Shallow-value clone (deep-copies mutable array fields). */
  clone() {
    return new Region(this.name, this.atlasName, this.pageFilename, {
      x: this.x,
      y: this.y,
      w: this.w,
      h: this.h,
      index: this.index,
      offsets: this.offsets ? [...this.offsets] : null,
      rotate: this.rotate,
      split: this.split ? [...this.split] : null,
      pad: this.pad ? [...this.pad] : null,
      extraPairs: this.extraPairs.map(([k, v]) => [k, [...v]]),
    });
  }
}

/** One texture page referenced by the atlas. */
export class Page {
  /**
   * @param {string} filename
   * @param {object} [opts]
   */
  constructor(filename, opts = {}) {
    this.filename = filename;
    this.size = opts.size ?? [0, 0];
    this.format = opts.format ?? 'RGBA8888';
    this.filter = opts.filter ?? ['Nearest', 'Nearest'];
    this.repeat = opts.repeat ?? 'none';
    this.pma = opts.pma ?? false;
    // Runtime-only: set when the actually-loaded PNG's pixel size differs
    // from the atlas's declared `size:` line. Never serialized.
    this.scaleX = opts.scaleX ?? 1.0;
    this.scaleY = opts.scaleY ?? 1.0;
    // Unknown page keys, preserved verbatim: Array<[key, values[]]>
    this.extraPairs = opts.extraPairs ?? [];
    /** @type {Region[]} */
    this.regions = opts.regions ?? [];
  }

  clone() {
    return new Page(this.filename, {
      size: [...this.size],
      format: this.format,
      filter: [...this.filter],
      repeat: this.repeat,
      pma: this.pma,
      scaleX: this.scaleX,
      scaleY: this.scaleY,
      extraPairs: this.extraPairs.map(([k, v]) => [k, [...v]]),
      regions: this.regions.map((r) => r.clone()),
    });
  }
}

export class AtlasDocument {
  /** @param {Page[]} [pages] */
  constructor(pages = []) {
    this.pages = pages;
  }

  /**
   * Parse Spine atlas text into an AtlasDocument.
   *
   * Ported directly from `AtlasDocument.parse` in document.py — see that
   * file for the authoritative behavior. Notable specifics preserved here:
   *  - A line ending in exactly ".png" (case-sensitive) starts a new Page.
   *  - Region-name de-duplication (`#2`, `#3`, ...) is recomputed fresh on
   *    every parse and is order-dependent; algorithm lifted from
   *    atlas-extracter.js's `getUniqueRegionKey` (itself equivalent to
   *    Python's `unique_region_key`).
   *  - Malformed/short values for a *known* region key (e.g. `offsets:` with
   *    fewer than 4 values) fall through to `extraPairs` under that same
   *    key name, because the region branch is an if/elif chain ending in a
   *    catch-all `else`. Malformed values for a *known* page key are instead
   *    silently dropped (no extraPairs entry), because the page branch uses
   *    an explicit "not in known-keys" whitelist check instead of a
   *    catch-all else. This asymmetry exists in the Python source and is
   *    intentionally preserved here rather than "fixed".
   *
   * @param {string} text
   * @returns {AtlasDocument}
   */
  static parse(text) {
    const lines = text.split(/\r\n|\r|\n/).map((line) => line.trim());

    /** @type {Page[]} */
    const pages = [];
    const regionNameCounts = new Map();

    /** @type {Page|null} */
    let currentPage = null;
    /** @type {Region|null} */
    let currentRegion = null;

    // Lifted from atlas-extracter.js's getUniqueRegionKey (equivalent to
    // Python's unique_region_key nested function in document.py).
    const uniqueRegionKey = (atlasName) => {
      const next = (regionNameCounts.get(atlasName) || 0) + 1;
      regionNameCounts.set(atlasName, next);
      return next === 1 ? atlasName : `${atlasName}#${next}`;
    };

    for (const line of lines) {
      if (!line) continue;

      if (line.endsWith('.png')) {
        currentPage = new Page(line);
        pages.push(currentPage);
        currentRegion = null;
        continue;
      }

      const colonIdx = line.indexOf(':');
      if (colonIdx !== -1) {
        const key = line.slice(0, colonIdx).trim().toLowerCase();
        const values = line
          .slice(colonIdx + 1)
          .split(',')
          .map((v) => v.trim());

        if (currentRegion !== null) {
          if (key === 'bounds' && values.length >= 4) {
            currentRegion.x = parseInt(values[0], 10);
            currentRegion.y = parseInt(values[1], 10);
            currentRegion.w = parseInt(values[2], 10);
            currentRegion.h = parseInt(values[3], 10);
          } else if (key === 'xy') {
            currentRegion.x = parseInt(values[0], 10);
            currentRegion.y = parseInt(values[1], 10);
          } else if (key === 'size' && currentRegion.w === 0) {
            currentRegion.w = parseInt(values[0], 10);
            currentRegion.h = parseInt(values[1], 10);
          } else if (key === 'rotate') {
            const val = (values[0] || '').toLowerCase();
            if (val === 'true') {
              currentRegion.rotate = 90;
            } else if (val === 'false') {
              currentRegion.rotate = 0;
            } else {
              const n = parseInt(val, 10);
              currentRegion.rotate = Number.isNaN(n) ? 0 : n;
            }
          } else if (key === 'offsets' && values.length >= 4) {
            currentRegion.offsets = values.slice(0, 4).map((v) => parseInt(v, 10));
          } else if (key === 'index') {
            currentRegion.index = parseInt(values[0], 10);
          } else if (key === 'split' && values.length >= 4) {
            currentRegion.split = values.map((v) => parseInt(v, 10));
          } else if (key === 'pad' && values.length >= 4) {
            currentRegion.pad = values.map((v) => parseInt(v, 10));
          } else {
            currentRegion.extraPairs.push([key, values]);
          }
        } else if (currentPage !== null) {
          if (key === 'size') {
            currentPage.size = [parseInt(values[0], 10), parseInt(values[1], 10)];
          } else if (key === 'format') {
            currentPage.format = values[0];
          } else if (key === 'filter' && values.length >= 2) {
            currentPage.filter = [values[0], values[1]];
          } else if (key === 'repeat') {
            currentPage.repeat = values[0];
          } else if (key === 'pma') {
            currentPage.pma = String(values[0]).trim().toLowerCase() === 'true';
          } else if (!PAGE_KNOWN_KEYS.has(key)) {
            currentPage.extraPairs.push([key, values]);
          }
        }
        continue;
      }

      if (currentPage === null) continue;

      const regionKey = uniqueRegionKey(line);
      currentRegion = new Region(regionKey, line, currentPage.filename);
      currentPage.regions.push(currentRegion);
    }

    return new AtlasDocument(pages);
  }

  pageFilenames() {
    return this.pages.map((p) => p.filename);
  }

  regionKeys() {
    const keys = [];
    for (const page of this.pages) {
      for (const region of page.regions) keys.push(region.name);
    }
    return keys;
  }

  regionsByKey() {
    const out = {};
    for (const page of this.pages) {
      for (const region of page.regions) out[region.name] = region;
    }
    return out;
  }

  firstPageInfo() {
    if (this.pages.length === 0) return {};
    const p = this.pages[0];
    return {
      page: p.filename,
      size: `${p.size[0]},${p.size[1]}`,
      format: p.format,
      filter: `${p.filter[0]}, ${p.filter[1]}`,
      repeat: p.repeat,
      pma: Boolean(p.pma),
    };
  }

  /**
   * Return a new AtlasDocument with the given regions' bounds/offsets/rotate
   * replaced (and optionally every page's declared size replaced), leaving
   * everything else preserved. Mirrors `AtlasDocument.with_updates` in
   * document.py, including the fact that unmatched regions/pages are reused
   * by reference rather than deep-cloned (a shallow "dataclasses.replace"
   * pattern) — safe because callers treat the result as a fresh snapshot to
   * serialize, not as something to mutate further.
   *
   * @param {Object<string, [number[], number[]|null, number]>} updatedRegions
   *   Map of region name -> [bounds[4], offsets|null, rotate].
   * @param {number[]|null} [pageSize] - if given, applied to every page.
   * @returns {AtlasDocument}
   */
  withUpdates(updatedRegions, pageSize = null) {
    const newPages = this.pages.map((page) => {
      const newSize = pageSize !== null && pageSize !== undefined ? pageSize : page.size;
      const newRegions = page.regions.map((region) => {
        if (Object.prototype.hasOwnProperty.call(updatedRegions, region.name)) {
          const [bounds, offsets, rotateVal] = updatedRegions[region.name];
          return new Region(region.name, region.atlasName, region.pageFilename, {
            x: bounds[0],
            y: bounds[1],
            w: bounds[2],
            h: bounds[3],
            index: region.index,
            offsets,
            rotate: rotateVal,
            split: region.split,
            pad: region.pad,
            extraPairs: region.extraPairs,
          });
        }
        return region;
      });
      return new Page(page.filename, {
        size: newSize,
        format: page.format,
        filter: page.filter,
        repeat: page.repeat,
        pma: page.pma,
        scaleX: page.scaleX,
        scaleY: page.scaleY,
        extraPairs: page.extraPairs,
        regions: newRegions,
      });
    });
    return new AtlasDocument(newPages);
  }

  /**
   * Build a full fresh single-page AtlasDocument from scratch (used after a
   * complete repack). Mirrors `AtlasDocument.from_rebuild_args` in
   * document.py.
   *
   * @param {object} pageInfo - {page, format, filter, repeat, pma}; any
   *   missing/undefined key falls back to the same default document.py uses.
   * @param {number[]} newSize - [width, height]
   * @param {string[]} regionNames - order in which regions should appear.
   * @param {Object<string, [number[], number[]|null, number, object?]>} regionData
   *   Map of region name -> [bounds[4], offsets|null, rotate, meta?], where
   *   meta may carry {atlasName, index, split, pad, extraPairs}.
   * @returns {AtlasDocument}
   */
  static fromRebuildArgs(pageInfo, newSize, regionNames, regionData) {
    const page = new Page(String(getOr(pageInfo, 'page', 'atlas.png')), {
      size: newSize,
      format: String(getOr(pageInfo, 'format', 'RGBA8888')),
      filter: parseFilter(getOr(pageInfo, 'filter', undefined)),
      repeat: String(getOr(pageInfo, 'repeat', 'none')),
      pma: Boolean(getOr(pageInfo, 'pma', undefined)),
    });

    const regions = [];
    for (const name of regionNames) {
      if (!Object.prototype.hasOwnProperty.call(regionData, name)) continue;
      const entry = regionData[name];
      const bounds = entry[0];
      const offsets = entry[1];
      const rotateVal = entry[2];
      const meta = entry.length > 3 && entry[3] ? entry[3] : {};

      const index = Number.isInteger(meta.index) ? meta.index : -1;
      const split = Array.isArray(meta.split) ? [...meta.split] : null;
      const pad = Array.isArray(meta.pad) ? [...meta.pad] : null;
      const extraPairs = Array.isArray(meta.extraPairs)
        ? meta.extraPairs
            .filter((p) => p && p[0])
            .map((p) => [String(p[0]), (p[1] || []).map((v) => String(v))])
        : [];

      regions.push(
        new Region(name, String(meta.atlasName || meta.name || name), page.filename, {
          x: bounds[0],
          y: bounds[1],
          w: bounds[2],
          h: bounds[3],
          offsets,
          rotate: rotateVal,
          index,
          split,
          pad,
          extraPairs,
        })
      );
    }
    page.regions = regions;
    return new AtlasDocument([page]);
  }

  /**
   * Canonicalizing serializer (not byte-preserving) — see document.py's
   * `serialize`/`_serialize_region` for the authoritative default-detection
   * rules. Reproduced here:
   *  - Omit `format:` when it equals the default (RGBA8888).
   *  - Omit `filter:` when it equals the default (Nearest, Nearest).
   *  - Omit `repeat:` when it equals the default (none).
   *  - Omit `pma:` entirely unless true (never emit `pma: false`).
   *  - Omit region `index:` when it's -1.
   *  - Omit the region `rotate:` line entirely when rotate==0; emit 90 as
   *    "true", 180/270 as their literal numbers.
   *  - Omit `offsets:` whenever offsets are absent OR equal to (0,0,w,h).
   *  - Pages after the first are separated by a blank line; no other
   *    blank-line logic.
   * @returns {string}
   */
  serialize() {
    const lines = [];
    this.pages.forEach((page, pageIdx) => {
      if (pageIdx > 0) lines.push('');
      lines.push(page.filename);
      lines.push(`size: ${page.size[0]},${page.size[1]}`);
      if (!isDefaultPageFormat(page.format)) lines.push(`format: ${page.format}`);
      if (!isDefaultPageFilter(page.filter)) {
        lines.push(`filter: ${page.filter[0]}, ${page.filter[1]}`);
      }
      if (!isDefaultPageRepeat(page.repeat)) lines.push(`repeat: ${page.repeat}`);
      if (page.pma) lines.push('pma: true');
      for (const [key, values] of page.extraPairs) {
        lines.push(`${key}: ${values.join(', ')}`);
      }
      for (const region of page.regions) {
        lines.push(...serializeRegion(region));
      }
    });
    return lines.join('\n');
  }
}

function getOr(obj, key, def) {
  if (obj && Object.prototype.hasOwnProperty.call(obj, key) && obj[key] !== undefined) {
    return obj[key];
  }
  return def;
}

function parseFilter(value) {
  if (Array.isArray(value) && value.length >= 2) {
    return [String(value[0]), String(value[1])];
  }
  if (typeof value === 'string') {
    const parts = value.split(',').map((p) => p.trim());
    if (parts.length >= 2) return [parts[0], parts[1]];
  }
  return ['Nearest', 'Nearest'];
}

function isDefaultOffsets(offsets, bounds) {
  if (!offsets) return true;
  return (
    offsets[0] === 0 &&
    offsets[1] === 0 &&
    offsets[2] === bounds[2] &&
    offsets[3] === bounds[3]
  );
}

function isDefaultPageFormat(fmt) {
  return String(fmt || '').toUpperCase() === 'RGBA8888';
}

function isDefaultPageFilter(flt) {
  if (Array.isArray(flt)) {
    return `${flt[0]},${flt[1]}`.replace(/\s+/g, '').toLowerCase() === 'nearest,nearest';
  }
  return String(flt || '')
    .split(/\s+/)
    .join('')
    .toLowerCase() === 'nearest,nearest';
}

function isDefaultPageRepeat(repeat) {
  return String(repeat || '').toLowerCase() === 'none';
}

function formatRotate(rotateVal) {
  if (rotateVal === 90) return 'true';
  if (rotateVal === 180) return '180';
  if (rotateVal === 270) return '270';
  return null;
}

function serializeRegion(region) {
  const lines = [region.atlasName];
  if (region.index !== -1) lines.push(`  index: ${region.index}`);
  const rotateStr = formatRotate(region.rotate);
  if (rotateStr) lines.push(`  rotate: ${rotateStr}`);
  lines.push(`  bounds: ${region.x}, ${region.y}, ${region.w}, ${region.h}`);
  const bounds = [region.x, region.y, region.w, region.h];
  if (region.offsets && !isDefaultOffsets(region.offsets, bounds)) {
    const o = region.offsets;
    lines.push(`  offsets: ${o[0]}, ${o[1]}, ${o[2]}, ${o[3]}`);
  }
  if (region.split && region.split.length >= 4) {
    lines.push(`  split: ${region.split.join(', ')}`);
  }
  if (region.pad && region.pad.length >= 4) {
    lines.push(`  pad: ${region.pad.join(', ')}`);
  }
  for (const [key, values] of region.extraPairs) {
    lines.push(`  ${key}: ${values.join(', ')}`);
  }
  return lines;
}
