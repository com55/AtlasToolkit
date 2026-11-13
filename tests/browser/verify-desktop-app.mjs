// SPDX-FileCopyrightText: 2026 com55
// SPDX-License-Identifier: Apache-2.0
/** Real desktop transport, native-dialog harness, and page integration. */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawn, spawnSync} from 'node:child_process';
import {createInterface} from 'node:readline';
import assert from 'node:assert/strict';
import {decodeFrame} from '../../www/js/rpc-frame.js';
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
async function waitInPage(page, asyncPredicate, timeout = 60000, arg = null) {
  await page.evaluate(async ({source, timeout, arg}) => {
    const predicate = (0, eval)('(' + source + ')');
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      if (await predicate(arg)) return;
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    throw new Error('Page condition did not become true within ' + timeout + ' ms');
  }, {source: asyncPredicate.toString(), timeout, arg});
}

async function loadChromium() {
  const candidates = [
    process.env.PLAYWRIGHT_CORE,
    'playwright-core',
    'playwright',
    path.join(os.homedir(), '.npm-global/lib/node_modules/@playwright/mcp/node_modules/playwright-core/index.js'),
    path.join(os.homedir(), '.npm-global/lib/node_modules/playwright-core/index.js'),
    '/usr/lib/node_modules/playwright-core/index.js',
  ].filter(Boolean);
  for (const candidate of candidates) {
    try {
      const mod = await import(candidate);
      const pw = mod.chromium ? mod : mod.default;
      if (pw && pw.chromium) return pw.chromium;
    } catch { /* try next */ }
  }
  return null;
}

