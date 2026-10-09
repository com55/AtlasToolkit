// Generates tests/fixtures/mesh-sample.json: three synthetic Spine-style
// meshes (uvs + triangles) used by tests/browser/verify-mesh-mask-acceptance.mjs.
// Each mesh is a concave, star-shaped outline fanned from an interior point,
// placed in a different band of the 0..1 UV square (top, middle, bottom) so
// its mask is non-degenerate and leaves the bottom-right corner uncovered.
// Deterministic: no randomness. Run: node tests/fixtures/gen-mesh-sample.mjs
import { writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const round = (v) => Math.round(v * 1e6) / 1e6;
const clamp = (v) => Math.min(1, Math.max(0, v));

// Outline first, centre last: the first `points` UV pairs are the hull,
// the same layout real Spine meshes use.
function starMesh({ cx, cy, rx, ry, points, lobes, depth, phase }) {
  const uvs = [];
  for (let i = 0; i < points; i++) {
    const t = (i / points) * Math.PI * 2 + phase;
    const r = 1 - depth * (0.5 + 0.5 * Math.sin(lobes * t));
    uvs.push(round(clamp(cx + Math.cos(t) * rx * r)), round(clamp(cy + Math.sin(t) * ry * r)));
  }
  uvs.push(round(cx), round(cy));
  const centre = points;
  const triangles = [];
  for (let i = 0; i < points; i++) triangles.push(centre, i, (i + 1) % points);
  return { type: 'Mesh', uvs, triangles };
}

const sample = {
  SAMPLE_1: starMesh({ cx: 0.47, cy: 0.2, rx: 0.28, ry: 0.19, points: 16, lobes: 3, depth: 0.4, phase: 0.3 }),
  SAMPLE_2: starMesh({ cx: 0.48, cy: 0.56, rx: 0.42, ry: 0.2, points: 13, lobes: 4, depth: 0.35, phase: 0.1 }),
  SAMPLE_3: starMesh({ cx: 0.5, cy: 0.85, rx: 0.5, ry: 0.14, points: 30, lobes: 5, depth: 0.3, phase: 0 }),
};
writeFileSync(path.join(HERE, 'mesh-sample.json'), JSON.stringify(sample, null, 2) + '\n');
