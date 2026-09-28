/**
 * Separable Lanczos-3 resize for straight RGBA.
 *
 * Samples at pixel centers, `(dest + 0.5) * src/dest - 0.5`, and filters in
 * premultiplied alpha so transparent neighbors do not darken an edge.
 * An exact size match is copied. Used by Forced Resizing, where canvas
 * "high" smoothing (Mitchell) was softer than a manual Lanczos enlarge.
 */

import { createCanvas } from './canvas-surface.js';

const LANCZOS_A = 3;

function lanczos(x) {
  x = Math.abs(x);
  if (x === 0) return 1;
  if (x >= LANCZOS_A) return 0;
  const pix = Math.PI * x;
  return (Math.sin(pix) / pix) * (Math.sin(pix / LANCZOS_A) / (pix / LANCZOS_A));
}

function clamp8(v) {
  if (v <= 0) return 0;
  if (v >= 255) return 255;
  return Math.round(v);
}

/** Per dest pixel: source indexes and normalized weights, clamp-to-edge. */
function buildKernels(srcSize, dstSize) {
  const scale = dstSize / srcSize;
  const filterScale = scale < 1 ? scale : 1;
  const support = LANCZOS_A / filterScale;
  const index = [];
  const weight = [];
  const offset = new Uint32Array(dstSize + 1);
  let cursor = 0;
  for (let d = 0; d < dstSize; d++) {
    offset[d] = cursor;
    const center = (d + 0.5) / scale - 0.5;
    const left = Math.floor(center - support);
    const right = Math.ceil(center + support);
    const accI = [];
    const accW = [];
    for (let i = left; i <= right; i++) {
      const w = lanczos((i - center) * filterScale);
      if (w === 0) continue;
      const idx = i < 0 ? 0 : i >= srcSize ? srcSize - 1 : i;
      const last = accI.length - 1;
      if (last >= 0 && accI[last] === idx) accW[last] += w;
      else {
        accI.push(idx);
        accW.push(w);
      }
    }
    let sum = 0;
    for (let k = 0; k < accW.length; k++) sum += accW[k];
    const inv = sum !== 0 ? 1 / sum : 0;
    for (let k = 0; k < accW.length; k++) {
      index.push(accI[k]);
      weight.push(accW[k] * inv);
      cursor++;
    }
  }
  offset[dstSize] = cursor;
  return { index: Int32Array.from(index), weight: Float32Array.from(weight), offset };
}

function opaqueBounds(src, srcW, srcH) {
  let minX = srcW;
  let minY = srcH;
  let maxX = -1;
  let maxY = -1;
  for (let y = 0; y < srcH; y++) {
    const row = y * srcW * 4;
    for (let x = 0; x < srcW; x++) {
      if (src[row + x * 4 + 3] === 0) continue;
      if (x < minX) minX = x;
      if (x > maxX) maxX = x;
      if (y < minY) minY = y;
      if (y > maxY) maxY = y;
    }
  }
  if (maxX < 0) return null;
  return { minX, minY, maxX, maxY };
}

/** Dest pixels whose kernel touches source [src0, src1]. Contiguous. */
function affectedDest(kernel, src0, src1) {
  const n = kernel.offset.length - 1;
  let first = -1;
  let last = -1;
  for (let d = 0; d < n; d++) {
    const s = kernel.offset[d];
    const e = kernel.offset[d + 1];
    const lo = kernel.index[s];
    const hi = kernel.index[e - 1];
    if (hi < src0 || lo > src1) {
      if (first >= 0) break;
      continue;
    }
    if (first < 0) first = d;
    last = d;
  }
  return first < 0 ? null : { first, last };
}

/**
 * @param {Uint8ClampedArray} src
 * @param {number} srcW
 * @param {number} srcH
 * @param {number} dstW
 * @param {number} dstH
 * @returns {Uint8ClampedArray}
 */
