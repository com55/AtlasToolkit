/**
 * nestPack job worker. Imports only nestPack — never atlas-modifier.js.
 */
import { nestPack } from '../js/repack-nest.js';

self.onmessage = (event) => {
  const data = event.data || {};
  const { id, type, items, options } = data;
  try {
    if (type !== 'nestPack') throw new Error(`unknown job type: ${type}`);
    const packedItems = (items || []).map((it) => ({
      name: it.name,
      w: it.w,
      h: it.h,
      footprint: it.footprint,
    }));
    const result = nestPack(packedItems, options || {});
    self.postMessage({ id, ok: true, result });
  } catch (err) {
    self.postMessage({ id, ok: false, error: String(err && err.message ? err.message : err) });
  }
};