if (!fs.existsSync(path.join(ROOT, 'dist/desktop/index.html'))) {
  console.log('SKIP: dist/desktop/index.html is missing; run the desktop build first.');
  process.exit(0);
}
const chromium = await loadChromium();
if (!chromium) {
  console.log('SKIP: playwright-core not found (set $PLAYWRIGHT_CORE to its index.js to run).');
  process.exit(0);
}
const scratch = path.join(ROOT, '.workspaces');
fs.mkdirSync(scratch, {recursive:true});
const temp = fs.mkdtempSync(path.join(scratch, 'desktop-browser-'));
const fixtures = path.join(temp, 'fixtures');
const python = process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python';
const generated = spawnSync(python, ['scripts/serve_desktop_dev.py', '--no-update-check', '--make-fixture', fixtures], {cwd:ROOT, encoding:'utf8'});
assert.equal(generated.status, 0, generated.stderr);
let browser;
let failed = false;
let serial = 0;
async function scenario(label, atlas, check, extra = []) {
  let child, page;
  const lines = [];
  let stderr = '';
  let exited = false;
  const nextLine = async (predicate, timeout = 30000) => {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      const index = lines.findIndex(predicate);
      if (index >= 0) return lines.splice(index, 1)[0];
      if (exited) throw new Error('Harness exited: ' + stderr);
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    throw new Error('Harness response timeout: ' + stderr);
  };
  try {
    const config = path.join(temp, 'config-' + serial++);
    child = spawn(python, ['scripts/serve_desktop_dev.py', '--no-update-check', '--config', config,
      ...(atlas ? ['--atlas', path.join(fixtures, atlas)] : []), ...extra], {cwd:ROOT, stdio:['pipe','pipe','pipe']});
    child.on('error', error => {stderr += error.message; exited = true;});
    child.on('exit', () => {exited = true;});
    child.stderr.on('data', data => {stderr += data;});
    createInterface({input:child.stdout}).on('line', line => lines.push(line));
    const url = (await nextLine(line => line.startsWith('URL '), 60000)).slice(4);
    const command = async (text, expected = 'ok') => {
      child.stdin.write(text + '\n');
      return nextLine(line => expected === null || line === expected);
    };
    page = await browser.newPage();
    page.setDefaultTimeout(60000);
    const requests = [], frames = [];
    page.on('request', request => {
      requests.push(request.url());
      if (new URL(request.url()).pathname.endsWith('/api/rpc')) {
        frames.push(decodeFrame(request.postDataBuffer()).header);
      }
    });
    const startupResponses = [];
    page.on('response', response => {
      if (new URL(response.url()).pathname.endsWith('/api/rpc') &&
          decodeFrame(response.request().postDataBuffer()).header.method === 'startup') {
        startupResponses.push(response.body().then(body => JSON.parse(decodeFrame(body).header.json)));
      }
    });
    await page.goto(url);
    const idle = () => waitInPage(page, async () => !(await import(new URL('js/busy-overlay.js', location.href))).isBusy());
    const regions = async names => {
      await page.waitForFunction(names => {
        const labels = [...document.querySelectorAll('.region-item')].map(el => el.dataset.label);
        return labels.length === names.length && names.every(name => labels.includes(name));
      }, names, {timeout:60000, polling:500});
      await idle();
    };
    const ready = () => page.waitForFunction(() => document.querySelector('#status-text').textContent === 'Ready', null, {timeout:60000,polling:500});
    const open = async file => {
      await command('dialog ' + path.join(fixtures, file));
      await page.locator('.btn-open').click();
    };
    const edit = async () => {
      if (!await page.evaluate(() => document.body.classList.contains('mode-modify'))) {
        await page.locator('#mode-modify').click();
        await waitInPage(page, async () => document.body.classList.contains('mode-modify') && !(await import(new URL('js/busy-overlay.js', location.href))).isBusy());
      }
      await page.locator('.region-item[data-label="a"]').click();
      const previous = frames.filter(frame => frame.method === 'modify').length;
      await command('dialog ' + path.join(fixtures, 'mod.png'));
      await page.locator('#btn-modify-sel').click();
      const deadline = Date.now() + 60000;
      while (frames.filter(frame => frame.method === 'modify').length <= previous) {
        if (Date.now() > deadline) throw new Error('Modify RPC missing');
        await new Promise(resolve => setTimeout(resolve, 100));
      }
      await idle();
      await page.waitForFunction(() => document.querySelector('#status-text').textContent.includes('Ready to save'), null, {timeout:60000,polling:500});
      await page.locator('#btn-save-mod').waitFor({state:'visible'});
      assert.equal(await command('unsaved', null), 'true');
    };
    await check({page, command, regions, ready, open, edit, idle, requests, frames, startupResponses, url, lines, nextLine});
    console.log('PASS ' + label);
  } catch (error) {
    failed = true;
    console.error('FAIL ' + label + ': ' + error.stack);
  } finally {
    await page?.close();
    if (child && !exited) {
      child.stdin.write('quit\n');
      await Promise.race([new Promise(resolve => child.once('exit', resolve)), new Promise(resolve => setTimeout(resolve, 2000))]);
      if (!exited) child.kill('SIGKILL');
    }
  }
}
try {
  browser = await chromium.launch({headless:true});
  await scenario('1 startup atlas and desktop transport', 'one/one.atlas', async ({page,regions,requests}) => {
    await regions(['a','b']);
    assert(await page.evaluate(() => document.documentElement.classList.contains('pywebview')));
    assert(!requests.some(url => url.toLowerCase().includes('pyodide')));
  });
  await scenario('2 Open', 'one/one.atlas', async ({regions,open}) => {
    await regions(['a','b']); await open('two/two.atlas'); await regions(['c']);
  });
  await scenario('3 Edit and Save with revision', 'one/one.atlas', async ({page,regions,edit,command,idle,frames}) => {
    await regions(['a','b']); await edit();
    const folder = path.join(temp, 'saved'); fs.mkdirSync(folder);
    await command('dialog ' + folder); await page.locator('#btn-save-mod').click();
    await page.waitForFunction(() => !document.querySelector('#btn-save-mod').disabled && [...document.querySelectorAll('.toast.success')].length > 0, null, {polling:500});
    await idle();
    const files = fs.readdirSync(folder);
    assert(files.includes('one.atlas')); assert(files.some(name => name.endsWith('.png')));
    const text = fs.readFileSync(path.join(folder, 'one.atlas'), 'utf8');
    const parsed = spawnSync(python, ['-c', 'import sys; from atlas_toolkit.document import AtlasDocument; d = AtlasDocument.parse(sys.stdin.read()); assert d.pages',], {cwd:ROOT,input:text,encoding:'utf8'});
    assert.equal(parsed.status, 0, parsed.stderr);
    assert.equal(await command('unsaved', null), 'false');
    const saved = frames.filter(frame => frame.method === 'mark_saved'); assert(saved.length);
    for (const frame of saved) {const args = JSON.parse(frame.argsJson); assert.equal(args.length,1); assert.equal(typeof args[0], 'number');}
  });
  await scenario('4 Save cancelled', 'one/one.atlas', async ({page,regions,edit,command}) => {
    await regions(['a','b']); await edit();
    const snapshot = () => fs.readdirSync(temp, {recursive:true}).sort()
      .filter(name => fs.statSync(path.join(temp,name)).isFile())
      .map(name => [name, fs.readFileSync(path.join(temp,name)).toString('base64')]);
    const before = snapshot();
    await command('dialog-cancel'); await page.locator('#btn-save-mod').click();
    await page.locator('.toast.info').filter({hasText:'Cancelled'}).waitFor();
    assert.deepEqual(snapshot(), before);
    assert(await page.locator('#modal-overlay').isHidden());
    assert.equal(await command('unsaved', null), 'true');
  });
  await scenario('5 missing-images drop', 'missing/missing.atlas', async ({page,command}) => {
    const missing = page.locator('.missing-images-row[data-page-name="missing.png"]');
    const row = page.locator('.missing-images-row[data-page-name="other.png"]');
    await row.waitFor(); const box = await row.boundingBox(); assert(box);
    await command(`drop ${path.join(fixtures,'missing-src/missing.png')} ${box.x+box.width/2} ${box.y+box.height/2}`);
    await row.locator('.missing-images-status').filter({hasText:'missing.png'}).waitFor();
    assert(await row.evaluate(el => el.classList.contains('selected')));
    assert(!await missing.evaluate(el => el.classList.contains('selected')));
    assert.match(await row.locator('.missing-images-status').textContent(), /missing\.png/);
    await command('drop ' + path.join(fixtures,'missing-src/missing.png'));
    await missing.locator('.missing-images-status').filter({hasText:'missing.png'}).waitFor();
    assert(await missing.evaluate(el => el.classList.contains('selected')));
  });
  await scenario('6 unsaved close Cancel and Confirm', 'one/one.atlas', async ({page,regions,edit,command,lines,nextLine}) => {
    await regions(['a','b']); await edit();
    assert.equal(await command('close', null), 'vetoed');
    const cancelled = page.waitForResponse(response =>
      response.url().endsWith('/api/rpc') &&
      decodeFrame(response.request().postDataBuffer()).header.method === 'close_answer');
    await page.locator('#btn-modal-cancel').click();
    await cancelled;
    await page.locator('#modal-overlay').waitFor({state:'hidden'});
    assert(!lines.includes('destroyed'));
    assert.equal(await command('close', null), 'vetoed');
    await page.locator('#btn-modal-confirm').click();
    await nextLine(line => line === 'destroyed');
  });
  await scenario('7 reload after Open', 'one/one.atlas', async ({page,regions,open,ready,startupResponses}) => {
    await regions(['a','b']); await open('two/two.atlas'); await regions(['c']);
    await Promise.all(startupResponses);
    const previous = startupResponses.length;
    const startup = page.waitForResponse(response => response.url().endsWith('/api/rpc') &&
      decodeFrame(response.request().postDataBuffer()).header.method === 'startup');
    await page.reload();
    await startup;
    assert.equal(startupResponses.length, previous + 1);
    const reply = await startupResponses[previous];
    assert.equal(reply.ok, true);
    assert.equal(reply.value.atlas, null);
    await ready(); await regions([]);
    await open('one/one.atlas'); await regions(['a','b']);
  });
  await scenario('8 foreign origin and wrong token', null, async ({page,url}) => {
    const rpc = new URL('api/rpc',url);
    const foreign = await page.request.post(rpc.href,{headers:{Origin:'http://evil.test'},data:Buffer.from([0])});
    assert.equal(foreign.status(),403);
    const wrong = new URL('/s/wrong-token/api/rpc',url);
    const response = await page.request.post(wrong.href,{headers:{Origin:rpc.origin},data:Buffer.from([0])});
    assert.equal(response.status(),404);
  });
  await scenario('9 failing startup remains usable', null, async ({page,ready,open,regions}) => {
    await page.locator('#modal-overlay').filter({has:page.locator('#modal-message').filter({hasText:'Startup check failed'})}).waitFor({state:'visible',timeout:120000});
    await page.locator('#btn-modal-confirm').click();
    await ready(); await open('one/one.atlas'); await regions(['a','b']);
  }, ['--fail-startup']);
} finally {
  await browser?.close();
  fs.rmSync(temp,{recursive:true,force:true});
}
process.exitCode = failed ? 1 : 0;
