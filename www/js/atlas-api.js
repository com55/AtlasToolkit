/**
 * atlas-api.js
 * Replaces pywebview.api with a pure-JS implementation.
 * Exposes the same async interface that script.js expects.
 */

import { autoConvertAtlas } from './atlas-converter.js';
import { AtlasProcessor } from './atlas-extracter.js';
import { parseAtlas, rebuildAtlasText } from './atlas-modifier.js';
import { platform } from './platform.js';
import { createZip } from './zip.js';
import { AtlasSession } from './atlas-session.js';

// ─── State ────────────────────────────────────────────────────────────────────

let _processor = null;
let _currentAtlasFilename = '';
let _currentAtlasText = '';
let _session = null;               // AtlasSession — owns modify-mode state / mod batches
let _lastSaveHandle = null;
let _currentSkel = null; // { name, blob } | null
let _previewMemo = { key: null, value: null };

const IMAGE_PICKER_ACCEPT = 'image/png,.png';

// ─── Helpers ──────────────────────────────────────────────────────────────────

function _readFileAsText(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = e => resolve(e.target.result);
    reader.onerror = reject;
    reader.readAsText(file);
  });
}

/** Extract page image filenames required by an atlas text. */
function _extractRequiredPages(atlasText) {
  const proc = new AtlasProcessor(atlasText);
  return proc.pages.map(p => p.filename);
}

function _isDesktopFsApiAvailable() {
  return typeof window !== 'undefined'
    && window.isSecureContext
    && typeof window.showSaveFilePicker === 'function'
    && typeof window.showDirectoryPicker === 'function';
}

/** True when running as an installed PWA (standalone window), not a browser tab. */
function _isInstalledPWA() {
  if (typeof window === 'undefined') return false;
  return !!(window.matchMedia?.('(display-mode: standalone)').matches
    || window.matchMedia?.('(display-mode: window-controls-overlay)').matches
    || window.navigator.standalone === true); // iOS Safari
}

/**
 * Whether saving should open a folder picker (installed-PWA File System
 * Access API) rather than downloading a zip. A browser tab — even a
 * Chromium one with the FS API — gets a zip download.
 */
function _useFolderPicker() {
  return _isInstalledPWA() && typeof window.showDirectoryPicker === 'function';
}

/** Base name (no extension) of the loaded atlas, for naming zip downloads. */
function _extractZipBaseName() {
  const base = (_currentAtlasFilename || 'atlas').replace(/\.[^.]+$/, '');
  return base || 'atlas';
}

function _isImageFile(file) {
  if (!file) return false;
  if (typeof file.type === 'string' && file.type === 'image/png') return true;
  return /\.png$/i.test(file.name || '');
}

function _getRegionPageMap() {
  if (!_processor) return {};
  const map = {};
  for (const [name, region] of Object.entries(_processor.regions || {})) {
    map[name] = region.pageFilename || '';
  }
  return map;
}

function _resolveModifyPageFromSelection(selectedNames) {
  if (!_processor || !selectedNames || selectedNames.length === 0) return null;
  const pages = new Set();
  for (const name of selectedNames) {
    const page = AtlasAPI.get_region_page_name(name);
    if (page) pages.add(page);
  }
  if (pages.size !== 1) return null;
  return Array.from(pages)[0];
}

function _packCanvasesSimple(sprites) {
  if (!sprites || sprites.length === 0) {
    const canvas = document.createElement('canvas');
    canvas.width = 1;
    canvas.height = 1;
    return { canvas, placements: {} };
  }

  const totalArea = sprites.reduce((sum, sprite) => sum + sprite.canvas.width * sprite.canvas.height, 0);
  const maxW = Math.max(...sprites.map(sprite => sprite.canvas.width));
  const targetRowW = Math.max(maxW, Math.ceil(Math.sqrt(totalArea)));

  let x = 0;
  let y = 0;
  let rowH = 0;
  let usedW = 0;
  const placements = {};

  for (const sprite of sprites) {
    const w = sprite.canvas.width;
    const h = sprite.canvas.height;
    if (x > 0 && x + w > targetRowW) {
      x = 0;
      y += rowH;
      rowH = 0;
    }
    placements[sprite.name] = { x, y, w, h };
    x += w;
    rowH = Math.max(rowH, h);
    usedW = Math.max(usedW, x);
  }

  const usedH = y + rowH;
  const canvas = document.createElement('canvas');
  canvas.width = Math.max(1, usedW);
  canvas.height = Math.max(1, usedH);
  const ctx = canvas.getContext('2d');

  for (const sprite of sprites) {
    const p = placements[sprite.name];
    ctx.drawImage(sprite.canvas, p.x, p.y);
  }

  return { canvas, placements };
}

