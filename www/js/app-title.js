/**
 * Window / tab title. Desktop native title uses the same formula via
 * bridge.set_window_title (version from pyproject.toml / VERSION).
 */

export function formatAppTitle(version, atlasFilename) {
  const v = String(version || '').replace(/^v/i, '').trim();
  const base = v ? `Atlas Toolkit v${v}` : 'Atlas Toolkit';
  const name = String(atlasFilename || '').trim();
  return name ? `${base} - ${name}` : base;
}

export function versionFromDocument() {
  return document.querySelector('meta[name="app-version"]')?.content?.trim() || '';
}
