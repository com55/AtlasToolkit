/**
 * script.js – AtlasToolkit Android / Web front-end
 *
 * All pywebview.api.* calls have been replaced with AtlasAPI.* calls from
 * js/atlas-api.js.  File I/O is handled via HTML5 File API and download links.
 */

import { AtlasAPI, _loadAtlasFiles } from './js/atlas-api.js';

// ─── Data State ───────────────────────────────────────────────────────────────
let regionsData = [];
let selectedIndices = new Set();
let lastClickIndex = -1;
let isDragSelecting = false;
let dragStartIndex = -1;
let currentMode = 'extract'; // 'extract' | 'modify'
let modifyRegionBounds = {};  // { name: [x, y, w, h, rotate] }
let hasModImage = false;
let viewState = { scale: 1, x: 0, y: 0, isDragging: false, startX: 0, startY: 0 };

// ─── Startup ──────────────────────────────────────────────────────────────────
window.addEventListener('DOMContentLoaded', async () => {
  const repackPref = AtlasAPI.get_pref('repack', false);
  document.getElementById('chk-repack').checked = repackPref;

  const loaded = await AtlasAPI.startup_check();
  if (loaded) await loadRegions();
});

// ─── Mode Switching ───────────────────────────────────────────────────────────
function setMode(mode) {
  currentMode = mode;
  const normalHeader   = document.getElementById('normal-header');
  const modifyHeader   = document.getElementById('modify-header');
  const extractControls = document.getElementById('extract-controls');
  const modifyControls  = document.getElementById('modify-controls');
  const repackOptions   = document.getElementById('repack-options');
  const dropMsg         = document.getElementById('drop-message-text');

  if (mode === 'modify') {
    normalHeader.classList.add('hidden');
    modifyHeader.classList.remove('hidden');
    extractControls.classList.add('hidden');
    modifyControls.classList.remove('hidden');
    repackOptions.classList.remove('hidden');
    dropMsg.textContent = 'Drop image to modify, or .atlas to load';
  } else {
    normalHeader.classList.remove('hidden');
    modifyHeader.classList.add('hidden');
    extractControls.classList.remove('hidden');
    modifyControls.classList.add('hidden');
    repackOptions.classList.add('hidden');
    dropMsg.textContent = 'Drop .atlas file here to load';
    clearOverlay();
  }
}

async function enterModifyMode() {
  try {
    const data = await AtlasAPI.enter_modify_mode();
    if (data) {
      setMode('modify');
      modifyRegionBounds = data.regions || {};
      hasModImage = false;
      document.getElementById('modify-status-text').innerText = 'Select regions and click Modify Selected';
      document.getElementById('btn-save-mod').disabled = true;
      previewImg.src = data.image;
      previewImg.style.display = 'block';
      previewImg.onload = function () {
        resetPreview();
        const containerW = previewContainer.clientWidth - 40;
        const containerH = previewContainer.clientHeight - 40;
        const imgW = previewImg.naturalWidth;
        const imgH = previewImg.naturalHeight;
        if (imgW > containerW || imgH > containerH) {
          viewState.scale = Math.min(containerW / imgW, containerH / imgH);
          applyTransform();
        }
        previewImg.onload = null;
      };
    } else {
      showToast('Load an atlas first.', 'error');
    }
  } catch (e) {
    console.error(e);
    showToast('Failed to enter modify mode.', 'error');
  }
}

async function exitModifyMode() {
  try { AtlasAPI.exit_modify_mode(); } catch (e) { console.error(e); }
  setMode('extract');
  modifyRegionBounds = {};
  hasModImage = false;
  clearOverlay();
  previewImg.style.display = 'none';
  resetPreview();
  document.getElementById('status-text').innerText = 'Ready';
  updatePreview(getSelectedNames());
}

async function modifySelected() {
  const names = getSelectedNames();
  if (names.length === 0) { showToast('Select at least one region to modify.', 'error'); return; }
  try {
    document.getElementById('modify-status-text').innerText = 'Selecting mod image...';
    const repack = document.getElementById('chk-repack').checked;
    const result = await AtlasAPI.select_mod_image(names, repack);
    if (result) {
      onModPreviewReceived(result);
    } else {
      document.getElementById('modify-status-text').innerText = 'Cancelled or no image selected.';
    }
  } catch (e) {
    console.error(e);
    showToast('Error selecting mod image.', 'error');
  }
}

