// SPDX-FileCopyrightText: 2026 com55
// SPDX-License-Identifier: Apache-2.0
/**
 * About dialog: Info / Credits / Licenses. Versions come from the running
 * build: the app version stamped into index.html, and the library, runtime
 * and package versions the engine host reports (AppHost.about), so each build
 * lists only what it actually ships.
 */
import { AtlasAPI } from './atlas-api.js';

const REPO = 'https://github.com/com55/AtlasToolkit';
// Shown one at a time behind a picker, so every file is visible from the top.
const LICENSE_FILES = [
  ['THIRD_PARTY_NOTICES.md', 'Third-party notices'],
  ['LICENSE', 'License (Apache-2.0)'],
  ['NOTICE', 'Notice'],
];
// name → [url, license]; shown only when the host reports the package.
const THIRD_PARTY = {
  Pyodide: ['https://github.com/pyodide/pyodide', 'MPL-2.0'],
  Python: ['https://www.python.org/', 'PSF-2.0'],
  NumPy: ['https://github.com/numpy/numpy', 'BSD-3-Clause and others'],
  Pillow: ['https://github.com/python-pillow/Pillow', 'MIT-CMU'],
  'skel-mesh-parser': ['https://github.com/com55/skel-mesh-parser', 'Apache-2.0'],
  pywebview: ['https://github.com/r0x0r/pywebview', 'BSD-3-Clause'],
  requests: ['https://github.com/psf/requests', 'Apache-2.0'],
};

let aboutInfo = null;
let licenseText = null;
let licenseFile = LICENSE_FILES[0][0];

function esc(text) {
  return String(text).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);
}

function link(url, text) {
  return `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(text)}</a>`;
}

export function appVersion() {
  return document.querySelector('meta[name="app-version"]')?.content || '';
}

function runtimeLine(info) {
  if (!info) return '…';
  const { name, version } = info.runtime;
  return name === 'Pyodide' ? `Pyodide ${version} (Python ${info.python})` : `Python ${version}`;
}

function infoTab(info) {
  const row = (label, value) => `<div class="about-row"><span>${label}</span><span>${value}</span></div>`;
  return `<p class="about-lead">Spine Atlas Toolkit - Extract, modify, and repack atlas sprites.</p>
    <div class="about-rows">
      ${row('Author', link('https://github.com/com55', 'com55'))}
      ${row('Source', link(REPO, 'github.com/com55/AtlasToolkit'))}
      ${row('Engine', info ? `spine-atlas-toolkit ${esc(info.library)}` : '…')}
      ${row('Runtime', esc(runtimeLine(info)))}
      ${row('Donation', link('https://ko-fi.com/com55', 'Ko-fi'))}
    </div>
    <p class="about-note">Neither this tool nor its author is affiliated with, endorsed by, or associated with Esoteric Software or the Spine runtime.</p>`;
}

function creditsTab(info) {
  const year = new Date().getFullYear();
  const used = info ? [info.runtime.name, ...Object.keys(info.components)] : [];
  const items = used.filter(name => THIRD_PARTY[name]).map(name => {
    const [url, license] = THIRD_PARTY[name];
    const version = name === info.runtime.name ? info.runtime.version : info.components[name];
    return `<li>${link(url, name)}${version ? ` <span class="about-dim">${esc(version)}</span>` : ''} <span class="about-dim">· ${esc(license)}</span></li>`;
  }).join('');
  return `<p>© ${year > 2026 ? `2026–${year}` : '2026'} com55. AtlasToolkit is open source under the Apache License 2.0.</p>
    <p class="about-sub">Third-party software</p>
    ${info ? `<ul class="about-list">${items}</ul>` : '<p class="about-dim">Loading…</p>'}
    <p class="about-note">Atlas format reference: ${link('https://esotericsoftware.com/spine-atlas-format', 'Spine Atlas Format')}.</p>`;
}

/** Inline markdown on escaped text: `code`, **bold**, [text](url), bare URLs. */
function inline(text) {
  return esc(text)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
    .replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, '$1<a href="$2" target="_blank" rel="noopener">$2</a>');
}

