// Tests for site/dnflash.js against a simulated mixer that enforces the wire format and sequence read from Denon's updater.
//   node site/test_flash.mjs [path/to/X600FW.BIN]
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
const require = createRequire(import.meta.url);
const F = require('./dnflash.js');
const X = require('./patcher.js');
require('./x600/patches.js');
const P = globalThis.X600_PATCHES;
let fails = 0;
const ok = (n, c, i = '') => { console.log(`${c ? 'PASS' : 'FAIL'}  ${n} ${i}`); if (!c) fails++; };

// simulated mixer: strict about every byte of every packet
class FakeMixer {
  constructor(o = {}) { this.o = o; this.l = {}; this.state = 'idle'; this.rx = []; this.log = []; this.execSeen = 0; this.eot = false; this.eots = 0; this.errors = []; this.nakExec = 0; this.capsSeen = 0; this.pk = []; }
  addEventListener(t, f) { this.l[t] = f; }
  removeEventListener() { delete this.l.inputreport; }
  reply(cmd, payload) { const r = new Uint8Array(32); r.set([0x23, 0x0b, cmd]); if (payload) r.set(payload, 3); queueMicrotask(() => this.l.inputreport && this.l.inputreport({ data: new DataView(r.buffer) })); }
  async sendReport(id, d) {
    const p = new Uint8Array(d.buffer ? d.buffer.slice(d.byteOffset, d.byteOffset + d.byteLength) : d);
    if (id !== 0 || p.length !== 32 || p[0] !== 0xFF || (p[1] & 0xF0) !== 0xF0) { this.errors.push('bad framing ' + F.hex(p, 4)); return; }
    const type = p[1] & 7, cmd = p[2]; this.pk.push(p);
    if (type === 1 && cmd === 0x21 && (p[1] & 8) === 0) { this.capsSeen++; if (this.o.silentCaps) return; const c = F.CAPS_REPLY.slice(); if (this.o.name) { c.fill(0, 16); c.set(Array.from(this.o.name, (x) => x.charCodeAt(0)), 16); } queueMicrotask(() => this.l.inputreport && this.l.inputreport({ data: new DataView(c.buffer) })); return; }
    if (type === 0 && cmd === 0x40) { if (this.o.nakSelect) return this.reply(0x15); this.state = 'selected'; this.rx = []; return this.reply(0x06); }
    if (type === 4 && cmd === 0x40) { if (this.state !== 'selected') this.errors.push('start before select'); this.state = 'rx'; this.rx.push(p.slice(3)); return this.reply(0x06); }
    if (type === 5 && cmd === 0x40) { if (this.state !== 'rx') this.errors.push('middle outside transfer'); this.rx.push(p.slice(3)); return; }
    if (type === 6 && cmd === 0x40) { this.rx.push(p.slice(3)); this.state = 'loaded'; return this.reply(0x06, Uint8Array.of(0x00, 0x1e)); }   // 0x1e -> 30 s, as on the real unit
    if (type === 0 && cmd === 0x22) { this.execSeen++; if (this.state !== 'closed') { this.nakExec++; return this.reply(0x15); }   // real mixer: NAK unless the transfer was closed with End Of Transmission
      if (this.o.silentExec && this.execSeen < 3) return; this.state = 'done'; return this.reply(0x06); }
    if (type === 3 && cmd === 0x04) { this.eot = true; this.eots++; if (this.state === 'loaded') this.state = 'closed'; return; }
    this.errors.push('unknown packet ' + F.hex(p, 4));
  }
  image(n) { const b = new Uint8Array(this.rx.length * 29); this.rx.forEach((c, i) => b.set(c, i * 29)); return b.subarray(0, n); }
}
const run = (dev, img, extra = {}) => { const link = new F.Link(dev); const logs = []; return F.flash({ link, image: img, log: (m) => logs.push(m), sleep: async () => {}, ...extra }).then(() => ({ logs }), (e) => ({ err: e, logs })); };

const orig = new Uint8Array(readFileSync(process.argv[2] || new URL('../firmware/original/X600FW.BIN', import.meta.url).pathname));
const patched = X.buildImage(orig, P).image;
const EXPECT = { name: 'DN-X600', pid: 0x500F, size: 0x200000 };

ok('header of the patched image is accepted', F.checkImage(patched, EXPECT).crc === new DataView(patched.buffer).getUint32(patched.length - 4));
let t = null; try { F.checkImage(new Uint8Array(0x200000), EXPECT); } catch (e) { t = e; } ok('a file without the X600 header is refused', t instanceof F.FlashError);
ok('ACK / NAK recognition', F.isAck(F.packet(3, 6)) && F.isNak(F.packet(3, 0x15)) && !F.isAck(F.packet(3, 0x15)) && !F.isAck(null));
ok('Select packet bytes', F.hex(F.packet(0, 0x40), 4) === 'ff f8 40 00' && F.hex(F.packet(3, 4), 3) === 'ff fb 04' && F.hex(F.packet(0, 0x22), 3) === 'ff f8 22');

