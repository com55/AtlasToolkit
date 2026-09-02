// tests/effective-region-model.test.js
import test from 'node:test';
import assert from 'node:assert/strict';
import { deriveEffectiveModel } from '../www/js/effective-region-model.js';
import { ModBatch, AddBatch, RemoveBatch, RenameBatch } from '../www/js/atlas-session.js';

function pristine() {
  return {
    arm: { atlasName: 'arm', offsets: [1, 2, 10, 10], index: -1, split: null, pad: null, extraPairs: [] },
    leg: { atlasName: 'leg', offsets: null, index: -1, split: null, pad: null, extraPairs: [] },
  };
}

test('no batches: passes pristine regions through unchanged, no labels, no modifiedKeys', () => {
  const m = deriveEffectiveModel(pristine(), []);
  assert.deepEqual(m.regionNames, ['arm', 'leg']);
  assert.deepEqual(m.regions.arm, pristine().arm);
  assert.deepEqual(m.labels, {});
  assert.deepEqual([...m.modifiedKeys], []);
});

test('ModBatch: names go into modifiedKeys, regions/labels untouched', () => {
  const m = deriveEffectiveModel(pristine(), [new ModBatch(['arm'], 'src')]);
  assert.deepEqual([...m.modifiedKeys].sort(), ['arm']);
  assert.deepEqual(m.labels, {});
  assert.deepEqual(m.regionNames, ['arm', 'leg']);
});

test('AddBatch: new key appended, gets §3 defaults, is in labels and modifiedKeys', () => {
  const add = new AddBatch('helmet', 'helmet', 'fake-canvas');
  const m = deriveEffectiveModel(pristine(), [add]);
  assert.deepEqual(m.regionNames, ['arm', 'leg', 'helmet']);
  assert.deepEqual(m.regions.helmet, {
    atlasName: 'helmet', offsets: null, index: -1, split: null, pad: null, extraPairs: [],
  });
  assert.equal(m.labels.helmet, 'helmet');
  assert.ok(m.modifiedKeys.has('helmet'));
  assert.equal(m.labels.arm, undefined); // untouched pristine key stays absent from labels
});

test('RemoveBatch: key drops out of regionNames and regions entirely, no highlight needed', () => {
  const m = deriveEffectiveModel(pristine(), [new RemoveBatch('leg')]);
  assert.deepEqual(m.regionNames, ['arm']);
  assert.equal(m.regions.leg, undefined);
  assert.ok(!m.modifiedKeys.has('leg'));
});

test('RenameBatch: atlasName overridden, key/regionNames order unchanged, label set, modifiedKeys includes it', () => {
  const input = pristine();
  const m = deriveEffectiveModel(input, [new RenameBatch('arm', 'forearm')]);
  assert.deepEqual(m.regionNames, ['arm', 'leg']); // key never changes
  assert.equal(m.regions.arm.atlasName, 'forearm');
  assert.equal(m.regions.arm.offsets, input.arm.offsets); // rest of RegionMeta untouched
  assert.equal(m.labels.arm, 'forearm');
  assert.ok(m.modifiedKeys.has('arm'));
});

test('Add then Rename the same new region (chained by internalKey)', () => {
  const add = new AddBatch('helmet', 'helmet', 'fake-canvas');
  const ren = new RenameBatch('helmet', 'iron_helmet');
  const m = deriveEffectiveModel(pristine(), [add, ren]);
  assert.deepEqual(m.regionNames, ['arm', 'leg', 'helmet']);
  assert.equal(m.regions.helmet.atlasName, 'iron_helmet');
  assert.equal(m.labels.helmet, 'iron_helmet');
});

test('Add then Remove the same new region: never appears in output at all', () => {
  const add = new AddBatch('helmet', 'helmet', 'fake-canvas');
  const rem = new RemoveBatch('helmet');
  const m = deriveEffectiveModel(pristine(), [add, rem]);
  assert.deepEqual(m.regionNames, ['arm', 'leg']);
  assert.equal(m.regions.helmet, undefined);
});

test('is shape-agnostic: works on a mock RegionMeta shape carrying extra unrelated fields (e.g. scaled bounds)', () => {
  const scaledLike = {
    arm: { atlasName: 'arm', offsets: null, index: -1, split: null, pad: null, extraPairs: [], bounds: [0, 0, 5, 5] },
  };
  const m = deriveEffectiveModel(scaledLike, [new RenameBatch('arm', 'forearm')]);
  assert.equal(m.regions.arm.atlasName, 'forearm');
  assert.deepEqual(m.regions.arm.bounds, [0, 0, 5, 5]); // passed through untouched, never read/required
});
