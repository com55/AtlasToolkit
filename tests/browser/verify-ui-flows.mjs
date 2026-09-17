/**
 * Scripted UI-flow verification driving the REAL index.html DOM (not the
 * AtlasAPI harness that verify-app-e2e.mjs uses).
 *
 * Covers, at a desktop viewport (1440x900):
 *   load -> multi-select (click / ctrl / shift) -> extract-selected download
 *   -> enter edit mode via the mode toggle -> multi-page prev/next switcher
 *   -> mod apply through the real file-chooser (ReplaceSelected) -> sequential
 *   second mod (rebuild-from-pristine regression, at the UI level) -> repack
 *   toggle on/off without a reload -> unsaved-changes guard (cancel + confirm
 *   paths) -> missing-page dialog abort -> Save As... zip download.
 * Then a condensed touch pass (390x844, isMobile+hasTouch): tap-select,
 * edit mode, page switcher, guard dialog.
 *
 * Not part of `node --test` (needs a browser + playwright-core). Run via:
 *
 *   node test/browser/verify-ui-flows.mjs
 *
 * playwright-core is located via $PLAYWRIGHT_CORE, a bare import, then common
 * global locations; the script SKIPS (exit 0) if none resolve.
 */
import http from 'node:http';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const WWW  = path.resolve(HERE, '..', '..', 'www');
const PYPROJECT = path.resolve(HERE, '..', '..', 'pyproject.toml');
const APP_VERSION = (fs.readFileSync(PYPROJECT, 'utf8').match(/^version\s*=\s*"([^"]+)"/m) || [])[1] || '';

function stampIndexHtml(html, version) {
  const v = String(version || '').replace(/^v/i, '');
  const title = `<title>Atlas Toolkit v${v}</title>`;
  const meta = `<meta name="app-version" content="${v}" />`;
  let out = html.replace(/<title>[^<]*<\/title>/, title);
  if (/<meta\s+name="app-version"/i.test(out)) {
    return out.replace(/<meta\s+name="app-version"\s+content="[^"]*"\s*\/?>/i, meta);
  }
  return out.replace(title, `${meta}\n    ${title}`);
}

async function loadChromium() {
  const candidates = [
    process.env.PLAYWRIGHT_CORE,
    'playwright-core',
    'playwright',
    path.join(os.homedir(), '.npm-global/lib/node_modules/@playwright/mcp/node_modules/playwright-core/index.js'),
  ].filter(Boolean);
  for (const c of candidates) {
    try {
      const mod = await import(c);
      const pw = mod.chromium ? mod : mod.default;
      if (pw && pw.chromium) return pw.chromium;
    } catch { /* try next */ }
  }
  return null;
}

const chromium = await loadChromium();
if (!chromium) {
  console.log('SKIP: playwright-core not found (set $PLAYWRIGHT_CORE to its index.js to run).');
  process.exit(0);
}

const MIME = {
  '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html',
  '.png': 'image/png', '.json': 'application/json', '.ico': 'image/x-icon',
};
const server = http.createServer((req, res) => {
  let p = decodeURIComponent(req.url.split('?')[0]);
  if (p === '/') p = '/index.html';
  const filePath = path.join(WWW, p);
  if (!filePath.startsWith(WWW) || !fs.existsSync(filePath)) { res.statusCode = 404; return res.end('nf'); }
  res.setHeader('content-type', MIME[path.extname(filePath)] || 'text/plain');
  let body = fs.readFileSync(filePath);
  if (p === '/index.html') body = Buffer.from(stampIndexHtml(body.toString('utf8'), APP_VERSION), 'utf8');
  res.end(body);
});
await new Promise((r) => server.listen(0, r));
const URL_ROOT = `http://127.0.0.1:${server.address().port}/`;

const MOD_PNG = fs.readFileSync(path.join(HERE, 'fixtures/opaque-transparent/fixture-opaque.png'));

let pass = 0, fail = 0;
const check = (name, ok, detail = '') => {
  console.log(`${ok ? 'PASS ' : 'FAIL '} ${name}${detail ? ` (${detail})` : ''}`);
  ok ? pass++ : fail++;
};

/** Load a synthetic 2-page atlas through the app's real load path + refresh the region list. */
async function loadFixtureAtlas(page) {
  return page.evaluate(async () => {
    const { AtlasAPI } = await import('./js/atlas-api.js');
    const { loadRegions } = await import('./js/region-list.js');
    const { state } = await import('./js/state.js');
    const { updateMeshCroppingUI } = await import('./js/modify-mode.js');

    const solid = (w, h, rgba) => {
      const c = document.createElement('canvas');
      c.width = w; c.height = h;
      const ctx = c.getContext('2d');
      ctx.fillStyle = `rgba(${rgba.join(',')})`;
      ctx.fillRect(0, 0, w, h);
      return c;
    };
    const toFile = (canvas, name) => new Promise((res) =>
      canvas.toBlob((b) => res(new File([b], name, { type: 'image/png' })), 'image/png'));

    const atlasText = `page1.png
size: 100, 100
alpha
bounds: 0, 0, 30, 30
beta
bounds: 30, 0, 30, 30
gamma
bounds: 0, 30, 40, 40

page2.png
size: 80, 80
delta
bounds: 0, 0, 40, 40
epsilon
bounds: 40, 0, 30, 30
`;
    const atlasFile = new File([atlasText], 'flows.atlas', { type: 'text/plain' });
    const p1 = await toFile(solid(100, 100, [40, 80, 120, 255]), 'page1.png');
    const p2 = await toFile(solid(80, 80, [120, 40, 80, 255]), 'page2.png');
    const ok = await AtlasAPI.load_atlas_from_file(atlasFile, { 'page1.png': p1, 'page2.png': p2 });
    if (!ok) return { ok: false };
    state.selectedIndices.clear();
    state.lastClickIndex = -1;
    await loadRegions();
    updateMeshCroppingUI();
    return { ok: true, names: AtlasAPI.get_region_names() };
  });
}

/** Load a synthetic 1-page atlas through the app's real load path (Task 8's multi-page-guard test needs a single-page case; the file's only existing fixture is 2-page). */
async function loadSinglePageFixtureAtlas(page) {
  return page.evaluate(async () => {
    const { AtlasAPI } = await import('./js/atlas-api.js');
    const { loadRegions } = await import('./js/region-list.js');
    const { state } = await import('./js/state.js');

    const solid = (w, h, rgba) => {
      const c = document.createElement('canvas');
      c.width = w; c.height = h;
      const ctx = c.getContext('2d');
      ctx.fillStyle = `rgba(${rgba.join(',')})`;
      ctx.fillRect(0, 0, w, h);
      return c;
    };
    const toFile = (canvas, name) => new Promise((res) =>
      canvas.toBlob((b) => res(new File([b], name, { type: 'image/png' })), 'image/png'));

    const atlasText = `page1.png
size: 50, 50
zeta
bounds: 0, 0, 20, 20
omega
bounds: 25, 0, 20, 20
`;
    const atlasFile = new File([atlasText], 'single.atlas', { type: 'text/plain' });
    const p1 = await toFile(solid(50, 50, [80, 40, 200, 255]), 'page1.png');
    const ok = await AtlasAPI.load_atlas_from_file(atlasFile, { 'page1.png': p1 });
    if (!ok) return { ok: false };
    state.selectedIndices.clear();
    state.lastClickIndex = -1;
    await loadRegions();
    return { ok: true, names: AtlasAPI.get_region_names() };
  });
}

