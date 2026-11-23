// SPDX-FileCopyrightText: 2026 com55
// SPDX-License-Identifier: Apache-2.0
import test from 'node:test';
import assert from 'node:assert/strict';
import { pinchView } from '../www/js/platform.js';

// Midpoints are relative to the zoom origin (container centre).
const near = (a, b) => assert.ok(Math.abs(a - b) < 1e-9, `${a} != ${b}`);

test('two fingers moving together pan the view by the midpoint movement', () => {
  const v = pinchView({x: 5, y: -3, scale: 2}, {dist: 100, mid: {x: 10, y: 10}}, {dist: 100, mid: {x: 40, y: -5}});
  near(v.scale, 2); near(v.x, 35); near(v.y, -18);
});

test('spreading fingers zooms around the midpoint, keeping the point under it still', () => {
  const view = {x: 20, y: 10, scale: 1};
  const mid = {x: 50, y: 30};
  const v = pinchView(view, {dist: 100, mid}, {dist: 200, mid});
  near(v.scale, 2);
  // The image point under the fingers: (mid - view) / scale stays the same.
  near((mid.x - v.x) / v.scale, (mid.x - view.x) / view.scale);
  near((mid.y - v.y) / v.scale, (mid.y - view.y) / view.scale);
});

test('zoom and pan together: the point under the old midpoint follows the fingers', () => {
  const view = {x: 0, y: 0, scale: 1};
  const v = pinchView(view, {dist: 100, mid: {x: 10, y: 20}}, {dist: 150, mid: {x: 30, y: 60}});
  near(v.scale, 1.5);
  near((30 - v.x) / v.scale, 10);
  near((60 - v.y) / v.scale, 20);
});

test('scale is clamped to 0.1 .. 50', () => {
  assert.equal(pinchView({x: 0, y: 0, scale: 40}, {dist: 10, mid: {x: 0, y: 0}}, {dist: 100, mid: {x: 0, y: 0}}).scale, 50);
  assert.equal(pinchView({x: 0, y: 0, scale: 0.2}, {dist: 100, mid: {x: 0, y: 0}}, {dist: 10, mid: {x: 0, y: 0}}).scale, 0.1);
});