function onModPreviewReceived(data) {
  hasModImage = true;
  if (data.regions) modifyRegionBounds = data.regions;
  previewImg.src = data.image;
  previewImg.style.display = 'block';
  document.getElementById('modify-status-text').innerText = 'Mod image merged. Ready to save.';
  document.getElementById('btn-save-mod').disabled = false;
  previewImg.onload = function () {
    resetPreview();
    const containerW = previewContainer.clientWidth - 40;
    const containerH = previewContainer.clientHeight - 40;
    const imgW = previewImg.naturalWidth;
    const imgH = previewImg.naturalHeight;
    document.getElementById('modify-status-text').innerText = `Merged preview (${imgW}x${imgH}). Ready to save.`;
    if (imgW > containerW || imgH > containerH) {
      viewState.scale = Math.min(containerW / imgW, containerH / imgH);
    }
    applyTransform();
    previewImg.onload = null;
  };
}

async function saveModified() {
  try {
    document.getElementById('modify-status-text').innerText = 'Saving...';
    const result = await AtlasAPI.save_modified();
    if (result.startsWith('Error') || result === 'Cancelled') {
      showToast(result, result === 'Cancelled' ? 'info' : 'error');
    } else {
      showToast(result, 'success');
    }
    document.getElementById('modify-status-text').innerText = result;
  } catch (e) {
    console.error(e);
    showToast('Save failed.', 'error');
  }
}

document.getElementById('chk-repack').addEventListener('change', async (e) => {
  AtlasAPI.set_pref('repack', e.target.checked);
  if (!hasModImage) return;
  const statusEl = document.getElementById('modify-status-text');
  statusEl.innerText = e.target.checked ? 'Applying repack...' : 'Reverting repack...';
  try {
    const result = await AtlasAPI.toggle_repack(e.target.checked);
    if (result) {
      onModPreviewReceived(result);
    } else {
      showToast('No merged data to repack.', 'error');
    }
  } catch (err) {
    console.error(err);
    showToast('Repack toggle failed.', 'error');
  }
});

// ─── Core Logic ───────────────────────────────────────────────────────────────
async function openFile() {
  try {
    const success = await AtlasAPI.choose_file();
    if (success) {
      selectedIndices.clear();
      lastClickIndex = -1;
      document.getElementById('preview-img').style.display = 'none';
      resetPreview();
      updateButtons();
      await loadRegions();
    }
  } catch (e) {
    console.error(e);
  }
}

async function loadRegions() {
  regionsData = AtlasAPI.get_region_names();
  if (!regionsData) return;
  const listEl = document.getElementById('region-list');
  listEl.innerHTML = '';
  document.getElementById('count').innerText = regionsData.length;
  regionsData.forEach((name, index) => {
    const li = document.createElement('li');
    li.className = 'region-item';
    li.innerText = name;
    li.dataset.index = index;
    li.addEventListener('mousedown', (e) => onRegionMouseDown(e, index, name));
    li.addEventListener('mouseenter', (e) => onRegionMouseEnter(e, index));
    listEl.appendChild(li);
  });
  if (regionsData.length > 0) {
    document.getElementById('status-text').innerText = 'Atlas loaded.';
    document.getElementById('btn-enter-modify').disabled = false;
    document.getElementById('btn-extract-all').disabled = false;
  } else {
    document.getElementById('btn-enter-modify').disabled = true;
    document.getElementById('btn-extract-all').disabled = true;
  }
}

function getSelectedNames() {
  return Array.from(selectedIndices).sort((a, b) => a - b).map(i => regionsData[i]);
}

// ─── Auto-Scroll State ────────────────────────────────────────────────────────
let autoScrollSpeed = 0;
let autoScrollInterval = null;
const SCROLL_ZONE_SIZE = 50;
const MAX_SCROLL_SPEED = 15;
let lastMouseX = 0;
let lastMouseY = 0;

