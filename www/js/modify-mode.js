import { AtlasAPI } from './atlas-api.js';
import { state, getSelectedNames } from './state.js';
import {
  previewImg, previewContainer,
  resetPreview, applyTransform,
  drawRegionOverlay, clearOverlay,
  updatePreview, updateModifyPreview, updateSaveMergedButton, setPreviewSrc,
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
  document.body.classList.toggle('mode-modify', mode === 'modify');
  document.body.classList.toggle('mode-extract', mode !== 'modify');
  const extractControls = document.getElementById('extract-controls');
  const modifyControls  = document.getElementById('modify-controls');
  const saveSplit       = document.getElementById('save-split');
  const dropMsg         = document.getElementById('drop-message-text');

  if (mode === 'modify') {
    extractControls.classList.add('hidden');
    modifyControls.classList.remove('hidden');
    saveSplit.classList.remove('hidden');
    dropMsg.textContent = 'Drop image to modify, or .atlas to load'; // matches old Python engine's ui/js/mode.js
  } else {
    extractControls.classList.remove('hidden');
    modifyControls.classList.add('hidden');
    saveSplit.classList.add('hidden');
    closeSaveMenu();
    dropMsg.textContent = 'Drop .atlas file here to load';
    clearOverlay();
  }
  updateModeToggleUI();
  updatePageSwitcher();
  // #repack-options is always-visible now (round-4-reviewed design decision
  // -- see the mesh-mask design spec's UI section) and its own height is
  // fixed (--panel-header-h), unaffected by which of its children show per
  // mode -- so panel-resizer.js's minRightHeight() (repackOptions height +
  // statusBar height) no longer actually changes across a mode switch.
  // Keeping this call anyway: harmless (a no-op re-clamp to the same
  // floor), and it stays correct if minRightHeight()'s inputs ever change
  // again (corrected post-review, Fable, 2026-08-31 -- the previous comment
  // here claimed #repack-options' height still changed, which stopped being
  // true once this task removed its whole-container .hidden toggle).
  refreshPanelSplit();
}

/** Apply a fresh modify-view payload (from enter_modify_mode) to the UI. */
function applyModifyView(data, statusMsg) {
  state.modifyRegionBounds = data.regions || {};
  state.modifyPages = Array.isArray(data.pages) ? data.pages : [];
  state.modifyRegionPages = data.regionPages || {};
  state.modifyActivePage = data.activePage || (state.modifyPages[0] || null);
  state.modifyActivePageIndex = Math.max(0, state.modifyPages.indexOf(state.modifyActivePage));
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
  state.modifyActivePageIndex = 0;
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
  if (Array.isArray(data.pages) && data.pages.length > 0) {
    state.modifyPages = data.pages;
  }
  if (data.regionPages) state.modifyRegionPages = data.regionPages;
  if (state.modifyActivePageIndex < 0
      || state.modifyActivePageIndex >= state.modifyPages.length) {
    state.modifyActivePageIndex = 0;
  }
  state.modifyActivePage = state.modifyPages[state.modifyActivePageIndex] || state.modifyActivePage;
  updatePageSwitcher();

  // _buildResult() still ships page-0's image as `data.image`. Re-fetch the
  // active index (Python get_modify_page_image) so a mod on page 2+ stays
  // on that page's merged canvas.
  let image = data.image;
  if (state.modifyPages.length > 1) {
    const pageData = await AtlasAPI.get_modify_page_preview(state.modifyActivePageIndex);
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
    closeSaveMenu();
    setStatus('Saving...');
    const result = await AtlasAPI.save_modified();
    if (result.startsWith('Error') || result === 'Cancelled') {
      showToast(result, result === 'Cancelled' ? 'info' : 'error');
    } else {
      showToast(result, 'success');
    }
    // Outcome lives in the toast only — don't echo the same string into
    // the status bar (e.g. "Saved to: …"). Restore the durable edit-mode
    // status from current selection / merged state.
    updateModifyPreview(getSelectedNames());
  } catch (e) {
    console.error(e);
    showToast('Save failed.', 'error');
    updateModifyPreview(getSelectedNames());
  }
}

function closeSaveMenu() {
  const menu = document.getElementById('save-menu');
  const btn = document.getElementById('btn-save-menu');
  if (menu) menu.classList.remove('open');
  if (btn) btn.setAttribute('aria-expanded', 'false');
}

export function initSaveSplitMenu() {
  const menuBtn = document.getElementById('btn-save-menu');
  const menu = document.getElementById('save-menu');
  const chk = document.getElementById('chk-copy-skel');
  if (!menuBtn || !menu || !chk) return;

  menuBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    const open = !menu.classList.contains('open');
    menu.classList.toggle('open', open);
    menuBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
  });
  menu.addEventListener('click', (e) => e.stopPropagation());
  document.addEventListener('click', () => closeSaveMenu());
  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeSaveMenu();
  });
  chk.addEventListener('change', () => {
    AtlasAPI.set_pref('copySkel', chk.checked);
  });
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

// --- Mesh-mask Event Listeners ---
document.getElementById('chk-mesh-mask').addEventListener('change', async (e) => {
  await AtlasAPI.set_mesh_mask_enabled(e.target.checked);
  updatePreview(getSelectedNames());
});

document.getElementById('btn-pick-skel').addEventListener('click', async () => {
  const picked = await AtlasAPI.pick_skel_file();
  if (picked) {
    const { available, enabled } = AtlasAPI.get_mesh_mask_state();
    document.getElementById('chk-mesh-mask').checked = enabled;
    document.getElementById('chk-mesh-mask').disabled = !available;
    updatePreview(getSelectedNames());
  }
});
