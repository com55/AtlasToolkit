/**
 * atlas-api.js
 * Replaces pywebview.api with a pure-JS implementation.
 * Exposes the same async interface that script.js expects.
 */

import { autoConvertAtlas } from './atlas-converter.js';
import { AtlasProcessor, canvasToPreviewUrl } from './atlas-extracter.js';
import { platform, isTouchDevice, fileMatchesAccept, isPywebviewDesktop, base64ToFile, pathToFileUrl, joinNativePath, loadFileAsFile, siblingSkelFilename, pickSiblingSkelFile } from './platform.js';
import { createZip } from './zip.js';
import { AtlasSession } from './atlas-session.js';
import { AtlasDocument, pngNamesForSave } from './atlas-document.js';
import { parseSkeleton, UnsupportedVersionError } from './vendor/spine-skeleton-binary/index.js';
import { buildMeshLookup } from './region-mesh-lookup.js';

/** Returned by load helpers when the user cancels a missing-images dialog. */
export const LOAD_CANCELLED = 'cancelled';

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
let _parsedSkeleton = null;   // {version, attachments} | null
let _meshLookup = null;       // Map<name, {uvs,triangles}> | null
// User preference, persisted like the Repack toggle -- does NOT reset per
// atlas load. Initialized once from the 'meshCropping' pref at startup via
// init_mesh_mask_from_pref(); changed only by an explicit user toggle
// (set_mesh_mask_enabled). Independent of whether the CURRENT atlas's
// .skel is actually usable -- see _meshUnavailableReason for that.
let _meshMaskEnabled = true;
// null | 'unsupported-version' | 'parse-error' | 'no-mesh-attachments' --
// why the current .skel (if any) can't be used, for the picker button's
// tooltip. null when there's no .skel captured yet, or when it parsed with
// usable Mesh geometry.
let _meshUnavailableReason = null;
let _previewMemo = { key: null, value: null };

function _clearPreviewMemo() {
  if (_previewMemo.value && String(_previewMemo.value).startsWith('blob:')) {
    URL.revokeObjectURL(_previewMemo.value);
  }
  _previewMemo = { key: null, value: null };
}

/** Drop the memo if it still points at *url* (already revoked by the caller). */
function _forgetPreviewMemoUrl(url) {
  if (url && _previewMemo.value === url) {
    _previewMemo = { key: null, value: null };
  }
}

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
  const nonImageFiles = files.filter(file => !_isImageFile(file) && !/\.skel$/i.test(file.name || ''));
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

  return _loadAtlasFiles(atlasFile, imageFileMap, '', list);
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
 * @param {string} sourceDir  native folder the atlas was opened from, or ''
 * @param {Array<File>} extraFiles  dropped/picked siblings (may include a .skel)
 * @returns {Promise<boolean>}
 */
async function _loadAtlasFiles(atlasFile, imageFileMap, sourceDir = '', extraFiles = []) {
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
        selectedByPage = await window.showMissingAtlasImages(missingPages, sourceDir);
      } else {
        const proceed = await _confirmDialog(
          `Missing image files for atlas pages:\n${missingPages.map(pageName => `- ${pageName}`).join('\n')}`,
          'Missing Atlas Page Images'
        );
        if (!proceed) return LOAD_CANCELLED;

        selectedByPage = {};
        for (const pageName of missingPages) {
          const files = await _pickFiles({ accept: IMAGE_PICKER_ACCEPT, multiple: false });
          if (files.length === 0) return LOAD_CANCELLED;
          selectedByPage[pageName] = files[0];
        }
      }

      if (!selectedByPage) return LOAD_CANCELLED;

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
    await _captureSiblingSkel(atlasFile, sourceDir, extraFiles);
    _clearPreviewMemo();

    // Fresh session bound to the pristine processor + atlas text.
    _session = new AtlasSession(_processor, _currentAtlasText, _currentAtlasFilename);

    return true;
  } catch (e) {
    console.error('load_atlas error:', e);
    return false;
  }
}

/** Hold the sibling `.skel` (same stem as the atlas) so Save As can copy it.
 *  Desktop: load from the atlas folder (Python `Path.with_suffix(".skel")`).
 *  Browser: only if the user included the .skel in the picked/dropped files. */