function onRegionMouseDown(e, index, name) {
  if (e.button !== 0) return;
  if (e.shiftKey) e.preventDefault();
  isDragSelecting = true;
  dragStartIndex = index;
  if (e.ctrlKey || e.metaKey) {
    toggleIndex(index); lastClickIndex = index;
  } else if (e.shiftKey && lastClickIndex !== -1) {
    selectRange(Math.min(lastClickIndex, index), Math.max(lastClickIndex, index), false);
  } else {
    selectedIndices.clear(); selectedIndices.add(index); lastClickIndex = index;
  }
  renderSelection();
  triggerPreviewUpdate();
  window.addEventListener('mousemove', onWindowMouseMove);
  startAutoScroll();
}

function onWindowMouseMove(e) {
  if (!isDragSelecting) return;
  e.preventDefault();
  lastMouseX = e.clientX; lastMouseY = e.clientY;
  const container = document.getElementById('region-list-container');
  const rect = container.getBoundingClientRect();
  if (e.clientY < rect.top + SCROLL_ZONE_SIZE) {
    autoScrollSpeed = -((rect.top + SCROLL_ZONE_SIZE - e.clientY) / SCROLL_ZONE_SIZE) * MAX_SCROLL_SPEED;
  } else if (e.clientY > rect.bottom - SCROLL_ZONE_SIZE) {
    autoScrollSpeed = ((e.clientY - (rect.bottom - SCROLL_ZONE_SIZE)) / SCROLL_ZONE_SIZE) * MAX_SCROLL_SPEED;
  } else {
    autoScrollSpeed = 0;
  }
  updateSelectionFromMouse(e.clientX, e.clientY);
}

function updateSelectionFromMouse(clientX, clientY) {
  const container = document.getElementById('region-list-container');
  const rect = container.getBoundingClientRect();
  const checkY = Math.max(rect.top + 1, Math.min(clientY, rect.bottom - 1));
  const el = document.elementFromPoint(rect.left + rect.width / 2, checkY);
  const item = el?.closest('.region-item');
  if (item) {
    const index = parseInt(item.dataset.index);
    if (!isNaN(index)) {
      const start = Math.min(dragStartIndex, index);
      const end = Math.max(dragStartIndex, index);
      selectedIndices.clear();
      for (let i = start; i <= end; i++) selectedIndices.add(i);
      lastClickIndex = index;
      renderSelection();
      triggerPreviewUpdate();
    }
  }
}

// ─── Preview Debounce ─────────────────────────────────────────────────────────
let previewTimeout = null;
let lastSelectedJSON = '[]';

function triggerPreviewUpdate() {
  const currentJSON = JSON.stringify(getSelectedNames());
  if (currentJSON !== lastSelectedJSON) {
    lastSelectedJSON = currentJSON;
    if (previewTimeout) clearTimeout(previewTimeout);
    previewTimeout = setTimeout(() => {
      const names = getSelectedNames();
      if (currentMode === 'modify') updateModifyPreview(names);
      else updatePreview(names);
    }, 50);
    updateButtons();
  }
}

function updateModifyPreview(names) {
  drawRegionOverlay();
  if (!names || names.length === 0) {
    document.getElementById('modify-status-text').innerText = hasModImage
      ? 'Mod image merged. Ready to save.'
      : 'Select regions and click Modify Selected';
  } else {
    document.getElementById('modify-status-text').innerText = hasModImage
      ? `Merged preview. ${names.length} region(s) selected.`
      : `${names.length} region(s) selected`;
  }
}

function startAutoScroll() {
  if (autoScrollInterval) return;
  function scrollLoop() {
    if (!isDragSelecting) { stopAutoScroll(); return; }
    if (autoScrollSpeed !== 0) {
      document.getElementById('region-list-container').scrollTop += autoScrollSpeed;
      updateSelectionFromMouse(lastMouseX, lastMouseY);
    }
    triggerPreviewUpdate();
    autoScrollInterval = requestAnimationFrame(scrollLoop);
  }
  autoScrollInterval = requestAnimationFrame(scrollLoop);
}

function stopAutoScroll() {
  if (autoScrollInterval) { cancelAnimationFrame(autoScrollInterval); autoScrollInterval = null; }
  autoScrollSpeed = 0;
}

function onRegionMouseEnter(e, index) { /* handled by global mousemove */ }

