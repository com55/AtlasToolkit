import test from 'node:test';
import assert from 'node:assert/strict';

import { nativePathBasename, matchDroppedPngToPage, joinNativePath } from '../www/js/platform.js';

test('joinNativePath keeps Windows or POSIX separators', () => {
  assert.equal(joinNativePath('C:\\atlas', 'page.png'), 'C:\\atlas\\page.png');
  assert.equal(joinNativePath('/tmp/atlas', 'page.png'), '/tmp/atlas/page.png');
});

test('nativePathBasename handles Windows and POSIX paths', () => {
  assert.equal(nativePathBasename('C:\\\\atlas\\\\page.png'), 'page.png');
  assert.equal(nativePathBasename('/tmp/atlas/page.png'), 'page.png');
  assert.equal(nativePathBasename('page.png'), 'page.png');
});

test('matchDroppedPngToPage matches page name case-insensitively', () => {
  const pages = ['Hero.png', 'hero2.png'];
  assert.equal(matchDroppedPngToPage('C:\\\\drop\\\\Hero.png', pages), 'Hero.png');
  assert.equal(matchDroppedPngToPage('/tmp/HERO.PNG', pages), 'Hero.png');
  assert.equal(matchDroppedPngToPage('/tmp/other.png', pages), null);
});