export function lanczosResizeRGBA(src, srcW, srcH, dstW, dstH) {
  if (srcW === dstW && srcH === dstH) return new Uint8ClampedArray(src);
  const out = new Uint8ClampedArray(dstW * dstH * 4);
  const bounds = opaqueBounds(src, srcW, srcH);
  if (!bounds) return out;

  const xk = buildKernels(srcW, dstW);
  const yk = buildKernels(srcH, dstH);
  const dxR = affectedDest(xk, bounds.minX, bounds.maxX);
  const dyR = affectedDest(yk, bounds.minY, bounds.maxY);
  if (!dxR || !dyR) return out;

  const dx0 = dxR.first;
  const dx1 = dxR.last;
  const dy0 = dyR.first;
  const dy1 = dyR.last;
  const span = dx1 - dx0 + 1;
  const sy0 = yk.index[yk.offset[dy0]];
  const sy1 = yk.index[yk.offset[dy1 + 1] - 1];
  const rows = new Array(sy1 - sy0 + 1);

  function filteredRow(sy) {
    const slot = sy - sy0;
    const hit = rows[slot];
    if (hit) return hit;
    const row = new Float32Array(span * 4);
    const base = sy * srcW * 4;
    for (let dx = dx0; dx <= dx1; dx++) {
      const start = xk.offset[dx];
      const end = xk.offset[dx + 1];
      let r = 0, g = 0, b = 0, a = 0;
      for (let k = start; k < end; k++) {
        const p = base + xk.index[k] * 4;
        const pa = src[p + 3];
        const pf = pa / 255;
        const w = xk.weight[k];
        r += src[p] * pf * w;
        g += src[p + 1] * pf * w;
        b += src[p + 2] * pf * w;
        a += pa * w;
      }
      const o = (dx - dx0) * 4;
      row[o] = r;
      row[o + 1] = g;
      row[o + 2] = b;
      row[o + 3] = a;
    }
    rows[slot] = row;
    return row;
  }

  for (let dy = dy0; dy <= dy1; dy++) {
    const start = yk.offset[dy];
    const end = yk.offset[dy + 1];
    const dstRow = dy * dstW;
    for (let dx = dx0; dx <= dx1; dx++) {
      let r = 0, g = 0, b = 0, a = 0;
      const p = (dx - dx0) * 4;
      for (let k = start; k < end; k++) {
        const srcRow = filteredRow(yk.index[k]);
        const w = yk.weight[k];
        r += srcRow[p] * w;
        g += srcRow[p + 1] * w;
        b += srcRow[p + 2] * w;
        a += srcRow[p + 3] * w;
      }
      if (a <= 0.5) continue;
      const o = (dstRow + dx) * 4;
      const inv = 255 / a;
      out[o] = clamp8(r * inv);
      out[o + 1] = clamp8(g * inv);
      out[o + 2] = clamp8(b * inv);
      out[o + 3] = clamp8(a);
    }
  }
  return out;
}

const HORIZONTAL_FS = `#version 300 es
precision highp float;
uniform sampler2D u_src;
uniform vec2 u_srcSize;
uniform vec2 u_dstSize;
out vec4 o;
const float A = 3.0;
const float PI = 3.141592653589793;
float lanczos(float x) {
  x = abs(x);
  if (x == 0.0) return 1.0;
  if (x >= A) return 0.0;
  float pix = PI * x;
  return (sin(pix) / pix) * (sin(pix / A) / (pix / A));
}
void main() {
  float srcW = u_srcSize.x;
  float scale = u_dstSize.x / srcW;
  float filterScale = min(1.0, scale);
  float center = gl_FragCoord.x / scale - 0.5;
  int y = int(gl_FragCoord.y);
  int base = int(floor(center));
  vec4 acc = vec4(0.0);
  float sum = 0.0;
  for (int di = -6; di <= 6; ++di) {
    int i = base + di;
    float w = lanczos((float(i) - center) * filterScale);
    if (w == 0.0) continue;
    int ix = clamp(i, 0, int(srcW) - 1);
    vec4 s = texelFetch(u_src, ivec2(ix, y), 0);
    acc += vec4(s.rgb * s.a, s.a) * w;
    sum += w;
  }
  o = sum != 0.0 ? acc / sum : vec4(0.0);
}
`;

