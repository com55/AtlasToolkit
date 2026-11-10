/** Serialized calls to the Python application host in a module Worker. */
export class HostError extends Error {
  constructor(type, message) {
    super(message);
    this.name = 'HostError';
    this.type = type;
  }
}

let engine = null;
let sequence = 0;
const deathCallbacks = new Set();

function walk(value, bytes) {
  const binary = bytes(value);
  if (binary !== undefined) return binary;
  if (Array.isArray(value)) return value.map((item) => walk(item, bytes));
  if (value && (Object.getPrototypeOf(value) === Object.prototype || Object.getPrototypeOf(value) === null)) {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, walk(item, bytes)]));
  }
  return value;
}

function close(state, type, message) {
  if (engine !== state) return;
  engine = null;
  state.worker?.terminate();
  const error = new HostError(type, message);
  state.rejectStart(error);
  state.running?.reject(error);
  for (const call of state.queue) call.reject(error);
  if (type === 'EngineDied') {
    for (const callback of deathCallbacks) {
      try { callback(); } catch (error) { console.error(error); }
    }
  }
}

function pump(state) {
  if (engine !== state || !state.ready || state.running || !state.queue.length) return;
  const call = state.queue.shift();
  state.running = call;
  try {
    const buffers = [];
    const args = walk(call.args, (value) => {
      if (!(value instanceof Uint8Array) && !(value instanceof ArrayBuffer)) return undefined;
      let buffer = value;
      if (value instanceof Uint8Array) {
        buffer = value.byteOffset === 0 && value.byteLength === value.buffer.byteLength
          ? value.buffer : value.slice().buffer;
      }
      const index = buffers.length;
      buffers.push(buffer);
      return { $buf: index };
    });
    state.worker.postMessage({ id: call.id, method: call.method, argsJson: JSON.stringify(args), buffers }, [...new Set(buffers)]);
  } catch (error) {
    state.running = null;
    call.reject(error);
    pump(state);
  }
}

export function startEngine(onStatus, options = {}) {
  if (engine) return engine.promise;
  const state = { ready: false, running: null, queue: [], worker: null };
  state.promise = new Promise((resolve, reject) => {
    state.resolveStart = resolve;
    state.rejectStart = reject;
  });
  engine = state;
  try {
    state.worker = options.createWorker
      ? options.createWorker()
      : new Worker(new URL('../workers/engine-host-worker.js', import.meta.url), { type: 'module' });
    state.worker.onmessage = ({ data }) => {
      if (engine !== state) return;
      if (data.type === 'status') {
        onStatus?.(data.message);
      } else if (data.type === 'ready') {
        state.ready = true;
        state.resolveStart();
        pump(state);
      } else if (data.type === 'init-failed') {
        close(state, 'EngineStartFailed', data.error);
      } else if (data.fatal) {
        close(state, 'EngineDied', data.error);
      } else if (state.running && data.id === state.running.id) {
        const call = state.running;
        state.running = null;
        try {
          const result = JSON.parse(data.json);
          if (!result.ok) throw new HostError(result.error.type, result.error.message);
          const value = walk(result.value, (item) => {
            if (item && Object.getPrototypeOf(item) === Object.prototype
              && Object.keys(item).length === 1 && Object.hasOwn(item, '$buf')) {
              return new Uint8Array(data.buffers[item.$buf]);
            }
            return undefined;
          });
          call.resolve(value);
        } catch (error) {
          call.reject(error);
        }
        pump(state);
      }
    };
    state.worker.onerror = (event) => close(state,
      state.ready ? 'EngineDied' : 'EngineStartFailed', event.message || 'Engine worker failed');
  } catch (error) {
    close(state, 'EngineStartFailed', String(error));
  }
  return state.promise;
}

export function callHost(method, args, opts = {}) {
  if (!engine) return Promise.reject(new HostError('EngineStopped', 'Engine is not started'));
  const state = engine;
  return new Promise((resolve, reject) => {
    if (opts.coalesce !== undefined) {
      const index = state.queue.findIndex((call) => call.coalesce === opts.coalesce);
      if (index !== -1) {
        const [older] = state.queue.splice(index, 1);
        older.reject(new HostError('Superseded', 'A newer call superseded this request'));
      }
    }
    state.queue.push({ id: ++sequence, method, args, coalesce: opts.coalesce, resolve, reject });
    pump(state);
  });
}

export function onEngineDied(callback) {
  deathCallbacks.add(callback);
}

export function stopEngine() {
  if (engine) close(engine, 'EngineStopped', 'Engine stopped');
}