/** The markdown our notice files use: headings, paragraphs, lists, tables. */
export function renderMarkdown(text) {
  const lines = text.replace(/\r\n?/g, '\n').split('\n');
  const out = [];
  let paragraph = [];
  const flush = () => { if (paragraph.length) { out.push(`<p>${inline(paragraph.join(' '))}</p>`); paragraph = []; } };
  const cells = row => row.trim().replace(/^\||\|$/g, '').split('|').map(cell => cell.trim());
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    if (heading) { flush(); out.push(`<h${heading[1].length + 3}>${inline(heading[2])}</h${heading[1].length + 3}>`); continue; }
    if (line.trim().startsWith('|') && /^\s*\|?\s*:?-{3,}/.test(lines[i + 1] || '')) {
      flush();
      const head = cells(line).map(cell => `<th>${inline(cell)}</th>`).join('');
      const rows = [];
      for (i += 2; i < lines.length && lines[i].trim().startsWith('|'); i++) {
        rows.push(`<tr>${cells(lines[i]).map(cell => `<td>${inline(cell)}</td>`).join('')}</tr>`);
      }
      i--;
      out.push(`<div class="about-table"><table><thead><tr>${head}</tr></thead><tbody>${rows.join('')}</tbody></table></div>`);
      continue;
    }
    const item = /^\s*[-*]\s+(.*)$/.exec(line);
    if (item) {
      flush();
      const items = [item[1]];
      while (i + 1 < lines.length && /^\s*[-*]\s+/.test(lines[i + 1])) items.push(lines[++i].replace(/^\s*[-*]\s+/, ''));
      out.push(`<ul>${items.map(entry => `<li>${inline(entry)}</li>`).join('')}</ul>`);
      continue;
    }
    if (!line.trim()) { flush(); continue; }
    paragraph.push(line.trim());
  }
  flush();
  return out.join('');
}

function licensesTab() {
  const picker = `<div class="about-files" role="tablist">${LICENSE_FILES.map(([name, label]) =>
    `<button type="button" class="about-file-btn${name === licenseFile ? ' active' : ''}" role="tab" aria-selected="${name === licenseFile}" data-file="${esc(name)}">${esc(label)}</button>`).join('')}</div>`;
  if (licenseText === null) return `${picker}<p class="about-dim">Loading…</p>`;
  const { name, text } = licenseText.find(entry => entry.name === licenseFile);
  return `${picker}${name.endsWith('.md')
    ? `<div class="about-md">${renderMarkdown(text)}</div>`
    : `<div class="about-scroll"><pre class="about-license">${esc(text)}</pre></div>`}`;
}

async function loadLicenses() {
  licenseText = await Promise.all(LICENSE_FILES.map(async ([name]) => {
    try {
      const response = await fetch(name);
      return { name, text: response.ok ? await response.text() : 'Not available in this build.' };
    } catch {
      return { name, text: 'Not available in this build.' };
    }
  }));
}

function overlay() {
  return document.getElementById('about-overlay');
}

function render(tab) {
  const root = overlay();
  root.querySelectorAll('.about-tab').forEach(button => {
    const on = button.dataset.tab === tab;
    button.classList.toggle('active', on);
    button.setAttribute('aria-selected', String(on));
  });
  const body = root.querySelector('.about-body');
  body.innerHTML = tab === 'credits' ? creditsTab(aboutInfo) : tab === 'licenses' ? licensesTab() : infoTab(aboutInfo);
  body.querySelectorAll('.about-file-btn').forEach(button => {
    button.onclick = () => { licenseFile = button.dataset.file; render('licenses'); body.scrollTop = 0; };
  });
  root.dataset.tab = tab;
}

function close() {
  overlay()?.classList.add('hidden');
}

function build() {
  const root = document.createElement('div');
  root.id = 'about-overlay';
  root.className = 'hidden';
  const version = appVersion();
  root.innerHTML = `<div id="about" role="dialog" aria-modal="true" aria-labelledby="about-title">
      <div class="about-head">
        <img src="icons/icon-192.png" alt="" width="56" height="56">
        <h3 id="about-title">AtlasToolkit</h3>
        <p class="about-dim">${esc(version ? `v${version}` : '')}</p>
      </div>
      <div class="about-tabs" role="tablist">
        <button type="button" class="about-tab" role="tab" data-tab="info">Info</button>
        <button type="button" class="about-tab" role="tab" data-tab="credits">Credits</button>
        <button type="button" class="about-tab" role="tab" data-tab="licenses">Licenses</button>
      </div>
      <div class="about-body" role="tabpanel"></div>
      <div class="modal-buttons"><button type="button" class="btn-primary" id="about-close">Close</button></div>
    </div>`;
  document.body.append(root);
  root.addEventListener('click', e => { if (e.target === root) close(); });
  root.addEventListener('keydown', e => { if (e.key === 'Escape') close(); });
  root.querySelectorAll('.about-tab').forEach(button => {
    button.onclick = () => {
      render(button.dataset.tab);
      if (button.dataset.tab === 'licenses' && licenseText === null) {
        loadLicenses().then(() => { if (root.dataset.tab === 'licenses') render('licenses'); });
      }
    };
  });
  document.getElementById('about-close').onclick = close;
  return root;
}

export function showAbout() {
  const root = overlay() || build();
  render('info');
  root.classList.remove('hidden');
  document.getElementById('about-close').focus({ preventScroll: true });
  if (!aboutInfo) {
    AtlasAPI.about().then(info => {
      aboutInfo = info;
      if (!root.classList.contains('hidden')) render(root.dataset.tab || 'info');
    }).catch(() => { /* engine unavailable: rows keep their placeholders */ });
  }
}

export function initAbout() {
  const button = document.getElementById('btn-about');
  if (button) button.onclick = showAbout;
}
