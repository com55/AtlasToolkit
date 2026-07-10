/**
 * platform.js
 * Small facade isolating browser/File-System-Access-API quirks (file pickers,
 * folder writes, preferences) behind a stable interface for the rest of the
 * app. PWA-only — there is no native shell here.
 */

const ATLAS_EXTS = new Set(['atlas', 'txt']);

function _ext(name) {
  const idx = String(name || '').lastIndexOf('.');
  return idx >= 0 ? name.slice(idx + 1).toLowerCase() : '';
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
 * Pick a folder to save into, via the File System Access API.
 * Returns null if unsupported or the user cancels.
 */
export async function pickSaveFolder() {
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
 * target: a FileSystemDirectoryHandle (from pickSaveFolder).
 */
export async function writeFilesToFolder(target, files) {
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

// ─── Preferences (localStorage) ────────────────────────────────────────────────

export async function loadPref(key, defaultValue = null) {
  try {
    const raw = localStorage.getItem(`atlastoolkit.${key}`);
    return raw !== null ? JSON.parse(raw) : defaultValue;
  } catch (_) { return defaultValue; }
}

export function savePref(key, value) {
  try { localStorage.setItem(`atlastoolkit.${key}`, JSON.stringify(value)); } catch (_) {}
}

export const platform = {
  pickAtlasFile,
  pickSaveFolder,
  writeFilesToFolder,
  loadPref,
  savePref,
};
