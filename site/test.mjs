// Tests for the web patcher core, for both pages. Needs the original firmware (not in the repo):
//   node site/test.mjs [path/to/X1700FW.BIN] [path/to/X1600FW.BIN] [path/to/X600FW.BIN]
// Defaults: firmware/original/. A missing X1600 or X600 original skips that suite (the X1700 one is required).
// It also builds zips of the firmware on the fly to test the zip path, like Denon's download.
import { createRequire } from 'node:module';
import { readFileSync, existsSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { inflateRawSync } from 'node:zlib';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createHash } from 'node:crypto';

const require = createRequire(import.meta.url);
const X = require('./patcher.js');
require('./patches.js');
require('./x1600/patches.js');
require('./x600/patches.js');
X.setInflate(async (d) => new Uint8Array(inflateRawSync(d)));

let fails = 0;
const ok = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'}  ${name} ${info}`); if (!cond) fails++; };
const throwsUser = (name, fn) => { try { fn(); ok(name, false, '(did not throw)'); } catch (e) { ok(name, e instanceof X.UserError, '-> ' + e.message.slice(0, 70)); } };
const py = (code, ...args) => execFileSync('python3', ['-I', '-c', code, ...args], { maxBuffer: 1 << 26 });

async function suite(label, P, path, fwName, zipName, size) {
  console.log(`\n== ${label}`);
  const orig = new Uint8Array(readFileSync(path));
  const L = (s) => `${label}: ${s}`;

  // raw .BIN
  const r = X.buildImage(orig, P);
  ok(L('raw BIN: result sha256 equals reference build'), r.sha256 === P.resultSha256, r.sha256.slice(0, 16));
  ok(L('raw BIN: updater integrity rule holds (' + (P.integrity || 'word sum') + ')'), X.integrityOk(r.image, P));
  if (P.integrity === 'crc32') ok(L('raw BIN: header untouched (last 32 bytes except the CRC)'), X.sha256(r.image.subarray(size - 32, size - 4)) === X.sha256(orig.subarray(size - 32, size - 4)));
  ok(L('raw BIN: 11 patch entries + new checksum word'), r.changes.length === 11 && r.checksumWord.new !== r.checksumWord.old, `${r.changes.length} changes`);
  ok(L('result has the expected size'), r.image.length === size, String(r.image.length));
  ok(L('original input is not modified'), X.sha256(orig) === P.originalSha256);

  // zip containing the BIN (deflate), as Denon distributes it
  const dir = join(tmpdir(), 'x1700-site-test'); execFileSync('mkdir', ['-p', dir]);
  const zpath = join(dir, `${label}.zip`);
  py(`import zipfile,sys;z=zipfile.ZipFile(sys.argv[1],'w',zipfile.ZIP_DEFLATED);z.write(sys.argv[2],sys.argv[3]);z.writestr('README.txt','x');z.close()`, zpath, path, `${zipName}/${fwName}`);
  const fromZip = await X.loadFirmware(new Uint8Array(readFileSync(zpath)), P.fwName);
  ok(L('zip: extracted firmware equals original'), X.sha256(fromZip) === P.originalSha256);
  ok(L('zip: patched result equals reference build'), X.buildImage(fromZip, P).sha256 === P.resultSha256);
  const stored = new Uint8Array(py(`import zipfile,sys,io;b=io.BytesIO();z=zipfile.ZipFile(b,'w',zipfile.ZIP_STORED);z.write(sys.argv[1],sys.argv[2]);z.close();sys.stdout.buffer.write(b.getvalue())`, path, fwName));
  ok(L('stored (uncompressed) zip works'), X.sha256(await X.loadFirmware(stored, P.fwName)) === P.originalSha256);
  let noEntry = false; try { await X.extractFromZip(new Uint8Array(py(`import zipfile,sys,io;b=io.BytesIO();z=zipfile.ZipFile(b,'w');z.writestr('other.txt','x');z.close();sys.stdout.buffer.write(b.getvalue())`)), P.fwName); } catch (e) { noEntry = e instanceof X.UserError && e.message.includes(fwName); }
  ok(L(`zip without ${fwName} gives a friendly error naming it`), noEntry);

  // things that must be refused
  throwsUser(L('wrong size is refused'), () => X.buildImage(orig.subarray(0, 1000), P));
  const tampered = orig.slice(); tampered[0x20000] ^= 1;
  throwsUser(L('modified firmware (1 bit) is refused'), () => X.buildImage(tampered, P));
  throwsUser(L('already patched firmware is refused'), () => X.buildImage(r.image, P));
  throwsUser(L(`wrong ${size} byte file is refused`), () => X.buildImage(new Uint8Array(size), P));
  return orig;
}

