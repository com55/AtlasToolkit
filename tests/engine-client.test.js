import test, { afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { startEngine, callHost, stopEngine, onEngineDied, HostError } from '../www/js/engine-client.js';

class FakeWorker {
  posts = [];
  transfers = [];
  terminated = false;
  postMessage(message, transfer) {
    this.transfers.push(transfer);
    this.posts.push(structuredClone(message, { transfer }));
  }
  terminate() { this.terminated = true; }
  emit(data) { this.onmessage({ data }); }
  result(value, index = this.posts.length - 1) {
    this.emit({ id: this.posts[index].id, json: JSON.stringify({ ok: true, value }), buffers: [] });
  }
}
function boot(ready = true) {
  const worker = new FakeWorker();
  const promise = startEngine(() => {}, { createWorker: () => worker });
  if (ready) worker.emit({ type: 'ready' });
  return { worker, promise };
}
const errorType = (type, message) => (error) => error instanceof HostError
  && error.type === type && (message === undefined || error.message === message);
afterEach(() => stopEngine());

test('waits for ready, shares start promise, and posts calls serially in order', async () => {
  const { worker, promise } = boot(false);
  assert.equal(startEngine(), promise);
  const first = callHost('first', []);
  const second = callHost('second', []);
  assert.equal(worker.posts.length, 0);
  worker.emit({ type: 'ready' });
  await promise;
  assert.deepEqual(worker.posts.map((p) => p.method), ['first']);
  worker.result(1);
  assert.equal(await first, 1);
  assert.deepEqual(worker.posts.map((p) => p.method), ['first', 'second']);
  worker.result(2);
  assert.equal(await second, 2);
});

test('coalesces queued previews while preserving running and unkeyed calls', async () => {
  const { worker } = boot();
  const first = callHost('first', [], { coalesce: 'preview' });
  const middle = callHost('middle', [], { coalesce: 'preview' });
  const superseded = assert.rejects(middle, errorType('Superseded'));
  const plain = callHost('plain', []);
  const last = callHost('last', [], { coalesce: 'preview' });
  await superseded;
  worker.result(1);
  await first;
  assert.equal(worker.posts[1].method, 'plain');
  worker.result(2);
  await plain;
  assert.equal(worker.posts[2].method, 'last');
  worker.result(3);
  await last;
  assert.deepEqual(worker.posts.map((p) => p.method), ['first', 'plain', 'last']);
});

test('an unkeyed call queued before a superseded preview is never dropped', async () => {
  const { worker } = boot();
  const first = callHost('first', [], { coalesce: 'preview' });
  const edit = callHost('edit', []);
  const middle = callHost('middle', [], { coalesce: 'preview' });
  const superseded = assert.rejects(middle, errorType('Superseded'));
  const last = callHost('last', [], { coalesce: 'preview' });
  await superseded;
  worker.result(1);
  await first;
  assert.equal(worker.posts[1].method, 'edit');
  worker.result(2);
  await edit;
  assert.equal(worker.posts[2].method, 'last');
  worker.result(3);
  await last;
  assert.deepEqual(worker.posts.map((p) => p.method), ['first', 'edit', 'last']);
});

test('coalescing replaces only the queued call with the same key', async () => {
  const { worker } = boot();
  const first = callHost('first', []);
  const a = callHost('a', [], { coalesce: 'preview' });
  const superseded = assert.rejects(a, errorType('Superseded'));
  const b = callHost('b', [], { coalesce: 'modify-preview' });
  const last = callHost('last', [], { coalesce: 'preview' });
  await superseded;
  worker.result(1);
  await first;
  assert.equal(worker.posts[1].method, 'b');
  worker.result(2);
  assert.equal(await b, 2);
  assert.equal(worker.posts[2].method, 'last');
  worker.result(3);
  await last;
  assert.deepEqual(worker.posts.map((p) => p.method), ['first', 'b', 'last']);
});

test('the same Uint8Array is transferred once and both markers index the buffer list', async () => {
  const { worker } = boot();
  const bytes = new Uint8Array([1, 2, 3]);
  const pending = callHost('two', [{ p: bytes, q: bytes }]);
  const message = worker.posts[0];
  assert.equal(worker.transfers[0].length, 1);
  assert.equal(bytes.byteLength, 0);
  const args = JSON.parse(message.argsJson);
  assert.deepEqual(args, [{ p: { $buf: 0 }, q: { $buf: 1 } }]);
  assert.equal(message.buffers.length, 2);
  assert.deepEqual([...new Uint8Array(message.buffers[args[0].p.$buf])], [1, 2, 3]);
  assert.deepEqual([...new Uint8Array(message.buffers[args[0].q.$buf])], [1, 2, 3]);
  worker.result(null);
  await pending;
});

test('a result with a stale id leaves the running call pending', async () => {
  const { worker } = boot();
  let settled = false;
  const call = callHost('running', []).finally(() => { settled = true; });
  const next = callHost('next', []);
  worker.emit({ id: -1, json: '{"ok":true,"value":"stale"}', buffers: [] });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(settled, false);
  assert.equal(worker.posts.length, 1);
  worker.result('real');
  assert.equal(await call, 'real');
  assert.equal(worker.posts[1].method, 'next');
  worker.result('next');
  assert.equal(await next, 'next');
});

test('callHost before startEngine rejects with EngineStopped', async () => {
  await assert.rejects(callHost('before-start', []), errorType('EngineStopped'));
});

test('encodes nested bytes, transfers exact views, and decodes result markers', async () => {
  const { worker } = boot();
  const large = new Uint8Array([9, 1, 2, 9]);
  const full = new Uint8Array([3, 4]).buffer;
  const pending = callHost('bytes', [{ nested: [large.subarray(1, 3), full] }]);
  const message = worker.posts[0];
  assert.deepEqual(JSON.parse(message.argsJson), [{ nested: [{ $buf: 0 }, { $buf: 1 }] }]);
  assert.deepEqual(message.buffers.map((b) => [...new Uint8Array(b)]), [[1, 2], [3, 4]]);
  assert.deepEqual([...large], [9, 1, 2, 9]);
  assert.equal(full.byteLength, 0);
  worker.emit({ id: message.id, json: '{"ok":true,"value":{"rgba":{"$buf":0}}}', buffers: [new Uint8Array([5, 6]).buffer] });
  const result = await pending;
  assert.ok(result.rgba instanceof Uint8Array);
  assert.deepEqual([...result.rgba], [5, 6]);
});

test('HostError preserves Python type and message and advances queue', async () => {
  const { worker } = boot();
  const bad = callHost('bad', []);
  const rejected = assert.rejects(bad, errorType('CanvasTooLarge', 'too large'));
  const next = callHost('next', []);
  worker.emit({ id: worker.posts[0].id, json: '{"ok":false,"error":{"type":"CanvasTooLarge","message":"too large"}}', buffers: [] });
  await rejected;
  worker.result('next');
  assert.equal(await next, 'next');
});

for (const mode of ['fatal', 'onerror']) {
  test(`${mode} rejects all calls, notifies once, and permits restart`, async () => {
    const { worker } = boot();
    let deaths = 0;
    onEngineDied(() => deaths++);
    const running = assert.rejects(callHost('running', []), errorType('EngineDied'));
    const queued = assert.rejects(callHost('queued', []), errorType('EngineDied'));
    const stale = worker.onmessage;
    if (mode === 'fatal') worker.emit({ id: worker.posts[0].id, fatal: true, error: 'dead' });
    else worker.onerror({ message: 'dead' });
    stale({ data: { fatal: true, error: 'again' } });
    await Promise.all([running, queued]);
    assert.equal(deaths, 1);
    assert.ok(worker.terminated);
    const fresh = boot();
    await fresh.promise;
    const call = callHost('fresh', []);
    fresh.worker.result('alive');
    assert.equal(await call, 'alive');
  });
}

for (const mode of ['init-failed', 'onerror']) {
  test(`${mode} before ready rejects startup and queued calls`, async () => {
    const { worker, promise } = boot(false);
    const started = assert.rejects(promise, errorType('EngineStartFailed', 'boot failed'));
    const queued = assert.rejects(callHost('queued', []), errorType('EngineStartFailed'));
    if (mode === 'init-failed') worker.emit({ type: mode, error: 'boot failed' });
    else worker.onerror({ message: 'boot failed' });
    await Promise.all([started, queued]);
    assert.ok(worker.terminated);
  });
}

test('status is forwarded and stop rejects startup and all pending calls', async () => {
  const worker = new FakeWorker();
  const statuses = [];
  let deaths = 0;
  onEngineDied(() => deaths++);
  const start = startEngine((message, progress) => statuses.push([message, progress]), { createWorker: () => worker });
  const stopped = assert.rejects(start, errorType('EngineStopped'));
  const queued = assert.rejects(callHost('queued', []), errorType('EngineStopped'));
  worker.emit({ type: 'status', message: 'Loading', progress: 0.5 });
  assert.deepEqual(statuses, [['Loading', 0.5]]);
  stopEngine();
  await Promise.all([stopped, queued]);
  const fresh = boot();
  await fresh.promise;
  const running = assert.rejects(callHost('running', []), errorType('EngineStopped'));
  stopEngine();
  await running;
  assert.equal(deaths, 0);
});

test('a Worker factory failure rejects startup and permits a fresh start', async () => {
  await assert.rejects(startEngine(null, {
    createWorker() { throw new Error('creation failed'); },
  }), errorType('EngineStartFailed'));
  const { promise } = boot();
  await promise;
});

test('an encoding failure rejects that call and lets the next call run', async () => {
  const { worker } = boot();
  const cyclic = [];
  cyclic.push(cyclic);
  await assert.rejects(callHost('cyclic', cyclic));
  const next = callHost('next', []);
  worker.result('ok');
  assert.equal(await next, 'ok');
});
