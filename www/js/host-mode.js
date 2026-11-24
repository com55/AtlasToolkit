// SPDX-FileCopyrightText: 2026 com55
// SPDX-License-Identifier: Apache-2.0
/**
 * Host mode: another app opens an atlas here and gets the edit back.
 * Web: an <iframe> of this app with ?host=<origin>, talking postMessage.
 * Desktop: the app started with --return <dir> (startup reports a job).
 * Off unless one of those is present; a normal visit never reaches it.
 */

export const PROTOCOL = 1;
const LABEL_LIMIT = 40;

let context = null;
let warnedOrigin = false;

/** Host-supplied text for display: a string, trimmed, at most 40 characters, else `fallback`. */
export function cleanLabel(text, fallback) {
  const value = text == null ? '' : String(text).trim().slice(0, LABEL_LIMIT).trim();
  return value || fallback;
}

/** The host origin from `?host=` when this page is framed, or null. Only a bare origin counts. */
export function webHostOrigin(search, framed) {
  if (!framed) return null;
  const value = new URLSearchParams(search || '').get('host');
  if (!value) return null;
  let origin = null;
  try { origin = new URL(value).origin; } catch (_) { /* not a URL */ }
  if (origin && origin !== 'null' && origin === value) return value;
  if (!warnedOrigin) {
    warnedOrigin = true;
    console.warn('Host mode off: ?host must be an origin such as https://example.com');
  }
  return null;
}

/** The message's data when it comes from the host window and origin with the protocol marker, else null. */
export function acceptMessage(event, origin, parentWindow) {
  if (!event || event.origin !== origin || event.source !== parentWindow) return null;
  const data = event.data;
  if (!data || typeof data !== 'object' || data.atlasToolkitHost !== PROTOCOL) return null;
  return data;
}

/** {kind: 'web'|'desktop', hostName, saveLabel} while in host mode, else null. */
export function hostContext() { return context; }
export function setHostContext(value) { context = value || null; }

/** Badge, Save label and the controls host mode hides (style.css, body.host-mode). */
export function applyHostUi(ctx) {
  document.body.classList.add('host-mode');
  const badge = document.getElementById('host-badge');
  if (badge) {
    badge.querySelector('.host-badge-text').textContent = `From ${ctx.hostName}`;
    badge.dataset.help = `Opened from ${ctx.hostName}. Save sends the edited files back.`;
    badge.classList.remove('hidden');
  }
  const save = document.getElementById('btn-save-mod');
  const label = save && [...save.childNodes].find(n => n.nodeType === 3 && n.textContent.trim());
  if (label) label.textContent = ` ${ctx.saveLabel} `;
}

/** Host-mode copy shared by drops and the close question. */
export function atlasDropRefusal(ctx) {
  return `This atlas was opened from ${ctx.hostName}. Only a .skel can be dropped.`;
}
export function discardQuestion(ctx) {
  return {
    message: `These changes have not been sent to ${ctx.hostName}.`,
    title: 'Discard changes?',
    labels: {confirmLabel: 'Discard', cancelLabel: 'Keep editing'},
  };
}

/** Desktop --return: Save & Return writes into the caller's folder, then the app closes. */
export function initDesktopHost(job, {desktopCall, writeFilesToFolder, setSaveSink}) {
  const ctx = {kind: 'desktop', hostName: cleanLabel(job?.hostName, 'another app'), saveLabel: 'Save & Return'};
  setHostContext(ctx);
  applyHostUi(ctx);
  setSaveSink({
    async deliver(outputs) {
      try {
        const folder = await desktopCall('return_folder');
        // The caller owns the folder: replace its files without asking.
        await writeFilesToFolder(folder, outputs, async () => true);
        return {ok: true, message: 'Saved. Returning...'};
      } catch (e) {
        return {ok: false, message: e?.message || String(e)};
      }
    },
    afterSaved: outputs => desktopCall('finish_return', [outputs.map(o => o.name)]),
  });
  return ctx;
}

/**
 * Web host: this page is an <iframe> of the host at `origin`. Messages (all
 * carry atlasToolkitHost: 1; see README "Embedding"):
 *   app -> host  ready {appVersion, protocol} | opened {atlas} | open-failed {message}
 *                save {id, files} | close {ok} | download {filename, blob}
 *   host -> app  open {files, hostName?, saveLabel?} | progress {text} | saved {id, ok, message?} | close-request
 * `deps` are the app pieces this needs, passed in so the module stays testable.
 */
