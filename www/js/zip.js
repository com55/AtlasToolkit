/**
 * zip.js — minimal STORE-only (no compression) ZIP writer.
 *
 * PNG and atlas text are either already compressed or tiny, so storing them
 * uncompressed avoids pulling in a DEFLATE dependency while staying fully
 * compatible with Windows Explorer, macOS, and `unzip`.
 */

const _crcTable = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = (c & 1) ? (0xEDB88320 ^ (c >>> 1)) : (c >>> 1);
    t[n] = c >>> 0;
  }
  return t;
})();

function _crc32(bytes) {
  let crc = 0xFFFFFFFF;
  for (let i = 0; i < bytes.length; i++) {
    crc = (crc >>> 8) ^ _crcTable[(crc ^ bytes[i]) & 0xFF];
  }
  return (crc ^ 0xFFFFFFFF) >>> 0;
}

async function _toBytes(data) {
  if (data instanceof Uint8Array) return data;
  if (data instanceof ArrayBuffer) return new Uint8Array(data);
  if (data instanceof Blob) return new Uint8Array(await data.arrayBuffer());
  if (typeof data === 'string') return new TextEncoder().encode(data);
  return new Uint8Array(data);
}

/**
 * Build a ZIP Blob from a list of { name, data } entries, where data is a
 * Blob | Uint8Array | ArrayBuffer | string.
 */
export async function createZip(files) {
  const encoder = new TextEncoder();
  const chunks = [];
  const entries = [];
  let offset = 0;

  for (const f of files) {
    const nameBytes = encoder.encode(f.name);
    const dataBytes = await _toBytes(f.data);
    const crc = _crc32(dataBytes);
    const size = dataBytes.length;

    const local = new Uint8Array(30 + nameBytes.length);
    const lv = new DataView(local.buffer);
    lv.setUint32(0, 0x04034b50, true);   // local file header signature
    lv.setUint16(4, 20, true);           // version needed to extract
    lv.setUint16(6, 0x0800, true);       // flags: UTF-8 filename
    lv.setUint16(8, 0, true);            // compression method: store
    lv.setUint16(10, 0, true);           // mod time
    lv.setUint16(12, 0, true);           // mod date
    lv.setUint32(14, crc, true);
    lv.setUint32(18, size, true);        // compressed size
    lv.setUint32(22, size, true);        // uncompressed size
    lv.setUint16(26, nameBytes.length, true);
    lv.setUint16(28, 0, true);           // extra field length
    local.set(nameBytes, 30);

    chunks.push(local, dataBytes);
    entries.push({ nameBytes, crc, size, offset });
    offset += local.length + size;
  }

  const central = [];
  let centralSize = 0;
  for (const e of entries) {
    const cd = new Uint8Array(46 + e.nameBytes.length);
    const cv = new DataView(cd.buffer);
    cv.setUint32(0, 0x02014b50, true);   // central directory header signature
    cv.setUint16(4, 20, true);           // version made by
    cv.setUint16(6, 20, true);           // version needed to extract
    cv.setUint16(8, 0x0800, true);       // flags: UTF-8 filename
    cv.setUint16(10, 0, true);           // compression method: store
    cv.setUint16(12, 0, true);           // mod time
    cv.setUint16(14, 0, true);           // mod date
    cv.setUint32(16, e.crc, true);
    cv.setUint32(20, e.size, true);      // compressed size
    cv.setUint32(24, e.size, true);      // uncompressed size
    cv.setUint16(28, e.nameBytes.length, true);
    cv.setUint16(30, 0, true);           // extra field length
    cv.setUint16(32, 0, true);           // comment length
    cv.setUint16(34, 0, true);           // disk number start
    cv.setUint16(36, 0, true);           // internal attributes
    cv.setUint32(38, 0, true);           // external attributes
    cv.setUint32(42, e.offset, true);    // local header offset
    cd.set(e.nameBytes, 46);
    central.push(cd);
    centralSize += cd.length;
  }

  const eocd = new Uint8Array(22);
  const ev = new DataView(eocd.buffer);
  ev.setUint32(0, 0x06054b50, true);     // end of central directory signature
  ev.setUint16(4, 0, true);              // this disk number
  ev.setUint16(6, 0, true);              // disk with central directory
  ev.setUint16(8, entries.length, true); // entries on this disk
  ev.setUint16(10, entries.length, true);// total entries
  ev.setUint32(12, centralSize, true);   // central directory size
  ev.setUint32(16, offset, true);        // central directory offset
  ev.setUint16(20, 0, true);             // comment length

  return new Blob([...chunks, ...central, eocd], { type: 'application/zip' });
}
