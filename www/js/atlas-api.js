import { desktopCall } from './desktop.js';
import { callHost as engineCallHost, HostError, onEngineDied, startEngine, stopEngine } from './engine-client.js';
import { rasterToUrl } from './raster-url.js';
import { getSelectedKeys } from './state.js';
import { platform, isTouchDevice, fileMatchesAccept, isPywebviewDesktop, fileFromUrl, siblingSkelFilename, pickSiblingSkelFile, isSkelFilename } from './platform.js';
import { createZip } from './zip.js';
import { withBusy, withLock, BUSY_LABEL } from './busy-overlay.js';
import { PreviewCache, viewPreviewKey } from './preview-cache.js';
let engineStartupError = null;
export function setEngineStartupError(error) { engineStartupError = error; }
function callHost(...args) {
  if (engineStartupError) return Promise.reject(engineStartupError);
  return engineCallHost(...args);
}
export const LOAD_CANCELLED = 'cancelled';
let _edit = false, _activePageIndex = 0, _editPages = [];
let _state = null, _sourceFiles = null, _currentSkel = null;
let _currentAtlasFilename = '', _currentAtlasDirectory = '', _lastSaveHandle = null;
let _meshMaskEnabled = true;
let _forcedResizing = false, _meshHullEnabled = false;
let _loadEpoch = 0, _skelEpoch = 0;
const _previewCache = new PreviewCache(24), _hulls = new Map();
const _viewKey = names => viewPreviewKey(names, _loadEpoch, _meshMaskEnabled, _skelEpoch, _forcedResizing);
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
async function _extractRequiredPages(atlasText) {
  return callHost('required_pages', [atlasText]);
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
  const nonImageFiles = files.filter(file => !_isImageFile(file) && !isSkelFilename(file.name));
  const prioritized = nonImageFiles.sort((a, b) => {
    const aAtlas = /\.atlas$/i.test(a.name || '');
    const bAtlas = /\.atlas$/i.test(b.name || '');
    return Number(bAtlas) - Number(aAtlas);
  });

  for (const file of prioritized) {
    try {
      const rawText = await _readFileAsText(file);
      const convertedText = rawText;
      const requiredPages = await _extractRequiredPages(convertedText);
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

async function _saveBlobWithDialog(filename, blob) {
  // platform.js branches browser (File System Access API) vs pywebview
  // (native save dialog and grant write); _lastSaveHandle only means
  // anything for the former.
  const result = await platform.saveFileWithDialog(filename, blob, { startIn: _lastSaveHandle });
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
    const convertedText = rawText;
    const requiredPages = await _extractRequiredPages(convertedText);
    if (!requiredPages.length) return false;

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

    const pages = {};
    for (const [name, source] of Object.entries(finalMap)) {
      const file = source;
      finalMap[name] = file;
      pages[name] = new Uint8Array(await file.arrayBuffer());
    }
    const skel = await _captureSiblingSkel(atlasFile, sourceDir, extraFiles);
    const skelBytes = skel ? new Uint8Array(await skel.blob.arrayBuffer()) : null;
    const loaded = await callHost('load', [atlasFile.name, rawText, pages, skel?.name || null, skelBytes]);
    (await import('./settings-panel.js')).resetSettingsFallback();
    _state = loaded;
    await publishSettings();
    _edit = false;
    _currentSkel = skel;
    _sourceFiles = { atlas: atlasFile, pages: finalMap, skel: _currentSkel };
    _currentAtlasFilename = atlasFile.name;
    _currentAtlasDirectory = sourceDir;
    _loadEpoch++;
    _previewCache.invalidateAll();
    _hulls.clear();
    return true;
  } catch (e) {
    if (e instanceof HostError) await window.showAlert(e.message, 'Cannot open atlas');
    else console.error('load_atlas error:', e);
    return false;
  }
}

/** Hold the sibling `.skel` (same stem as the atlas) so Save As can copy it.
 *  Desktop: load from the atlas folder (Python `Path.with_suffix(".skel")`).
 *  Browser: only if the user included the .skel in the picked/dropped files. */
async function _captureSiblingSkel(atlasFile, sourceDir, extraFiles) {

  const skelName = siblingSkelFilename(atlasFile?.name);
  if (!skelName) { return null; }

  const fromList = pickSiblingSkelFile(atlasFile.name, extraFiles);
  if (fromList) {
    return { name: skelName, blob: fromList };
  }

  return null;
}

/** Save the skeleton captured when the atlas was loaded or attached. */
async function _skelForSave() {
  return await AtlasAPI.get_pref('copySkel', true) ? _currentSkel : null;
}

export const AtlasAPI = {

  /** Read a preference from localStorage. Returns a Promise. */
  get_pref(key, defaultValue = null) {
    return platform.loadPref(key, defaultValue);
  },

  set_pref(key, value) {
    platform.savePref(key, value);
  },

  /** Whether the currently loaded atlas spans more than one page. Sync getter
   *  (no await) — read straight off the active session. */
  is_multi_page() { return !!_state?.isMultiPage; },

  /** Reads the persisted 'meshCropping' pref into _meshMaskEnabled. Call
   *  once at app startup, NOT per atlas load -- this toggle persists across
   *  sessions the same way other startup-restored prefs do (
   *  copySkel). */
  async init_mesh_mask_from_pref() {
    _meshMaskEnabled = await AtlasAPI.get_pref('meshCropping', true);
  },

  /** Reads the persisted 'forcedResizing' pref. Call once at startup. */
  async init_forced_resizing_from_pref() {
    _forcedResizing = !!(await AtlasAPI.get_pref('forcedResizing', false));
  },

  get_forced_resizing() {
    return _forcedResizing;
  },

  async set_forced_resizing(enabled) {
    _forcedResizing = !!enabled;
    AtlasAPI.set_pref('forcedResizing', _forcedResizing);
  },

  /** Reads the persisted 'meshHullView' pref. Call once at startup. */
  async init_mesh_hull_from_pref() {
    _meshHullEnabled = !!(await AtlasAPI.get_pref('meshHullView', false));
  },

  get_mesh_hull_enabled() {
    return _meshHullEnabled;
  },

  async set_mesh_hull_enabled(enabled) {
    _meshHullEnabled = !!enabled;
    AtlasAPI.set_pref('meshHullView', _meshHullEnabled);
  },

  /** Composite-space hulls for the current View selection. Null when the
   *  overlay is off. Independent of Mesh Cropping. */
  get_mesh_hull_layout(names) {
    if (!_meshHullEnabled) return null;
    const hulls = _hulls.get(_viewKey(names));
    return hulls ? { items: hulls.map(h => ({ points: h.points.map(([x,y]) => ({x,y})) })) } : null;
  },

  get_mesh_mask_state() { return { available: !!_state?.mesh.available, enabled: _meshMaskEnabled, skelFileName: _state?.mesh.skelName || null, unavailableReason: _state?.mesh.reason || null }; },

  async set_mesh_mask_enabled(enabled) {
    _meshMaskEnabled = !!enabled;
    AtlasAPI.set_pref('meshCropping', _meshMaskEnabled);
    return null; // Mesh Cropping no longer affects repack output at all now that the two toggles are decoupled -- no rerun needed.
  },

  get_settings() { return _state?.settings || null; },
  async init_settings_from_prefs() {
    const partial = {};
    for (const [key, pref, value] of SETTING_PREFS) partial[key] = await AtlasAPI.get_pref(pref, value);
    try { await AtlasAPI.set_settings(partial, {busy: false}); }
    catch (e) {
      if (!(e instanceof HostError) || e.type !== 'ValueError') throw e;
      for (const [key, pref, value] of SETTING_PREFS) { partial[key] = value; await AtlasAPI.set_pref(pref, value); }
      await AtlasAPI.set_settings(partial, {busy: false});
    }
  },
  async set_settings(partial, {busy = true} = {}) {
    const run = async () => {
      const result = await callHost('set_settings', [partial]);
      // Persist the requested values before publishing state: the chip can
      // become visible before Edit preview conversion finishes. Never save
      // effective fallback/locked values in place of the user's choice.
      for (const [key, pref] of SETTING_PREFS) {
        if (Object.hasOwn(partial, key)) await AtlasAPI.set_pref(pref, partial[key]);
      }
      // `unchanged`: same packed pages, so no image came back to convert.
      const applied = result?.state && !result.unchanged ? await acceptEditResult(result) : result;
      if (result?.unchanged) _state = result.state;
      else if (result && !result.state) _state = result;
      _previewCache.invalidateAll(); _hulls.clear();
      await publishSettings();
      return applied;
    };
    if (!busy) return run();
    // Show the busy overlay only when the engine has to pack; a cached pack,
    // an atlas without edits or View Mode answer at once (still locked).
    const needsPack = await callHost('settings_need_pack', [partial]).catch(() => true);
    return needsPack ? withBusy(BUSY_LABEL, run) : withLock(run);
  },

  /** Apply a `.skel` File (picker or drag-drop). Replaces any previously
   *  captured sibling. Returns false if `file` is missing or not a .skel. */
  async apply_skel_file(file) {
    if (!file || !isSkelFilename(file.name)) return false;
    try {
    const result = await callHost('set_skeleton', [file.name, new Uint8Array(await file.arrayBuffer())]);
    if (result.state) {
      const preview = await acceptEditResult(result);
      await (await import('./modify-mode.js')).onModPreviewReceived(preview);
    } else _state = result;
    await publishSettings();
    _currentSkel = {name: file.name, blob: file};
    if (_sourceFiles) _sourceFiles.skel = _currentSkel;
    _skelEpoch++;
    _previewCache.invalidateAll(); _hulls.clear();
    return true;
    } catch (e) {
      if (e instanceof HostError) { window.showToast(e.message, 'error'); return false; }
      throw e;
    }
  },

  /** Manual .skel picker — covers sibling auto-resolve misses and the
   *  browser/PWA target where .skel isn't in the picked/dropped file set.
   *  pywebview uses the native Open dialog at the atlas folder, same as
   *  pick_mod_image / pick_page_image — a plain file input cannot. */
  async pick_skel_file() {
    if (isPywebviewDesktop()) {
      const file = await desktopCall('pick_skel');
      if (!file) return false;
      return AtlasAPI.apply_skel_file(await fileFromUrl(file, 'application/octet-stream'));
    }
    const files = await _pickFiles({ accept: '.skel', multiple: false });
    if (files.length === 0) return false;
    return AtlasAPI.apply_skel_file(files[0]);
  },

  /**
   * Open a file picker that accepts .atlas and image files. Under pywebview,
   * use the native single-file Open dialog + list_sibling_page_images() to
   * auto-resolve sibling PNGs from disk (matching the old Python-engine
   * desktop UX) instead of requiring the user to multi-select the atlas +
   * every PNG together in a browser `<input type=file>` picker.
   */
  async choose_file() {
    if (isPywebviewDesktop()) {
      try {
        const bundle = await desktopCall('pick_atlas');
        return bundle ? AtlasAPI.load_atlas_bundle(bundle) : false;
      } catch (e) {
        if (!(e instanceof HostError)) throw e;
        window.showToast(`Failed to load atlas file: ${e.message}`, 'error');
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

  async load_atlas_bundle(bundle) {
    const atlasFile = await fileFromUrl(bundle.atlas, 'text/plain');
    const pages = {};
    await Promise.all(Object.entries(bundle.pages).map(async ([name, file]) => {
      try { pages[name] = await fileFromUrl(file, 'image/png'); }
      catch (_) { /* Missing page: let the attachment dialog ask for it. */ }
    }));
    const skelFile = bundle.skel ? await fileFromUrl(bundle.skel, 'application/octet-stream') : null;
    return _loadAtlasFiles(atlasFile, pages, bundle.dir, skelFile ? [skelFile] : []);
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

  get_region_names() { return (_edit ? _state?.regions : _state?.pristineRegions) || []; },

  get_page_names() { return [...(_state?.pages || [])]; },

  get_region_page_name(key) { return _state?.regionPages[key] || ''; },

  preview_is_cached(names) { return _previewCache.has(_viewKey(names)); },

  async get_preview(names) {
    if (!_state || !names?.length) return null;
    const key = _viewKey(names);
    const hit = _previewCache.get(key);
    if (hit) return hit;
    try {
      const raster = await callHost('preview', [names, _meshMaskEnabled && _state.mesh.available, _forcedResizing, true, _state.settings.alpha], {coalesce: 'preview'});
      if (!raster) return null;
      const url = await rasterToUrl(raster);
      _previewCache.set(key, url);
      _hulls.set(key, raster.hulls);
      for (const cachedKey of _hulls.keys()) {
        if (!_previewCache.has(cachedKey)) _hulls.delete(cachedKey);
      }
      return url;
    } catch (e) { if (e instanceof HostError && e.type === 'Superseded') return null; throw e; }
  },

  /**
   * Extract regions to files (downloads).
   * @param {{key:string,label:string}[]|null} regions  null = extract all
   *
   * Filenames are built from .key, not .label, deliberately. Today
   * label === key for every region by construction (see
   * get_region_names()), so this makes no visible difference yet -- but
   * it matters once a future Rename feature lets label diverge from key:
   * two regions could then share the same displayed label on purpose
   * while keeping distinct internal keys, and .label would collide as a
   * filename. .key stays collision-safe by construction and matches this
   * function's pre-refactor behavior for every region. (Historically,
   * before this refactor settled on that default, label was derived from
   * the parser's atlasName field instead -- under which two regions
   * sharing a literal atlas name, e.g. 'dagger'/'dagger#2', already had
   * the same label with different keys and collided this exact way; see
   * commit 6569241 for that incident.)
   */
  async extract_files(regions) {
    if (!_state) return 'No atlas loaded.';
    const targets = regions || AtlasAPI.get_region_names();
    if (targets.length === 0) return 'No regions to extract.';

    const run = async () => {
    const results = await callHost('extract', [regions ? regions.map(r => r.key) : null, _meshMaskEnabled && _state.mesh.available, _state.settings.alpha]);
    const extracted = results.map(({filename, png}) => ({filename, blob: new Blob([png], {type: 'image/png'})}));
    const count = extracted.length;

    if (extracted.length === 0) return 'No regions to extract.';

    try {
      // Single region, installed PWA / pywebview desktop: a native Save As
      // dialog defaulting to "{region}.png" — matches the old Python engine's
      // extract_files() single-file save flow (a folder picker for one file
      // was a desktop-UX regression, found via parity audit 2026-08-23).
      if (extracted.length === 1 && _useFolderPicker()) {
        await _saveBlobWithDialog(extracted[0].filename, extracted[0].blob);
        return `Successfully extracted ${count} image${count !== 1 ? 's' : ''}.`;
      }

      // Installed PWA / pywebview desktop, multiple regions: pick a folder and write the PNGs into it.
      if (_useFolderPicker()) {
        const folder = await platform.pickSaveFolder();
        if (!folder) return 'Cancelled';
        await platform.writeFilesToFolder(
          folder,
          extracted.map(item => ({ name: item.filename, data: item.blob })),
          _confirmDialog,
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
    };
    if (targets.length > 1) return withBusy(BUSY_LABEL, run);
    return run();
  },


  preview_url_is_cached(url) { return _previewCache.owns(url); },
  has_pending_modifications() { return !!(_edit && _state?.hasPending); },
  get_modified_region_names() { return _edit ? _state?.modifiedKeys || [] : []; },
  exit_modify_mode() {
    _edit = false;
    if (_state) _state = {..._state, regions: _state.pristineRegions};
    callHost('exit_edit', []).catch(e => {
      if (!(e instanceof HostError && e.message === 'not in edit mode')) console.error(String(e.message).split('\n')[0]);
    });
  },
  async save_image() {
    if (!_state) return null;
    const png = await callHost('save_image', [getSelectedKeys(), _meshMaskEnabled && _state.mesh.available, _forcedResizing, _state.settings.alpha]);
    return png ? new Blob([png], {type: 'image/png'}) : null;
  },
  async enter_modify_mode() {
    if (!_state) return null;
    return withBusy(BUSY_LABEL, async () => {
      const result = await editCall('enter_edit', []);
      _edit = true; _activePageIndex = 0;
      return result;
    });
  },
  async reset_modifications() {
    return withBusy(BUSY_LABEL, async () => {
      const result = await editCall('reset_edits', []);
      _activePageIndex = 0;
      return result;
    });
  },
  validate_region_name(name, excludeKey = null) { return callHost('validate_name', [name, excludeKey]); },
  async get_modify_page_preview(index) {
    if (typeof index === 'string') index = _editPages.indexOf(index);
    const result = await callHost('page_preview', [index]);
    _activePageIndex = result.activeIndex;
    return {...result, image: await rasterToUrl(result.image)};
  },
  async process_mod_image(source, keys) {
    if (!keys?.length) return null;
    return withBusy(BUSY_LABEL, async () => {
      try {
        return await editCall('modify', [keys, await imageBytes(source), _state.settings.alpha]);
      } catch (e) { await window.showAlert(e.message, 'Cannot modify atlas'); return null; }
    });
  },
  async select_mod_image(keys) {
    let file;
    if (isPywebviewDesktop()) {
      const picked = await desktopCall('pick_mod_image');
      if (!picked) return null;
      file = await fileFromUrl(picked, 'image/png');
    } else { [file] = await _pickFiles({accept: IMAGE_PICKER_ACCEPT}); }
    return file ? AtlasAPI.process_mod_image(file, keys) : null;
  },
  async save_page_image() {
    const png = await callHost('page_png', [_activePageIndex, _state.settings.alpha]);
    return new Blob([png], {type: 'image/png'});
  },
  async save_modified() {
    try {
      const output = await callHost('save_output', []);
      if (typeof output.revision !== 'number') throw new Error('save_output returned no revision');
      const outputs = output.pages.map(p => ({name: p.name, data: new Blob([p.png], {type: 'image/png'})}));
      outputs.push({name: _currentAtlasFilename, data: output.text});
      const skel = await _skelForSave();
      if (skel) outputs.push({name: skel.name, data: skel.blob});
      if (_useFolderPicker()) {
        const folder = await platform.pickSaveFolder();
        if (!folder) return 'Cancelled';
        await platform.writeFilesToFolder(folder, outputs, _confirmDialog);
        _state = await callHost('mark_saved', [output.revision]);
        return (isPywebviewDesktop() ? `Saved to: ${folder.path}` : 'Saved to selected folder.')
          + (_state.hasPending ? ' (edits made during the save were not included)' : '');
      }
      await _downloadBlob(`${_extractZipBaseName()}_modified.zip`, await createZip(outputs));
      _state = await callHost('mark_saved', [output.revision]);
      return 'Saved as zip: ' + outputs.map(o => o.name).join(' and ')
        + (_state.hasPending ? ' (edits made during the save were not included)' : '');
    } catch (e) { return e.name === 'AbortError' ? 'Cancelled' : 'Error: ' + e.message; }
  },
  async add_region(file, name, page = null) { return withBusy(BUSY_LABEL, async () => editCall('add', [name, await imageBytes(file), page, _state.settings.alpha])); },
  rename_region(key, name) { return withBusy(BUSY_LABEL, () => editCall('rename', [key, name])); },
  remove_regions(keys) { return withBusy(BUSY_LABEL, () => editCall('remove', [keys])); },
};
export { _loadAtlasFiles };

async function imageBytes(source) {
  return new Uint8Array(await source.arrayBuffer());
}
async function editCall(method, args) {
  const result = await callHost(method, args);
  return acceptEditResult(result);
}
async function acceptEditResult(result) {
  _state = result.state;
  await publishSettings();
  _editPages = result.pages;
  if (_activePageIndex >= _editPages.length) _activePageIndex = 0;
  return {...result, image: await rasterToUrl(result.image)};
}
onEngineDied(async (message) => {
  console.error(String(message).split('\n')[0]);
  window.showAlert('The AtlasToolkit engine stopped and your edits were lost. The atlas will be reloaded.', 'Engine stopped');
  const sources = _sourceFiles;
  stopEngine(); engineStartupError = null;
  try {
    await startEngine();
    _edit = false;
    await AtlasAPI.init_settings_from_prefs();
    if (!sources) return;
    if (!await _loadAtlasFiles(sources.atlas, sources.pages, _currentAtlasDirectory)) return;
    if (sources.skel) await AtlasAPI.apply_skel_file(sources.skel.blob);
    const {state} = await import('./state.js');
    const {setMode, updateMeshCroppingUI} = await import('./modify-mode.js');
    const {loadRegions, updateButtons, updateRemoveButtonState, updateRenameButtonState} = await import('./region-list.js');
    const {resetPreview, clearOverlay, updatePreview, updateSaveMergedButton} = await import('./preview.js');
    state.selectedIndices.clear(); state.hasModImage = false; state.modifyRegionBounds = {};
    state.modifyPages = []; state.modifyRegionPages = {};
    state.modifyActivePage = null; state.modifyActivePageIndex = 0;
    _activePageIndex = 0; _editPages = [];
    setMode('extract'); resetPreview(); clearOverlay();
    await loadRegions(); updateButtons(); updateRemoveButtonState(); updateRenameButtonState();
    updateMeshCroppingUI(); await updatePreview([]); updateSaveMergedButton();
  } catch (e) {
    setEngineStartupError(e);
    console.error(String(e.message).split('\n')[0]);
  }
});

const SETTING_PREFS = [
  ['packer', 'packer', 'shelf'], ['padding', 'packPadding', 2],
  ['meshAware', 'packMeshAware', true], ['splitComposite', 'packSplitComposite', false],
  ['keepLayout', 'packKeepLayout', true], ['alpha', 'imageAlpha', 'straight'],
  ['bleed', 'packBleed', true],
];
async function publishSettings() {
  if (_state?.settings) (await import('./settings-panel.js')).renderSettings(_state.settings);
}
