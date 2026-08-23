import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { AtlasDocument, Page, Region } from '../www/js/atlas-document.js';

const HERE = path.dirname(fileURLToPath(import.meta.url));

// Shared fixture text used across several tests below. Covers, in one atlas:
//  - a region with an explicit but redundant `offsets:` line equal to
//    (0,0,w,h) — should serialize WITHOUT an offsets line (default-dropping)
//  - a region with genuinely non-default offsets
//  - a region rotated 90 ("true") and one rotated 180
//  - a region with an explicit `index:`
//  - two regions sharing the literal name "dagger" (dedup -> "dagger#2",
//    both keep atlasName "dagger")
//  - an unknown region key ("customKey") that must round-trip via extraPairs
//  - a second page, to exercise multi-page parsing/association
const FIXTURE = `page1.png
size: 100,100
filter: Linear, Linear
pma: true
armR
bounds: 0, 0, 10, 10
offsets: 0, 0, 10, 10
armL
index: 3
bounds: 10, 0, 8, 8
offsets: 1, 1, 12, 12
head
bounds: 0, 10, 20, 20
rotate: 90
foot
bounds: 20, 10, 5, 5
rotate: 180
dagger
bounds: 30, 10, 4, 4
customKey: foo, bar
dagger
bounds: 34, 10, 4, 4

page2.png
size: 50,50
regionA
bounds: 0, 0, 5, 5
regionB
bounds: 5, 0, 5, 5
`;

// Expected canonical serialization of FIXTURE. Hand-computed, then verified
// against a direct run of the real `document.py` (extracted from
// `git show main:atlas_toolkit/core/document.py`) via a standalone Python
// script — see task-2-report.md for the transcript. Notably, the unknown
// region key `customKey` comes back out as lowercase `customkey`, because
// both the Python source and this port lowercase keys during parsing before
// stashing them in extraPairs — that quirk is intentional/verified, not a
// typo.
const FIXTURE_SERIALIZED = [
  'page1.png',
  'size: 100,100',
  'filter: Linear, Linear',
  'pma: true',
  'armR',
  '  bounds: 0, 0, 10, 10',
  'armL',
  '  index: 3',
  '  bounds: 10, 0, 8, 8',
  '  offsets: 1, 1, 12, 12',
  'head',
  '  rotate: true',
  '  bounds: 0, 10, 20, 20',
  'foot',
  '  rotate: 180',
  '  bounds: 20, 10, 5, 5',
  'dagger',
  '  bounds: 30, 10, 4, 4',
  '  customkey: foo, bar',
  'dagger',
  '  bounds: 34, 10, 4, 4',
  '',
  'page2.png',
  'size: 50,50',
  'regionA',
  '  bounds: 0, 0, 5, 5',
  'regionB',
  '  bounds: 5, 0, 5, 5',
].join('\n');

test('parse -> serialize round-trip matches hand-computed (Python-cross-checked) output', () => {
  const doc = AtlasDocument.parse(FIXTURE);
  assert.equal(doc.serialize(), FIXTURE_SERIALIZED);
});

test('default-offsets region drops the offsets line even though the input had an explicit redundant one', () => {
  const doc = AtlasDocument.parse(FIXTURE);
  const armR = doc.pages[0].regions.find((r) => r.name === 'armR');
  assert.ok(armR, 'armR region should exist');
  // Parsed value is preserved on the model...
  assert.deepEqual(armR.offsets, [0, 0, 10, 10]);
  // ...but serialize() must not emit it, since it equals (0,0,w,h).
  assert.ok(!doc.serialize().includes('offsets: 0, 0, 10, 10'));
});

test('non-default-offsets region keeps its offsets line', () => {
  const doc = AtlasDocument.parse(FIXTURE);
  assert.ok(doc.serialize().includes('  offsets: 1, 1, 12, 12'));
});

