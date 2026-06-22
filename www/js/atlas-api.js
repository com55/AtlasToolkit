/**
 * atlas-api.js
 * Replaces pywebview.api with a pure-JS implementation.
 * Exposes the same async interface that script.js expects.
 */

import { autoConvertAtlas } from './atlas-converter.js';
import { AtlasProcessor, _loadImage } from './atlas-extracter.js';
import { AtlasModifier, parseAtlas, rebuildAtlasText, repackMultiPage } from './atlas-modifier.js';
import { platform } from './platform.js';

// ─── State ────────────────────────────────────────────────────────────────────

let _processor = null;
let _modifier = null;
let _currentAtlasFilename = '';
let _currentAtlasText = '';
let _mergedCanvas = null;
let _mergedAtlasText = null;
let _mergedPages = null;
let _preRepackCanvas = null;
let _preRepackText = null;
let _lastSaveHandle = null;
let _lastExtractDirHandle = null;
let _currentModifyPage = null;
let _currentAtlasPath = null;
let _currentSkel = null; // { name, blob } | null

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

function _isImageFile(file) {
  if (!file) return false;
  if (typeof file.type === 'string' && file.type === 'image/png') return true;
  return /\.png$/i.test(file.name || '');
}

function _atlasDirName(path) {
  if (!path) return null;
  const norm = String(path).replace(/\\/g, '/');
  const idx = norm.lastIndexOf('/');
  return idx >= 0 ? path.slice(0, idx) : null;
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

function _createModifierForPage(pageName) {
  if (!_processor || !pageName) return null;
  const baseImg = _processor.getPageImage(pageName);
  if (!baseImg) return null;

  _modifier = new AtlasModifier(_currentAtlasText, _currentAtlasFilename, baseImg, pageName);
  _currentModifyPage = pageName;
  _mergedCanvas = null;
  _mergedAtlasText = null;
  _mergedPages = null;
  _preRepackCanvas = null;
  _preRepackText = null;
  return _modifier;
}

function _resolveModifySelectionInfo(selectedNames) {
  if (!_processor || !selectedNames || selectedNames.length === 0) {
    return { targetPage: null, hasMultiplePages: false };
  }

  const pages = [];
  const pageSet = new Set();
  for (const name of selectedNames) {
    const page = AtlasAPI.get_region_page_name(name);
    if (!page) continue;
    if (!pageSet.has(page)) {
      pageSet.add(page);
      pages.push(page);
    }
  }

  if (pages.length === 0) return { targetPage: null, hasMultiplePages: false };
  if (pages.length === 1) return { targetPage: pages[0], hasMultiplePages: false };

  const firstPage = (_processor.pages && _processor.pages[0] && _processor.pages[0].filename) || pages[0];
  return { targetPage: firstPage, hasMultiplePages: true };
}

function _relocateRegionsToPage(atlasText, regionNames, targetPage) {
  if (!atlasText || !targetPage || !regionNames || regionNames.length === 0) return atlasText;

  const moveSet = new Set(regionNames);
  const lines = atlasText.split('\n');
  const sections = [];
  const pageRe = /\.png$/i;

  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const s = line.trim();
    if (!pageRe.test(s)) {
      i++;
      continue;
    }

    const section = { page: s, pageLine: line, headerLines: [], regions: [] };
    i += 1;

    while (i < lines.length) {
      const h = lines[i].trim();
      if (!h) { i += 1; continue; }
      if (pageRe.test(h)) break;
      if (!lines[i].includes(':')) break;
      section.headerLines.push(lines[i]);
      i += 1;
    }

    while (i < lines.length) {
      const r = lines[i].trim();
      if (!r) { i += 1; continue; }
      if (pageRe.test(r)) break;

      const block = [lines[i]];
      const name = r;
      i += 1;
      while (i < lines.length) {
        const body = lines[i].trim();
        if (!body || pageRe.test(body) || !lines[i].includes(':')) break;
        block.push(lines[i]);
        i += 1;
      }
      section.regions.push({ name, lines: block });
    }

    sections.push(section);
  }

  if (sections.length === 0) return atlasText;

  const targetSection = sections.find(section => section.page === targetPage);
  if (!targetSection) return atlasText;

  const moved = [];
  for (const section of sections) {
    if (section.page === targetPage) continue;
    const keep = [];
    for (const region of section.regions) {
      if (moveSet.has(region.name)) moved.push(region);
      else keep.push(region);
    }
    section.regions = keep;
  }

  const existing = new Set(targetSection.regions.map(region => region.name));
  for (const region of moved) {
    if (!existing.has(region.name)) {
      targetSection.regions.push(region);
      existing.add(region.name);
    }
  }

  const out = [];
  sections.forEach((section, idx) => {
    out.push(section.pageLine);
    section.headerLines.forEach(line => out.push(line));
    section.regions.forEach(region => region.lines.forEach(line => out.push(line)));
    if (idx < sections.length - 1) out.push('');
  });
  return out.join('\n');
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
  const pickerOptions = {
    id: 'atlastoolkit-export',
    suggestedName: filename,
    types: [
      ext === 'png'
        ? { description: 'PNG image', accept: { 'image/png': ['.png'] } }
        : { description: 'Atlas text', accept: { 'text/plain': ['.atlas', '.txt'] } }
    ],
    excludeAcceptAllOption: false,
  };

  if (_lastSaveHandle) pickerOptions.startIn = _lastSaveHandle;

  const fileHandle = await window.showSaveFilePicker(pickerOptions);
  await _writeBlobToHandle(fileHandle, blob);
  _lastSaveHandle = fileHandle;
}

