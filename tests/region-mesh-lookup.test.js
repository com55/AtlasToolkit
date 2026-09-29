import test from 'node:test';
import assert from 'node:assert/strict';
import { buildMeshLookup } from '../www/js/region-mesh-lookup.js';

test('keeps only Mesh-type attachments, drops others', () => {
  const attachments = new Map([
    ['SAMPLE_1', { type: 'Mesh', path: 'SAMPLE_1', uvs: [0,0,1,0,0,1], triangles: [0,1,2], hullLength: 3 }],
    ['Halo', { type: 'Region', path: 'Halo' }],
    ['fronthair', { type: 'LinkedMesh', path: 'fronthair' }],
  ]);
  const lookup = buildMeshLookup({ attachments });
  assert.deepEqual([...lookup.keys()], ['SAMPLE_1']);
  assert.deepEqual(lookup.get('SAMPLE_1'), { uvs: [0,0,1,0,0,1], triangles: [0,1,2], hullLength: 3 });
});

test('returns an empty Map when no attachments are Mesh-type', () => {
  const attachments = new Map([['Halo', { type: 'Region', path: 'Halo' }]]);
  const lookup = buildMeshLookup({ attachments });
  assert.equal(lookup.size, 0);
});