{ const m = new FakeMixer(); const r = await run(m, patched);
  ok('full transfer succeeds against the simulated mixer', !r.err, r.err ? r.err.message : '');
  ok('mixer received exactly the patched image', Buffer.compare(Buffer.from(m.image(patched.length)), Buffer.from(patched)) === 0, `${m.rx.length} blocks`);
  ok('72316 blocks of 29 bytes', m.rx.length === 72316);
  ok('no framing/sequence errors', m.errors.length === 0, m.errors.join(';'));
  ok('Exec once, then End Of Transmission', m.execSeen === 1 && m.eot && m.state === 'done');
  ok('execute timeout taken from the last ACK (30 s)', r.logs.some((l) => l.includes('allows 30 s')));
  ok('mixer was asked who it is, and said DN-X600', m.capsSeen === 1 && r.logs.some((l) => l.includes('Mixer says: "DN-X600"')));
  ok('Exec was never NAKed (transfer closed first)', m.nakExec === 0);
  ok('End Of Transmission sent twice: after the last block and after Exec', m.eots === 2); }
{ const m = new FakeMixer({ nakSelect: true }); const r = await run(m, patched);
  ok('NAK on Select aborts with nothing transferred', r.err instanceof F.FlashError && m.rx.length === 0 && m.execSeen === 0); }
{ const m = new FakeMixer(); let n = 0; const r = await run(m, patched, { aborted: () => ++n > 1000 });
  ok('stopping mid-transfer sends no Exec', r.err instanceof F.FlashError && m.execSeen === 0 && !m.eot, r.err && r.err.message.slice(0, 40)); }
{ const m = new FakeMixer({ silentExec: true }); const r = await run(m, patched, { readTimeout: 20 });
  ok('Exec is retried on timeout (original: 3 tries)', !r.err && m.execSeen === 3, 'seen ' + m.execSeen); }
{ // control flow identical to Denon's updater as captured on a real DN-X600 (middle data blocks omitted)
  const m = new FakeMixer(); await run(m, patched);
  const seq = m.pk.filter((p) => !(p[1] === 0xFD && p[2] === 0x40)).map((p) => F.hex(p, 3).replace(/ /g, ''));
  ok("packet sequence equals Denon's captured one", JSON.stringify(seq) === JSON.stringify(['fff121', 'fff840', 'fffc40', 'fffe40', 'fffb04', 'fff822', 'fffb04']), seq.join(' '));
  ok('first data packet carries the first 29 bytes of the image', Buffer.compare(Buffer.from(m.pk[2].subarray(3)), Buffer.from(patched.subarray(0, 29))) === 0);
  ok('last data packet carries the last 17 bytes (trailer)', Buffer.compare(Buffer.from(m.pk[m.pk.length - 4].subarray(3, 20)), Buffer.from(patched.subarray(patched.length - 17))) === 0); }
{ const m = new FakeMixer({ name: 'DN-X1700' }); const r = await run(m, patched, { expectName: 'DN-X600' });
  ok('a device that names itself differently is refused before anything is sent', r.err instanceof F.FlashError && m.rx.length === 0 && m.pk.length === 1, r.err && r.err.message.slice(0, 50)); }
{ const m = new FakeMixer({ silentCaps: true }); const r = await run(m, patched, { readTimeout: 20 });
  ok('no answer to the identity request: continues', !r.err && m.execSeen === 1 && r.logs.some((l) => l.includes('No answer to the identity request'))); }
{ const m = new FakeMixer(); const link = new F.Link(m); await link.send(F.packet(0, 0x22));
  ok('Exec without a closed transfer is NAKed by the model (this is what the first real test hit)', m.nakExec === 1); }
{ const link = new F.DryLink(); const r = await F.flash({ link, image: patched, log() {}, sleep: async () => {}, expectName: 'DN-X600' }).then(() => null, (e) => e);
  ok('dry run completes and records every packet', r === null && link.sent.length === 72316 + 5, String(link.sent.length));
  ok('dry run: first/last data packets have types 4 and 6', (link.sent[2][1] & 7) === 4 && (link.sent[link.sent.length - 4][1] & 7) === 6); }

console.log(fails ? `\n${fails} FAILED` : '\nALL PASS'); process.exit(fails ? 1 : 0);
