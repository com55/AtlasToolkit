import { test } from 'node:test';
import assert from 'node:assert/strict';
import { formatAppTitle } from '../www/js/app-title.js';

test('formatAppTitle uses version only when no atlas is loaded', () => {
  assert.equal(formatAppTitle('0.3.5', ''), 'Atlas Toolkit v0.3.5');
  assert.equal(formatAppTitle('v0.3.5', '  '), 'Atlas Toolkit v0.3.5');
});

test('formatAppTitle appends the atlas filename after load', () => {
  assert.equal(formatAppTitle('0.3.5', 'hero.atlas'), 'Atlas Toolkit v0.3.5 - hero.atlas');
});

test('formatAppTitle falls back when version is missing', () => {
  assert.equal(formatAppTitle('', ''), 'Atlas Toolkit');
  assert.equal(formatAppTitle('', 'hero.atlas'), 'Atlas Toolkit - hero.atlas');
});
