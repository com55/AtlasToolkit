/**
 * platform.js
 * Feature-detected facade over Tauri APIs.
 *
 * Detection uses window.__TAURI_INTERNALS__ (always present in Tauri,
 * regardless of withGlobalTauri setting) so isTauri is reliable.
 *
 * All file/dialog/path operations use invoke() directly via __TAURI_INTERNALS__
 * so they work even when plugin JS namespaces aren't bundled into __TAURI__.
 *
 * Event listening and webview drag-drop still use window.__TAURI__ (requires
 * withGlobalTauri: true) with graceful no-op fallback if unavailable.
 */

const _TI = (typeof window !== 'undefined') ? window.__TAURI_INTERNALS__ : null;
export const isTauri = !!(_TI && typeof _TI.invoke === 'function');

// The public __TAURI__ global (withGlobalTauri: true) — used only for
// event.listen and webview.getCurrentWebview which have no invoke equivalent.
const _T = (isTauri && typeof window !== 'undefined') ? window.__TAURI__ : null;

const IMAGE_EXTS = new Set(['png']);
const ATLAS_EXTS = new Set(['atlas', 'txt']);

function _ext(name) {
  const idx = String(name || '').lastIndexOf('.');
  return idx >= 0 ? name.slice(idx + 1).toLowerCase() : '';
}

function _baseName(path) {
  const norm = String(path).replace(/\\/g, '/');
  const idx = norm.lastIndexOf('/');
  return idx >= 0 ? norm.slice(idx + 1) : norm;
}

function _dirName(path) {
  const norm = String(path).replace(/\\/g, '/');
  const idx = norm.lastIndexOf('/');
  return idx >= 0 ? path.slice(0, idx) : '';
}

function _replaceSuffix(path, newSuffix) {
  const idx = path.lastIndexOf('.');
  return idx >= 0 ? path.slice(0, idx) + newSuffix : path + newSuffix;
}

function _joinPath(dir, name) {
  if (!dir) return name;
  const sep = dir.includes('\\') && !dir.includes('/') ? '\\' : '/';
  return dir.endsWith('/') || dir.endsWith('\\') ? dir + name : dir + sep + name;
}

// ─── Low-level invoke wrappers ────────────────────────────────────────────────

/** Calls a Tauri command. Always use this instead of T.plugin.method(). */
function _invoke(cmd, args) {
  return _TI.invoke(cmd, args || {});
}

async function _readText(path) {
  return _invoke('plugin:fs|read_text_file', { path });
}

async function _readBytes(path) {
  const result = await _invoke('plugin:fs|read_file', { path });
  if (result instanceof Uint8Array) return result;
  if (result instanceof ArrayBuffer) return new Uint8Array(result);
  // Some Tauri versions return a plain number array
  if (Array.isArray(result)) return new Uint8Array(result);
  return new Uint8Array(result);
}

async function _writeText(path, data) {
  return _invoke('plugin:fs|write_text_file', { path, data });
}

async function _writeBytes(path, data) {
  // Tauri expects a plain number array for binary writes via invoke
  let arr;
  if (data instanceof Uint8Array) {
    arr = Array.from(data);
  } else if (data instanceof ArrayBuffer) {
    arr = Array.from(new Uint8Array(data));
  } else if (data instanceof Blob) {
    arr = Array.from(new Uint8Array(await data.arrayBuffer()));
  } else {
    arr = Array.from(data);
  }
  return _invoke('plugin:fs|write_file', { path, data: arr });
}

async function _exists(path) {
  try { return !!(await _invoke('plugin:fs|exists', { path })); } catch (_) { return false; }
}

async function _mkdir(path) {
  return _invoke('plugin:fs|mkdir', { path, options: { recursive: true } });
}

// BaseDirectory.AppConfig = 13 in Tauri 2
async function _appConfigDir() {
  return _invoke('plugin:path|resolve_directory', { directory: 13, path: '' });
}

function _bytesToBlob(bytes, type = 'application/octet-stream') {
  return new Blob([bytes.slice().buffer], { type });
}

function _bytesToFile(bytes, name, type = 'image/png') {
  return new File([bytes.slice().buffer], name, { type });
}

function _extractRequiredPagesFromText(atlasText) {
  const lines = atlasText.split(/\r?\n/);
  const pages = [];
  for (const line of lines) {
    const s = line.trim();
    if (!s || s.includes(':')) continue;
    if (!IMAGE_EXTS.has(_ext(s))) continue;
    if (!pages.includes(s)) pages.push(s);
  }
  return pages;
}

// ─── Public API ───────────────────────────────────────────────────────────────

/**
 * Pick a single .atlas (or compatible) file.
 * Tauri: native dialog via invoke. Web: hidden <input type=file>.
 */
