import test from 'node:test';
import assert from 'node:assert/strict';

import {
  PREVIEW_TOP_PAD_PX,
  previewImageTopLeft,
  previewTopAnchoredY,
  previewZoomOriginY,
} from '../www/js/platform.js';

test('small image is vertically centered in the current container', () => {
  assert.equal(previewTopAnchoredY(800, 400), 200);
  assert.equal(previewTopAnchoredY(400, 100), 150);
});

test('image that would sit above the 10px top pad is clamped to 10px', () => {
  assert.equal(PREVIEW_TOP_PAD_PX, 10);
  assert.equal(previewTopAnchoredY(100, 90), 10);
  assert.equal(previewTopAnchoredY(800, 2000), 10);
});

test('centering that already has 10px of headroom is left centered', () => {
  assert.equal(previewTopAnchoredY(100, 80), 10);
  assert.equal(previewTopAnchoredY(120, 80), 20);
});

test('top-anchored image stays at y when the container height changes (splitter)', () => {
  const imgW = 100, imgH = 400, scale = 1, x = 0, y = 10;
  const short = previewImageTopLeft(300, 200, imgW, imgH, scale, x, y, true);
  const tall = previewImageTopLeft(300, 800, imgW, imgH, scale, x, y, true);
  assert.equal(short.y, 10);
  assert.equal(tall.y, 10);
  assert.equal(short.x, tall.x);
});

test('center-anchored image stays vertically centered when y is 0', () => {
  const pos = previewImageTopLeft(300, 800, 100, 400, 1, 0, 0, false);
  assert.equal(pos.y, 200);
});

test('zoom origin Y is the container top when top-anchored', () => {
  assert.equal(previewZoomOriginY(800, true), 0);
  assert.equal(previewZoomOriginY(800, false), 400);
});