async function _captureSiblingSkel(atlasFile, sourceDir, extraFiles) {
  _currentSkel = null;
  const skelName = siblingSkelFilename(atlasFile?.name);
  if (!skelName) { await _reparseSkelAndPushToProcessor(); return; }

  const fromList = pickSiblingSkelFile(atlasFile.name, extraFiles);
  if (fromList) {
    _currentSkel = { name: skelName, blob: fromList };
    await _reparseSkelAndPushToProcessor();
    return;
  }

  if (!sourceDir) { await _reparseSkelAndPushToProcessor(); return; }
  try {
    let skelPath = joinNativePath(sourceDir, skelName);
    if (isPywebviewDesktop() && window.pywebview.api.resolve_sibling_skel) {
      const atlasPath = joinNativePath(sourceDir, atlasFile.name);
      const resolved = await window.pywebview.api.resolve_sibling_skel(atlasPath);
      if (resolved) skelPath = resolved;
      else { await _reparseSkelAndPushToProcessor(); return; }
    }
    const file = await loadFileAsFile(skelPath, 'application/octet-stream');
    if (file && file.size > 0) _currentSkel = { name: skelName, blob: file };
  } catch (_) {
    // No sibling on disk — Copy .skel is a no-op; mask state stays unavailable.
  }
  await _reparseSkelAndPushToProcessor();
}

/** Parses _currentSkel.blob (if set) into _parsedSkeleton/_meshLookup and
 *  pushes the result into _processor, then clears the preview memo since
 *  the effective output for already-cached selections has changed. Safe
 *  to call with _currentSkel === null (clears mask state instead).
 *  Does NOT touch _meshMaskEnabled -- that's a persisted user preference
 *  (like Repack's), not something that resets per atlas load. Sets
 *  _meshUnavailableReason to explain why the current .skel (if any) can't
 *  be used, for the picker button's tooltip. */
async function _reparseSkelAndPushToProcessor() {
  _parsedSkeleton = null;
  _meshLookup = null;
  _meshUnavailableReason = null;
  if (_currentSkel) {
    try {
      const bytes = new Uint8Array(await _currentSkel.blob.arrayBuffer());
      _parsedSkeleton = parseSkeleton(bytes);
      _meshLookup = buildMeshLookup(_parsedSkeleton);
      // buildMeshLookup always returns a Map (never null), so an all-Region
      // .skel or one with zero Mesh attachments parses "successfully" but
      // has nothing this feature could ever mask (found by Fable scrutinize
      // review, 2026-08-31) -- report that distinctly from a parse failure.
      if (_meshLookup.size === 0) _meshUnavailableReason = 'no-mesh-attachments';
    } catch (e) {
      if (e instanceof UnsupportedVersionError) {
        _meshUnavailableReason = 'unsupported-version';
      } else {
        console.error('skel parse error:', e);
        _meshUnavailableReason = 'parse-error';
      }
    }
  }
  if (_processor) _processor.setMeshMaskData(_meshLookup, _meshMaskEnabled);
  _clearPreviewMemo();
}

/** Prefer the skel captured at load; otherwise retry from the atlas folder
 *  at save time (historical Python copied at save, not at open). */
