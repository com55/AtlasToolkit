import test from 'node:test';
import assert from 'node:assert/strict';

import { lanczosResizeRGBA } from '../www/js/lanczos-resize.js';

function px(data, i) {
  const o = i * 4;
  return [data[o], data[o + 1], data[o + 2], data[o + 3]];
}

test('lanczosResizeRGBA copies an image that is already the target size', () => {
  const src = new Uint8ClampedArray([
    1, 2, 3, 255, 4, 5, 6, 128,
    7, 8, 9, 0, 10, 11, 12, 255,
  ]);
  const out = lanczosResizeRGBA(src, 2, 2, 2, 2);
  assert.deepEqual([...out], [...src]);
  assert.notEqual(out, src);
});

test('lanczosResizeRGBA keeps a flat color flat when enlarging', () => {
  const src = new Uint8ClampedArray(2 * 2 * 4);
  for (let i = 0; i < 4; i++) {
    src[i * 4] = 10;
    src[i * 4 + 1] = 20;
    src[i * 4 + 2] = 30;
    src[i * 4 + 3] = 255;
  }
  const out = lanczosResizeRGBA(src, 2, 2, 5, 4);
  for (let i = 0; i < 5 * 4; i++) {
    assert.deepEqual(px(out, i), [10, 20, 30, 255]);
  }
});

test('lanczosResizeRGBA keeps a red edge red instead of mixing in black from transparency', () => {
  const src = new Uint8ClampedArray([
    255, 0, 0, 255,
    0, 0, 0, 0,
  ]);
  const out = lanczosResizeRGBA(src, 2, 1, 6, 1);
  let colored = 0;
  for (let i = 0; i < 6; i++) {
    const [r, g, b, a] = px(out, i);
    if (a < 16) continue;
    colored++;
    assert.ok(r > g + 40 && r > b + 40, `pixel ${i} is ${r},${g},${b},${a}`);
  }
  assert.ok(colored >= 2);
});

test('lanczosResizeRGBA leaves transparent padding clear around a sprite', () => {
  const srcW = 24;
  const srcH = 8;
  const src = new Uint8ClampedArray(srcW * srcH * 4);
  const x = 10;
  const y = 3;
  const o = (y * srcW + x) * 4;
  src[o] = 255;
  src[o + 1] = 255;
  src[o + 2] = 255;
  src[o + 3] = 255;
  const out = lanczosResizeRGBA(src, srcW, srcH, srcW * 2, srcH * 2);
  assert.equal(out[3], 0);
  let max = 0;
  for (let i = 3; i < out.length; i += 4) if (out[i] > max) max = out[i];
  assert.ok(max > 200);
});

test('lanczosResizeRGBA keeps a one-pixel stroke near its scaled position', () => {
  const srcW = 8;
  const src = new Uint8ClampedArray(srcW * 4);
  src[3 * 4] = 255;
  src[3 * 4 + 1] = 255;
  src[3 * 4 + 2] = 255;
  src[3 * 4 + 3] = 255;
  const dstW = 16;
  const out = lanczosResizeRGBA(src, srcW, 1, dstW, 1);
  let peak = 0;
  let peakA = -1;
  for (let i = 0; i < dstW; i++) {
    const a = out[i * 4 + 3];
    if (a > peakA) {
      peakA = a;
      peak = i;
    }
  }
  assert.ok(peak >= 5 && peak <= 7, `peak at ${peak}`);
  assert.ok(out[0 + 3] < 20);
  assert.ok(out[(dstW - 1) * 4 + 3] < 20);
  let wide = 0;
  for (let i = 0; i < dstW; i++) {
    if (out[i * 4 + 3] > 40) wide++;
  }
  assert.ok(wide <= 4, `stroke covers ${wide} columns`);
});
