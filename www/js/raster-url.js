// SPDX-FileCopyrightText: 2026 com55
// SPDX-License-Identifier: Apache-2.0
/** Encode a host RGBA raster as a browser-owned preview URL. */
export async function rasterToUrl({width, height, rgba}) {
  const canvas = document.createElement('canvas');
  canvas.width = width; canvas.height = height;
  canvas.getContext('2d').putImageData(new ImageData(new Uint8ClampedArray(rgba), width, height), 0, 0);
  const blob = await new Promise((resolve, reject) => canvas.toBlob(
    value => value ? resolve(value) : reject(new Error('PNG encoding failed')), 'image/png'));
  return URL.createObjectURL(blob);
}
