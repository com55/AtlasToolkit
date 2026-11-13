// SPDX-FileCopyrightText: 2026 com55
// SPDX-License-Identifier: Apache-2.0
/** Local app-process HTTP transport. */
import { HostError, walkPayload } from './engine-client.js';
import { encodeFrame, decodeFrame } from './rpc-frame.js';

let clock = () => Date.now();
let lastAlert = -Infinity;
let eventSource = null;
let streamInterrupted = false;
const handlers = new Map();

export function isDesktop() {
  return typeof location !== 'undefined' && location.pathname.startsWith('/s/');
}

function serverError(error) {
  const now = clock();
  if (now - lastAlert >= 5000 && typeof window !== 'undefined' && typeof window.showAlert === 'function') {
    lastAlert = now;
    window.showAlert('Lost contact with the AtlasToolkit app process. Save your work if you can and restart the app.', 'App error');
  }
  return new HostError('DesktopServerError', error.message || String(error));
}

async function rpc(header, buffers) {
  try {
    const response = await fetch('api/rpc', {
      method: 'POST',
      body: encodeFrame(header, buffers),
      headers: { 'Content-Type': 'application/octet-stream' },
    });
    if (response.status !== 200 && response.status < 500) {
      throw new HostError('DesktopRequestRejected', `The app refused the request (HTTP ${response.status}).`);
    }
    if (response.status !== 200) throw new Error(`App server returned HTTP ${response.status}`);
    return decodeFrame(await response.arrayBuffer());
  } catch (error) {
    if (error instanceof HostError && error.type === 'DesktopRequestRejected') throw error;
    throw serverError(error);
  }
}

export async function desktopCall(method, args = [], opts = {}) {
  const buffers = [];
  const walkedArgs = walkPayload(args, value => {
    if (!(value instanceof Uint8Array) && !(value instanceof ArrayBuffer)) return undefined;
    const index = buffers.length;
    buffers.push(value);
    return { $buf: index };
  });
  const { header, buffers: resultBuffers } = await rpc({ target: 'desktop', method, argsJson: JSON.stringify(walkedArgs) }, buffers);
  const result = JSON.parse(header.json);
  if (!result.ok) throw new HostError(result.error.type, result.error.message);
  return walkPayload(result.value, item => {
    if (item && Object.getPrototypeOf(item) === Object.prototype
      && Object.keys(item).length === 1 && Object.hasOwn(item, '$buf')) {
      return new Uint8Array(resultBuffers[item.$buf]);
    }
    return undefined;
  });
}

export function createDesktopWorker() {
  let terminated = false;
  const worker = {
    onmessage: null,
    onerror: null,
    async postMessage({ id, method, argsJson, buffers }) {
      if (terminated) return;
      let data;
      try {
        const result = await rpc({ target: 'engine', method, argsJson }, buffers);
        data = { id, json: result.header.json, buffers: result.buffers };
      } catch (error) {
        data = { id, json: JSON.stringify({ ok: false, error: { type: error.type, message: error.message } }), buffers: [] };
      }
      if (!terminated) worker.onmessage?.({ data });
    },
    terminate() { terminated = true; },
  };
  setTimeout(() => {
    if (!terminated) worker.onmessage?.({ data: { type: 'ready' } });
  }, 0);
  return worker;
}

export function onDesktopEvent(kind, handler) {
  if (!eventSource) {
    eventSource = new EventSource('api/events');
    eventSource.onerror = () => {
      if (!streamInterrupted) console.warn('Desktop event stream interrupted; reconnecting.');
      streamInterrupted = true;
    };
    eventSource.onmessage = event => {
      try {
        const data = JSON.parse(event.data);
        streamInterrupted = false;
        for (const callback of handlers.get(data.kind) || []) {
          try { callback(data); } catch (error) { console.error(error); }
        }
      } catch (error) { console.error(error); }
    };
  }
  if (!handlers.has(kind)) handlers.set(kind, new Set());
  const callbacks = handlers.get(kind);
  callbacks.add(handler);
  return () => callbacks.delete(handler);
}

export function _setClock(fn) { clock = fn; }

export function _resetForTests() {
  eventSource?.close();
  eventSource = null;
  handlers.clear();
  streamInterrupted = false;
  lastAlert = -Infinity;
  clock = () => Date.now();
}