const VERTICAL_FS = `#version 300 es
precision highp float;
uniform sampler2D u_src;
uniform vec2 u_srcSize;
uniform vec2 u_dstSize;
out vec4 o;
const float A = 3.0;
const float PI = 3.141592653589793;
float lanczos(float x) {
  x = abs(x);
  if (x == 0.0) return 1.0;
  if (x >= A) return 0.0;
  float pix = PI * x;
  return (sin(pix) / pix) * (sin(pix / A) / (pix / A));
}
void main() {
  float srcH = u_srcSize.y;
  float scale = u_dstSize.y / srcH;
  float filterScale = min(1.0, scale);
  float center = gl_FragCoord.y / scale - 0.5;
  int x = int(gl_FragCoord.x);
  int base = int(floor(center));
  vec4 acc = vec4(0.0);
  float sum = 0.0;
  for (int di = -6; di <= 6; ++di) {
    int i = base + di;
    float w = lanczos((float(i) - center) * filterScale);
    if (w == 0.0) continue;
    int iy = clamp(i, 0, int(srcH) - 1);
    vec4 s = texelFetch(u_src, ivec2(x, iy), 0);
    acc += s * w;
    sum += w;
  }
  if (sum != 0.0) acc /= sum;
  if (acc.a <= 0.5 / 255.0) {
    o = vec4(0.0);
    return;
  }
  o = vec4(clamp(acc.rgb / acc.a, 0.0, 1.0), clamp(acc.a, 0.0, 1.0));
}
`;

const VERT_SRC = `#version 300 es
void main() {
  vec2 pos[3] = vec2[](vec2(-1.0, -1.0), vec2(3.0, -1.0), vec2(-1.0, 3.0));
  gl_Position = vec4(pos[gl_VertexID], 0.0, 1.0);
}
`;

let gpuState = null;
let gpuFailed = false;

function compileShader(gl, type, source) {
  const shader = gl.createShader(type);
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) return null;
  return shader;
}

function linkProgram(gl, fsSource) {
  const vs = compileShader(gl, gl.VERTEX_SHADER, VERT_SRC);
  const fs = compileShader(gl, gl.FRAGMENT_SHADER, fsSource);
  if (!vs || !fs) return null;
  const program = gl.createProgram();
  gl.attachShader(program, vs);
  gl.attachShader(program, fs);
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) return null;
  return program;
}

function getGpu() {
  if (gpuState || gpuFailed) return gpuState;
  const canvas = createCanvas(1, 1);
  const gl = canvas.getContext('webgl2', {
    alpha: true,
    premultipliedAlpha: false,
    antialias: false,
    depth: false,
    stencil: false,
  });
  if (!gl || !gl.getExtension('EXT_color_buffer_float')) {
    gpuFailed = true;
    return null;
  }
  const horizontal = linkProgram(gl, HORIZONTAL_FS);
  const vertical = linkProgram(gl, VERTICAL_FS);
  if (!horizontal || !vertical) {
    gpuFailed = true;
    return null;
  }
  gpuState = {
    gl,
    horizontal,
    vertical,
    maxSize: gl.getParameter(gl.MAX_TEXTURE_SIZE),
    srcTex: gl.createTexture(),
    midTex: gl.createTexture(),
    midFb: gl.createFramebuffer(),
    outTex: gl.createTexture(),
    outFb: gl.createFramebuffer(),
    outCanvas: createCanvas(1, 1),
  };
  for (const tex of [gpuState.srcTex, gpuState.midTex, gpuState.outTex]) {
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  }
  return gpuState;
}

