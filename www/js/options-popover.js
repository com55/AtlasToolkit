/**
 * options-popover.js
 * Help popover for #options-row toggles (hover delay / long-press) and the
 * Smart Packing gap panel. Both overlays live on <body>, never inside
 * #options-row (overflow-y:hidden would clip them).
 */

import { isTouchDevice, isPywebviewDesktop } from './platform.js';

const HOVER_DELAY_MS = 500;
const LONG_PRESS_MS = 450;

const helpPopover = () => document.getElementById('opt-help-popover');
const gapOverlay = () => document.getElementById('nest-gap-overlay');
const gapPanel = () => document.getElementById('nest-gap-panel');
const gearBtn = () => document.getElementById('btn-nest-settings');

let hoverTimer = null;
let longPressTimer = null;
let swallowNextClick = false;
let helpTarget = null;
let sliderStart = null;
let applyGapCb = null;
let revertGapCb = null;

function useLongPress() {
  return isTouchDevice() && !isPywebviewDesktop();
}

export function isGapPanelOpen() {
  const overlay = gapOverlay();
  return !!(overlay && !overlay.classList.contains('hidden'));
}

export function syncHelpPopover() {
  if (helpTarget) showHelpFor(helpTarget);
}

export function hideHelpPopover() {
  if (hoverTimer) { clearTimeout(hoverTimer); hoverTimer = null; }
  if (longPressTimer) { clearTimeout(longPressTimer); longPressTimer = null; }
  const pop = helpPopover();
  if (pop) pop.classList.add('hidden');
  if (helpTarget) helpTarget.removeAttribute('aria-describedby');
  helpTarget = null;
}

function showHelpFor(el) {
  const text = el.getAttribute('data-help');
  if (!text) return;
  const pop = helpPopover();
  pop.textContent = text;
  pop.classList.remove('hidden');
  helpTarget = el;
  el.setAttribute('aria-describedby', 'opt-help-popover');
  const rect = el.getBoundingClientRect();
  const pad = 8;
  let left = rect.left;
  let top = rect.bottom + 6;
  const popRect = pop.getBoundingClientRect();
  if (left + popRect.width > window.innerWidth - pad) {
    left = Math.max(pad, window.innerWidth - pad - popRect.width);
  }
  if (top + popRect.height > window.innerHeight - pad) {
    top = Math.max(pad, rect.top - popRect.height - 6);
  }
  pop.style.left = `${Math.max(pad, left)}px`;
  pop.style.top = `${top}px`;
}

function positionGapPanel(anchor) {
  const panel = gapPanel();
  const rect = anchor.getBoundingClientRect();
  const pad = 8;
  panel.style.left = `${rect.left}px`;
  panel.style.top = `${rect.bottom + 6}px`;
  const popRect = panel.getBoundingClientRect();
  let left = rect.left;
  let top = rect.bottom + 6;
  if (left + popRect.width > window.innerWidth - pad) {
    left = Math.max(pad, window.innerWidth - pad - popRect.width);
  }
  if (top + popRect.height > window.innerHeight - pad) {
    top = Math.max(pad, rect.top - popRect.height - 6);
  }
  panel.style.left = `${left}px`;
  panel.style.top = `${top}px`;
}

export function closeGapPanel({ revert = true } = {}) {
  const overlay = gapOverlay();
  const btn = gearBtn();
  const active = document.activeElement;
  if (overlay.contains(active)) active.blur();
  overlay.classList.add('hidden');
  overlay.classList.remove('is-touch-modal');
  if (btn) btn.setAttribute('aria-expanded', 'false');
  if (revert) {
    // Number inputs restore their pre-focus value on Escape after this
    // keydown handler returns -- revert on the next task so that native
    // reset cannot clobber the applied value.
    queueMicrotask(() => revertGapCb?.());
  }
}

export function openGapPanel(e) {
  if (e) e.stopPropagation();
  hideHelpPopover();
  const btn = gearBtn();
  if (btn?.disabled) return;
  const overlay = gapOverlay();
  if (!overlay.classList.contains('hidden')) {
    closeGapPanel();
    return;
  }
  const number = document.getElementById('nest-gap-distance');
  const slider = document.getElementById('nest-gap-slider');
  const n = Number(number?.value);
  if (slider && Number.isFinite(n) && n >= 1) slider.value = String(Math.min(100, n));
  overlay.classList.remove('hidden');
  const touch = useLongPress();
  overlay.classList.toggle('is-touch-modal', touch);
  btn.setAttribute('aria-expanded', 'true');
  if (!touch) positionGapPanel(btn);
}