function _buildRegionBoundsFromAtlasText(atlasText) {
  const { regions } = parseAtlas(atlasText);
  const regionBounds = {};
  for (const [name, info] of Object.entries(regions)) {
    regionBounds[name] = [...info.bounds, info.rotate];
  }
  return regionBounds;
}

/**
 * "Repack all pages to one" post-step: collapse the session's current merge
 * result into a single page via the legacy _repackAllPagesToSingle path, and
 * install it as the session's active output so save/preview see it.
 * (Its multi-page limitations are pre-existing and scoped to Phase B.)
 */
async function _applyRepackAllOverride() {
  if (!_session) return null;
  const merged = _session.getMergedOutput();
  if (!merged) return null;
  const pageCanvas = merged.canvas || (merged.pages && merged.pages[0]) || null;
  const activePage = _processor && _processor.pages.length > 0 ? _processor.pages[0].filename : null;
  const collapsed = await _repackAllPagesToSingle(pageCanvas, merged.text, activePage);
  if (!collapsed) return null;
  _session.setActiveOverride(collapsed.canvas, collapsed.atlasText);
  return {
    image: collapsed.canvas.toDataURL('image/png'),
    regions: _buildRegionBoundsFromAtlasText(collapsed.atlasText),
  };
}

async function _repackAllPagesToSingle(mergedPageCanvas, atlasText, activePage) {
  if (!_processor || !atlasText) return null;

  const proc = new AtlasProcessor(atlasText);
  const pageImageMap = {};
  for (const page of proc.pages) {
    if (activePage && page.filename === activePage && mergedPageCanvas) {
      pageImageMap[page.filename] = mergedPageCanvas;
    } else {
      pageImageMap[page.filename] = _processor.getPageImage(page.filename);
    }
  }

  for (const [pageName, img] of Object.entries(pageImageMap)) {
    if (!img) continue;
    proc._loadedImages[pageName] = img;
    const page = proc._pageMap[pageName];
    if (!page) continue;
    const realW = img.naturalWidth || img.width || 0;
    const realH = img.naturalHeight || img.height || 0;
    if (page.width && page.height && realW > 0 && realH > 0) {
      page.scaleX = realW / page.width;
      page.scaleY = realH / page.height;
    }
  }

  const regionNames = Object.keys(proc.regions);
  const sprites = [];
  for (const name of regionNames) {
    const canvas = proc.extractRegion(name);
    if (!canvas) continue;
    sprites.push({ name, canvas });
  }

  const { canvas: packedCanvas, placements } = _packCanvasesSimple(sprites);
  const pageInfo = proc.pages.length > 0
    ? {
        page: proc.pages[0].filename,
        format: proc.pages[0].format,
        filter: `${proc.pages[0].filter[0]}, ${proc.pages[0].filter[1]}`,
        repeat: proc.pages[0].repeat,
      }
    : { page: 'atlas.png', format: 'RGBA8888', filter: 'Nearest, Nearest', repeat: 'none' };

  const regionData = {};
  for (const name of regionNames) {
    const p = placements[name];
    if (!p) continue;
    const region = proc.regions[name];
    regionData[name] = [
      [p.x, p.y, p.w, p.h],
      region && region.offsets ? region.offsets : null,
      0,
      {
        atlasName: region && region.atlasName ? region.atlasName : name,
        index: region && Number.isFinite(region.index) ? region.index : -1,
        split: region ? region.split : null,
        pad: region ? region.pad : null,
        extraPairs: region && Array.isArray(region.extraPairs) ? region.extraPairs : [],
      },
    ];
  }

  const atlasOut = rebuildAtlasText(pageInfo, [packedCanvas.width, packedCanvas.height], regionNames, regionData);
  return { canvas: packedCanvas, atlasText: atlasOut };
}

function _normalizeName(name) {
  return String(name || '').trim().toLowerCase();
}

function _getUniqueFilesByName(fileMap) {
  const unique = [];
  const seen = new Set();
  for (const file of Object.values(fileMap || {})) {
    if (!file || !file.name || seen.has(file.name)) continue;
    seen.add(file.name);
    unique.push(file);
  }
  return unique;
}

