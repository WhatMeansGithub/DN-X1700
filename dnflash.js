// DN-X600 firmware transfer over WebHID (Chrome / Edge, macOS / Windows / Linux). EXPERIMENTAL: not yet run against a real unit.
// Protocol as read from Denon's DDJ Updater 2.0.0 (ddj_updater.exe, class dnhid::*); see docs/x600.md "Updater protocol".
// Runs in the browser and in Node (tests drive it with a simulated mixer). Nothing is uploaded anywhere.
//
// Wire format [C = read from the updater's code, nothing here has been seen on the wire yet]:
//   HID report ID 0, 32 bytes, both directions.
//   byte 0      0xFF
//   byte 1      0xF0 | flag<<3 | type      (flag is 1 in every update message; the mixer's replies also have flag 1)
//   byte 2      command
//   bytes 3..31 payload (29 bytes, zero padded)
//   Reply = ACK when flag 1, type 3, command 0x06 (NAK is command 0x15).
// Sequence [C]: Select (type 0, cmd 0x40) -> ACK; file in 29 byte blocks with cmd 0x40: first block type 4 -> ACK,
//   middle blocks type 5 (no reply), last block type 6 -> ACK whose bytes 3..4 (big endian) x 1000 = time the Execute step may take
//   (15000 ms if 0); then Exec (type 0, cmd 0x22) -> ACK (retried twice on timeout, 1.5 s apart); then End Of Transmission (type 3, cmd 0x04), no reply.
// [H] Nothing is written to flash before Exec ("Load" only transfers; the UI of Denon's updater has them as two buttons), so stopping
//   before Exec is harmless. Not verified.
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.DNFLASH = factory();
})(typeof self !== 'undefined' ? self : globalThis, function () {
  'use strict';

  const VENDOR_ID = 0x154E;
  const PKT = 32, PAYLOAD = 29;
  const TYPE = { SELECT: 0, START: 4, MIDDLE: 5, END: 6, REPLY: 3 };
  const CMD = { DATA: 0x40, EXEC: 0x22, EOT: 0x04, ACK: 0x06, NAK: 0x15 };
  const hex = (b, n) => Array.from(b.subarray(0, n || b.length), (x) => x.toString(16).padStart(2, '0')).join(' ');
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  class FlashError extends Error {}

  function packet(type, cmd, payload) {
    const p = new Uint8Array(PKT);
    p[0] = 0xFF;
    p[1] = 0xF0 | 0x08 | (type & 7);
    p[2] = cmd;
    if (payload) p.set(payload.subarray(0, PAYLOAD), 3);
    return p;
  }
  const isReply = (r, cmd) => !!r && r.length >= 3 && ((r[1] >> 3) & 1) === 1 && (r[1] & 7) === TYPE.REPLY && r[2] === cmd;
  const isAck = (r) => isReply(r, CMD.ACK);
  const isNak = (r) => isReply(r, CMD.NAK);

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
    async send(p) { this.sent.push(p.slice()); this.next = (p[1] === 0xFB && p[2] === CMD.EOT) ? null : packet(TYPE.REPLY, CMD.ACK); }
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

    check();                                   // last point where stopping is harmless [H]
    progress(total, total, 'execute');
    log('Executing: the mixer now writes its flash. Do not unplug anything or switch it off.');
    await ask(packet(TYPE.SELECT, CMD.EXEC), 'Execute', execTimeout, 3);
    await link.send(packet(TYPE.REPLY, CMD.EOT));
    progress(total, total, 'done');
    log('Finished. The mixer should show "donE". Switch it off and on again.');
  }

  return { VENDOR_ID, PKT, PAYLOAD, TYPE, CMD, FlashError, packet, isAck, isNak, readHeader, checkImage, Link, DryLink, flash, hex };
});
