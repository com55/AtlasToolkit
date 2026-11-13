// SPDX-FileCopyrightText: 2026 com55
// SPDX-License-Identifier: Apache-2.0
import test from 'node:test';
import { before, beforeEach, after, afterEach } from 'node:test';
import { AtlasAPI } from '../www/js/atlas-api.js';
import { desktopCall, onDesktopEvent, _resetForTests } from '../www/js/desktop.js';
import { startEngine, stopEngine, HostError } from '../www/js/engine-client.js';
import { fileFromUrl, pickSaveFolder, savePref } from '../www/js/platform.js';
import { withLock, resetBusyForTests, lastBusyMessage, BUSY_WAIT_MESSAGE } from '../www/js/busy-overlay.js';
import { openAddRegionModal, showMissingAtlasImagesDialog } from '../www/js/dialogs.js';
import assert from 'node:assert/strict';
import { fakeDesktopServer } from './helpers/fake-desktop-server.js';
import { loadPref, writeFilesToFolder, saveFileWithDialog } from '../www/js/platform.js';

// Small event-capable DOM stub, following the existing tests' hand-built DOMs.
const nodes = new Map(), created = [];
function node() {
  const classes = new Set(['hidden']), events = new Map();
  return {
    style: {}, dataset: {}, children: [], value: '', innerText: '', textContent: '',
    classList: { add: n => classes.add(n), remove: n => classes.delete(n), contains: n => classes.has(n), toggle() {} },
    addEventListener(k, f) { if (!events.has(k)) events.set(k, new Set()); events.get(k).add(f); },
    removeEventListener(k, f) { events.get(k)?.delete(f); },
    async emit(k, e = {}) { for (const f of events.get(k) || []) await f(e); },
    appendChild(c) { this.children.push(c); }, remove() {}, focus() {}, blur() {}, setAttribute() {}, removeAttribute() {},
    querySelector() { return this.children.find(c => c.className?.includes('btn')); },
    closest() { return this; },
  };
}
let originals, handleDesktopDrop;
before(async () => {
  const keys = ['document', 'window', 'FileReader', 'Image', 'Worker', 'ImageData', 'EventSource'];
  originals = keys.map(k => [k, Object.getOwnPropertyDescriptor(globalThis, k)]);
  globalThis.window = { showToast() {}, showAlert() {}, addEventListener() {}, removeEventListener() {}, matchMedia: () => ({matches: false}) };
  globalThis.document = {
    getElementById(id) { if (!nodes.has(id)) nodes.set(id, node()); return nodes.get(id); },
    createElement() { const n = node(); created.push(n); return n; },
    body: node(), querySelectorAll: () => [],
    elementFromPoint: () => null,
  };
  globalThis.FileReader = class {
    async readAsText(file) { this.onload({target: {result: await file.text()}}); }
  };
  globalThis.Worker = class { terminate() {} };
  globalThis.EventSource = class { close() {} };
  globalThis.Image = class { set src(v) { this.onload?.(); } };
  ({handleDesktopDrop} = await import('../www/js/drop.js'));
});
beforeEach(t => { t.mock.timers.enable({apis: ['setTimeout']}); });
afterEach(() => { stopEngine(); _resetForTests(); resetBusyForTests(); document.body.dataset = {}; nodes.get('modal-overlay')?.classList.add('hidden'); });
after(() => originals.forEach(([k, d]) => d ? Object.defineProperty(globalThis, k, d) : delete globalThis[k]));

