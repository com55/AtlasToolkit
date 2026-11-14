// SPDX-FileCopyrightText: 2026 com55
// SPDX-License-Identifier: Apache-2.0
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
const inline = readFileSync(new URL('../www/index.html', import.meta.url), 'utf8')
  .match(/<script>([\s\S]*?)<\/script>/)[1];
function launch(pathname = '/s/secret/index.html') {
  const beacons = [];
  let handler;
  runInNewContext(inline, {
    location: { pathname },
    document: { documentElement: { classList: { add() {} } } },
    navigator: { sendBeacon(...args) { beacons.push(args); } },
    window: { addEventListener(name, fn, capture) {
      assert.equal(name, 'error'); assert.equal(capture, true); handler = fn;
    } },
  });
  return { beacons, error(event) { handler?.(event); } };
}
test('script errors send token-redacted boot evidence', () => {
  const page = launch();
  page.error({ target: { tagName: 'SCRIPT', src: 'http://localhost/s/secret/script.js' } });
  assert.equal(page.beacons.length, 1);
  assert.equal(page.beacons[0][0], 'api/boot-error');
  assert.ok(page.beacons[0][1].includes('/s/<token>/script.js'));
  assert.ok(!page.beacons[0][1].includes('secret'));
});
test('web path sends no boot beacons', () => {
  const page = launch('/index.html');
  page.error({ target: { tagName: 'SCRIPT' } });
  assert.equal(page.beacons.length, 0);
});
test('filters ordinary errors and caps boot beacons at five', () => {
  const page = launch();
  page.error({ message: 'ordinary exception' });
  assert.equal(page.beacons.length, 0);
  page.error({ message: 'Failed to fetch dynamically imported module: /s/secret/x.js' });
  page.error({ target: { tagName: 'LINK', href: '/s/secret/style.css' } });
  for (let i = 0; i < 4; i++) page.error({ target: { tagName: 'SCRIPT' } });
  assert.equal(page.beacons.length, 5);
});
