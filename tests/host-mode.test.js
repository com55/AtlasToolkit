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
