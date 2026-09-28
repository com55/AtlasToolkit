/**
 * View Mode preview jobs. Each cache key has at most one in-flight job.
 * A different key starts its own Worker immediately and does not wait.
 * Finished jobs are cached by the caller even when the selection has moved on.
 */

const inflight = new Map();
let jobSeq = 0;
let activeWorkers = 0;
let maxActiveWorkers = 0;
let workersStarted = 0;

export function previewWorkerStats() {
  return { activeWorkers, maxActiveWorkers, workersStarted };
}

export function resetPreviewJobStatsForTests() {
  activeWorkers = 0;
  maxActiveWorkers = 0;
  workersStarted = 0;
}

/** Share one promise per key. `execute` runs only for the first caller. */
export function createPreviewScheduler() {
  const pending = new Map();
  return {
    run(key, execute) {
      const existing = pending.get(key);
      if (existing) return existing;
      const promise = Promise.resolve().then(execute).finally(() => {
        pending.delete(key);
      });
      pending.set(key, promise);
      return promise;
    },
    has(key) {
      return pending.has(key);
    },
  };
}

const scheduler = createPreviewScheduler();

function canUsePreviewWorker() {
  return typeof Worker !== 'undefined' && typeof createImageBitmap === 'function';
}

function noteWorkerStart() {
  workersStarted += 1;
  activeWorkers += 1;
  if (activeWorkers > maxActiveWorkers) maxActiveWorkers = activeWorkers;
}

function noteWorkerEnd() {
  activeWorkers = Math.max(0, activeWorkers - 1);
}

function workerUrl() {
  return new URL('../workers/preview-job-worker.js', import.meta.url);
}

async function blobPreviewWorker() {
  const res = await fetch(workerUrl());
  if (!res.ok) throw new Error(`preview worker fetch failed: ${res.status}`);
  let source = await res.text();
  const replacements = [
    ['../js/core-region-ops.js', new URL('./core-region-ops.js', import.meta.url).href],
    ['../js/atlas-extracter.js', new URL('./atlas-extracter.js', import.meta.url).href],
    ['../js/canvas-surface.js', new URL('./canvas-surface.js', import.meta.url).href],
  ];
  for (const [rel, abs] of replacements) {
    const next = source.replaceAll(`'${rel}'`, `'${abs}'`).replaceAll(`"${rel}"`, `"${abs}"`);
    if (next === source) throw new Error(`preview worker blob rewrite missed ${rel}`);
    source = next;
  }
  const blobUrl = URL.createObjectURL(new Blob([source], { type: 'text/javascript' }));
  const worker = new Worker(blobUrl, { type: 'module' });
  worker.addEventListener('error', () => URL.revokeObjectURL(blobUrl), { once: true });
  return worker;
}

function jobFailure(message) {
  const err = new Error(message);
  err.previewJobFailed = true;
  return err;
}

function bindPreviewWorker(worker, message, resolve, reject) {
  noteWorkerStart();
  let settled = false;
  const finish = (fn, value) => {
    if (settled) return;
    settled = true;
    noteWorkerEnd();
    try { worker.terminate(); } catch { /* already dead */ }
    fn(value);
  };
  const id = ++jobSeq;
  const timer = setTimeout(() => {
    finish(reject, jobFailure('preview job timed out'));
  }, 5 * 60 * 1000);
  worker.onmessage = (event) => {
    const data = event.data || {};
    if (data.id !== id) return;
    clearTimeout(timer);
    if (!data.ok) {
      finish(reject, jobFailure(data.error || 'preview job failed'));
      return;
    }
    const blob = data.buffer ? new Blob([data.buffer], { type: 'image/png' }) : null;
    finish(resolve, blob);
  };
  worker.onerror = (event) => {
    clearTimeout(timer);
    finish(reject, new Error(event && event.message ? event.message : 'preview worker error'));
  };
  try {
    const { transfer, ...payload } = message;
    worker.postMessage({ id, ...payload }, transfer || []);
  } catch (err) {
    clearTimeout(timer);
    finish(reject, err);
  }
}

function spawnPreviewWorker(kind, message) {
  return new Promise((resolve, reject) => {
    if (kind === 'module') {
      try {
        bindPreviewWorker(new Worker(workerUrl(), { type: 'module' }), message, resolve, reject);
      } catch (err) {
        reject(err);
      }
      return;
    }
    blobPreviewWorker().then(
      (worker) => bindPreviewWorker(worker, message, resolve, reject),
      reject,
    );
  });
}

async function runOnWorker(build) {
  try {
    return await spawnPreviewWorker('module', await build());
  } catch (first) {
    if (first && first.previewJobFailed) throw first;
    return await spawnPreviewWorker('blob', await build());
  }
}

/**
 * @param {string} key
 * @param {{ build: () => Promise<object>, fallback: () => Promise<string|null>, store: (url: string|null) => void }} job
 * @returns {Promise<string|null>}
 */
export function runPreviewJob(key, job) {
  return scheduler.run(key, async () => {
    let url = null;
    try {
      if (!canUsePreviewWorker()) {
        url = await job.fallback();
      } else {
        const blob = await runOnWorker(job.build);
        url = blob ? URL.createObjectURL(blob) : null;
      }
    } catch (err) {
      console.error(err);
      url = await job.fallback();
    }
    if (job.store) job.store(url);
    return url;
  });
}
