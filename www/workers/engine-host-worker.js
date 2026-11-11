import { loadPyodide } from '../pyodide/pyodide.mjs';
import { makeTransferCaller } from './engine-transfer.js';

let callHost;

async function fetchLocal(url) {
  const response = await fetch(new URL(url, import.meta.url));
  if (!response.ok) throw new Error(`Engine resource fetch failed: ${response.status}`);
  return response;
}

async function start() {
  try {
    self.postMessage({ type: 'status', message: 'Loading Python runtime', progress: 0 });
    const pyodide = await loadPyodide({ indexURL: new URL('../pyodide/', import.meta.url).href });
    self.postMessage({ type: 'status', message: 'Loading numpy and pillow', progress: 0.5 });
    await pyodide.loadPackage(['numpy', 'pillow']);
    self.postMessage({ type: 'status', message: 'Loading engine wheels', progress: 0.65 });
    const manifest = await (await fetchLocal('../wheels/manifest.json')).json();
    for (const name of ['skel_mesh_parser', 'atlas_toolkit']) {
      const buffer = await (await fetchLocal(`../wheels/${manifest[name]}`)).arrayBuffer();
      pyodide.unpackArchive(buffer, 'wheel');
    }
    self.postMessage({ type: 'status', message: 'Starting application host', progress: 0.9 });
    pyodide.runPython(`
from atlas_toolkit.host import AppHost, wire
_host = AppHost()
def _call_host(method, args_json, buffers):
    return wire.call(_host, method, args_json, buffers)
`);
    callHost = makeTransferCaller(pyodide, '_call_host');
    self.postMessage({ type: 'status', message: 'Starting application host', progress: 1 });
    self.postMessage({ type: 'ready' });
  } catch (error) {
    self.postMessage({ type: 'init-failed', error: String(error) });
  }
}

self.onmessage = ({ data }) => {
  const { id, method, argsJson, buffers } = data;
  try {
    const { json, buffers: copies } = callHost(method, argsJson, buffers);
    self.postMessage({ id, json, buffers: copies }, copies);
  } catch (error) {
    self.postMessage({ id, fatal: true, error: String(error) });
  }
};

start();