test('rotate 90 serializes as "true" and 180 as literal "180"', () => {
  const doc = AtlasDocument.parse(FIXTURE);
  const head = doc.pages[0].regions.find((r) => r.name === 'head');
  const foot = doc.pages[0].regions.find((r) => r.name === 'foot');
  assert.equal(head.rotate, 90);
  assert.equal(foot.rotate, 180);
  const text = doc.serialize();
  assert.ok(text.includes('  rotate: true'));
  assert.ok(text.includes('  rotate: 180'));
});

test('explicit index round-trips and index:-1 (default) is omitted', () => {
  const doc = AtlasDocument.parse(FIXTURE);
  const armL = doc.pages[0].regions.find((r) => r.name === 'armL');
  assert.equal(armL.index, 3);
  const armR = doc.pages[0].regions.find((r) => r.name === 'armR');
  assert.equal(armR.index, -1);
  const text = doc.serialize();
  assert.ok(text.includes('  index: 3'));
  assert.ok(!text.includes('index: -1'));
});

test('region name collision numbering: second "dagger" gets internal key "dagger#2", both keep atlasName "dagger"', () => {
  const doc = AtlasDocument.parse(FIXTURE);
  const daggers = doc.pages[0].regions.filter((r) => r.atlasName === 'dagger');
  assert.equal(daggers.length, 2);
  assert.equal(daggers[0].name, 'dagger');
  assert.equal(daggers[1].name, 'dagger#2');
  assert.equal(daggers[0].atlasName, 'dagger');
  assert.equal(daggers[1].atlasName, 'dagger');
  // Both serialize back out under the literal name "dagger", not "dagger#2".
  const text = doc.serialize();
  assert.equal((text.match(/^dagger$/gm) || []).length, 2);
  assert.ok(!text.includes('dagger#2'));
});

test('multi-page atlas parses into the correct number of Page objects with correct region-to-page association', () => {
  const doc = AtlasDocument.parse(FIXTURE);
  assert.equal(doc.pages.length, 2);
  assert.equal(doc.pages[0].filename, 'page1.png');
  assert.equal(doc.pages[1].filename, 'page2.png');
  assert.equal(doc.pages[0].regions.length, 6); // armR, armL, head, foot, dagger, dagger#2
  assert.equal(doc.pages[1].regions.length, 2); // regionA, regionB
  for (const region of doc.pages[0].regions) {
    assert.equal(region.pageFilename, 'page1.png');
  }
  for (const region of doc.pages[1].regions) {
    assert.equal(region.pageFilename, 'page2.png');
  }
});

test('unknown region key round-trips unchanged through parse -> serialize (as a lowercased extraPair)', () => {
  const doc = AtlasDocument.parse(FIXTURE);
  const dagger = doc.pages[0].regions.find((r) => r.name === 'dagger');
  assert.deepEqual(dagger.extraPairs, [['customkey', ['foo', 'bar']]]);
  assert.ok(doc.serialize().includes('  customkey: foo, bar'));
});

test('unknown page key round-trips unchanged through parse -> serialize', () => {
  const text = `page1.png
size: 10,10
mystery: 1, 2, 3
region
bounds: 0, 0, 5, 5
`;
  const doc = AtlasDocument.parse(text);
  assert.deepEqual(doc.pages[0].extraPairs, [['mystery', ['1', '2', '3']]]);
  assert.ok(doc.serialize().includes('mystery: 1, 2, 3'));
});

test('withUpdates replaces bounds/offsets/rotate for named regions and applies pageSize, leaving the rest untouched', () => {
  const text = `page1.png
size: 100,100
armR
bounds: 0, 0, 10, 10
`;
  const doc = AtlasDocument.parse(text);
  const updated = doc.withUpdates(
    { armR: [[5, 5, 20, 20], [1, 1, 25, 25], 90] },
    [200, 200]
  );

  const expected = [
    'page1.png',
    'size: 200,200',
    'armR',
    '  rotate: true',
    '  bounds: 5, 5, 20, 20',
    '  offsets: 1, 1, 25, 25',
  ].join('\n');
  assert.equal(updated.serialize(), expected);

  // Original document must be unaffected (withUpdates returns a new doc).
  assert.equal(doc.pages[0].size[0], 100);
  assert.equal(doc.pages[0].regions[0].x, 0);
});

