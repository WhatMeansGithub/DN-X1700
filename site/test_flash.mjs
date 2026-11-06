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
  constructor(o = {}) { this.o = o; this.l = {}; this.state = 'idle'; this.rx = []; this.log = []; this.execSeen = 0; this.eot = false; this.errors = []; }
  addEventListener(t, f) { this.l[t] = f; }
  removeEventListener() { delete this.l.inputreport; }
  reply(cmd, payload) { const r = F.packet(F.TYPE.REPLY, cmd, payload); queueMicrotask(() => this.l.inputreport && this.l.inputreport({ data: new DataView(r.buffer) })); }
  async sendReport(id, d) {
    const p = new Uint8Array(d.buffer ? d.buffer.slice(d.byteOffset, d.byteOffset + d.byteLength) : d);
    if (id !== 0 || p.length !== 32 || p[0] !== 0xFF || (p[1] & 0xF8) !== 0xF8) { this.errors.push('bad framing ' + F.hex(p, 4)); return; }
    const type = p[1] & 7, cmd = p[2];
    if (type === 0 && cmd === 0x40) { if (this.o.nakSelect) return this.reply(0x15); this.state = 'selected'; this.rx = []; return this.reply(0x06); }
    if (type === 4 && cmd === 0x40) { if (this.state !== 'selected') this.errors.push('start before select'); this.state = 'rx'; this.rx.push(p.slice(3)); return this.reply(0x06); }
    if (type === 5 && cmd === 0x40) { if (this.state !== 'rx') this.errors.push('middle outside transfer'); this.rx.push(p.slice(3)); return; }
    if (type === 6 && cmd === 0x40) { this.rx.push(p.slice(3)); this.state = 'loaded'; return this.reply(0x06, Uint8Array.of(0x00, 0x02)); }   // 2 -> 2000 ms
    if (type === 0 && cmd === 0x22) { this.execSeen++; if (this.state !== 'loaded') this.errors.push('exec before load'); if (this.o.silentExec && this.execSeen < 3) return; return this.reply(0x06); }
    if (type === 3 && cmd === 0x04) { this.eot = true; return; }
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
  ok('Exec once, then End Of Transmission', m.execSeen === 1 && m.eot);
  ok('execute timeout taken from the last ACK', r.logs.some((l) => l.includes('allows 2 s'))); }
{ const m = new FakeMixer({ nakSelect: true }); const r = await run(m, patched);
  ok('NAK on Select aborts with nothing transferred', r.err instanceof F.FlashError && m.rx.length === 0 && m.execSeen === 0); }
{ const m = new FakeMixer(); let n = 0; const r = await run(m, patched, { aborted: () => ++n > 1000 });
  ok('stopping mid-transfer sends no Exec', r.err instanceof F.FlashError && m.execSeen === 0 && !m.eot, r.err && r.err.message.slice(0, 40)); }
{ const m = new FakeMixer({ silentExec: true }); const r = await run(m, patched, { readTimeout: 20 });
  ok('Exec is retried on timeout (original: 3 tries)', !r.err && m.execSeen === 3, 'seen ' + m.execSeen); }
{ const link = new F.DryLink(); const r = await F.flash({ link, image: patched, log() {}, sleep: async () => {} }).then(() => null, (e) => e);
  ok('dry run completes and records every packet', r === null && link.sent.length === 72316 + 3, String(link.sent.length));
  ok('dry run: first/last data packets have types 4 and 6', (link.sent[1][1] & 7) === 4 && (link.sent[link.sent.length - 3][1] & 7) === 6); }

console.log(fails ? `\n${fails} FAILED` : '\nALL PASS'); process.exit(fails ? 1 : 0);