const grant = { grant: 'g', path: 'output/image.png' };
test('desktop preferences and folder writes use commands and binary buffers', async t => {
  const calls = fakeDesktopServer(t, { get_pref: () => 2, existing_output_names: () => [], write_files: () => null });
  assert.equal(await loadPref('k', 1), 2);
  await writeFilesToFolder(grant, [{ name: 'a.png', data: new Blob(['png']) }], () => true);
  assert.deepEqual(calls.map(c => c.method), ['get_pref', 'existing_output_names', 'write_files']);
  assert.deepEqual(calls[0].args, ['k', 1]);
  assert.deepEqual(calls[2].args, ['g', [{ name: 'a.png', data: new TextEncoder().encode('png') }]]);
});
test('declined folder overwrite writes nothing', async t => {
  const calls = fakeDesktopServer(t, { existing_output_names: () => ['a.png'] });
  await assert.rejects(writeFilesToFolder(grant, [{ name: 'a.png', data: 'x' }], () => false), { name: 'AbortError' });
  assert.equal(calls.length, 1);
});
for (const accepted of [true, false]) test(`adjusted save asks before writing (${accepted})`, async t => {
  const previous = globalThis.window;
  t.after(() => { globalThis.window = previous; });
  let confirms = 0;
  globalThis.window = { showConfirm: async () => { confirms++; return accepted; } };
  const calls = fakeDesktopServer(t, { pick_save_file: () => ({ ...grant, adjusted: true }), existing_output_names: () => ['image.png'], write_files: () => null });
  assert.equal(await saveFileWithDialog('image.png', new Blob(['x'])), accepted ? true : null);
  assert.equal(confirms, 1);
  assert.deepEqual(calls.map(c => c.method), accepted ? ['pick_save_file', 'existing_output_names', 'write_files'] : ['pick_save_file', 'existing_output_names']);
});


const bundle = { atlas: {name: 'hero.atlas', url: 'asset/atlas'}, pages: {'page.png': {name: 'page.png', url: 'asset/page'}}, skel: {name: 'hero.skel', url: 'asset/skel'}, dir: 'source' };
const assets = {'asset/atlas': 'page.png\nsize: 1,1\n', 'asset/page': 'png', 'asset/skel': 'skel'};
const file = {name: 'page.png', url: 'asset/page'};
async function boot(t) { const pending = startEngine(); t.mock.timers.tick(0); await pending; }
const engineHandlers = { required_pages: () => ['page.png'], load: () => ({mesh: {}, pages: ['page.png']}) };

