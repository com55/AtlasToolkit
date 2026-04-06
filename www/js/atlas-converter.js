/**
 * atlas-converter.js
 * Port of atlas_converter.py
 * Converts LibGDX atlas format → Spine atlas format.
 */

const PAGE_KEYS = new Set(['size', 'format', 'filter', 'repeat', 'pma']);

function _isPageLine(line) {
  return /^.+\.(png|jpg|jpeg|webp|bmp|gif)$/i.test(line.trim());
}

function _parseKV(line) {
  if (!line.includes(':')) return null;
  const idx = line.indexOf(':');
  const key = line.slice(0, idx).trim().toLowerCase();
  const values = line.slice(idx + 1).split(',').map(v => v.trim());
  return { key, values };
}

export function isOldFormat(atlasText) {
  for (const line of atlasText.split('\n')) {
    const s = line.trim().toLowerCase();
    if (s.startsWith('xy:') || s.startsWith('orig:') || s.startsWith('offset:')) return true;
  }
  return false;
}

export function convertAtlasToNewFormat(atlasText) {
  const lines = atlasText.split('\n');
  const result = [];

  let inPageHeader = false;
  let inRegion = false;
  let regionNameLine = '';
  let regionKV = {};
  let regionExtraLines = [];

  function flushRegion() {
    if (!regionNameLine) return;
    result.push(regionNameLine);

    if ('index' in regionKV) result.push(`  index: ${regionKV['index'][0]}`);

    const rotateVal = (regionKV['rotate'] || ['false'])[0].trim().toLowerCase();
    result.push(`  rotate: ${rotateVal}`);

    if ('bounds' in regionKV) {
      const b = regionKV['bounds'];
      result.push(`  bounds: ${b[0]}, ${b[1]}, ${b[2]}, ${b[3]}`);
    } else {
      const xy = regionKV['xy'] || ['0', '0'];
      const sz = regionKV['size'] || ['0', '0'];
      result.push(`  bounds: ${xy[0]}, ${xy[1]}, ${sz[0]}, ${sz[1]}`);
    }

    if ('offsets' in regionKV) {
      const o = regionKV['offsets'];
      result.push(`  offsets: ${o[0]}, ${o[1]}, ${o[2]}, ${o[3]}`);
    } else if ('orig' in regionKV) {
      const orig = regionKV['orig'];
      const offset = regionKV['offset'] || ['0', '0'];
      result.push(`  offsets: ${offset[0]}, ${offset[1]}, ${orig[0]}, ${orig[1]}`);
    }

    for (const k of ['split', 'pad']) {
      if (k in regionKV) result.push(`  ${k}: ${regionKV[k].join(', ')}`);
    }

    result.push(...regionExtraLines);
    inRegion = false;
  }

  for (const rawLine of lines) {
    const stripped = rawLine.trim();

    if (!stripped) {
      if (inRegion) {
        flushRegion();
        regionNameLine = '';
        regionKV = {};
        regionExtraLines = [];
      }
      inPageHeader = false;
      result.push(rawLine);
      continue;
    }

    if (_isPageLine(stripped)) {
      if (inRegion) {
        flushRegion();
        regionNameLine = '';
        regionKV = {};
        regionExtraLines = [];
      }
      inPageHeader = true;
      inRegion = false;
      result.push(rawLine);
      continue;
    }

    const parsed = _parseKV(rawLine);
    if (parsed) {
      const { key, values } = parsed;
      if (inPageHeader) {
        result.push(rawLine);
        if (!PAGE_KEYS.has(key)) inPageHeader = false;
        continue;
      }
      if (inRegion) {
        regionKV[key] = values;
      } else {
        result.push(rawLine);
      }
      continue;
    }

    // No colon → new region name
    if (inRegion) {
      flushRegion();
      regionNameLine = '';
      regionKV = {};
      regionExtraLines = [];
    }
    inPageHeader = false;
    inRegion = true;
    regionNameLine = rawLine;
    regionKV = {};
    regionExtraLines = [];
  }

  if (inRegion) flushRegion();

  return result.join('\n');
}

export function autoConvertAtlas(atlasText) {
  return isOldFormat(atlasText) ? convertAtlasToNewFormat(atlasText) : atlasText;
}