const readUi = (page) => page.evaluate(() => ({
  count: document.getElementById('count').innerText,
  items: document.querySelectorAll('.region-item').length,
  selected: [...document.querySelectorAll('.region-item.selected')].map((el) => el.innerText),
  mode: document.getElementById('mode-modify').classList.contains('active') ? 'modify' : 'extract',
  editEnabled: !document.getElementById('mode-modify').disabled,
  extractAllEnabled: !document.getElementById('btn-extract-all').disabled,
  modifyControlsVisible: !document.getElementById('modify-controls').classList.contains('hidden'),
  switcherVisible: !document.getElementById('modify-page-switcher').classList.contains('hidden'),
  indicator: document.getElementById('page-indicator').innerText,
  prevDisabled: document.getElementById('page-prev').disabled,
  nextDisabled: document.getElementById('page-next').disabled,
  saveEnabled: !document.getElementById('btn-save-mod').disabled,
  saveVisible: !document.getElementById('save-split').classList.contains('hidden'),
  saveMenuOpen: document.getElementById('save-menu').classList.contains('open'),
  repackRowVisible: !document.getElementById('options-row').classList.contains('hidden'),
  resetEnabled: !document.getElementById('btn-reset-mod').disabled,
  saveMergedEnabled: !document.getElementById('btn-save-merged').disabled,
  modalVisible: !document.getElementById('modal-overlay').classList.contains('hidden'),
  previewSrcLen: (document.getElementById('preview-img').src || '').length,
  missingDialog: !!document.querySelector('.missing-images-overlay'),
  pickSkelVisible: !document.getElementById('btn-pick-skel').classList.contains('hidden')
    && getComputedStyle(document.getElementById('btn-pick-skel')).display !== 'none',
  nestSettingsVisible: getComputedStyle(document.getElementById('options-group-pack')).display !== 'none'
    && document.getElementById('btn-nest-settings').getBoundingClientRect().width > 0,
  nestGapPanelOpen: !document.getElementById('nest-gap-overlay').classList.contains('hidden'),
}));

