import test from 'node:test';
import assert from 'node:assert/strict';

import {
  canvasSizeMatch,
  resolveModCanvas,
  selectedShareCanvas,
  pickAnchorOffsets,
  findBestPlacement,
  repackOffsetsForRegion,
} from '../www/js/atlas-modifier.js';

// These cover the PURE canvas-resolution / placement / repack-offset decisions
// ported from modifier.py::_resolve_mod_canvas / _canvas_size_match /
// _find_best_placement and repacker.py::repack_from_sprites. The canvas-pixel
// paths (merge/repack) need a DOM and are covered by the browser harness.
//
// Ground truth for the resolution cases was cross-checked against a standalone
// run of the real main-branch _resolve_mod_canvas (see task-4b-report.md):
// is_full_canvas comes back True for every selection containing offsets, so the
// anchor off_x/off_y always resolve to 0 and base_orig_* keeps the first-
// selected canvas size.

// ─── canvasSizeMatch — 2px / 2% tolerance boundary ────────────────────────────

test('canvasSizeMatch: floor is 2px on small canvases (exactly-2 matches, 3 fails)', () => {
  // 100x100 -> round(100*0.02) = 2, so max(2,2) = 2px tolerance.
  assert.equal(canvasSizeMatch(102, 100, 100, 100), true);  // dw=2 <= 2
  assert.equal(canvasSizeMatch(100, 98, 100, 100), true);   // dh=2 <= 2
  assert.equal(canvasSizeMatch(103, 100, 100, 100), false); // dw=3 > 2
  assert.equal(canvasSizeMatch(100, 103, 100, 100), false); // dh=3 > 2
});

test('canvasSizeMatch: tolerance scales to 2% on larger canvases', () => {
  // 500x500 -> round(500*0.02) = 10px tolerance.
  assert.equal(canvasSizeMatch(510, 500, 500, 500), true);  // dw=10 <= 10
  assert.equal(canvasSizeMatch(511, 500, 500, 500), false); // dw=11 > 10
  assert.equal(canvasSizeMatch(500, 490, 500, 500), true);  // dh=10 <= 10
});

test('canvasSizeMatch: non-positive canvas dims never match', () => {
  assert.equal(canvasSizeMatch(10, 10, 0, 10), false);
  assert.equal(canvasSizeMatch(10, 10, 10, -1), false);
});

test('canvasSizeMatch: banker-rounded tolerance (0.5 tie rounds to even)', () => {
  // 125*0.02 = 2.5 -> roundHalfEven -> 2, so max(2,2) = 2px (Math.round would give 3).
  assert.equal(canvasSizeMatch(128, 125, 125, 125), false); // dw=3 > 2
  assert.equal(canvasSizeMatch(127, 125, 125, 125), true);  // dw=2 <= 2
});

// ─── pickAnchorOffsets — min (offX+offY, offX, offY) tuple ─────────────────────

test('pickAnchorOffsets: picks the smallest offX+offY sum', () => {
  assert.deepEqual(pickAnchorOffsets([[10, 10, 100, 100], [4, 4, 100, 100], [7, 2, 100, 100]]), [4, 4]);
});

test('pickAnchorOffsets: ties on sum broken by offX then offY', () => {
  // both sum to 8; (4,4) has smaller offX than (6,2).
  assert.deepEqual(pickAnchorOffsets([[6, 2, 200, 100], [4, 4, 200, 100]]), [4, 4]);
  // both sum to 8, same offX 3; smaller offY (3<5) wins.
  assert.deepEqual(pickAnchorOffsets([[3, 5, 50, 50], [3, 3, 50, 50]]), [3, 3]);
});

// ─── resolveModCanvas ─────────────────────────────────────────────────────────

test('resolveModCanvas: canvas-size disagreement -> FIRST-selected-with-offsets wins (deterministic deviation from Python hash order)', () => {
  // r1 canvas 200x100, r2 canvas 150x80 (different) — first selected is r1.
  const r = resolveModCanvas([[10, 10, 200, 100], [4, 4, 150, 80]], 60, 40);
  assert.equal(r.baseOrigW, 200);
  assert.equal(r.baseOrigH, 100);
});

