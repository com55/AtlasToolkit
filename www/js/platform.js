/**
 * platform.js
 * Small facade isolating browser/File-System-Access-API vs. pywebview-native
 * quirks (file pickers, folder writes, preferences) behind a stable
 * interface for the rest of the app. Callers never need to know which
 * backend is active.
 */

const ATLAS_EXTS = new Set(['atlas', 'txt']);

function _ext(name) {
  const idx = String(name || '').lastIndexOf('.');
  return idx >= 0 ? name.slice(idx + 1).toLowerCase() : '';
}

// ─── pywebview native bridge detection ─────────────────────────────────────
// Detection idiom matches the one already used in www/js/dialogs.js /
// ui/js/updates.js: pywebview.api exists once the native bridge is ready.

function _isPywebview() {
  return typeof window !== 'undefined' && !!(window.pywebview && window.pywebview.api);
}

/** True when running inside the pywebview desktop shell. */
export function isPywebviewDesktop() {
  return _isPywebview();
}

/**
 * Shared stacked/portrait-layout query (Phase 6 of the unify-js-engine
 * plan) — replaces what used to be two independently-implemented copies in
 * panel-resizer.js and preview.js. Desktop (pywebview) always reports
 * `false`, locking the layout to landscape/desktop regardless of window
 * size/aspect: reads the `pywebview` class set synchronously on `<html>` by
 * an inline script in index.html's `<head>` (before first paint, so the
 * media query below never has a chance to flash on) rather than
 * `isPywebviewDesktop()`/`window.pywebview.api`, which aren't guaranteed
 * ready that early (see script.js's `_waitForPywebviewReady()`).
 */
export function isPortrait() {
  if (document.documentElement.classList.contains('pywebview')) return false;
  return window.matchMedia('(orientation: portrait), (max-width: 900px)').matches;
}

/**
 * Base64-encode a Blob/string/binary payload — pywebview's `js_api` bridge
 * only accepts JSON-serializable arguments, so file bytes have to cross as
 * base64 rather than as a Blob/ArrayBuffer.
 */
export async function blobToBase64(data) {
  const blob = data instanceof Blob
    ? data
    : typeof data === 'string'
      ? new Blob([data], { type: 'text/plain' })
      : new Blob([data]);
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(',')[1] || '');
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });
}

/** Reconstruct a browser File from base64 bytes handed back by the
 * pywebview bridge (read_file_as_base64 / list_sibling_page_images). */
export function base64ToFile(base64, filename, mime) {
  const bytes = Uint8Array.from(atob(base64), c => c.charCodeAt(0));
  return new File([bytes], filename, { type: mime });
}

// ─── Public API ───────────────────────────────────────────────────────────────

/**
 * True on touch (mobile/tablet) devices. Their OS file pickers often show a
 * photo-only view for `accept="image/*"`, which can hide files the user
 * actually wants — pickers should fall back to an unrestricted browser there
 * and validate the selection afterward with fileMatchesAccept().
 */
export function isTouchDevice() {
  return typeof window !== 'undefined' && !!window.matchMedia?.('(pointer: coarse)').matches;
}

/** Does file match a comma-separated `accept` string (extensions and/or MIME types)? */
export function fileMatchesAccept(file, accept) {
  if (!accept) return true;
  const name = String(file?.name || '').toLowerCase();
  const type = String(file?.type || '').toLowerCase();
  return accept.split(',').map(s => s.trim().toLowerCase()).filter(Boolean).some((token) => {
    if (token.startsWith('.')) return name.endsWith(token);
    if (token.endsWith('/*')) return type.startsWith(token.slice(0, -1));
    return type === token;
  });
}

/**
 * Pick a single .atlas (or compatible) file via a hidden <input type=file>.
 */
export async function pickAtlasFile() {
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
 * Pick a folder to save into. pywebview: native folder dialog (returns a
 * plain path string). Browser: File System Access API (returns a
 * FileSystemDirectoryHandle). Returns null if unsupported or cancelled.
 * @param {string} [defaultDir] Starting directory for the native dialog (old
 *   Python engine always opened extract/save dialogs at the loaded atlas's
 *   folder; the initial JS port never passed one — found via parity audit,
 *   2026-08-23). No effect on the browser File System Access API path.
 */
export async function pickSaveFolder(defaultDir = '') {
  if (_isPywebview() && window.pywebview.api.pick_save_folder) {
    return (await window.pywebview.api.pick_save_folder(defaultDir)) || null;
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
 * target: a plain path string (pywebview) or a FileSystemDirectoryHandle
 * (browser, from pickSaveFolder).
 */
export async function writeFilesToFolder(target, files) {
  if (typeof target === 'string') {
    const sep = target.includes('\\') ? '\\' : '/';
    for (const item of files) {
      const base64 = await blobToBase64(item.data);
      await window.pywebview.api.write_file_bytes(`${target}${sep}${item.name}`, base64);
    }
    return;
  }

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
 * Save a single Blob via a Save As dialog (D1 — output pickers route
 * through the native bridge on pywebview, unlike input pickers).
 * Returns a FileSystemFileHandle (browser, truthy — pass back in as
 * `startIn` for the next save), `true` (pywebview — no handle concept), or
 * `null`/`false` if the user cancelled. Never throws on cancel.
 * @param {object} [opts]
 * @param {*} [opts.startIn] Browser File System Access API resume point.
 * @param {string} [opts.defaultDir] pywebview native dialog starting directory
 *   (see pickSaveFolder's doc comment for why this exists).
 */
export async function saveFileWithDialog(filename, blob, { startIn = null, defaultDir = '' } = {}) {
  if (_isPywebview() && window.pywebview.api.pick_save_file) {
    const path = await window.pywebview.api.pick_save_file(filename, defaultDir);
    if (!path) return null;
    await window.pywebview.api.write_file_bytes(path, await blobToBase64(blob));
    return true;
  }

  if (typeof window.showSaveFilePicker !== 'function') return null;
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
  if (startIn) pickerOptions.startIn = startIn;

  try {
    const fileHandle = await window.showSaveFilePicker(pickerOptions);
    const writable = await fileHandle.createWritable();
    await writable.write(blob);
    await writable.close();
    return fileHandle;
  } catch (e) {
    if (e?.name === 'AbortError') return null;
    throw e;
  }
}

// ─── Preferences ────────────────────────────────────────────────────────────
// pywebview: disk-backed via config.json (Api.get_pref/set_pref). Browser:
// localStorage.

export async function loadPref(key, defaultValue = null) {
  if (_isPywebview() && window.pywebview.api.get_pref) {
    try {
      const value = await window.pywebview.api.get_pref(key, defaultValue);
      return value === undefined ? defaultValue : value;
    } catch (_) { return defaultValue; }
  }
  try {
    const raw = localStorage.getItem(`atlastoolkit.${key}`);
    return raw !== null ? JSON.parse(raw) : defaultValue;
  } catch (_) { return defaultValue; }
}

export function savePref(key, value) {
  if (_isPywebview() && window.pywebview.api.set_pref) {
    window.pywebview.api.set_pref(key, value);
    return;
  }
  try { localStorage.setItem(`atlastoolkit.${key}`, JSON.stringify(value)); } catch (_) {}
}

export const platform = {
  pickAtlasFile,
  pickSaveFolder,
  writeFilesToFolder,
  saveFileWithDialog,
  loadPref,
  savePref,
};
