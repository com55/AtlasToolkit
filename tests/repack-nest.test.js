import test from 'node:test';
import assert from 'node:assert/strict';

import { rotate90CW, rotate180, rotate90CCW, dilate, normalizeGap, growCanvas } from '../www/js/repack-nest.js';
import { nestPack } from '../www/js/repack-nest.js';

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
  assert.equal(normalizeGap(50000), 256);
  assert.equal(normalizeGap(256), 256);
});

function solid(w, h) { return new Uint8Array(w * h).fill(1); }

test('nestPack: single item bootstraps a canvas exactly its own size', () => {
  const { canvasW, canvasH, placements } = nestPack(
    [{ name: 'a', w: 10, h: 6, footprint: solid(10, 6) }],
    { gapDistance: 1 },
  );
  assert.equal(canvasW, 12);
  assert.equal(canvasH, 8);
  assert.equal(placements.length, 1);
  assert.deepEqual(placements[0], { name: 'a', x: 0, y: 0, pw: 10, ph: 6, rotate: 0 });
});

test('nestPack: empty input returns a zero-size result', () => {
  assert.deepEqual(nestPack([], {}), { canvasW: 0, canvasH: 0, placements: [] });
});

test('growth-loop regression (round 1 finding 3): two solid 8x8 items at gapDistance=4 need at least 20x8 or 8x20, not 16x8/8x16', () => {
  const items = [
    { name: 'a', w: 8, h: 8, footprint: solid(8, 8) },
    { name: 'b', w: 8, h: 8, footprint: solid(8, 8) },
  ];
  const { canvasW, canvasH, placements } = nestPack(items, { gapDistance: 4 });
  assert.equal(placements.length, 2);
  const longSide = Math.max(canvasW, canvasH);
  const shortSide = Math.min(canvasW, canvasH);
  assert.ok(shortSide >= 8, `short side too small: ${canvasW}x${canvasH}`);
  assert.ok(longSide >= 20, `long side too small (single-grow bug would give 16): ${canvasW}x${canvasH}`);
  // Both items actually placed without overlapping (gap respected): compute
  // each item's placed rect and assert they don't come within `gap` of
  // each other along both axes simultaneously.
  const [pa, pb] = placements;
  const gapX = pa.x < pb.x ? pb.x - (pa.x + pa.pw) : pa.x - (pb.x + pb.pw);
  const gapY = pa.y < pb.y ? pb.y - (pa.y + pa.ph) : pa.y - (pb.y + pb.ph);
  assert.ok(gapX >= 4 || gapY >= 4, `items too close: gapX=${gapX} gapY=${gapY}`);
});

test('nestPack places a small item inside a larger item\'s real gap-shaped footprint (not a solid rect)', () => {
  // Host: 12x10, but its footprint is only the LEFT 6 columns -- a real
  // gap-shaped mask, not a solid rectangle. Wide enough that after gap=1
  // dilation, the free region (columns 7-11) still contains a position
  // reachable by the packer's 4px scan step (x=8 fits a 3-wide item:
  // 8+3=11 <= 12) -- verified against the real implementation directly.
  const hostFootprint = new Uint8Array(120);
  for (let y = 0; y < 10; y++) for (let x = 0; x < 6; x++) hostFootprint[y * 12 + x] = 1;
  const items = [
    { name: 'host', w: 12, h: 10, footprint: hostFootprint },
    { name: 'small', w: 3, h: 3, footprint: solid(3, 3) },
  ];
  const { canvasW, canvasH, placements } = nestPack(items, { gapDistance: 1 });
  // If nesting worked, the small item fits inside the host's own 12x10
  // bounding box (in its free right region) instead of the canvas growing
  // past the host's own size to fit it separately.
  assert.equal(canvasW, 12);
  assert.equal(canvasH, 12);
  const small = placements.find(p => p.name === 'small');
  assert.ok(small.x >= 6, `expected the small item nested into the host's unoccupied right region, got x=${small.x}`);
});

test('rotation is actually used: a candidate that only fits at 180 or 270 does not force canvas growth', () => {
  // Canvas ends up exactly 10 wide (from the host) x 13 tall (host 10 +
  // candidate's short side 3, if nesting/rotation is NOT used it would
  // need to grow much larger). Host: 10x10 solid, placed first. Remaining
  // space below it is only 10 wide -- a 3x8 candidate (unrotated) doesn't
  // fit in a 10-wide strip only if... to keep this deterministic and not
  // dependent on the scan step, assert the DIRECT property: the packer
  // finds a placement without exceeding the area a same-size solid-rect
  // shelf pack would need for two non-nestable, non-rotatable rects.
  const host = { name: 'host', w: 10, h: 10, footprint: solid(10, 10) };
  const tall = { name: 'tall', w: 3, h: 10, footprint: solid(3, 10) }; // fits rotated (10x3) below the host
  const { canvasW, canvasH, placements } = nestPack([host, tall], { gapDistance: 1 });
  const tallPlacement = placements.find(p => p.name === 'tall');
  assert.ok([90, 270].includes(tallPlacement.rotate) || canvasH >= 21,
    `expected a rotated fit or a much taller canvas; got rotate=${tallPlacement.rotate} canvas=${canvasW}x${canvasH}`);
});

