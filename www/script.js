import { AtlasAPI } from './js/atlas-api.js';
import { state } from './js/state.js';
import { showToast, showConfirm, showMissingAtlasImagesDialog, showUpdateToast } from './js/dialogs.js';
import { initPanelResizer } from './js/panel-resizer.js';
import { initRepackInfoOverlay, enterEditMode, exitEditMode, ReplaceSelected, saveModified } from './js/modify-mode.js';
import { loadRegions, updateButtons } from './js/region-list.js';
import { previewImg, resetPreview } from './js/preview.js';
import { copyPreviewImage, savePreviewImageAs } from './js/drop.js';

// ─── Startup ──────────────────────────────────────────────────────────────────
window.addEventListener('DOMContentLoaded', async () => {
  initPanelResizer();
  registerServiceWorker();

  const repackPref     = await AtlasAPI.get_pref('repack', false);
  const repackModePref = await AtlasAPI.get_pref('repackMode', 'page');
  document.getElementById('chk-repack').checked = repackPref;
  document.getElementById('sel-repack-mode').value = repackModePref === 'all' ? 'all' : 'page';

  initRepackInfoOverlay();

  const loaded = await AtlasAPI.startup_check();
  if (loaded) await loadRegions();

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

async function openFile() {
  try {
    const success = await AtlasAPI.choose_file();
    if (success) {
      state.selectedIndices.clear();
      state.lastClickIndex = -1;
      previewImg.style.display = 'none';
      resetPreview();
      updateButtons();
      await loadRegions();
    }
  } catch (e) {
    console.error(e);
  }
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
window.showConfirm         = showConfirm;
window.showMissingAtlasImages = showMissingAtlasImagesDialog;
window.showToast           = showToast;
