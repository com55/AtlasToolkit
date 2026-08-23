import { AtlasAPI } from './atlas-api.js';
import { state, getSelectedNames } from './state.js';
import {
  previewImg, previewContainer,
  resetPreview, applyTransform,
  drawRegionOverlay, clearOverlay,
  updatePreview, updateSaveMergedButton, setPreviewSrc,
  fitScaleIfOversized,
} from './preview.js';
import { showToast, showConfirm } from './dialogs.js';
import { updateModeToggleUI, updatePageSwitcher } from './app-bar.js';
import { refreshPanelSplit } from './panel-resizer.js';
import { refreshModifiedHighlight } from './region-list.js';

function setStatus(text) {
  document.getElementById('status-text').innerText = text;
}

/** Save As... / Reset follow whether a merged mod image is pending. */
function updateModifyActionButtons() {
  document.getElementById('btn-save-mod').disabled = !state.hasModImage;
  document.getElementById('btn-reset-mod').disabled = !state.hasModImage;
}

export function setMode(mode) {
  state.currentMode = mode;
  const extractControls = document.getElementById('extract-controls');
  const modifyControls  = document.getElementById('modify-controls');
  const repackOptions   = document.getElementById('repack-options');
  const saveModBtn      = document.getElementById('btn-save-mod');
  const dropMsg         = document.getElementById('drop-message-text');

  if (mode === 'modify') {
    extractControls.classList.add('hidden');
    modifyControls.classList.remove('hidden');
    repackOptions.classList.remove('hidden');
    saveModBtn.classList.remove('hidden');
    dropMsg.textContent = 'Drop image to modify, or .atlas to load'; // matches old Python engine's ui/js/mode.js
  } else {
    extractControls.classList.remove('hidden');
    modifyControls.classList.add('hidden');
    repackOptions.classList.add('hidden');
    saveModBtn.classList.add('hidden');
    dropMsg.textContent = 'Drop .atlas file here to load';
    clearOverlay();
  }
  updateModeToggleUI();
  updatePageSwitcher();
  // repack-options just appeared/disappeared -- re-clamp the stacked-layout
  // split so the splitter follows instead of leaving it stranded.
  refreshPanelSplit();
}

/** Apply a fresh modify-view payload (from enter_modify_mode) to the UI. */
function applyModifyView(data, statusMsg) {
  state.modifyRegionBounds = data.regions || {};
  state.modifyPages = Array.isArray(data.pages) ? data.pages : [];
  state.modifyRegionPages = data.regionPages || {};
  state.modifyActivePage = data.activePage || (state.modifyPages[0] || null);
  state.hasModImage = false;
  setMode('modify');
  setStatus(statusMsg);
  updateModifyActionButtons();
  setPreviewSrc(data.image);
  previewImg.style.display = 'block';
  previewImg.onload = function () {
    resetPreview();
    const containerW = previewContainer.clientWidth - 40;
    const containerH = previewContainer.clientHeight - 40;
    const imgW = previewImg.naturalWidth;
    const imgH = previewImg.naturalHeight;
    const fitScale = fitScaleIfOversized(containerW, containerH, imgW, imgH);
    if (fitScale !== null) {
      state.viewState.scale = fitScale;
      applyTransform();
    }
    previewImg.onload = null;
  };
}

export async function enterEditMode() {
  try {
    const data = await AtlasAPI.enter_modify_mode();
    if (data) {
      // Same status regardless of page count, matching old Python engine's
      // ui/js/mode.js exactly (parity fix, 2026-08-23).
      applyModifyView(data, 'Select regions and click Modify Selected');
      refreshModifiedHighlight();
    } else {
      showToast('Load an atlas first.', 'error');
    }
  } catch (e) {
    console.error(e);
    showToast('Failed to enter modify mode.', 'error'); // matches old ui/js/mode.js
  }
}

