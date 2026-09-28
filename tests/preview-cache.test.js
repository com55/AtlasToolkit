import test from 'node:test';
import assert from 'node:assert/strict';

import {
  PreviewCache,
  PackResultCache,
  viewPreviewKey,
  editPreviewKey,
  packSignature,
} from '../www/js/preview-cache.js';
import { withBusy, resetBusyForTests } from '../www/js/busy-overlay.js';

function blobUrl() {
  return URL.createObjectURL(new Blob(['x'], { type: 'text/plain' }));
}

test.afterEach(() => {
  resetBusyForTests();
});

test('PreviewCache.has reports a stored key without dropping it', () => {
  const cache = new PreviewCache();
  const url = blobUrl();
  cache.set('a', url);
  assert.equal(cache.has('a'), true);
  assert.equal(cache.has('missing'), false);
  assert.equal(cache.get('a'), url);
  cache.invalidateAll();
});

test('viewPreviewKey omits modGeneration so A→B→A across Edit still hits', () => {
  const a = viewPreviewKey(['b', 'a'], 1, true, 0);
  const b = viewPreviewKey(['c'], 1, true, 0);
  assert.equal(a, viewPreviewKey(['a', 'b'], 1, true, 0));
  assert.notEqual(a, b);
  assert.notEqual(a, viewPreviewKey(['a', 'b'], 2, true, 0));
  assert.notEqual(a, viewPreviewKey(['a', 'b'], 1, false, 0));
  assert.notEqual(a, viewPreviewKey(['a', 'b'], 1, true, 1));
  assert.equal(a.startsWith('view:'), true);
  assert.equal(a.includes('modGeneration'), false);
});

test('editPreviewKey includes pageIndex, modGeneration, and loadEpoch', () => {
  const k = editPreviewKey(0, 3, 1);
  assert.equal(k, 'edit:0:3:1');
  assert.notEqual(k, editPreviewKey(1, 3, 1));
  assert.notEqual(k, editPreviewKey(0, 4, 1));
  assert.notEqual(k, editPreviewKey(0, 3, 2));
});

test('packSignature distinguishes nest/gap/mesh-aware so option A→B→A can hit', () => {
  const nestOn = packSignature(true, 4, true);
  const nestOff = packSignature(false, 4, true);
  const gap8 = packSignature(true, 8, true);
  const meshOff = packSignature(true, 4, false);
  assert.notEqual(nestOn, nestOff);
  assert.notEqual(nestOn, gap8);
  assert.notEqual(nestOn, meshOff);
  const page = editPreviewKey(0, 5, 1, nestOn);
  assert.equal(page, editPreviewKey(0, 5, 1, nestOn));
  assert.notEqual(page, editPreviewKey(0, 5, 1, nestOff));
  assert.notEqual(page, editPreviewKey(0, 6, 1, nestOn));
});

test('PackResultCache restores the previous packed state after toggling away and back', () => {
  const cache = new PackResultCache(12);
  const keyOn = `5:${packSignature(true, 4, true)}`;
  const keyOff = `5:${packSignature(false, 4, true)}`;
  const onState = { active: { text: 'nest-on' }, repacked: { text: 'nest-on' }, result: { image: 'blob:on' } };
  const offState = { active: { text: 'nest-off' }, repacked: { text: 'nest-off' }, result: { image: 'blob:off' } };
  cache.set(keyOn, onState);
  cache.set(keyOff, offState);
  assert.equal(cache.get(keyOn).active.text, 'nest-on');
  assert.equal(cache.get(keyOff).result.image, 'blob:off');
  cache.invalidateAll();
  assert.equal(cache.get(keyOn), null);
});

test('LRU 24: 25th unique key evicts and revokes the oldest URL', () => {
  const cache = new PreviewCache(24);
  const urls = [];
  for (let i = 0; i < 24; i++) {
    const url = blobUrl();
    urls.push(url);
    cache.set(`k${i}`, url);
  }
  const first = urls[0];
  cache.set('k24', blobUrl());
  assert.equal(cache.get('k0'), null);
  assert.equal(cache.owns(first), false);
  // Evicted blob URL is dead; createObjectURL leftovers from the remaining 24
  // are cleaned by invalidateAll below.
  cache.invalidateAll();
});

test('invalidateAll revokes every cached blob URL', () => {
  const cache = new PreviewCache(24);
  const url = blobUrl();
  cache.set('view:a', url);
  assert.equal(cache.owns(url), true);
  cache.invalidateAll();
  assert.equal(cache.get('view:a'), null);
  assert.equal(cache.owns(url), false);
});

test('View A→B→A hits the original URL without regenerating', () => {
  const cache = new PreviewCache(24);
  const keyA = viewPreviewKey(['arm'], 1, true, 0);
  const keyB = viewPreviewKey(['leg'], 1, true, 0);
  const urlA = blobUrl();
  const urlB = blobUrl();
  cache.set(keyA, urlA);
  cache.set(keyB, urlB);
  assert.equal(cache.get(keyA), urlA);
  cache.invalidateAll();
});

test('mesh toggle is a different View key; the previous URL remains cached', () => {
  const cache = new PreviewCache(24);
  const on = viewPreviewKey(['arm'], 1, true, 0);
  const off = viewPreviewKey(['arm'], 1, false, 0);
  const urlOn = blobUrl();
  cache.set(on, urlOn);
  assert.equal(cache.get(off), null);
  assert.equal(cache.get(on), urlOn);
  cache.invalidateAll();
});

test('forced resizing is a trailing View key bit and defaults off when omitted', () => {
  const omitted = viewPreviewKey(['arm'], 1, true, 0);
  const off = viewPreviewKey(['arm'], 1, true, 0, false);
  const on = viewPreviewKey(['arm'], 1, true, 0, true);
  assert.equal(omitted, off);
  assert.equal(viewPreviewKey(['arm'], 1, true, 0), viewPreviewKey(['arm'], 1, true, 0));
  assert.notEqual(off, on);
  assert.equal(on.endsWith(':1'), true);
  assert.equal(off.endsWith(':0'), true);
});

test('skelEpoch bump misses the previous View entry', () => {
  const cache = new PreviewCache(24);
  const before = viewPreviewKey(['arm'], 1, true, 0);
  const after = viewPreviewKey(['arm'], 1, true, 1);
  cache.set(before, blobUrl());
  assert.equal(cache.get(after), null);
  cache.invalidateAll();
});

test('Edit page 0→1→0 hits page 0', () => {
  const cache = new PreviewCache(24);
  const p0 = editPreviewKey(0, 5, 1);
  const p1 = editPreviewKey(1, 5, 1);
  const url0 = blobUrl();
  cache.set(p0, url0);
  cache.set(p1, blobUrl());
  assert.equal(cache.get(p0), url0);
  cache.invalidateAll();
});

test('PreviewCache.set is allowed during withBusy so get_preview can cache the committed blob', async () => {
  const cache = new PreviewCache(24);
  const key = editPreviewKey(0, 1, 1);
  await withBusy('working', async () => {
    const committed = blobUrl();
    cache.set(key, committed);
    assert.equal(cache.get(key), committed);
  });
  cache.invalidateAll();
});
