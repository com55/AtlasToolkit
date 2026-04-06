/**
 * atlas-api.js
 * Replaces pywebview.api with a pure-JS implementation.
 * Exposes the same async interface that script.js expects.
 */

import { autoConvertAtlas } from './atlas-converter.js';
import { AtlasProcessor, _loadImage } from './atlas-extracter.js';
import { AtlasModifier, parseAtlas } from './atlas-modifier.js';

// ─── State ────────────────────────────────────────────────────────────────────

let _processor = null;
let _modifier = null;
let _currentAtlasFilename = '';
let _currentAtlasText = '';
let _mergedCanvas = null;
let _mergedAtlasText = null;
let _preRepackCanvas = null;
let _preRepackText = null;

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

/**
 * Download data as a file.
 * @param {string} filename
 * @param {Blob} blob
 */
async function _downloadBlob(filename, blob) {
  // If running in Capacitor, use Filesystem plugin to write to Documents
  if (window.Capacitor && window.Capacitor.isNativePlatform() && window.Capacitor.Plugins.Filesystem) {
    const { Filesystem, Directory } = window.Capacitor.Plugins;
    const base64 = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result.split(',')[1]);
      reader.onerror = reject;
      reader.readAsDataURL(blob);
    });
    await Filesystem.writeFile({ path: filename, data: base64, directory: Directory.Documents });
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
    const onchange = (e) => {
      input.removeEventListener('change', onchange);
      resolve(Array.from(e.target.files));
    };
    input.addEventListener('change', onchange);
    input.click();
    // Safari/some Android may not fire 'cancel' event; resolve with [] after long delay
    setTimeout(() => { input.removeEventListener('change', onchange); resolve([]); }, 60000);
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

    const finalMap = { ...imageFileMap };

    // For any missing page images, prompt the user
    for (const pageName of requiredPages) {
      if (finalMap[pageName]) continue;
      // Ask user to locate the file
      const msg = `Please select the image file for: "${pageName}"`;
      // Simple prompt via toast + file picker
      if (typeof window.showToast === 'function') window.showToast(msg, 'info');
      const files = await _pickFiles({ accept: 'image/png,image/jpeg,image/webp,image/bmp,image/gif', multiple: false });
      if (files.length === 0) return false;
      finalMap[pageName] = files[0];
    }

    _processor = new AtlasProcessor(convertedText);
    await _processor.loadImages(finalMap);

    _currentAtlasFilename = atlasFile.name;
    _currentAtlasText = convertedText;

    // Clear modify state
    _modifier = null;
    _mergedCanvas = null;
    _mergedAtlasText = null;
    _preRepackCanvas = null;
    _preRepackText = null;

    return true;
  } catch (e) {
    console.error('load_atlas error:', e);
    return false;
  }
}

// ─── Public API (mirrors pywebview.api) ───────────────────────────────────────

