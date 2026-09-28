/**
 * One View Mode preview. The main thread does not wait for this to finish
 * before starting another selection's worker.
 */
import { extractRegionFromPage } from '../js/core-region-ops.js';
import { renderPreviewCanvases } from '../js/atlas-extracter.js';
import { canvasToPngBlob } from '../js/canvas-surface.js';

function closeBitmaps(pages) {
  for (const page of pages || []) {
    try { page.bitmap?.close?.(); } catch { /* already closed */ }
  }
}

self.onmessage = (event) => {
  const data = event.data || {};
  const { id, forceResize, regions, pages } = data;
  const fail = (err) => {
    closeBitmaps(pages);
    self.postMessage({ id, ok: false, error: String(err && err.message ? err.message : err) });
  };
  try {
    const byPage = new Map((pages || []).map((page) => [page.id, page.bitmap]));
    const images = [];
    for (const region of regions || []) {
      const bitmap = byPage.get(region.pageId);
      if (!bitmap) continue;
      const canvas = extractRegionFromPage(bitmap, region, region.page, region.mesh);
      if (canvas) images.push(canvas);
    }
    const rendered = renderPreviewCanvases(images, !!forceResize);
    closeBitmaps(pages);
    if (!rendered) {
      self.postMessage({ id, ok: true, buffer: null });
      return;
    }
    canvasToPngBlob(rendered).then(async (blob) => {
      if (!blob) {
        self.postMessage({ id, ok: true, buffer: null });
        return;
      }
      const buffer = await blob.arrayBuffer();
      self.postMessage({ id, ok: true, buffer }, [buffer]);
    }).catch(fail);
  } catch (err) {
    fail(err);
  }
};