function toggleLabels() {
  return document.querySelectorAll('#options-row .toggle-label');
}

export function initOptionsPopover({ applyGap, revertGap } = {}) {
  applyGapCb = applyGap || null;
  revertGapCb = revertGap || null;
  const pop = helpPopover();
  const overlay = gapOverlay();
  const btn = gearBtn();
  const slider = document.getElementById('nest-gap-slider');
  const number = document.getElementById('nest-gap-distance');
  const closeBtn = document.getElementById('nest-gap-close');
  const confirmBtn = document.getElementById('nest-gap-confirm');
  if (!pop || !overlay || !btn || !slider || !number) return;

  for (const label of toggleLabels()) {
    label.addEventListener('mouseenter', () => {
      if (useLongPress()) return;
      if (hoverTimer) clearTimeout(hoverTimer);
      hoverTimer = setTimeout(() => showHelpFor(label), HOVER_DELAY_MS);
    });
    label.addEventListener('mouseleave', () => {
      if (useLongPress()) return;
      if (hoverTimer) { clearTimeout(hoverTimer); hoverTimer = null; }
      hideHelpPopover();
    });
    label.addEventListener('touchstart', (ev) => {
      if (!useLongPress() || ev.touches.length !== 1) return;
      if (longPressTimer) clearTimeout(longPressTimer);
      longPressTimer = setTimeout(() => {
        swallowNextClick = true;
        showHelpFor(label);
      }, LONG_PRESS_MS);
    }, { passive: true });
    label.addEventListener('touchend', () => {
      if (longPressTimer) { clearTimeout(longPressTimer); longPressTimer = null; }
    });
    label.addEventListener('touchcancel', () => {
      if (longPressTimer) { clearTimeout(longPressTimer); longPressTimer = null; }
    });
    label.addEventListener('click', (ev) => {
      if (!swallowNextClick) return;
      swallowNextClick = false;
      ev.preventDefault();
      ev.stopPropagation();
    }, true);
  }

  document.addEventListener('click', () => {
    if (!useLongPress()) return;
    hideHelpPopover();
  });

  btn.addEventListener('click', openGapPanel);
  closeBtn?.addEventListener('click', (e) => { e.stopPropagation(); closeGapPanel(); });
  confirmBtn?.addEventListener('click', async (e) => {
    e.stopPropagation();
    if (!applyGapCb) return;
    confirmBtn.disabled = true;
    try {
      const ok = await applyGapCb(number.value);
      if (ok) closeGapPanel({ revert: false });
    } finally {
      confirmBtn.disabled = false;
    }
  });
  overlay.addEventListener('click', (e) => {
    if (e.target === overlay) closeGapPanel();
  });
  document.addEventListener('click', (e) => {
    if (overlay.classList.contains('hidden')) return;
    if (btn.contains(e.target) || overlay.contains(e.target)) return;
    if (e.target.closest('#save-menu') || e.target.closest('#btn-save-menu')) return;
    closeGapPanel();
  });
  window.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    hideHelpPopover();
    if (overlay.classList.contains('hidden')) return;
    e.preventDefault();
    closeGapPanel();
  });

  slider.addEventListener('pointerdown', () => {
    sliderStart = slider.value;
  });
  slider.addEventListener('pointerup', () => { sliderStart = null; });
  slider.addEventListener('pointercancel', () => { sliderStart = null; });
  slider.addEventListener('input', () => {
    const n = Number(number.value);
    if (sliderStart !== null && slider.value === sliderStart && n > 100) return;
    number.value = slider.value;
  });
  number.addEventListener('input', () => {
    const n = Number(number.value);
    if (!Number.isFinite(n) || n < 1) return;
    if (n <= 100) slider.value = String(n);
  });
  number.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter') return;
    e.preventDefault();
    confirmBtn?.click();
  });
}
