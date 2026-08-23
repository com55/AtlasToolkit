import test from 'node:test';
import assert from 'node:assert/strict';

import { toastDurationMs } from '../www/js/dialogs.js';

test('toastDurationMs is 3s for short messages', () => {
  assert.equal(toastDurationMs('Cancelled'), 3000);
  assert.equal(toastDurationMs('Atlas loaded via drag & drop.'), 3000);
});

test('toastDurationMs grows for long messages and caps at 8s', () => {
  const long = 'x'.repeat(80);
  assert.ok(toastDurationMs(long) > 3000);
  assert.ok(toastDurationMs('x'.repeat(400)) <= 8000);
});
