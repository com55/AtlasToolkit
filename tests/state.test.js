import test from 'node:test';
import assert from 'node:assert/strict';
import { state, getSelectedRegions, getSelectedKeys, getSelectedLabels } from '../www/js/state.js';

test('getSelectedRegions returns entries in ascending index order, not selection order', () => {
  state.regionsData = [
    { key: 'alpha', label: 'alpha' },
    { key: 'beta', label: 'beta' },
    { key: 'gamma', label: 'gamma' },
  ];
  state.selectedIndices = new Set([2, 0]); // clicked gamma then alpha, out of order
  assert.deepEqual(getSelectedRegions(), [
    { key: 'alpha', label: 'alpha' },
    { key: 'gamma', label: 'gamma' },
  ]);
});

test('getSelectedKeys derives .key from getSelectedRegions, same order', () => {
  state.regionsData = [{ key: 'a', label: 'A' }, { key: 'b', label: 'B' }];
  state.selectedIndices = new Set([1, 0]);
  assert.deepEqual(getSelectedKeys(), ['a', 'b']);
});

test('getSelectedLabels derives .label from getSelectedRegions, same order', () => {
  state.regionsData = [{ key: 'a', label: 'A' }, { key: 'b', label: 'B' }];
  state.selectedIndices = new Set([1, 0]);
  assert.deepEqual(getSelectedLabels(), ['A', 'B']);
});

test('empty selection returns empty arrays from all three accessors', () => {
  state.regionsData = [{ key: 'a', label: 'A' }];
  state.selectedIndices = new Set();
  assert.deepEqual(getSelectedRegions(), []);
  assert.deepEqual(getSelectedKeys(), []);
  assert.deepEqual(getSelectedLabels(), []);
});

test('a divergent key and label are both preserved through one entry, not merged', () => {
  state.regionsData = [{ key: 'arm', label: 'forearm' }];
  state.selectedIndices = new Set([0]);
  const [entry] = getSelectedRegions();
  assert.equal(entry.key, 'arm');
  assert.equal(entry.label, 'forearm');
});