// ─── Desktop pass ─────────────────────────────────────────────────────────────
const browser = await chromium.launch({ headless: true });
{
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, acceptDownloads: true });
  // The app prefers showSaveFilePicker (a native dialog automation can't
  // drive) when available. Remove it so _downloadBlob takes the anchor
  // download fallback — same code path a non-FS-API browser (Firefox) gets —
  // which Playwright can observe via the 'download' event.
  await ctx.addInitScript(() => { try { delete window.showSaveFilePicker; } catch { /* ignore */ } });
  // Task 7 scrutinize finding: the existing "no page errors after rapid
  // gap-distance edits" check passes identically whether the debounce
  // collapses N keystrokes to 1 rerun or fires N reruns -- it never
  // actually verifies debouncing. Count writes to the specific persisted
  // pref key (set_nest_gap_distance's only externally-observable side
  // effect besides the packer call itself) to get a real assertion.
  await ctx.addInitScript(() => {
    window.__nestGapWriteCount = 0;
    const realSetItem = Storage.prototype.setItem;
    Storage.prototype.setItem = function (key, value) {
      if (key === 'atlastoolkit.nestGapDistance') window.__nestGapWriteCount++;
      return realSetItem.call(this, key, value);
    };
  });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto(URL_ROOT, { waitUntil: 'networkidle' });

  let ui = await readUi(page);
  check('desktop: .skel picker hidden before atlas load', !ui.pickSkelVisible);
  const titleBefore = await page.title();
  check('desktop: tab title includes version before load',
    titleBefore === `Atlas Toolkit v${APP_VERSION}`, titleBefore);

  // 1. Load
  const loaded = await loadFixtureAtlas(page);
  check('desktop: atlas loads through real load path', loaded.ok && loaded.names.length === 5, `names=${loaded.names?.length}`);
  const titleAfter = await page.title();
  check('desktop: tab title appends atlas filename after load',
    titleAfter === `Atlas Toolkit v${APP_VERSION} - flows.atlas`, titleAfter);
  ui = await readUi(page);
  check('desktop: region list rendered + count badge', ui.items === 5 && ui.count === '5', `items=${ui.items} count=${ui.count}`);
  check('desktop: Edit toggle + Extract All enabled after load', ui.editEnabled && ui.extractAllEnabled);
  check('desktop: .skel picker visible after atlas load in view mode', ui.pickSkelVisible);
  check('desktop: gap settings gear hidden in view mode', !ui.nestSettingsVisible);
  const toggleHelp = await page.evaluate(() => ({
    title: document.getElementById('mesh-mask-toggle-row').getAttribute('title'),
    help: document.getElementById('mesh-mask-toggle-row').getAttribute('data-help'),
  }));
  check('desktop: Mesh Cropping uses data-help instead of title', !toggleHelp.title && !!toggleHelp.help);
  await page.hover('#mesh-mask-toggle-row');
  await page.waitForTimeout(600);
  const helpGeom = await page.evaluate(() => {
    const pop = document.getElementById('opt-help-popover').getBoundingClientRect();
    const row = document.getElementById('options-row').getBoundingClientRect();
    return {
      shown: !document.getElementById('opt-help-popover').classList.contains('hidden'),
      popHeight: pop.height,
      popBottom: pop.bottom,
      rowBottom: row.bottom,
    };
  });
  check('desktop: hover delay shows help popover below the toggle, not clipped by #options-row',
    helpGeom.shown && helpGeom.popHeight > 8 && helpGeom.popBottom > helpGeom.rowBottom,
    JSON.stringify(helpGeom));
  await page.mouse.move(0, 0);
  const pickerSize = await page.evaluate(() => {
    const row = document.getElementById('options-row').getBoundingClientRect();
    const btn = document.getElementById('btn-pick-skel').getBoundingClientRect();
    return { rowH: row.height, btnH: btn.height };
  });
  check(
    'desktop: .skel picker is shorter than the options row',
    pickerSize.btnH > 0 && pickerSize.btnH < pickerSize.rowH,
    `btn=${pickerSize.btnH} row=${pickerSize.rowH}`,
  );
  const collapseAtDesktop = await page.evaluate(() => {
    const btn = document.getElementById('btn-options-collapse');
    return {
      hidden: btn.classList.contains('hidden') || getComputedStyle(btn).display === 'none',
      overflowing: document.getElementById('options-row').classList.contains('is-overflowing'),
    };
  });
  check('desktop: collapse chevron hidden when the row fits',
    collapseAtDesktop.hidden && !collapseAtDesktop.overflowing, JSON.stringify(collapseAtDesktop));

  // 2. Multi-select: click, shift-click, ctrl-click
  const items = page.locator('.region-item');
  await items.nth(0).click();
  await items.nth(2).click({ modifiers: ['Shift'] });
  await page.waitForTimeout(120); // 50ms preview debounce
  ui = await readUi(page);
  check('desktop: shift-click range selects 3', ui.selected.length === 3, ui.selected.join(','));
  check('desktop: composite preview rendered', ui.previewSrcLen > 100);
  check('desktop: Save Image enabled with a live preview', ui.saveMergedEnabled);
  await items.nth(1).click({ modifiers: ['Control'] });
  await page.waitForTimeout(120);
  ui = await readUi(page);
  check('desktop: ctrl-click toggles one off (2 left)', ui.selected.length === 2, ui.selected.join(','));

  // 3. Extract selected -> browser download
  await items.nth(0).click(); // single selection
  await page.waitForTimeout(120);
  const dl1 = page.waitForEvent('download');
  await page.click('#btn-extract-sel');
  const download1 = await dl1;
  check('desktop: Extract Selected downloads a PNG', download1.suggestedFilename().endsWith('.png'), download1.suggestedFilename());

  // View mode: Smart Packing + gear live in .only-edit-mode, so the gear
  // must not be visible until Edit is entered.
  check('desktop: gap settings gear still hidden in view mode after load', !ui.nestSettingsVisible);

  // 4. Enter edit mode via the toggle
  await page.click('#mode-modify');
  await page.waitForTimeout(200);
  ui = await readUi(page);
  check('desktop: mode toggle enters edit mode', ui.mode === 'modify' && ui.modifyControlsVisible);
  check('desktop: repack row + Save As appear in edit mode', ui.repackRowVisible && ui.saveVisible);
  check('desktop: gap settings gear visible in edit mode', ui.nestSettingsVisible);
  const editGroupOrder = await page.evaluate(() =>
    [...document.querySelectorAll('#options-row > li')]
      .filter((li) => getComputedStyle(li).display !== 'none')
      .map((li) => li.id),
  );
  check('desktop: edit-mode group order is mesh, pack, then Advance Mode last',
    editGroupOrder.join(',') === 'options-group-mesh,options-group-pack,options-group-advance',
    editGroupOrder.join(','));
  check('desktop: gap panel stays closed until the gear is clicked', !ui.nestGapPanelOpen);
  check('desktop: gap settings gear disabled while Smart Packing is off',
    await page.locator('#btn-nest-settings').isDisabled());
  // #chk-nest-regions is display:none (styled as a .toggle-switch); the real
  // user control is the wrapping label. Clicking it toggles the checkbox and
  // fires the change handler that enables the gap-distance input.
  await page.click('#nest-regions-toggle-row');
  await page.waitForTimeout(500); // clear the 400ms toggle debounce/rerun window
  check('desktop: gap settings gear enabled once Smart Packing is checked',
    !(await page.locator('#btn-nest-settings').isDisabled()));
  await page.click('#btn-nest-settings');
  await page.waitForTimeout(80);
  ui = await readUi(page);
  check('desktop: gear opens the gap panel', ui.nestGapPanelOpen);
  const gapInput = page.locator('#nest-gap-distance');
  check('desktop: gap-distance input enabled once Nest Regions is checked', !(await gapInput.isDisabled()));
  const popoverBtns = await page.evaluate(() => {
    const closeBtn = document.getElementById('nest-gap-close');
    const confirmBtn = document.getElementById('nest-gap-confirm');
    return {
      closeDisplay: getComputedStyle(closeBtn).display,
      confirmH: confirmBtn.getBoundingClientRect().height,
      confirmText: confirmBtn.innerText,
    };
  });
  check('desktop: popover shows a small Confirm and no Close',
    popoverBtns.closeDisplay === 'none' && popoverBtns.confirmText === 'Confirm' && popoverBtns.confirmH > 0 && popoverBtns.confirmH <= 26,
    JSON.stringify(popoverBtns));
  await page.evaluate(() => { window.__nestGapWriteCount = 0; }); // clean baseline before the burst
  await gapInput.fill('2');
  await gapInput.type('7'); // draft only -- must not write until Confirm
  await gapInput.dispatchEvent('input');
  await page.waitForTimeout(200);
  check('desktop: no page errors after rapid gap-distance edits', errors.length === 0, errors.join('; '));
  const writeCountBeforeConfirm = await page.evaluate(() => window.__nestGapWriteCount);
  check('desktop: gap-distance edits do not write until Confirm',
    writeCountBeforeConfirm === 0, `writeCount=${writeCountBeforeConfirm}`);
  await page.click('#nest-gap-confirm');
  await page.waitForTimeout(600);
  const writeCount = await page.evaluate(() => window.__nestGapWriteCount);
  check('desktop: Confirm writes the drafted gap distance once',
    writeCount === 1, `writeCount=${writeCount}`);
  await page.keyboard.press('Escape');
  await page.click('#nest-regions-toggle-row');
  await page.waitForTimeout(200);
  check('desktop: gap-distance input disabled again once Nest Regions is unchecked', await gapInput.isDisabled());
  check('desktop: gap settings gear disabled again once Smart Packing is unchecked',
    await page.locator('#btn-nest-settings').isDisabled());
  check('desktop: Save As chevron is visible', await page.locator('#btn-save-menu').isVisible());
  await page.click('#btn-save-menu');
  await page.waitForTimeout(80);
  ui = await readUi(page);
  check('desktop: chevron opens Copy .skel menu', ui.saveMenuOpen);
  await page.click('#sidebar-head');
  await page.waitForTimeout(80);
  ui = await readUi(page);
  check('desktop: click away closes save menu', !ui.saveMenuOpen);
  check('desktop: multi-page switcher visible, Page 1 / 2, prev disabled', ui.switcherVisible && ui.indicator === 'Page 1 / 2' && ui.prevDisabled && !ui.nextDisabled, ui.indicator);

  // 5. Page switcher next/prev
  const srcBefore = await page.evaluate(() => document.getElementById('preview-img').src.length);
  await page.click('#page-next');
  await page.waitForTimeout(200);
  ui = await readUi(page);
  const srcAfter = await page.evaluate(() => document.getElementById('preview-img').src.length);
  check('desktop: next -> Page 2 / 2, next disabled, preview changed', ui.indicator === 'Page 2 / 2' && ui.nextDisabled && srcAfter !== srcBefore, ui.indicator);
  await page.click('#page-prev');
  await page.waitForTimeout(200);
  ui = await readUi(page);
  check('desktop: prev -> back to Page 1 / 2', ui.indicator === 'Page 1 / 2');

  // 6. Mod apply through the REAL file chooser (ReplaceSelected)
  await items.nth(0).click(); // select "alpha" (page1)
  await page.waitForTimeout(120);
  const chooser1 = page.waitForEvent('filechooser');
  await page.click('#btn-modify-sel');
  await (await chooser1).setFiles({ name: 'mod1.png', mimeType: 'image/png', buffer: MOD_PNG });
  await page.waitForTimeout(400);
  ui = await readUi(page);
  check('desktop: mod apply #1 enables Save As + Reset', ui.saveEnabled && ui.resetEnabled);

  // Reset: confirm dialog -> pristine edit view (Save As / Reset disabled again)
  await page.click('#btn-reset-mod');
  await page.waitForTimeout(150);
  ui = await readUi(page);
  check('desktop: Reset asks for confirmation', ui.modalVisible);
  await page.click('#btn-modal-confirm');
  await page.waitForTimeout(300);
  ui = await readUi(page);
  check('desktop: Reset restores pristine edit view', ui.mode === 'modify' && !ui.saveEnabled && !ui.resetEnabled);

  // Re-apply mod #1 so the sequential test below still starts from one batch
  await items.nth(0).click();
  await page.waitForTimeout(120);
  const chooser1b = page.waitForEvent('filechooser');
  await page.click('#btn-modify-sel');
  await (await chooser1b).setFiles({ name: 'mod1.png', mimeType: 'image/png', buffer: MOD_PNG });
  await page.waitForTimeout(400);

  // Sequential second mod on a different region (UI-level rebuild-from-pristine regression)
  await items.nth(1).click(); // "beta"
  await page.waitForTimeout(120);
  const chooser2 = page.waitForEvent('filechooser');
  await page.click('#btn-modify-sel');
  await (await chooser2).setFiles({ name: 'mod2.png', mimeType: 'image/png', buffer: MOD_PNG });
  await page.waitForTimeout(400);
  const twoBatches = await page.evaluate(async () => {
    const { AtlasAPI } = await import('./js/atlas-api.js');
    return AtlasAPI.has_pending_modifications();
  });
  check('desktop: sequential mod apply #2 accepted, pending mods true', twoBatches);
  check('desktop: no page errors after sequential mods', errors.length === 0, errors.join('; '));

  // (Repack-toggle on/off test removed: the #repack-toggle-row switch was
  // deleted when repack became unconditional -- there is no toggle left to
  // flip, so the old "no reload on toggle" assertion is obsolete.)

  // 8. Unsaved-changes guard: cancel keeps edit mode, confirm exits
  await page.click('#mode-extract');
  await page.waitForTimeout(150);
  ui = await readUi(page);
  check('desktop: guard modal appears on exit with pending mods', ui.modalVisible);
  await page.click('#btn-modal-cancel');
  await page.waitForTimeout(150);
  ui = await readUi(page);
  check('desktop: cancel keeps edit mode', !ui.modalVisible && ui.mode === 'modify');
  await page.click('#mode-extract');
  await page.waitForTimeout(150);
  await page.click('#btn-modal-confirm');
  await page.waitForTimeout(200);
  ui = await readUi(page);
  check('desktop: confirm discards and returns to view mode', ui.mode === 'extract' && !ui.modifyControlsVisible);

  // 9. Missing-page dialog: load an atlas with no image -> cancel aborts cleanly
  const missingResult = page.evaluate(async () => {
    const { AtlasAPI } = await import('./js/atlas-api.js');
    const atlasFile = new File(['orphan.png\nsize: 10, 10\nlone\nbounds: 0, 0, 5, 5\n'], 'orphan.atlas', { type: 'text/plain' });
    return AtlasAPI.load_atlas_from_file(atlasFile, {});
  });
  await page.waitForTimeout(300);
  ui = await readUi(page);
  check('desktop: missing-page dialog appears', ui.missingDialog);
  await page.click('.missing-images-buttons .btn-secondary'); // Cancel
  const aborted = await missingResult;
  await page.waitForTimeout(200);
  ui = await readUi(page);
  check('desktop: cancel aborts load, previous atlas intact', aborted === false && ui.items === 5, `items=${ui.items}`);

  // 10. Save As... -> zip download; saved state clears the guard
  await page.click('#mode-modify');
  await page.waitForTimeout(200);
  await items.nth(0).click();
  await page.waitForTimeout(120);
  const chooser3 = page.waitForEvent('filechooser');
  await page.click('#btn-modify-sel');
  await (await chooser3).setFiles({ name: 'mod3.png', mimeType: 'image/png', buffer: MOD_PNG });
  await page.waitForTimeout(400);
  const dl2 = page.waitForEvent('download');
  await page.click('#btn-save-mod');
  const download2 = await dl2;
  check('desktop: Save As downloads a zip', download2.suggestedFilename().endsWith('.zip'), download2.suggestedFilename());
  await page.waitForTimeout(300);
  const afterSave = await page.evaluate(() => ({
    status: document.getElementById('status-text').innerText,
    toast: [...document.querySelectorAll('#toast-container .toast')].map((el) => el.innerText),
  }));
  const toastText = afterSave.toast.join('\n');
  check('desktop: save outcome is toasted', /Saved/i.test(toastText), toastText);
  check(
    'desktop: status bar does not echo the toast',
    afterSave.status && toastText && afterSave.status !== toastText && !/^Saved\b/i.test(afterSave.status),
    `status=${afterSave.status}`,
  );
  await page.click('#mode-extract');
  await page.waitForTimeout(150);
  ui = await readUi(page);
  check('desktop: after save, exit needs no guard', !ui.modalVisible && ui.mode === 'extract');

  check('desktop: zero page errors across all flows', errors.length === 0, errors.join('; '));
  await ctx.close();
}

