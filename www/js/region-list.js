import { AtlasAPI } from './atlas-api.js';
import { state, getSelectedNames } from './state.js';
import { updatePreview, updateModifyPreview } from './preview.js';

// ─── Auto-Scroll State ────────────────────────────────────────────────────────
let autoScrollSpeed    = 0;
let autoScrollInterval = null;
const SCROLL_ZONE_SIZE = 50;
const MAX_SCROLL_SPEED = 15;
let lastMouseX = 0;
let lastMouseY = 0;

// Mobile: tap toggles, long-press+drag selects range
const TOUCH_TAP_MOVE_PX  = 10;
const TOUCH_LONG_PRESS_MS = 220;
let touchSelectActive     = false;
let touchRangeMode        = false;
let touchStartIndex       = -1;
let touchStartX           = 0;
let touchStartY           = 0;
let touchMovedBeyondTap   = false;
let touchLastRangeIndex   = -1;
let touchLongPressTimer   = null;
let suppressMouseDownUntil = 0;

// ─── Preview Debounce ─────────────────────────────────────────────────────────
let previewTimeout   = null;
let lastSelectedJSON = '[]';

export async function loadRegions() {
  state.regionsData = AtlasAPI.get_region_names();
  if (!state.regionsData) return;
  const listEl = document.getElementById('region-list');
  listEl.innerHTML = '';
  document.getElementById('count').innerText = state.regionsData.length;
  state.regionsData.forEach((name, index) => {
    const li = document.createElement('li');
    li.className    = 'region-item';
    li.innerText    = name;
    li.dataset.index = index;
    li.addEventListener('mousedown', (e) => onRegionMouseDown(e, index, name));
    li.addEventListener('mouseenter', () => { /* handled by global mousemove */ });
    li.addEventListener('touchstart', (e) => onRegionTouchStart(e, index), { passive: false });
    listEl.appendChild(li);
  });
  const hasRegions = state.regionsData.length > 0;
  document.getElementById('status-text').innerText = hasRegions ? 'Atlas loaded.' : '';
  document.getElementById('btn-enter-modify').disabled  = !hasRegions;
  document.getElementById('btn-extract-all').disabled   = !hasRegions;
}

export function renderSelection() {
  document.querySelectorAll('.region-item').forEach((el, idx) => {
    el.classList.toggle('selected', state.selectedIndices.has(idx));
  });
}

export function updateButtons() {
  const btnSel = document.getElementById('btn-extract-sel');
  btnSel.disabled  = state.selectedIndices.size === 0;
  btnSel.innerText = `Extract Selected (${state.selectedIndices.size})`;
  const btnModSel = document.getElementById('btn-modify-sel');
  if (btnModSel) {
    btnModSel.disabled  = state.selectedIndices.size === 0;
    btnModSel.innerText = `Replace (${state.selectedIndices.size})`;
  }
}

export function triggerPreviewUpdate() {
  const currentJSON = JSON.stringify(getSelectedNames());
  if (currentJSON !== lastSelectedJSON) {
    lastSelectedJSON = currentJSON;
    if (previewTimeout) clearTimeout(previewTimeout);
    previewTimeout = setTimeout(() => {
      const names = getSelectedNames();
      if (state.currentMode === 'modify') updateModifyPreview(names);
      else updatePreview(names);
    }, 50);
    updateButtons();
  }
}

// ─── Selection Helpers ────────────────────────────────────────────────────────
function toggleIndex(index) {
  if (state.selectedIndices.has(index)) state.selectedIndices.delete(index);
  else state.selectedIndices.add(index);
}

function selectRange(start, end, keepExisting) {
  if (!keepExisting) state.selectedIndices.clear();
  for (let i = start; i <= end; i++) state.selectedIndices.add(i);
}

// ─── Mouse Selection ──────────────────────────────────────────────────────────
function onRegionMouseDown(e, index) {
  if (Date.now() < suppressMouseDownUntil) return;
  if (e.button !== 0) return;
  if (e.shiftKey) e.preventDefault();
  state.isDragSelecting = true;
  state.dragStartIndex  = index;
  if (e.ctrlKey || e.metaKey) {
    toggleIndex(index);
    state.lastClickIndex = index;
  } else if (e.shiftKey && state.lastClickIndex !== -1) {
    selectRange(Math.min(state.lastClickIndex, index), Math.max(state.lastClickIndex, index), false);
  } else {
    state.selectedIndices.clear();
    state.selectedIndices.add(index);
    state.lastClickIndex = index;
  }
  renderSelection();
  triggerPreviewUpdate();
  window.addEventListener('mousemove', onWindowMouseMove);
  startAutoScroll();
}

function onWindowMouseMove(e) {
  if (!state.isDragSelecting) return;
  e.preventDefault();
  lastMouseX = e.clientX;
  lastMouseY = e.clientY;
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
  const rect  = container.getBoundingClientRect();
  const checkY = Math.max(rect.top + 1, Math.min(clientY, rect.bottom - 1));
  const el    = document.elementFromPoint(rect.left + rect.width / 2, checkY);
  const item  = el?.closest('.region-item');
  if (!item) return;
  const index = parseInt(item.dataset.index);
  if (!isNaN(index)) {
    const start = Math.min(state.dragStartIndex, index);
    const end   = Math.max(state.dragStartIndex, index);
    state.selectedIndices.clear();
    for (let i = start; i <= end; i++) state.selectedIndices.add(i);
    state.lastClickIndex = index;
    renderSelection();
    triggerPreviewUpdate();
  }
}

