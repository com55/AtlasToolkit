import test from 'node:test';
import assert from 'node:assert/strict';

import {
  canvasSizeMatch,
  resolveModCanvas,
  selectedShareCanvas,
  pickAnchorOffsets,
  findBestPlacement,
  repackOffsetsForRegion,
  _shelfPack,
  maskCropRectForOffsets,
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

// ─── _shelfPack — square-ish tie-break among equal-area candidates ────────────

test('_shelfPack: prefers a squarer layout over an elongated one at the same area', () => {
  // Six identical 20x20 squares tile perfectly (zero waste) in several
  // shapes -- 1x6, 2x3, 3x2, 6x1 -- all at the exact same minimum area
  // (2400). The OLD algorithm (pure minimum area, first-found-wins on ties,
  // width candidates tried ascending) picked the first width tried (20),
  // landing on a 20x120 layout (aspect 6.0) -- this is the exact class of
  // "expands out long and thin unnecessarily" behavior reported by the
  // user. The new algorithm must pick the squarest among the tied-minimum
  // candidates instead: 40x60 (aspect 1.5), at the identical area.
  const items = Array.from({ length: 6 }, (_, i) => ({ name: `s${i}`, w: 20, h: 20 }));
  const result = _shelfPack(items);
  assert.equal(result.canvasW, 40);
  assert.equal(result.canvasH, 60);
  assert.equal(result.canvasW * result.canvasH, 2400); // no worse than the minimum possible area
});

test('_shelfPack: unaffected when the minimum-area candidate is already square-ish', () => {
  // Three 60x10 strips: the minimum-area candidate (60 wide, 3 rows stacked
  // -> 60x30, aspect 2.0) is already the squarest among every tied-minimum
  // candidate, so squareness selection changes nothing here -- basic
  // no-regression check alongside the tie-break test above.
  const items = [
    { name: 'a', w: 60, h: 10 },
    { name: 'b', w: 60, h: 10 },
    { name: 'c', w: 60, h: 10 },
  ];
  const result = _shelfPack(items);
  assert.equal(result.canvasW, 60);
  assert.equal(result.canvasH, 32); // 30 rounded up to the nearest multiple of 4
});

test('_shelfPack: pays up to 15% extra area for a genuinely squarer layout, but not more', () => {
  // Seven identical 20x20 squares. The pure-minimum-area layout is a single
  // 140x20 (or equivalently 20x140) row/column -- area 2800, aspect 7.0,
  // exactly what the OLD algorithm picked (first-found on ties, and this
  // area is achieved at both extremes of the candidate-width search). A
  // 40x80 grid (4 rows of ~2) costs 3200 -- 14.29% more than the minimum,
  // just inside the 15% budget -- but has a far better aspect (2.0). A
  // 60x60 grid (3 rows of ~3, the mathematically perfect square, aspect
  // 1.0) costs 3600 -- 28.57% more than the minimum -- which is OUTSIDE
  // the 15% budget, so it must NOT be picked despite being the squarest
  // theoretically achievable layout. This test pins both halves: the
  // algorithm spends the budget it's allowed, and refuses to spend past it.
  const items = Array.from({ length: 7 }, (_, i) => ({ name: `s${i}`, w: 20, h: 20 }));
  const result = _shelfPack(items);
  assert.equal(result.canvasW, 40);
  assert.equal(result.canvasH, 80);
  assert.equal(result.canvasW * result.canvasH, 3200);
});

test('maskCropRectForOffsets: no offset (offX=offY=0) crops from the mesh space origin', () => {
  // origW=100, origH=100, sprite is a 40x30 raw crop with zero offsets.
  const rect = maskCropRectForOffsets([0, 0, 100, 100], 40, 30);
  assert.deepEqual(rect, { x: 0, y: 100 - 0 - 30, w: 40, h: 30 }); // { x: 0, y: 70, w: 40, h: 30 }
});

test('maskCropRectForOffsets: nonzero offX/offY use the bottom-origin paste formula', () => {
  // origW=200, origH=150, offX=20, offY=10, raw crop is 50x40.
  // pasteY = origH - offY - h = 150 - 10 - 40 = 100.
  const rect = maskCropRectForOffsets([20, 10, 200, 150], 50, 40);
  assert.deepEqual(rect, { x: 20, y: 100, w: 50, h: 40 });
});

test('maskCropRectForOffsets: sprite occupying the full offsets canvas crops from (offX, offY)', () => {
  // A raw crop exactly as tall as origH with offY=0 crops from y=0 -- the
  // top of the mesh space, matching what pasting the same sprite back
  // at pasteY = origH - 0 - h = 0 would look like.
  const rect = maskCropRectForOffsets([5, 0, 80, 60], 80, 60);
  assert.deepEqual(rect, { x: 5, y: 0, w: 80, h: 60 });
});
