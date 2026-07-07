import test from 'node:test';
import assert from 'node:assert/strict';

import { roundHalfEven, overlayRect } from '../www/js/core-region-ops.js';
import { updateAtlasText, rebuildAtlasText } from '../www/js/atlas-modifier.js';

// ─── roundHalfEven — Python round() parity (banker's rounding) ────────────────
// Ground-truth cross-checked against `python3 -c "print(round(x))"` for each
// value (see task-4a-report.md): 2.5->2, 3.5->4, 0.5->0, 1.5->2, 10.5->10,
// 7.5->8, 2.4->2, 2.6->3.

test('roundHalfEven: exact .5 tie on an EVEN base rounds down (toward even)', () => {
  assert.equal(roundHalfEven(2.5), 2); // Math.round would give 3
  assert.equal(roundHalfEven(0.5), 0);
  assert.equal(roundHalfEven(10.5), 10);
});

test('roundHalfEven: exact .5 tie on an ODD base rounds up (toward even)', () => {
  assert.equal(roundHalfEven(3.5), 4);
  assert.equal(roundHalfEven(1.5), 2);
  assert.equal(roundHalfEven(7.5), 8);
});

test('roundHalfEven: non-tie values match plain rounding', () => {
  assert.equal(roundHalfEven(2.4), 2);
  assert.equal(roundHalfEven(2.6), 3);
  assert.equal(roundHalfEven(5), 5);
  assert.equal(roundHalfEven(0), 0);
  assert.equal(roundHalfEven(9.9), 10);
});

test('roundHalfEven disagrees with Math.round exactly on even-base .5 ties', () => {
  // The whole point of the helper: Math.round(2.5) === 3 but Python round is 2.
  assert.notEqual(roundHalfEven(2.5), Math.round(2.5));
  // ...and agrees on odd-base ties (both give 4).
  assert.equal(roundHalfEven(3.5), Math.round(3.5));
});

// ─── overlayRect — w/h swap only for 90/270 ───────────────────────────────────

test('overlayRect swaps w/h only for rotate 90 and 270', () => {
  const bounds = [3, 4, 20, 8];
  assert.deepEqual(overlayRect({ bounds, rotate: 0 }), [3, 4, 20, 8]);
  assert.deepEqual(overlayRect({ bounds, rotate: 180 }), [3, 4, 20, 8]);
  assert.deepEqual(overlayRect({ bounds, rotate: 90 }), [3, 4, 8, 20]);
  assert.deepEqual(overlayRect({ bounds, rotate: 270 }), [3, 4, 8, 20]);
});

// ─── updateAtlasText re-homing onto AtlasDocument ─────────────────────────────

const MULTIPAGE = `page1.png
size: 100,100
armR
bounds: 0, 0, 10, 10
armL
bounds: 10, 0, 8, 8

page2.png
size: 50,50
legR
bounds: 0, 0, 5, 5
`;

test('updateAtlasText with a targetPage rewrites only that page size + named region, leaving the other page (size and regions) untouched', () => {
  const out = updateAtlasText(MULTIPAGE, [200, 200], { armR: [[1, 2, 3, 4], null, 90] }, 'page1.png');
  const expected = [
    'page1.png',
    'size: 200,200',
    'armR',
    '  rotate: true',
    '  bounds: 1, 2, 3, 4',
    'armL',
    '  bounds: 10, 0, 8, 8',
    '',
    'page2.png',
    'size: 50,50', // NON-target page size must be preserved
    'legR',
    '  bounds: 0, 0, 5, 5',
  ].join('\n');
  assert.equal(out, expected);
});

test('updateAtlasText with targetPage=null applies the new size to every page (matches the old line-patcher rule)', () => {
  const out = updateAtlasText(MULTIPAGE, [200, 200], {}, null);
  assert.ok(out.includes('page1.png\nsize: 200,200'));
  assert.ok(out.includes('page2.png\nsize: 200,200'));
});

// ─── rebuildAtlasText re-homing onto AtlasDocument.fromRebuildArgs ─────────────

test('rebuildAtlasText builds canonical text and bridges extraPairs from {key,values} objects to tuples', () => {
  const out = rebuildAtlasText(
    { page: 'atlas.png', format: 'RGBA8888', filter: 'Nearest, Nearest', repeat: 'none', pma: false },
    [64, 64],
    ['foo', 'bar'],
    {
      foo: [
        [0, 0, 10, 10],
        null,
        90,
        { atlasName: 'foo', index: 2, split: [1, 2, 3, 4], pad: null, extraPairs: [{ key: 'orig', values: ['5', '5'] }] },
      ],
      bar: [[10, 0, 15, 15], [1, 1, 15, 15], 0, { atlasName: 'bar', index: -1 }],
    }
  );
  const expected = [
    'atlas.png',
    'size: 64,64',
    'foo',
    '  index: 2',
    '  rotate: true',
    '  bounds: 0, 0, 10, 10',
    '  split: 1, 2, 3, 4',
    '  orig: 5, 5',
    'bar',
    '  bounds: 10, 0, 15, 15',
    '  offsets: 1, 1, 15, 15',
  ].join('\n');
  assert.equal(out, expected);
});

test('rebuildAtlasText emits non-default page header lines (format/filter/repeat/pma)', () => {
  const out = rebuildAtlasText(
    { page: 'p.png', format: 'RGB565', filter: 'Linear, Linear', repeat: 'x', pma: true },
    [8, 8],
    ['r'],
    { r: [[0, 0, 4, 4], null, 0, { atlasName: 'r' }] }
  );
  assert.ok(out.includes('format: RGB565'));
  assert.ok(out.includes('filter: Linear, Linear'));
  assert.ok(out.includes('repeat: x'));
  assert.ok(out.includes('pma: true'));
});
