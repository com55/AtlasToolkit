import test from 'node:test';
import assert from 'node:assert/strict';

import {
  pagesTouchedByModBatches,
  replacePageInAtlas,
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
