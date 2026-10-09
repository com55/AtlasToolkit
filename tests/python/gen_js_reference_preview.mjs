/**
 * Cross-language parity helper for the View Mode preview helpers in
 * atlas_toolkit/engine/extraction.py and domain/region_ops.py -- runs the
 * real www/js/atlas-extracter.js / core-region-ops.js / lanczos-resize.js
 * exports (all pure, no Canvas) in plain Node against a JSON array of {op, args} cases on
 * stdin and prints the results, in order, as JSON. Invoked by
 * tests/python/conftest.py's run_preview_oracle fixture.
 */
import { extractedCanvasSize } from '../../www/js/core-region-ops.js';
import {
  hullPolyline, layoutPreviewHulls, pickForceResizeTarget, previewCompositeFrame,
} from '../../www/js/atlas-extracter.js';
// CPU path only: resizeCanvasLanczos prefers a WebGL2 shader in a browser.
import { lanczosResizeRGBA } from '../../www/js/lanczos-resize.js';

const OPS = {
  lanczosResizeRGBA: (src, srcW, srcH, dstW, dstH) => Array.from(
    lanczosResizeRGBA(Uint8ClampedArray.from(src), srcW, srcH, dstW, dstH),
  ),
  extractedCanvasSize: (region, page) => extractedCanvasSize(region, page),
  pickForceResizeTarget: (sizes) => pickForceResizeTarget(sizes),
  previewCompositeFrame: (sizes, force) => previewCompositeFrame(sizes, force),
  // Layout plus each item's polyline, so both halves are checked together.
  layoutPreviewHulls: (entries, force) => {
    const layout = layoutPreviewHulls(entries, force);
    return { ...layout, polylines: layout.items.map((item) => hullPolyline(item)) };
  },
};

let input = '';
for await (const chunk of process.stdin) input += chunk;
const cases = JSON.parse(input);
process.stdout.write(JSON.stringify(cases.map(({ op, args }) => OPS[op](...args))));