export async function exitEditMode() {
  if (AtlasAPI.has_pending_modifications && AtlasAPI.has_pending_modifications()) {
    const ok = await showConfirm(
      // matches old Python engine's ui/js/ui.js DISCARD_MOD_MESSAGE exactly
      'You have unsaved atlas modifications.\nContinue and discard them?',
      'Discard modifications?',
    );
    if (!ok) return;
  }
  try { AtlasAPI.exit_modify_mode(); } catch (e) { console.error(e); }
  refreshModifiedHighlight();
  state.modifyRegionBounds = {};
  state.modifyPages        = [];
  state.modifyRegionPages  = {};
  state.modifyActivePage   = null;
  state.hasModImage        = false;
  setMode('extract');
  clearOverlay();
  previewImg.style.display = 'none';
  resetPreview();
  setStatus('Ready');
  updateSaveMergedButton();
  updatePreview(getSelectedNames());
}

/** Discard all modifications and restore the pristine atlas, staying in edit mode. */
export async function resetModify() {
  if (!state.hasModImage) return;
  const ok = await showConfirm(
    // matches old Python engine's ui/js/ui.js RESET_MOD_MESSAGE exactly
    'Reset all modifications and restore the original atlas preview?',
    'Reset modifications?',
  );
  if (!ok) return;
  try {
    // enter_modify_mode clears the batch list and returns a pristine view.
    const data = await AtlasAPI.enter_modify_mode();
    if (data) {
      applyModifyView(data, 'Select regions and click Modify Selected');
      refreshModifiedHighlight();
      showToast('Modifications reset.', 'success');
    } else {
      showToast('Failed to reset modifications.', 'error');
    }
  } catch (e) {
    console.error(e);
    showToast('Failed to reset modifications.', 'error');
  }
}

export async function ReplaceSelected() {
  const names = getSelectedNames();
  if (names.length === 0) { showToast('Select at least one region to modify.', 'error'); return; } // matches old ui/js/modify.js
  try {
    setStatus('Selecting mod image...');
    const repack = document.getElementById('chk-repack').checked;
    const result = await AtlasAPI.select_mod_image(names, repack);
    if (result) {
      await onModPreviewReceived(result);
    } else {
      setStatus('Cancelled or no image selected.');
    }
  } catch (e) {
    console.error(e);
    showToast('Error selecting mod image.', 'error');
  }
}

export async function onModPreviewReceived(data) {
  state.hasModImage = true;
  if (data.regions) state.modifyRegionBounds = data.regions;

  // _buildResult() (atlas-session.js) always returns page-0's image for a
  // multi-page result, regardless of which page was active when the mod was
  // applied — found via parity audit, 2026-08-23: modifying a region on page
  // 2+ would silently jump the preview back to page 1. Re-fetch the correct
  // page's (now-merged) image for whichever page is actually active.
  let image = data.image;
  if (state.modifyPages.length > 1 && state.modifyActivePage) {
    const pageData = await AtlasAPI.get_modify_page_preview(state.modifyActivePage);
    if (pageData && pageData.image) image = pageData.image;
  }

  setPreviewSrc(image);
  previewImg.style.display = 'block';
  setStatus('Mod image merged. Ready to save.');
  updateModifyActionButtons();
  refreshModifiedHighlight();
  previewImg.onload = function () {
    resetPreview();
    const containerW = previewContainer.clientWidth - 40;
    const containerH = previewContainer.clientHeight - 40;
    const imgW = previewImg.naturalWidth;
    const imgH = previewImg.naturalHeight;
    // Always the WxH form, regardless of page count — matches old Python
    // engine's ui/js/modify.js exactly (parity fix, 2026-08-23).
    setStatus(`Merged preview (${imgW}x${imgH}). Ready to save.`);
    const fitScale = fitScaleIfOversized(containerW, containerH, imgW, imgH);
    if (fitScale !== null) {
      state.viewState.scale = fitScale;
    }
    applyTransform();
    previewImg.onload = null;
  };
}

export async function saveModified() {
  try {
    setStatus('Saving...');
    const result = await AtlasAPI.save_modified();
    if (result.startsWith('Error') || result === 'Cancelled') {
      showToast(result, result === 'Cancelled' ? 'info' : 'error');
    } else {
      showToast(result, 'success');
    }
    setStatus(result);
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
  setStatus(e.target.checked ? 'Applying repack...' : 'Reverting repack...');
  try {
    const result = await AtlasAPI.toggle_repack(e.target.checked);
    if (result) {
      await onModPreviewReceived(result);
    } else {
      showToast('No merged data to repack.', 'error');
    }
  } catch (err) {
    console.error(err);
    showToast('Repack toggle failed.', 'error');
  }
});
