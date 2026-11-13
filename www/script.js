import { desktopCall, onDesktopEvent } from './js/desktop.js';
import { startEngine } from './js/engine-client.js';
import { AtlasAPI, setEngineStartupError } from './js/atlas-api.js';
import { state, getSelectedKeys, getSelectedRegions } from './js/state.js';
import { showToast, showAlert, showConfirm, showMissingAtlasImagesDialog, showUpdateToast } from './js/dialogs.js';
import { initPanelResizer } from './js/panel-resizer.js';
import { initSaveSplitMenu, enterEditMode, exitEditMode, ReplaceSelected, resetModify, saveModified, setMode, onModPreviewReceived, updateMeshCroppingUI, updateForcedResizingUI, updateMeshHullUI } from './js/modify-mode.js';
import { initAppBar } from './js/app-bar.js';
import { initSettingsPanel } from './js/settings-panel.js';
import { initOptionsPopover } from './js/options-popover.js';
import { initOptionsRowCollapse } from './js/options-row.js';
import { loadRegions, updateButtons, updateRemoveButtonState, updateRenameButtonState } from './js/region-list.js';
import { previewImg, resetPreview, clearOverlay } from './js/preview.js';
import { copyPreviewImage, savePreviewImageAs, handleDesktopDrop } from './js/drop.js';
import { fileFromUrl, isPywebviewDesktop } from './js/platform.js';
import { isBusy, noteBusyConflict, isModalOverlayOpen } from './js/busy-overlay.js';
import { formatAppTitle, versionFromDocument } from './js/app-title.js';
import './js/updates.js'; // attaches window.showUpdateNotification / .showUpdateInstallFailed (pywebview-only; see file header)

let appVersion = '';
let closePromptOpen = false;

export async function handleConfirmClose() {
  if (closePromptOpen) return;
  if (isModalOverlayOpen()) {
    await desktopCall('close_answer', [false]);
    return;
  }
  closePromptOpen = true;
  try {
    const ok = await showConfirm('You have unsaved atlas modifications.\nContinue and discard them?', 'Discard modifications?');
    await desktopCall('close_answer', [ok]);
  } catch (e) { console.error(e); }
  finally { closePromptOpen = false; }
}

function applyAppTitle(atlasFilename) {
  document.title = formatAppTitle(appVersion, atlasFilename);
}

// ─── Startup ──────────────────────────────────────────────────────────────────

window.addEventListener('DOMContentLoaded', async () => {
  let info = null;
  if (isPywebviewDesktop()) {
    const actions = {
      openAtlasBundle,
      applyModImage: applyNativeModImageDrop,
      confirmDiscard: () => showConfirm('You have unsaved atlas modifications.\nContinue and discard them?', 'Discard modifications?'),
    };
    onDesktopEvent('drop', e => handleDesktopDrop(e, actions).catch(console.error));
    onDesktopEvent('confirm-close', handleConfirmClose);
    onDesktopEvent('update-available', e => window.showUpdateNotification(e.payload));
    const installButton = document.getElementById('btn-install-update-file');
    try {
      info = await desktopCall('startup');
      if (info.updateFailure) window.showUpdateInstallFailed(info.updateFailure);
      if (info?.devBuild) installButton.classList.remove('hidden');
    } catch (e) {
      info = null;
      console.error(e);
      showToast(`Startup check failed: ${e.message}`, 'error');
    }
    installButton.addEventListener('click', async () => {
      try {
        const r = await desktopCall('install_update_from_file');
        if (!r.ok && !r.cancelled) await showAlert(r.error || 'Install failed.', 'Install update');
      } catch (e) {
        await showAlert(e.message, 'Install update');
      }
    });
  }
  const statusText = document.getElementById('status-text');
  const preparingText = 'Preparing AtlasToolkit engine...';
  statusText.textContent = preparingText;
  startEngine().then(async () => {
    await AtlasAPI.init_settings_from_prefs();
    if (statusText.textContent === preparingText) statusText.textContent = 'Ready';
  }).catch(error => {
    setEngineStartupError(error);
    if (statusText.textContent === preparingText) statusText.textContent = 'AtlasToolkit failed to start';
  });
  appVersion = info ? String(info.version).replace(/^v/i, '').trim() : versionFromDocument();
  applyAppTitle('');
  initPanelResizer();
  initAppBar();
  initOptionsPopover();
  initSettingsPanel();
  initOptionsRowCollapse();
  // Stamp the initial body.mode-extract class setMode()'s CSS-gated
  // #mesh-mask-toggle-row depends on -- setMode() is otherwise only called
  // reactively (entering/exiting modify mode, or on a fresh atlas load
  // while already in modify mode), so without this call that row would
  // show on first page load until the user toggles modes at least once
  // (found via adversarial DOM smoke test, 2026-08-31).
  setMode('extract');
  if (isPywebviewDesktop()) await _clearStaleServiceWorkerUnderDesktop();
  else registerServiceWorker();

  const copySkelPref = await AtlasAPI.get_pref('copySkel', true);
  document.getElementById('chk-copy-skel').checked = copySkelPref;
  await AtlasAPI.init_mesh_mask_from_pref();
  await AtlasAPI.init_forced_resizing_from_pref();
  await AtlasAPI.init_mesh_hull_from_pref();
  updateMeshCroppingUI();
  updateForcedResizingUI();
  updateMeshHullUI();

  initSaveSplitMenu();

  // PWA open-with (File Handling API): the browser hands us the launched
  // file via launchQueue instead of process args.
  if ('launchQueue' in window) {
    window.launchQueue.setConsumer(async (launchParams) => {
      if (!launchParams.files || launchParams.files.length === 0) return;
      try {
        const file = await launchParams.files[0].getFile();
        const ok = await AtlasAPI.load_atlas_from_file(file);
        if (!ok) { showToast('Failed to load atlas file.', 'error'); return; }
        await _resetUiAfterFreshLoad();
      } catch (e) {
        console.error('launchQueue consumer error:', e);
        showToast(`Open failed: ${e.message || e}`, 'error');
      }
    });
  }

  if (info?.atlasError) showToast(info.atlasError, 'error');
  if (info?.atlas) await openAtlasBundle(info.atlas);
});