// ─── Touch pass (condensed) ───────────────────────────────────────────────────
{
  const ctx = await browser.newContext({
    viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, acceptDownloads: true,
  });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto(URL_ROOT, { waitUntil: 'networkidle' });

  const loaded = await loadFixtureAtlas(page);
  check('touch: atlas loads', loaded.ok, '');

  // Tap = toggle-select per the touch input model
  await page.locator('.region-item').nth(0).tap();
  await page.waitForTimeout(350); // tap-vs-longpress settle + preview debounce
  let ui = await readUi(page);
  check('touch: tap selects a region', ui.selected.length === 1, ui.selected.join(','));

  await page.locator('#mode-modify').tap();
  await page.waitForTimeout(250);
  ui = await readUi(page);
  check('touch: edit mode + page switcher on stacked layout', ui.mode === 'modify' && ui.switcherVisible && ui.indicator === 'Page 1 / 2', ui.indicator);
  check('touch: Save As split is visible', ui.saveVisible);
  await page.locator('#btn-save-menu').tap();
  await page.waitForTimeout(80);
  ui = await readUi(page);
  check('touch: chevron opens Copy .skel menu', ui.saveMenuOpen);
  await page.locator('#sidebar-head').tap();
  await page.waitForTimeout(80);
  ui = await readUi(page);
  check('touch: tap away closes save menu', !ui.saveMenuOpen);

  await page.locator('#page-next').tap();
  await page.waitForTimeout(250);
  ui = await readUi(page);
  check('touch: page switcher works by tap', ui.indicator === 'Page 2 / 2');

  // Guard: apply a mod first so the guard has something to protect.
  // (Touch tap = TOGGLE-select: alpha is already selected from the earlier
  // tap, so tap a different region — re-tapping alpha would empty the
  // selection and ReplaceSelected would refuse to open the chooser.)
  await page.locator('.region-item').nth(1).tap();
  await page.waitForTimeout(350);
  const chooser = page.waitForEvent('filechooser');
  await page.locator('#btn-modify-sel').tap();
  await (await chooser).setFiles({ name: 'mod.png', mimeType: 'image/png', buffer: MOD_PNG });
  await page.waitForTimeout(400);
  await page.locator('#mode-extract').tap();
  await page.waitForTimeout(150);
  ui = await readUi(page);
  check('touch: guard modal on exit', ui.modalVisible);
  await page.locator('#btn-modal-confirm').tap();
  await page.waitForTimeout(200);
  ui = await readUi(page);
  check('touch: confirm exits to view mode', ui.mode === 'extract');

  check('touch: zero page errors', errors.length === 0, errors.join('; '));
  await ctx.close();
}

// ─── Task 8: Advance Mode toggle, multi-page guard ─────────────────────────────
{
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto(URL_ROOT, { waitUntil: 'networkidle' });

  const single = await loadSinglePageFixtureAtlas(page);
  check('task8: single-page fixture loaded', single.ok);
  await page.click('#mode-modify');
  await page.waitForTimeout(150);
  const rowVisibleSingle = await page.isVisible('#advance-mode-row');
  check('Advance Mode entry point is visible for a single-page atlas', rowVisibleSingle === true);

  // #chk-advance-mode is display:none (styled as a .toggle-switch); the real
  // user control is the wrapping label. Clicking it toggles the checkbox and
  // fires the change handler that reveals the toolbar.
  await page.click('#advance-mode-row');
  const toolbarVisible = await page.isVisible('#advance-toolbar');
  check('Advance Mode toolbar shows once the checkbox is toggled on', toolbarVisible === true);

  // Advance Mode persists across sessions the same way other startup-restored
  // prefs do (copySkel, meshCropping) -- exiting and re-entering
  // Edit Mode on the SAME atlas must restore it, not reset to off.
  await page.click('#mode-extract');
  await page.waitForTimeout(100);
  await page.click('#mode-modify');
  await page.waitForTimeout(150);
  const toolbarVisibleAfterReentry = await page.isVisible('#advance-toolbar');
  const checkedAfterReentry = await page.isChecked('#chk-advance-mode');
  check('Advance Mode persists across an Edit Mode exit + re-entry',
    toolbarVisibleAfterReentry === true && checkedAfterReentry === true);

  const multi = await loadFixtureAtlas(page);
  check('task8: multi-page fixture (re)loaded', multi.ok);
  await page.click('#mode-extract'); // exit back to view mode before re-entering, matching how
                                      // a real user would switch atlases between edit sessions
  await page.waitForTimeout(100);
  await page.click('#mode-modify');
  await page.waitForTimeout(150);
  const rowVisibleMulti = await page.isVisible('#advance-mode-row');
  check('Advance Mode entry point is hidden entirely for a multi-page atlas', rowVisibleMulti === false);

  // add_region() is only supported on single-page atlases, so switch back to the
  // single-page fixture before exercising the structural add.
  await loadSinglePageFixtureAtlas(page);
  const resetResult = await page.evaluate(async () => {
    const { AtlasAPI } = await import('./js/atlas-api.js');
    const solid = (w, h, rgba) => {
      const c = document.createElement('canvas'); c.width = w; c.height = h;
      c.getContext('2d').fillStyle = `rgba(${rgba.join(',')})`; c.getContext('2d').fillRect(0, 0, w, h);
      return c;
    };
    const toFile = (canvas, name) => new Promise((res) =>
      canvas.toBlob((b) => res(new File([b], name, { type: 'image/png' })), 'image/png'));
    const beforeAdd = AtlasAPI.get_region_names().length;
    const helmetFile = await toFile(solid(6, 6, [10, 200, 10, 255]), 'helmet.png');
    await AtlasAPI.add_region(helmetFile, 'testHelmet');
    const afterAdd = AtlasAPI.get_region_names().length;
    return { beforeAdd, afterAdd };
  });
  check('task8: add_region() via direct API call added one region (setup for the reset-refresh check)', resetResult.afterAdd === resetResult.beforeAdd + 1);
  // No UI path to trigger Reset here yet (Tasks 9-11 build the confirm handlers) -- reload the
  // pristine single-page fixture instead of driving the Reset button, to confirm loadRegions()
  // (which resetModify()/exitEditMode() now both call via refreshStructuralUi) really does drop
  // the structurally-added region back out of the effective list once the session is replaced.
  const afterReload = await loadSinglePageFixtureAtlas(page);
  check('task8: reloading drops the structurally-added region back out (effective model resets with the session)',
    afterReload.ok === true && afterReload.names.length === resetResult.beforeAdd && !afterReload.names.some((r) => r.key === 'testHelmet'));

  check('task8: zero page errors', errors.length === 0, errors.join('; '));
  await ctx.close();
}