async function _saveManyPngToDirectory(fileBlobs) {
  const pickerOptions = { id: 'atlastoolkit-extract-folder', mode: 'readwrite' };
  if (_lastExtractDirHandle) pickerOptions.startIn = _lastExtractDirHandle;

  const dirHandle = await window.showDirectoryPicker(pickerOptions);
  _lastExtractDirHandle = dirHandle;

  for (const { filename, blob } of fileBlobs) {
    const fileHandle = await dirHandle.getFileHandle(filename, { create: true });
    await _writeBlobToHandle(fileHandle, blob);
  }
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
function _acceptToExtensions(accept) {
  return accept.split(',')
    .map(s => s.trim())
    .filter(s => s.startsWith('.'))
    .map(s => s.slice(1).toLowerCase());
}

function _pickFiles({ accept = '', multiple = false } = {}) {
  // Tauri webview blocks <input type=file>; use the native dialog instead.
  if (platform.isTauri) {
    return platform.pickFiles({ extensions: _acceptToExtensions(accept), multiple });
  }
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
    _currentAtlasPath = null;
    _currentSkel = null;

    // Clear modify state
    _modifier = null;
    _mergedCanvas = null;
    _mergedAtlasText = null;
    _mergedPages = null;
    _preRepackCanvas = null;
    _preRepackText = null;
    _currentModifyPage = null;

    return true;
  } catch (e) {
    console.error('load_atlas error:', e);
    return false;
  }
}

/**
 * Tauri-only: load atlas + sibling PNGs (+ optional .skel) directly from a disk path.
 * Falls back to the missing-image dialog if any sibling PNG isn't on disk.
 */
async function _loadAtlasFromPath(atlasPath) {
  if (!platform.isTauri) return false;
  try {
    const { atlasText, atlasName, imageBlobs, skelBlob, skelName, missingPages } =
      await platform.readAtlasWithSiblings(atlasPath);

    // Use the existing pipeline so all parse/scale logic stays in one place.
    const convertedText = autoConvertAtlas(atlasText);
    const atlasFile = new File([convertedText], atlasName, { type: 'text/plain' });

    const imageFileMap = {};
    for (const [name, file] of imageBlobs) imageFileMap[name] = file;

    // _loadAtlasFiles handles missingPages via the missing-image dialog.
    const ok = await _loadAtlasFiles(atlasFile, imageFileMap);
    if (!ok) return false;

    _currentAtlasPath = atlasPath;
    _currentSkel = (skelBlob && skelName) ? { name: skelName, blob: skelBlob } : null;
    return true;
  } catch (e) {
    console.error('load_from_path error:', e);
    return false;
  }
}

