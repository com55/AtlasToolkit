import test from 'node:test';
import assert from 'node:assert/strict';

import { extractedCanvasSize } from '../www/js/core-region-ops.js';
import { AtlasProcessor, hullPolyline, layoutPreviewHulls } from '../www/js/atlas-extracter.js';

test('extractedCanvasSize uses the original canvas when the region has offsets', () => {
  const size = extractedCanvasSize({
    x: 1, y: 2, w: 4, h: 6, rotate: 90,
    offsets: [1, 1, 10, 12],
  });
  assert.deepEqual(size, { width: 10, height: 12 });
});

test('extractedCanvasSize keeps packed w×h when there are no offsets, even if rotated', () => {
  const size = extractedCanvasSize({
    x: 0, y: 0, w: 4, h: 6, rotate: 90, offsets: null,
  });
  assert.deepEqual(size, { width: 4, height: 6 });
});

test('extractedCanvasSize applies page scale with round-half-even', () => {
  const size = extractedCanvasSize(
    { x: 0, y: 0, w: 5, h: 5, rotate: 0, offsets: [0, 0, 5, 3] },
    { scaleX: 0.5, scaleY: 0.5 },
  );
  assert.deepEqual(size, { width: 2, height: 2 });
});

test('layoutPreviewHulls leaves each hull at its own size when forced resizing is off', () => {
  const layout = layoutPreviewHulls([
    { width: 10, height: 10, uvs: [0, 0, 1, 0, 0, 1], hullLength: 3 },
    { width: 20, height: 10, uvs: [0, 0, 1, 0, 0, 1], hullLength: 3 },
  ], false);
  assert.deepEqual(layout.frame, { width: 20, height: 10, scale: false });
  assert.equal(layout.items[0].scaleX, 1);
  assert.equal(layout.items[0].scaleY, 1);
});

test('layoutPreviewHulls stretches a smaller hull to the forced-resize frame', () => {
  const layout = layoutPreviewHulls([
    { width: 10, height: 10, uvs: [0, 0, 1, 0, 0, 1], hullLength: 3 },
    { width: 20, height: 10, uvs: [0, 0, 1, 0, 0.2, 1], hullLength: 3 },
  ], true);
  assert.equal(layout.items[0].scaleX, 2);
  assert.equal(layout.items[0].scaleY, 1);
  assert.equal(layout.items[1].scaleX, 1);
  assert.equal(layout.items[1].scaleY, 1);
  assert.deepEqual(hullPolyline(layout.items[0])[1], { x: 20, y: 0 });
});

test('hullPolyline strokes only the ordered hull, not interior vertices', () => {
  const pts = hullPolyline({
    width: 100,
    height: 50,
    scaleX: 1,
    scaleY: 1,
    uvs: [0, 0, 1, 0, 1, 1, 0, 1, 0.5, 0.5],
    hullLength: 4,
  });
  assert.equal(pts.length, 4);
  assert.deepEqual(pts[2], { x: 100, y: 50 });
  assert.equal(hullPolyline({
    width: 10, height: 10, scaleX: 1, scaleY: 1, uvs: [0, 0], hullLength: 4,
  }), null);
});

test('previewHullLayout follows mesh data even when mesh cropping is off, and skips an unloaded page', () => {
  const processor = new AtlasProcessor(`page.png
size: 20, 20
box
  bounds: 1, 2, 4, 6
  offsets: 1, 1, 10, 12
  rotate: 90

other.png
size: 10, 10
missing
  bounds: 0, 0, 3, 3
`);
  processor._loadedImages['page.png'] = {};
  processor.setMeshMaskData(new Map([
    ['box', {
      uvs: [0, 0, 1, 0, 1, 1, 0, 1, 0.5, 0.5],
      triangles: [0, 1, 4],
      hullLength: 4,
    }],
  ]), false, false);
  const layout = processor.previewHullLayout(['box', 'missing', 'nope'], false);
  assert.equal(layout.items.length, 1);
  assert.equal(layout.items[0].width, 10);
  assert.equal(layout.items[0].height, 12);
  assert.equal(hullPolyline(layout.items[0]).length, 4);
});

test('previewHullLayout drops the hull on a PMA page', () => {
  const processor = new AtlasProcessor(`page.png
size: 20, 20
pma: true
box
  bounds: 0, 0, 4, 4
`);
  processor._loadedImages['page.png'] = {};
  processor.setMeshMaskData(new Map([
    ['box', { uvs: [0, 0, 1, 0, 0, 1], triangles: [0, 1, 2], hullLength: 3 }],
  ]), false, false);
  const layout = processor.previewHullLayout(['box'], false);
  assert.equal(layout.items[0].hullLength, 0);
  assert.equal(hullPolyline(layout.items[0]), null);
});