// ─── Edit->View selection preservation ─────────────────────────────────────────
{
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto(URL_ROOT, { waitUntil: 'networkidle' });

  const loaded = await loadSinglePageFixtureAtlas(page);
  check('selection-preservation: single-page fixture loaded', loaded.ok);
  await page.locator('.region-item').nth(0).click(); // selects "zeta"
  await page.click('#mode-modify');
  await page.waitForTimeout(150);
  await page.click('#mode-extract'); // exit back to View Mode, no pending mods to confirm
  await page.waitForTimeout(100);
  const selected = await page.evaluate(() =>
    [...document.querySelectorAll('.region-item.selected')].map((el) => el.innerText));
  check('exiting Edit Mode keeps the current selection instead of clearing it', selected.length === 1 && selected[0] === 'zeta', selected.join(','));

  check('selection-preservation: zero page errors', errors.length === 0, errors.join('; '));
  await ctx.close();
}

// ─── Task 9: Rename flow ──────────────────────────────────────────────────────
{
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto(URL_ROOT, { waitUntil: 'networkidle' });

  const loaded = await loadSinglePageFixtureAtlas(page);
  check('task9: single-page fixture loaded', loaded.ok);
  await page.click('#mode-modify');
  await page.waitForTimeout(150);

  await page.locator('.region-item').nth(0).click();
  await page.click('#advance-mode-row');
  await page.click('#btn-rename-region');
  await page.fill('#rename-name-input', 'forearm');
  const saveEnabled = await page.isEnabled('#rename-confirm-btn');
  check('valid name enables Save', saveEnabled === true);
  await page.click('#rename-confirm-btn');
  // The confirm handler runs rename_region (a forced full repack) before the
  // sidebar rebuilds — that repack takes well over 150ms, so poll for the
  // renamed label instead of a fixed sleep (a longer fixed wait is still flaky).
  await page.waitForFunction(() => {
    const el = document.querySelector('.region-item.selected');
    return !!el && el.innerText.includes('forearm');
  }, undefined, { timeout: 5000 });
  const selectedLabel = await page.evaluate(() =>
    document.querySelector('.region-item.selected')?.innerText);
  check('sidebar shows the new label immediately', !!selectedLabel && selectedLabel.includes('forearm'), selectedLabel);

  // re-entrancy guard: typing a valid name again while the previous submission
  // is still in flight must not re-enable Save. page.click() only waits for the
  // click event + synchronous handler code to run, not the async chain inside
  // it -- so by the time it resolves, onclick has already reached its first
  // await and is genuinely 'in flight'.
  await page.locator('.region-item').nth(0).click();
  await page.click('#btn-rename-region');
  await page.fill('#rename-name-input', 'reentrant1');
  await page.click('#rename-confirm-btn');
  await page.fill('#rename-name-input', 'reentrant2');
  const stillDisabledMidFlight = await page.isDisabled('#rename-confirm-btn');
  check('Save stays disabled if the user types again during the in-flight repack', stillDisabledMidFlight === true);
  await page.waitForFunction(() => {
    const el = document.querySelector('.region-item.selected');
    return !!el && el.innerText.includes('reentrant1');
  }, undefined, { timeout: 5000 });

  // Cancel must also be blocked during the in-flight window -- this is the actual
  // loophole a prior fix round missed: without this, Cancel closes the modal,
  // reopening it creates a fresh in-flight flag, and a second submission can
  // start while the first is still pending on the same session.
  await page.locator('.region-item').nth(0).click();
  await page.click('#btn-rename-region');
  await page.fill('#rename-name-input', 'cancelblocked1');
  await page.click('#rename-confirm-btn');
  // Fire Cancel synchronously in-page (no Playwright actionability/stability
  // wait) so it lands inside the in-flight window -- page.click() would wait
  // for the element to be 'stable', and by then the real repack has already
  // finished and hidden the modal, so the click would find nothing.
  await page.evaluate(() => document.getElementById('rename-cancel-btn').click()); // should be a no-op while in flight
  const modalStillVisibleMidFlight = await page.isVisible('#rename-modal');
  check('Cancel is blocked while a submission is in flight', modalStillVisibleMidFlight === true);
  await page.waitForFunction(() => {
    const el = document.querySelector('.region-item.selected');
    return !!el && el.innerText.includes('cancelblocked1');
  }, undefined, { timeout: 5000 });
  const modalHiddenAfterCompletion = await page.isHidden('#rename-modal');
  check('Cancel becomes usable again once the in-flight submission completes (modal auto-closed on success)', modalHiddenAfterCompletion === true);

  // invalid name shows inline error, keeps Save disabled
  await page.locator('.region-item').nth(0).click();
  await page.click('#btn-rename-region');
  await page.fill('#rename-name-input', '');
  const errorVisible = await page.isVisible('#rename-name-error');
  const saveDisabled = await page.isDisabled('#rename-confirm-btn');
  check('blank name shows inline error', errorVisible === true);
  check('blank name keeps Save disabled', saveDisabled === true);
  await page.click('#rename-cancel-btn');

  // Rename only ever targets a single region -- the button itself is
  // disabled outside a 1-region selection, not just guarded on click.
  await page.locator('.region-item').nth(0).click();
  await page.locator('.region-item').nth(1).click({ modifiers: ['Control'] });
  const renameDisabledMulti = await page.isDisabled('#btn-rename-region');
  check('Rename is disabled when more than one region is selected', renameDisabledMulti === true);
  await page.locator('.region-item').nth(0).click({ modifiers: ['Control'] }); // deselect back to 1
  const renameEnabledSingle = await page.isEnabled('#btn-rename-region');
  check('Rename is enabled again once exactly one region is selected', renameEnabledSingle === true);

  check('task9: zero page errors', errors.length === 0, errors.join('; '));
  await ctx.close();
}

