import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseSkeleton } from '../www/js/vendor/spine-skeleton-binary/index.js';

// parseSkeleton is pure (no DOM) -- unlike everything else in this feature's
// test suite, this belongs in plain `node --test`, not tests/browser/.

const HERE = path.dirname(fileURLToPath(import.meta.url));
const CH0169_SKEL_PATH = path.resolve(HERE, '..', '.workspaces', 'CH0169', 'CH0169_spr.skel');

test(
  'CH0169_1/2/3 parse as Mesh-type attachments (developer-local, self-skips)',
  { skip: !existsSync(CH0169_SKEL_PATH) },
  () => {
    const bytes = new Uint8Array(readFileSync(CH0169_SKEL_PATH));
    const { attachments } = parseSkeleton(bytes);
    for (const name of ['CH0169_1', 'CH0169_2', 'CH0169_3']) {
      assert.equal(attachments.get(name)?.type, 'Mesh');
    }
  },
);