// ─── Public API (mirrors pywebview.api) ───────────────────────────────────────

export const AtlasAPI = {

  /**
   * Read a preference. Tauri: from config.json in appConfigDir.
   * Web/PWA: from localStorage. Returns a Promise in both cases.
   */
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
    // Tauri: pick the atlas via native dialog and load sibling PNGs from disk.
    if (platform.isTauri) {
      const picked = await platform.pickAtlasFile();
      if (!picked || !picked.path) return false;
      return _loadAtlasFromPath(picked.path);
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
  async load_atlas_from_file(atlasFile, imageFileMap = {}) {
    return _loadAtlasFiles(atlasFile, imageFileMap);
  },

  /** Tauri-only: load an atlas (and sibling PNGs / .skel) from a disk path. */
  async load_from_path(atlasPath) {
    return _loadAtlasFromPath(atlasPath);
  },

  get_atlas_path() { return _currentAtlasPath; },

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

    try {
      if (platform.isTauri && extracted.length >= 1) {
        const defaultPath = _currentAtlasPath ? _atlasDirName(_currentAtlasPath) : null;
        const folder = await platform.pickSaveFolder(defaultPath);
        if (!folder) return 'Cancelled';
        await platform.writeFilesToFolder(
          folder,
          extracted.map(item => ({ name: item.filename, data: item.blob })),
        );
        return `Saved ${count} image${count !== 1 ? 's' : ''} to selected folder.`;
      }

      if (_isDesktopFsApiAvailable() && extracted.length > 1) {
        await _saveManyPngToDirectory(extracted);
        return `Saved ${count} image${count !== 1 ? 's' : ''} to selected folder.`;
      }

      for (const item of extracted) {
        await _downloadBlob(item.filename, item.blob);
      }
    } catch (e) {
      if (e && e.name === 'AbortError') return 'Cancelled';
      throw e;
    }

    return `Successfully extracted ${count} image${count !== 1 ? 's' : ''}.`;
  },

  // ── Modify Mode ────────────────────────────────────────────────────────────

  async enter_modify_mode() {
    if (!_processor) return null;
    try {
      const pages = _processor.pages || [];
      if (pages.length === 0) return null;
      const initialPage = pages[0].filename;
      const baseImg = _processor.getPageImage(initialPage);
      if (!baseImg) return null;

      _modifier = new AtlasModifier(_currentAtlasText, _currentAtlasFilename, baseImg, initialPage);
      _currentModifyPage = initialPage;
      _mergedCanvas = null;
      _mergedAtlasText = null;
      _preRepackCanvas = null;
      _preRepackText = null;

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
        activePage: _currentModifyPage,
      };
    } catch (e) {
      console.error('enter_modify_mode error:', e);
      return null;
    }
  },

  exit_modify_mode() {
    _modifier = null;
    _mergedCanvas = null;
    _mergedAtlasText = null;
    _mergedPages = null;
    _preRepackCanvas = null;
    _preRepackText = null;
    _currentModifyPage = null;
  },

  /** Pick a mod PNG and process it. */
  async select_mod_image(selectedNames, repack = false, repackMode = 'page') {
    if (!_processor) return null;
    const { targetPage } = _resolveModifySelectionInfo(selectedNames);
    if (!targetPage) return null;

    if (!_modifier || _currentModifyPage !== targetPage) {
      const modifier = _createModifierForPage(targetPage);
      if (!modifier) {
        if (typeof window.showToast === 'function') {
          window.showToast(`Failed to load atlas page image: ${targetPage}`, 'error');
        }
        return null;
      }
    }

    const files = await _pickFiles({ accept: IMAGE_PICKER_ACCEPT, multiple: false });
    if (files.length === 0) return null;
    return AtlasAPI.process_mod_image(files[0], selectedNames, repack, repackMode);
  },

  async _processModMultiPage(source, selectedNames) {
    // 1. Extract all regions from all pages (whitespace restored)
    const allSprites = {};
    for (const name of Object.keys(_processor.regions)) {
      const canvas = _processor.extractRegion(name);
      if (canvas) allSprites[name] = canvas;
    }

    // 2. Load mod image and replace selected regions with it
    const rawImg = source instanceof File ? await _loadImage(source) : source;
    let modCanvas;
    if (rawImg instanceof HTMLCanvasElement) {
      modCanvas = rawImg;
    } else {
      modCanvas = document.createElement('canvas');
      modCanvas.width = rawImg.naturalWidth || rawImg.width;
      modCanvas.height = rawImg.naturalHeight || rawImg.height;
      modCanvas.getContext('2d').drawImage(rawImg, 0, 0);
    }
    for (const name of selectedNames) {
      if (name in allSprites) allSprites[name] = modCanvas;
    }

    // 3. Collect page infos and region metas
    const numPages = _processor.pages.length;
    const pageInfos = _processor.pages.map(p => ({
      page: p.filename,
      format: p.format,
      filter: `${p.filter[0]}, ${p.filter[1]}`,
      repeat: p.repeat,
      pma: p.pma,
    }));
    const regionMetas = {};
    for (const [name, r] of Object.entries(_processor.regions)) {
      regionMetas[name] = {
        atlasName: r.atlasName || r.name || name,
        index: Number.isFinite(r.index) ? r.index : -1,
        split: r.split,
        pad: r.pad,
        extraPairs: Array.isArray(r.extraPairs) ? r.extraPairs : [],
      };
    }

    // 4. Repack across all pages
    const { pages, atlasText } = await repackMultiPage(allSprites, numPages, pageInfos, regionMetas);

    _mergedPages = pages;
    _mergedAtlasText = atlasText;
    _mergedCanvas = null;

    // 5. Build region bounds for overlay — include all regions so selections
    //    on any page are highlighted when that page's preview is shown.
    const page0Filename = pageInfos.length > 0 ? pageInfos[0].page : null;
    const proc = new AtlasProcessor(atlasText);
    const regionBounds = {};
    for (const [name, info] of Object.entries(proc.regions)) {
      regionBounds[name] = [info.x, info.y, info.w, info.h, info.rotate];
    }

    const image = pages.length > 0 ? pages[0].toDataURL('image/png') : null;
    return { image, regions: regionBounds, pageCount: numPages, previewPage: page0Filename };
  },

  /** Process a mod image (File or canvas/img) for the selected regions. */
  async process_mod_image(source, selectedNames, repack = false, repackMode = 'page') {
    if (!_processor) return null;

    if (_processor.pages.length > 1) {
      return AtlasAPI._processModMultiPage(source, selectedNames);
    }

    const { targetPage, hasMultiplePages } = _resolveModifySelectionInfo(selectedNames);
    if (!targetPage) return null;

    if (!_modifier || _currentModifyPage !== targetPage) {
      const modifier = _createModifierForPage(targetPage);
      if (!modifier) {
        if (typeof window.showToast === 'function') {
          window.showToast(`Failed to load atlas page image: ${targetPage}`, 'error');
        }
        return null;
      }
    }

    try {
      const img = source instanceof File ? await _loadImage(source) : source;
      const preferred = selectedNames.filter(name => _modifier.regions[name]);
      const rest = selectedNames.filter(name => !_modifier.regions[name]);
      const orderedSelection = [...preferred, ...rest];

      const { mergedCanvas, atlasText: mergedTextRaw } = _modifier.mergeModImage(img, orderedSelection);

      const mergedText = hasMultiplePages
        ? _relocateRegionsToPage(mergedTextRaw, selectedNames, targetPage)
        : mergedTextRaw;

      _preRepackCanvas = mergedCanvas;
      _preRepackText = mergedText;
      _modifier = new AtlasModifier(_preRepackText, _currentAtlasFilename, _preRepackCanvas, _currentModifyPage);

      let finalCanvas = mergedCanvas;
      let finalText = mergedText;

      if (repack) {
        if (repackMode === 'all') {
          const repackedAll = await _repackAllPagesToSingle(mergedCanvas, mergedText, targetPage);
          if (repackedAll) {
            finalCanvas = repackedAll.canvas;
            finalText = repackedAll.atlasText;
          }
        } else {
          const repacked = await _modifier.repack(mergedCanvas, mergedText);
          finalCanvas = repacked.canvas;
          finalText = repacked.atlasText;
        }
      }

      _mergedCanvas = finalCanvas;
      _mergedAtlasText = finalText;

      const regionBounds = _buildRegionBoundsFromAtlasText(finalText);

      return { image: finalCanvas.toDataURL('image/png'), regions: regionBounds };
    } catch (e) {
      console.error('process_mod_image error:', e);
      if (typeof window.showToast === 'function') window.showToast(`Error: ${e.message}`, 'error');
      return null;
    }
  },

  /** Save the merged atlas files. Tauri: folder picker + batched write.
   *  Web/PWA: per-file Save As dialogs / downloads (unchanged). */
  async save_modified() {
    if (!_mergedAtlasText) return 'Error: No merged data to save.';
    try {
      // Build the list of outputs (png(s), atlas text, optional skel) up front
      // so we can route through either the Tauri folder writer or the
      // per-file download flow.
      const outputs = [];

      if (_mergedPages && _mergedPages.length > 0) {
        for (let i = 0; i < _mergedPages.length; i++) {
          const pageName = (_processor && _processor.pages[i])
            ? _processor.pages[i].filename
            : `page${i}.png`;
          outputs.push({ name: pageName, data: await _canvasToBlob(_mergedPages[i]) });
        }
      } else if (_mergedCanvas) {
        const base = _currentAtlasFilename.replace(/\.[^.]+$/, '');
        outputs.push({ name: `${base}.png`, data: await _canvasToBlob(_mergedCanvas) });
      } else {
        return 'Error: No merged data to save.';
      }

      outputs.push({ name: _currentAtlasFilename, data: _mergedAtlasText });
      if (_currentSkel) outputs.push({ name: _currentSkel.name, data: _currentSkel.blob });

      if (platform.isTauri) {
        const defaultPath = _atlasDirName(_currentAtlasPath);
        const folder = await platform.pickSaveFolder(defaultPath);
        if (!folder) return 'Cancelled';
        await platform.writeFilesToFolder(folder, outputs);
        const fileNames = outputs.map(o => o.name).join(', ');
        return `Saved to selected folder: ${fileNames}`;
      }

      // Web fallback: per-file Save As / downloads (matches previous behavior).
      for (const item of outputs) {
        const blob = item.data instanceof Blob
          ? item.data
          : new Blob([item.data], { type: 'text/plain' });
        await _downloadBlob(item.name, blob);
      }
      const summary = outputs.map(o => o.name).join(' and ');
      return `Saved: ${summary}`;
    } catch (e) {
      if (e && e.name === 'AbortError') return 'Cancelled';
      return `Error: ${e.message}`;
    }
  },

  /** Toggle repack on/off using the pre-repack cached state. */
  async toggle_repack(repack, repackMode = 'page') {
    if (!_modifier || !_currentModifyPage || !_preRepackCanvas || !_preRepackText) return null;
    try {
      let canvas = _preRepackCanvas, text = _preRepackText;
      if (repack) {
        if (repackMode === 'all') {
          const repackedAll = await _repackAllPagesToSingle(_preRepackCanvas, _preRepackText, _currentModifyPage);
          if (repackedAll) {
            canvas = repackedAll.canvas;
            text = repackedAll.atlasText;
          }
        } else {
          const repacked = await _modifier.repack(_preRepackCanvas, _preRepackText);
          canvas = repacked.canvas;
          text = repacked.atlasText;
        }
      }
      _mergedCanvas = canvas;
      _mergedAtlasText = text;

      const regionBounds = _buildRegionBoundsFromAtlasText(text);
      return { image: canvas.toDataURL('image/png'), regions: regionBounds };
    } catch (e) {
      console.error('toggle_repack error:', e);
      return null;
    }
  },

};

export { _loadAtlasFiles };
