import { AtlasAPI } from './atlas-api.js';
import { state, getSelectedNames } from './state.js';
import { loadRegions, updateButtons } from './region-list.js';
import { setMode, onModPreviewReceived, getRepackMode } from './modify-mode.js';
import { previewContainer, previewImg, resetPreview, clearOverlay } from './preview.js';
import { showToast } from './dialogs.js';
import { platform } from './platform.js';

const dropOverlay = document.getElementById('drop-overlay');
const contextMenu = document.getElementById('context-menu');

function showDropOverlay() {
  dropOverlay.classList.remove('hidden');
  dropOverlay.style.pointerEvents = 'auto';
}
function hideDropOverlay() {
  dropOverlay.classList.add('hidden');
  dropOverlay.style.pointerEvents = 'none';
}

async function processDroppedFiles(files) {
  if (!files || files.length === 0) return;

  const isPng = (f) => f && (f.type === 'image/png' || /\.png$/i.test(f.name || ''));
  const hasNonImage = files.some(f => !isPng(f));

  const loaded = await AtlasAPI.load_from_files(files, { showNoAtlasToast: false });

  if (loaded) {
    if (state.currentMode === 'modify') {
      setMode('extract');
      state.modifyRegionBounds = {};
      state.hasModImage = false;
    }
    state.selectedIndices.clear();
    state.lastClickIndex = -1;
    previewImg.style.display = 'none';
    resetPreview();
    clearOverlay();
    updateButtons();
    await loadRegions();
    showToast('Atlas loaded via drag & drop.', 'success');
    return;
  }

  if (hasNonImage) {
    showToast('No valid atlas-format text file found in dropped files.', 'error');
    return;
  }

  const imgFile = files.find(isPng);
  if (imgFile && state.currentMode === 'modify') {
    const names = getSelectedNames();
    if (names.length === 0) { showToast('Select at least one region first.', 'error'); return; }
    const repack     = document.getElementById('chk-repack').checked;
    const repackMode = getRepackMode();
    const result     = await AtlasAPI.process_mod_image(imgFile, names, repack, repackMode);
    if (result) {
      onModPreviewReceived(result);
      showToast('Mod image loaded via drag & drop.', 'success');
    }
  } else {
    showToast('Enter Edit Mode first to drop images.', 'error');
  }
}

// ─── Tauri drag-drop (path-based, with sibling auto-load) ─────────────────────
// Tauri intercepts file drops at the native layer, so WebView drop events never
// fire. We subscribe to Tauri's drag-drop event instead and synthesize File
// objects from disk paths so the rest of the pipeline stays unchanged.
async function processDroppedPaths(paths) {
  if (!paths || paths.length === 0) return;
  try {
    const { atlasPath, droppedImageFiles } = await platform.readDroppedPaths(paths);

    if (atlasPath) {
      const ok = await AtlasAPI.load_from_path(atlasPath);
      if (ok) {
        if (state.currentMode === 'modify') {
          setMode('extract');
          state.modifyRegionBounds = {};
          state.hasModImage = false;
        }
        state.selectedIndices.clear();
        state.lastClickIndex = -1;
        previewImg.style.display = 'none';
        resetPreview();
        clearOverlay();
        updateButtons();
        await loadRegions();
        state.atlasPath = atlasPath;
        showToast('Atlas loaded via drag & drop.', 'success');
        return;
      }
    }

    // No atlas — try mod-image flow with the dropped PNGs.
    const pngs = Array.from(droppedImageFiles.values());
    if (pngs.length > 0 && state.currentMode === 'modify') {
      const names = getSelectedNames();
      if (names.length === 0) { showToast('Select at least one region first.', 'error'); return; }
      const repack     = document.getElementById('chk-repack').checked;
      const repackMode = getRepackMode();
      const result     = await AtlasAPI.process_mod_image(pngs[0], names, repack, repackMode);
      if (result) {
        onModPreviewReceived(result);
        showToast('Mod image loaded via drag & drop.', 'success');
      }
      return;
    }

    if (pngs.length > 0) {
      showToast('Enter Edit Mode first to drop images.', 'error');
    } else {
      showToast('No valid atlas-format text file found in dropped files.', 'error');
    }
  } catch (e) {
    console.error('processDroppedPaths error:', e);
    showToast(`Drop failed: ${e.message || e}`, 'error');
  }
}

