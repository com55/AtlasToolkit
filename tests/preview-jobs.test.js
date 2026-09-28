import test from 'node:test';
import assert from 'node:assert/strict';

import { createPreviewScheduler } from '../www/js/preview-jobs.js';

test('the same preview key shares one in-flight job', async () => {
  const scheduler = createPreviewScheduler();
  let runs = 0;
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const first = scheduler.run('a', () => {
    runs += 1;
    return gate;
  });
  const second = scheduler.run('a', () => {
    runs += 1;
    return 'other';
  });
  await Promise.resolve();
  assert.equal(runs, 1);
  assert.equal(scheduler.has('a'), true);
  release('done');
  assert.equal(await first, 'done');
  assert.equal(await second, 'done');
  assert.equal(scheduler.has('a'), false);
});

test('a different preview key starts without waiting for the earlier job', async () => {
  const scheduler = createPreviewScheduler();
  let release;
  const first = scheduler.run('a', () => new Promise((resolve) => { release = resolve; }));
  let started = false;
  const second = scheduler.run('b', async () => {
    started = true;
    return 'b';
  });
  await Promise.resolve();
  assert.equal(started, true);
  assert.equal(await second, 'b');
  assert.equal(scheduler.has('a'), true);
  release('a');
  assert.equal(await first, 'a');
});
