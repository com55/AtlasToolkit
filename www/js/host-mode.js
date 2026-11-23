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
