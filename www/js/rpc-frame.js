/** Encode a length-prefixed JSON header followed by raw buffers. */
export function encodeFrame(header, buffers) {
  if (Object.hasOwn(header, 'sizes')) throw new Error('sizes is reserved');
  const views = buffers.map(b => ArrayBuffer.isView(b) ? new Uint8Array(b.buffer, b.byteOffset, b.byteLength) : new Uint8Array(b));
  const sizes = views.map(buffer => buffer.byteLength);
  const raw = new TextEncoder().encode(JSON.stringify({...header, sizes}));
  const frame = new Uint8Array(4 + raw.length + sizes.reduce((a, b) => a + b, 0));
  new DataView(frame.buffer).setUint32(0, raw.length, true);
  frame.set(raw, 4);
  let offset = 4 + raw.length;
  for (const buffer of views) {
    frame.set(buffer, offset);
    offset += buffer.byteLength;
  }
  return frame;
}

/** Decode a complete frame, returning independent copies of its buffers. */
export function decodeFrame(data) {
  const bytes = ArrayBuffer.isView(data) ? new Uint8Array(data.buffer, data.byteOffset, data.byteLength) : new Uint8Array(data);
  if (bytes.byteLength < 4) throw new Error('Missing header length');
  const end = 4 + new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength).getUint32(0, true);
  if (end > bytes.byteLength) throw new Error('Truncated header');
  const header = JSON.parse(new TextDecoder('utf-8', {fatal: true}).decode(bytes.subarray(4, end)));
  if (header === null || typeof header !== 'object' || Array.isArray(header)) {
    throw new Error('Header must be an object');
  }
  const {sizes} = header;
  if (!Array.isArray(sizes) || sizes.some(size => !Number.isSafeInteger(size) || size < 0)) {
    throw new Error('Invalid buffer sizes');
  }
  if (sizes.reduce((a, b) => a + b, 0) !== bytes.byteLength - end) {
    throw new Error('Buffer sizes do not match body length');
  }
  delete header.sizes;
  let offset = end;
  const buffers = sizes.map(size => {
    const copy = new Uint8Array(size);
    copy.set(bytes.subarray(offset, offset + size));
    offset += size;
    return copy.buffer;
  });
  return {header, buffers};
}