test('asset fetch uses the descriptor name and MIME and rejects non-OK', async t => {
  fakeDesktopServer(t, {}, assets);
  const f = await fileFromUrl(file, 'image/png');
  assert.equal(f.name, 'page.png'); assert.equal(f.type, 'image/png'); assert.equal(await f.text(), 'png');
  await assert.rejects(fileFromUrl({name:'absent.png', url:'asset/absent'}), /Failed to read file/);
});
test('desktop prefs default on error, save is fire-and-forget, folder cancel returns null', async t => {
  const calls = fakeDesktopServer(t, {get_pref: () => {throw new Error('bad');}, set_pref: () => null, pick_save_folder: () => null});
  assert.equal(await loadPref('k', 7), 7);
  savePref('k', 8);
  assert.equal(await pickSaveFolder(), null);
  assert.deepEqual(calls.map(c => [c.method,c.args]), [['get_pref',['k',7]], ['set_pref',['k',8]], ['pick_save_folder',[]]]);
});
test('bundle fetch loads atlas, page bytes and captured skeleton; missing pages go to dialog', async t => {
  let captured;
  const calls = fakeDesktopServer(t, {...engineHandlers, load: (...args) => {captured = args; return {mesh:{}};}}, assets);
  await boot(t);
  assert.equal(await AtlasAPI.load_atlas_bundle(bundle), true);
  assert.equal(AtlasAPI.get_current_atlas_directory(), 'source');
  assert.deepEqual(captured.slice(0,2), ['hero.atlas', assets['asset/atlas']]);
  assert.deepEqual(captured[2], {'page.png': new TextEncoder().encode('png')});
  assert.equal(captured[3], 'hero.skel'); assert.deepEqual(captured[4], new TextEncoder().encode('skel'));
  const old = window.showMissingAtlasImages;
  t.after(() => {window.showMissingAtlasImages = old;});
  window.showMissingAtlasImages = async names => {assert.deepEqual(names, ['page.png']); return {'page.png':new File(['replacement'],'page.png')};};
  assert.equal(await AtlasAPI.load_atlas_bundle({...bundle,pages:{'page.png':{...file,url:'asset/absent'}}}), true);
  assert.equal(calls.filter(c => c.method === 'load').length,2);
});
test('choose_file cancellation, bundle and HostError use pick_atlas', async t => {
  let picked = null;
  const calls = fakeDesktopServer(t, {...engineHandlers, pick_atlas: () => {if (picked instanceof Error) throw picked; return picked;}}, assets);
  await boot(t);
  assert.equal(await AtlasAPI.choose_file(),'cancelled');
  picked = bundle; assert.equal(await AtlasAPI.choose_file(),true);
  let toast;
  t.mock.method(window,'showToast', (...args) => {toast=args;});
  picked = new HostError('OSError','unreadable'); assert.equal(await AtlasAPI.choose_file(),false);
  assert.deepEqual(toast,['Failed to load atlas file: unreadable','error']);
  assert.ok(calls.filter(c=>c.method==='pick_atlas').every(c=>c.args.length===0));
});
test('pick_skel and pick_mod_image fetch descriptors into Files', async t => {
  const calls=fakeDesktopServer(t,{pick_skel:()=>bundle.skel,pick_mod_image:()=>file},assets);
  let skel,mod;
  t.mock.method(AtlasAPI,'apply_skel_file',async f=>{skel=f;return true;});
  t.mock.method(AtlasAPI,'process_mod_image',async (f,keys)=>{mod=f;assert.deepEqual(keys,['r']);return true;});
  assert.equal(await AtlasAPI.pick_skel_file(),true); assert.equal(skel.name,'hero.skel');
  assert.equal(await AtlasAPI.select_mod_image(['r']),true); assert.equal(mod.name,'page.png');
  assert.deepEqual(calls.map(c=>[c.method,c.args]),[['pick_skel',[]],['pick_mod_image',[]]]);
});
for (const pending of [false,true]) test(`save_modified validates revision and preserves pending edits (${pending})`,async t=>{
  const output={revision:4,text:'atlas',pages:[{name:'page.png',png:new Uint8Array([1])}]};
  const calls=fakeDesktopServer(t,{...engineHandlers,save_output:()=>output,get_pref:()=>true,pick_save_folder:()=>grant,existing_output_names:()=>[],write_files:()=>null,mark_saved:revision=>{assert.equal(revision,4);return {hasPending:pending};}},assets);
  await boot(t); await AtlasAPI.load_atlas_bundle(bundle);
  assert.equal(await AtlasAPI.save_modified(),`Saved to: ${grant.path}`+(pending?' (edits made during the save were not included)':''));
  const writes=calls.find(c=>c.method==='write_files');
  assert.deepEqual(writes.args[1].map(f=>f.name),['page.png','hero.atlas','hero.skel']);
  assert.ok(writes.args[1].every(f=>f.data instanceof Uint8Array));
  delete output.revision;
  assert.equal(await AtlasAPI.save_modified(),'Error: save_output returned no revision');
  assert.equal(calls.filter(c=>c.method==='mark_saved').length,1);
});
for (const status of [403,500]) test(`HTTP ${status} rejection alert behavior`,async t=>{
  const calls=[];
  t.mock.method(window,'showAlert',(...args)=>calls.push(args));
  t.mock.method(globalThis,'fetch',async()=>({status}));
  await assert.rejects(desktopCall('bad'), e=>e.type===(status===403?'DesktopRequestRejected':'DesktopServerError') && (status!==403||e.message==='The app refused the request (HTTP 403).'));
  assert.equal(calls.length,status===403?0:1);
});
test('event interruptions warn once until next successful message',t=>{
  let source;
  t.mock.method(globalThis,'EventSource',class {constructor(){source=this;} close() {}});
  const warnings=[];t.mock.method(console,'warn',s=>warnings.push(s));
  onDesktopEvent('drop',()=>{});
  source.onerror();source.onerror();assert.equal(warnings.length,1);
  source.onmessage({data:'{"kind":"drop"}'});source.onerror();assert.equal(warnings.length,2);
  assert.ok(warnings.every(s=>s==='Desktop event stream interrupted; reconnecting.'));
});
test('browser startEngine constructs one module Worker without fetch',async t=>{
  fakeDesktopServer(t);location.pathname='/index.html';
  const workers=[];
  t.mock.method(globalThis,'Worker',class {constructor(url,opts){workers.push({url,opts,worker:this});} terminate(){}});
  t.mock.method(globalThis,'fetch',()=>assert.fail('browser engine must not fetch'));
  const pending=startEngine();assert.equal(startEngine(),pending);
  assert.equal(workers.length,1);assert.match(String(workers[0].url),/workers\/engine-host-worker\.js$/);assert.deepEqual(workers[0].opts,{type:'module'});
  workers[0].worker.onmessage({data:{type:'ready'}});await pending;
});