export async function pickAtlasFile() {
  if (isTauri) {
    const selected = await _invoke('plugin:dialog|open', {
      multiple: false,
      directory: false,
      filters: [
        { name: 'Atlas', extensions: ['atlas', 'txt'] },
        { name: 'All Files', extensions: ['*'] },
      ],
    });
    if (!selected) return null;
    const path = Array.isArray(selected) ? selected[0] : selected;
    return { path, file: null };
  }

  return await new Promise((resolve) => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.atlas,.txt,text/plain,image/png,.png';
    input.multiple = true;
    let settled = false;
    input.addEventListener('change', (e) => {
      settled = true;
      const files = Array.from(e.target.files || []);
      if (files.length === 0) return resolve(null);
      const atlas = files.find(f => ATLAS_EXTS.has(_ext(f.name))) || files[0];
      resolve({ path: null, file: atlas, extraFiles: files });
    });
    input.click();
    setTimeout(() => { if (!settled) resolve(null); }, 5 * 60 * 1000);
  });
}

/**
 * Read an atlas + siblings (PNG pages, optional .skel) from disk.
 * Tauri only. Returns { atlasText, atlasPath, atlasName, imageBlobs: Map, skelBlob?, missingPages }.
 */
export async function readAtlasWithSiblings(atlasPath) {
  if (!isTauri) throw new Error('readAtlasWithSiblings requires Tauri');

  const atlasText = await _readText(atlasPath);
  const dir = _dirName(atlasPath);
  const pageNames = _extractRequiredPagesFromText(atlasText);

  const imageBlobs = new Map();
  const missingPages = [];
  for (const name of pageNames) {
    const candidate = _joinPath(dir, name);
    if (await _exists(candidate)) {
      const bytes = await _readBytes(candidate);
      imageBlobs.set(name, _bytesToFile(bytes, name, 'image/png'));
    } else {
      missingPages.push(name);
    }
  }

  let skelBlob = null;
  let skelName = null;
  const skelPath = _replaceSuffix(atlasPath, '.skel');
  if (await _exists(skelPath)) {
    const bytes = await _readBytes(skelPath);
    skelName = _baseName(skelPath);
    skelBlob = _bytesToBlob(bytes, 'application/octet-stream');
  }

  return { atlasText, atlasPath, atlasName: _baseName(atlasPath), imageBlobs, skelBlob, skelName, missingPages };
}

/**
 * Read a list of mixed paths from a drag-drop event.
 */
export async function readDroppedPaths(paths) {
  if (!isTauri) throw new Error('readDroppedPaths requires Tauri');

  const atlases = [];
  const images = [];
  for (const p of paths) {
    const ext = _ext(p);
    if (ATLAS_EXTS.has(ext)) atlases.push(p);
    else if (IMAGE_EXTS.has(ext)) images.push(p);
  }

  const droppedImageFiles = new Map();
  for (const p of images) {
    const bytes = await _readBytes(p);
    const name = _baseName(p);
    droppedImageFiles.set(name, _bytesToFile(bytes, name, 'image/png'));
  }

  if (atlases.length === 0) {
    return { atlasPath: null, droppedImageFiles };
  }

  const result = await readAtlasWithSiblings(atlases[0]);

  for (const [name, file] of droppedImageFiles) {
    if (result.imageBlobs.has(name)) result.imageBlobs.set(name, file);
  }
  result.missingPages = result.missingPages.filter(name => {
    if (droppedImageFiles.has(name)) {
      result.imageBlobs.set(name, droppedImageFiles.get(name));
      return false;
    }
    return true;
  });

  return { ...result, droppedImageFiles };
}

/**
 * Pick a folder to save into.
 * Tauri: native folder dialog. Web: showDirectoryPicker or null.
 */
export async function pickSaveFolder(defaultPath = null) {
  if (isTauri) {
    const selected = await _invoke('plugin:dialog|open', {
      directory: true,
      multiple: false,
      defaultPath: defaultPath || undefined,
    });
    return selected || null;
  }
  if (typeof window.showDirectoryPicker !== 'function') return null;
  try {
    return await window.showDirectoryPicker({ mode: 'readwrite', id: 'atlastoolkit-extract-folder' });
  } catch (e) {
    if (e?.name === 'AbortError') return null;
    throw e;
  }
}

/**
 * Write a list of {name, data} to a folder.
 * target: Tauri path string OR a FileSystemDirectoryHandle.
 */
