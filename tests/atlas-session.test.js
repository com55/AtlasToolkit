import test from 'node:test';
import assert from 'node:assert/strict';

import {
  pagesTouchedByModBatches,
  replacePageInAtlas,
  ModBatch,
  AddBatch,
  RemoveBatch,
  RenameBatch,
  AtlasSession,
} from '../www/js/atlas-session.js';

test('multi-page repack touches only pages that own a modified region', () => {
  const regionPages = {
    CH0355: 'CH0355_spr.png',
    CH0355C: 'CH0355_spr.png',
    NP0288: 'CH0355_spr_1.png',
  };
  const pageOrder = ['CH0355_spr.png', 'CH0355_spr_1.png'];
  assert.deepEqual(
    pagesTouchedByModBatches(regionPages, [['CH0355', 'CH0355C']], pageOrder),
    ['CH0355_spr.png'],
  );
});

test('replacePageInAtlas rewrites one page and leaves the others intact', () => {
  const full = [
    'page1.png',
    'size: 10,10',
    'foo',
    '  bounds: 0, 0, 10, 10',
    'page2.png',
    'size: 20,20',
    'bar',
    '  bounds: 0, 0, 20, 20',
  ].join('\n');
  const packed = [
    'page1.png',
    'size: 8,8',
    'foo',
    '  bounds: 0, 0, 8, 8',
  ].join('\n');
  const out = replacePageInAtlas(full, 'page1.png', packed);
  const names = out.split('\n').filter((l) => l.endsWith('.png'));
  assert.deepEqual(names, ['page1.png', 'page2.png']);
  assert.match(out, /page1\.png\nsize: 8,8/);
  assert.match(out, /page2\.png\nsize: 20,20/);
  assert.match(out, /\nbar\n/);
});

test('ModBatch carries type "mod"', () => {
  const b = new ModBatch(['arm'], 'fake-source');
  assert.equal(b.type, 'mod');
});

test('AddBatch/RemoveBatch/RenameBatch carry their own type and fields', () => {
  const add = new AddBatch('arm_2', 'arm', 'fake-canvas');
  assert.equal(add.type, 'add');
  assert.equal(add.internalKey, 'arm_2');
  assert.equal(add.atlasName, 'arm');
  assert.equal(add.sourceCanvas, 'fake-canvas');

  const rem = new RemoveBatch('arm');
  assert.equal(rem.type, 'remove');
  assert.equal(rem.targetKey, 'arm');

  const ren = new RenameBatch('arm', 'forearm');
  assert.equal(ren.type, 'rename');
  assert.equal(ren.targetKey, 'arm');
  assert.equal(ren.newAtlasName, 'forearm');
});

test('_hasStructuralBatches() is true iff any batch has type !== "mod"', () => {
  const proto = { modBatches: [] };
  // Import the real method off AtlasSession.prototype so the test exercises
  // the actual implementation, not a re-derivation of it.
  const hasStructural = AtlasSession.prototype._hasStructuralBatches;
  proto.modBatches = [new ModBatch(['a'], 'x')];
  assert.equal(hasStructural.call(proto), false);
  proto.modBatches.push(new AddBatch('b_2', 'b', 'canvas'));
  assert.equal(hasStructural.call(proto), true);
});
