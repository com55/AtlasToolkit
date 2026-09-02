import test from 'node:test';
import assert from 'node:assert/strict';
import { validateRegionName } from '../www/js/region-name-validation.js';

test('trims before validating and returns the trimmed value', () => {
  const r = validateRegionName('  arm  ', new Set(['leg']));
  assert.equal(r.ok, true);
  assert.equal(r.value, 'arm');
});

test('rejects blank (including all-whitespace)', () => {
  assert.equal(validateRegionName('', new Set()).ok, false);
  assert.equal(validateRegionName('   ', new Set()).ok, false);
});

test('rejects embedded newline or carriage return', () => {
  assert.equal(validateRegionName('arm\nleg', new Set()).ok, false);
  assert.equal(validateRegionName('arm\rleg', new Set()).ok, false);
});

test('rejects a colon (parser line-type marker)', () => {
  assert.equal(validateRegionName('arm:leg', new Set()).ok, false);
});

test('rejects a trailing .png (parser page-filename marker), trimmed first', () => {
  assert.equal(validateRegionName('foo.png', new Set()).ok, false);
  assert.equal(validateRegionName('foo.png ', new Set()).ok, false); // trailing space, still rejected
});

test('rejects a literal # (tool policy, not a parser fact)', () => {
  assert.equal(validateRegionName('arm#2', new Set()).ok, false);
});

test('rejects a collision with another effective display name, compared trimmed', () => {
  const r = validateRegionName(' arm ', new Set(['arm', 'leg']));
  assert.equal(r.ok, false);
  assert.match(r.reason, /already in use/i);
});

test('does not reject the empty-collision case (no other names)', () => {
  assert.equal(validateRegionName('arm', new Set()).ok, true);
});

test('checks are evaluated in the documented order — blank wins over collision', () => {
  // whitespace-only, even against a set that already contains '' — blank
  // must be reported as blank, not as a collision, per spec §2.4's ordering.
  const r = validateRegionName('   ', new Set(['']));
  assert.equal(r.ok, false);
  assert.match(r.reason, /blank/i);
});

// No test for "blank wins over newline/colon/.png/#" exists, deliberately:
// those four checks all test a property of the TRIMMED value itself
// (contains \n, contains ':', ends in '.png', contains '#'), and the empty
// string can satisfy none of them — so there is no input where swapping
// the blank check with any of those four would change the outcome. Only
// blank-vs-collision (above) is genuinely order-dependent, because the
// collision check depends on the caller-supplied effectiveDisplayNames
// set (an independent input), not on a property of `value` — e.g. '' can
// itself be a name already "in use". Confirmed by hand (validateRegionName('\n', ...)
// returns the blank reason either way the two checks are ordered, since
// '' never matches /[\n\r]/), and by a Codex task-review round on this
// exact point (2026-09-02) after an earlier attempt at this test wrongly
// assumed it was discriminating.

test('checks are evaluated in the documented order — newline wins over colon', () => {
  const r = validateRegionName('arm\nleg:foot', new Set());
  assert.equal(r.ok, false);
  assert.match(r.reason, /line break/i);
});

test('checks are evaluated in the documented order — colon wins over trailing .png', () => {
  const r = validateRegionName('arm:leg.png', new Set());
  assert.equal(r.ok, false);
  assert.match(r.reason, /:/);
});

test('checks are evaluated in the documented order — trailing .png wins over #', () => {
  const r = validateRegionName('arm#2.png', new Set());
  assert.equal(r.ok, false);
  assert.match(r.reason, /\.png/i);
});

test('checks are evaluated in the documented order — # wins over collision', () => {
  // 'arm#2' also collides with an existing region named 'arm#2' — the #
  // policy must be reported, not the collision.
  const r = validateRegionName('arm#2', new Set(['arm#2']));
  assert.equal(r.ok, false);
  assert.match(r.reason, /#/);
});
