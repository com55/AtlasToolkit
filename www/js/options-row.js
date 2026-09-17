/**
 * options-row.js
 * When #options-row's groups do not fit on one 35px line, wrap whole <li>
 * groups onto the next line and collapse that extra height behind a chevron
 * instead of scrolling horizontally.
 */

import { refreshPanelSplit } from './panel-resizer.js';

const COLLAPSED_H = 35;

function wrapEl() {
  return document.getElementById('options-row-wrap');
}

function rowEl() {
  return document.getElementById('options-row');
}

function collapseBtn() {
  return document.getElementById('btn-options-collapse');
}

let scheduled = 0;
let lastChromeHeight = 0;

function chromeHeight() {
  const el = wrapEl() || rowEl();
  return el ? el.getBoundingClientRect().height : 0;
}

function syncLineStartClasses(row) {
  for (let pass = 0; pass < 3; pass++) {
    const before = [...row.querySelectorAll(':scope > li.is-line-start')].map((el) => el.id).join();
    let lineTop = null;
    for (const li of row.querySelectorAll(':scope > li')) {
      if (getComputedStyle(li).display === 'none') {
        li.classList.remove('is-line-start');
        continue;
      }
      const top = Math.round(li.getBoundingClientRect().top);
      const isStart = lineTop === null || top > lineTop + COLLAPSED_H / 2;
      li.classList.toggle('is-line-start', isStart);
      if (isStart) lineTop = top;
    }
    const after = [...row.querySelectorAll(':scope > li.is-line-start')].map((el) => el.id).join();
    if (before === after) break;
  }
}

export function syncOptionsRowOverflow() {
  const row = rowEl();
  const btn = collapseBtn();
  if (!row || !btn) return;

  const keepExpanded = row.classList.contains('is-expanded');
  const overflow = keepExpanded
    ? row.getBoundingClientRect().height > COLLAPSED_H + 1
    : row.scrollHeight > row.clientHeight + 1;

  row.classList.toggle('is-overflowing', overflow);
  if (!overflow) row.classList.remove('is-expanded');
  const expanded = overflow && row.classList.contains('is-expanded');
  btn.classList.toggle('hidden', !overflow);
  btn.setAttribute('aria-expanded', expanded ? 'true' : 'false');
  btn.setAttribute('aria-label', expanded ? 'Show fewer options' : 'Show more options');
  syncLineStartClasses(row);

  const h = chromeHeight();
  if (Math.abs(h - lastChromeHeight) > 0.5) {
    lastChromeHeight = h;
    refreshPanelSplit();
  }
}

export function scheduleOptionsRowOverflowSync() {
  if (scheduled) return;
  scheduled = requestAnimationFrame(() => {
    scheduled = 0;
    syncOptionsRowOverflow();
  });
}

export function initOptionsRowCollapse() {
  const row = rowEl();
  const btn = collapseBtn();
  if (!row || !btn) return;

  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    if (btn.classList.contains('hidden')) return;
    row.classList.toggle('is-expanded');
    syncOptionsRowOverflow();
  });

  new ResizeObserver(scheduleOptionsRowOverflowSync).observe(row);
  new MutationObserver(scheduleOptionsRowOverflowSync).observe(row, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: ['class', 'style', 'hidden'],
    characterData: true,
  });
  window.addEventListener('resize', scheduleOptionsRowOverflowSync);
  syncOptionsRowOverflow();
}