export function initWebHost(origin, deps) {
  const {appVersion, waitForEngine, loadFiles, hasPending, showConfirm, withBusy, setSaveSink, setDownloadSink} = deps;
  const parent = window.parent;
  const post = message => parent.postMessage({atlasToolkitHost: PROTOCOL, ...message}, origin);
  const pendingSaves = new Map();
  let saveSeq = 0;
  let ctx = {kind: 'web', hostName: 'another app', saveLabel: 'Save'};
  setHostContext(ctx);
  applyHostUi(ctx);
  setDownloadSink((filename, blob) => post({type: 'download', filename, blob}));
  setSaveSink({
    deliver: outputs => withBusy(`Sending to ${ctx.hostName}...`, () => new Promise(resolve => {
      const id = ++saveSeq;
      pendingSaves.set(id, resolve);
      post({type: 'save', id, files: outputs.map(({name, data}) => ({
        name, blob: data instanceof Blob ? data : new Blob([data], {type: 'text/plain'}),
      }))});
    })),
  });

  async function open(message) {
    ctx = {
      kind: 'web',
      hostName: cleanLabel(message.hostName, 'another app'),
      saveLabel: cleanLabel(message.saveLabel, 'Save'),
    };
    setHostContext(ctx);
    applyHostUi(ctx);
    const files = Array.isArray(message.files) ? message.files.filter(f => f instanceof Blob) : [];
    if (hasPending() && !(await showConfirm('You have unsaved atlas modifications.\nContinue and discard them?', 'Discard modifications?'))) {
      post({type: 'open-failed', message: 'Cancelled'});
      return;
    }
    // Hosts send files right after "ready", usually while the engine is still
    // starting: wait under the busy overlay with its progress ring, as a
    // normal early open does.
    await waitForEngine();
    let result = false;
    try { result = await loadFiles(files); } catch (e) { console.error(e); }
    if (result === true) {
      const atlas = files.find(f => !/\.(png|skel)$/i.test(f.name || ''));
      post({type: 'opened', atlas: atlas?.name || ''});
    } else {
      post({type: 'open-failed', message: result === 'cancelled' ? 'Cancelled' : 'Atlas Toolkit could not open these files.'});
    }
  }

  async function closeRequest() {
    // A host that never answered a save would leave the frame busy: give up on it.
    for (const resolve of pendingSaves.values()) resolve({ok: false, message: 'Cancelled'});
    pendingSaves.clear();
    await Promise.resolve();
    let ok = true;
    if (hasPending()) {
      const question = discardQuestion(ctx);
      ok = await showConfirm(question.message, question.title, question.labels);
    }
    post({type: 'close', ok});
  }

  window.addEventListener('message', event => {
    const message = acceptMessage(event, origin, parent);
    if (!message) return;
    if (message.type === 'open') open(message).catch(console.error);
    else if (message.type === 'progress') {
      const label = document.getElementById('busy-label');
      if (label && pendingSaves.size) label.textContent = cleanText(message.text);
    } else if (message.type === 'saved') {
      // The id may come back as a string after the host serialised it.
      const id = Number(message.id);
      const resolve = pendingSaves.get(id);
      pendingSaves.delete(id);
      const text = message.message == null ? '' : cleanText(message.message);
      resolve?.({ok: message.ok === true, message: text || (message.ok === true ? `Sent to ${ctx.hostName}.` : '')});
    } else if (message.type === 'close-request') closeRequest().catch(console.error);
  });

  // "ready" once the page is styled, so a host can show the frame without a flash.
  const styled = Promise.all([...document.querySelectorAll('link[rel="stylesheet"]')].map(link =>
    link.sheet ? null : new Promise(resolve => {
      link.addEventListener('load', resolve, {once: true});
      link.addEventListener('error', resolve, {once: true});
    })));
  styled.then(() => post({type: 'ready', appVersion, protocol: PROTOCOL}));
}

/** Host text shown in a status or busy label: a string, at most 200 characters. */
function cleanText(text) {
  return String(text ?? '').slice(0, 200);
}
