/**
 * LRU cache of preview blob: URLs. JobRunner must not import this module.
 */

export function viewPreviewKey(names, loadEpoch, meshMaskEnabled, skelEpoch) {
  const sorted = [...(names || [])].map(String).sort();
  return `view:${sorted.join('\0')}:${loadEpoch}:${meshMaskEnabled ? 1 : 0}:${skelEpoch}`;
}

export function editPreviewKey(pageIndex, modGeneration, loadEpoch, packSig = '') {
  const base = `edit:${pageIndex}:${modGeneration}:${loadEpoch}`;
  return packSig === '' || packSig == null ? base : `${base}:${packSig}`;
}

/** Pack-option fingerprint so Edit A→B→A (nest/gap/mesh-aware) can hit. */
export function packSignature(nestEnabled, gapDistance, meshAware) {
  return `${nestEnabled ? 1 : 0}:${Number(gapDistance)}:${meshAware ? 1 : 0}`;
}

export class PreviewCache {
  constructor(maxSize = 24) {
    this.maxSize = maxSize;
    this._map = new Map(); // key -> blob: URL, insertion order = LRU
  }

  get(key) {
    if (!this._map.has(key)) return null;
    const url = this._map.get(key);
    this._map.delete(key);
    this._map.set(key, url);
    return url;
  }

  set(key, url) {
    if (this._map.has(key)) {
      const prev = this._map.get(key);
      this._map.delete(key);
      if (prev && prev !== url) this._revoke(prev);
    }
    this._map.set(key, url);
    while (this._map.size > this.maxSize) {
      const oldest = this._map.keys().next().value;
      const evicted = this._map.get(oldest);
      this._map.delete(oldest);
      this._revoke(evicted);
    }
  }

  owns(url) {
    if (!url) return false;
    for (const cached of this._map.values()) {
      if (cached === url) return true;
    }
    return false;
  }

  invalidateAll() {
    for (const url of this._map.values()) this._revoke(url);
    this._map.clear();
  }

  _revoke(url) {
    if (url && String(url).startsWith('blob:')) {
      try { URL.revokeObjectURL(url); } catch { /* already revoked */ }
    }
  }
}

/** LRU of packed Edit-mode session snapshots. Does not revoke blob URLs. */
export class PackResultCache {
  constructor(maxSize = 12) {
    this.maxSize = maxSize;
    this._map = new Map();
  }

  get(key) {
    if (!this._map.has(key)) return null;
    const value = this._map.get(key);
    this._map.delete(key);
    this._map.set(key, value);
    return value;
  }

  set(key, value) {
    if (this._map.has(key)) this._map.delete(key);
    this._map.set(key, value);
    while (this._map.size > this.maxSize) {
      const oldest = this._map.keys().next().value;
      this._map.delete(oldest);
    }
  }

  invalidateAll() {
    this._map.clear();
  }
}
