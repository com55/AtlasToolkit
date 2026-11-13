// SPDX-FileCopyrightText: 2026 com55
// SPDX-License-Identifier: Apache-2.0
// Engine call buffers must cross between JavaScript and Python after the
// Pyodide heap has grown past 2 GiB, where Pyodide's own getBuffer/to_bytes
// fail on signed 32-bit pointers (www/workers/engine-transfer.js). Runs the
// built Pyodide in Node; needs about 2.5 GB of free memory.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { makeTransferCaller } from '../../www/workers/engine-transfer.js';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const PYODIDE = path.join(ROOT, 'dist/web/pyodide');
if (!fs.existsSync(path.join(PYODIDE, 'pyodide.asm.wasm'))) {
  console.log('SKIP: dist/web/pyodide is missing; build the web site first.');
  process.exit(0);
}

const failures = [];
const check = (label, ok) => { console.log(`${ok ? 'PASS' : 'FAIL'} ${label}`); if (!ok) failures.push(label); };

const { loadPyodide } = await import(pathToFileURL(path.join(PYODIDE, 'pyodide.mjs')).href);
const pyodide = await loadPyodide({ indexURL: PYODIDE + path.sep });
await pyodide.loadPackage(['numpy']);
pyodide.runPython(`
import json
import numpy as np

_hold = [bytearray(256 * 1024 * 1024) for _ in range(9)]  # grow the heap past 2 GiB


def pattern(n, seed):
    return (np.arange(n, dtype=np.uint32) * 7 + seed).astype(np.uint8).tobytes()


def echo(method, args_json, buffers):
    size = json.loads(args_json)[0]
    checks = [bytes(b) == pattern(len(b), i) for i, b in enumerate(buffers)]
    return json.dumps({"method": method, "inputs": checks}), [pattern(size, 3), b"", pattern(5, 9)]


def above_2gib():
    probe = bytes(25_000_000)
    return int(np.frombuffer(probe, np.uint8).__array_interface__["data"][0]) >= 2 ** 31
`);
const pattern = (n, seed) => Uint8Array.from({ length: n }, (_, i) => (i * 7 + seed) & 255);
const same = (buffer, expected) => {
  const got = new Uint8Array(buffer);
  if (got.length !== expected.length) return false;
  for (let i = 0; i < got.length; i++) if (got[i] !== expected[i]) return false;
  return true;
};

check('heap grew past 2 GiB', pyodide._module.HEAPU8.length > 2 ** 31 && pyodide.globals.get('above_2gib')());

// Control: Pyodide's own conversion fails in this state, so the checks
// below exercise the case the worker used to die on.
const big = pyodide.runPython('pattern(25_000_000, 3)');
let controlFailed = false;
try { big.getBuffer('u8').release(); } catch { controlFailed = true; }
big.destroy();
check('control: getBuffer fails past 2 GiB', controlFailed);

const call = makeTransferCaller(pyodide, 'echo');
const size = 25_000_000;
const inputs = [pattern(30_000_000, 0).buffer, new ArrayBuffer(0), pattern(4, 2).buffer];
const { json, buffers } = call('m', JSON.stringify([size]), inputs);
const result = JSON.parse(json);
check('method and inputs reach Python intact', result.method === 'm' && result.inputs.join() === 'true,true,true');
check('large output copied intact', same(buffers[0], pattern(size, 3)));
check('empty output stays empty', buffers[1].byteLength === 0);
check('small output copied intact', same(buffers[2], pattern(5, 9)));
for (let round = 0; round < 3; round++) call('m', JSON.stringify([size]), [pattern(1_000_000, 0).buffer]);
check('repeated calls keep working', JSON.parse(call('m', JSON.stringify([10]), []).json).inputs.length === 0);

if (failures.length) {
  console.log(`${failures.length} check(s) failed`);
  process.exit(1);
}
console.log('All engine transfer checks passed');
