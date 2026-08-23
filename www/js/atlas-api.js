/**
 * atlas-api.js
 * Replaces pywebview.api with a pure-JS implementation.
 * Exposes the same async interface that script.js expects.
 */

import { autoConvertAtlas } from './atlas-converter.js';
import { AtlasProcessor } from './atlas-extracter.js';
import { platform, isTouchDevice, fileMatchesAccept, isPywebviewDesktop, base64ToFile } from './platform.js';
import { createZip } from './zip.js';
import { AtlasSession } from './atlas-session.js';

// ─── State ────────────────────────────────────────────────────────────────────

let _processor = null;
let _currentAtlasFilename = '';
// Native-open source folder (pywebview only — a browser <input type=file>
// never exposes a real filesystem path). Used as the starting directory for
// native extract/save dialogs, matching the old Python engine's behavior of
// always opening those at the loaded atlas's folder (found missing via
// parity audit, 2026-08-23). Empty string just means "let the OS pick".
let _currentAtlasDirectory = '';
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
  if (isPywebviewDesktop()) return true;
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
 * Access API, or the pywebview desktop app's native folder dialog) rather
 * than downloading a zip. A plain browser tab — even a Chromium one with
 * the FS API — gets a zip download.
 */
function _useFolderPicker() {
  return isPywebviewDesktop()
    || (_isInstalledPWA() && typeof window.showDirectoryPicker === 'function');
}

