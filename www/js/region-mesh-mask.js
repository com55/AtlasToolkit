/**
 * Rasterizes a mesh's texture-space UV polygon into an alpha mask.
 * Pure geometry — takes the exact target width/height as arguments and has
 * no knowledge of atlas scale, rotation, or offsets; the caller
 * (core-region-ops.js's extractRegionFromPage) is responsible for choosing
 * the correct dimensions for whichever coordinate space it's masking.
 *
 * @param {number[]} uvs  flat [u0,v0,u1,v1,...] array, 0-1 normalized.
 *   v increases downward, matching canvas row order (Spine's regionUVs V-axis
 *   convention — see the design spec's V-axis derivation) — do not pre-flip.
 * @param {number[]} triangles  flat index array into uvs, 3 per triangle
 * @param {number} width
 * @param {number} height
 * @returns {HTMLCanvasElement}  width×height, opaque inside the polygon
 */
export function rasterizeMeshMask(uvs, triangles, width, height) {
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = '#fff';
  ctx.beginPath();
  for (let i = 0; i < triangles.length; i += 3) {
    const ia = triangles[i] * 2, ib = triangles[i + 1] * 2, ic = triangles[i + 2] * 2;
    ctx.moveTo(uvs[ia] * width, uvs[ia + 1] * height);
    ctx.lineTo(uvs[ib] * width, uvs[ib + 1] * height);
    ctx.lineTo(uvs[ic] * width, uvs[ic + 1] * height);
    ctx.closePath();
  }
  ctx.fill('nonzero'); // union of all triangles in one path, non-zero winding
  return canvas;
}
