import test from 'node:test';
import assert from 'node:assert/strict';

import { nativePathBasename, matchDroppedPngToPage, joinNativePath, siblingSkelFilename, pickSiblingSkelFile, isSkelFilename, ensurePngExtension, finalizeSaveFilename } from '../www/js/platform.js';

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

test('siblingSkelFilename matches Python Path.with_suffix(".skel")', () => {
  assert.equal(siblingSkelFilename('hero.atlas'), 'hero.skel');
  assert.equal(siblingSkelFilename('Hero.ATLAS'), 'Hero.skel');
  assert.equal(siblingSkelFilename('spine-boy.atlas.txt'), 'spine-boy.atlas.skel');
  assert.equal(siblingSkelFilename('hero'), 'hero.skel');
  assert.equal(siblingSkelFilename(''), '');
});

test('pickSiblingSkelFile finds the matching .skel in a dropped file list', () => {
  const skel = { name: 'hero.skel' };
  const files = [{ name: 'hero.atlas' }, { name: 'hero.png' }, skel];
  assert.equal(pickSiblingSkelFile('hero.atlas', files), skel);
  assert.equal(pickSiblingSkelFile('hero.atlas', [{ name: 'other.skel' }]), null);
  const upper = { name: 'HERO.SKEL' };
  assert.equal(pickSiblingSkelFile('Hero.atlas', [upper]), upper);
});

test('ensurePngExtension appends .png only when the filename has no extension', () => {
  assert.equal(ensurePngExtension('hero'), 'hero.png');
  assert.equal(ensurePngExtension('hero.png'), 'hero.png');
  assert.equal(ensurePngExtension('hero.PNG'), 'hero.PNG');
  assert.equal(ensurePngExtension('hero.jpg'), 'hero.jpg');
  assert.equal(ensurePngExtension('shot.tar.gz'), 'shot.tar.gz');
  assert.equal(ensurePngExtension('hero.'), 'hero.png');
  assert.equal(ensurePngExtension('.png'), '.png');
  assert.equal(ensurePngExtension(''), '');
  assert.equal(ensurePngExtension('C:\\atlas\\hero'), 'C:\\atlas\\hero.png');
  assert.equal(ensurePngExtension('C:\\atlas\\hero.png'), 'C:\\atlas\\hero.png');
  assert.equal(ensurePngExtension('/tmp/atlas/hero'), '/tmp/atlas/hero.png');
  assert.equal(ensurePngExtension('my.folder/hero'), 'my.folder/hero.png');
});

test('finalizeSaveFilename appends .png only for PNG saves', () => {
  assert.equal(finalizeSaveFilename('hero.png', 'C:\\out\\hero'), 'C:\\out\\hero.png');
  assert.equal(finalizeSaveFilename('Hero.PNG', 'C:\\out\\hero.jpg'), 'C:\\out\\hero.jpg');
  assert.equal(finalizeSaveFilename('pack.zip', 'C:\\out\\pack'), 'C:\\out\\pack');
  assert.equal(finalizeSaveFilename('notes.atlas', 'notes'), 'notes');
});

test('isSkelFilename matches .skel case-insensitively', () => {
  assert.equal(isSkelFilename('hero.skel'), true);
  assert.equal(isSkelFilename('HERO.SKEL'), true);
  assert.equal(isSkelFilename('hero.atlas'), false);
  assert.equal(isSkelFilename('hero.png'), false);
  assert.equal(isSkelFilename(''), false);
});