function _mapInitialImagesToPages(requiredPages, imageFileMap) {
  const mapped = {};
  const initialImages = _getUniqueFilesByName(imageFileMap);

  // Rule A.1: one page + one image => map immediately.
  if (requiredPages.length === 1 && initialImages.length === 1) {
    mapped[requiredPages[0]] = initialImages[0];
    return mapped;
  }

  const lowerToImages = new Map();
  for (const file of initialImages) {
    const key = _normalizeName(file.name);
    if (!lowerToImages.has(key)) lowerToImages.set(key, []);
    lowerToImages.get(key).push(file);
  }

  // Rule A.2: case-insensitive pageName <-> filename matching.
  for (const pageName of requiredPages) {
    const exact = imageFileMap[pageName];
    if (exact) {
      mapped[pageName] = exact;
      continue;
    }

    const matches = lowerToImages.get(_normalizeName(pageName));
    if (matches && matches.length > 0) {
      mapped[pageName] = matches[0];
    }
  }

  return mapped;
}

async function _confirmDialog(message, title = 'Confirm') {
  if (typeof window !== 'undefined' && typeof window.showConfirm === 'function') {
    return !!(await window.showConfirm(message, title));
  }
  return window.confirm(message);
}

async function _findAtlasTextFile(files) {
  const nonImageFiles = files.filter(file => !_isImageFile(file));
  const prioritized = nonImageFiles.sort((a, b) => {
    const aAtlas = /\.atlas$/i.test(a.name || '');
    const bAtlas = /\.atlas$/i.test(b.name || '');
    return Number(bAtlas) - Number(aAtlas);
  });

  for (const file of prioritized) {
    try {
      const rawText = await _readFileAsText(file);
      const convertedText = autoConvertAtlas(rawText);
      const requiredPages = _extractRequiredPages(convertedText);
      if (requiredPages.length > 0) return file;
    } catch (_) {
      // Try next text file candidate.
    }
  }

  return null;
}

async function _loadAtlasFromFileList(files, options = {}) {
  const list = Array.isArray(files) ? files : Array.from(files || []);
  if (list.length === 0) return false;

  const atlasFile = await _findAtlasTextFile(list);
  if (!atlasFile) {
    if (options.showNoAtlasToast !== false && typeof window.showToast === 'function') {
      window.showToast('No valid atlas-format text file found in the selected files.', 'error');
    }
    return false;
  }

  const imageFileMap = {};
  for (const file of list) {
    if (_isImageFile(file)) imageFileMap[file.name] = file;
  }

  return _loadAtlasFiles(atlasFile, imageFileMap);
}

async function _writeBlobToHandle(fileHandle, blob) {
  const writable = await fileHandle.createWritable();
  await writable.write(blob);
  await writable.close();
}

async function _saveBlobWithDialog(filename, blob) {
  const ext = filename.includes('.') ? filename.split('.').pop().toLowerCase() : '';
  const fileType = ext === 'png'
    ? { description: 'PNG image', accept: { 'image/png': ['.png'] } }
    : ext === 'zip'
      ? { description: 'ZIP archive', accept: { 'application/zip': ['.zip'] } }
      : { description: 'Atlas text', accept: { 'text/plain': ['.atlas', '.txt'] } };
  const pickerOptions = {
    id: 'atlastoolkit-export',
    suggestedName: filename,
    types: [fileType],
    excludeAcceptAllOption: false,
  };

  if (_lastSaveHandle) pickerOptions.startIn = _lastSaveHandle;

  const fileHandle = await window.showSaveFilePicker(pickerOptions);
  await _writeBlobToHandle(fileHandle, blob);
  _lastSaveHandle = fileHandle;
}

/**
 * Download data as a file.
 * @param {string} filename
 * @param {Blob} blob
 */
async function _downloadBlob(filename, blob) {
  // Desktop browser: prefer Save As dialog (File System Access API).
  if (_isDesktopFsApiAvailable()) {
    await _saveBlobWithDialog(filename, blob);
    return;
  }

  // Fallback: browser download
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 100);
}

function _canvasToBlob(canvas) {
  return new Promise((resolve, reject) => {
    canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error('toBlob failed')), 'image/png');
  });
}

