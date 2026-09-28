import test from 'node:test';
import assert from 'node:assert/strict';

import { pickForceResizeTarget, previewCompositeFrame } from '../www/js/atlas-extracter.js';

const workedExample = [
  { width: 4621, height: 3799 },
  { width: 4621, height: 3799 },
  { width: 4621, height: 3799 },
  { width: 3697, height: 3039 },
  { width: 3119, height: 2564 },
  { width: 2773, height: 2279 },
  { width: 2773, height: 2279 },
];

test('pickForceResizeTarget chooses the greatest area', () => {
  const target = pickForceResizeTarget([
    { width: 100, height: 10 },
    { width: 40, height: 40 },
  ]);
  assert.deepEqual(target, { width: 40, height: 40 });
});

test('pickForceResizeTarget keeps the earlier canvas when areas tie', () => {
  const target = pickForceResizeTarget([
    { width: 10, height: 20 },
    { width: 20, height: 10 },
  ]);
  assert.deepEqual(target, { width: 10, height: 20 });
});

test('pickForceResizeTarget resolves the worked example to 4621×3799', () => {
  assert.deepEqual(pickForceResizeTarget(workedExample), { width: 4621, height: 3799 });
});

test('previewCompositeFrame stretches to the target when forced resizing is on', () => {
  const frame = previewCompositeFrame([
    { width: 100, height: 10 },
    { width: 40, height: 40 },
  ], true);
  assert.deepEqual(frame, { width: 40, height: 40, scale: true });
});

test('previewCompositeFrame keeps the max canvas and does not scale when forced resizing is off', () => {
  const frame = previewCompositeFrame(workedExample, false);
  assert.deepEqual(frame, { width: 4621, height: 3799, scale: false });
});
