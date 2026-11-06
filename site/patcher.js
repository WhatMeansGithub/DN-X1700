// DN-X1700 / DN-X1600 firmware patcher: core logic. Runs in the browser and in Node. Nothing is ever uploaded.
// Mirrors tools/build.py: verify the original byte for byte, apply the patches, fix the updater checksum, verify the result.
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.X1700 = factory();
})(typeof self !== 'undefined' ? self : globalThis, function () {
  'use strict';

  const SIZE = 0x400000;          // X1700 image: exactly 4 MiB. Patch data for another model sets P.size / P.fwName.
  const sizeOf = (P) => (P && P.size) || SIZE;
  const nameOf = (P) => (P && P.fwName) || 'X1700FW.BIN';
  const BOOT_END = 0x10000;       // never patch the boot area
  const TARGET = 0xFFFFFFFF;      // updater rule: little-endian 32-bit word sum of the whole image

  class UserError extends Error {}

  // ---- SHA-256 (pure JS, so it also works when the page is opened from a file) ----
  const K = new Uint32Array([
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2]);

  function sha256(bytes) {
    const h = new Uint32Array([0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19]);
    const len = bytes.length, padded = ((len + 9 + 63) >> 6) << 6;
    const buf = new Uint8Array(padded);
    buf.set(bytes); buf[len] = 0x80;
    const dv = new DataView(buf.buffer);
    dv.setUint32(padded - 8, Math.floor(len / 0x20000000));
    dv.setUint32(padded - 4, (len * 8) >>> 0);
    const w = new Uint32Array(64);
    for (let o = 0; o < padded; o += 64) {
      for (let i = 0; i < 16; i++) w[i] = dv.getUint32(o + i * 4);
      for (let i = 16; i < 64; i++) {
        const a = w[i - 15], b = w[i - 2];
        const s0 = ((a >>> 7) | (a << 25)) ^ ((a >>> 18) | (a << 14)) ^ (a >>> 3);
        const s1 = ((b >>> 17) | (b << 15)) ^ ((b >>> 19) | (b << 13)) ^ (b >>> 10);
        w[i] = (w[i - 16] + s0 + w[i - 7] + s1) | 0;
      }
      let a = h[0], b = h[1], c = h[2], d = h[3], e = h[4], f = h[5], g = h[6], hh = h[7];
      for (let i = 0; i < 64; i++) {
        const S1 = ((e >>> 6) | (e << 26)) ^ ((e >>> 11) | (e << 21)) ^ ((e >>> 25) | (e << 7));
        const t1 = (hh + S1 + ((e & f) ^ (~e & g)) + K[i] + w[i]) | 0;
        const S0 = ((a >>> 2) | (a << 30)) ^ ((a >>> 13) | (a << 19)) ^ ((a >>> 22) | (a << 10));
        const t2 = (S0 + ((a & b) ^ (a & c) ^ (b & c))) | 0;
        hh = g; g = f; f = e; e = (d + t1) | 0; d = c; c = b; b = a; a = (t1 + t2) | 0;
      }
      h[0] += a; h[1] += b; h[2] += c; h[3] += d; h[4] += e; h[5] += f; h[6] += g; h[7] += hh;
    }
    return Array.from(h, (x) => x.toString(16).padStart(8, '0')).join('');
  }

  function wordSum(b) {
    const dv = new DataView(b.buffer, b.byteOffset, b.byteLength);
    let s = 0;
    for (let o = 0; o < b.length; o += 4) s = (s + dv.getUint32(o, true)) >>> 0;
    return s;
  }

  const fromHex = (s) => Uint8Array.from(s.match(/../g) || [], (x) => parseInt(x, 16));
  const toHex = (b) => Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('');

  // ---- ZIP: find the firmware file (X1700FW.BIN, X1600FW.BIN) in the zip Denon provides (stored or deflate) ----
  let inflateRaw = async function (data) {
    if (typeof DecompressionStream === 'undefined')
      throw new UserError('This browser cannot unzip files. Unzip the download yourself and drop the .BIN file inside instead.');
    const stream = new Blob([data]).stream().pipeThrough(new DecompressionStream('deflate-raw'));
    return new Uint8Array(await new Response(stream).arrayBuffer());
  };
  function setInflate(fn) { inflateRaw = fn; }       // lets tests supply zlib

  async function extractFromZip(b, fwName = 'X1700FW.BIN') {
    const want = new RegExp('(^|[\\\\/])' + fwName.replace('.', '\\.') + '$', 'i');
    const dv = new DataView(b.buffer, b.byteOffset, b.byteLength);
    let eocd = -1;
    for (let i = b.length - 22; i >= Math.max(0, b.length - 65557); i--)
      if (dv.getUint32(i, true) === 0x06054b50) { eocd = i; break; }
    if (eocd < 0) throw new UserError('This does not look like a valid zip file.');
    const count = dv.getUint16(eocd + 10, true);
    let p = dv.getUint32(eocd + 16, true);
    for (let n = 0; n < count; n++) {
      if (dv.getUint32(p, true) !== 0x02014b50) throw new UserError('The zip file is damaged.');
      const method = dv.getUint16(p + 10, true), csize = dv.getUint32(p + 20, true), usize = dv.getUint32(p + 24, true);
      const nlen = dv.getUint16(p + 28, true), elen = dv.getUint16(p + 30, true), clen = dv.getUint16(p + 32, true);
      const lho = dv.getUint32(p + 42, true);
      const name = new TextDecoder().decode(b.subarray(p + 46, p + 46 + nlen));
      p += 46 + nlen + elen + clen;
      if (!want.test(name)) continue;
      const start = lho + 30 + dv.getUint16(lho + 26, true) + dv.getUint16(lho + 28, true);
      const data = b.subarray(start, start + csize);
      const out = method === 0 ? data : method === 8 ? await inflateRaw(data) : null;
      if (!out) throw new UserError('The zip uses a compression method this tool does not support. Unzip it and drop ' + fwName + '.');
      if (out.length !== usize) throw new UserError('The zip file is damaged (size mismatch).');
      return out;
    }
    throw new UserError('That zip does not contain ' + fwName + '. Download the firmware from Denon\'s page (link above).');
  }

  // Accepts the Denon zip or the raw firmware file; returns the image bytes. fwName defaults to X1700FW.BIN.
  async function loadFirmware(bytes, fwName) {
    if (bytes.length > 4 && bytes[0] === 0x50 && bytes[1] === 0x4b && bytes[2] === 3 && bytes[3] === 4) return extractFromZip(bytes, fwName);
    return bytes;
  }

  // ---- the patcher: same steps and checks as tools/build.py ----
  function buildImage(orig, P) {
    const SIZE = sizeOf(P);
    if (orig.length !== SIZE)
      throw new UserError(`That file is ${orig.length.toLocaleString('en-US')} bytes. The firmware must be exactly ${SIZE.toLocaleString('en-US')} bytes (${nameOf(P)} from Denon's v2.000 download).`);
    const origSha = sha256(orig);
    if (origSha !== P.originalSha256)
      throw new UserError('This is not the original Denon v2.000 firmware, so it will not be patched. ' +
        `Expected sha256 ${P.originalSha256} but got ${origSha}. Use the file from Denon's download page (link above); a file that was already patched or modified is refused.`);
    if (wordSum(orig) !== TARGET) throw new UserError('The firmware fails its own checksum, so it will not be patched.');

    const img = orig.slice();
    const changes = [];
    for (const e of P.patches) {
      const exp = fromHex(e.expect), nw = fromHex(e.new);
      if (exp.length !== nw.length || e.offset < BOOT_END || e.offset + nw.length > SIZE - 4)
        throw new Error('Internal error: bad patch definition at ' + e.offset);
      for (let i = 0; i < exp.length; i++)
        if (img[e.offset + i] !== exp[i]) throw new UserError(`The firmware differs from the expected original at offset 0x${e.offset.toString(16)}. Not patching.`);
      img.set(nw, e.offset);
      changes.push({ offset: e.offset, patch: e.patch, old: e.expect, new: e.new, reason: e.reason });
    }
    const dv = new DataView(img.buffer);
    const oldWord = toHex(img.subarray(SIZE - 4));
    dv.setUint32(SIZE - 4, 0, true);
    dv.setUint32(SIZE - 4, (TARGET - wordSum(img)) >>> 0, true);     // balance the updater checksum
    if (wordSum(img) !== TARGET) throw new Error('Internal error: checksum fix failed.');
    const sha = sha256(img);
    if (sha !== P.resultSha256) throw new Error('Internal error: the result does not match the reference build (' + sha + ').');
    return { image: img, sha256: sha, originalSha256: origSha, changes, checksumWord: { old: oldWord, new: toHex(img.subarray(SIZE - 4)) } };
  }

  return { SIZE, UserError, sha256, wordSum, loadFirmware, buildImage, extractFromZip, setInflate, toHex, fromHex };
});