window.addEventListener('mouseup', () => {
  if (isDragSelecting) {
    isDragSelecting = false;
    stopAutoScroll();
    window.removeEventListener('mousemove', onWindowMouseMove);
    if (currentMode === 'extract') updatePreview(getSelectedNames());
    else updateModifyPreview(getSelectedNames());
    updateButtons();
  }
  viewState.isDragging = false;
});

function toggleIndex(index) {
  if (selectedIndices.has(index)) selectedIndices.delete(index);
  else selectedIndices.add(index);
}

function selectRange(start, end, keepExisting) {
  if (!keepExisting) selectedIndices.clear();
  for (let i = start; i <= end; i++) selectedIndices.add(i);
}

function renderSelection() {
  document.querySelectorAll('.region-item').forEach((el, idx) => {
    el.classList.toggle('selected', selectedIndices.has(idx));
  });
}

const previewContainer = document.getElementById('preview-container');
const previewImg = document.getElementById('preview-img');

function resetPreview() {
  viewState = { scale: 1, x: 0, y: 0, isDragging: false, startX: 0, startY: 0 };
  applyTransform();
}

function applyTransform() {
  previewImg.style.transform = `translate(calc(-50% + ${viewState.x}px), calc(-50% + ${viewState.y}px)) scale(${viewState.scale})`;
  if (currentMode === 'modify') drawRegionOverlay();
}

// ─── Region Overlay ───────────────────────────────────────────────────────────
function drawRegionOverlay() {
  const canvas = document.getElementById('region-overlay');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const containerW = previewContainer.clientWidth;
  const containerH = previewContainer.clientHeight;
  canvas.width = containerW * dpr; canvas.height = containerH * dpr;
  canvas.style.width = containerW + 'px'; canvas.style.height = containerH + 'px';
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, containerW, containerH);
  if (currentMode !== 'modify' || selectedIndices.size === 0) return;

  const imgW = previewImg.naturalWidth, imgH = previewImg.naturalHeight;
  if (!imgW || !imgH) return;

  const scale = viewState.scale;
  const centerX = containerW / 2 + viewState.x;
  const centerY = containerH / 2 + viewState.y;
  const topLeftX = centerX - imgW * scale / 2;
  const topLeftY = centerY - imgH * scale / 2;
  const lineWidth = 3;

  for (const name of getSelectedNames()) {
    const bounds = modifyRegionBounds[name];
    if (!bounds) continue;
    const [bx, by, bw, bh, rotate] = bounds;
    const isRotated = rotate === 90 || rotate === 270;
    const drawW = isRotated ? bh : bw;
    const drawH = isRotated ? bw : bh;
    const rx = topLeftX + bx * scale;
    const ry = topLeftY + by * scale;
    const rw = drawW * scale;
    const rh = drawH * scale;

    ctx.strokeStyle = 'rgba(255, 60, 60, 0.85)';
    ctx.lineWidth = lineWidth;
    ctx.strokeRect(rx - lineWidth / 2, ry - lineWidth / 2, rw + lineWidth, rh + lineWidth);

    const fontSize = 13;
    ctx.font = `bold ${fontSize}px "Segoe UI", sans-serif`;
    const textW = ctx.measureText(name).width;
    const labelX = rx;
    const labelY = ry - lineWidth - 2;
    ctx.fillStyle = 'rgba(255, 60, 60, 0.85)';
    ctx.fillRect(labelX - 1, labelY - fontSize, textW + 8, fontSize + 4);
    ctx.fillStyle = 'white';
    ctx.fillText(name, labelX + 3, labelY - 1);
  }
}

function clearOverlay() {
  const canvas = document.getElementById('region-overlay');
  if (canvas) canvas.getContext('2d').clearRect(0, 0, canvas.width, canvas.height);
}

