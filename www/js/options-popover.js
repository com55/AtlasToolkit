// SPDX-FileCopyrightText: 2026 com55
// SPDX-License-Identifier: Apache-2.0
/** Help popover for every [data-help] element (hover delay / long-press). */

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

/** The help target under `node`: any element carrying data-help. */
function helpTargetOf(node) {
  return node instanceof Element ? node.closest('[data-help]') : null;
}

/** One delegated tooltip for every [data-help] element (it replaces the
 *  native title tooltip: hover delay on mouse, long-press on touch). */
export function initOptionsPopover() {
  let hovered = null;
  document.addEventListener('mouseover', (ev) => {
    if (useLongPress()) return;
    const target = helpTargetOf(ev.target);
    if (target === hovered) return;
    hovered = target;
    if (hoverTimer) { clearTimeout(hoverTimer); hoverTimer = null; }
    hideHelpPopover();
    if (target) hoverTimer = setTimeout(() => { if (hovered === target && target.isConnected) showHelpFor(target); }, HOVER_DELAY_MS);
  });
  document.addEventListener('mouseout', (ev) => {
    if (useLongPress() || !hovered) return;
    if (ev.relatedTarget instanceof Node && hovered.contains(ev.relatedTarget)) return;
    if (helpTargetOf(ev.relatedTarget) === hovered) return;
    hovered = null;
    if (hoverTimer) { clearTimeout(hoverTimer); hoverTimer = null; }
    hideHelpPopover();
  });
  // A click acts on the control; the tooltip should not linger over what it opened.
  document.addEventListener('mousedown', () => { if (!useLongPress()) hideHelpPopover(); }, true);
  document.addEventListener('touchstart', (ev) => {
    const target = helpTargetOf(ev.target);
    if (!useLongPress() || !target || ev.touches.length !== 1) return;
    if (longPressTimer) clearTimeout(longPressTimer);
    longPressTimer = setTimeout(() => {
      swallowNextClick = true;
      showHelpFor(target);
    }, LONG_PRESS_MS);
  }, { passive: true });
  const cancelPress = () => { if (longPressTimer) { clearTimeout(longPressTimer); longPressTimer = null; } };
  document.addEventListener('touchend', cancelPress);
  document.addEventListener('touchcancel', cancelPress);
  document.addEventListener('click', (ev) => {
    if (!swallowNextClick) return;
    swallowNextClick = false;
    ev.preventDefault();
    ev.stopPropagation();
  }, true);

  document.addEventListener('click', () => {
    if (!useLongPress()) return;
    hideHelpPopover();
  });

  window.addEventListener('keydown', e => { if (e.key === 'Escape') hideHelpPopover(); });
}