test('fromRebuildArgs builds a fresh document whose serialize() matches a hand-computed (Python-cross-checked) expectation', () => {
  const pageInfo = {
    page: 'atlas2.png',
    format: 'RGBA8888',
    filter: 'Nearest, Nearest',
    repeat: 'none',
    pma: false,
  };
  const regionData = {
    foo: [
      [0, 0, 10, 10],
      null,
      0,
      {
        index: 2,
        split: [1, 2, 3, 4],
        pad: [0, 0, 0, 0],
        extraPairs: [['origin', ['5', '5']]],
        atlasName: 'foo',
      },
    ],
    bar: [[10, 0, 15, 15], [0, 0, 15, 15], 0],
  };
  const doc = AtlasDocument.fromRebuildArgs(pageInfo, [300, 300], ['foo', 'bar'], regionData);

  const expected = [
    'atlas2.png',
    'size: 300,300',
    'foo',
    '  index: 2',
    '  bounds: 0, 0, 10, 10',
    '  split: 1, 2, 3, 4',
    '  pad: 0, 0, 0, 0',
    '  origin: 5, 5',
    'bar',
    '  bounds: 10, 0, 15, 15',
  ].join('\n');
  assert.equal(doc.serialize(), expected);
});

test('Region helper methods: bounds, boundsWithRotate, toMetaDict', () => {
  const r = new Region('leg', 'leg', 'page1.png', {
    x: 1,
    y: 2,
    w: 3,
    h: 4,
    rotate: 90,
    index: 7,
    split: [1, 1, 1, 1],
    pad: null,
    extraPairs: [['foo', ['bar']]],
  });
  assert.deepEqual(r.bounds, [1, 2, 3, 4]);
  assert.deepEqual(r.boundsWithRotate(), [1, 2, 3, 4, 90]);
  assert.deepEqual(r.toMetaDict(), {
    atlasName: 'leg',
    index: 7,
    split: [1, 1, 1, 1],
    pad: null,
    extraPairs: [['foo', ['bar']]],
  });
});

test('a line ending in .png (case-sensitive) starts a new page; case-insensitive matches like ".PNG" do not', () => {
  const text = `weird.PNG
notreallyapage
page1.png
size: 10,10
region
bounds: 0, 0, 1, 1
`;
  const doc = AtlasDocument.parse(text);
  // "weird.PNG" does not end in exactly ".png" (case-sensitive), so it is
  // not treated as a page header, nor as a region (no page open yet) --
  // matches Python's `line.endswith(".png")` exactly, and intentionally
  // differs from atlas-extracter.js's case-insensitive /\.png$/i regex.
  assert.equal(doc.pages.length, 1);
  assert.equal(doc.pages[0].filename, 'page1.png');
});

test('empty document parses to zero pages', () => {
  const doc = AtlasDocument.parse('');
  assert.equal(doc.pages.length, 0);
  assert.equal(doc.serialize(), '');
});

test('legacy xy:/size: region keys (pre-bounds format) are still supported', () => {
  const text = `page1.png
size: 100,100
leg
xy: 3, 4
size: 20, 30
`;
  const doc = AtlasDocument.parse(text);
  const leg = doc.pages[0].regions[0];
  assert.equal(leg.x, 3);
  assert.equal(leg.y, 4);
  assert.equal(leg.w, 20);
  assert.equal(leg.h, 30);
});

