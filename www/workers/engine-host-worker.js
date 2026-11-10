import { loadPyodide } from '../pyodide/pyodide.mjs';

let callHost;

async function fetchLocal(url) {
  const response = await fetch(new URL(url, import.meta.url));
  if (!response.ok) throw new Error(`Engine resource fetch failed: ${response.status}`);
  return response;
}

async function start() {
  try {
    self.postMessage({ type: 'status', message: 'Loading Python runtime' });
    const pyodide = await loadPyodide({ indexURL: new URL('../pyodide/', import.meta.url).href });
    self.postMessage({ type: 'status', message: 'Loading numpy and pillow' });
    await pyodide.loadPackage(['numpy', 'pillow']);
    self.postMessage({ type: 'status', message: 'Loading engine wheels' });
    const manifest = await (await fetchLocal('../wheels/manifest.json')).json();
    for (const name of ['skel_mesh_parser', 'atlas_toolkit']) {
      const buffer = await (await fetchLocal(`../wheels/${manifest[name]}`)).arrayBuffer();
      pyodide.unpackArchive(buffer, 'wheel');
    }
    self.postMessage({ type: 'status', message: 'Starting application host' });
    pyodide.runPython(`
from atlas_toolkit.host import AppHost, wire
_host = AppHost()
def _call_host(method, args_json, buffers):
    return wire.call(_host, method, args_json, [b.to_bytes() for b in buffers])
`);
    callHost = pyodide.globals.get('_call_host');
    self.postMessage({ type: 'ready' });
  } catch (error) {
    self.postMessage({ type: 'init-failed', error: String(error) });
  }
}

self.onmessage = ({ data }) => {
  const { id, method, argsJson, buffers } = data;
  let result;
  let output;
  try {
    result = callHost(method, argsJson, buffers.map((buffer) => new Uint8Array(buffer)));
    const json = result.get(0);
    output = result.get(1);
    const copies = [];
    for (const item of output) {
      try {
        const view = item.getBuffer('u8');
        try {
          copies.push(view.data.slice().buffer);
        } finally {
          view.release();
        }
      } finally {
        item.destroy();
      }
    }
    output.destroy();
    output = null;
    result.destroy();
    result = null;
    self.postMessage({ id, json, buffers: copies }, copies);
  } catch (error) {
    self.postMessage({ id, fatal: true, error: String(error) });
  } finally {
    output?.destroy();
    result?.destroy();
  }
};

start();