function dropActions(t) {
  const seen=[];
  return {seen, actions:{openAtlasBundle:async (...a)=>seen.push(['atlas',...a]),applyModImage:async f=>seen.push(['png',f]),confirmDiscard:async()=>true}};
}
test('desktop drop error and unsupported types hide overlay and show error',async t=>{
  fakeDesktopServer(t);
  const {actions}=dropActions(t);
  await handleDesktopDrop({type:'error',message:'Unreadable'},actions);
  assert.equal(document.getElementById('modal-message').innerText,'Unreadable');
  assert.equal(document.getElementById('drop-overlay').classList.contains('hidden'),true);
  assert.equal(document.getElementById('drop-overlay').style.pointerEvents,'none');
  await document.getElementById('btn-modal-confirm').emit('click');
  await handleDesktopDrop({type:'file',file:{name:'other.txt',url:'asset/other'}},actions);
  assert.equal(document.getElementById('modal-message').innerText,'Unsupported file type.');
  await document.getElementById('btn-modal-confirm').emit('click');
});
test('desktop drop missing dialog routes PNG with coordinates before busy and ignores atlas',async t=>{
  fakeDesktopServer(t,{},assets);
  const {actions,seen}=dropActions(t);
  const start=created.length;
  const pending=showMissingAtlasImagesDialog(['first.png','page.png']);
  const row=created.slice(start).find(n=>n.dataset.pageName==='first.png');
  let coords;
  t.mock.method(document,'elementFromPoint',(x,y)=>{coords=[x,y];return row;});
  await withLock(async()=>{
    await handleDesktopDrop({type:'file',file,x:12,y:34},actions);
    await handleDesktopDrop({type:'atlas',bundle},actions);
  });
  assert.deepEqual(coords,[12,34]);assert.equal(seen.length,0);
  const firstStatus=row.children.find(n=>n.className==='missing-images-status');
  assert.match(firstStatus.innerText,/page.png/);
  // No coordinates: filename matching supplies the other row.
  await handleDesktopDrop({type:'file',file},actions);
  const load=created.slice(start).find(n=>n.innerText==='Load');
  await load.emit('click');
  const chosen=await pending;assert.equal(chosen['first.png'].name,'page.png');assert.equal(chosen['page.png'].name,'page.png');
});
test('desktop drop add dialog routes PNG and ignores other types before busy',async t=>{
  fakeDesktopServer(t,{},assets);
  const {actions,seen}=dropActions(t);
  openAddRegionModal({getEffectiveNames:()=>[],onConfirm:async()=>{}});
  await withLock(async()=>{
    await handleDesktopDrop({type:'file',file},actions);
    await handleDesktopDrop({type:'atlas',bundle},actions);
  });
  assert.equal(document.getElementById('add-name-input').value,'page');assert.equal(seen.length,0);
  document.getElementById('add-cancel-btn').onclick();
});
test('desktop drop busy conflict prevents actions',async t=>{
  fakeDesktopServer(t);const {actions,seen}=dropActions(t);
  await withLock(()=>handleDesktopDrop({type:'atlas',bundle},actions));
  assert.equal(lastBusyMessage(),BUSY_WAIT_MESSAGE);assert.equal(seen.length,0);
});
for(const accepted of [false,true]) test(`atlas drop respects discard answer (${accepted})`,async t=>{
  fakeDesktopServer(t);const {actions,seen}=dropActions(t);let confirms=0;
  t.mock.method(AtlasAPI,'has_pending_modifications',()=>true);
  actions.confirmDiscard=async()=>{confirms++;return accepted;};
  await handleDesktopDrop({type:'atlas',bundle},actions);
  assert.equal(confirms,1);assert.deepEqual(seen,accepted?[['atlas',bundle,{viaDrop:true}]]:[]);
});
test('atlas drop with no edits skips confirmation; PNG routes mod image',async t=>{
  fakeDesktopServer(t);const {actions,seen}=dropActions(t);
  t.mock.method(AtlasAPI,'has_pending_modifications',()=>false);
  actions.confirmDiscard=()=>assert.fail('no edits');
  await handleDesktopDrop({type:'atlas',bundle},actions);
  await handleDesktopDrop({type:'file',file},actions);
  assert.deepEqual(seen,[['atlas',bundle,{viaDrop:true}],['png',file]]);
});
test('skeleton drop fetches the file and applies skeleton',async t=>{
  fakeDesktopServer(t,{},assets);const {actions,seen}=dropActions(t);let selected;
  t.mock.method(AtlasAPI,'get_current_atlas_filename',()=> 'hero.atlas');
  t.mock.method(AtlasAPI,'apply_skel_file',async f=>{selected=f;return false;});
  await handleDesktopDrop({type:'file',file:bundle.skel},actions);
  assert.equal(selected.name,'hero.skel');assert.equal(await selected.text(),'skel');assert.equal(seen.length,0);
  await document.getElementById('btn-modal-confirm').emit('click');
});