if (platform.isTauri) {
  platform.subscribeDragDrop({
    onEnter: () => showDropOverlay(),
    onLeave: () => hideDropOverlay(),
    onDrop: async (paths) => {
      hideDropOverlay();
      await processDroppedPaths(paths);
    },
  });
} else {
  // ─── Browser / PWA drag & drop (unchanged) ───────────────────────────────────
  ['dragover', 'drop'].forEach(ev => window.addEventListener(ev, e => e.preventDefault(), false));

  window.addEventListener('dragenter', (e) => {
    e.preventDefault();
    if (e.dataTransfer.types.includes('Files')) showDropOverlay();
  });

  dropOverlay.addEventListener('dragover', e => e.preventDefault());

  dropOverlay.addEventListener('dragleave', (e) => {
    e.preventDefault();
    if (e.relatedTarget === null || !dropOverlay.contains(e.relatedTarget)) hideDropOverlay();
  });

  dropOverlay.addEventListener('drop', async (e) => {
    e.preventDefault();
    hideDropOverlay();
    const files = Array.from(e.dataTransfer.files);
    await processDroppedFiles(files);
  });
}

// ─── Context Menu ─────────────────────────────────────────────────────────────
previewContainer.addEventListener('contextmenu', (e) => {
  e.preventDefault();
  if (state.currentMode !== 'extract') return;
  if (previewImg.style.display === 'none' || !previewImg.src) return;
  contextMenu.style.left = e.clientX + 'px';
  contextMenu.style.top  = e.clientY + 'px';
  contextMenu.classList.remove('hidden');
});

window.addEventListener('click', () => contextMenu.classList.add('hidden'));
window.addEventListener('keydown', (e) => { if (e.key === 'Escape') contextMenu.classList.add('hidden'); });

export async function copyPreviewImage() {
  contextMenu.classList.add('hidden');
  try {
    if (!previewImg.naturalWidth) { showToast('No image to copy.', 'error'); return; }
    const canvas = document.createElement('canvas');
    canvas.width  = previewImg.naturalWidth;
    canvas.height = previewImg.naturalHeight;
    canvas.getContext('2d').drawImage(previewImg, 0, 0);
    const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'));
    if (!blob) { showToast('Failed to copy image.', 'error'); return; }
    await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })]);
    showToast('Image copied to clipboard.', 'success');
  } catch (e) {
    console.error(e);
    showToast('Failed to copy image.', 'error');
  }
}

export async function savePreviewImageAs() {
  contextMenu.classList.add('hidden');
  try {
    if (!previewImg.naturalWidth) { showToast('No image to save.', 'error'); return; }
    const canvas = document.createElement('canvas');
    canvas.width  = previewImg.naturalWidth;
    canvas.height = previewImg.naturalHeight;
    canvas.getContext('2d').drawImage(previewImg, 0, 0);
    const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'));
    if (!blob) { showToast('Failed to save image.', 'error'); return; }

    const selectedNames  = getSelectedNames();
    const rawPageName    = selectedNames.length > 0 ? AtlasAPI.get_region_page_name(selectedNames[0]) : '';
    const sanitize       = (v) => String(v || '').replace(/\.[^.]+$/, '').replace(/[\\/:*?"<>|]/g, '_').trim().replace(/\s+/g, '-');
    const pageName       = sanitize(rawPageName) || 'page';
    const regionsJoined  = selectedNames.map(sanitize).filter(Boolean).join('-') || 'preview';
    const filename       = `${pageName}_${regionsJoined}.png`;

    if ('showSaveFilePicker' in window) {
      const fileHandle = await window.showSaveFilePicker({
        suggestedName: filename,
        types: [{ description: 'PNG Image', accept: { 'image/png': ['.png'] } }],
      });
      const writable = await fileHandle.createWritable();
      await writable.write(blob);
      await writable.close();
      showToast('Image saved.', 'success');
      return;
    }

    const url  = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href     = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    showToast('Image download started.', 'success');
  } catch (e) {
    if (e?.name === 'AbortError') { showToast('Save cancelled.', 'info'); return; }
    console.error(e);
    showToast('Failed to save image.', 'error');
  }
}
