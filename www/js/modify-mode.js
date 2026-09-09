import { AtlasAPI } from './atlas-api.js';
import { state, getSelectedRegions, getSelectedKeys, getSelectedLabels } from './state.js';
import { validateRegionName } from './region-name-validation.js';
import {
  previewImg, previewContainer,
  resetPreview, applyTransform,
  drawRegionOverlay, clearOverlay,
  updatePreview, updateModifyPreview, updateSaveMergedButton, setPreviewSrc,
  fitScaleIfOversized,
} from './preview.js';
import { showToast, showConfirm, openAddRegionModal } from './dialogs.js';
import { updateModeToggleUI, updatePageSwitcher, setAdvanceMode } from './app-bar.js';
import { refreshPanelSplit } from './panel-resizer.js';
import { refreshModifiedHighlight, loadRegions, renderSelection, updateButtons, updateRemoveButtonState, updateRenameButtonState } from './region-list.js';

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
  // #options-row is always-visible now (round-4-reviewed design decision
  // -- see the mesh-mask design spec's UI section) and its own height is
  // fixed (--panel-header-h), unaffected by which of its children show per
  // mode -- so panel-resizer.js's minRightHeight() (options-row height +
  // statusBar height) no longer actually changes across a mode switch.
  // Keeping this call anyway: harmless (a no-op re-clamp to the same
  // floor), and it stays correct if minRightHeight()'s inputs ever change
  // again (corrected post-review, Fable, 2026-08-31 -- the previous comment
  // here claimed #options-row's height still changed, which stopped being
  // true once this task removed its whole-container .hidden toggle).
  refreshPanelSplit();
  // Picker visibility follows the mode-relevant mesh toggle; refresh so a
  // View↔Edit switch doesn't leave the button stuck on the previous mode.
  updateMeshCroppingUI();
  updateNestRegionsUI();
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
  try {
    const data = await AtlasAPI.enter_modify_mode();
    if (data) {
      // Same status regardless of page count, matching old Python engine's
      // ui/js/mode.js exactly (parity fix, 2026-08-23).
      applyModifyView(data, 'Select regions and click Modify Selected');
      refreshModifiedHighlight();
      // Restore Advance Mode -- persisted across sessions, re-applied on
      // every Edit Mode entry rather than left as transient DOM state.
      // Multi-page atlases never allow it regardless of the saved
      // preference (loadRegions() already hides #advance-mode-row for
      // them; skip restoring here too so the toolbar can't end up shown
      // for one).
      if (!AtlasAPI.is_multi_page()) {
        setAdvanceMode(await AtlasAPI.get_pref('advanceMode', false));
      }
    } else {
      showToast('Load an atlas first.', 'error');
    }
  } catch (e) {
    console.error(e);
    showToast('Failed to enter modify mode.', 'error'); // matches old ui/js/mode.js
  }
}

