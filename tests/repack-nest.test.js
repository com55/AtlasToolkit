import test from 'node:test';
import assert from 'node:assert/strict';

import { rotate90CW, rotate180, rotate90CCW, dilate, normalizeGap } from '../www/js/repack-nest.js';

// 2x3 (w=2,h=3) asymmetric pattern -- chosen specifically to NOT be
// 180-degree-symmetric, so rotate90CW and rotate90CCW must differ.
//   col: 0 1
// row0:  1 0
// row1:  1 0
// row2:  0 1
const SRC_W = 2, SRC_H = 3;
const SRC = new Uint8Array([1, 0, 1, 0, 0, 1]);

test('rotate90CW: 2x3 -> 3x2, matches hand-rotated expectation', () => {
  const out = rotate90CW(SRC, SRC_W, SRC_H);
  // Rotating 90 CW: new(x,y) = old(y, h-1-x) in a w=3,h=2 output.
  //   col: 0 1 2
  // row0:  0 1 1
  // row1:  1 0 0
  assert.deepEqual(Array.from(out), [0, 1, 1, 1, 0, 0]);
});

test('rotate90CCW: 2x3 -> 3x2, matches hand-rotated expectation', () => {
  const out = rotate90CCW(SRC, SRC_W, SRC_H);
  //   col: 0 1 2
  // row0:  0 0 1
  // row1:  1 1 0
  // Fixture is not 180-degree-symmetric, so CW and CCW must differ.
  assert.deepEqual(Array.from(out), [0, 0, 1, 1, 1, 0]);
});

test('rotate180: 2x3 -> 2x3, reversed', () => {
  const out = rotate180(SRC, SRC_W, SRC_H);
  assert.deepEqual(Array.from(out), [1, 0, 0, 1, 0, 1]);
});

test('rotate90CW then rotate90CCW returns to the original', () => {
  const cw = rotate90CW(SRC, SRC_W, SRC_H);
  const back = rotate90CCW(cw, SRC_H, SRC_W); // dims swapped after the first rotation
  assert.deepEqual(Array.from(back), Array.from(SRC));
});

test('rotate90CW and rotate90CCW produce genuinely different output for this fixture (guards against a future edit silently reintroducing 180-degree symmetry)', () => {
  const cw = rotate90CW(SRC, SRC_W, SRC_H);
  const ccw = rotate90CCW(SRC, SRC_W, SRC_H);
  assert.notDeepEqual(Array.from(cw), Array.from(ccw));
});

test('dilate: single cell in a 5x5 mask, gap=1 -> 3x3 block in the padded 7x7 output', () => {
  const mask = new Uint8Array(25); // 5x5, all zero
  mask[2 * 5 + 2] = 1; // (x=2,y=2)
  const { mask: out, w, h } = dilate(mask, 5, 5, 1);
  assert.equal(w, 7);
  assert.equal(h, 7);
  const set = new Set();
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) if (out[y * w + x]) set.add(`${x},${y}`);
  const expected = new Set();
  for (let dy = 2; dy <= 4; dy++) for (let dx = 2; dx <= 4; dx++) expected.add(`${dx},${dy}`);
  assert.deepEqual(set, expected);
});

test('dilate: a cell at the mask edge is not clipped in the padded output', () => {
  const mask = new Uint8Array(9); // 3x3
  mask[0] = 1; // (x=0,y=0), the top-left corner
  const { mask: out, w, h } = dilate(mask, 3, 3, 2);
  assert.equal(w, 7); // 3 + 2*2
  assert.equal(h, 7);
  // Original cell (0,0) -> padded (2,2); dilated by 2 -> full 5x5 block from (0,0) to (4,4).
  for (let y = 0; y <= 4; y++) {
    for (let x = 0; x <= 4; x++) {
      assert.equal(out[y * w + x], 1, `expected (${x},${y}) set`);
    }
  }
  assert.equal(out[5 * w + 5], 0); // well outside the dilated block
});

test('normalizeGap: valid integers pass through, invalid input falls back to 4', () => {
  assert.equal(normalizeGap(1), 1);
  assert.equal(normalizeGap(10), 10);
  assert.equal(normalizeGap(2.7), 2); // floored, not rounded
  assert.equal(normalizeGap(0), 4);
  assert.equal(normalizeGap(-3), 4);
  assert.equal(normalizeGap(NaN), 4);
  assert.equal(normalizeGap(Infinity), 4);
  assert.equal(normalizeGap('not a number'), 4);
});