test('confirm-close rejects closing while a modal is open without showing a confirm', async t => {
  const calls = fakeDesktopServer(t, { close_answer: () => null });
  const { handleConfirmClose } = await import('../www/script.js');
  document.getElementById('modal-overlay').classList.remove('hidden');
  document.getElementById('modal-message').innerText = 'Existing alert';
  document.getElementById('modal-title').innerText = 'Existing title';
  t.mock.method(document.getElementById('btn-modal-confirm'), 'addEventListener', () => assert.fail('showConfirm must not bind listeners'));
  await handleConfirmClose();
  assert.deepEqual(calls.map(c => [c.method, c.args]), [['close_answer', [false]]]);
  assert.equal(document.getElementById('modal-message').innerText, 'Existing alert');
  assert.equal(document.getElementById('modal-title').innerText, 'Existing title');
  assert.equal(document.getElementById('modal-overlay').classList.contains('hidden'), false);
});

test('404 atlas rejects at the API and openAtlasBundle toasts without throwing', async t => {
  fakeDesktopServer(t, {}, {});
  await assert.rejects(AtlasAPI.load_atlas_bundle(bundle), /Failed to read file/);
  const { openAtlasBundle } = await import('../www/script.js');
  t.mock.method(console, 'error', () => {});
  assert.equal(await openAtlasBundle(bundle), false);
  assert.equal(document.getElementById('modal-message').innerText, 'Failed to load atlas file.');
});
test('404 skeleton still loads atlas without applying skeleton', async t => {
  let captured;
  const warnings = [];
  t.mock.method(console, 'warn', (...args) => warnings.push(args));
  fakeDesktopServer(t, {...engineHandlers, load: (...args) => { captured = args; return {mesh: {}}; }}, {'asset/atlas': assets['asset/atlas'], 'asset/page': 'png'});
  await boot(t);
  assert.equal(await AtlasAPI.load_atlas_bundle(bundle), true);
  assert.equal(captured[3], null);
  assert.equal(captured[4], null);
  assert.equal(warnings.length, 1);
});
