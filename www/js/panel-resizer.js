// SPDX-FileCopyrightText: 2026 com55
// SPDX-License-Identifier: Apache-2.0
import { applyPortraitVerticalAlign, applyTransform } from './preview.js';
import { isPortrait } from './platform.js';

let _applyStoredSplit = null;

/**
 * Re-clamp the panel split after something outside this module changes the
 * chrome it accounts for -- e.g. setMode() (modify-mode.js) shows/hides
 * children of #options-row, which can shift minRightHeight() in stacked layout.
 * No-op before initPanelResizer() has run.
 */
export function refreshPanelSplit() {
  if (_applyStoredSplit) _applyStoredSplit();
}

export function initPanelResizer() {
  const splitter      = document.getElementById('panel-splitter');
  const leftPanel     = document.getElementById('left-panel');
  const rightPanel    = document.getElementById('right-panel');
  const mainContent   = document.getElementById('main-content');
  const sidebarHead   = document.getElementById('sidebar-head');
  const advanceToolbar = document.getElementById('advance-toolbar');
  const optionsRowWrap = document.getElementById('options-row-wrap');
  const statusBar     = document.getElementById('status-bar');
  const startCard     = document.getElementById('start-card');
  if (!splitter || !leftPanel || !rightPanel || !mainContent || !sidebarHead || !advanceToolbar || !optionsRowWrap || !statusBar) return;

  let dragging  = false;
  let startPos  = 0;
  let startSize = 0;
  // Portrait preview height for this visit only: people re-drag it while
  // they work, so it is not remembered. null = not placed yet.
  let portraitHeight = null;
  try { localStorage.removeItem('atlastoolkit-py.layout.portrait.previewHeight'); } catch (_) { /* storage off */ }

  const clamp = (v, min, max) => Math.max(min, Math.min(max, v));

  // Stacked (portrait/mobile) mode is user-resizable, but each side's own
  // header row always stays visible: dragging up stops once the preview has
  // shrunk to nothing beneath #options-row + status-bar; dragging down
  // stops once the region list has shrunk to nothing beneath sidebar-head
  // (and #advance-toolbar too in Edit Mode -- its
  // getBoundingClientRect().height is naturally 0 while hidden, so this
  // reads correctly either way without a separate visibility check).
  // The budget is #main-content's own height (not window.innerHeight) minus
  // the splitter's own footprint, since #app-bar can grow taller than its
  // base 42px when it wraps -- eating into the space actually left for the
  // panels. The wide left/right layout is locked to a fixed width, matching
  // the Python app -- it is not user-resizable.
  // While the start card shows (no atlas open), the preview keeps room for
  // all of it plus the 12px margin above and below (style.css #start-card).
  const startCardRoom = () => (startCard && startCard.offsetParent ? startCard.scrollHeight + 24 : 0);
  const minRightHeight = () =>
    optionsRowWrap.getBoundingClientRect().height + statusBar.getBoundingClientRect().height + startCardRoom();
  const maxRightHeight = () => Math.max(
    minRightHeight(),
    mainContent.getBoundingClientRect().height
      - splitter.getBoundingClientRect().height
      - sidebarHead.getBoundingClientRect().height
      - advanceToolbar.getBoundingClientRect().height,
  );

  const applyStoredSplit = () => {
    if (isPortrait()) {
      splitter.setAttribute('aria-orientation', 'horizontal');
      document.documentElement.style.removeProperty('--sidebar-width');
      leftPanel.style.removeProperty('width');
      leftPanel.style.removeProperty('min-width');
      leftPanel.style.removeProperty('flex');

      // First placement: just tall enough for the start card when it shows,
      // else 44% of the window. Later calls only re-clamp, so the splitter
      // stays where it is when an atlas opens and the card goes away.
      const minH  = minRightHeight();
      const maxH  = maxRightHeight();
      const first = startCardRoom() > 0 ? minH : Math.round(window.innerHeight * 0.44);
      portraitHeight = clamp(portraitHeight ?? first, minH, maxH);

      rightPanel.style.flex   = 'none';
      rightPanel.style.height = `${portraitHeight}px`;
    } else {
      splitter.setAttribute('aria-orientation', 'vertical');
      rightPanel.style.removeProperty('height');
      rightPanel.style.removeProperty('flex');

      // Fixed width, matching the Python app exactly (--sidebar-width: 300px
      // in :root) -- not user-resizable here, and no narrower tier either.
      document.documentElement.style.removeProperty('--sidebar-width');
      leftPanel.style.removeProperty('width');
      leftPanel.style.removeProperty('min-width');
      leftPanel.style.removeProperty('flex');
    }

    applyPortraitVerticalAlign();
    applyTransform();
  };

  splitter.addEventListener('pointerdown', (e) => {
    if (!isPortrait()) return; // locked/fixed in left-right mode
    e.preventDefault();
    dragging  = true;
    startPos  = e.clientY;
    startSize = rightPanel.getBoundingClientRect().height;
    document.body.classList.add('resizing');
    splitter.setPointerCapture(e.pointerId);
  });

  splitter.addEventListener('pointermove', (e) => {
    if (!dragging) return;
    const delta = e.clientY - startPos;
    const minH  = minRightHeight();
    const maxH  = maxRightHeight();
    rightPanel.style.flex   = 'none';
    rightPanel.style.height = `${clamp(startSize + delta, minH, maxH)}px`;
    applyPortraitVerticalAlign();
    applyTransform();
  });

  const stopDragging = (e) => {
    if (!dragging) return;
    dragging = false;
    document.body.classList.remove('resizing');
    if (e?.pointerId !== undefined && splitter.hasPointerCapture(e.pointerId)) {
      splitter.releasePointerCapture(e.pointerId);
    }
    portraitHeight = Math.round(rightPanel.getBoundingClientRect().height);
  };

  splitter.addEventListener('pointerup', stopDragging);
  splitter.addEventListener('pointercancel', stopDragging);
  window.addEventListener('resize', applyStoredSplit);
  _applyStoredSplit = applyStoredSplit;
  applyStoredSplit();
}
