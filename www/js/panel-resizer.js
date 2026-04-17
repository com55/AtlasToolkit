import { state } from './state.js';
import { drawRegionOverlay } from './preview.js';

export function initPanelResizer() {
  const splitter   = document.getElementById('panel-splitter');
  const leftPanel  = document.getElementById('left-panel');
  const rightPanel = document.getElementById('right-panel');
  if (!splitter || !leftPanel || !rightPanel) return;

  const LEFT_MIN    = 220;
  const RIGHT_MIN   = 320;
  const PREVIEW_MIN = 260;
  const LIST_MIN    = 220;

  let dragging  = false;
  let startPos  = 0;
  let startSize = 0;

  const isPortrait = () => window.matchMedia('(orientation: portrait), (max-width: 900px)').matches;
  const clamp = (v, min, max) => Math.max(min, Math.min(max, v));

  const applyStoredSplit = () => {
    if (isPortrait()) {
      splitter.setAttribute('aria-orientation', 'horizontal');
      leftPanel.style.removeProperty('width');
      leftPanel.style.removeProperty('min-width');
      leftPanel.style.removeProperty('flex');

      const stored   = Number(localStorage.getItem('atlastoolkit.layout.portrait.previewHeight'));
      const fallback = Math.round(window.innerHeight * 0.44);
      const maxH     = Math.max(PREVIEW_MIN, window.innerHeight - LIST_MIN);
      const nextH    = clamp(Number.isFinite(stored) && stored > 0 ? stored : fallback, PREVIEW_MIN, maxH);

      rightPanel.style.flex   = 'none';
      rightPanel.style.height = `${nextH}px`;
    } else {
      splitter.setAttribute('aria-orientation', 'vertical');
      rightPanel.style.removeProperty('height');
      rightPanel.style.removeProperty('flex');

      const stored   = Number(localStorage.getItem('atlastoolkit.layout.desktop.leftWidth'));
      const fallback = 300;
      const maxW     = Math.max(LEFT_MIN, window.innerWidth - RIGHT_MIN);
      const nextW    = clamp(Number.isFinite(stored) && stored > 0 ? stored : fallback, LEFT_MIN, maxW);

      leftPanel.style.flex     = 'none';
      leftPanel.style.width    = `${nextW}px`;
      leftPanel.style.minWidth = `${nextW}px`;
    }

    if (state.currentMode === 'modify') drawRegionOverlay();
  };

  splitter.addEventListener('pointerdown', (e) => {
    e.preventDefault();
    dragging  = true;
    startPos  = isPortrait() ? e.clientY : e.clientX;
    startSize = isPortrait()
      ? rightPanel.getBoundingClientRect().height
      : leftPanel.getBoundingClientRect().width;
    document.body.classList.add('resizing');
    splitter.setPointerCapture(e.pointerId);
  });

  splitter.addEventListener('pointermove', (e) => {
    if (!dragging) return;
    if (isPortrait()) {
      const delta = e.clientY - startPos;
      const maxH  = Math.max(PREVIEW_MIN, window.innerHeight - LIST_MIN);
      rightPanel.style.flex   = 'none';
      rightPanel.style.height = `${clamp(startSize + delta, PREVIEW_MIN, maxH)}px`;
    } else {
      const delta = e.clientX - startPos;
      const maxW  = Math.max(LEFT_MIN, window.innerWidth - RIGHT_MIN);
      const nextW = clamp(startSize + delta, LEFT_MIN, maxW);
      leftPanel.style.flex     = 'none';
      leftPanel.style.width    = `${nextW}px`;
      leftPanel.style.minWidth = `${nextW}px`;
    }
    if (state.currentMode === 'modify') drawRegionOverlay();
  });

  const stopDragging = (e) => {
    if (!dragging) return;
    dragging = false;
    document.body.classList.remove('resizing');
    if (e?.pointerId !== undefined && splitter.hasPointerCapture(e.pointerId)) {
      splitter.releasePointerCapture(e.pointerId);
    }
    if (isPortrait()) {
      localStorage.setItem(
        'atlastoolkit.layout.portrait.previewHeight',
        String(Math.round(rightPanel.getBoundingClientRect().height)),
      );
    } else {
      localStorage.setItem(
        'atlastoolkit.layout.desktop.leftWidth',
        String(Math.round(leftPanel.getBoundingClientRect().width)),
      );
    }
  };

  splitter.addEventListener('pointerup', stopDragging);
  splitter.addEventListener('pointercancel', stopDragging);
  window.addEventListener('resize', applyStoredSplit);
  applyStoredSplit();
}
