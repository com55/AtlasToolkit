import { fakeDesktopServer } from './helpers/fake-desktop-server.js';
import test from 'node:test';
import assert from 'node:assert/strict';

import { AtlasAPI } from '../www/js/atlas-api.js';
import { openAddRegionModal } from '../www/js/dialogs.js';

test('desktop skeleton picker calls pick_skel without directory arguments', async t => {
  const calls = fakeDesktopServer(t, { pick_skel: () => null });
  assert.equal(await AtlasAPI.pick_skel_file(), false);
  assert.deepEqual(calls, [{ target: 'desktop', method: 'pick_skel', args: [] }]);
});

test('desktop add-region picker calls pick_mod_image without directory arguments', async (t) => {
  const calls = fakeDesktopServer(t, { pick_mod_image: () => null });
  const fileInput = {
    value: '',
    files: [],
    clickCount: 0,
    click() { this.clickCount += 1; },
    onchange: null,
  };
  const dropArea = { onclick: null, ondragover: null, ondrop: null };
  const nameInput = { value: '', oninput: null };
  const classList = () => ({
    add() {},
    remove() {},
    contains() { return false; },
  });
  const nodes = {
    'add-region-modal': { classList: classList() },
    'add-file-input': fileInput,
    'add-name-input': nameInput,
    'add-name-error': { classList: classList(), textContent: '' },
    'add-confirm-btn': { disabled: false, onclick: null },
    'add-image-preview': { classList: classList(), src: '', removeAttribute() {} },
    'add-drop-hint': { classList: classList() },
    'add-drop-area': dropArea,
    'add-cancel-btn': { onclick: null },
  };
  const previousWindow = globalThis.window;
  const previousDocument = globalThis.document;
  globalThis.window = {
    matchMedia: () => ({ matches: false }),
  };
  globalThis.document = {
    getElementById: (id) => nodes[id] || null,
    body: { dataset: {} },
  };
  try {
    openAddRegionModal({ getEffectiveNames: () => [], onConfirm: async () => {} });
    await dropArea.onclick();
    assert.equal(fileInput.clickCount, 0);
    assert.deepEqual(calls, [{ target: 'desktop', method: 'pick_mod_image', args: [] }]);
  } finally {
    globalThis.window = previousWindow;
    globalThis.document = previousDocument;
  }
});

test('browser add-region picker still opens the file input', async () => {
  const fileInput = {
    value: '',
    files: [],
    clickCount: 0,
    click() { this.clickCount += 1; },
    onchange: null,
  };
  const dropArea = { onclick: null, ondragover: null, ondrop: null };
  const classList = () => ({ add() {}, remove() {}, contains() { return false; } });
  const nodes = {
    'add-region-modal': { classList: classList() },
    'add-file-input': fileInput,
    'add-name-input': { value: '', oninput: null },
    'add-name-error': { classList: classList(), textContent: '' },
    'add-confirm-btn': { disabled: false, onclick: null },
    'add-image-preview': { classList: classList(), src: '', removeAttribute() {} },
    'add-drop-hint': { classList: classList() },
    'add-drop-area': dropArea,
    'add-cancel-btn': { onclick: null },
  };
  const previousWindow = globalThis.window;
  const previousDocument = globalThis.document;
  globalThis.window = { matchMedia: () => ({ matches: false }) };
  globalThis.document = {
    getElementById: (id) => nodes[id] || null,
    body: { dataset: {} },
  };
  try {
    openAddRegionModal({ getEffectiveNames: () => [], onConfirm: async () => {} });
    await dropArea.onclick();
    assert.equal(fileInput.clickCount, 1);
  } finally {
    globalThis.window = previousWindow;
    globalThis.document = previousDocument;
  }
});
