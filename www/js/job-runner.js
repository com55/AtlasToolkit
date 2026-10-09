/**
 * nestPack JobRunner.
 *
 * Desktop Worker origin (Windows/WebView2 spike, 2026-09-18):
 *   1. `new Worker(new URL('../workers/engine-job-worker.js', import.meta.url), { type: 'module' })`
 *      under the existing file:// document — CONFIRMED on WebView2
 *      (empty nestPack probe returned {canvasW:0,canvasH:0,placements:[]}).
 *      Production already fetch()es sibling PNGs as file:// from that
 *      document (platform.js loadFileAsFile), and pywebview's default
 *      ALLOW_FILE_URLS adds --allow-file-access-from-files.
 *   2. If 1 throws: fetch the worker source and `new Worker(blobUrl, { type: 'module' })`.
 * HTTP (§4 of the worker/cache plan) is not implemented — spike 1 passed.
 *
 * Node (`node --test`) has no Web Worker global. nestPack numbers stay on
 * this thread so unit tests can pin them; that branch is not used in the
 * browser or pywebview.
 */
import { nestPack } from './repack-nest.js';

let _worker = null;
let _workerReady = null;
let _origin = null; // 'module-url' | 'blob-url' | 'node-main'
const _pending = new Map();
let _seq = 0;
let _boundWorker = null;
let _jobTimeoutMs = 5 * 60 * 1000;

export function setJobTimeoutMsForTests(ms) {
  _jobTimeoutMs = ms;
}

export function cloneNestPackItems(items) {
  return (items || []).map((it) => ({
    name: it.name,
    w: it.w,
    h: it.h,
    footprint: it.footprint instanceof Uint8Array
      ? it.footprint.slice()
      : new Uint8Array(it.footprint || []),
  }));
}

function rejectAllPending(reason) {
  const err = reason instanceof Error ? reason : new Error(String(reason || 'worker died'));
  for (const [, rec] of _pending) {
    if (rec.timer) clearTimeout(rec.timer);
    rec.reject(err);
  }
  _pending.clear();
}

function bindWorker(worker) {
  _boundWorker = worker;
  const fail = (err) => {
    if (_boundWorker !== worker) return;
    rejectAllPending(err);
    try { worker.terminate(); } catch { /* already dead */ }
    if (_worker === worker) {
      _worker = null;
      _workerReady = null;
    }
    _boundWorker = null;
  };
  worker.onmessage = (event) => {
    const data = event.data || {};
    const rec = _pending.get(data.id);
    if (!rec) return;
    _pending.delete(data.id);
    if (rec.timer) clearTimeout(rec.timer);
    if (data.ok) rec.resolve(data.result);
    else rec.reject(new Error(data.error || 'nestPack job failed'));
  };
  worker.onerror = (event) => {
    fail(new Error(event && event.message ? event.message : 'engine-job-worker error'));
  };
  worker.onmessageerror = () => {
    fail(new Error('engine-job-worker messageerror'));
  };
}

async function startBlobWorker() {
  const srcUrl = new URL('../workers/engine-job-worker.js', import.meta.url);
  const res = await fetch(srcUrl);
  if (!res.ok) throw new Error(`worker fetch failed: ${res.status}`);
  const source = await res.text();
  // Blob module workers resolve relative imports against the blob: URL, so
  // rewrite the nestPack import to an absolute URL before the blob is made.
  const nestUrl = new URL('./repack-nest.js', import.meta.url).href;
  const rewritten = source.replace(
    /from\s+['"]\.\.\/js\/repack-nest\.js['"]/,
    `from '${nestUrl}'`,
  );
  if (rewritten === source) {
    throw new Error('worker blob rewrite missed nestPack import');
  }
  const blob = new Blob([rewritten], { type: 'text/javascript' });
  const blobUrl = URL.createObjectURL(blob);
  const worker = new Worker(blobUrl, { type: 'module' });
  bindWorker(worker);
  // Keep blobUrl alive for the module worker's import; one URL for the
  // process lifetime if this fallback is used.
  worker.addEventListener('error', () => URL.revokeObjectURL(blobUrl), { once: true });
  return worker;
}

function probeWorker(worker) {
  return new Promise((resolve, reject) => {
    const id = ++_seq;
    const timer = setTimeout(() => {
      _pending.delete(id);
      reject(new Error('worker probe timed out'));
    }, 5000);
    _pending.set(id, {
      resolve(result) { clearTimeout(timer); resolve(result); },
      reject(err) { clearTimeout(timer); reject(err); },
    });
    try {
      worker.postMessage({ id, type: 'nestPack', items: [], options: {} });
    } catch (err) {
      clearTimeout(timer);
      _pending.delete(id);
      reject(err);
    }
  });
}

async function startWorker() {
  if (typeof Worker === 'undefined') {
    _origin = 'node-main';
    return null;
  }
  const url = new URL('../workers/engine-job-worker.js', import.meta.url);
  let firstWorker = null;
  try {
    firstWorker = new Worker(url, { type: 'module' });
    bindWorker(firstWorker);
    await probeWorker(firstWorker);
    _origin = 'module-url';
    return firstWorker;
  } catch (first) {
    if (firstWorker) {
      if (_boundWorker === firstWorker) _boundWorker = null;
      try { firstWorker.terminate(); } catch { /* already dead */ }
    }
    try {
      const worker = await startBlobWorker();
      await probeWorker(worker);
      _origin = 'blob-url';
      return worker;
    } catch (second) {
      throw new Error(
        `nestPack Worker unavailable (file URL: ${first && first.message}; blob: ${second && second.message})`,
      );
    }
  }
}

function ensureWorker() {
  if (_worker) return Promise.resolve(_worker);
  if (_workerReady) return _workerReady;
  _workerReady = startWorker().then((w) => {
    _worker = w;
    return w;
  }).catch((err) => {
    _workerReady = null;
    throw err;
  });
  return _workerReady;
}

export function jobRunnerOrigin() {
  return _origin;
}

export async function runNestPack(items, options) {
  const cloned = cloneNestPackItems(items);
  const worker = await ensureWorker();
  if (!worker) {
    return nestPack(cloned, options || {});
  }
  const id = ++_seq;
  const transfer = [];
  for (const it of cloned) {
    if (it.footprint && it.footprint.buffer) transfer.push(it.footprint.buffer);
  }
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      _pending.delete(id);
      try { worker.terminate(); } catch { /* already dead */ }
      _worker = null;
      _workerReady = null;
      _boundWorker = null;
      rejectAllPending(new Error('nestPack job timed out'));
      reject(new Error('nestPack job timed out'));
    }, _jobTimeoutMs);
    _pending.set(id, { resolve, reject, timer });
    try {
      worker.postMessage({ id, type: 'nestPack', items: cloned, options: options || {} }, transfer);
    } catch (err) {
      clearTimeout(timer);
      _pending.delete(id);
      reject(err);
    }
  });
}

export async function terminateJobRunnerForTests() {
  rejectAllPending(new Error('job-runner terminated'));
  if (_worker && typeof _worker.terminate === 'function') _worker.terminate();
  _worker = null;
  _workerReady = null;
  _boundWorker = null;
  _origin = null;
}