async function updatePreview(names) {
  const status = document.getElementById('status-text');
  if (!names || names.length === 0) {
    previewImg.style.display = 'none'; status.innerText = 'No selection'; return;
  }
  const base64Img = AtlasAPI.get_preview ? await AtlasAPI.get_preview(names) : null;
  if (base64Img) {
    previewImg.src = base64Img;
    previewImg.style.display = 'block';
    status.innerText = names.length === 1 ? `Previewing: ${names[0]}` : `Previewing: ${names.length} regions`;
    previewImg.onload = function () {
      resetPreview();
      const containerW = previewContainer.clientWidth - 40;
      const containerH = previewContainer.clientHeight - 40;
      const imgW = previewImg.naturalWidth, imgH = previewImg.naturalHeight;
      status.innerText = names.length === 1
        ? `Previewing: ${names[0]} (${imgW}x${imgH})`
        : `Previewing: ${names.length} regions (${imgW}x${imgH})`;
      if (imgW > containerW || imgH > containerH) {
        viewState.scale = Math.min(containerW / imgW, containerH / imgH);
        applyTransform();
      }
      previewImg.onload = null;
    };
  } else {
    previewImg.style.display = 'none'; status.innerText = 'Preview failed';
  }
}

// ─── Pan & Zoom ───────────────────────────────────────────────────────────────
previewContainer.addEventListener('wheel', (e) => {
  e.preventDefault();
  const direction = -Math.sign(e.deltaY);
  const newScale = viewState.scale + direction * 0.1 * viewState.scale;
  if (newScale > 0.1 && newScale < 50) {
    const rect = previewContainer.getBoundingClientRect();
    const cx = rect.width / 2, cy = rect.height / 2;
    const mx = e.clientX - rect.left - cx, my = e.clientY - rect.top - cy;
    viewState.x = mx - (mx - viewState.x) * (newScale / viewState.scale);
    viewState.y = my - (my - viewState.y) * (newScale / viewState.scale);
    viewState.scale = newScale;
    applyTransform();
  }
});

previewContainer.addEventListener('mousedown', (e) => {
  if (e.button !== 0 && e.button !== 1) return;
  e.preventDefault();
  viewState.isDragging = true;
  viewState.startX = e.clientX - viewState.x;
  viewState.startY = e.clientY - viewState.y;
});

window.addEventListener('mousemove', (e) => {
  if (viewState.isDragging) {
    viewState.x = e.clientX - viewState.startX;
    viewState.y = e.clientY - viewState.startY;
    applyTransform();
  }
});

// ─── Touch pan & pinch-to-zoom (Android) ─────────────────────────────────────
let _lastTouchDist = null;
let _lastTouchMid = null;

previewContainer.addEventListener('touchstart', (e) => {
  if (e.touches.length === 2) {
    _lastTouchDist = Math.hypot(
      e.touches[1].clientX - e.touches[0].clientX,
      e.touches[1].clientY - e.touches[0].clientY
    );
    _lastTouchMid = {
      x: (e.touches[0].clientX + e.touches[1].clientX) / 2,
      y: (e.touches[0].clientY + e.touches[1].clientY) / 2,
    };
  } else if (e.touches.length === 1) {
    viewState.isDragging = true;
    viewState.startX = e.touches[0].clientX - viewState.x;
    viewState.startY = e.touches[0].clientY - viewState.y;
  }
}, { passive: true });

previewContainer.addEventListener('touchmove', (e) => {
  if (e.touches.length === 2) {
    const dist = Math.hypot(
      e.touches[1].clientX - e.touches[0].clientX,
      e.touches[1].clientY - e.touches[0].clientY
    );
    if (_lastTouchDist) {
      const factor = dist / _lastTouchDist;
      const newScale = Math.min(50, Math.max(0.1, viewState.scale * factor));
      const rect = previewContainer.getBoundingClientRect();
      const mid = { x: (e.touches[0].clientX + e.touches[1].clientX) / 2, y: (e.touches[0].clientY + e.touches[1].clientY) / 2 };
      const cx = rect.width / 2, cy = rect.height / 2;
      const mx = mid.x - rect.left - cx, my = mid.y - rect.top - cy;
      viewState.x = mx - (mx - viewState.x) * (newScale / viewState.scale);
      viewState.y = my - (my - viewState.y) * (newScale / viewState.scale);
      viewState.scale = newScale;
      applyTransform();
    }
    _lastTouchDist = dist;
  } else if (e.touches.length === 1 && viewState.isDragging) {
    viewState.x = e.touches[0].clientX - viewState.startX;
    viewState.y = e.touches[0].clientY - viewState.startY;
    applyTransform();
  }
}, { passive: true });

