// SPDX-FileCopyrightText: 2026 com55
// SPDX-License-Identifier: Apache-2.0
/** Create a reusable progress ring, sized in pixels. */
export function createProgressRing(size) {
  const element = document.createElement('span');
  element.className = 'progress-ring';
  element.style.setProperty('--progress-ring-size', size + 'px');
  element.innerHTML = '<svg viewBox="0 0 64 64" aria-hidden="true"><circle class="progress-ring-track" cx="32" cy="32" r="28" stroke-width="5" /><circle class="progress-ring-bar" cx="32" cy="32" r="28" stroke-width="5" /></svg><span class="progress-ring-label"></span>';
  setProgressRing(element, 0);
  return element;
}

export function setProgressRing(element, progress) {
  const value = Math.max(0, Math.min(1, progress));
  element.querySelector('.progress-ring-bar').style.strokeDashoffset = 2 * Math.PI * 28 * (1 - value);
  element.querySelector('.progress-ring-label').textContent = Math.round(value * 100) + '%';
}
