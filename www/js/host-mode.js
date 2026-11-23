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