export async function exitEditMode() {
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
    // enter_modify_mode clears the batch list and returns a pristine view.
    const data = await AtlasAPI.enter_modify_mode();
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

/** Syncs the Mesh Cropping / Mesh-Aware Repack toggles + .skel picker button
 *  from AtlasAPI.get_mesh_mask_state(). Call after anything that can change
 *  that state: an atlas load (either load path), the toggle itself, a
 *  successful pick_skel_file(), or a mode switch that changes which toggle
 *  is the relevant one. The toggles' checked states always reflect the
 *  user's persisted preferences, independent of the current atlas's .skel
 *  availability -- the picker button communicates availability instead, and
 *  is hidden while the mode-relevant toggle is off or no atlas is loaded. */
export function updateMeshCroppingUI() {
  const { available, enabled, repackEnabled, skelFileName, unavailableReason } = AtlasAPI.get_mesh_mask_state();
  document.getElementById('chk-mesh-mask').checked = enabled;

  const repackChk = document.getElementById('chk-mesh-aware-repack');
  repackChk.checked = repackEnabled;
  repackChk.disabled = !available;
  document.getElementById('mesh-aware-repack-toggle-row').title = repackChk.disabled
    ? 'Requires a usable .skel file.'
    : 'Mask each region pixels to its mesh silhouette before packing, so the repacked atlas matches what extraction already shows.';

  // Shared .skel picker button -- relevant toggle depends on which mode is
  // currently active: View mode shows Mesh Cropping row, Edit mode shows
  // Mesh-Aware Repack row (CSS hides whichever one is not current), so the
  // button follows whichever toggle is actually visible right now, not
  // "either toggle is on" -- the two toggles are otherwise fully
  // independent of each other.
  const btn = document.getElementById('btn-pick-skel');
  // View mode follows Mesh Cropping; Edit mode follows Mesh-Aware Repack.
  // Also require an atlas so the picker stays hidden on the empty startup screen.
  const relevantToggleOn = state.currentMode === 'modify' ? repackEnabled : enabled;
  const atlasLoaded = !!AtlasAPI.get_current_atlas_filename();
  if (!relevantToggleOn || !atlasLoaded) {
    btn.classList.add('hidden');
    return;
  }
  btn.classList.remove('hidden');
  btn.classList.remove('skel-missing', 'skel-invalid', 'skel-ok');
  if (!skelFileName) {
    btn.textContent = '⚠️ Choose .skel file';
    btn.classList.add('skel-missing');
    btn.title = 'Pick a .skel file to enable mesh-based masking.';
  } else if (!available) {
    btn.textContent = '⚠️ ' + skelFileName;
    btn.classList.add('skel-invalid');
    btn.title = MESH_UNAVAILABLE_MESSAGES[unavailableReason] || 'This .skel file cannot be used for mesh masking.';
  } else {
    btn.textContent = skelFileName;
    btn.classList.add('skel-ok');
    btn.title = `Mesh masking active using ${skelFileName}. Click to choose a different file.`;
  }
}

/** Syncs the Nest Regions checkbox + gap-distance input from
 *  AtlasAPI.get_nest_options(). Call after anything that can change that
 *  state: the toggle itself, the gap-distance input, or app startup.
 *  Unlike updateMeshCroppingUI(), this is never gated by mesh availability
 *  -- Nest Regions works with no mesh data at all (Gap A space alone). */
export function updateNestRegionsUI() {
  const { enabled, gapDistance } = AtlasAPI.get_nest_options();
  document.getElementById('chk-nest-regions').checked = enabled;
  const gapInput = document.getElementById('nest-gap-distance');
  gapInput.disabled = !enabled;
  // Don't clobber an in-progress edit if this gets called while the user is
  // actively typing in the field (e.g. from a toggle elsewhere firing a
  // broader UI sync).
  if (document.activeElement !== gapInput) gapInput.value = gapDistance;
}

document.getElementById('chk-mesh-mask').addEventListener('change', async (e) => {
  if (structuralOpInFlight) {
    e.target.checked = !e.target.checked;
    showToast('Please wait for the current operation to finish.', 'error');
    return;
  }
  structuralOpInFlight = true;
  try {
    const result = await AtlasAPI.set_mesh_mask_enabled(e.target.checked);
    updateMeshCroppingUI();
    updatePreview(getSelectedRegions()); // unrelated to repack -- always refresh, as today
    if (result) await onModPreviewReceived(result);
  } catch (err) {
    console.error(err);
    showToast('Failed to update Mesh Cropping.', 'error');
  } finally {
    structuralOpInFlight = false;
  }
});

document.getElementById('chk-mesh-aware-repack').addEventListener('change', async (e) => {
  if (structuralOpInFlight) {
    e.target.checked = !e.target.checked;
    showToast('Please wait for the current operation to finish.', 'error');
    return;
  }
  structuralOpInFlight = true;
  try {
    const result = await AtlasAPI.set_mesh_aware_repack_enabled(e.target.checked);
    updateMeshCroppingUI();
    if (result) await onModPreviewReceived(result);
    // else: nothing modified yet, no packed-atlas preview to refresh -- leave the
    // extraction-composite preview (which this toggle never affects) as-is
  } catch (err) {
    console.error(err);
    showToast('Failed to update Mesh-Aware Repack.', 'error');
  } finally {
    structuralOpInFlight = false;
  }
});

document.getElementById('chk-nest-regions').addEventListener('change', async (e) => {
  // Final whole-branch review finding: the gap-distance debounce timer
  // (declared below) was not cleared here -- toggling off within its
  // 400ms window let it fire afterward (a stale repack for a
  // now-irrelevant setting) or during this op (an error toast for an
  // action the user already completed, via the structuralOpInFlight
  // guard in that other handler).
  if (nestGapDebounceTimer) clearTimeout(nestGapDebounceTimer);
  if (structuralOpInFlight) {
    e.target.checked = !e.target.checked;
    showToast('Please wait for the current operation to finish.', 'error');
    return;
  }
  structuralOpInFlight = true;
  try {
    // Final whole-branch review finding: nestPack is fully synchronous and
    // can take several real seconds on a large atlas (mesh-driven concave
    // footprints measured ~6.5x slower than the shipped benchmark's
    // no-mesh case) -- with no status message the tab just hard-freezes,
    // no spinner would even have a chance to animate. Paint the status
    // BEFORE the potentially-blocking call, with an explicit yield (a
    // synchronous DOM write alone isn't guaranteed to reach the screen
    // before a long synchronous stretch begins).
    setStatus('Repacking...');
    await new Promise(requestAnimationFrame);
    const result = await AtlasAPI.set_nest_regions_enabled(e.target.checked);
    updateNestRegionsUI();
    if (result) {
      await onModPreviewReceived(result); // resets status itself once done
    } else {
      setStatus('Ready'); // _maybeRerunRepack no-op'd (no mods yet) -- don't leave 'Repacking...' stuck
    }
  } catch (err) {
    console.error(err);
    showToast('Failed to update Nest Regions.', 'error');
    setStatus('Ready');
  } finally {
    structuralOpInFlight = false;
  }
});

let nestGapDebounceTimer = null;

document.getElementById('nest-gap-distance').addEventListener('input', (e) => {
  if (nestGapDebounceTimer) clearTimeout(nestGapDebounceTimer);
  // Don't even schedule an apply for an empty/invalid value -- Number('')
  // is 0, which normalizeGap (repack-nest.js) silently substitutes 4 for,
  // with nothing in the UI showing that happened. The blur listener below
  // resyncs the field to the true applied value once the user leaves it,
  // whatever state they left it in.
  if (e.target.value === '' || !e.target.checkValidity()) return;
  const px = Number(e.target.value);
  nestGapDebounceTimer = setTimeout(async () => {
    if (structuralOpInFlight) {
      // A structural op is mid-flight -- skip this cycle rather than queue
      // behind it, but resync the field (it may still be showing the typed
      // value even though nothing was applied) and tell the user, matching
      // the checkbox handlers' revert+toast pattern above.
      updateNestRegionsUI();
      showToast('Please wait for the current operation to finish.', 'error');
      return;
    }
    structuralOpInFlight = true;
    try {
      // See the same-purpose comment on the Nest Regions checkbox handler
      // above -- nestPack is synchronous and can run for real seconds.
      setStatus('Repacking...');
      await new Promise(requestAnimationFrame);
      const result = await AtlasAPI.set_nest_gap_distance(px);
      updateNestRegionsUI(); // resync the field to the actually-applied (normalized) value
      if (result) {
        await onModPreviewReceived(result); // resets status itself once done
      } else {
        setStatus('Ready'); // _maybeRerunRepack no-op'd (no mods yet) -- don't leave 'Repacking...' stuck
      }
    } catch (err) {
      console.error(err);
      showToast('Failed to update gap distance.', 'error');
      setStatus('Ready');
    } finally {
      structuralOpInFlight = false;
    }
  }, 400);
});

// Catch-all: whatever the field is left showing when the user leaves it
// (cleared, out-of-range, or simply never triggered a debounced apply
// because the input event above bailed out) must not silently diverge from
// what the packer is actually using -- resync on blur. updateNestRegionsUI's
// own activeElement guard means this only takes effect once the field has
// genuinely lost focus, never mid-edit.
document.getElementById('nest-gap-distance').addEventListener('blur', () => {
  updateNestRegionsUI();
});

document.getElementById('btn-pick-skel').addEventListener('click', async () => {
  const picked = await AtlasAPI.pick_skel_file();
  if (picked) {
    updateMeshCroppingUI();
    updatePreview(getSelectedRegions());
  }
});

// ─── Rename Region Modal (Task 9) ─────────────────────────────────────────────
// Shared across every structural confirm handler (Rename now; Add/Remove in
// Tasks 10-11) -- the risk (two concurrent applyStructuralBatch() calls
// racing on the same session) isn't scoped to any one modal, so this can't
// be a per-open local variable the way an earlier fix round tried.
let structuralOpInFlight = false;

function openRenameModal() {
  if (structuralOpInFlight) return;
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

  const revalidate = () => {
    const effectiveOthers = state.regionsData
      .filter((r) => r.key !== key)
      .map((r) => r.label);
    const result = validateRegionName(input.value, effectiveOthers);
    const errorEl = document.getElementById('rename-name-error');
    const confirmBtn = document.getElementById('rename-confirm-btn');
    if (result.ok) {
      errorEl.classList.add('hidden');
      confirmBtn.disabled = structuralOpInFlight;
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
    if (structuralOpInFlight) return;
    const result = revalidate();
    if (!result.ok) return;
    structuralOpInFlight = true;
    const confirmBtn = document.getElementById('rename-confirm-btn');
    confirmBtn.disabled = true;
    const prevSelectedKeys = getSelectedKeys();
    try {
      const payload = await AtlasAPI.rename_region(key, result.value);
      document.getElementById('rename-modal').classList.add('hidden');
      await onModPreviewReceived(payload);
      await refreshStructuralUi(prevSelectedKeys);
    } catch (e) {
      console.error(e);
      showToast('Failed to rename region.', 'error');
      confirmBtn.disabled = false;
    } finally {
      structuralOpInFlight = false;
    }
  };
  document.getElementById('rename-cancel-btn').onclick = () => {
    if (structuralOpInFlight) return; // can't cancel out of a submission that's still in flight
    document.getElementById('rename-modal').classList.add('hidden');
  };
}

document.getElementById('btn-rename-region').addEventListener('click', openRenameModal);

document.getElementById('btn-add-region').addEventListener('click', () => {
  if (structuralOpInFlight) return;
  openAddRegionModal({
    getEffectiveNames: () => state.regionsData.map((r) => r.label),
    onConfirm: async (file, atlasName) => {
      if (structuralOpInFlight) return;
      structuralOpInFlight = true;
      try {
        const prevSelectedKeys = getSelectedKeys();
        const payload = await AtlasAPI.add_region(file, atlasName);
        await onModPreviewReceived(payload);
        await refreshStructuralUi(prevSelectedKeys);
      } finally {
        structuralOpInFlight = false;
      }
    },
  });
});

document.getElementById('btn-remove-region').addEventListener('click', async () => {
  if (structuralOpInFlight) return;
  const keys = getSelectedKeys();
  if (keys.length === 0) return; // button is disabled in this case; defensive no-op
  structuralOpInFlight = true;
  const btn = document.getElementById('btn-remove-region');
  btn.disabled = true;
  try {
    const ok = await showConfirm(
      `Remove ${keys.length} region${keys.length > 1 ? 's' : ''}? This cannot be undone after Save.`,
      'Remove region' + (keys.length > 1 ? 's' : '') + '?',
    );
    if (!ok) return;
    const prevSelectedKeys = []; // removed regions can't remain selected — start from empty
    try {
      const payload = await AtlasAPI.remove_regions(keys);
      await onModPreviewReceived(payload);
      await refreshStructuralUi(prevSelectedKeys);
    } catch (e) {
      console.error(e);
      showToast('Failed to remove region(s).', 'error');
    }
  } finally {
    structuralOpInFlight = false;
    updateRemoveButtonState();
    updateRenameButtonState();
  }
});