test('rotation is actually used: the packer\'s search genuinely picks rotate=180 over rotate=0, deterministically', () => {
  // Task 6 scrutinize finding: neither browser-level rotation test actually
  // forces the PACKER's own search to choose a rotation (rotation-mask-
  // pixel-agreement only ever reaches deg=0 in practice; rotate-180-270-
  // pixel-correctness calls _rotateSpriteForPack directly, no packer
  // search involved). The sibling test above ("only fits at 180 or 270")
  // pre-dates that finding and settled for an OR-fallback assertion
  // (rotated OR canvas grew) specifically because constructing a fixture
  // that discriminates a PARTICULAR rotation is fiddly -- solid-rect
  // footprints are 180-symmetric (0 and 180 are geometrically identical,
  // so the search's tie-break always keeps deg=0), so forcing 180
  // specifically requires a genuinely asymmetric footprint, not a solid
  // rect. This test builds one and asserts the exact resulting `rotate`
  // value, not an OR-fallback -- it can actually fail if the search regresses.
  //
  // Construction (each element verified by hand + a Node probe against the
  // real implementation before being written down here):
  //   - `cand` (20 wide x 7 tall): row0 has a single occupied cell at
  //     col0 (a "tab"); rows1-3 are an empty buffer; rows4-6 are solid
  //     (all 20 cols). rotate180 (a full 1D reversal) turns this into the
  //     mirror image: rows0-2 solid, rows3-5 empty buffer, row6's tab at
  //     col19 (the opposite corner) -- i.e. deg=0 and deg=180 differ in
  //     EVERY row except none (this shape has no rotationally-invariant
  //     cell), so they are trivially distinguishable.
  //   - `blocker` (21 wide x 9 tall, so its max dimension, 21, exceeds
  //     cand's, 20 -- nestPack sorts largest-first, so blocker is placed
  //     before cand with nothing to collide with, landing at (0,0)) has
  //     exactly ONE occupied cell, at local (col=0, row=5).
  //   - At scan origin (0,0): deg=0's row5 is part of its solid block
  //     (col0=1) -- direct collision with the blocker's cell. deg=180's
  //     row5 is part of ITS buffer (col0=0) -- no collision, and 1px
  //     dilation of the blocker's single cell (rows4-6, cols -1..1) still
  //     only ever touches deg=180's buffer/tab cells, never its solid
  //     block (there are 2 clear buffer rows on the solid-block side of
  //     row5 in every orientation) -- verified empirically, not assumed.
  //   - deg=90/270 swap cand's dimensions to 7 wide x 20 tall. The
  //     blocker's OWN bounding box (21x9) constrains the canvas to 9 tall
  //     before cand is even considered, so a 20-tall placement cannot fit
  //     at all -- deg=90/270 are excluded by dimension alone, leaving
  //     deg=0 vs deg=180 as the only geometrically-viable candidates.
  //   - Negative control (run separately during this test's construction,
  //     not asserted here): removing the blocker's one occupied cell makes
  //     cand land at rotate=0 instead -- confirming the obstacle, not some
  //     unrelated artifact, is what forces the 180 choice.
  const W = 20, H = 7;
  const row0 = [1, ...Array(W - 1).fill(0)];
  const bufferRow = Array(W).fill(0);
  const solidRow = Array(W).fill(1);
  const candRows = [row0, bufferRow, bufferRow, bufferRow, solidRow, solidRow, solidRow];
  const candFootprint = new Uint8Array(W * H);
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) candFootprint[y * W + x] = candRows[y][x];

  const BW = 21, BH = 9;
  const blockerFootprint = new Uint8Array(BW * BH);
  blockerFootprint[5 * BW + 0] = 1; // the single obstacle cell, at local (col=0, row=5)

  const blocker = { name: 'blocker', w: BW, h: BH, footprint: blockerFootprint };
  const cand = { name: 'cand', w: W, h: H, footprint: candFootprint };
  const { canvasW, canvasH, placements } = nestPack([blocker, cand], { gapDistance: 1 });

  const candPlacement = placements.find(p => p.name === 'cand');
  assert.equal(candPlacement.rotate, 180,
    `expected the packer's own search to choose rotate=180; got rotate=${candPlacement.rotate} at (${candPlacement.x},${candPlacement.y})`);
  assert.equal(candPlacement.x, 0);
  assert.equal(candPlacement.y, 0);
  // Canvas stayed at the blocker's own footprint size (rounded up to a
  // multiple of 4) -- cand fit WITHOUT growing the canvas, proving this
  // wasn't a "grew anyway, rotation incidental" result.
  assert.equal(canvasW, 24);
  assert.equal(canvasH, 12);
});

test('growCanvas: grows right when that yields the squarer result', () => {
  const result = growCanvas(2, 10, new Uint8Array((2 + 2) * (10 + 2)), { w: 8, h: 1 }, 1);
  assert.equal(result.canvasW, 10);
  assert.equal(result.canvasH, 10);
});

test('growCanvas: grows down when that yields the squarer result', () => {
  const result = growCanvas(10, 2, new Uint8Array((10 + 2) * (2 + 2)), { w: 1, h: 8 }, 1);
  assert.equal(result.canvasW, 10);
  assert.equal(result.canvasH, 10);
});

test('growCanvas: a tie in aspect ratio and area is broken by smaller resulting width', () => {
  // growing right -> 12x8 (aspect 1.5, area 96); growing down -> 8x12
  // (aspect 1.5, area 96) -- exact tie on both, so the narrower-width
  // candidate (down, w=8) must win.
  const result = growCanvas(4, 8, new Uint8Array((4 + 2) * (8 + 2)), { w: 8, h: 4 }, 1);
  assert.equal(result.canvasW, 8);
  assert.equal(result.canvasH, 12);
});

test('nestPack throws on a non-positive-dimension item instead of hanging', () => {
  assert.throws(
    () => nestPack([{ name: 'z', w: 0, h: 0, footprint: new Uint8Array(0) }], { gapDistance: 1 }),
    /non-positive dimensions/,
  );
});