function gpuLanczos(source, dstW, dstH) {
  const state = getGpu();
  if (!state) return null;
  const { gl } = state;
  const srcW = source.width;
  const srcH = source.height;
  if (srcW > state.maxSize || srcH > state.maxSize || dstW > state.maxSize || dstH > state.maxSize) {
    return null;
  }
  gl.disable(gl.BLEND);
  gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
  gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
  gl.bindTexture(gl.TEXTURE_2D, state.srcTex);
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, gl.RGBA, gl.UNSIGNED_BYTE, source);

  gl.bindTexture(gl.TEXTURE_2D, state.midTex);
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA32F, dstW, srcH, 0, gl.RGBA, gl.FLOAT, null);
  gl.bindFramebuffer(gl.FRAMEBUFFER, state.midFb);
  gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, state.midTex, 0);
  if (gl.checkFramebufferStatus(gl.FRAMEBUFFER) !== gl.FRAMEBUFFER_COMPLETE) return null;
  gl.viewport(0, 0, dstW, srcH);
  gl.useProgram(state.horizontal);
  gl.uniform1i(gl.getUniformLocation(state.horizontal, 'u_src'), 0);
  gl.uniform2f(gl.getUniformLocation(state.horizontal, 'u_srcSize'), srcW, srcH);
  gl.uniform2f(gl.getUniformLocation(state.horizontal, 'u_dstSize'), dstW, dstH);
  gl.bindTexture(gl.TEXTURE_2D, state.srcTex);
  gl.drawArrays(gl.TRIANGLES, 0, 3);

  gl.bindTexture(gl.TEXTURE_2D, state.outTex);
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, dstW, dstH, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
  gl.bindFramebuffer(gl.FRAMEBUFFER, state.outFb);
  gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, state.outTex, 0);
  if (gl.checkFramebufferStatus(gl.FRAMEBUFFER) !== gl.FRAMEBUFFER_COMPLETE) return null;
  gl.viewport(0, 0, dstW, dstH);
  gl.useProgram(state.vertical);
  gl.uniform1i(gl.getUniformLocation(state.vertical, 'u_src'), 0);
  gl.uniform2f(gl.getUniformLocation(state.vertical, 'u_srcSize'), dstW, srcH);
  gl.uniform2f(gl.getUniformLocation(state.vertical, 'u_dstSize'), dstW, dstH);
  gl.bindTexture(gl.TEXTURE_2D, state.midTex);
  gl.drawArrays(gl.TRIANGLES, 0, 3);

  const raw = new Uint8Array(dstW * dstH * 4);
  gl.readPixels(0, 0, dstW, dstH, gl.RGBA, gl.UNSIGNED_BYTE, raw);
  const out = state.outCanvas;
  if (out.width !== dstW || out.height !== dstH) {
    out.width = dstW;
    out.height = dstH;
  }
  const ctx = out.getContext('2d');
  const image = ctx.createImageData(dstW, dstH);
  image.data.set(raw);
  ctx.putImageData(image, 0, 0);
  return out;
}

/** Lanczos-3 enlarge. Uses the GPU when WebGL2 can hold the textures,
 *  otherwise the pure-JS resizer. The returned canvas is only valid until
 *  the next call. */
export function resizeCanvasLanczos(source, dstW, dstH) {
  if (source.width === dstW && source.height === dstH) return source;
  if (dstW >= source.width && dstH >= source.height) {
    try {
      const gpu = gpuLanczos(source, dstW, dstH);
      if (gpu) return gpu;
    } catch {
      gpuFailed = true;
      gpuState = null;
    }
  }
  const srcCtx = source.getContext('2d', { willReadFrequently: true });
  const data = srcCtx.getImageData(0, 0, source.width, source.height);
  const resized = lanczosResizeRGBA(data.data, source.width, source.height, dstW, dstH);
  const canvas = createCanvas(dstW, dstH);
  const ctx = canvas.getContext('2d');
  const image = ctx.createImageData(dstW, dstH);
  image.data.set(resized);
  ctx.putImageData(image, 0, 0);
  return canvas;
}