previewContainer.addEventListener('touchend', () => {
  _lastTouchDist = null; _lastTouchMid = null; viewState.isDragging = false;
}, { passive: true });

// ─── Buttons ──────────────────────────────────────────────────────────────────
function updateButtons() {
  const btnSel = document.getElementById('btn-extract-sel');
  btnSel.disabled = selectedIndices.size === 0;
  btnSel.innerText = `Extract Selected (${selectedIndices.size})`;
  const btnModSel = document.getElementById('btn-modify-sel');
  if (btnModSel) {
    btnModSel.disabled = selectedIndices.size === 0;
    btnModSel.innerText = `Modify Selected (${selectedIndices.size})`;
  }
}

async function extractSelected() {
  if (selectedIndices.size === 0) return;
  const names = Array.from(selectedIndices).map(i => regionsData[i]);
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

// ─── Modal ────────────────────────────────────────────────────────────────────
function showConfirm(message, title = 'Confirm') {
  return new Promise((resolve) => {
    const overlay = document.getElementById('modal-overlay');
    document.getElementById('modal-title').innerText = title;
    document.getElementById('modal-message').innerText = message;
    overlay.classList.remove('hidden');
    const btnConfirm = document.getElementById('btn-modal-confirm');
    const btnCancel = document.getElementById('btn-modal-cancel');
    if (document.activeElement) document.activeElement.blur();
    btnConfirm.focus();
    function cleanup() {
      overlay.classList.add('hidden');
      btnConfirm.removeEventListener('click', onConfirm);
      btnCancel.removeEventListener('click', onCancel);
      window.removeEventListener('keydown', onKey);
    }
    function onConfirm() { cleanup(); resolve(true); }
    function onCancel() { cleanup(); resolve(false); }
    function onKey(e) { if (e.key === 'Escape') onCancel(); }
    btnConfirm.addEventListener('click', onConfirm);
    btnCancel.addEventListener('click', onCancel);
    window.addEventListener('keydown', onKey);
  });
}

// ─── Toast ────────────────────────────────────────────────────────────────────
window.showToast = function showToast(message, type = 'info') {
  const container = document.getElementById('toast-container');
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.innerText = message;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.animation = 'none';
    toast.offsetHeight;
    toast.style.animation = 'fadeOut 0.5s ease-out forwards';
    toast.addEventListener('animationend', () => toast.remove());
  }, 3000);
};

