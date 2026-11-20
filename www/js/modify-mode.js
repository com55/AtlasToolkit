// SPDX-FileCopyrightText: 2026 com55
// SPDX-License-Identifier: Apache-2.0
import { HostError } from './engine-client.js';
import { AtlasAPI } from './atlas-api.js';
import { state, getSelectedRegions, getSelectedKeys, getSelectedLabels } from './state.js';
import {
  previewImg,
  resetPreview,
  drawRegionOverlay, clearOverlay,
  updatePreview, updateModifyPreview, updateSaveMergedButton, setPreviewSrc,
  applyAutoFit, refreshMeshHullOverlay,
} from './preview.js';
import { showToast, showConfirm, openAddRegionModal } from './dialogs.js';
import { withLock, withBusy, isBusy, noteBusyConflict } from './busy-overlay.js';
import { updateModeToggleUI, updatePageSwitcher } from './app-bar.js';
import { refreshPanelSplit } from './panel-resizer.js';
import { refreshModifiedHighlight, loadRegions, renderSelection, updateButtons, updateRemoveButtonState, updateRenameButtonState } from './region-list.js';
import { syncHelpPopover, hideHelpPopover } from './options-popover.js';
import { closeSettingsPanel } from './settings-panel.js';
import { syncOptionsRowOverflow } from './options-row.js';

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
  hideHelpPopover();
  closeSettingsPanel();
  updateMeshCroppingUI();
  // Wrapping extra <li> groups can change #options-row height when expanded,
  // so re-measure overflow after the mode-gated children settle, then
  // re-clamp the stacked-layout split (minRightHeight includes #options-row-wrap).
  syncOptionsRowOverflow();
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
    applyAutoFit();
    previewImg.onload = null;
  };
}

/** Rebuild the region list from the effective model after a structural
 *  (add/remove/rename) change, preserving the user's selection by key.
 *  Shared by the Task 9-11 structural ops. */
export async function refreshStructuralUi(prevSelectedKeys) {
  state.lastClickIndex = -1;
  state.dragStartIndex = -1;
  await loadRegions(); // rebuilds state.regionsData from the effective model
  const newIndices = new Set();
  state.regionsData.forEach((entry, idx) => {
    if (prevSelectedKeys.includes(entry.key)) newIndices.add(idx);
  });
  state.selectedIndices = newIndices;
  renderSelection();
  updateButtons();
  updateRemoveButtonState();
  updateRenameButtonState();
}

