import { AtlasAPI } from './atlas-api.js';
import { state, getSelectedKeys, getSelectedLabels } from './state.js';
import { loadRegions, updateButtons, updateRemoveButtonState, updateRenameButtonState } from './region-list.js';
import { setMode, onModPreviewReceived, updateMeshCroppingUI } from './modify-mode.js';
import { previewContainer, previewImg, resetPreview, clearOverlay } from './preview.js';
import { showToast, showConfirm, isAddRegionDialogOpen } from './dialogs.js';
import { platform, isPywebviewDesktop } from './platform.js';

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

  // Dropping a new atlas (a non-image file is present) over an in-progress
  // edit session would discard unsaved modifications — confirm first.
  if (hasNonImage && AtlasAPI.has_pending_modifications && AtlasAPI.has_pending_modifications()) {
    const ok = await showConfirm(
      // matches old Python engine's ui/js/ui.js DISCARD_MOD_MESSAGE exactly
      'You have unsaved atlas modifications.\nContinue and discard them?',
      'Discard modifications?',
    );
    if (!ok) return;
  }

  const loaded = await AtlasAPI.load_from_files(files, { showNoAtlasToast: false });

  if (loaded === 'cancelled') {
    showToast('Cancelled', 'info');
    return;
  }

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
    updateRemoveButtonState();
    updateRenameButtonState();
    await loadRegions();
    updateMeshCroppingUI();
    showToast('Atlas loaded via drag & drop.', 'success');
    return;
  }

  if (hasNonImage) {
    showToast('No valid atlas-format text file found in dropped files.', 'error');
    return;
  }

  const imgFile = files.find(isPng);
  if (imgFile && state.currentMode === 'modify') {
    const keys = getSelectedKeys();
    if (keys.length === 0) { showToast('Select at least one region first.', 'error'); return; }
    const repack = document.getElementById('chk-repack').checked;
    const result = await AtlasAPI.process_mod_image(imgFile, keys, repack);
    if (result) {
      await onModPreviewReceived(result);
      showToast('Mod image loaded via drag & drop.', 'success');
    }
  } else {
    showToast('Enter Edit Mode first to drop images.', 'error');
  }
}

// The missing-images dialog runs its own per-row drag-drop handling while
// open; stand down here so we don't also try to load the dropped PNG as a
// new atlas/mod-image.
const isMissingDialogOpen = () => document.body.dataset.missingDialogOpen === 'true';

// ─── Browser / PWA drag & drop ───────────────────────────────────
['dragover', 'drop'].forEach(ev => window.addEventListener(ev, e => e.preventDefault(), false));

window.addEventListener('dragenter', (e) => {
  e.preventDefault();
  if (isMissingDialogOpen()) return;
  if (isAddRegionDialogOpen()) return;
  if (e.dataTransfer.types.includes('Files')) showDropOverlay();
});

dropOverlay.addEventListener('dragover', e => e.preventDefault());

dropOverlay.addEventListener('dragleave', (e) => {
  e.preventDefault();
  if (e.relatedTarget === null || !dropOverlay.contains(e.relatedTarget)) hideDropOverlay();
});

dropOverlay.addEventListener('drop', async (e) => {
  e.preventDefault();
  if (isMissingDialogOpen()) return;
  if (isAddRegionDialogOpen()) return;
  hideDropOverlay();

  // pywebview: a native OS file drop's browser File objects carry no
  // readable bytes client-side (only a Python-side-only `pywebviewFullPath`
  // — see pywebview's DOM guide) — reading them here always fails and would
  // surface a bogus "no valid atlas file found" error. bridge.py's on_drop
  // (bound via webview.dom's capture-phase DOMEventHandler, see setup_drop)
  // owns native drop handling end-to-end, reading real bytes off disk by
  // path; nothing to do here for that case.
  if (isPywebviewDesktop()) return;

  const files = Array.from(e.dataTransfer.files);
  await processDroppedFiles(files);
});

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

/**
 * Read back the PNG bytes actually behind the visible <img> instead of
 * re-encoding via canvas.toBlob() — a canvas round-trip softens edges
 * (port of old Python engine's ui/js/preview.js getPreviewPngBlob(); the old
 * app avoided this by writing the PIL-generated PNG directly). previewImg.src
 * is a blob: URL (or rarely a data: URL), so fetch() reads the PNG bytes
 * without a second canvas encode. Falls back to a canvas capture only if
 * that somehow fails.
 */
async function getPreviewPngBlob() {
  const img = previewImg;
  if (!img.naturalWidth || !img.naturalHeight) return null;

  if (img.src) {
    try {
      const response = await fetch(img.src);
      if (response.ok) {
        const blob = await response.blob();
        if (blob && blob.size > 0) return blob;
      }
    } catch (e) {
      console.warn('fetch preview PNG failed, using canvas fallback', e);
    }
  }

  const canvas = document.createElement('canvas');
  canvas.width  = img.naturalWidth;
  canvas.height = img.naturalHeight;
  const ctx = canvas.getContext('2d');
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(img, 0, 0);
  return new Promise(resolve => canvas.toBlob(resolve, 'image/png'));
}

/** Port of old Python engine's ui/js/extract.js previewSaveDefaultName(). */
function previewSaveDefaultName(names) {
  if (!names || names.length === 0) return 'image.png';
  const safe = names.map(n => String(n).replace(/[<>:"/\\|?*]/g, '_'));
  if (safe.length === 1) return `${safe[0]}.png`;
  if (safe.length <= 5) return `${safe.join('+')}.png`;
  const more = safe.length - 5;
  return `${safe.slice(0, 5).join('+')}+ ${more} more.png`;
}

export async function copyPreviewImage() {
  contextMenu.classList.add('hidden');
  try {
    const blob = await getPreviewPngBlob();
    if (!blob) { showToast('No image to copy.', 'error'); return; }
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
    const blob = await getPreviewPngBlob();
    if (!blob) { showToast('Error: No image to save.', 'error'); return; }

    const filename = previewSaveDefaultName(getSelectedLabels());

    // platform.js branches browser (File System Access API) vs pywebview
    // (native save dialog + write_file_bytes) — see D1's revised split.
    const hasNativeDialog = isPywebviewDesktop() || typeof window.showSaveFilePicker === 'function';
    if (hasNativeDialog) {
      const saved = await platform.saveFileWithDialog(filename, blob, {
        defaultDir: AtlasAPI.get_current_atlas_directory(),
      });
      if (saved) { showToast('Image saved.', 'success'); return; }
      showToast('Cancelled', 'info'); // matches old ui/js/extract.js's "Cancelled"
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
