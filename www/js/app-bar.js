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
import { previewImg, previewContainer, resetPreview, applyTransform, fitScaleIfOversized } from './preview.js';

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

/** Show/refresh the page switcher; visible only in edit mode on a multi-page atlas. */
export function updatePageSwitcher() {
  const switcher = document.getElementById('modify-page-switcher');
  if (!switcher) return;
  const multi = state.currentMode === 'modify' && state.modifyPages.length > 1;
  switcher.classList.toggle('hidden', !multi);
  if (!multi) return;

  let idx = state.modifyPages.indexOf(state.modifyActivePage);
  if (idx < 0) idx = 0;
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
  const pageName = state.modifyPages[newIndex];
  state.modifyActivePage = pageName;
  updatePageSwitcher();
  try {
    const data = await AtlasAPI.get_modify_page_preview(pageName);
    if (!data || !data.image) return;
    previewImg.src = data.image;
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
    goToPage(state.modifyPages.indexOf(state.modifyActivePage) - 1);
  });
  document.getElementById('page-next').addEventListener('click', () => {
    goToPage(state.modifyPages.indexOf(state.modifyActivePage) + 1);
  });

  updateModeToggleUI();
}