// ─── Task 10: Add flow (browser/PWA path) ──────────────────────────────
{
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto(URL_ROOT, { waitUntil: 'networkidle' });

  const loaded = await loadSinglePageFixtureAtlas(page);
  check('task10: single-page fixture loaded', loaded.ok);
  await page.click('#mode-modify');
  await page.waitForTimeout(150);
  await page.click('#advance-mode-row');

  await page.click('#btn-add-region');
  const chooser = page.waitForEvent('filechooser');
  await page.click('#add-drop-area');
  await (await chooser).setFiles({ name: 'stem-test.png', mimeType: 'image/png', buffer: MOD_PNG });
  await page.waitForTimeout(150);
  const autoFilledName = await page.inputValue('#add-name-input');
  check('name auto-fills from the file stem on first pick', autoFilledName === 'stem-test', autoFilledName);
  const previewVisible = await page.isVisible('#add-image-preview');
  check('image preview shows once a file is picked', previewVisible === true);

  await page.fill('#add-name-input', 'helmet');
  const addSaveEnabled = await page.isEnabled('#add-confirm-btn');
  check('valid name + picked file enables Add', addSaveEnabled === true);
  await page.click('#add-confirm-btn');
  await page.waitForFunction(() =>
    document.querySelector('.region-item[data-key="helmet"]'),
  undefined, { timeout: 5000 });
  const regionNames = await page.evaluate(async () => {
    const { AtlasAPI } = await import('./js/atlas-api.js');
    return AtlasAPI.get_region_names().map((r) => r.key);
  });
  check('added region appears in the effective list', regionNames.includes('helmet'), regionNames.join(','));

  // global drop overlay must not intercept a drop while the Add modal is open
  await page.click('#btn-add-region');
  const guardActive = await page.evaluate(() => document.body.dataset.addRegionDialogOpen === 'true');
  check('addRegionDialogOpen flag is set while the Add modal is open', guardActive === true);
  await page.click('#add-cancel-btn');
  const guardCleared = await page.evaluate(() => document.body.dataset.addRegionDialogOpen === 'true');
  check('flag is cleared on Cancel, not just on Confirm', guardCleared === false);

  // Hold Rename's structural operation in flight, then exercise the covered
  // Add toolbar button through both direct dispatch and keyboard focus. The
  // shared guard must reject every activation until Rename has settled.
  await page.locator('.region-item').nth(0).click();
  await page.click('#btn-rename-region');
  await page.fill('#rename-name-input', 'task10Lock');
  await page.evaluate(async () => {
    const { AtlasAPI } = await import('./js/atlas-api.js');
    const originalRenameRegion = AtlasAPI.rename_region;
    let releaseRename;
    const renameGate = new Promise((resolve) => { releaseRename = resolve; });
    window.__task10ReleaseRename = releaseRename;
    window.__task10RestoreRename = () => { AtlasAPI.rename_region = originalRenameRegion; };
    AtlasAPI.rename_region = async (...args) => {
      await renameGate;
      return originalRenameRegion(...args);
    };
  });
  await page.click('#rename-confirm-btn');

  await page.evaluate(() => document.getElementById('btn-add-region').click());
  const addHiddenAfterDirectClick = await page.isHidden('#add-region-modal');
  check('Add stays closed after a direct click while Rename is in flight', addHiddenAfterDirectClick === true);
  if (!addHiddenAfterDirectClick) await page.evaluate(() => document.getElementById('add-cancel-btn').click());

  await page.focus('#btn-rename-region');
  await page.keyboard.press('Tab');
  const tabFocusedAddForEnter = await page.evaluate(() => document.activeElement?.id);
  check('Tab can focus the covered Add button during Rename submission', tabFocusedAddForEnter === 'btn-add-region', tabFocusedAddForEnter);
  await page.keyboard.press('Enter');
  const addHiddenAfterEnter = await page.isHidden('#add-region-modal');
  check('Add stays closed after Tab + Enter while Rename is in flight', addHiddenAfterEnter === true);
  if (!addHiddenAfterEnter) await page.evaluate(() => document.getElementById('add-cancel-btn').click());

  await page.focus('#btn-rename-region');
  await page.keyboard.press('Tab');
  await page.keyboard.press('Space');
  const addHiddenAfterSpace = await page.isHidden('#add-region-modal');
  check('Add stays closed after Tab + Space while Rename is in flight', addHiddenAfterSpace === true);
  if (!addHiddenAfterSpace) await page.evaluate(() => document.getElementById('add-cancel-btn').click());

  await page.evaluate(() => window.__task10ReleaseRename());
  await page.waitForFunction(() => {
    const el = document.querySelector('.region-item.selected');
    return !!el && el.innerText.includes('task10Lock');
  }, undefined, { timeout: 5000 });
  await page.evaluate(() => {
    window.__task10RestoreRename();
    delete window.__task10ReleaseRename;
    delete window.__task10RestoreRename;
  });

  check('task10: zero page errors', errors.length === 0, errors.join('; '));
  await ctx.close();
}

// --- Task 11: Remove flow + empty-atlas guard ---
{
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto(URL_ROOT, { waitUntil: 'networkidle' });

  const loaded = await loadSinglePageFixtureAtlas(page);
  check('task11: single-page fixture loaded', loaded.ok);
  await page.click('#mode-modify');
  await page.waitForTimeout(150);
  await page.click('#advance-mode-row');

  await page.locator('.region-item').nth(0).click(); // selects "zeta"
  await page.click('#btn-remove-region');
  const dialogVisible = await page.evaluate(() =>
    !document.getElementById('modal-overlay').classList.contains('hidden'));
  check('Remove always confirms before removing', dialogVisible === true);
  await page.click('#btn-modal-confirm');
  await page.waitForFunction(async () => {
    const { AtlasAPI } = await import('./js/atlas-api.js');
    return AtlasAPI.get_region_names().length === 1;
  }, undefined, { timeout: 5000 });
  const namesAfterRemove = await page.evaluate(async () => {
    const { AtlasAPI } = await import('./js/atlas-api.js');
    return AtlasAPI.get_region_names().map((r) => r.key);
  });
  check('one fewer region after Remove confirms', namesAfterRemove.length === 1, namesAfterRemove.join(','));

  // select the one remaining region -> Remove must be disabled, not just refuse on click
  await page.locator('.region-item').nth(0).click();
  const removeDisabled = await page.isDisabled('#btn-remove-region');
  check('Remove is disabled when it would empty the atlas', removeDisabled === true);

  // --- Task 11 fix round 1: re-entrancy window on Remove ---
  // showConfirm() hides #modal-overlay the instant Confirm is clicked, BEFORE
  // the awaited remove_regions() starts. Until refreshStructuralUi() finishes,
  // #btn-remove-region sat uncovered and still enabled — a second click (or a
  // Tab+Enter keyboard activation) could re-enter the handler and fire a second
  // remove_regions(). The structuralOpInFlight guard must close that window.

  // Helper: reload the fixture to a fresh 2-region atlas and make sure the
  // advance toolbar is visible (mode persists across reload, but be defensive).
  async function reloadForRemoveReentry() {
    const reloaded = await loadSinglePageFixtureAtlas(page);
    if (!reloaded.ok) return { ok: false, names: [] };
    const toolbarVisible = await page.evaluate(() =>
      !document.getElementById('advance-toolbar').classList.contains('hidden'));
    if (!toolbarVisible) {
      await page.click('#mode-modify');
      await page.waitForTimeout(150);
      await page.click('#advance-mode-row');
    }
    return { ok: true, names: reloaded.names };
  }

  // Test A: double-click in the post-confirm window must not fire a 2nd remove.
  {
    const fresh = await reloadForRemoveReentry();
    check('task11 fix: fixture reloaded to 2 regions for re-entrancy test', fresh.ok && fresh.names.length === 2, fresh.names.join(','));

    await page.locator('.region-item').nth(0).click(); // selects "zeta"
    await page.evaluate(async () => {
      const { AtlasAPI } = await import('./js/atlas-api.js');
      const originalRemoveRegions = AtlasAPI.remove_regions;
      let releaseRemove;
      const removeGate = new Promise((resolve) => { releaseRemove = resolve; });
      window.__task11ReleaseRemove = releaseRemove;
      window.__task11RestoreRemove = () => { AtlasAPI.remove_regions = originalRemoveRegions; };
      AtlasAPI.remove_regions = async (...args) => {
        await removeGate;
        return originalRemoveRegions(...args);
      };
    });

    await page.click('#btn-remove-region');
    await page.click('#btn-modal-confirm'); // starts the gated await; overlay is now hidden
    // The fix disables #btn-remove-region during the in-flight window (point 3). A
    // disabled button can't be re-clicked, so to exercise the structuralOpInFlight
    // guard itself (the backstop this commit is about), re-enable it in-page to
    // simulate the pre-fix clickable state, then dispatch a real click. The guard
    // must reject the re-entrant handler entry — no second confirm dialog.
    await page.evaluate(() => {
      const b = document.getElementById('btn-remove-region');
      b.disabled = false;
      b.click();
    });
    const overlayHiddenAfterSecondClick = await page.evaluate(() =>
      document.getElementById('modal-overlay').classList.contains('hidden'));
    check('no second Remove confirm after a re-click in the post-confirm window', overlayHiddenAfterSecondClick === true);

    await page.evaluate(() => window.__task11ReleaseRemove());
    await page.waitForFunction(async () => {
      const { AtlasAPI } = await import('./js/atlas-api.js');
      return AtlasAPI.get_region_names().length === 1;
    }, undefined, { timeout: 5000 });
    const namesAfterReentry = await page.evaluate(async () => {
      const { AtlasAPI } = await import('./js/atlas-api.js');
      return AtlasAPI.get_region_names().map((r) => r.key);
    });
    check('exactly one region removed on the re-entrant double click', namesAfterReentry.length === 1, namesAfterReentry.join(','));
    await page.evaluate(() => {
      window.__task11RestoreRemove();
      delete window.__task11ReleaseRemove;
      delete window.__task11RestoreRemove;
    });
  }

  // Test B: a Tab+Enter keyboard activation must not fire a 2nd remove either.
  {
    const fresh = await reloadForRemoveReentry();
    check('task11 fix: fixture reloaded to 2 regions for keyboard re-entrancy test', fresh.ok && fresh.names.length === 2, fresh.names.join(','));

    await page.locator('.region-item').nth(0).click(); // selects "zeta"
    await page.evaluate(async () => {
      const { AtlasAPI } = await import('./js/atlas-api.js');
      const originalRemoveRegions = AtlasAPI.remove_regions;
      let releaseRemove;
      const removeGate = new Promise((resolve) => { releaseRemove = resolve; });
      window.__task11ReleaseRemove = releaseRemove;
      window.__task11RestoreRemove = () => { AtlasAPI.remove_regions = originalRemoveRegions; };
      AtlasAPI.remove_regions = async (...args) => {
        await removeGate;
        return originalRemoveRegions(...args);
      };
    });

    await page.click('#btn-remove-region');
    await page.click('#btn-modal-confirm'); // starts the gated await; overlay is now hidden
    // The fix disables #btn-remove-region during the in-flight window (point 3); a
    // disabled button can't receive Tab focus. Re-enable it in-page (pre-fix state)
    // so Tab can land on it, then Enter — the structuralOpInFlight guard must
    // reject the re-entrant keyboard activation, no second confirm dialog.
    await page.evaluate(() => { document.getElementById('btn-remove-region').disabled = false; });
    // Tab from the button just before #btn-remove-region toward it, then Enter.
    await page.focus('#btn-add-region');
    await page.keyboard.press('Tab');
    await page.keyboard.press('Enter');
    const overlayHiddenAfterTabEnter = await page.evaluate(() =>
      document.getElementById('modal-overlay').classList.contains('hidden'));
    check('no second Remove confirm after Tab + Enter while Remove is in flight', overlayHiddenAfterTabEnter === true);

    await page.evaluate(() => window.__task11ReleaseRemove());
    await page.waitForFunction(async () => {
      const { AtlasAPI } = await import('./js/atlas-api.js');
      return AtlasAPI.get_region_names().length === 1;
    }, undefined, { timeout: 5000 });
    const namesAfterKeyboardReentry = await page.evaluate(async () => {
      const { AtlasAPI } = await import('./js/atlas-api.js');
      return AtlasAPI.get_region_names().map((r) => r.key);
    });
    check('exactly one region removed on the keyboard re-entrant activation', namesAfterKeyboardReentry.length === 1, namesAfterKeyboardReentry.join(','));
    await page.evaluate(() => {
      window.__task11RestoreRemove();
      delete window.__task11ReleaseRemove;
      delete window.__task11RestoreRemove;
    });
  }

  check('task11: zero page errors', errors.length === 0, errors.join('; '));
  await ctx.close();
}

