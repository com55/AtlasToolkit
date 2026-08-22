import { AtlasAPI } from './js/atlas-api.js';
import { state, getSelectedNames } from './js/state.js';
import { showToast, showAlert, showConfirm, showMissingAtlasImagesDialog, showUpdateToast } from './js/dialogs.js';
import { initPanelResizer } from './js/panel-resizer.js';
import { initRepackInfoOverlay, enterEditMode, exitEditMode, ReplaceSelected, resetModify, saveModified, setMode, onModPreviewReceived } from './js/modify-mode.js';
import { initAppBar } from './js/app-bar.js';
import { loadRegions, updateButtons } from './js/region-list.js';
import { previewImg, resetPreview } from './js/preview.js';
import { copyPreviewImage, savePreviewImageAs } from './js/drop.js';
import { base64ToFile } from './js/platform.js';

// ─── Startup ──────────────────────────────────────────────────────────────────
window.addEventListener('DOMContentLoaded', async () => {
  initPanelResizer();
  initAppBar();
  registerServiceWorker();

  const repackPref = await AtlasAPI.get_pref('repack', false);
  document.getElementById('chk-repack').checked = repackPref;

  initRepackInfoOverlay();

  // PWA open-with (File Handling API): the browser hands us the launched
  // file via launchQueue instead of process args.
  if ('launchQueue' in window) {
    window.launchQueue.setConsumer(async (launchParams) => {
      if (!launchParams.files || launchParams.files.length === 0) return;
      try {
        const file = await launchParams.files[0].getFile();
        const ok = await AtlasAPI.load_atlas_from_file(file);
        if (!ok) { showToast('Failed to load atlas file.', 'error'); return; }
        state.selectedIndices.clear();
        state.lastClickIndex = -1;
        previewImg.style.display = 'none';
        resetPreview();
        updateButtons();
        await loadRegions();
      } catch (e) {
        console.error('launchQueue consumer error:', e);
        showToast(`Open failed: ${e.message || e}`, 'error');
      }
    });
  }
});

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
  updateButtons();
  await loadRegions();
}

async function openFile() {
  try {
    if (AtlasAPI.has_pending_modifications && AtlasAPI.has_pending_modifications()) {
      const ok = await showConfirm(
        'You have unsaved atlas modifications. Load a new atlas and discard them?',
        'Discard modifications?',
      );
      if (!ok) return;
    }
    const success = await AtlasAPI.choose_file();
    // Open lives in the always-visible app-bar left zone, so it can be
    // clicked mid-edit. The pending-mods guard above already handled discard.
    if (success) await _resetUiAfterFreshLoad();
  } catch (e) {
    console.error(e);
  }
}

// ─── Native (pywebview) bridge glue ────────────────────────────────────────
// pywebview's native CLI-arg/file-association/drag-drop paths hand Python a
// filesystem path, not a browser File — bridge.py reads it (and any sibling
// page images) as base64 and calls these via evaluate_js() to reconstruct
// File objects client-side and feed them into the same AtlasAPI used by the
// file-picker/browser-drop paths above. See atlas_toolkit/app/bridge.py.

/** Load an atlas opened via a native path (CLI arg, file association, or
 *  single-file native drag-drop with no in-DOM FileList available). */
async function loadAtlasFromNative(atlasBase64, atlasFilename, imagesBase64Map) {
  try {
    const atlasFile = base64ToFile(atlasBase64, atlasFilename, 'text/plain');
    const imageFileMap = {};
    for (const [name, b64] of Object.entries(imagesBase64Map || {})) {
      imageFileMap[name] = base64ToFile(b64, name, 'image/png');
    }
    const ok = await AtlasAPI.load_atlas_from_file(atlasFile, imageFileMap);
    if (ok) await _resetUiAfterFreshLoad();
    return ok;
  } catch (e) {
    console.error('loadAtlasFromNative error:', e);
    return false;
  }
}

/** Apply a mod image dropped natively onto the currently-selected regions
 *  in Edit mode (native drag-drop delivers a path, not a browser File). */
async function applyNativeModImageDrop(imageBase64, filename) {
  if (state.currentMode !== 'modify') {
    showToast('Enter Edit Mode first to drop images.', 'error');
    return false;
  }
  const names = getSelectedNames();
  if (names.length === 0) {
    showToast('Select at least one region first.', 'error');
    return false;
  }
  const repack = document.getElementById('chk-repack').checked;
  const file = base64ToFile(imageBase64, filename, 'image/png');
  const result = await AtlasAPI.process_mod_image(file, names, repack);
  if (result) {
    onModPreviewReceived(result);
    showToast('Mod image loaded via drag & drop.', 'success');
    return true;
  }
  showToast('Failed to process mod image.', 'error');
  return false;
}

async function extractSelected() {
  if (state.selectedIndices.size === 0) return;
  const names = Array.from(state.selectedIndices).map(i => state.regionsData[i]);
  document.getElementById('status-text').innerText = 'Extracting...';
  const result = await AtlasAPI.extract_files(names);
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

// ─── Expose for the pywebview native bridge (evaluate_js from Python) ─────────
window.AtlasAPI                  = AtlasAPI;
window.loadAtlasFromNative       = loadAtlasFromNative;
window.applyNativeModImageDrop   = applyNativeModImageDrop;
