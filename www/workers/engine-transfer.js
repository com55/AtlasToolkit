// SPDX-FileCopyrightText: 2026 com55
// SPDX-License-Identifier: Apache-2.0
// Moves call buffers between JavaScript and the Python heap by address.
//
// Pyodide's own buffer helpers (PyProxy.getBuffer, JsProxy.to_bytes) pass
// wasm pointers as signed 32-bit integers. Once the engine heap grows past
// 2 GiB, a buffer stored there fails ("Invalid typed array length",
// "offset is out of bounds") and the worker dies. Python reports each
// buffer's unsigned address and the copies go through the heap directly.
// PyProxy.toJs() of bytes past 2 GiB returns wrong bytes without an error:
// never use it here. Upstream (pyodide/pyodide#6545): PR #6495 fixed
// to_bytes/assign/toJs and PR #6546 fixed getBuffer, both merged but
// unreleased as of 2026-11-12. Drop this module once the app ships a
// Pyodide release that has both.

export const TRANSFER_PY = `
import json as _transfer_json
import numpy as _transfer_np


def _transfer_address(buffer):
    if not len(buffer):
        return 0
    return int(_transfer_np.frombuffer(buffer, _transfer_np.uint8).__array_interface__["data"][0])


def _transfer_alloc(sizes_json):
    buffers = [bytearray(size) for size in _transfer_json.loads(sizes_json)]
    return buffers, _transfer_json.dumps([_transfer_address(b) for b in buffers])


def _transfer_call(target, method, args_json, buffers):
    text, output = target(method, args_json, [bytes(b) for b in buffers])
    return text, output, _transfer_json.dumps([[_transfer_address(b), len(b)] for b in output])
`;

/** A function (method, argsJson, ArrayBuffer[]) -> {json, buffers} calling
 *  the Python callable `targetName(method, args_json, list[bytes])`, which
 *  returns (json_text, list[bytes]). Needs numpy loaded. */
export function makeTransferCaller(pyodide, targetName) {
  pyodide.runPython(TRANSFER_PY);
  const alloc = pyodide.globals.get('_transfer_alloc');
  const call = pyodide.globals.get('_transfer_call');
  const target = pyodide.globals.get(targetName);
  // Module.HEAPU8 is replaced when the heap grows: read it after each step.
  const heap = () => pyodide._module.HEAPU8;
  return (method, argsJson, buffers) => {
    const inputs = buffers.map((buffer) => new Uint8Array(buffer));
    let allocated;
    let list;
    let result;
    try {
      allocated = alloc(JSON.stringify(inputs.map((input) => input.length)));
      const addresses = JSON.parse(allocated.get(1));
      inputs.forEach((input, index) => { if (input.length) heap().set(input, addresses[index]); });
      list = allocated.get(0);
      result = call(target, method, argsJson, list);
      const json = result.get(0);
      const spans = JSON.parse(result.get(2));
      const copies = spans.map(([address, length]) => (
        length ? heap().slice(address, address + length).buffer : new ArrayBuffer(0)
      ));
      return { json, buffers: copies };
    } finally {
      list?.destroy();
      allocated?.destroy();
      result?.destroy();
    }
  };
}
