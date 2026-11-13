import { encodeFrame, decodeFrame } from '../../www/js/rpc-frame.js';
import { walkPayload } from '../../www/js/engine-client.js';

export function fakeDesktopServer(t, handlers = {}, assets = {}) {
  const keys = ['fetch', 'location'];
  const originals = keys.map(key => Object.getOwnPropertyDescriptor(globalThis, key));
  const calls = [];
  globalThis.location = { pathname: '/s/t/index.html' };
  globalThis.fetch = async (url, options) => {
    if (url !== 'api/rpc') return new Response(assets[url] ?? '', { status: Object.hasOwn(assets, url) ? 200 : 404 });
    const { header, buffers } = decodeFrame(options.body);
    const args = walkPayload(JSON.parse(header.argsJson), value => value && Object.keys(value).length === 1 && Object.hasOwn(value, '$buf') ? new Uint8Array(buffers[value.$buf]) : undefined);
    const call = { target: header.target, method: header.method, args };
    calls.push(call);
    let envelope;
    const outputBuffers = [];
    try {
      const handler = handlers[`${call.target}.${call.method}`] ?? handlers[call.method];
      if (!handler) throw new Error(`Unexpected command: ${call.target}.${call.method}`);
      const value = walkPayload(await handler(...args), item => {
        if (!(item instanceof Uint8Array)) return undefined;
        outputBuffers.push(item);
        return { $buf: outputBuffers.length - 1 };
      });
      envelope = { ok: true, value };
    } catch (error) { envelope = { ok: false, error: { type: error.type || error.name, message: error.message } }; }
    return { status: 200, arrayBuffer: async () => encodeFrame({ json: JSON.stringify(envelope) }, outputBuffers).buffer };
  };
  t.after(() => keys.forEach((key, i) => originals[i] ? Object.defineProperty(globalThis, key, originals[i]) : delete globalThis[key]));
  return calls;
}
