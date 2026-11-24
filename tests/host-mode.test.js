// SPDX-FileCopyrightText: 2026 com55
// SPDX-License-Identifier: Apache-2.0
import test from 'node:test';
import assert from 'node:assert/strict';
import { cleanLabel, webHostOrigin, acceptMessage, hostContext, setHostContext } from '../www/js/host-mode.js';
import { runSaveSink } from '../www/js/atlas-api.js';
import { setDownloadSink, downloadSink } from '../www/js/platform.js';

test('cleanLabel trims, cuts to 40 characters and falls back when empty', () => {
  assert.equal(cleanLabel('  x  ', 'f'), 'x');
  assert.equal(cleanLabel('', 'f'), 'f');
  assert.equal(cleanLabel('   ', 'f'), 'f');
  assert.equal(cleanLabel('a'.repeat(50), 'f').length, 40);
  assert.equal(cleanLabel(null, 'Save'), 'Save');
  assert.equal(cleanLabel(undefined, 'Save'), 'Save');
  assert.equal(cleanLabel(42, 'Save'), '42');
});

test('webHostOrigin accepts only a bare origin inside a frame', t => {
  t.mock.method(console, 'warn', () => {});
  assert.equal(webHostOrigin('?host=https://a.example', true), 'https://a.example');
  assert.equal(webHostOrigin('?host=http://localhost:8080', true), 'http://localhost:8080');
  assert.equal(webHostOrigin('?host=https://a.example', false), null);
  assert.equal(webHostOrigin('?host=https://a.example/x', true), null);
  assert.equal(webHostOrigin('?host=javascript:alert(1)', true), null);
  assert.equal(webHostOrigin('?host=not a url', true), null);
  assert.equal(webHostOrigin('', true), null);
  assert.equal(webHostOrigin('?other=1', true), null);
});

test('acceptMessage needs the host origin, the parent window and the protocol marker', () => {
  const parent = {}, other = {};
  const data = { atlasToolkitHost: 1, type: 'open' };
  const origin = 'https://a.example';
  assert.equal(acceptMessage({ origin, source: parent, data }, origin, parent), data);
  assert.equal(acceptMessage({ origin: 'https://b.example', source: parent, data }, origin, parent), null);
  assert.equal(acceptMessage({ origin, source: other, data }, origin, parent), null);
  assert.equal(acceptMessage({ origin, source: parent, data: { type: 'open' } }, origin, parent), null);
  assert.equal(acceptMessage({ origin, source: parent, data: { atlasToolkitHost: 2, type: 'open' } }, origin, parent), null);
  assert.equal(acceptMessage({ origin, source: parent, data: null }, origin, parent), null);
  assert.equal(acceptMessage({ origin, source: parent, data: 'text' }, origin, parent), null);
});

test('host context is null until set', () => {
  assert.equal(hostContext(), null);
  setHostContext({ kind: 'web', hostName: 'Example Host', saveLabel: 'Save' });
  assert.deepEqual(hostContext(), { kind: 'web', hostName: 'Example Host', saveLabel: 'Save' });
  setHostContext(null);
  assert.equal(hostContext(), null);
});

const outputs = [{ name: 'p.png', data: new Blob(['png']) }, { name: 'a.atlas', data: 'text' }];

test('a refused save is not marked saved', async () => {
  const marked = [];
  const result = await runSaveSink({ deliver: async () => ({ ok: false, message: 'nope' }) }, outputs, async () => marked.push(1));
  assert.equal(result, 'Error: nope');
  assert.deepEqual(marked, []);
  const fallback = await runSaveSink({ deliver: async () => ({ ok: false }) }, outputs, async () => marked.push(1));
  assert.equal(fallback, 'Error: The host did not accept the save.');
  assert.deepEqual(marked, []);
});

test('an accepted save is marked saved before afterSaved runs', async () => {
  const order = [];
  const sink = {
    deliver: async list => { order.push(['deliver', list.map(o => o.name)]); return { ok: true, message: 'Sent.' }; },
    afterSaved: async list => { order.push(['after', list.length]); },
  };
  const result = await runSaveSink(sink, outputs, async () => order.push(['mark']));
  assert.equal(result, 'Sent.');
  assert.deepEqual(order, [['deliver', ['p.png', 'a.atlas']], ['mark'], ['after', 2]]);
  assert.equal(await runSaveSink({ deliver: async () => ({ ok: true }) }, outputs, async () => {}), 'Saved.');
});

