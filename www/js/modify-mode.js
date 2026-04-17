import { AtlasAPI } from './atlas-api.js';
import { state, getSelectedNames } from './state.js';
import {
  previewImg, previewContainer,
  resetPreview, applyTransform,
  drawRegionOverlay, clearOverlay,
  updatePreview,
} from './preview.js';
import { showToast } from './dialogs.js';

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
      updateRepackModeAvailability();
      state.hasModImage = false;
      document.getElementById('modify-status-text').innerText = 'Select regions want to edit.';
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
    const repack     = document.getElementById('chk-repack').checked;
    const repackMode = getRepackMode();
    const result     = await AtlasAPI.select_mod_image(names, repack, repackMode);
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
    document.getElementById('modify-status-text').innerText = `Merged preview (${imgW}x${imgH}). Ready to save.`;
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

export function getRepackMode() {
  const sel = document.getElementById('sel-repack-mode');
  return sel && sel.value === 'all' ? 'all' : 'page';
}

export function updateRepackModeAvailability() {
  const repackCheckbox = document.getElementById('chk-repack');
  const modeGroup      = document.getElementById('repack-mode-group');
  const select         = document.getElementById('sel-repack-mode');
  if (!select || !repackCheckbox || !modeGroup) return;

  const hasMultiPage = (state.modifyPages || []).length > 1;
  const showMode     = hasMultiPage && repackCheckbox.checked;
  modeGroup.classList.toggle('hidden', !showMode);
  select.disabled = !showMode;

  if (!hasMultiPage) {
    select.value = 'page';
    AtlasAPI.set_pref('repackMode', 'page');
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
  updateRepackModeAvailability();
  if (!state.hasModImage) return;
  const repackMode = getRepackMode();
  const statusEl   = document.getElementById('modify-status-text');
  statusEl.innerText = e.target.checked ? 'Applying repack...' : 'Reverting repack...';
  try {
    const result = await AtlasAPI.toggle_repack(e.target.checked, repackMode);
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

document.getElementById('sel-repack-mode').addEventListener('change', async (e) => {
  const mode = e.target.value === 'all' ? 'all' : 'page';
  AtlasAPI.set_pref('repackMode', mode);
  if (!state.hasModImage) return;
  if (!document.getElementById('chk-repack').checked) return;
  const statusEl = document.getElementById('modify-status-text');
  statusEl.innerText = mode === 'all' ? 'Applying repack all pages...' : 'Applying repack current page...';
  try {
    const result = await AtlasAPI.toggle_repack(true, mode);
    if (result) onModPreviewReceived(result);
  } catch (err) {
    console.error(err);
    showToast('Repack mode change failed.', 'error');
  }
});