// ─── #options-row overflow: wrap + collapse instead of horizontal scroll ───
// Groups wrap as whole <li> units. Collapsed height stays 35px; extra lines
// clip until the chevron expands the row. Labels must still be single-line
// (nowrap) so they are not clipped vertically inside a 35px flex line.
for (const vp of [{ w: 430, h: 800 }, { w: 390, h: 844 }, { w: 360, h: 800 }, { w: 320, h: 800 }]) {
  const ctx = await browser.newContext({ viewport: { width: vp.w, height: vp.h }, isMobile: true, hasTouch: true });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto(URL_ROOT, { waitUntil: 'networkidle' });

  const loaded = await loadSinglePageFixtureAtlas(page);
  check(`portrait ${vp.w}x${vp.h}: single-page fixture loaded`, loaded.ok);
  await page.click('#mode-modify');
  await page.waitForTimeout(150);

  await page.evaluate(async () => {
    const skelBtn = document.getElementById('btn-pick-skel');
    skelBtn.classList.remove('hidden');
    skelBtn.textContent = 'very_long_skeleton_filename_to_force_wrap.skel';
    await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
  });

  const geometry = await page.evaluate(() => {
    const row = document.getElementById('options-row');
    const rowClientHeight = row.clientHeight;
    const labels = [...row.querySelectorAll('.toggle-label')].map((l) => {
      const cs = getComputedStyle(l);
      return { id: l.id, height: l.getBoundingClientRect().height, visible: cs.display !== 'none' };
    });
    const gearBtn = document.getElementById('btn-nest-settings');
    const skelBtn = document.getElementById('btn-pick-skel');
    const collapseBtn = document.getElementById('btn-options-collapse');
    const collapseCs = getComputedStyle(collapseBtn);
    return {
      rowClientHeight,
      rowHeight: row.getBoundingClientRect().height,
      scrollWidth: row.scrollWidth,
      clientWidth: row.clientWidth,
      overflowing: row.classList.contains('is-overflowing'),
      expanded: row.classList.contains('is-expanded'),
      collapseVisible: collapseCs.display !== 'none',
      labels,
      gearBtnWidth: gearBtn.getBoundingClientRect().width,
      skelBtnWidth: skelBtn.getBoundingClientRect().width,
    };
  });
  const visibleLabels = geometry.labels.filter((l) => l.visible);
  check(`portrait ${vp.w}x${vp.h}: at least the 3 edit-mode toggle labels are visible`,
    visibleLabels.length >= 3, JSON.stringify(geometry.labels));
  const clipped = visibleLabels.filter((l) => l.height > geometry.rowClientHeight + 1); // +1px rounding slack
  check(`portrait ${vp.w}x${vp.h}: REGRESSION CHECK -- no toggle label is taller than #options-row's content box (clipped)`,
    clipped.length === 0, JSON.stringify(geometry));
  check(`portrait ${vp.w}x${vp.h}: options-row stays at its 35px header height while collapsed`,
    geometry.rowHeight <= 36, `rowHeight=${geometry.rowHeight}`);
  check(`portrait ${vp.w}x${vp.h}: REGRESSION CHECK -- no horizontal scroll on #options-row`,
    geometry.scrollWidth <= geometry.clientWidth + 1,
    `scrollWidth=${geometry.scrollWidth} clientWidth=${geometry.clientWidth}`);
  check(`portrait ${vp.w}x${vp.h}: REGRESSION CHECK -- gear button is not collapsed to zero content width`,
    geometry.gearBtnWidth >= 22, `gearBtnWidth=${geometry.gearBtnWidth}`);
  check(`portrait ${vp.w}x${vp.h}: REGRESSION CHECK -- .skel picker button is not collapsed to zero content width`,
    geometry.skelBtnWidth >= 30, `skelBtnWidth=${geometry.skelBtnWidth}`);
  check(`portrait ${vp.w}x${vp.h}: overflow chevron matches is-overflowing`,
    geometry.overflowing === geometry.collapseVisible,
    JSON.stringify({ overflowing: geometry.overflowing, collapseVisible: geometry.collapseVisible }));
  check(`portrait ${vp.w}x${vp.h}: row starts collapsed`, !geometry.expanded);

  if (geometry.overflowing) {
    await page.click('#btn-options-collapse');
    await page.waitForTimeout(50);
    const expandedGeo = await page.evaluate(() => {
      const row = document.getElementById('options-row');
      const groups = [...row.querySelectorAll(':scope > li')].filter((li) => getComputedStyle(li).display !== 'none').map((li) => {
        const kids = [...li.children].filter((c) => getComputedStyle(c).display !== 'none');
        const box = li.getBoundingClientRect();
        const rowBox = row.getBoundingClientRect();
        return {
          id: li.id,
          height: Math.round(box.height),
          sameLine: box.height <= 42,
          fullyVisible: box.top >= rowBox.top - 1 && box.bottom <= rowBox.bottom + 1,
          kidCount: kids.length,
          lineStart: li.classList.contains('is-line-start'),
          borderLeft: parseFloat(getComputedStyle(li).borderLeftWidth) || 0,
        };
      });
      const btn = document.getElementById('btn-options-collapse');
      const wrap = document.getElementById('options-row-wrap').getBoundingClientRect();
      const btnBox = btn.getBoundingClientRect();
      return {
        expanded: row.classList.contains('is-expanded'),
        rowHeight: row.getBoundingClientRect().height,
        scrollWidth: row.scrollWidth,
        clientWidth: row.clientWidth,
        groups,
        chevronTop: btnBox.top + btnBox.height / 2,
        firstRowCenter: wrap.top + 17.5,
        chevronRight: wrap.right - btnBox.right,
      };
    });
    check(`portrait ${vp.w}x${vp.h}: chevron expands the row past 35px`,
      expandedGeo.expanded && expandedGeo.rowHeight > 36,
      JSON.stringify({ height: expandedGeo.rowHeight, expanded: expandedGeo.expanded }));
    check(`portrait ${vp.w}x${vp.h}: expanded row still does not scroll horizontally`,
      expandedGeo.scrollWidth <= expandedGeo.clientWidth + 1,
      `scrollWidth=${expandedGeo.scrollWidth} clientWidth=${expandedGeo.clientWidth}`);
    check(`portrait ${vp.w}x${vp.h}: each visible <li> group stays on one line`,
      expandedGeo.groups.every((g) => g.sameLine), JSON.stringify(expandedGeo.groups));
    check(`portrait ${vp.w}x${vp.h}: expanded groups are fully visible in the row`,
      expandedGeo.groups.every((g) => g.fullyVisible), JSON.stringify(expandedGeo.groups));
    check(`portrait ${vp.w}x${vp.h}: chevron stays vertically centered on row 1`,
      Math.abs(expandedGeo.chevronTop - expandedGeo.firstRowCenter) <= 3,
      JSON.stringify({ chevronTop: expandedGeo.chevronTop, firstRowCenter: expandedGeo.firstRowCenter }));
    check(`portrait ${vp.w}x${vp.h}: chevron stays flush right`,
      expandedGeo.chevronRight >= 0 && expandedGeo.chevronRight <= 10,
      `chevronRight=${expandedGeo.chevronRight}`);
    check(`portrait ${vp.w}x${vp.h}: Advance Mode is the last visible group`,
      expandedGeo.groups.at(-1)?.id === 'options-group-advance',
      JSON.stringify(expandedGeo.groups.map((g) => g.id)));
    check(`portrait ${vp.w}x${vp.h}: first group on each wrap line has no vertical divider`,
      expandedGeo.groups.every((g) => g.lineStart === (g.borderLeft === 0)),
      JSON.stringify(expandedGeo.groups));
    const lineStarts = expandedGeo.groups.filter((g) => g.lineStart);
    check(`portrait ${vp.w}x${vp.h}: wrapped lines each have a divider-free start`,
      lineStarts.length >= 1 && lineStarts.every((g) => g.borderLeft === 0),
      JSON.stringify(lineStarts));
  }

  check(`portrait ${vp.w}x${vp.h}: zero page errors`, errors.length === 0, errors.join('; '));
  await ctx.close();
}