test('malformed known region keys (fewer than 4 values) fall through to extraPairs under that key -- ported by inspection from document.py\'s if/elif-else chain, not run against live Python', () => {
  const text = `page1.png
size: 100,100
leg
bounds: 0, 0, 10, 10
offsets: 1, 2
split: 1, 2
pad: 1, 2
`;
  const doc = AtlasDocument.parse(text);
  const leg = doc.pages[0].regions[0];
  assert.equal(leg.offsets, null);
  assert.equal(leg.split, null);
  assert.equal(leg.pad, null);
  assert.deepEqual(leg.extraPairs, [
    ['offsets', ['1', '2']],
    ['split', ['1', '2']],
    ['pad', ['1', '2']],
  ]);
});

test('a malformed known-but-guarded page key ("filter:" with fewer than 2 values) is silently dropped, NOT stashed in extraPairs -- ported by inspection, not run against live Python', () => {
  // Only "filter" is guarded with an explicit `len(values) >= 2` check on
  // the page side in document.py; a short "filter:" line fails that guard
  // and then also fails the "not in known-keys" check (since "filter" IS a
  // known key), so it is dropped entirely -- unlike the region side, where
  // the same shape of short line for a known key falls through to
  // extraPairs (see the region-level malformed-key test above). Note:
  // other page keys like "size"/"format"/"repeat" have NO such guard in
  // document.py, so malformed short values there would raise in Python;
  // this test intentionally only exercises the one page key that is
  // actually guarded, to avoid asserting behavior for an input shape the
  // Python source doesn't handle gracefully either.
  const text = `page1.png
size: 100,100
filter: Nearest
leg
bounds: 0, 0, 10, 10
`;
  const doc = AtlasDocument.parse(text);
  const page = doc.pages[0];
  assert.deepEqual(page.filter, ['Nearest', 'Nearest']); // unchanged from default
  assert.deepEqual(page.extraPairs, []);
});

// ─── Task 5 Deliverable 3: round-trip the two checked-in oracle fixtures ───
// against live-Python-computed (Python-cross-checked) canonical output.
// Task 2's own round-trip coverage above already cross-checks a general
// hand-built fixture against live Python; this specifically closes the one
// gap the Task 5 brief calls out -- the two REAL, checked-in Deliverable-1
// fixtures (test/browser/fixtures/) hadn't themselves been round-trip-
// checked against Python's AtlasDocument.serialize(). Expected strings below
// were produced by running the actual pinned main@d1f196e document.py's
// AtlasDocument.parse(text).serialize() against these exact fixture files
// (see task-5-report.md for the one-off generation command); this test does
// NOT shell out to Python at run time, matching Task 2's established
// pure-Node convention for `node --test`.

test('fixtures/opaque-transparent/fixture-opaque.atlas parse->serialize matches live-Python (main@d1f196e) output', () => {
  const text = fs.readFileSync(
    path.join(HERE, 'browser', 'fixtures', 'opaque-transparent', 'fixture-opaque.atlas'),
    'utf8',
  );
  const got = AtlasDocument.parse(text).serialize();
  const expected = `fixture-opaque.png
size: 32,10
plain
  bounds: 0, 0, 10, 10
withOffsets
  bounds: 10, 0, 8, 6
  offsets: 2, 2, 16, 10
dupeA
  bounds: 20, 0, 6, 6
dupeB
  bounds: 26, 0, 6, 6`;
  assert.equal(got, expected);
});

test('fixtures/tie-rounding/fixture-tie.atlas parse->serialize matches live-Python (main@d1f196e) output', () => {
  const text = fs.readFileSync(
    path.join(HERE, 'browser', 'fixtures', 'tie-rounding', 'fixture-tie.atlas'),
    'utf8',
  );
  const got = AtlasDocument.parse(text).serialize();
  const expected = `fixture-tie.png
size: 14,14
tieRegion
  bounds: 0, 0, 6, 6
  offsets: 0, 0, 7, 7`;
  assert.equal(got, expected);
});
