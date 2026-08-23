import { AtlasAPI } from './js/atlas-api.js';
import { state, getSelectedNames } from './js/state.js';
import { showToast, showAlert, showConfirm, showMissingAtlasImagesDialog, showUpdateToast } from './js/dialogs.js';
import { initPanelResizer } from './js/panel-resizer.js';
import { initRepackInfoOverlay, enterEditMode, exitEditMode, ReplaceSelected, resetModify, saveModified, setMode, onModPreviewReceived } from './js/modify-mode.js';
import { initAppBar } from './js/app-bar.js';
import { loadRegions, updateButtons } from './js/region-list.js';
import { previewImg, resetPreview } from './js/preview.js';
import { copyPreviewImage, savePreviewImageAs } from './js/drop.js';
import { base64ToFile, loadFileAsFile, pathToFileUrl } from './js/platform.js';
import './js/updates.js'; // attaches window.showUpdateNotification / .showUpdateInstallFailed (pywebview-only; see file header)

// ─── Startup ──────────────────────────────────────────────────────────────────

/**
 * `window.pywebview.api` is injected asynchronously by pywebview — it is
 * NOT guaranteed to exist yet by `DOMContentLoaded` (pywebview's own
 * `before_load` event, which triggers the injection, fires "roughly
 * corresponding to DOMContentLoaded", i.e. no ordering guarantee either
 * way). Calling into it too early (e.g. `get_pref`/`startup_check`) would
 * silently no-op or fall through to the browser-only path. `launch.py`
 * always loads `www/index.html` via a literal `file://` URI (never true for
 * the hosted PWA/browser case), so that's a reliable synchronous signal
 * this is the desktop shell and it's worth waiting for `pywebviewready`.
 */
function _waitForPywebviewReady() {
  return new Promise((resolve) => {
    if (window.pywebview && window.pywebview.api) { resolve(); return; }
    if (location.protocol !== 'file:') { resolve(); return; }
    let settled = false;
    const onReady = () => {
      if (settled) return;
      settled = true;
      resolve();
    };
    window.addEventListener('pywebviewready', onReady, { once: true });
    setTimeout(() => {
      if (settled) return;
      settled = true;
      window.removeEventListener('pywebviewready', onReady);
      resolve();
    }, 5000);
  });
}

window.addEventListener('DOMContentLoaded', async () => {
  await _waitForPywebviewReady();
  initPanelResizer();
  initAppBar();
  if (window.pywebview) {
    await _clearStaleServiceWorkerUnderDesktop();
  } else {
    registerServiceWorker();
  }

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
        await _resetUiAfterFreshLoad();
      } catch (e) {
        console.error('launchQueue consumer error:', e);
        showToast(`Open failed: ${e.message || e}`, 'error');
      }
    });
  }

  // pywebview desktop equivalent of the above: CLI arg / "Open with" file
  // association. bridge.py's startup_check() reads sys.argv itself and, if
  // it matches, drives the native atlas-open flow (which calls back into
  // _resetUiAfterFreshLoad via window.loadAtlasFromNative) — nothing else
  // to do here but trigger it once on startup.
  if (window.pywebview && window.pywebview.api && window.pywebview.api.startup_check) {
    try {
      await window.pywebview.api.startup_check();
    } catch (e) {
      console.error('startup_check error:', e);
    }
  }
});

/**
 * Desktop shell (Phase 4 of the unify-js-engine plan): `launch.py` always
 * loads `www/index.html` via a literal `file://` URI, and `file://` is
 * never a valid service-worker registration origin (verified directly: the
 * browser itself rejects `register()` with "The URL protocol of the
 * current origin ('file://') is not supported") — so `registerServiceWorker()`'s
 * existing `location.protocol` guard below already prevents registration
 * from ever being attempted here, and there is no PWA-caching bug to guard
 * against (unlike Tauri's old `http://tauri.localhost` setup, which DID
 * satisfy `register()`'s origin requirement and so needed an explicit
 * runtime guard). This is just cheap insurance against a leftover
 * registration from some future platform/config change (e.g. a webview
 * that serves over http(s) instead of file://) — normally a no-op.
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
  updateButtons();
  await loadRegions();

  // Native window title (old Python engine's load_atlas() used to do this;
  // the JS engine has no equivalent hook, so it's centralized here instead
  // — covers all three open paths: Open button, drag-drop, CLI/file
  // association).
  if (window.pywebview && window.pywebview.api && window.pywebview.api.set_window_title) {
    try {
      await window.pywebview.api.set_window_title(AtlasAPI.get_current_atlas_filename());
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
    if (success) await _resetUiAfterFreshLoad();
  } catch (e) {
    console.error(e);
  }
}

// ─── Native (pywebview) bridge glue ────────────────────────────────────────
// pywebview's native CLI-arg/file-association/drag-drop paths hand Python a
// filesystem path, not a browser File. The .atlas TEXT itself is small
// (plain text, a few KB) and still crosses as base64. Sibling/mod PNGs do
// NOT -- bridge.py hands over their plain paths and these reconstruct File
// objects via fetch(file://...) (see platform.loadFileAsFile's doc comment:
// base64 through pywebview's evaluate_js bridge measured over 1s for a
// single ~5MB PNG, this measured under 10ms for the same file, 2026-08-23)
// and feed them into the same AtlasAPI used by the file-picker/browser-drop
// paths above. See atlas_toolkit/app/bridge.py.

/** Load an atlas opened via a native path (CLI arg, file association, or
 *  single-file native drag-drop with no in-DOM FileList available).
 *  `atlasDirectory` (added for parity-audit fix, 2026-08-23) is the folder the
 *  .atlas file lives in on disk — threaded through so extract/save dialogs can
 *  default to it, matching the old Python engine. */
async function loadAtlasFromNative(atlasBase64, atlasFilename, imagePathsMap, atlasDirectory) {
  try {
    const atlasFile = base64ToFile(atlasBase64, atlasFilename, 'text/plain');
    const imageFileMap = {};
    for (const [name, imgPath] of Object.entries(imagePathsMap || {})) {
      imageFileMap[name] = await loadFileAsFile(imgPath);
    }
    const ok = await AtlasAPI.load_atlas_from_file(atlasFile, imageFileMap, atlasDirectory || '');
    if (ok) await _resetUiAfterFreshLoad();
    return ok;
  } catch (e) {
    console.error('loadAtlasFromNative error:', e);
    return false;
  }
}

/** Apply a mod image dropped natively onto the currently-selected regions
 *  in Edit mode (native drag-drop delivers a path, not a browser File). */
async function applyNativeModImageDrop(imagePath) {
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
  // file:// URL goes straight into Image() — no fetch→File copy, no
  // toDataURL of the dropped PNG (perf fix, 2026-08-23). Preview after
  // merge is a blob: URL from canvas.toBlob(), not a data: URI.
  const result = await AtlasAPI.process_mod_image(pathToFileUrl(imagePath), names, repack);
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
