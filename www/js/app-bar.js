/**
 * app-bar.js
 * Wires the full-width app-bar: the persistent View/Edit mode toggle and the
 * multi-page preview switcher. Mode-switch and page-switch UI state are kept in
 * sync from here; setMode() (modify-mode.js) is the single choke point that
 * calls updateModeToggleUI() + updatePageSwitcher() whenever the mode changes.
 */
import { AtlasAPI } from './atlas-api.js';
import { state } from './state.js';
import { enterEditMode, exitEditMode } from './modify-mode.js';
import { previewImg, previewContainer, resetPreview, applyTransform, fitScaleIfOversized, setPreviewSrc } from './preview.js';

/** Reflect the current mode on the toggle, and gate Edit on regions loaded. */
export function updateModeToggleUI() {
  const extractBtn = document.getElementById('mode-extract');
  const modifyBtn  = document.getElementById('mode-modify');
  if (!extractBtn || !modifyBtn) return;
  extractBtn.classList.toggle('active', state.currentMode !== 'modify');
  modifyBtn.classList.toggle('active', state.currentMode === 'modify');
  const count = parseInt(document.getElementById('count').innerText, 10) || 0;
  // Never leave the toggle stuck on a disabled Edit button while in edit mode.
  modifyBtn.disabled = count === 0 && state.currentMode !== 'modify';
}

/** Single choke point for every place that flips Advance Mode on/off, so the
 *  checkbox, the toolbar's visibility, and the body class that lets #sidebar-head
 *  track which row it lines up with (#repack-options when the toolbar is hidden,
 *  #status-bar when it's showing) never drift out of sync with each other. */
export function setAdvanceMode(active) {
  document.getElementById('chk-advance-mode').checked = active;
  document.getElementById('advance-toolbar').classList.toggle('hidden', !active);
  document.body.classList.toggle('advance-mode-on', active);
}

/** Show/refresh the page switcher; visible only in edit mode on a multi-page atlas. */
export function updatePageSwitcher() {
  const switcher = document.getElementById('modify-page-switcher');
  if (!switcher) return;
  const multi = state.currentMode === 'modify' && state.modifyPages.length > 1;
  switcher.classList.toggle('hidden', !multi);
  if (!multi) return;

  let idx = Number.isInteger(state.modifyActivePageIndex)
    ? state.modifyActivePageIndex
    : state.modifyPages.indexOf(state.modifyActivePage);
  if (idx < 0) idx = 0;
  state.modifyActivePageIndex = idx;
  state.modifyActivePage = state.modifyPages[idx] || state.modifyActivePage;
  document.getElementById('page-indicator').innerText =
    `Page ${idx + 1} / ${state.modifyPages.length}`;
  document.getElementById('page-prev').disabled = idx <= 0;
  document.getElementById('page-next').disabled = idx >= state.modifyPages.length - 1;
}

/** Fit the preview image into the container after its source changes. */
function fitPreviewOnLoad() {
  previewImg.onload = function () {
    resetPreview();
    const containerW = previewContainer.clientWidth - 40;
    const containerH = previewContainer.clientHeight - 40;
    const imgW = previewImg.naturalWidth;
    const imgH = previewImg.naturalHeight;
    const fitScale = fitScaleIfOversized(containerW, containerH, imgW, imgH);
    if (fitScale !== null) {
      state.viewState.scale = fitScale;
    }
    applyTransform(); // also redraws the overlay (filtered to the active page)
    previewImg.onload = null;
  };
}

async function goToPage(newIndex) {
  if (newIndex < 0 || newIndex >= state.modifyPages.length) return;
  state.modifyActivePageIndex = newIndex;
  state.modifyActivePage = state.modifyPages[newIndex];
  updatePageSwitcher();
  try {
    const data = await AtlasAPI.get_modify_page_preview(newIndex);
    if (!data || !data.image) return;
    setPreviewSrc(data.image);
    previewImg.style.display = 'block';
    fitPreviewOnLoad();
  } catch (e) {
    console.error('goToPage error:', e);
  }
}

export function initAppBar() {
  const extractBtn = document.getElementById('mode-extract');
  const modifyBtn  = document.getElementById('mode-modify');

  extractBtn.addEventListener('click', () => {
    if (state.currentMode !== 'modify') return;
    exitEditMode();
  });
  modifyBtn.addEventListener('click', () => {
    if (state.currentMode === 'modify') return;
    enterEditMode();
  });

  document.getElementById('page-prev').addEventListener('click', () => {
    goToPage(state.modifyActivePageIndex - 1);
  });
  document.getElementById('page-next').addEventListener('click', () => {
    goToPage(state.modifyActivePageIndex + 1);
  });

  document.getElementById('chk-advance-mode').addEventListener('change', (e) => {
    setAdvanceMode(e.target.checked);
    // Persist like #chk-repack does (modify-mode.js's own change listener) --
    // restored on every Edit Mode entry in enterEditMode(), not here, since
    // setAdvanceMode() is also called by the multi-page force-off reset
    // (region-list.js), which must NOT overwrite the user's real preference.
    AtlasAPI.set_pref('advanceMode', e.target.checked);
  });

  updateModeToggleUI();
}
