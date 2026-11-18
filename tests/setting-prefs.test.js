// SPDX-FileCopyrightText: 2026 com55
// SPDX-License-Identifier: Apache-2.0
import test from 'node:test';
import assert from 'node:assert/strict';
import { AtlasAPI, migrateSettingPrefs } from '../www/js/atlas-api.js';

const defaults = {packer: 'auto', keepLayout: true, shareCompositeImage: false};

test('nothing stored uses the new defaults', () => {
  assert.deepEqual(migrateSettingPrefs({}), defaults);
});

for (const packer of ['rectangles', 'polygons']) {
  test(`${packer}_in_place migrates to the base packer and enables keep layout`, () => {
    assert.deepEqual(migrateSettingPrefs({packer: packer + '_in_place', packKeepLayout: false}), {
      ...defaults, packer, keepLayout: true,
    });
  });
}

for (const packer of ['grid', 'unknown']) {
  test(`unknown packer ${packer} migrates to Auto`, () => {
    assert.deepEqual(migrateSettingPrefs({packer}), defaults);
  });
}

for (const split of [false, true]) {
  test(`legacy split ${split} migrates to inverse sharing`, () => {
    assert.deepEqual(migrateSettingPrefs({packSplitComposite: split}), {
      ...defaults, shareCompositeImage: !split,
    });
  });
  test(`explicit sharing ${split} wins over a legacy split preference`, () => {
    assert.equal(migrateSettingPrefs({packShareCompositeImage: split, packSplitComposite: split}).shareCompositeImage, split);
  });
}

for (const packer of ['auto', 'shelf', 'rectangles', 'polygons']) {
  test(`${packer} preserves explicit layout and sharing preferences`, () => {
    assert.deepEqual(migrateSettingPrefs({packer, packKeepLayout: false, packShareCompositeImage: true}), {
      packer, keepLayout: false, shareCompositeImage: true,
    });
  });
}

for (const stored of [
  {},
  {packer: 'polygons_in_place', packKeepLayout: false, packSplitComposite: false},
  {packer: 'unknown', packSplitComposite: true},
  {packer: 'rectangles', packKeepLayout: false, packShareCompositeImage: false, packSplitComposite: false},
]) {
  test(`loader applies and persists migrated preferences: ${JSON.stringify(stored)}`, async t => {
    const saved = {}, applied = [];
    t.mock.method(AtlasAPI, 'get_pref', async (key, fallback) => Object.hasOwn(stored, key) ? stored[key] : fallback);
    t.mock.method(AtlasAPI, 'set_pref', async (key, value) => { saved[key] = value; });
    t.mock.method(AtlasAPI, 'set_settings', async (partial, options) => { applied.push({partial, options}); });
    await AtlasAPI.init_settings_from_prefs();
    const migration = migrateSettingPrefs(stored);
    assert.deepEqual(applied, [{partial: {
      ...migration, padding: 2, meshAware: true, alpha: 'straight', bleed: true,
    }, options: {busy: false}}]);
    assert.equal(saved.packer, migration.packer);
    assert.equal(saved.packKeepLayout, migration.keepLayout);
    assert.equal(saved.packShareCompositeImage, migration.shareCompositeImage);
    assert.equal(Object.hasOwn(saved, 'packSplitComposite'), false);
  });
}
