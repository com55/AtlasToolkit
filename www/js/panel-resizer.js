import { state } from './state.js';
import { drawRegionOverlay } from './preview.js';

let _applyStoredSplit = null;

/**
 * Re-clamp the panel split after something outside this module changes the
 * chrome it accounts for -- e.g. setMode() (modify-mode.js) shows/hides
 * repack-options, which shifts minRightHeight() in stacked/portrait layout.
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
  const repackOptions = document.getElementById('repack-options');
  const statusBar     = document.getElementById('status-bar');
  if (!splitter || !leftPanel || !rightPanel || !mainContent || !sidebarHead || !repackOptions || !statusBar) return;

  let dragging  = false;
  let startPos  = 0;
  let startSize = 0;

  const isPortrait = () => window.matchMedia('(orientation: portrait), (max-width: 900px)').matches;
  const clamp = (v, min, max) => Math.max(min, Math.min(max, v));

  // Stacked (portrait/mobile) mode is user-resizable, but each side's own
  // header row always stays visible: dragging up stops once the preview has
  // shrunk to nothing beneath repack-options + status-bar; dragging down
  // stops once the region list has shrunk to nothing beneath sidebar-head.
  // The budget is #main-content's own height (not window.innerHeight) minus
  // the splitter's own footprint, since #app-bar can grow taller than its
  // base 42px when it wraps -- eating into the space actually left for the
  // panels. The wide left/right layout is locked to a fixed width, matching
  // the Python app -- it is not user-resizable.
  const minRightHeight = () =>
    repackOptions.getBoundingClientRect().height + statusBar.getBoundingClientRect().height;
  const maxRightHeight = () => Math.max(
    minRightHeight(),
    mainContent.getBoundingClientRect().height
      - splitter.getBoundingClientRect().height
      - sidebarHead.getBoundingClientRect().height,
  );

  const applyStoredSplit = () => {
    if (isPortrait()) {
      splitter.setAttribute('aria-orientation', 'horizontal');
      document.documentElement.style.removeProperty('--sidebar-width');
      leftPanel.style.removeProperty('width');
      leftPanel.style.removeProperty('min-width');
      leftPanel.style.removeProperty('flex');

      const stored   = Number(localStorage.getItem('atlastoolkit.layout.portrait.previewHeight'));
      const fallback = Math.round(window.innerHeight * 0.44);
      const minH     = minRightHeight();
      const maxH     = maxRightHeight();
      const nextH    = clamp(Number.isFinite(stored) && stored > 0 ? stored : fallback, minH, maxH);

      rightPanel.style.flex   = 'none';
      rightPanel.style.height = `${nextH}px`;
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

    if (state.currentMode === 'modify') drawRegionOverlay();
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
    if (state.currentMode === 'modify') drawRegionOverlay();
  });

  const stopDragging = (e) => {
    if (!dragging) return;
    dragging = false;
    document.body.classList.remove('resizing');
    if (e?.pointerId !== undefined && splitter.hasPointerCapture(e.pointerId)) {
      splitter.releasePointerCapture(e.pointerId);
    }
    localStorage.setItem(
      'atlastoolkit.layout.portrait.previewHeight',
      String(Math.round(rightPanel.getBoundingClientRect().height)),
    );
  };

  splitter.addEventListener('pointerup', stopDragging);
  splitter.addEventListener('pointercancel', stopDragging);
  window.addEventListener('resize', applyStoredSplit);
  _applyStoredSplit = applyStoredSplit;
  applyStoredSplit();
}