/** Pick one or more files using a hidden file input triggered by a user gesture. */
function _pickFiles({ accept = '', multiple = false } = {}) {
  return new Promise((resolve) => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = accept;
    input.multiple = multiple;
    // listen for change; also handle cancel gracefully
    const cleanup = () => {
      input.removeEventListener('change', onchange);
      input.removeEventListener('cancel', oncancel);
      clearTimeout(timer);
    };
    const onchange = (e) => { cleanup(); resolve(Array.from(e.target.files)); };
    const oncancel = () => { cleanup(); resolve([]); };
    input.addEventListener('change', onchange);
    input.addEventListener('cancel', oncancel);
    input.click();
    // Safari/some Android may not fire 'cancel'; resolve with [] after long delay
    const timer = setTimeout(() => { cleanup(); resolve([]); }, 60000);
  });
}

// ─── Core: load an atlas File + associated image Files ───────────────────────

/**
 * Load an atlas from File objects.
 * @param {File} atlasFile
 * @param {Object.<string, File>} imageFileMap  { pageName: File }  (may be partial)
 * @returns {Promise<boolean>}
 */
async function _loadAtlasFiles(atlasFile, imageFileMap) {
  try {
    const rawText = await _readFileAsText(atlasFile);
    const convertedText = autoConvertAtlas(rawText);
    const requiredPages = _extractRequiredPages(convertedText);

    const finalMap = _mapInitialImagesToPages(requiredPages, imageFileMap);

    // For missing pages, ask user to attach image per pageName (manual mapping).
    let missingPages = requiredPages.filter(pageName => !finalMap[pageName]);
    while (missingPages.length > 0) {
      let selectedByPage = null;

      if (typeof window !== 'undefined' && typeof window.showMissingAtlasImages === 'function') {
        selectedByPage = await window.showMissingAtlasImages(missingPages);
      } else {
        const proceed = await _confirmDialog(
          `Missing image files for atlas pages:\n${missingPages.map(pageName => `- ${pageName}`).join('\n')}`,
          'Missing Atlas Page Images'
        );
        if (!proceed) return false;

        selectedByPage = {};
        for (const pageName of missingPages) {
          const files = await _pickFiles({ accept: IMAGE_PICKER_ACCEPT, multiple: false });
          if (files.length === 0) return false;
          selectedByPage[pageName] = files[0];
        }
      }

      if (!selectedByPage) return false;

      for (const pageName of missingPages) {
        const chosenFile = selectedByPage[pageName];
        if (chosenFile) finalMap[pageName] = chosenFile;
      }

      missingPages = requiredPages.filter(pageName => !finalMap[pageName]);
    }

    _processor = new AtlasProcessor(convertedText);
    await _processor.loadImages(finalMap);

    _currentAtlasFilename = atlasFile.name;
    _currentAtlasText = convertedText;
    _currentSkel = null;
    _previewMemo = { key: null, value: null };

    // Fresh session bound to the pristine processor + atlas text.
    _session = new AtlasSession(_processor, _currentAtlasText, _currentAtlasFilename);

    return true;
  } catch (e) {
    console.error('load_atlas error:', e);
    return false;
  }
}

// ─── Public API (mirrors pywebview.api) ───────────────────────────────────────

