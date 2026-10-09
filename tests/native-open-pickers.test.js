import test, { mock } from 'node:test';
import assert from 'node:assert/strict';

import { AtlasAPI } from '../www/js/atlas-api.js';
import { openAddRegionModal } from '../www/js/dialogs.js';

function installWindow({ pickSkel } = {}) {
  const previousWindow = globalThis.window;
  const previousDocument = globalThis.document;
  const previousFetch = globalThis.fetch;
  let inputClicks = 0;
  globalThis.window = {
    pywebview: {
      api: {
        pick_skel_file: pickSkel,
      },
    },
    matchMedia: () => ({ matches: false }),
  };
  globalThis.document = {
    createElement() {
      inputClicks += 1;
      return {
        click() {},
        addEventListener() {},
        removeEventListener() {},
        remove() {},
      };
    },
  };
  return {
    inputClicks: () => inputClicks,
    restore() {
      globalThis.window = previousWindow;
      globalThis.document = previousDocument;
      globalThis.fetch = previousFetch;
    },
  };
}

test('pywebview .skel picker uses the native dialog at the atlas folder', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let nativeDir;
  const env = installWindow({
    pickSkel: async (dir) => {
      nativeDir = dir;
      return null;
    },
  });
  try {
    const pending = AtlasAPI.pick_skel_file();
    await Promise.resolve();
    t.mock.timers.tick(60_000);
    const ok = await pending;
    assert.equal(ok, false);
    assert.equal(nativeDir, AtlasAPI.get_current_atlas_directory());
    assert.equal(env.inputClicks(), 0);
  } finally {
    env.restore();
    mock.timers.reset();
  }
});

test('pywebview add-region picker uses the native png dialog at the atlas folder', async () => {
  const calls = [];
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
    pywebview: {
      api: {
        pick_page_image: async (pageName, dir) => {
          calls.push([pageName, dir]);
          return null;
        },
      },
    },
    AtlasAPI: {
      get_current_atlas_directory: () => 'D:\\atlases',
    },
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
    assert.deepEqual(calls, [['', 'D:\\atlases']]);
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
