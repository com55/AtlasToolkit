import test from 'node:test';
import assert from 'node:assert/strict';

import {
  withLock,
  withBusy,
  isBusy,
  isOverlayPainted,
  rejectBusy,
  reportBusyError,
  queueBusyError,
  resetBusyForTests,
  lastBusyMessage,
  BUSY_LABEL,
  BUSY_LOADING_LABEL,
} from '../www/js/busy-overlay.js';

function installBusyDom() {
  const classes = (hidden) => ({
    _hidden: hidden,
    add(name) { if (name === 'hidden') this._hidden = true; },
    remove(name) { if (name === 'hidden') this._hidden = false; },
    contains(name) { return name === 'hidden' ? this._hidden : false; },
  });
  const overlay = { classList: classes(true), className: '' };
  const label = { textContent: '' };
  const reject = { textContent: '' };
  const status = { textContent: '', classList: classes(true) };
  const modal = { classList: classes(true) };
  const rename = { classList: classes(false) };
  const add = { classList: classes(false) };
  const gap = { classList: classes(false), className: 'is-touch-modal' };
  const help = { classList: classes(false) };
  const body = { dataset: { addRegionDialogOpen: 'true' } };
  globalThis.document = {
    body,
    getElementById(id) {
      if (id === 'busy-overlay') return overlay;
      if (id === 'busy-label') return label;
      if (id === 'busy-reject') return reject;
      if (id === 'busy-status-line') return status;
      if (id === 'modal-overlay') return modal;
      if (id === 'rename-modal') return rename;
      if (id === 'add-region-modal') return add;
      if (id === 'nest-gap-overlay') return gap;
      if (id === 'opt-help-popover') return help;
      return null;
    },
  };
  return { overlay, label, reject, status, modal, rename, add, gap, help, body };
}

test.afterEach(() => {
  resetBusyForTests();
  delete globalThis.document;
});

test('withLock raises isBusy without painting the overlay', async () => {
  const { overlay } = installBusyDom();
  await withLock(async () => {
    assert.equal(isBusy(), true);
    assert.equal(isOverlayPainted(), false);
    assert.equal(overlay.classList.contains('hidden'), true);
  });
  assert.equal(isBusy(), false);
});

test('withBusy paints the overlay for the duration of fn', async () => {
  const { overlay, label } = installBusyDom();
  await withBusy('Repacking...', async () => {
    assert.equal(isBusy(), true);
    assert.equal(isOverlayPainted(), true);
    assert.equal(overlay.classList.contains('hidden'), false);
    assert.equal(label.textContent, 'Repacking...');
  });
  assert.equal(isBusy(), false);
  assert.equal(isOverlayPainted(), false);
  assert.equal(overlay.classList.contains('hidden'), true);
});

test('nested withBusy keeps the overlay painted and does not hide early', async () => {
  const { overlay } = installBusyDom();
  await withBusy('outer', async () => {
    await withBusy('inner', async () => {
      assert.equal(isOverlayPainted(), true);
      assert.equal(overlay.classList.contains('hidden'), false);
    });
    assert.equal(isBusy(), true);
    assert.equal(isOverlayPainted(), true);
    assert.equal(overlay.classList.contains('hidden'), false);
  });
  assert.equal(isOverlayPainted(), false);
});

test('nested withLock/withBusy keep the lock until the outer call finishes', async () => {
  installBusyDom();
  await withLock(async () => {
    await withBusy('inner', async () => {
      assert.equal(isBusy(), true);
    });
    assert.equal(isBusy(), true);
  });
  assert.equal(isBusy(), false);
});

test('withLock still releases when fn throws', async () => {
  installBusyDom();
  await assert.rejects(() => withLock(async () => { throw new Error('boom'); }), /boom/);
  assert.equal(isBusy(), false);
});

test('rejectBusy writes onto the painted overlay, not a modal', async () => {
  const { reject, modal } = installBusyDom();
  await withBusy('working', async () => {
    rejectBusy('Please wait for the current operation to finish.');
    assert.equal(reject.textContent, 'Please wait for the current operation to finish.');
    assert.equal(modal.classList.contains('hidden'), true);
  });
});

test('reportBusyError during silent lock does not unhide #modal-overlay', async () => {
  const { modal, status, overlay } = installBusyDom();
  await withLock(async () => {
    reportBusyError('Please wait for the current operation to finish.');
    assert.equal(overlay.classList.contains('hidden'), true);
    assert.equal(modal.classList.contains('hidden'), true);
    assert.equal(status.classList.contains('hidden'), false);
    assert.match(status.textContent, /Please wait/);
  });
  assert.equal(status.classList.contains('hidden'), true);
  assert.equal(status.textContent, '');
});

test('lastBusyMessage records overlay reject text for tests', async () => {
  installBusyDom();
  await withBusy('x', async () => {
    rejectBusy('busy-drop');
    assert.equal(lastBusyMessage(), 'busy-drop');
  });
});

test('withBusy default label is Processing...', async () => {
  const { label } = installBusyDom();
  await withBusy(async () => {
    assert.equal(label.textContent, BUSY_LABEL);
  });
});

test('withBusy Loading... label is used when passed explicitly', async () => {
  const { label } = installBusyDom();
  await withBusy(BUSY_LOADING_LABEL, async () => {
    assert.equal(label.textContent, 'Loading...');
  });
});

test('withBusy closes work overlays including confirm before painting', async () => {
  const { rename, add, gap, help, body, modal } = installBusyDom();
  modal.classList.remove('hidden');
  await withBusy(async () => {
    assert.equal(rename.classList.contains('hidden'), true);
    assert.equal(add.classList.contains('hidden'), true);
    assert.equal(gap.classList.contains('hidden'), true);
    assert.equal(help.classList.contains('hidden'), true);
    assert.equal(modal.classList.contains('hidden'), true);
    assert.equal(body.dataset.addRegionDialogOpen, 'false');
  });
});

test('queueBusyError during withBusy flushes to window.showAlert after unlock', async () => {
  const { reject, overlay } = installBusyDom();
  const alerts = [];
  globalThis.window = { showAlert(message, title) { alerts.push({ message, title }); } };
  await withBusy('working', async () => {
    queueBusyError('Failed to update Nest Regions.', 'Error');
    assert.equal(reject.textContent, 'Failed to update Nest Regions.');
    assert.equal(overlay.classList.contains('hidden'), false);
    assert.equal(alerts.length, 0);
  });
  assert.equal(isBusy(), false);
  await new Promise((r) => queueMicrotask(r));
  assert.deepEqual(alerts, [{ message: 'Failed to update Nest Regions.', title: 'Error' }]);
});
