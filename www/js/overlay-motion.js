// SPDX-FileCopyrightText: 2026 com55
// SPDX-License-Identifier: Apache-2.0
/**
 * Exit animation for the app's modal overlays. Callers keep hiding them with
 * classList.add('hidden') (so every "is it open?" check stays instant); this
 * watcher sees the class change and keeps the overlay drawn, unclickable,
 * while it fades out. Showing it again cancels the fade.
 */
const SELECTOR = '#modal-overlay, #rename-modal, #add-region-modal, #packer-info-overlay, #about-overlay, .missing-images-overlay';
const LEAVING = 'overlay-leaving';
const DURATION_MS = 250;
const timers = new WeakMap();

function settle(node) {
  clearTimeout(timers.get(node));
  timers.delete(node);
  node.classList.remove(LEAVING);
}

function leave(node) {
  settle(node);
  node.classList.add(LEAVING);
  timers.set(node, setTimeout(() => settle(node), DURATION_MS + 50));
}

/** Fade an overlay out, then remove it from the document. */
export function removeOverlay(node) {
  node.classList.add(LEAVING);
  setTimeout(() => node.remove(), DURATION_MS + 50);
}

export function initOverlayMotion() {
  if (typeof MutationObserver === 'undefined') return;
  const observer = new MutationObserver(records => {
    for (const { target, oldValue } of records) {
      if (!target.matches?.(SELECTOR)) continue;
      const wasHidden = /(^|\s)hidden(\s|$)/.test(oldValue || '');
      const isHidden = target.classList.contains('hidden');
      if (isHidden && !wasHidden) leave(target);
      else if (!isHidden && target.classList.contains(LEAVING)) settle(target);
    }
  });
  observer.observe(document.body, { attributes: true, attributeFilter: ['class'], attributeOldValue: true, subtree: true });
}
