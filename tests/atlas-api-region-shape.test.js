import test from 'node:test';
import assert from 'node:assert/strict';
import { AtlasAPI, __testOnlySetLabel } from '../www/js/atlas-api.js';

test('get_region_names returns an empty array with no atlas loaded', () => {
  assert.deepEqual(AtlasAPI.get_region_names(), []);
});

test('extract_files returns the no-atlas message with no atlas loaded', async () => {
  const result = await AtlasAPI.extract_files(null);
  assert.equal(result, 'No atlas loaded.');
});

test('__testOnlySetLabel is a no-op with no atlas loaded (does not throw)', () => {
  assert.doesNotThrow(() => __testOnlySetLabel('anything', 'anything'));
});

test('__testOnlySetLabel is not attached to the AtlasAPI object', () => {
  assert.equal(AtlasAPI.__testOnlySetLabel, undefined);
});