export async function enterEditMode() {
  if (isBusy()) { noteBusyConflict(); return; }
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
  if (isBusy()) { noteBusyConflict(); return; }
  // Captured before anything below rebuilds state.regionsData, so the
  // reconcile-by-key inside refreshStructuralUi() carries the selection
  // across into View Mode instead of dropping it.
  const prevSelectedKeys = getSelectedKeys();
  if (AtlasAPI.has_pending_modifications && AtlasAPI.has_pending_modifications()) {
    const ok = await showConfirm(
      // matches old Python engine's ui/js/ui.js DISCARD_MOD_MESSAGE exactly
      'You have unsaved atlas modifications.\nContinue and discard them?',
      'Discard modifications?',
    );
    if (!ok) return;
  }
  try { AtlasAPI.exit_modify_mode(); } catch (e) { console.error(e); }
  await refreshStructuralUi(prevSelectedKeys); // rebuilds sidebar back to pristine, keeps selection
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
  updatePreview(getSelectedRegions());
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
    const data = await AtlasAPI.reset_modifications();
    if (data) {
      applyModifyView(data, 'Select regions and click Modify Selected');
      await refreshStructuralUi([]); // rebuilds sidebar back to pristine, clears anchors
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
  const keys = getSelectedKeys();
  if (keys.length === 0) { showToast('Select at least one region to modify.', 'error'); return; } // matches old ui/js/modify.js
  if (isBusy()) { noteBusyConflict(); return; }
  try {
    setStatus('Selecting mod image...');
    const result = await AtlasAPI.select_mod_image(keys);
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
  state.hasModImage = data.state ? !!data.state.hasChanges : AtlasAPI.has_pending_modifications();
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
    if (pageData && pageData.image) {
      image = pageData.image;
      if (image !== data.image) URL.revokeObjectURL(data.image);
    }
  }

  setPreviewSrc(image);
  previewImg.style.display = 'block';
  setStatus(state.hasModImage ? 'Mod image merged. Ready to save.' : 'Select regions and click Modify Selected');
  updateModifyActionButtons();
  refreshModifiedHighlight();
  previewImg.onload = function () {
    const imgW = previewImg.naturalWidth;
    const imgH = previewImg.naturalHeight;
    // Always the WxH form, regardless of page count — matches old Python
    // engine's ui/js/modify.js exactly (parity fix, 2026-08-23).
    setStatus(state.hasModImage ? `Merged preview (${imgW}x${imgH}). Ready to save.` : 'Select regions and click Modify Selected');
    applyAutoFit();
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
    updateModifyPreview(getSelectedKeys());
  } catch (e) {
    console.error(e);
    showToast('Save failed.', 'error');
    updateModifyPreview(getSelectedKeys());
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

// --- Mesh Cropping Event Listeners ---

const MESH_UNAVAILABLE_MESSAGES = {
  'unsupported-version': 'This .skel file uses a Spine version this app does not support (3.8.x and 4.2.x only).',
  'parse-error': 'This .skel file could not be read -- it may be corrupted or not a valid Spine skeleton file.',
  'no-mesh-attachments': 'This .skel file parsed successfully but contains no Mesh attachments to crop with.',
};

/** Sync mesh controls and the independent skeleton picker from host state. */
export function updateMeshCroppingUI() {
  const { available, enabled, skelFileName, unavailableReason } = AtlasAPI.get_mesh_mask_state();
  document.getElementById('chk-mesh-mask').checked = enabled;

  document.getElementById('chk-mesh-mask').disabled = !available;
  updateMeshHullUI();

  // Always shown; a .skel attaches to an open atlas, so it waits for one.
  const btn = document.getElementById('btn-pick-skel');
  const atlasLoaded = !!AtlasAPI.get_current_atlas_filename();
  let state, value, help, icon = '';
  if (!atlasLoaded) {
    [state, value, help] = ['skel-none', 'Open an atlas first', 'Open an atlas, then add its .skel file.'];
  } else if (!skelFileName) {
    [state, value, help, icon] = ['skel-missing', 'Add .skel', 'Add the .skel file to use mesh outlines (Mesh Cropping, Mesh-Aware, Polygons).', SKEL_ADD];
  } else if (!available) {
    [state, value, help, icon] = ['skel-invalid', skelFileName, MESH_UNAVAILABLE_MESSAGES[unavailableReason] || 'This .skel file cannot be used for mesh masking.', SKEL_ERROR];
  } else {
    [state, value, help, icon] = ['skel-ok', skelFileName, `Mesh data from ${skelFileName}. Click to choose a different file.`, SKEL_OK];
  }
  btn.disabled = !atlasLoaded;
  btn.classList.remove('skel-none', 'skel-missing', 'skel-invalid', 'skel-ok');
  btn.classList.add(state);
  btn.dataset.help = help;
  btn.setAttribute('aria-label', `Skeleton: ${value}`);
  const safe = value.replace(/[&<>"']/g, c => `&#${c.charCodeAt(0)};`);  // a file name is user data
  btn.innerHTML = `${SKEL_ICON}<span class="chip-label">Skeleton</span><span class="chip-sep"></span>${icon}<span class="skel-value">${safe}</span>`;
  refreshPanelSplit();
}

const SKEL_ICON = '<svg class="skel-icon" viewBox="0 0 200 200" aria-hidden="true"><path fill="currentColor" d="M60.7 28.6c.5 14.6 10.6 22.7 30.8 25.9 17.1 2.7 32.5 1.3 37.5-13.3 9.8-28.5-13.5-21.4-33.6-24.6-20.2-3.2-35.4-11.4-34.7 12m6.1 60c1.9 10.9 12 17.1 27.6 16.7 15.5-.4 27.1-6.5 28.8-18.3 3-21.8-14.2-13.4-29.7-12.9-15.7.5-30.4-6.9-26.7 14.5M87 138.5c3.1 8.1 11.6 11.6 23 9.1 11.4-2.4 19.4-6.2 18.7-15.5-1.2-17.2-12.5-11.8-23.9-9.3-11.4 2.4-23.8-.3-17.8 15.7m9.8 38.1c.9 6.9 6.8 10.9 16.1 10.9 9.3-.1 16.4-3.8 17.6-11.1 2.2-13.6-8.2-8.6-17.5-8.5-9.3 0-17.9-4.8-16.2 8.7"/></svg>';
const SKEL_ADD = '<svg class="skel-status" viewBox="0 -960 960 960" aria-hidden="true"><path fill="currentColor" d="M440-280h80v-160h160v-80H520v-160h-80v160H280v80h160v160Zm40 200q-83 0-156-31.5T197-197q-54-54-85.5-127T80-480q0-83 31.5-156T197-763q54-54 127-85.5T480-880q83 0 156 31.5T763-763q54 54 85.5 127T880-480q0 83-31.5 156T763-197q-54 54-127 85.5T480-80Zm0-80q134 0 227-93t93-227q0-134-93-227t-227-93q-134 0-227 93t-93 227q0 134 93 227t227 93Z"/></svg>';
const SKEL_ERROR = '<svg class="skel-status" viewBox="0 -960 960 960" aria-hidden="true"><path fill="currentColor" d="m336-280 144-144 144 144 56-56-144-144 144-144-56-56-144 144-144-144-56 56 144 144-144 144 56 56ZM480-80q-83 0-156-31.5T197-197q-54-54-85.5-127T80-480q0-83 31.5-156T197-763q54-54 127-85.5T480-880q83 0 156 31.5T763-763q54 54 85.5 127T880-480q0 83-31.5 156T763-197q-54 54-127 85.5T480-80Z"/></svg>';
const SKEL_OK = '<svg class="skel-status" viewBox="0 -960 960 960" aria-hidden="true"><path fill="currentColor" d="m424-296 282-282-56-56-226 226-114-114-56 56 170 170Zm56 216q-83 0-156-31.5T197-197q-54-54-85.5-127T80-480q0-83 31.5-156T197-763q54-54 127-85.5T480-880q83 0 156 31.5T763-763q54 54 85.5 127T880-480q0 83-31.5 156T763-197q-54 54-127 85.5T480-80Z"/></svg>';

const MESH_HULL_HELP = "Draw each selected region's mesh silhouette in blue over the preview. The saved image stays unchanged.";

export function updateMeshHullUI() {
  const chk = document.getElementById('chk-mesh-hull');
  const row = document.getElementById('mesh-hull-toggle-row');
  if (!chk || !row) return;
  const { available } = AtlasAPI.get_mesh_mask_state();
  chk.checked = AtlasAPI.get_mesh_hull_enabled();
  chk.disabled = !available;
  row.dataset.help = available
    ? MESH_HULL_HELP
    : 'Requires a usable .skel file with mesh attachments.';
  row.removeAttribute('title');
  syncHelpPopover();
}

export function updateForcedResizingUI() {
  document.getElementById('chk-forced-resizing').checked = AtlasAPI.get_forced_resizing();
}

document.getElementById('chk-mesh-hull').addEventListener('change', async (e) => {
  if (isBusy()) {
    e.target.checked = !e.target.checked;
    noteBusyConflict();
    return;
  }
  hideHelpPopover();
  try {
    await AtlasAPI.set_mesh_hull_enabled(e.target.checked);
    updateMeshHullUI();
    refreshMeshHullOverlay();
  } catch (err) {
    console.error(err);
    showToast('Failed to update Show Mesh Silhouettes.', 'error');
  }
});

document.getElementById('chk-forced-resizing').addEventListener('change', async (e) => {
  if (isBusy()) {
    e.target.checked = !e.target.checked;
    noteBusyConflict();
    return;
  }
  hideHelpPopover();
  try {
    await AtlasAPI.set_forced_resizing(e.target.checked);
    updateForcedResizingUI();
    updatePreview(getSelectedRegions());
  } catch (err) {
    console.error(err);
    showToast('Failed to update Forced Resizing.', 'error');
  }
});

document.getElementById('chk-mesh-mask').addEventListener('change', async (e) => {
  if (isBusy()) {
    e.target.checked = !e.target.checked;
    noteBusyConflict();
    return;
  }
  hideHelpPopover();
  try {
    const result = await AtlasAPI.set_mesh_mask_enabled(e.target.checked);
    updateMeshCroppingUI();
    updatePreview(getSelectedRegions()); // unrelated to repack -- always refresh, as today
    if (result) await onModPreviewReceived(result);
  } catch (err) {
    console.error(err);
    showToast('Failed to update Mesh Cropping.', 'error');
  }
});

document.getElementById('btn-pick-skel').addEventListener('click', async () => {
  const picked = await AtlasAPI.pick_skel_file();
  if (picked) {
    updateMeshCroppingUI();
    updatePreview(getSelectedRegions());
  }
});

// ─── Rename / Add / Remove ────────────────────────────────────────────────────

function openRenameModal() {
  if (isBusy()) return;
  const keys = getSelectedKeys();
  // Primary guard is updateRenameButtonState() (enabled only for exactly one
  // selection). Keep a strict click-time check too, in case a stale enabled
  // state slips through after a selection clear that skipped the updater.
  if (keys.length !== 1) return;
  const [key] = keys;
  const label = getSelectedLabels()[0];
  const input = document.getElementById('rename-name-input');
  input.value = label;
  document.getElementById('rename-name-error').classList.add('hidden');
  document.getElementById('rename-confirm-btn').disabled = false;
  document.getElementById('rename-modal').classList.remove('hidden');

  let validationId = 0;
  const revalidate = async () => {
    const id = ++validationId, value = input.value;
    document.getElementById('rename-confirm-btn').disabled = true;
    await new Promise(resolve => setTimeout(resolve, 150));
    if (id !== validationId) return {ok: false};
    let result;
    try { result = await AtlasAPI.validate_region_name(value, key); }
    catch (e) { result = {ok: false, reason: e.message}; }
    if (id !== validationId || input.value !== value) return {ok: false};
    const errorEl = document.getElementById('rename-name-error');
    const confirmBtn = document.getElementById('rename-confirm-btn');
    if (result.ok) {
      errorEl.classList.add('hidden');
      confirmBtn.disabled = isBusy();
    } else {
      errorEl.textContent = result.reason;
      errorEl.classList.remove('hidden');
      confirmBtn.disabled = true;
    }
    return result;
  };
  input.oninput = revalidate;
  revalidate();

  document.getElementById('rename-confirm-btn').onclick = async () => {
    if (isBusy()) return;
    const result = await revalidate();
    if (!result.ok) return;
    const confirmBtn = document.getElementById('rename-confirm-btn');
    confirmBtn.disabled = true;
    const prevSelectedKeys = getSelectedKeys();
    validationId++;
    document.getElementById('rename-modal').classList.add('hidden');
    await withBusy(async () => {
      try {
        const payload = await AtlasAPI.rename_region(key, result.value);
        await onModPreviewReceived(payload);
        await refreshStructuralUi(prevSelectedKeys);
      } catch (e) {
        console.error(e);
        if (e instanceof HostError) window.showAlert(e.message, 'Cannot rename region');
        else showToast('Failed to rename region.', 'error');
        confirmBtn.disabled = false;
      }
    });
  };
  document.getElementById('rename-cancel-btn').onclick = () => {
    if (isBusy()) return; // can't cancel out of a submission that's still in flight
    validationId++;
    document.getElementById('rename-modal').classList.add('hidden');
  };
}

document.getElementById('btn-rename-region').addEventListener('click', openRenameModal);

document.getElementById('btn-add-region').addEventListener('click', () => {
  if (isBusy()) return;
  const pages = AtlasAPI.get_page_names();
  const pageSelect = document.getElementById('add-page-select');
  pageSelect.replaceChildren(new Option('Auto (least used)', ''), ...pages.map(name => new Option(name, name)));
  document.getElementById('add-page-row').classList.toggle('hidden', pages.length <= 1);
  openAddRegionModal({
    getEffectiveNames: () => state.regionsData.map((r) => r.label),
    onConfirm: async (file, atlasName) => {
      if (isBusy()) return;
      await withBusy(async () => {
        const prevSelectedKeys = getSelectedKeys();
        const payload = await AtlasAPI.add_region(file, atlasName, pageSelect.value || null);
        await onModPreviewReceived(payload);
        await refreshStructuralUi(prevSelectedKeys);
      });
    },
  });
});

document.getElementById('btn-remove-region').addEventListener('click', async () => {
  if (isBusy()) return;
  const keys = getSelectedKeys();
  if (keys.length === 0) return; // button is disabled in this case; defensive no-op
  const btn = document.getElementById('btn-remove-region');
  btn.disabled = true;
  try {
    await withLock(async () => {
      const ok = await showConfirm(
        `Remove ${keys.length} region${keys.length > 1 ? 's' : ''}? This cannot be undone after Save.`,
        'Remove region' + (keys.length > 1 ? 's' : '') + '?',
      );
      if (!ok) return;
      const prevSelectedKeys = []; // removed regions can't remain selected — start from empty
      await withBusy(async () => {
        try {
          const payload = await AtlasAPI.remove_regions(keys);
          await onModPreviewReceived(payload);
          await refreshStructuralUi(prevSelectedKeys);
        } catch (e) {
          console.error(e);
          if (e instanceof HostError) window.showAlert(e.message, 'Cannot remove region');
          else showToast('Failed to remove region(s).', 'error');
        }
      });
    });
  } finally {
    updateRemoveButtonState();
    updateRenameButtonState();
  }
});