/** Directory portion of a native (Windows or POSIX) filesystem path. */
function _dirnameOf(path) {
  if (!path) return '';
  const idx = Math.max(path.lastIndexOf('/'), path.lastIndexOf('\\'));
  return idx >= 0 ? path.slice(0, idx) : '';
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

async function _saveBlobWithDialog(filename, blob, { defaultDir = '' } = {}) {
  // platform.js branches browser (File System Access API) vs pywebview
  // (native save dialog + write_file_bytes); _lastSaveHandle only means
  // anything for the former.
  const result = await platform.saveFileWithDialog(filename, blob, { startIn: _lastSaveHandle, defaultDir });
  if (!result) {
    const e = new Error('Save cancelled');
    e.name = 'AbortError';
    throw e;
  }
  if (typeof result === 'object') _lastSaveHandle = result;
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

/**
 * Pick one or more files using a hidden file input triggered by a user gesture.
 * On touch devices, `accept` is dropped in favor of the OS's full file browser
 * (a MIME-restricted picker there often shows a photo-only view that hides
 * files the user wants), and the selection is validated against `accept`
 * afterward instead.
 */
function _pickFiles({ accept = '', multiple = false } = {}) {
  const touchMode = isTouchDevice();
  return new Promise((resolve) => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = touchMode ? '*' : accept;
    input.multiple = multiple;
    // listen for change; also handle cancel gracefully
    const cleanup = () => {
      input.removeEventListener('change', onchange);
      input.removeEventListener('cancel', oncancel);
      clearTimeout(timer);
    };
    const onchange = async (e) => {
      cleanup();
      let files = Array.from(e.target.files);
      if (touchMode && accept) {
        const valid = files.filter(f => fileMatchesAccept(f, accept));
        if (valid.length < files.length) {
          if (typeof window.showAlert === 'function') {
            await window.showAlert('Unsupported file type ignored.', 'Unsupported file');
          } else if (typeof window.showToast === 'function') {
            window.showToast('Unsupported file type ignored.', 'error');
          }
        }
        files = valid;
      }
      resolve(files);
    };
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
async function _loadAtlasFiles(atlasFile, imageFileMap, sourceDir = '') {
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
    _currentAtlasDirectory = sourceDir;
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

  /**
   * Open a file picker that accepts .atlas and image files. Under pywebview,
   * use the native single-file Open dialog + list_sibling_page_images() to
   * auto-resolve sibling PNGs from disk (matching the old Python-engine
   * desktop UX) instead of requiring the user to multi-select the atlas +
   * every PNG together in a browser `<input type=file>` picker.
   */
  async choose_file() {
    if (isPywebviewDesktop() && window.pywebview.api.pick_atlas_file) {
      const path = await window.pywebview.api.pick_atlas_file();
      if (!path) return false;
      try {
        const atlasInfo = await window.pywebview.api.read_file_as_base64(path);
        const imagesInfo = await window.pywebview.api.list_sibling_page_images(path);
        const atlasFile = base64ToFile(atlasInfo.base64, atlasInfo.name, 'text/plain');
        const imageFileMap = {};
        for (const [name, b64] of Object.entries(imagesInfo || {})) {
          imageFileMap[name] = base64ToFile(b64, name, 'image/png');
        }
        return _loadAtlasFiles(atlasFile, imageFileMap, _dirnameOf(path));
      } catch (e) {
        console.error('choose_file (pywebview) error:', e);
        return false;
      }
    }

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
  async load_atlas_from_file(atlasFile, imageFileMap = {}, sourceDir = '') {
    return _loadAtlasFiles(atlasFile, imageFileMap, sourceDir);
  },

  /** Directory the current atlas was natively opened from, or '' — pywebview
   * only (see `_currentAtlasDirectory`'s doc comment). Used as the starting
   * directory for extract/save native dialogs. */
  get_current_atlas_directory() {
    return _currentAtlasDirectory;
  },

  /** Filename of the currently-loaded atlas, or '' if none — used by
   * script.js to update the pywebview native window title. */
  get_current_atlas_filename() {
    return _currentAtlasFilename;
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
      // Single region, installed PWA / pywebview desktop: a native Save As
      // dialog defaulting to "{region}.png" — matches the old Python engine's
      // extract_files() single-file save flow (a folder picker for one file
      // was a desktop-UX regression, found via parity audit 2026-08-23).
      if (extracted.length === 1 && _useFolderPicker()) {
        await _saveBlobWithDialog(extracted[0].filename, extracted[0].blob, { defaultDir: _currentAtlasDirectory });
        return `Successfully extracted ${count} image${count !== 1 ? 's' : ''}.`;
      }

      // Installed PWA / pywebview desktop, multiple regions: pick a folder and write the PNGs into it.
      if (_useFolderPicker()) {
        const folder = await platform.pickSaveFolder(_currentAtlasDirectory);
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

      // Build region bounds for overlay: { name: [x, y, w, h, rotate] }.
      // Scaled per page to the real loaded image size (see
      // AtlasSession.getModifyRegionBounds) so the overlay lines up even
      // when a page's PNG doesn't match the atlas's declared `size:`.
      const regionBounds = _session.getModifyRegionBounds();

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

  /**
   * Preview data for a single atlas page, for the multi-page switcher. Prefers
   * the current merged/repacked page image (`_session.getActivePageCanvas`) so
   * mods already applied to that page stay visible when navigating away and
   * back — falls back to the pristine page (reusing _processor.getPageImage)
   * before any mod has been applied yet. Region overlay filtering by page is
   * done client-side (state.modifyRegionPages), so this only shapes the image.
   * @param {string} pageFilename
   * @returns {Promise<{image: string, activePage: string}|null>}
   */
  async get_modify_page_preview(pageFilename) {
    if (!_processor || !pageFilename) return null;
    try {
      const activeCanvas = _session ? _session.getActivePageCanvas(pageFilename) : null;
      let canvas;
      if (activeCanvas) {
        canvas = activeCanvas;
      } else {
        const baseImg = _processor.getPageImage(pageFilename);
        if (!baseImg) return null;
        canvas = document.createElement('canvas');
        canvas.width = baseImg.naturalWidth || baseImg.width;
        canvas.height = baseImg.naturalHeight || baseImg.height;
        canvas.getContext('2d').drawImage(baseImg, 0, 0);
      }
      return { image: canvas.toDataURL('image/png'), activePage: pageFilename };
    } catch (e) {
      console.error('get_modify_page_preview error:', e);
      return null;
    }
  },

  /** True when there are unsaved modify-mode modifications. */
  has_pending_modifications() {
    return !!_session && _session.hasPendingModifications();
  },

  /** Names of regions touched by any pending mod batch — old Python engine's
   * `AtlasSession.modified_regions` property, used by region-list.js to render
   * the bold-green "name*" highlight it had. Mirrors `moddedSprites`, which
   * (like Python's `modded_sprites` dict) persists across repack toggles and
   * accumulates across every mod apply, not just the latest. */
  get_modified_region_names() {
    return _session ? Object.keys(_session.moddedSprites) : [];
  },

  /**
   * Pick a mod PNG and process it. Under pywebview, use the native Open
   * dialog (pick_mod_image) starting at the atlas's own folder, matching the
   * old Python engine — a plain `<input type=file>` can't be pointed at a
   * starting directory (found via parity audit, 2026-08-23).
   */
  async select_mod_image(selectedNames, repack = false) {
    if (!_session || !selectedNames || selectedNames.length === 0) return null;
    let file;
    if (isPywebviewDesktop() && window.pywebview.api.pick_mod_image) {
      const path = await window.pywebview.api.pick_mod_image(_currentAtlasDirectory);
      if (!path) return null;
      const info = await window.pywebview.api.read_file_as_base64(path);
      file = base64ToFile(info.base64, info.name, 'image/png');
    } else {
      const files = await _pickFiles({ accept: IMAGE_PICKER_ACCEPT, multiple: false });
      if (files.length === 0) return null;
      file = files[0];
    }
    return AtlasAPI.process_mod_image(file, selectedNames, repack);
  },

  /** Process a mod image (File or canvas/img) for the selected regions. */
  async process_mod_image(source, selectedNames, repack = false) {
    if (!_session || !selectedNames || selectedNames.length === 0) return null;
    try {
      return await _session.processModImage(source, selectedNames, repack);
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

      // Installed PWA / pywebview desktop: pick a folder and write all outputs into it.
      if (_useFolderPicker()) {
        const folder = await platform.pickSaveFolder(_currentAtlasDirectory);
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
  async toggle_repack(repack) {
    if (!_session || _session.modBatches.length === 0) return null;
    try {
      return await _session.toggleRepack(repack);
    } catch (e) {
      console.error('toggle_repack error:', e);
      return null;
    }
  },

};

export { _loadAtlasFiles };