async function _skelForSave() {
  const copySkel = await AtlasAPI.get_pref('copySkel', true);
  if (!copySkel) return null;
  if (_currentSkel) return _currentSkel;
  const skelName = siblingSkelFilename(_currentAtlasFilename);
  if (!skelName || !_currentAtlasDirectory) return null;
  try {
    let skelPath = joinNativePath(_currentAtlasDirectory, skelName);
    if (isPywebviewDesktop() && window.pywebview.api.resolve_sibling_skel) {
      const atlasPath = joinNativePath(_currentAtlasDirectory, _currentAtlasFilename);
      const resolved = await window.pywebview.api.resolve_sibling_skel(atlasPath);
      if (!resolved) return null;
      skelPath = resolved;
    }
    const file = await loadFileAsFile(skelPath, 'application/octet-stream');
    if (file && file.size > 0) return { name: skelName, blob: file };
  } catch (_) {
    // Still no sibling .skel.
  }
  return null;
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

  /** Reads the persisted 'meshCropping' pref into _meshMaskEnabled. Call
   *  once at app startup (mirrors the Repack pref's own init in script.js) —
   *  NOT per atlas load, since this toggle persists like Repack's does. */
  async init_mesh_mask_from_pref() {
    _meshMaskEnabled = await AtlasAPI.get_pref('meshCropping', true);
  },

  get_mesh_mask_state() {
    return {
      available: !!_meshLookup && _meshLookup.size > 0,
      enabled: _meshMaskEnabled,
      skelFileName: _currentSkel ? _currentSkel.name : null,
      unavailableReason: _meshUnavailableReason,
    };
  },

  async set_mesh_mask_enabled(enabled) {
    _meshMaskEnabled = !!enabled;
    AtlasAPI.set_pref('meshCropping', _meshMaskEnabled);
    if (_processor) _processor.setMeshMaskData(_meshLookup, _meshMaskEnabled);
    _clearPreviewMemo();
  },

  /** Manual .skel picker — covers sibling auto-resolve misses and the
   *  browser/PWA target where .skel isn't in the picked/dropped file set. */
  async pick_skel_file() {
    const files = await _pickFiles({ accept: '.skel', multiple: false });
    if (files.length === 0) return false;
    _currentSkel = { name: files[0].name, blob: files[0] };
    await _reparseSkelAndPushToProcessor();
    return true;
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
      const picked = await window.pywebview.api.pick_atlas_file();
      const path = Array.isArray(picked) ? picked[0] : picked;
      if (!path) return false;
      try {
        const atlasInfo = await window.pywebview.api.read_file_as_base64(path);
        const atlasFile = base64ToFile(atlasInfo.base64, atlasInfo.name, 'text/plain');
        const dir = _dirnameOf(String(path));
        const imageFileMap = {};
        // Resolve each atlas page line against the .atlas folder — same rule
        // as Python session.resolve_page_images (drag/CLI). Do not depend on
        // list_sibling_page_images's js_api dict (Open was showing missing
        // even when siblings existed, 2026-08-24). Image() loads file://
        // directly, same as native mod-image drop.
        let resolved = {};
        let resolverUsed = false;
        if (window.pywebview.api.resolve_sibling_page_images) {
          resolverUsed = true;
          const pairs = await window.pywebview.api.resolve_sibling_page_images(path);
          if (Array.isArray(pairs)) {
            for (const row of pairs) {
              if (row && row.name && row.path) resolved[row.name] = row.path;
            }
          } else if (pairs && typeof pairs === 'object') {
            resolved = pairs;
          }
        }
        if (!resolverUsed) {
          // Older bridge without the exists()-checked resolver: try dirname
          // + page line. Do not do this after an empty resolver result —
          // missing files must stay out of the map so the missing-images
          // dialog still appears.
          for (const pageName of _extractRequiredPages(autoConvertAtlas(await _readFileAsText(atlasFile)))) {
            resolved[pageName] = joinNativePath(dir, pageName);
          }
        }
        for (const [pageName, imgPath] of Object.entries(resolved)) {
          if (imgPath) imageFileMap[pageName] = pathToFileUrl(imgPath);
        }
        return _loadAtlasFiles(atlasFile, imageFileMap, dir);
      } catch (e) {
        console.error('choose_file (pywebview) error:', e);
        if (typeof window.showToast === 'function') {
          window.showToast(`Failed to load atlas file: ${e.message || e}`, 'error');
        }
        return false;
      }
    }

    const files = await _pickFiles({
      accept: '.atlas,.txt,text/plain,image/png,.png,.skel',
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
    return Object.entries(_processor.regions).map(([key, region]) => ({
      key,
      label: region.atlasName || key,
    }));
  },

  get_region_page_name(key) {
    if (!_processor || !key) return '';
    const region = _processor.regions[key];
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
    _clearPreviewMemo();
    try {
      const url = await _processor.getPreviewDataURL(names);
      _previewMemo = { key, value: url };
      return url;
    } catch (e) {
      console.error('get_preview error:', e);
      return null;
    }
  },

  /**
   * Extract regions to files (downloads).
   * @param {{key:string,label:string}[]|null} regions  null = extract all
   */
  async extract_files(regions) {
    if (!_processor) return 'No atlas loaded.';
    const targets = regions || AtlasAPI.get_region_names();
    if (targets.length === 0) return 'No regions to extract.';

    const extracted = [];

    let count = 0;
    for (const { key, label } of targets) {
      const canvas = _processor.extractRegion(key);
      if (!canvas) continue;
      try {
        const safeName = key.replace(/[^\w.\- ]/g, '_');
        const blob = await _canvasToBlob(canvas);
        extracted.push({ filename: `${safeName}.png`, blob });
        count++;
      } catch (e) {
        console.error(`Failed to extract ${key}:`, e);
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
      // Drop the view-mode preview memo so a later exit can't reuse a
      // blob: URL that setPreviewSrc is about to revoke (broken-image
      // after Edit→View, 2026-08-23).
      _session.clearModifyState();
      _clearPreviewMemo();

      // Build region bounds for overlay: { name: [x, y, w, h, rotate] }.
      // Scaled per page to the real loaded image size (see
      // AtlasSession.getModifyRegionBounds) so the overlay lines up even
      // when a page's PNG doesn't match the atlas's declared `size:`.
      const regionBounds = _session.getModifyRegionBounds();

      // Preview as a blob: URL — see canvasToPreviewUrl (avoids toDataURL).
      const baseCanvas = document.createElement('canvas');
      baseCanvas.width = baseImg.naturalWidth || baseImg.width;
      baseCanvas.height = baseImg.naturalHeight || baseImg.height;
      baseCanvas.getContext('2d').drawImage(baseImg, 0, 0);

      return {
        image: await canvasToPreviewUrl(baseCanvas),
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
    _clearPreviewMemo();
  },

  /** Called by setPreviewSrc when it revokes a blob: URL that get_preview
   *  may still be memoizing — otherwise Edit→View serves a dead blob. */
  forget_preview_url(url) {
    _forgetPreviewMemoUrl(url);
  },

  /**
   * Preview data for one atlas page. Accepts a 0-based index (old Python
   * `get_modify_page_image(index)`) or a page filename. Prefers the merged
   * slot at that index once mods exist.
   * @param {number|string} pageFilenameOrIndex
   * @returns {Promise<{image: string, activePage: string, activeIndex: number}|null>}
   */
  async get_modify_page_preview(pageFilenameOrIndex) {
    if (!_processor || !_session) return null;
    try {
      let index = pageFilenameOrIndex;
      if (typeof pageFilenameOrIndex === 'string') {
        index = _session.processor.pages.findIndex(p => p.filename === pageFilenameOrIndex);
        if (index < 0 && _session.active && _session.active.text) {
          index = AtlasDocument.parse(_session.active.text).pageFilenames()
            .indexOf(pageFilenameOrIndex);
        }
      }
      index = Number(index);
      if (!Number.isInteger(index) || index < 0) return null;
      const canvas = _session.getModifyPageImage(index);
      if (!canvas) return null;
      const activePage = (_session.processor.pages[index] || {}).filename || String(pageFilenameOrIndex);
      return { image: await canvasToPreviewUrl(canvas), activePage, activeIndex: index };
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
      // Pass a file:// URL straight to process_mod_image — Image() loads
      // it directly (no fetch→File copy, no toDataURL). Same trick as
      // applyNativeModImageDrop (perf fix, 2026-08-23).
      file = pathToFileUrl(path);
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
        const pageNames = pngNamesForSave(merged.text, merged.pages.length, _currentAtlasFilename);
        for (let i = 0; i < merged.pages.length; i++) {
          if (!merged.pages[i]) continue;
          outputs.push({ name: pageNames[i], data: await _canvasToBlob(merged.pages[i]) });
        }
      } else if (merged.canvas) {
        const pageName = pngNamesForSave(merged.text, 1, _currentAtlasFilename)[0];
        outputs.push({ name: pageName, data: await _canvasToBlob(merged.canvas) });
      } else {
        return 'Error: No merged data to save.';
      }

      outputs.push({ name: _currentAtlasFilename, data: merged.text });
      const skel = await _skelForSave();
      if (skel) {
        outputs.push({ name: skel.name, data: skel.blob });
      }

      // Installed PWA / pywebview desktop: pick a folder and write all outputs into it.
      if (_useFolderPicker()) {
        const folder = await platform.pickSaveFolder(_currentAtlasDirectory);
        if (!folder) return 'Cancelled';
        await platform.writeFilesToFolder(folder, outputs);
        _session.markSaved();
        // Matches old Python engine's bridge.py save_merged_to() exactly
        // (`f"Saved to: {result[0]}"`) when a real path is available
        // (pywebview: folder is a plain string; browser: a
        // FileSystemDirectoryHandle never exposes a full path) — parity fix,
        // 2026-08-23.
        return typeof folder === 'string'
          ? `Saved to: ${folder}`
          : 'Saved to selected folder.';
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

/**
 * Test-only: force a region's display label to diverge from its stable
 * internal key, so a test can verify the key/label split is wired
 * correctly end-to-end (nothing in production behavior creates this
 * divergence yet -- see the region-identity-key-refactor spec's §
 * Acceptance criterion). Deliberately a plain named export, NOT attached
 * to the `AtlasAPI` object or `window` -- reachable only via a direct ES
 * module import (`import { __testOnlySetLabel } from './atlas-api.js'`),
 * the same access pattern `tests/browser/verify-ui-flows.mjs` already
 * uses for other module internals. Not part of the public API.
 */
export function __testOnlySetLabel(key, label) {
  if (_processor && _processor.regions[key]) {
    _processor.regions[key].atlasName = label;
  }
}

export { _loadAtlasFiles };