function startAutoScroll() {
  if (autoScrollInterval) return;
  function scrollLoop() {
    if (!state.isDragSelecting) { stopAutoScroll(); return; }
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

window.addEventListener('mouseup', () => {
  if (state.isDragSelecting) {
    state.isDragSelecting = false;
    stopAutoScroll();
    window.removeEventListener('mousemove', onWindowMouseMove);
    if (state.currentMode === 'extract') updatePreview(getSelectedNames());
    else updateModifyPreview(getSelectedNames());
    updateButtons();
  }
  state.viewState.isDragging = false;
});

// ─── Touch Selection ──────────────────────────────────────────────────────────
function clearTouchLongPressTimer() {
  if (touchLongPressTimer) { clearTimeout(touchLongPressTimer); touchLongPressTimer = null; }
}

function findRegionIndexAtPoint(clientX, clientY) {
  const el   = document.elementFromPoint(clientX, clientY);
  const item = el?.closest('.region-item');
  if (!item) return null;
  const index = parseInt(item.dataset.index);
  return Number.isNaN(index) ? null : index;
}

function onRegionTouchStart(e, index) {
  if (!e.touches || e.touches.length !== 1) return;
  suppressMouseDownUntil = Date.now() + 700;
  const t = e.touches[0];
  touchSelectActive   = true;
  touchRangeMode      = false;
  touchStartIndex     = index;
  touchStartX         = t.clientX;
  touchStartY         = t.clientY;
  touchMovedBeyondTap = false;
  touchLastRangeIndex = -1;

  clearTouchLongPressTimer();
  touchLongPressTimer = setTimeout(() => {
    touchRangeMode = true;
    state.selectedIndices.clear();
    state.selectedIndices.add(touchStartIndex);
    state.lastClickIndex = touchStartIndex;
    touchLastRangeIndex  = touchStartIndex;
    renderSelection();
    triggerPreviewUpdate();
  }, TOUCH_LONG_PRESS_MS);

  window.addEventListener('touchmove',   onWindowTouchMove,   { passive: false });
  window.addEventListener('touchend',    onWindowTouchEnd,    { passive: false });
  window.addEventListener('touchcancel', onWindowTouchCancel, { passive: false });
}

function onWindowTouchMove(e) {
  if (!touchSelectActive || !e.touches || e.touches.length !== 1) return;
  const t  = e.touches[0];
  const dx = Math.abs(t.clientX - touchStartX);
  const dy = Math.abs(t.clientY - touchStartY);
  if (!touchRangeMode && (dx > TOUCH_TAP_MOVE_PX || dy > TOUCH_TAP_MOVE_PX)) {
    touchMovedBeyondTap = true;
    clearTouchLongPressTimer();
  }
  if (!touchRangeMode) return;
  e.preventDefault();
  const idx = findRegionIndexAtPoint(t.clientX, t.clientY);
  if (idx !== null && idx !== touchLastRangeIndex) {
    state.selectedIndices.clear();
    const start = Math.min(touchStartIndex, idx);
    const end   = Math.max(touchStartIndex, idx);
    for (let i = start; i <= end; i++) state.selectedIndices.add(i);
    state.lastClickIndex = idx;
    touchLastRangeIndex  = idx;
    renderSelection();
    triggerPreviewUpdate();
  }
  const container = document.getElementById('region-list-container');
  const rect = container.getBoundingClientRect();
  if (t.clientY < rect.top    + SCROLL_ZONE_SIZE) container.scrollTop -= 12;
  if (t.clientY > rect.bottom - SCROLL_ZONE_SIZE) container.scrollTop += 12;
}

function endTouchSelection() {
  clearTouchLongPressTimer();
  touchSelectActive   = false;
  touchRangeMode      = false;
  touchStartIndex     = -1;
  touchMovedBeyondTap = false;
  touchLastRangeIndex = -1;
  window.removeEventListener('touchmove',   onWindowTouchMove);
  window.removeEventListener('touchend',    onWindowTouchEnd);
  window.removeEventListener('touchcancel', onWindowTouchCancel);
}

function onWindowTouchEnd() {
  if (!touchSelectActive) return;
  if (!touchRangeMode && touchStartIndex !== -1 && !touchMovedBeyondTap) {
    toggleIndex(touchStartIndex);
    state.lastClickIndex = touchStartIndex;
    renderSelection();
    triggerPreviewUpdate();
  }
  endTouchSelection();
}

function onWindowTouchCancel() { endTouchSelection(); }

// ─── Keyboard Navigation ──────────────────────────────────────────────────────
window.addEventListener('keydown', (e) => {
  if (state.regionsData.length === 0) return;
  if (e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return;
  e.preventDefault();
  let newIndex = state.lastClickIndex;
  if (e.key === 'ArrowDown') newIndex = Math.min(newIndex + 1, state.regionsData.length - 1);
  else                       newIndex = Math.max(newIndex - 1, 0);
  if (newIndex === state.lastClickIndex && state.selectedIndices.size > 0) return;
  if (e.shiftKey) {
    if (state.dragStartIndex === -1) state.dragStartIndex = state.lastClickIndex;
    state.selectedIndices.clear();
    const start = Math.min(state.dragStartIndex, newIndex);
    const end   = Math.max(state.dragStartIndex, newIndex);
    for (let i = start; i <= end; i++) state.selectedIndices.add(i);
  } else {
    state.selectedIndices.clear();
    state.selectedIndices.add(newIndex);
    state.dragStartIndex = newIndex;
  }
  state.lastClickIndex = newIndex;
  renderSelection();
  if (state.currentMode === 'extract') updatePreview(getSelectedNames());
  else updateModifyPreview(getSelectedNames());
  updateButtons();
  document.querySelector(`.region-item[data-index="${newIndex}"]`)?.scrollIntoView({ block: 'nearest' });
});
