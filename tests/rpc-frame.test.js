// SPDX-FileCopyrightText: 2026 com55
// SPDX-License-Identifier: Apache-2.0
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { encodeFrame, decodeFrame } from '../www/js/rpc-frame.js';

test('pins the shared frame vector and preserves the caller header', () => {
  const header = {method: 'm'};
  const frame = encodeFrame(header, [new Uint8Array([97, 98]), new ArrayBuffer(0)]);
  assert.ok(frame instanceof Uint8Array);
  assert.equal(Buffer.from(frame).toString('hex'), '1c0000007b226d6574686f64223a226d222c2273697a6573223a5b322c305d7d6162');
  const decoded = decodeFrame(frame);
  assert.deepEqual(decoded.header, header);
  assert.deepEqual(decoded.buffers.map(b => [...new Uint8Array(b)]), [[97, 98], []]);
  assert.deepEqual(header, {method: 'm'});
});

test('pins the shared Unicode frame vector', () => {
  const header = {method: 'ทดสอบ\n"\\\u0001\u2028😀'};
  const frame = encodeFrame(header, [new Uint8Array([255])]);
  assert.equal(Buffer.from(frame).toString('hex'), '3b0000007b226d6574686f64223a22e0b897e0b894e0b8aae0b8ade0b89a5c6e5c225c5c5c7530303031e280a8f09f9880222c2273697a6573223a5b315d7dff');
  const decoded = decodeFrame(frame);
  assert.deepEqual(decoded.header, header);
  assert.deepEqual(decoded.buffers.map(b => [...new Uint8Array(b)]), [[255]]);
});

test('preserves bytes of non-Uint8Array views', () => {
  const frame = encodeFrame({}, [new Uint16Array([258])]);
  const decoded = decodeFrame(new DataView(frame.buffer, frame.byteOffset, frame.byteLength));
  assert.deepEqual(decoded.buffers.map(b => [...new Uint8Array(b)]), [[2, 1]]);
});

test('round trips Unicode, a 1 MB buffer, and empty buffers', () => {
  const header = {method: 'ทดสอบ', argsJson: '雪'};
  const buffers = [new Uint8Array(1_000_000).fill(120).buffer, new ArrayBuffer(0)];
  const decoded = decodeFrame(encodeFrame(header, buffers).buffer);
  assert.deepEqual(decoded, {header, buffers});
  assert.deepEqual(decodeFrame(encodeFrame({}, [])), {header: {}, buffers: []});
});

test('honors input view offsets and returns independent ArrayBuffer copies', () => {
  const frame = encodeFrame({}, [new Uint8Array([9, 1, 2, 9]).subarray(1, 3)]);
  const padded = new Uint8Array(frame.length + 6);
  padded.set(frame, 3);
  const decoded = decodeFrame(padded.subarray(3, 3 + frame.length));
  padded.fill(0);
  assert.ok(decoded.buffers[0] instanceof ArrayBuffer);
  assert.deepEqual([...new Uint8Array(decoded.buffers[0])], [1, 2]);
});

test('rejects reserved sizes and malformed frames', () => {
  assert.throws(() => encodeFrame({sizes: []}, []), Error);
  const frames = [new Uint8Array(0), new Uint8Array(3), new Uint8Array([10, 0, 0, 0, 123, 125])];
  for (const header of [null, [], 1, {}, {sizes: null}, {sizes: [-1]}, {sizes: [-1, 2]}, {sizes: [1.5]}, {sizes: [true]}, {sizes: ['1']}, {sizes: [2]}, {sizes: []}]) {
    const raw = new TextEncoder().encode(JSON.stringify(header));
    const frame = new Uint8Array(4 + raw.length + 1);
    new DataView(frame.buffer).setUint32(0, raw.length, true);
    frame.set(raw, 4);
    frame[frame.length - 1] = 0x78;
    frames.push(frame);
  }
  const raw = Buffer.concat([Buffer.from('{"method":"'), Buffer.from([0xff]), Buffer.from('","sizes":[]}')]);
  const invalidUtf8 = new Uint8Array(4 + raw.length);
  new DataView(invalidUtf8.buffer).setUint32(0, raw.length, true);
  invalidUtf8.set(raw, 4);
  frames.push(invalidUtf8);
  frames.push(new Uint8Array([1, 0, 0, 0, 123]), new Uint8Array([1, 0, 0, 0, 255]));
  frames.push(encodeFrame({}, [new Uint8Array([1, 2])]).slice(0, -1));
  for (const frame of frames) assert.throws(() => decodeFrame(frame), Error);
});
