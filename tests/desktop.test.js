import test, { beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { isDesktop, desktopCall, createDesktopWorker, onDesktopEvent, _resetForTests, _setClock } from '../www/js/desktop.js';
import { startEngine, stopEngine, callHost, HostError } from '../www/js/engine-client.js';
import { isPywebviewDesktop } from '../www/js/platform.js';
import { encodeFrame, decodeFrame } from '../www/js/rpc-frame.js';

const keys = ['location', 'fetch', 'window', 'EventSource'];
let originals;
const reply = (value, buffers = []) => ({ status: 200, arrayBuffer: async () => encodeFrame({ json: JSON.stringify({ ok: true, value }) }, buffers).buffer });
const isError = type => error => error instanceof HostError && error.type === type;
beforeEach(() => {
  originals = keys.map(key => Object.getOwnPropertyDescriptor(globalThis, key));
  globalThis.location = { pathname: '/s/token/index.html', search: '' };
  globalThis.window = {};
  globalThis.fetch = async () => reply(null);
  globalThis.EventSource = class {
    constructor(url) { this.url = url; }
    close() { this.closed = true; }
  };
});
afterEach(() => {
  stopEngine();
  _resetForTests();
  keys.forEach((key, i) => {
    if (originals[i]) Object.defineProperty(globalThis, key, originals[i]);
    else delete globalThis[key];
  });
});

test('desktop detection uses the session path without a native bridge', () => {
  assert.equal(isDesktop(), true);
  assert.equal(isPywebviewDesktop(), true);
  location.pathname = '/index.html';
  assert.equal(isDesktop(), false);
  assert.equal(isPywebviewDesktop(), false);
  delete globalThis.location;
  assert.equal(isDesktop(), false);
});

test('desktop call encodes nested byte views and decodes result bytes', async () => {
  globalThis.fetch = async (url, opts) => {
    assert.equal(url, 'api/rpc');
    assert.equal(opts.method, 'POST');
    assert.deepEqual(opts.headers, { 'Content-Type': 'application/octet-stream' });
    const { header, buffers } = decodeFrame(opts.body);
    assert.deepEqual(header, { target: 'desktop', method: 'save', argsJson: '[{"bytes":{"$buf":0}},{"$buf":1}]' });
    assert.deepEqual(buffers.map(b => [...new Uint8Array(b)]), [[1], [2]]);
    return reply({ bytes: { $buf: 0 } }, [new Uint8Array([3])]);
  };
  const result = await desktopCall('save', [{ bytes: new Uint8Array([0, 1, 0]).subarray(1, 2) }, new Uint8Array([2]).buffer]);
  assert.deepEqual(result, { bytes: new Uint8Array([3]) });
});

test('host errors preserve their type and do not alert', async () => {
  window.showAlert = () => assert.fail('host errors must not alert');
  globalThis.fetch = async () => ({ status: 200, arrayBuffer: async () => encodeFrame({ json: '{"ok":false,"error":{"type":"Rejected","message":"no"}}' }, []).buffer });
  await assert.rejects(desktopCall('bad'), error => isError('Rejected')(error) && error.message === 'no');
});

for (const mode of ['fetch', 'status', 'frame']) {
  test(`${mode} failures become DesktopServerError and throttle alerts`, async () => {
    let now = 0;
    _setClock(() => now);
    const alerts = [];
    window.showAlert = (...args) => alerts.push(args);
    globalThis.fetch = async () => {
      if (mode === 'fetch') throw new Error('offline');
      if (mode === 'status') return { status: 503 };
      return { status: 200, arrayBuffer: async () => new ArrayBuffer(0) };
    };
    await assert.rejects(desktopCall('bad'), isError('DesktopServerError'));
    now = 1000;
    await assert.rejects(desktopCall('bad'), isError('DesktopServerError'));
    assert.equal(alerts.length, 1);
    now = 6000;
    await assert.rejects(desktopCall('bad'), isError('DesktopServerError'));
    assert.deepEqual(alerts, Array(2).fill(['Lost contact with the AtlasToolkit app process. Save your work if you can and restart the app.', 'App error']));
  });
}

for (const explicit of [true, false]) {
  test(`engine fetch worker roundtrip (explicit factory: ${explicit}) and recovery`, async () => {
    let count = 0;
    globalThis.fetch = async (url, opts) => {
      const { header, buffers } = decodeFrame(opts.body);
      assert.equal(url, 'api/rpc');
      assert.equal(header.target, 'engine');
      if (++count === 1) throw new Error('offline');
      assert.equal(header.method, 'echo');
      assert.equal(header.argsJson, '[{"$buf":0}]');
      assert.deepEqual([...new Uint8Array(buffers[0])], [1]);
      return reply({ $buf: 0 }, buffers);
    };
    await startEngine(undefined, explicit ? { createWorker: createDesktopWorker } : undefined);
    await assert.rejects(callHost('bad', []), isError('DesktopServerError'));
    assert.deepEqual(await callHost('echo', [new Uint8Array([1])]), new Uint8Array([1]));
    assert.equal(count, 2);
  });
}

test('worker ready is asynchronous and terminate drops late replies', async () => {
  const worker = createDesktopWorker();
  const messages = [];
  worker.onmessage = event => messages.push(event.data);
  assert.deepEqual(messages, []);
  await new Promise(resolve => setTimeout(resolve, 5));
  assert.deepEqual(messages, [{ type: 'ready' }]);
  let finish;
  globalThis.fetch = () => new Promise(resolve => { finish = resolve; });
  const pending = worker.postMessage({ id: 7, method: 'late', argsJson: '[]', buffers: [] });
  worker.terminate();
  finish(reply(null));
  await pending;
  assert.deepEqual(messages, [{ type: 'ready' }]);
});

test('events share one source, route by kind, isolate handlers, unsubscribe and reset', t => {
  const sources = [];
  globalThis.EventSource = class {
    constructor(url) { this.url = url; sources.push(this); }
    close() { this.closed = true; }
  };
  t.mock.method(console, 'error', () => {});
  const seen = [];
  onDesktopEvent('open', () => { throw new Error('handler'); });
  const off = onDesktopEvent('open', event => seen.push(event));
  onDesktopEvent('other', () => assert.fail('wrong kind'));
  assert.equal(sources.length, 1);
  assert.equal(sources[0].url, 'api/events');
  sources[0].onmessage({ data: '{"kind":"open","name":"sample"}' });
  assert.deepEqual(seen, [{ kind: 'open', name: 'sample' }]);
  off();
  sources[0].onmessage({ data: '{"kind":"open"}' });
  assert.equal(seen.length, 1);
  _resetForTests();
  assert.equal(sources[0].closed, true);
  onDesktopEvent('open', () => {});
  assert.equal(sources.length, 2);
});