test('a thrown deliver error becomes an error result', async () => {
  const marked = [];
  const result = await runSaveSink({ deliver: async () => { throw new Error('disk full'); } }, outputs, async () => marked.push(1));
  assert.equal(result, 'Error: disk full');
  assert.deepEqual(marked, []);
});

test('the download sink is unset by default and can be set and cleared', () => {
  assert.equal(downloadSink(), null);
  const fn = () => {};
  setDownloadSink(fn);
  assert.equal(downloadSink(), fn);
  setDownloadSink(null);
  assert.equal(downloadSink(), null);
});

// initWebHost with a hand-built window: the host's replies drive it.
function webHarness(t) {
  const posted = [], listeners = [];
  const parent = { postMessage: (data, target) => posted.push({ data, target }) };
  const saved = { window: globalThis.window, document: globalThis.document };
  const el = () => ({ textContent: '', dataset: {}, classList: { add() {}, remove() {} }, childNodes: [], querySelector: () => el() });
  const nodes = new Map();
  globalThis.window = { parent, addEventListener: (type, fn) => type === 'message' && listeners.push(fn) };
  globalThis.document = {
    body: { classList: { add() {} } },
    getElementById: id => { if (!nodes.has(id)) nodes.set(id, el()); return nodes.get(id); },
    querySelectorAll: () => [],
  };
  t.after(() => { globalThis.window = saved.window; globalThis.document = saved.document; setHostContext(null); });
  let sink = null;
  const confirms = [];
  const h = { answer: false, discarded: 0 };
  return Object.assign(h, {
    posted, nodes,
    deps: {
      appVersion: '1', waitForEngine: async () => {}, loadFiles: async () => true, hasPending: () => true,
      showConfirm: async (...args) => { confirms.push(args); return h.answer; }, confirms,
      discardEdits: async () => { h.discarded++; },
      withBusy: async (_label, fn) => fn(), setSaveSink: s => { sink = s; }, setDownloadSink() {},
    },
    sink: () => sink,
    send: (data, source = parent, origin = 'https://h.example') =>
      listeners.forEach(fn => fn({ origin, source, data: { atlasToolkitHost: 1, ...data } })),
  });
}
const tick = () => new Promise(resolve => setTimeout(resolve, 0));

test('a web save resolves on the matching id, even sent back as a string', async t => {
  const { initWebHost } = await import('../www/js/host-mode.js');
  const h = webHarness(t);
  initWebHost('https://h.example', h.deps);
  h.send({ type: 'open', files: [], hostName: 'Example Host' });
  await tick();
  const delivered = h.sink().deliver([{ name: 'a.atlas', data: 'text' }]);
  const save = h.posted.find(p => p.data.type === 'save').data;
  assert.equal(h.posted.every(p => p.target === 'https://h.example'), true);
  h.send({ type: 'saved', id: String(save.id), ok: true });
  assert.deepEqual(await delivered, { ok: true, message: 'Sent to Example Host.' });
});

test('a web save from another window or origin is ignored; close-request cancels a pending save', async t => {
  const { initWebHost } = await import('../www/js/host-mode.js');
  const h = webHarness(t);
  initWebHost('https://h.example', h.deps);
  let done = null;
  h.sink().deliver([{ name: 'a.atlas', data: 'text' }]).then(r => { done = r; });
  const id = h.posted.find(p => p.data.type === 'save').data.id;
  h.send({ type: 'saved', id, ok: true }, {});
  h.send({ type: 'saved', id, ok: true }, undefined, 'https://other.example');
  await tick();
  assert.equal(done, null);
  h.send({ type: 'close-request' });
  await tick(); await tick();
  assert.deepEqual(done, { ok: false, message: 'Cancelled' });
  assert.equal(h.posted.at(-1).data.type, 'close');
});

test('close-request: Discard throws the edits away so the same frame can open the next atlas', async t => {
  const { initWebHost } = await import('../www/js/host-mode.js');
  const h = webHarness(t);
  initWebHost('https://h.example', h.deps);
  h.send({ type: 'close-request' });
  await tick(); await tick();
  assert.equal(h.discarded, 0);
  assert.deepEqual(h.posted.at(-1).data, { atlasToolkitHost: 1, type: 'close', ok: false });
  h.answer = true;
  h.send({ type: 'close-request' });
  await tick(); await tick();
  assert.equal(h.discarded, 1);
  assert.deepEqual(h.posted.at(-1).data, { atlasToolkitHost: 1, type: 'close', ok: true });
});