// ─── Keyboard Navigation ──────────────────────────────────────────────────────
window.addEventListener('keydown', (e) => {
  if (regionsData.length === 0) return;
  if (e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return;
  e.preventDefault();
  let newIndex = lastClickIndex;
  if (e.key === 'ArrowDown') newIndex = Math.min(newIndex + 1, regionsData.length - 1);
  else newIndex = Math.max(newIndex - 1, 0);
  if (newIndex === lastClickIndex && selectedIndices.size > 0) return;
  if (e.shiftKey) {
    if (dragStartIndex === -1) dragStartIndex = lastClickIndex;
    selectedIndices.clear();
    const start = Math.min(dragStartIndex, newIndex), end = Math.max(dragStartIndex, newIndex);
    for (let i = start; i <= end; i++) selectedIndices.add(i);
  } else {
    selectedIndices.clear(); selectedIndices.add(newIndex); dragStartIndex = newIndex;
  }
  lastClickIndex = newIndex;
  renderSelection();
  if (currentMode === 'extract') updatePreview(getSelectedNames());
  else updateModifyPreview(getSelectedNames());
  updateButtons();
  const item = document.querySelector(`.region-item[data-index="${newIndex}"]`);
  if (item) item.scrollIntoView({ block: 'nearest' });
});

// ─── Drag and Drop ────────────────────────────────────────────────────────────
const dropOverlay = document.getElementById('drop-overlay');

['dragover', 'drop'].forEach(ev => window.addEventListener(ev, e => e.preventDefault(), false));

window.addEventListener('dragenter', (e) => {
  e.preventDefault();
  if (e.dataTransfer.types.includes('Files')) {
    dropOverlay.classList.remove('hidden');
    dropOverlay.style.pointerEvents = 'auto';
  }
});

dropOverlay.addEventListener('dragover', e => e.preventDefault());

dropOverlay.addEventListener('dragleave', (e) => {
  e.preventDefault();
  if (e.relatedTarget === null || !dropOverlay.contains(e.relatedTarget)) {
    dropOverlay.classList.add('hidden');
    dropOverlay.style.pointerEvents = 'none';
  }
});

dropOverlay.addEventListener('drop', async (e) => {
  e.preventDefault();
  dropOverlay.classList.add('hidden');
  dropOverlay.style.pointerEvents = 'none';

  const files = Array.from(e.dataTransfer.files);
  if (files.length === 0) return;

  const atlasFile = files.find(f => f.name.endsWith('.atlas'));
  const imageExtensions = /\.(png|jpg|jpeg|webp|bmp|gif)$/i;
  const imageFiles = {};
  for (const f of files) if (imageExtensions.test(f.name)) imageFiles[f.name] = f;

  if (atlasFile) {
    const loaded = await _loadAtlasFiles(atlasFile, imageFiles);
    if (loaded) {
      if (currentMode === 'modify') { setMode('extract'); modifyRegionBounds = {}; hasModImage = false; }
      selectedIndices.clear(); lastClickIndex = -1;
      document.getElementById('preview-img').style.display = 'none';
      resetPreview(); clearOverlay(); updateButtons();
      await loadRegions();
      showToast('Atlas loaded via drag & drop.', 'success');
    }
  } else {
    // Image dropped in modify mode
    const imgFile = files.find(f => imageExtensions.test(f.name));
    if (imgFile && currentMode === 'modify') {
      const names = getSelectedNames();
      if (names.length === 0) { showToast('Select at least one region first.', 'error'); return; }
      const repack = document.getElementById('chk-repack').checked;
      const result = await AtlasAPI.process_mod_image(imgFile, names, repack);
      if (result) {
        onModPreviewReceived(result);
        showToast('Mod image loaded via drag & drop.', 'success');
      }
    } else {
      showToast('Enter Modify Mode first to drop images.', 'error');
    }
  }
});

// ─── Context Menu ─────────────────────────────────────────────────────────────
const contextMenu = document.getElementById('context-menu');

previewContainer.addEventListener('contextmenu', (e) => {
  e.preventDefault();
  if (currentMode !== 'extract') return;
  if (previewImg.style.display === 'none' || !previewImg.src) return;
  contextMenu.style.left = e.clientX + 'px';
  contextMenu.style.top = e.clientY + 'px';
  contextMenu.classList.remove('hidden');
});

window.addEventListener('click', () => contextMenu.classList.add('hidden'));
window.addEventListener('keydown', (e) => { if (e.key === 'Escape') contextMenu.classList.add('hidden'); });

async function copyPreviewImage() {
  contextMenu.classList.add('hidden');
  try {
    const img = previewImg;
    if (!img.naturalWidth) { showToast('No image to copy.', 'error'); return; }
    const canvas = document.createElement('canvas');
    canvas.width = img.naturalWidth; canvas.height = img.naturalHeight;
    canvas.getContext('2d').drawImage(img, 0, 0);
    const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'));
    if (!blob) { showToast('Failed to copy image.', 'error'); return; }
    await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })]);
    showToast('Image copied to clipboard.', 'success');
  } catch (e) {
    console.error(e);
    showToast('Failed to copy image.', 'error');
  }
}

// ─── URL helper (used internally) ────────────────────────────────────────────
async function openExternalUrl(url) {
  try {
    const parsed = new URL(String(url));
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
      showToast('Invalid URL.', 'error'); return;
    }
    const result = await AtlasAPI.open_url(parsed.toString());
    if (!result || !result.ok) showToast((result && result.error) || 'Failed to open URL.', 'error');
  } catch (err) {
    console.error(err);
    showToast('Invalid URL.', 'error');
  }
}

// ─── Expose globals for inline onclick handlers in index.html ─────────────────
window.openFile = openFile;
window.enterModifyMode = enterModifyMode;
window.exitModifyMode = exitModifyMode;
window.modifySelected = modifySelected;
window.saveModified = saveModified;
window.extractSelected = extractSelected;
window.extractAll = extractAll;
window.copyPreviewImage = copyPreviewImage;
window.showToast = window.showToast; // already set above