// ─── Long-press help on options-row toggles must not flip the checkbox ────────
{
  const ctx = await browser.newContext({
    viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true,
  });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto(URL_ROOT, { waitUntil: 'networkidle' });

  const loaded = await loadSinglePageFixtureAtlas(page);
  check('touch long-press: single-page fixture loaded', loaded.ok);
  const coarse = await page.evaluate(() => matchMedia('(pointer: coarse)').matches);
  check('touch long-press: viewport reports coarse pointer (long-press path)', coarse === true);
  const before = await page.isChecked('#chk-mesh-mask');
  await page.evaluate(() => {
    const el = document.getElementById('mesh-mask-toggle-row');
    const t = new Touch({ identifier: 0, target: el, clientX: 20, clientY: 20 });
    el.dispatchEvent(new TouchEvent('touchstart', {
      bubbles: true, cancelable: true, touches: [t], targetTouches: [t], changedTouches: [t],
    }));
  });
  await page.waitForTimeout(500);
  await page.evaluate(() => {
    const el = document.getElementById('mesh-mask-toggle-row');
    el.dispatchEvent(new TouchEvent('touchend', {
      bubbles: true, cancelable: true, touches: [], targetTouches: [], changedTouches: [],
    }));
  });
  await page.waitForTimeout(80);
  const after = await page.isChecked('#chk-mesh-mask');
  const helpShown = await page.evaluate(() => !document.getElementById('opt-help-popover').classList.contains('hidden'));
  check('touch: long-press does not toggle Mesh Cropping', after === before, `before=${before} after=${after}`);
  check('touch: long-press shows help popover', helpShown === true);
  check('touch long-press: zero page errors', errors.length === 0, errors.join('; '));
  await ctx.close();
}

// ─── Advance Mode + portrait splitter interaction (panel-resizer.js) ──────────
{
  const ctx = await browser.newContext({
    viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true,
  });
  // Force the splitter's stored height to whatever the (pre-Advance-Mode)
  // floor allows, matching the exact scenario setAdvanceMode() calling
  // refreshPanelSplit() fixes: the right panel already occupying as much
  // height as the floor allowed *before* #advance-toolbar entered the
  // layout and needed its own share of #left-panel's height too.
  await ctx.addInitScript(() => {
    localStorage.setItem('atlastoolkit.layout.portrait.previewHeight', '10000');
  });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto(URL_ROOT, { waitUntil: 'networkidle' });

  const loaded = await loadSinglePageFixtureAtlas(page);
  check('portrait splitter: single-page fixture loaded', loaded.ok);
  await page.click('#mode-modify');
  await page.waitForTimeout(150);
  await page.click('#advance-mode-row');
  await page.waitForTimeout(100);

  const geometry = await page.evaluate(() => {
    const leftPanel = document.getElementById('left-panel');
    const advanceToolbar = document.getElementById('advance-toolbar');
    const sidebarHead = document.getElementById('sidebar-head');
    const leftRect = leftPanel.getBoundingClientRect();
    const toolbarRect = advanceToolbar.getBoundingClientRect();
    const headRect = sidebarHead.getBoundingClientRect();
    return {
      toolbarVisible: getComputedStyle(advanceToolbar).display !== 'none',
      toolbarFitsInLeftPanel: toolbarRect.bottom <= leftRect.bottom + 1, // +1px rounding slack
      sidebarHeadFitsInLeftPanel: headRect.bottom <= leftRect.bottom + 1,
    };
  });
  check('portrait: Advance Mode toolbar is visible in stacked layout', geometry.toolbarVisible === true);
  // #advance-toolbar is #left-panel's FIRST child -- it never overflows on its
  // own regardless of this bug, so this is a basic sanity check, not proof of
  // the fix. #sidebar-head (the SECOND child) is what actually gets pushed
  // past #left-panel's bottom edge without the fix -- that's the one
  // mutation-tested to fail on the pre-fix code; see the commit message.
  check('portrait: toolbar sanity check -- not itself clipped (first child, not the regression case)',
    geometry.toolbarFitsInLeftPanel === true, JSON.stringify(geometry));
  check('portrait: REGRESSION CHECK -- sidebar-head (2nd child) stays inside the left panel once Advance Mode reserves its own space',
    geometry.sidebarHeadFitsInLeftPanel === true);

  check('portrait splitter: zero page errors', errors.length === 0, errors.join('; '));
  await ctx.close();
}

await browser.close();
server.close();
console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