export const AtlasAPI = {

  /** Restore preferences from localStorage. */
  get_pref(key, defaultValue = null) {
    try {
      const raw = localStorage.getItem(`atlastoolkit.${key}`);
      return raw !== null ? JSON.parse(raw) : defaultValue;
    } catch (_) { return defaultValue; }
  },

  set_pref(key, value) {
    try { localStorage.setItem(`atlastoolkit.${key}`, JSON.stringify(value)); } catch (_) {}
  },

  /** Called on startup; returns false (no file pre-loaded on Android). */
  async startup_check() {
    return false;
  },

  /** Open a file picker that accepts .atlas and image files. */
  async choose_file() {
    const files = await _pickFiles({
      accept: '.atlas,image/png,image/jpeg,image/webp,image/bmp',
      multiple: true,
    });
    if (files.length === 0) return false;

    const atlasFile = files.find(f => f.name.endsWith('.atlas'));
    if (!atlasFile) {
      if (typeof window.showToast === 'function') window.showToast('Please select an .atlas file.', 'error');
      return false;
    }

    const imageFileMap = {};
    for (const f of files) {
      if (!f.name.endsWith('.atlas')) imageFileMap[f.name] = f;
    }

    return _loadAtlasFiles(atlasFile, imageFileMap);
  },

  /** Load atlas directly from a File object (used by drag-and-drop). */
  async load_atlas_from_file(atlasFile, imageFileMap = {}) {
    return _loadAtlasFiles(atlasFile, imageFileMap);
  },

  get_region_names() {
    if (!_processor) return [];
    return Object.keys(_processor.regions);
  },

  async get_preview(names) {
    if (!_processor || !names || names.length === 0) return null;
    try {
      return _processor.getPreviewDataURL(names);
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

    let count = 0;
    for (const name of targets) {
      const canvas = _processor.extractRegion(name);
      if (!canvas) continue;
      try {
        const safeName = name.replace(/[^\w.\- ]/g, '_');
        const blob = await _canvasToBlob(canvas);
        await _downloadBlob(`${safeName}.png`, blob);
        count++;
      } catch (e) {
        console.error(`Failed to extract ${name}:`, e);
      }
    }
    return `Successfully extracted ${count} image${count !== 1 ? 's' : ''}.`;
  },

  // ── Modify Mode ────────────────────────────────────────────────────────────

  async enter_modify_mode() {
    if (!_processor) return null;
    try {
      const baseImg = _processor.getPageImage();
      if (!baseImg) return null;

      _modifier = new AtlasModifier(_currentAtlasText, _currentAtlasFilename, baseImg);
      _mergedCanvas = null;
      _mergedAtlasText = null;

      // Build region bounds for overlay: { name: [x, y, w, h, rotate] }
      const regionBounds = {};
      for (const [name, info] of Object.entries(_modifier.regions)) {
        regionBounds[name] = [...info.bounds, info.rotate];
      }

      // Convert base image to data URL for preview
      const baseCanvas = document.createElement('canvas');
      baseCanvas.width = baseImg.naturalWidth || baseImg.width;
      baseCanvas.height = baseImg.naturalHeight || baseImg.height;
      baseCanvas.getContext('2d').drawImage(baseImg, 0, 0);

      return { image: baseCanvas.toDataURL('image/png'), regions: regionBounds };
    } catch (e) {
      console.error('enter_modify_mode error:', e);
      return null;
    }
  },

  exit_modify_mode() {
    _modifier = null;
    _mergedCanvas = null;
    _mergedAtlasText = null;
    _preRepackCanvas = null;
    _preRepackText = null;
  },

  /** Pick a mod PNG and process it. */
  async select_mod_image(selectedNames, repack = false) {
    if (!_modifier) return null;
    const files = await _pickFiles({ accept: 'image/png,image/jpeg,image/webp', multiple: false });
    if (files.length === 0) return null;
    return AtlasAPI.process_mod_image(files[0], selectedNames, repack);
  },

  /** Process a mod image (File or canvas/img) for the selected regions. */
  async process_mod_image(source, selectedNames, repack = false) {
    if (!_modifier) return null;
    try {
      const img = source instanceof File ? await _loadImage(source) : source;
      const { mergedCanvas, atlasText } = _modifier.mergeModImage(img, selectedNames);

      _preRepackCanvas = mergedCanvas;
      _preRepackText = atlasText;

      let finalCanvas = mergedCanvas;
      let finalText = atlasText;

      if (repack) {
        const repacked = await _modifier.repack(mergedCanvas, atlasText);
        finalCanvas = repacked.canvas;
        finalText = repacked.atlasText;
      }

      _mergedCanvas = finalCanvas;
      _mergedAtlasText = finalText;

      const { regions: mergedRegions } = parseAtlas(finalText);
      const regionBounds = {};
      for (const [name, info] of Object.entries(mergedRegions)) {
        regionBounds[name] = [...info.bounds, info.rotate];
      }

      return { image: finalCanvas.toDataURL('image/png'), regions: regionBounds };
    } catch (e) {
      console.error('process_mod_image error:', e);
      if (typeof window.showToast === 'function') window.showToast(`Error: ${e.message}`, 'error');
      return null;
    }
  },

  /** Save the merged atlas files (downloads PNG + atlas text). */
  async save_modified() {
    if (!_mergedCanvas || !_mergedAtlasText) return 'Error: No merged data to save.';
    try {
      const base = _currentAtlasFilename.replace(/\.[^.]+$/, '');
      const pngName = `${base}.png`;
      const atlasName = _currentAtlasFilename;

      const pngBlob = await _canvasToBlob(_mergedCanvas);
      await _downloadBlob(pngName, pngBlob);

      const textBlob = new Blob([_mergedAtlasText], { type: 'text/plain' });
      await _downloadBlob(atlasName, textBlob);

      return `Saved: ${pngName} and ${atlasName}`;
    } catch (e) {
      return `Error: ${e.message}`;
    }
  },

  /** Toggle repack on/off using the pre-repack cached state. */
  async toggle_repack(repack) {
    if (!_modifier || !_preRepackCanvas || !_preRepackText) return null;
    try {
      let canvas = _preRepackCanvas, text = _preRepackText;
      if (repack) {
        const repacked = await _modifier.repack(_preRepackCanvas, _preRepackText);
        canvas = repacked.canvas;
        text = repacked.atlasText;
      }
      _mergedCanvas = canvas;
      _mergedAtlasText = text;

      const { regions: mergedRegions } = parseAtlas(text);
      const regionBounds = {};
      for (const [name, info] of Object.entries(mergedRegions)) {
        regionBounds[name] = [...info.bounds, info.rotate];
      }
      return { image: canvas.toDataURL('image/png'), regions: regionBounds };
    } catch (e) {
      console.error('toggle_repack error:', e);
      return null;
    }
  },

  /** Open a URL in the browser (or system browser on Android via Capacitor). */
  async open_url(url) {
    try {
      const parsed = new URL(String(url));
      if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:')
        return { ok: false, error: 'Invalid URL scheme.' };
      if (window.Capacitor && window.Capacitor.Plugins && window.Capacitor.Plugins.Browser) {
        await window.Capacitor.Plugins.Browser.open({ url: parsed.toString() });
      } else {
        window.open(parsed.toString(), '_blank');
      }
      return { ok: true };
    } catch (e) {
      return { ok: false, error: e.message };
    }
  },

  // Stub: update features not applicable to Android app
  async get_update_download_progress() { return { status: 'idle', percent: 0 }; },
  async download_update() { return { ok: false, error: 'Not supported on Android.' }; },
  async restart_and_install_update() { return { ok: false, error: 'Not supported on Android.' }; },
  async open_update_log() { return { ok: false, error: 'Not supported on Android.' }; },
};

export { _loadAtlasFiles };
