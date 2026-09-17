import test from 'node:test';
import assert from 'node:assert/strict';

import { showToast } from '../www/js/dialogs.js';

function classList(initial = []) {
  const set = new Set(initial);
  return {
    add: (c) => set.add(c),
    remove: (c) => set.delete(c),
    contains: (c) => set.has(c),
  };
}

function setupDom() {
  const overlay = { classList: classList(['hidden']) };
  const title = { innerText: 'Confirm' };
  const message = { innerText: '' };
  const btnConfirm = {
    innerText: 'Confirm',
    focus() {},
    _listeners: {},
    addEventListener(type, fn) { this._listeners[type] = fn; },
    removeEventListener(type) { delete this._listeners[type]; },
  };
  const btnCancel = {
    classList: classList(),
    addEventListener() {},
    removeEventListener() {},
  };
  const toasts = [];
  const toastContainer = {
    appendChild(node) { toasts.push(node); },
  };
  const nodes = {
    'modal-overlay': overlay,
    'modal-title': title,
    'modal-message': message,
    'btn-modal-confirm': btnConfirm,
    'btn-modal-cancel': btnCancel,
    'toast-container': toastContainer,
  };
  globalThis.document = {
    getElementById: (id) => nodes[id] || null,
    get activeElement() { return null; },
    createElement(tag) {
      return { tag, className: '', innerText: '', style: {}, addEventListener() {} };
    },
  };
  globalThis.window = {
    addEventListener() {},
    removeEventListener() {},
  };
  return { overlay, title, message, btnConfirm, btnCancel, toasts };
}

test('error notices open the modal dialog instead of a toast', () => {
  const dom = setupDom();
  showToast('Failed to load atlas file.', 'error');
  assert.equal(dom.overlay.classList.contains('hidden'), false);
  assert.equal(dom.title.innerText, 'Error');
  assert.equal(dom.message.innerText, 'Failed to load atlas file.');
  assert.equal(dom.btnConfirm.innerText, 'OK');
  assert.equal(dom.btnCancel.classList.contains('hidden'), true);
  assert.equal(dom.toasts.length, 0);
});

test('warning notices open the modal dialog instead of a toast', () => {
  const dom = setupDom();
  showToast('Please wait for the current operation to finish.', 'warning');
  assert.equal(dom.overlay.classList.contains('hidden'), false);
  assert.equal(dom.title.innerText, 'Warning');
  assert.equal(dom.message.innerText, 'Please wait for the current operation to finish.');
  assert.equal(dom.toasts.length, 0);
});

test('success and info notices stay as toasts', () => {
  const dom = setupDom();
  showToast('Image saved.', 'success');
  showToast('Cancelled', 'info');
  assert.equal(dom.overlay.classList.contains('hidden'), true);
  assert.equal(dom.toasts.length, 2);
  assert.equal(dom.toasts[0].className, 'toast success');
  assert.equal(dom.toasts[1].className, 'toast info');
});