test('resolveModCanvas: shared-canvas selection is full-canvas; anchor offsets zeroed', () => {
  // Two regions, same canvas size -> sharedCanvas (loose) true -> isFullCanvas.
  const r = resolveModCanvas([[10, 5, 100, 100], [4, 4, 100, 100]], 100, 100);
  assert.equal(r.isFullCanvas, true);
  assert.equal(r.offX, 0);
  assert.equal(r.offY, 0);
  assert.equal(r.origCanvasW, 100);
  assert.equal(r.origCanvasH, 100);
});

test('resolveModCanvas: single region with offsets is still full-canvas (matches real Python)', () => {
  // A cropped 60x40 mod against a 100x100 logical canvas — Python resolves this
  // to full-canvas anyway (padding branch is dead), so offsets zero out.
  const r = resolveModCanvas([[10, 5, 100, 100]], 60, 40);
  assert.equal(r.isFullCanvas, true);
  assert.equal(r.offX, 0);
  assert.equal(r.offY, 0);
  assert.equal(r.origCanvasW, 60);
  assert.equal(r.origCanvasH, 40);
});

// ─── selectedShareCanvas — strict flag (differs from loose sharedCanvas) ───────

test('selectedShareCanvas: all regions same canvas size and >1 region -> true', () => {
  assert.equal(selectedShareCanvas([[0, 0, 100, 100], [5, 5, 100, 100]]), true);
});

test('selectedShareCanvas: ANY region missing offsets -> false (the strict/loose distinction)', () => {
  // resolveModCanvas would call THIS a shared canvas (loose: 1 distinct size
  // among the regions that have offsets), but the strict check is false because
  // one region has no offsets. sharedCanvasMod uses the strict result.
  assert.equal(selectedShareCanvas([[0, 0, 100, 100], null]), false);
});

test('selectedShareCanvas: differing canvas sizes -> false; single region -> false', () => {
  assert.equal(selectedShareCanvas([[0, 0, 100, 100], [0, 0, 80, 80]]), false);
  assert.equal(selectedShareCanvas([[0, 0, 100, 100]]), false);
});

// ─── findBestPlacement — allowRotate gate ─────────────────────────────────────

test('findBestPlacement: allowRotate=false forbids a rotated placement that would otherwise win', () => {
  // A tall skinny mod (20x200) against a wide base (200x50): rotating it to
  // 200x20 packs to the right far cheaper, so rotation wins when allowed.
  const allowed = findBestPlacement(200, 50, 20, 200, true);
  assert.equal(allowed.rotated, true);

  // With rotation disabled (shared-canvas mod), the best NON-rotated option is chosen.
  const forbidden = findBestPlacement(200, 50, 20, 200, false);
  assert.equal(forbidden.rotated, false);
});

test('findBestPlacement: defaults to allowRotate=true', () => {
  const def = findBestPlacement(200, 50, 20, 200);
  assert.equal(def.rotated, true);
});

// ─── repackOffsetsForRegion — offsets-reset vs preserved asymmetry ─────────────

test('repackOffsetsForRegion: full-canvas region -> default (0,0,w,h) so serializer omits it', () => {
  const full = new Set(['hero']);
  assert.deepEqual(repackOffsetsForRegion('hero', full, [3, 4, 50, 60], 40, 40), [0, 0, 40, 40]);
});

test('repackOffsetsForRegion: non-full-canvas region -> pristine offsets preserved verbatim', () => {
  const full = new Set(['hero']);
  assert.deepEqual(repackOffsetsForRegion('sword', full, [3, 4, 50, 60], 40, 40), [3, 4, 50, 60]);
});

test('repackOffsetsForRegion: null/empty fullCanvasRegions preserves offsets (legacy repack path)', () => {
  assert.deepEqual(repackOffsetsForRegion('sword', null, [3, 4, 50, 60], 40, 40), [3, 4, 50, 60]);
  assert.deepEqual(repackOffsetsForRegion('sword', new Set(), [1, 2, 9, 9], 40, 40), [1, 2, 9, 9]);
  // A non-full region with no pristine offsets stays null.
  assert.equal(repackOffsetsForRegion('sword', new Set(['hero']), null, 40, 40), null);
});