export const AtlasAPI = {

  /** Read a preference from localStorage. Returns a Promise. */
  get_pref(key, defaultValue = null) {
    return platform.loadPref(key, defaultValue);
  },

  set_pref(key, value) {
    platform.savePref(key, value);
  },

  /** Called on startup; returns false (no file pre-loaded on Android). */
  async startup_check() {
    return false;
  },

  /** Open a file picker that accepts .atlas and image files. */
  async choose_file() {
    const files = await _pickFiles({
      accept: '.atlas,.txt,text/plain,image/png,.png',
      multiple: true,
    });
    return _loadAtlasFromFileList(files, {
      showNoAtlasToast: true,
    });
  },

  /** Load atlas from a mixed file list (used by drag-and-drop). */
  async load_from_files(files, options = {}) {
    return _loadAtlasFromFileList(files, {
      showNoAtlasToast: options.showNoAtlasToast,
    });
  },

  /** Load atlas directly from a File object (used by drag-and-drop). */
  async load_atlas_from_file(atlasFile, imageFileMap = {}) {
    return _loadAtlasFiles(atlasFile, imageFileMap);
  },

  get_region_names() {
    if (!_processor) return [];
    return Object.keys(_processor.regions);
  },

  get_region_page_name(regionName) {
    if (!_processor || !regionName) return '';
    const region = _processor.regions[regionName];
    return region ? region.pageFilename : '';
  },

  async get_preview(names) {
    if (!_processor || !names || names.length === 0) return null;
    // Memoize on the selection set + mod generation. Generation stays 0 in
    // extract mode, so identical selections short-circuit the composite;
    // applying a mod bumps it so a stale preview is never served.
    const gen = _session ? _session.modGeneration : 0;
    const key = `${[...names].sort().join(',')}:${gen}`;
    if (_previewMemo.key === key) return _previewMemo.value;
    try {
      const url = _processor.getPreviewDataURL(names);
      _previewMemo = { key, value: url };
      return url;
    } catch (e) {
      console.error('get_preview error:', e);
      return null;
    }
  },

  /**
   * Extract regions to files (downloads).
   * @param {string[]|null} regionNames  null = extract all
   */
  async extract_files(regionNames) {
    if (!_processor) return 'No atlas loaded.';
    const targets = regionNames || Object.keys(_processor.regions);
    if (targets.length === 0) return 'No regions to extract.';

    const extracted = [];

    let count = 0;
    for (const name of targets) {
      const canvas = _processor.extractRegion(name);
      if (!canvas) continue;
      try {
        const safeName = name.replace(/[^\w.\- ]/g, '_');
        const blob = await _canvasToBlob(canvas);
        extracted.push({ filename: `${safeName}.png`, blob });
        count++;
      } catch (e) {
        console.error(`Failed to extract ${name}:`, e);
      }
    }

    if (extracted.length === 0) return 'No regions to extract.';

    try {
      // Installed PWA: pick a folder and write the PNGs into it.
      if (_useFolderPicker()) {
        const folder = await platform.pickSaveFolder();
        if (!folder) return 'Cancelled';
        await platform.writeFilesToFolder(
          folder,
          extracted.map(item => ({ name: item.filename, data: item.blob })),
        );
        return `Saved ${count} image${count !== 1 ? 's' : ''} to selected folder.`;
      }

      // Single image in a browser tab: a direct download beats a zip-of-one.
      if (extracted.length === 1) {
        await _downloadBlob(extracted[0].filename, extracted[0].blob);
        return `Successfully extracted ${count} image${count !== 1 ? 's' : ''}.`;
      }

      // Browser tab, multiple images: bundle into a zip.
      const zipBlob = await createZip(
        extracted.map(item => ({ name: item.filename, data: item.blob })),
      );
      await _downloadBlob(`${_extractZipBaseName()}.zip`, zipBlob);
      return `Successfully extracted ${count} image${count !== 1 ? 's' : ''} (zip).`;
    } catch (e) {
      if (e && e.name === 'AbortError') return 'Cancelled';
      throw e;
    }
  },

  // ── Modify Mode ────────────────────────────────────────────────────────────

  async enter_modify_mode() {
    if (!_processor || !_session) return null;
    try {
      const pages = _processor.pages || [];
      if (pages.length === 0) return null;
      const initialPage = pages[0].filename;
      const baseImg = _processor.getPageImage(initialPage);
      if (!baseImg) return null;

      // Entering modify mode always starts from a clean batch list.
      _session.clearModifyState();

      // Build region bounds for overlay: { name: [x, y, w, h, rotate] }
      const regionBounds = {};
      for (const [name, info] of Object.entries(_processor.regions || {})) {
        regionBounds[name] = [info.x, info.y, info.w, info.h, info.rotate];
      }

      // Convert base image to data URL for preview
      const baseCanvas = document.createElement('canvas');
      baseCanvas.width = baseImg.naturalWidth || baseImg.width;
      baseCanvas.height = baseImg.naturalHeight || baseImg.height;
      baseCanvas.getContext('2d').drawImage(baseImg, 0, 0);

      return {
        image: baseCanvas.toDataURL('image/png'),
        regions: regionBounds,
        pages: pages.map(p => p.filename),
        regionPages: _getRegionPageMap(),
        activePage: initialPage,
      };
    } catch (e) {
      console.error('enter_modify_mode error:', e);
      return null;
    }
  },

  exit_modify_mode() {
    if (_session) _session.clearModifyState();
  },

  /** True when there are unsaved modify-mode modifications. */
  has_pending_modifications() {
    return !!_session && _session.hasPendingModifications();
  },

  /** Pick a mod PNG and process it. */
  async select_mod_image(selectedNames, repack = false, repackMode = 'page') {
    if (!_session || !selectedNames || selectedNames.length === 0) return null;
    const files = await _pickFiles({ accept: IMAGE_PICKER_ACCEPT, multiple: false });
    if (files.length === 0) return null;
    return AtlasAPI.process_mod_image(files[0], selectedNames, repack, repackMode);
  },

  /** Process a mod image (File or canvas/img) for the selected regions. */
  async process_mod_image(source, selectedNames, repack = false, repackMode = 'page') {
    if (!_session || !selectedNames || selectedNames.length === 0) return null;
    try {
      // "Repack all pages to one" is a JS-only post-step layered on the merge
      // result; the session itself only knows merge / per-page repack. It's
      // unsafe for multi-page atlases (see _applyRepackAllOverride) — the UI
      // already hides that option there, but guard here too in case it's
      // bypassed, falling back to the safe per-page repack instead.
      const isMultiPage = !!_processor && _processor.pages.length > 1;
      const wantAll = repack && repackMode === 'all' && !isMultiPage;
      const result = await _session.processModImage(source, selectedNames, repack && !wantAll);
      if (!result) return null;
      return wantAll ? (await _applyRepackAllOverride()) || result : result;
    } catch (e) {
      console.error('process_mod_image error:', e);
      if (typeof window.showToast === 'function') window.showToast(`Error: ${e.message}`, 'error');
      return null;
    }
  },

  /** Save the merged atlas files. Installed PWA: folder picker + batched
   *  write. Browser tab: zip download. */
  async save_modified() {
    if (!_session || !_session.hasMergedOutput()) return 'Error: No merged data to save.';
    const merged = _session.getMergedOutput();
    try {
      // Build the list of outputs (png(s), atlas text, optional skel) up front
      // so we can route through either the folder writer or the zip download.
      const outputs = [];

      if (merged.pages && merged.pages.length > 0) {
        for (let i = 0; i < merged.pages.length; i++) {
          const pageName = (_processor && _processor.pages[i])
            ? _processor.pages[i].filename
            : `page${i}.png`;
          outputs.push({ name: pageName, data: await _canvasToBlob(merged.pages[i]) });
        }
      } else if (merged.canvas) {
        const base = _currentAtlasFilename.replace(/\.[^.]+$/, '');
        outputs.push({ name: `${base}.png`, data: await _canvasToBlob(merged.canvas) });
      } else {
        return 'Error: No merged data to save.';
      }

      outputs.push({ name: _currentAtlasFilename, data: merged.text });
      if (_currentSkel) outputs.push({ name: _currentSkel.name, data: _currentSkel.blob });

      // Installed PWA: pick a folder and write all outputs into it.
      if (_useFolderPicker()) {
        const folder = await platform.pickSaveFolder();
        if (!folder) return 'Cancelled';
        await platform.writeFilesToFolder(folder, outputs);
        _session.markSaved();
        const fileNames = outputs.map(o => o.name).join(', ');
        return `Saved to selected folder: ${fileNames}`;
      }

      // Browser tab: bundle all outputs into a single zip download.
      const zipBlob = await createZip(outputs);
      await _downloadBlob(`${_extractZipBaseName()}_modified.zip`, zipBlob);
      _session.markSaved();
      const summary = outputs.map(o => o.name).join(' and ');
      return `Saved as zip: ${summary}`;
    } catch (e) {
      if (e && e.name === 'AbortError') return 'Cancelled';
      return `Error: ${e.message}`;
    }
  },

  /** Toggle repack on/off; the session lazily rebuilds whichever result is stale. */
  async toggle_repack(repack, repackMode = 'page') {
    if (!_session || _session.modBatches.length === 0) return null;
    try {
      // See process_mod_image: "all" mode is unsafe for multi-page atlases,
      // guard here too in case the UI gating is bypassed.
      const isMultiPage = !!_processor && _processor.pages.length > 1;
      const wantAll = repack && repackMode === 'all' && !isMultiPage;
      const result = await _session.toggleRepack(repack && !wantAll);
      if (!result) return null;
      return wantAll ? (await _applyRepackAllOverride()) || result : result;
    } catch (e) {
      console.error('toggle_repack error:', e);
      return null;
    }
  },

};

export { _loadAtlasFiles };
