/** Help popover for option toggles (hover delay / long-press). */

import { isTouchDevice, isPywebviewDesktop } from './platform.js';

const HOVER_DELAY_MS = 500;
const LONG_PRESS_MS = 450;

const helpPopover = () => document.getElementById('opt-help-popover');
let hoverTimer = null;
let longPressTimer = null;
let swallowNextClick = false;
let helpTarget = null;
function useLongPress() {
  return isTouchDevice() && !isPywebviewDesktop();
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

function toggleLabels() {
  return document.querySelectorAll('#options-row .toggle-label');
}

export function initOptionsPopover() {
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

  window.addEventListener('keydown', e => { if (e.key === 'Escape') hideHelpPopover(); });
}
