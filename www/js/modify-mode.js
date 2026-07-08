import { AtlasAPI } from './atlas-api.js';
import { state, getSelectedNames } from './state.js';
import {
  previewImg, previewContainer,
  resetPreview, applyTransform,
  drawRegionOverlay, clearOverlay,
  updatePreview,
} from './preview.js';
import { showToast, showConfirm } from './dialogs.js';

export function setMode(mode) {
  state.currentMode = mode;
  const normalHeader    = document.getElementById('normal-header');
  const modifyHeader    = document.getElementById('modify-header');
  const extractControls = document.getElementById('extract-controls');
  const modifyControls  = document.getElementById('modify-controls');
  const repackOptions   = document.getElementById('repack-options');
  const dropMsg         = document.getElementById('drop-message-text');

  if (mode === 'modify') {
    normalHeader.classList.add('hidden');
    modifyHeader.classList.remove('hidden');
    extractControls.classList.add('hidden');
    modifyControls.classList.remove('hidden');
    repackOptions.classList.remove('hidden');
    dropMsg.textContent = 'Drop image to edit, or .atlas to load';
  } else {
    normalHeader.classList.remove('hidden');
    modifyHeader.classList.add('hidden');
    extractControls.classList.remove('hidden');
    modifyControls.classList.add('hidden');
    repackOptions.classList.add('hidden');
    dropMsg.textContent = 'Drop .atlas file here to load';
    clearOverlay();
  }
}

export async function enterEditMode() {
  try {
    const data = await AtlasAPI.enter_modify_mode();
    if (data) {
      setMode('modify');
      state.modifyRegionBounds = data.regions || {};
      state.modifyPages = Array.isArray(data.pages) ? data.pages : [];
      state.hasModImage = false;
      const statusMsg = state.modifyPages.length > 1
        ? 'Multi-page atlas — select regions to edit.'
        : 'Select regions want to edit.';
      document.getElementById('modify-status-text').innerText = statusMsg;
      document.getElementById('btn-save-mod').disabled = true;
      previewImg.src = data.image;
      previewImg.style.display = 'block';
      previewImg.onload = function () {
        resetPreview();
        const containerW = previewContainer.clientWidth - 40;
        const containerH = previewContainer.clientHeight - 40;
        const imgW = previewImg.naturalWidth;
        const imgH = previewImg.naturalHeight;
        if (imgW > containerW || imgH > containerH) {
          state.viewState.scale = Math.min(containerW / imgW, containerH / imgH);
          applyTransform();
        }
        previewImg.onload = null;
      };
    } else {
      showToast('Load an atlas first.', 'error');
    }
  } catch (e) {
    console.error(e);
    showToast('Failed to enter edit mode.', 'error');
  }
}

export async function exitEditMode() {
  if (AtlasAPI.has_pending_modifications && AtlasAPI.has_pending_modifications()) {
    const ok = await showConfirm(
      'You have unsaved atlas modifications. Continue and discard them?',
      'Discard modifications?',
    );
    if (!ok) return;
  }
  try { AtlasAPI.exit_modify_mode(); } catch (e) { console.error(e); }
  setMode('extract');
  state.modifyRegionBounds = {};
  state.modifyPages        = [];
  state.hasModImage        = false;
  clearOverlay();
  previewImg.style.display = 'none';
  resetPreview();
  document.getElementById('status-text').innerText = 'Ready';
  updatePreview(getSelectedNames());
}

export async function ReplaceSelected() {
  const names = getSelectedNames();
  if (names.length === 0) { showToast('Select at least one region to edit.', 'error'); return; }
  try {
    document.getElementById('modify-status-text').innerText = 'Selecting mod image...';
    const repack = document.getElementById('chk-repack').checked;
    const result = await AtlasAPI.select_mod_image(names, repack);
    if (result) {
      onModPreviewReceived(result);
    } else {
      document.getElementById('modify-status-text').innerText = 'Cancelled or no image selected.';
    }
  } catch (e) {
    console.error(e);
    showToast('Error selecting mod image.', 'error');
  }
}

export function onModPreviewReceived(data) {
  state.hasModImage = true;
  if (data.regions) state.modifyRegionBounds = data.regions;
  previewImg.src = data.image;
  previewImg.style.display = 'block';
  document.getElementById('modify-status-text').innerText = 'Mod image merged. Ready to save.';
  document.getElementById('btn-save-mod').disabled = false;
  previewImg.onload = function () {
    resetPreview();
    const containerW = previewContainer.clientWidth - 40;
    const containerH = previewContainer.clientHeight - 40;
    const imgW = previewImg.naturalWidth;
    const imgH = previewImg.naturalHeight;
    const statusMsg = data.pageCount > 1
      ? `Repacked across ${data.pageCount} pages. Ready to save.`
      : `Merged preview (${imgW}x${imgH}). Ready to save.`;
    document.getElementById('modify-status-text').innerText = statusMsg;
    if (imgW > containerW || imgH > containerH) {
      state.viewState.scale = Math.min(containerW / imgW, containerH / imgH);
    }
    applyTransform();
    previewImg.onload = null;
  };
}

export async function saveModified() {
  try {
    document.getElementById('modify-status-text').innerText = 'Saving...';
    const result = await AtlasAPI.save_modified();
    if (result.startsWith('Error') || result === 'Cancelled') {
      showToast(result, result === 'Cancelled' ? 'info' : 'error');
    } else {
      showToast(result, 'success');
    }
    document.getElementById('modify-status-text').innerText = result;
  } catch (e) {
    console.error(e);
    showToast('Save failed.', 'error');
  }
}

export function initRepackInfoOverlay() {
  const btn      = document.getElementById('btn-repack-info');
  const overlay  = document.getElementById('repack-info-overlay');
  const closeBtn = document.getElementById('btn-repack-info-close');
  if (!btn || !overlay || !closeBtn) return;

  const close = () => overlay.classList.add('hidden');
  const open  = () => overlay.classList.remove('hidden');

  btn.addEventListener('click', open);
  closeBtn.addEventListener('click', close);
  overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });
  window.addEventListener('keydown', (e) => { if (e.key === 'Escape') close(); });
}

// ─── Repack Event Listeners ───────────────────────────────────────────────────
document.getElementById('chk-repack').addEventListener('change', async (e) => {
  AtlasAPI.set_pref('repack', e.target.checked);
  if (!state.hasModImage) return;
  const statusEl = document.getElementById('modify-status-text');
  statusEl.innerText = e.target.checked ? 'Applying repack...' : 'Reverting repack...';
  try {
    const result = await AtlasAPI.toggle_repack(e.target.checked);
    if (result) {
      onModPreviewReceived(result);
    } else {
      showToast('No merged data to repack.', 'error');
    }
  } catch (err) {
    console.error(err);
    showToast('Repack toggle failed.', 'error');
  }
});
