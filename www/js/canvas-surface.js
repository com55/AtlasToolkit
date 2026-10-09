/**
 * Canvas constructor that works on the main thread and in a preview Worker.
 * The main thread keeps HTMLCanvasElement. A Worker has no document, so it
 * uses OffscreenCanvas.
 */

export function createCanvas(width, height) {
  if (typeof document !== 'undefined' && typeof document.createElement === 'function') {
    const canvas = document.createElement('canvas');
    canvas.width = width;
    canvas.height = height;
    return canvas;
  }
  return new OffscreenCanvas(width, height);
}

export function canvasToPngBlob(canvas) {
  if (!canvas) return Promise.resolve(null);
  if (typeof canvas.convertToBlob === 'function') {
    return canvas.convertToBlob({ type: 'image/png' });
  }
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (!blob) reject(new Error('canvas.toBlob failed'));
      else resolve(blob);
    }, 'image/png');
  });
}
