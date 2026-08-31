import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseSkeleton } from '../www/js/vendor/spine-skeleton-binary/index.js';

// parseSkeleton is pure (no DOM) -- unlike everything else in this feature's
// test suite, this belongs in plain `node --test`, not tests/browser/.

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SAMPLE_SKEL_PATH = path.resolve(HERE, '..', '.workspaces', 'sample', 'sample_spr.skel');

test(
  'SAMPLE_1/2/3 parse as Mesh-type attachments (developer-local, self-skips)',
  { skip: !existsSync(SAMPLE_SKEL_PATH) },
  () => {
    const bytes = new Uint8Array(readFileSync(SAMPLE_SKEL_PATH));
    const { attachments } = parseSkeleton(bytes);
    for (const name of ['SAMPLE_1', 'SAMPLE_2', 'SAMPLE_3']) {
      assert.equal(attachments.get(name)?.type, 'Mesh');
    }
  },
);