/**
 * Desktop now runs on http and must never register a service worker.
 * Clear stale registrations and caches before continuing desktop startup.
 */
async function _clearStaleServiceWorkerUnderDesktop() {
  if (!('serviceWorker' in navigator)) return;
  try {
    const registrations = await navigator.serviceWorker.getRegistrations();
    await Promise.all(registrations.map((r) => r.unregister()));
    if ('caches' in window) {
      const keys = await caches.keys();
      await Promise.all(keys.map((k) => caches.delete(k)));
    }
  } catch (e) {
    console.warn('Stale service-worker cleanup failed:', e);
  }
}

/**
 * Register the service worker and wire up a user-prompted update flow: PWA
 * updates happen via the SW's own cache-name bump, so when a new worker is
 * waiting we show a toast rather than silently swapping the controller out
 * from under the running page (see sw.js).
 */
function registerServiceWorker() {
  if (!('serviceWorker' in navigator)) return;
  if (location.protocol !== 'http:' && location.protocol !== 'https:') return;

  // Only a user-initiated "Refresh" click should reload the page. On a first
  // visit (or after a cache clear) the new worker's activate() still calls
  // clients.claim(), which fires controllerchange even though there's no
  // previous version running — reloading then would be spurious, and could
  // even drop a launchQueue-delivered open-with file. So gate the reload on
  // the toast's click handler, not on controllerchange alone.
  let userInitiatedUpdate = false;

  const promptUpdate = (worker) => {
    showUpdateToast(() => {
      userInitiatedUpdate = true;
      worker.postMessage('SKIP_WAITING');
    });
  };

  navigator.serviceWorker.register('./sw.js').then((registration) => {
    if (registration.waiting) promptUpdate(registration.waiting);

    registration.addEventListener('updatefound', () => {
      const newWorker = registration.installing;
      if (!newWorker) return;
      newWorker.addEventListener('statechange', () => {
        // Only prompt if this is an update to an already-controlled page —
        // the very first install has nothing to hand off from.
        if (newWorker.state === 'installed' && navigator.serviceWorker.controller) {
          promptUpdate(newWorker);
        }
      });
    });
  }).catch((err) => {
    console.warn('Service worker registration failed:', err);
  });

  navigator.serviceWorker.addEventListener('controllerchange', () => {
    if (!userInitiatedUpdate) return;
    window.location.reload();
  });
}

/**
 * Reset the UI to a fresh-load state after a new atlas has been loaded into
 * AtlasAPI — return to View mode (a fresh atlas means a fresh session) and
 * re-render the region list/preview from scratch. Shared by the file-picker
 * open flow and native (pywebview) opens/drops, which land the atlas the
 * same way but can't reuse a DOM click handler.
 */