// SHA-256 against Node's own implementation (several sizes around block boundaries)
const p1700 = process.argv[2] || new URL('../firmware/original/X1700FW.BIN', import.meta.url).pathname;
const p1600 = process.argv[3] || new URL('../firmware/original/X1600FW.BIN', import.meta.url).pathname;
if (!existsSync(p1700)) { console.error('original firmware not found:', p1700); process.exit(2); }
const o1700 = new Uint8Array(readFileSync(p1700));
for (const n of [0, 1, 55, 56, 63, 64, 65, 1000, o1700.length]) {
  const data = o1700.subarray(0, n);
  ok(`sha256 matches node:crypto for ${n} bytes`, X.sha256(data) === createHash('sha256').update(data).digest('hex'));
}

const p600 = process.argv[4] || new URL('../firmware/original/X600FW.BIN', import.meta.url).pathname;
const P1700 = globalThis.X1700_PATCHES, P1600 = globalThis.X1600_PATCHES, P600 = globalThis.X600_PATCHES;
await suite('X1700', P1700, p1700, 'X1700FW.BIN', 'firmware-dnx1700-v2000', 0x400000);
if (existsSync(p1600)) {
  const o1600 = await suite('X1600', P1600, p1600, 'X1600FW.BIN', 'firmware-dnx1600-v2000', 0x200000);
  console.log('\n== cross-model');
  throwsUser('X1600 firmware is refused by the X1700 page', () => X.buildImage(o1600, P1700));
  throwsUser('X1700 firmware is refused by the X1600 page', () => X.buildImage(o1700, P1600));
  let wrongZip = false; try { await X.loadFirmware(new Uint8Array(py(`import zipfile,sys,io;b=io.BytesIO();z=zipfile.ZipFile(b,'w');z.write(sys.argv[1],'X1700FW.BIN');z.close();sys.stdout.buffer.write(b.getvalue())`, p1700)), P1600.fwName); } catch (e) { wrongZip = e instanceof X.UserError; }
  ok('X1700 zip dropped on the X1600 page gives a friendly error', wrongZip);
  ok('X1600 data is marked tested (confirmed by one owner)', P1600.untested === false && P1600.fwName === 'X1600FW.BIN' && P1600.size === 0x200000);
} else console.log('\n(X1600 original not found, X1600 suite skipped)');

if (existsSync(p600)) {
  const o600 = await suite('X600', P600, p600, 'X600FW.BIN', 'firmware-dnx600-v1030', 0x200000);
  console.log('\n== X600 specifics');
  throwsUser('X600 firmware is refused by the X1700 page', () => X.buildImage(o600, P1700));
  if (existsSync(p1600)) throwsUser('X600 firmware is refused by the X1600 page', () => X.buildImage(o600, P1600));
  throwsUser('X1700 firmware is refused by the X600 page', () => X.buildImage(o1700, P600));
  ok('X600 data is marked untested, CRC-32, v1.030', P600.untested === true && P600.fwName === 'X600FW.BIN' && P600.size === 0x200000 && P600.integrity === 'crc32' && P600.version === 'v1.030');
  const bad = o600.slice(); bad[bad.length - 1] ^= 1;
  throwsUser('X600 with a wrong CRC is refused', () => X.buildImage(bad, P600));
  const res = X.buildImage(o600, P600).image;
  ok('X600 size field in the header is kept', new DataView(res.buffer).getUint32(res.length - 8, false) === 0x200000);
  ok('X600 CRC matches Python tools/checksum.py', py(`import sys;sys.path.insert(0,'tools');import checksum;d=open(sys.argv[1],'rb').read();print(int(checksum.ok_crc32(d)))`, (await (async()=>{const {writeFileSync}=await import('node:fs');const f=join(tmpdir(),'x600-result.bin');writeFileSync(f,res);return f;})())).toString().trim() === '1');
} else console.log('\n(X600 original not found, X600 suite skipped)');

console.log(fails ? `\n${fails} FAILED` : '\nALL PASS');
process.exit(fails ? 1 : 0);
