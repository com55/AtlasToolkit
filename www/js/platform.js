/**
 * platform.js
 * Feature-detected facade over Tauri APIs. In browser/PWA, all functions either
 * fall back to web equivalents or no-op. Loaded via `withGlobalTauri: true`,
 * so we read Tauri APIs off `window.__TAURI__` directly (no bundler).
 */

const T = (typeof window !== 'undefined') ? window.__TAURI__ : null;
export const isTauri = !!(T && T.core && typeof T.core.invoke === 'function');

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

async function _readBytes(path) {
  const bytes = await T.fs.readFile(path);
  return bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
}

async function _exists(path) {
  try { return !!(await T.fs.exists(path)); } catch (_) { return false; }
}

function _bytesToBlob(bytes, type = 'application/octet-stream') {
  // Copy into a fresh ArrayBuffer so Blob doesn't reference Tauri's buffer.
  return new Blob([bytes.slice().buffer], { type });
}

function _bytesToFile(bytes, name, type = 'image/png') {
  return new File([bytes.slice().buffer], name, { type });
}

async function _readTextFile(file) {
  return await new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = e => resolve(e.target.result);
    r.onerror = reject;
    r.readAsText(file);
  });
}

function _extractRequiredPagesFromText(atlasText) {
  // Lightweight parser: page headers are lines ending in an image extension
  // followed by a line containing a `:` (page metadata like size/format/filter).
  const lines = atlasText.split(/\r?\n/);
  const pages = [];
  for (let i = 0; i < lines.length; i++) {
    const s = lines[i].trim();
    if (!s || s.includes(':')) continue;
    const ext = _ext(s);
    if (!IMAGE_EXTS.has(ext)) continue;
    // Look ahead for a metadata line.
    for (let j = i + 1; j < lines.length; j++) {
      const next = lines[j].trim();
      if (!next) continue;
      if (next.includes(':')) {
        if (!pages.includes(s)) pages.push(s);
      }
      break;
    }
  }
  return pages;
}

/**
 * Pick a single .atlas (or compatible) file. Returns { path, file } or null.
 * In Tauri: native dialog returns a path; we also read bytes so callers that
 * want a File object can use it directly. In web: hidden <input type=file>.
 */
export async function pickAtlasFile() {
  if (isTauri) {
    const selected = await T.dialog.open({
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

  const atlasText = await T.fs.readTextFile(atlasPath);
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

  return {
    atlasText,
    atlasPath,
    atlasName: _baseName(atlasPath),
    imageBlobs,
    skelBlob,
    skelName,
    missingPages,
  };
}

/**
 * Read a list of mixed paths from a drag-drop event. Returns the same shape as
 * readAtlasWithSiblings + the original PNG File objects under imageBlobs (by
 * the dropped filename), plus any extra PNGs that weren't requested by the
 * atlas (so the caller can still pass them through `load_from_files`).
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

  // Read all dropped images first so we can map them by basename regardless of
  // whether the atlas was also dropped.
  const droppedImageFiles = new Map();
  for (const p of images) {
    const bytes = await _readBytes(p);
    const name = _baseName(p);
    droppedImageFiles.set(name, _bytesToFile(bytes, name, 'image/png'));
  }

  if (atlases.length === 0) {
    return { atlasPath: null, droppedImageFiles };
  }

  // Use the first atlas; auto-load its siblings from disk.
  const result = await readAtlasWithSiblings(atlases[0]);

  // Overlay any dropped PNGs by basename — dropped files take priority over
  // sibling-resolved ones (lets the user override).
  for (const [name, file] of droppedImageFiles) {
    if (result.imageBlobs.has(name)) result.imageBlobs.set(name, file);
  }
  // Fill remaining missing pages from drops if possible.
  result.missingPages = result.missingPages.filter(name => {
    if (droppedImageFiles.has(name)) {
      result.imageBlobs.set(name, droppedImageFiles.get(name));
      return false;
    }
    return true;
  });

  return { ...result, droppedImageFiles };
}

/** Pick a folder to save into. Returns a path string in Tauri, a directory
 *  handle in web (FS-Access), or null in either case if cancelled. */
export async function pickSaveFolder(defaultPath = null) {
  if (isTauri) {
    const selected = await T.dialog.open({
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
 * data: string | Blob | Uint8Array | ArrayBuffer.
 */
export async function writeFilesToFolder(target, files) {
  if (isTauri && typeof target === 'string') {
    for (const item of files) {
      const path = _joinPath(target, item.name);
      const data = item.data;
      if (typeof data === 'string') {
        await T.fs.writeTextFile(path, data);
      } else if (data instanceof Blob) {
        const buf = new Uint8Array(await data.arrayBuffer());
        await T.fs.writeFile(path, buf);
      } else if (data instanceof Uint8Array) {
        await T.fs.writeFile(path, data);
      } else if (data instanceof ArrayBuffer) {
        await T.fs.writeFile(path, new Uint8Array(data));
      } else {
        throw new Error('Unsupported data type for writeFilesToFolder');
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
 * Subscribe to Tauri drag-drop events. Returns an unsubscribe function. In web,
 * returns a no-op — the browser's drop event already fires inside the WebView.
 */
export async function subscribeDragDrop({ onEnter, onOver, onLeave, onDrop }) {
  if (!isTauri) return () => {};
  const wv = T.webview && typeof T.webview.getCurrentWebview === 'function'
    ? T.webview.getCurrentWebview()
    : (T.webviewWindow && T.webviewWindow.getCurrentWebviewWindow && T.webviewWindow.getCurrentWebviewWindow());
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
}

/** Subscribe to "open-file" events emitted from Rust on second-instance launches. */
export async function subscribeOpenFile(handler) {
  if (!isTauri || !T.event || typeof T.event.listen !== 'function') return () => {};
  return await T.event.listen('open-file', (event) => {
    if (typeof event.payload === 'string') handler(event.payload);
  });
}

/** Read the startup file path (Tauri only). One-shot; consumed by the backend. */
export async function getStartupFile() {
  if (!isTauri) return null;
  try {
    return await T.core.invoke('get_startup_file');
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
  const dir = await T.path.appConfigDir();
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
      const txt = await T.fs.readTextFile(path);
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
    const dir = await T.path.appConfigDir();
    if (!(await _exists(dir))) {
      await T.fs.mkdir(dir, { recursive: true });
    }
    await T.fs.writeTextFile(path, JSON.stringify(_prefsCache));
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
  // Fire-and-forget; loadPref ensured cache exists if any read happened.
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