async function _resetUiAfterFreshLoad() {
  if (state.currentMode === 'modify') {
    AtlasAPI.exit_modify_mode();
    state.modifyRegionBounds = {};
    state.modifyPages        = [];
    state.modifyRegionPages  = {};
    state.modifyActivePage   = null;
    state.hasModImage        = false;
    setMode('extract');
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

  applyAppTitle(AtlasAPI.get_current_atlas_filename());
  // Native window title (old Python engine's load_atlas() used to do this;
  // the JS engine has no equivalent hook, so it's centralized here instead
  // — covers all three open paths: Open button, drag-drop, CLI/file
  // association).
  if (isPywebviewDesktop()) {
    try {
      await desktopCall('set_window_title', [AtlasAPI.get_current_atlas_filename()]);
    } catch (_) { /* non-fatal */ }
  }
}

async function openFile() {
  try {
    if (AtlasAPI.has_pending_modifications && AtlasAPI.has_pending_modifications()) {
      const ok = await showConfirm(
        // matches old Python engine's ui/js/ui.js DISCARD_MOD_MESSAGE exactly
        'You have unsaved atlas modifications.\nContinue and discard them?',
        'Discard modifications?',
      );
      if (!ok) return;
    }
    const success = await AtlasAPI.choose_file();
    // Open lives in the always-visible app-bar left zone, so it can be
    // clicked mid-edit. The pending-mods guard above already handled discard.
    if (success === 'cancelled') {
      showToast('Cancelled', 'info');
      return;
    }
    if (success) await _resetUiAfterFreshLoad();
  } catch (e) {
    console.error(e);
    showToast(`Open failed: ${e.message || e}`, 'error');
  }
}

export async function openAtlasBundle(bundle, {viaDrop = false} = {}) {
  if (isBusy()) { noteBusyConflict(); return false; }
  try {
    const ok = await AtlasAPI.load_atlas_bundle(bundle);
    if (ok === 'cancelled') { showToast('Cancelled', 'info'); return false; }
    if (!ok) { showToast('Failed to load atlas file.', 'error'); return false; }
    await _resetUiAfterFreshLoad();
    if (viaDrop) showToast('Atlas loaded via drag & drop.', 'success');
    return true;
  } catch (e) {
    console.error(e);
    showToast('Failed to load atlas file.', 'error');
    return false;
  }
}

/** Apply a mod image dropped natively onto the currently-selected regions
 *  in Edit mode (native drag-drop delivers a path, not a browser File). */
async function applyNativeModImageDrop(file) {
  if (isBusy()) {
    noteBusyConflict();
    return false;
  }
  if (state.currentMode !== 'modify') {
    showToast('Enter Edit Mode first to drop images.', 'error');
    return false;
  }
  const keys = getSelectedKeys();
  if (keys.length === 0) {
    showToast('Select at least one region first.', 'error');
    return false;
  }
  // Fetch the granted desktop asset into a File for image processing.
  const result = await AtlasAPI.process_mod_image(await fileFromUrl(file, 'image/png'), keys);
  if (result) {
    await onModPreviewReceived(result);
    showToast('Mod image loaded via drag & drop.', 'success');
    return true;
  }
  showToast('Failed to process mod image.', 'error');
  return false;
}

async function extractSelected() {
  if (state.selectedIndices.size === 0) return;
  const regions = getSelectedRegions();
  document.getElementById('status-text').innerText = 'Extracting...';
  const result = await AtlasAPI.extract_files(regions);
  showToast(result, _extractToastType(result));
  document.getElementById('status-text').innerText = 'Ready';
}

async function extractAll() {
  if (document.getElementById('count').innerText === '0') return;
  const confirmed = await showConfirm('Are you sure you want to extract all regions?', 'Confirm Extraction');
  if (!confirmed) return;
  document.getElementById('status-text').innerText = 'Extracting ALL...';
  const result = await AtlasAPI.extract_files(null);
  showToast(result, _extractToastType(result));
  document.getElementById('status-text').innerText = 'Ready';
}

function _extractToastType(result) {
  if (result === 'Cancelled') return 'info';
  if (result.startsWith('Error') || result.startsWith('No ')) return 'error';
  return 'success';
}

// ─── Expose globals for inline onclick handlers in index.html ─────────────────
window.openFile            = openFile;
window.enterEditMode       = enterEditMode;
window.exitEditMode        = exitEditMode;
window.ReplaceSelected     = ReplaceSelected;
window.saveModified        = saveModified;
window.extractSelected     = extractSelected;
window.extractAll          = extractAll;
window.copyPreviewImage    = copyPreviewImage;
window.savePreviewImageAs  = savePreviewImageAs;
window.saveMergedImage     = savePreviewImageAs;
window.resetModify         = resetModify;
window.showConfirm         = showConfirm;
window.showAlert           = showAlert;
window.showMissingAtlasImages = showMissingAtlasImagesDialog;
window.showToast           = showToast;
