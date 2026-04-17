import { AtlasAPI } from './js/atlas-api.js';
import { state } from './js/state.js';
import { showToast, showConfirm, showMissingAtlasImagesDialog } from './js/dialogs.js';
import { checkForTauriUpdate } from './js/update.js';
import { initPanelResizer } from './js/panel-resizer.js';
import { initRepackInfoOverlay, enterEditMode, exitEditMode, ReplaceSelected, saveModified } from './js/modify-mode.js';
import { loadRegions, updateButtons } from './js/region-list.js';
import { previewImg, resetPreview } from './js/preview.js';
import { copyPreviewImage, savePreviewImageAs } from './js/drop.js';

// ─── Startup ──────────────────────────────────────────────────────────────────
window.addEventListener('DOMContentLoaded', async () => {
  initPanelResizer();
  registerServiceWorker();

  const repackPref     = AtlasAPI.get_pref('repack', false);
  const repackModePref = AtlasAPI.get_pref('repackMode', 'page');
  document.getElementById('chk-repack').checked = repackPref;
  document.getElementById('sel-repack-mode').value = repackModePref === 'all' ? 'all' : 'page';

  initRepackInfoOverlay();

  const loaded = await AtlasAPI.startup_check();
  if (loaded) await loadRegions();

  checkForTauriUpdate();
});

function registerServiceWorker() {
  if (!('serviceWorker' in navigator)) return;
  if (location.protocol !== 'http:' && location.protocol !== 'https:') return;
  navigator.serviceWorker.register('./sw.js').catch((err) => {
    console.warn('Service worker registration failed:', err);
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
  showToast(result, result.includes('Error') ? 'error' : 'success');
  document.getElementById('status-text').innerText = 'Ready';
}

async function extractAll() {
  if (document.getElementById('count').innerText === '0') return;
  const confirmed = await showConfirm('Are you sure you want to extract all regions?', 'Confirm Extraction');
  if (!confirmed) return;
  document.getElementById('status-text').innerText = 'Extracting ALL...';
  const result = await AtlasAPI.extract_files(null);
  showToast(result, result.includes('Error') ? 'error' : 'success');
  document.getElementById('status-text').innerText = 'Ready';
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