export async function writeFilesToFolder(target, files) {
  if (isTauri && typeof target === 'string') {
    for (const item of files) {
      const path = _joinPath(target, item.name);
      const data = item.data;
      if (typeof data === 'string') {
        await _writeText(path, data);
      } else {
        await _writeBytes(path, data);
      }
    }
    return;
  }

  // Web: target is a directory handle.
  if (target && typeof target.getFileHandle === 'function') {
    for (const item of files) {
      const data = item.data;
      const blob = data instanceof Blob
        ? data
        : typeof data === 'string'
          ? new Blob([data], { type: 'text/plain' })
          : new Blob([data]);
      const fh = await target.getFileHandle(item.name, { create: true });
      const writable = await fh.createWritable();
      await writable.write(blob);
      await writable.close();
    }
    return;
  }

  throw new Error('writeFilesToFolder: invalid target');
}

/**
 * Subscribe to Tauri drag-drop events.
 * Requires withGlobalTauri: true for webview APIs.
 */
export async function subscribeDragDrop({ onEnter, onOver, onLeave, onDrop }) {
  if (!isTauri) return () => {};
  try {
    const wv = _T?.webview?.getCurrentWebview?.()
      ?? _T?.webviewWindow?.getCurrentWebviewWindow?.();
    if (!wv || typeof wv.onDragDropEvent !== 'function') return () => {};
    const unlisten = await wv.onDragDropEvent((event) => {
      const p = event.payload;
      const type = p?.type || p?.event;
      if (type === 'enter') onEnter && onEnter(p.paths || []);
      else if (type === 'over') onOver && onOver(p);
      else if (type === 'leave' || type === 'cancel') onLeave && onLeave();
      else if (type === 'drop') onDrop && onDrop(p.paths || []);
    });
    return unlisten;
  } catch (e) {
    console.warn('[platform] subscribeDragDrop failed:', e);
    return () => {};
  }
}

/** Subscribe to "open-file" events emitted from Rust. */
export async function subscribeOpenFile(handler) {
  if (!isTauri) return () => {};
  try {
    const listen = _T?.event?.listen;
    if (typeof listen !== 'function') return () => {};
    return await listen('open-file', (event) => {
      if (typeof event.payload === 'string') handler(event.payload);
    });
  } catch (e) {
    console.warn('[platform] subscribeOpenFile failed:', e);
    return () => {};
  }
}

/** Read the startup file path (Tauri only). One-shot. */
export async function getStartupFile() {
  if (!isTauri) return null;
  try {
    return await _invoke('get_startup_file');
  } catch (_) {
    return null;
  }
}

// ─── Preferences (config.json in appConfigDir on Tauri; localStorage on web) ──

let _prefsCache = null;
let _prefsLoaded = false;
let _prefsPath = null;

async function _prefsFilePath() {
  if (_prefsPath) return _prefsPath;
  const dir = await _appConfigDir();
  const sep = dir.includes('\\') && !dir.includes('/') ? '\\' : '/';
  _prefsPath = dir + (dir.endsWith('/') || dir.endsWith('\\') ? '' : sep) + 'config.json';
  return _prefsPath;
}

async function _ensurePrefsLoaded() {
  if (_prefsLoaded) return;
  _prefsLoaded = true;
  if (!isTauri) { _prefsCache = {}; return; }
  try {
    const path = await _prefsFilePath();
    if (await _exists(path)) {
      const txt = await _readText(path);
      _prefsCache = JSON.parse(txt);
    } else {
      _prefsCache = {};
    }
  } catch (e) {
    console.warn('Failed to load prefs:', e);
    _prefsCache = {};
  }
}

async function _savePrefsToDisk() {
  if (!isTauri) return;
  try {
    const path = await _prefsFilePath();
    const dir = await _appConfigDir();
    if (!(await _exists(dir))) await _mkdir(dir);
    await _writeText(path, JSON.stringify(_prefsCache));
  } catch (e) {
    console.warn('Failed to save prefs:', e);
  }
}

export async function loadPref(key, defaultValue = null) {
  if (!isTauri) {
    try {
      const raw = localStorage.getItem(`atlastoolkit.${key}`);
      return raw !== null ? JSON.parse(raw) : defaultValue;
    } catch (_) { return defaultValue; }
  }
  await _ensurePrefsLoaded();
  return key in _prefsCache ? _prefsCache[key] : defaultValue;
}

export function savePref(key, value) {
  if (!isTauri) {
    try { localStorage.setItem(`atlastoolkit.${key}`, JSON.stringify(value)); } catch (_) {}
    return;
  }
  (async () => {
    await _ensurePrefsLoaded();
    _prefsCache[key] = value;
    await _savePrefsToDisk();
  })();
}

export const platform = {
  isTauri,
  pickAtlasFile,
  readAtlasWithSiblings,
  readDroppedPaths,
  pickSaveFolder,
  writeFilesToFolder,
  subscribeDragDrop,
  subscribeOpenFile,
  getStartupFile,
  loadPref,
  savePref,
};
