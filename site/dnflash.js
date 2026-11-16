// DN-X600 firmware transfer over WebHID (Chrome / Edge, macOS / Windows / Linux). EXPERIMENTAL.
// Protocol as read from Denon's DDJ Updater 2.0.0 (ddj_updater.exe, class dnhid::*) and checked against a USB capture of that updater
// flashing a real DN-X600 (see docs/x600.md "Updater protocol"). Runs in the browser and in Node (tests drive it with a simulated mixer).
// Nothing is uploaded anywhere.
//
// Wire format [C = seen in the capture of Denon's updater on a real unit]:
//   HID report ID 0, 32 bytes, both directions (host -> mixer as control SET_REPORT, mixer -> host on interrupt IN 0x84).
//   byte 0      0xFF  (the mixer's own reports start with 0x23, the device ID from the image header)
//   byte 1      0xF0 | flag<<3 | type      (flag 1 in every update message and in the mixer's ACK/NAK; the mixer's own byte 1 has no 0xF0 nibble)
//   byte 2      command
//   bytes 3..31 payload (29 bytes)
//   Reply = ACK when flag 1, type 3, command 0x06 (NAK is command 0x15).
// Sequence [C], exactly as Denon's updater does it:
//   caps poll (flag 0, type 1, cmd 0x21) -> reply type 4, cmd 0x21, the name "DN-X600" at byte 16
//   Select (type 0, cmd 0x40) -> ACK
//   the file in 29 byte blocks, cmd 0x40: first block type 4 -> ACK, middle blocks type 5 (no reply), last block type 6 -> ACK whose
//     bytes 3..4 (big endian) x 1000 = time the Execute step may take (30 s seen)
//   End Of Transmission (type 3, cmd 0x04), no reply   <- right after the last block's ACK (the "Load" button); skipping it makes Exec fail with NAK
//   Exec (type 0, cmd 0x22) -> ACK after about 14 s (the "Execute" button)
//   End Of Transmission again, no reply
// [H] Nothing is written to flash before Exec, so stopping before Exec is harmless (the mixer needed about 14 s to answer Exec: that is the write).
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.DNFLASH = factory();
})(typeof self !== 'undefined' ? self : globalThis, function () {
  'use strict';

  const VENDOR_ID = 0x154E;
  const PKT = 32, PAYLOAD = 29;
  const TYPE = { SELECT: 0, CAPS: 1, START: 4, MIDDLE: 5, END: 6, REPLY: 3 };
  const CMD = { DATA: 0x40, EXEC: 0x22, EOT: 0x04, ACK: 0x06, NAK: 0x15, CAPS: 0x21 };
  const hex = (b, n) => Array.from(b.subarray(0, n || b.length), (x) => x.toString(16).padStart(2, '0')).join(' ');
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  class FlashError extends Error {}

  function packet(type, cmd, payload, flag) {
    const p = new Uint8Array(PKT);
    p[0] = 0xFF;
    p[1] = 0xF0 | ((flag === 0 ? 0 : 1) << 3) | (type & 7);
    p[2] = cmd;
    if (payload) p.set(payload.subarray(0, PAYLOAD), 3);
    return p;
  }
  const isReply = (r, cmd) => !!r && r.length >= 3 && ((r[1] >> 3) & 1) === 1 && (r[1] & 7) === TYPE.REPLY && r[2] === cmd;
  const isAck = (r) => isReply(r, CMD.ACK);
  const isNak = (r) => isReply(r, CMD.NAK);
  const isCapsReply = (r) => !!r && r.length >= 17 && (r[1] & 7) === TYPE.START && r[2] === CMD.CAPS;
  const capsName = (r) => { let s = ''; for (let i = 16; i < Math.min(r.length, 33) && r[i]; i++) s += String.fromCharCode(r[i]); return s; };
  // The mixer's capabilities reply as captured from a real DN-X600 (used by the dry run).
  const CAPS_REPLY = (() => { const r = new Uint8Array(PKT); r.set([0x23, 0x04, 0x21, 0x10, 0x20, 0x08, 0x08, 0x08, 0x60, 0x18, 0x08, 0x00, 0x02, 0x08, 0x00, 0x00]); r.set(Array.from('DN-X600', (c) => c.charCodeAt(0)), 16); return r; })();

  // The image's 32 byte header (the updater reads it from the end of the file): name[17], u8, VID, PID, version, size, crc (big endian).
  function readHeader(img) {
    if (img.length < 0x20) throw new FlashError('The file is too small to be a firmware image.');
    const dv = new DataView(img.buffer, img.byteOffset + img.length - 0x20, 0x20);
    let name = ''; for (let i = 0; i < 0x11 && dv.getUint8(i); i++) name += String.fromCharCode(dv.getUint8(i));
    return { name, vid: dv.getUint16(0x12), pid: dv.getUint16(0x14), version: dv.getUint16(0x16), size: dv.getUint32(0x18), crc: dv.getUint32(0x1c) };
  }
  function checkImage(img, expect) {
    const h = readHeader(img);
    if (h.name !== expect.name || h.vid !== VENDOR_ID || h.pid !== expect.pid || h.size !== img.length || img.length !== expect.size)
      throw new FlashError(`This file's header says "${h.name}", ${h.vid.toString(16)}:${h.pid.toString(16)}, ${h.size} bytes; expected "${expect.name}", ${expect.pid.toString(16)}, ${expect.size} bytes. Not sending it.`);
    return h;
  }

  // ---- link: sends 32 byte reports and queues the mixer's replies ----
  class Link {
    constructor(dev, log) {
      this.dev = dev; this.log = log || (() => {}); this.q = []; this.waiter = null;
      this.onReport = (e) => {
        const d = new Uint8Array(e.data.buffer, e.data.byteOffset, e.data.byteLength).slice();
        if (this.waiter) { const w = this.waiter; this.waiter = null; clearTimeout(w.t); w.res(d); } else this.q.push(d);
      };
      dev.addEventListener('inputreport', this.onReport);
    }
    close() { this.dev.removeEventListener('inputreport', this.onReport); }
    flush() { this.q.length = 0; }
    async send(p) { await this.dev.sendReport(0, p); }
    recv(ms) {
      if (this.q.length) return Promise.resolve(this.q.shift());
      return new Promise((res) => { const w = { res, t: setTimeout(() => { if (this.waiter === w) this.waiter = null; res(null); }, ms) }; this.waiter = w; });
    }
  }

  // Stand-in used by "dry run": acknowledges everything, sends nothing, remembers the packets.
  class DryLink {
    constructor() { this.sent = []; this.next = null; }
    close() {}
    flush() { this.next = null; }
    async send(p) {
      this.sent.push(p.slice());
      this.next = (p[1] === 0xFB && p[2] === CMD.EOT) ? null : p[2] === CMD.CAPS ? CAPS_REPLY.slice() : packet(TYPE.REPLY, CMD.ACK);
    }
    async recv() { const r = this.next; this.next = null; return r; }
  }

  // opts: { link, image, log(msg), progress(done, total, phase), aborted(): bool, readTimeout (ms, default 1000), sleep }
  async function flash(opts) {
    const { link, image } = opts;
    const log = opts.log || (() => {}), progress = opts.progress || (() => {});
    const aborted = opts.aborted || (() => false);
    const rt = opts.readTimeout || 1000, nap = opts.sleep || sleep;
    const total = Math.ceil(image.length / PAYLOAD);
    const check = () => { if (aborted()) throw new FlashError('Stopped by you before the Execute step. Nothing was written to flash.'); };

    async function ask(p, name, timeout, tries) {
      for (let t = 1; t <= tries; t++) {
        link.flush(); await link.send(p);
        const r = await link.recv(timeout);
        if (isAck(r)) { log(`${name}: ACK (${hex(r, 8)} ...)`); return r; }
        log(`${name}: ${r ? (isNak(r) ? 'NAK ' : 'unexpected reply ') + hex(r, 8) : 'no reply'} (try ${t}/${tries})`);
        if (t < tries) await nap(1500);
      }
      throw new FlashError(`The mixer did not accept "${name}". Check that it shows "UP", then try again.`);
    }

    progress(0, total, 'select');
    // Ask the mixer who it is, as Denon's updater does. Refuse only if it clearly answers as something else.
    link.flush(); await link.send(packet(TYPE.CAPS, CMD.CAPS, null, 0));
    const caps = await link.recv(rt);
    if (isCapsReply(caps)) {
      const nm = capsName(caps); log(`Mixer says: "${nm}" (${hex(caps, 8)} ...)`);
      if (opts.expectName && nm !== opts.expectName) throw new FlashError(`This device calls itself "${nm}", not "${opts.expectName}". Nothing was sent.`);
    } else log(caps ? `Unexpected answer to the identity request: ${hex(caps, 8)} ... (continuing)` : 'No answer to the identity request (continuing).');
    await ask(packet(TYPE.SELECT, CMD.DATA), 'Select', rt, 3);

    let execTimeout = 15000;
    for (let i = 0; i < total; i++) {
      check();
      const chunk = new Uint8Array(PAYLOAD);
      chunk.set(image.subarray(i * PAYLOAD, Math.min((i + 1) * PAYLOAD, image.length)));
      const last = i === total - 1, first = i === 0;
      const p = packet(first ? TYPE.START : last ? TYPE.END : TYPE.MIDDLE, CMD.DATA, chunk);
      if (first || last) {
        const r = await ask(p, first ? 'First block' : 'Last block', rt, 1);
        if (last) { const s = ((r[3] << 8) | r[4]) * 1000; if (s) execTimeout = s; log(`The mixer allows ${execTimeout / 1000} s for the Execute step.`); }
      } else await link.send(p);
      if (i % 500 === 0 || last) progress(i + 1, total, 'transfer');
    }

    await link.send(packet(TYPE.REPLY, CMD.EOT));   // closes the transfer; Denon's updater sends it here, before Exec (without it Exec is NAKed)
    log('Transfer finished and closed.');

    check();                                   // last point where stopping is harmless [H]
    progress(total, total, 'execute');
    log('Executing: the mixer now writes its flash. Do not unplug anything or switch it off.');
    await ask(packet(TYPE.SELECT, CMD.EXEC), 'Execute', execTimeout, 3);
    await link.send(packet(TYPE.REPLY, CMD.EOT));
    progress(total, total, 'done');
    log('Finished. The mixer should show "donE". Switch it off and on again.');
  }

  return { VENDOR_ID, PKT, PAYLOAD, TYPE, CMD, FlashError, packet, isAck, isNak, isCapsReply, capsName, CAPS_REPLY, readHeader, checkImage, Link, DryLink, flash, hex };
});
