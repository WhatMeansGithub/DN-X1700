// Tests for the web patcher core. Needs the original firmware (not in the repo):
//   node site/test.mjs [path/to/X1700FW.BIN]
// It also builds a zip of the firmware on the fly to test the zip path, like Denon's download.
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
const P = globalThis.X1700_PATCHES;
const path = process.argv[2] || new URL('../firmware/original/X1700FW.BIN', import.meta.url).pathname;
if (!existsSync(path)) { console.error('original firmware not found:', path); process.exit(2); }
const orig = new Uint8Array(readFileSync(path));

let fails = 0;
const ok = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'}  ${name} ${info}`); if (!cond) fails++; };
const throwsUser = (name, fn) => { try { fn(); ok(name, false, '(did not throw)'); } catch (e) { ok(name, e instanceof X.UserError, '-> ' + e.message.slice(0, 70)); } };

// SHA-256 against Node's own implementation (several sizes around block boundaries)
for (const n of [0, 1, 55, 56, 63, 64, 65, 1000, orig.length]) {
  const data = orig.subarray(0, n);
  ok(`sha256 matches node:crypto for ${n} bytes`, X.sha256(data) === createHash('sha256').update(data).digest('hex'));
}

// raw .BIN
const r = X.buildImage(orig, P);
ok('raw BIN: result sha256 equals reference build', r.sha256 === P.resultSha256, r.sha256.slice(0, 16));
ok('raw BIN: updater checksum is 0xFFFFFFFF', X.wordSum(r.image) === 0xFFFFFFFF);
ok('raw BIN: 9 patch entries + new checksum word', r.changes.length === 9 && r.checksumWord.new !== r.checksumWord.old, `${r.changes.length} changes`);
ok('original input is not modified', X.sha256(orig) === P.originalSha256);

// zip containing the BIN (deflate), as Denon distributes it
X.setInflate(async (d) => new Uint8Array(inflateRawSync(d)));
const dir = join(tmpdir(), 'x1700-site-test'); execFileSync('mkdir', ['-p', dir]);
execFileSync('python3', ['-I', '-c', `import zipfile,sys;z=zipfile.ZipFile(sys.argv[1],'w',zipfile.ZIP_DEFLATED);z.write(sys.argv[2],'firmware-dnx1700-v2000/X1700FW.BIN');z.writestr('README.txt','x');z.close()`, join(dir, 'fw.zip'), path]);
const zipped = new Uint8Array(readFileSync(join(dir, 'fw.zip')));
const fromZip = await X.loadFirmware(zipped);
ok('zip: extracted firmware equals original', X.sha256(fromZip) === P.originalSha256);
ok('zip: patched result equals reference build', X.buildImage(fromZip, P).sha256 === P.resultSha256);
const stored = new Uint8Array(execFileSync('python3', ['-I', '-c', `import zipfile,sys,io;b=io.BytesIO();z=zipfile.ZipFile(b,'w',zipfile.ZIP_STORED);z.write(sys.argv[1],'X1700FW.BIN');z.close();sys.stdout.buffer.write(b.getvalue())`, path], { maxBuffer: 1 << 26 }));
ok('stored (uncompressed) zip works', X.sha256(await X.loadFirmware(stored)) === P.originalSha256);
let noEntry = false; try { await X.extractFromZip(new Uint8Array(execFileSync('python3', ['-I', '-c', `import zipfile,sys,io;b=io.BytesIO();z=zipfile.ZipFile(b,'w');z.writestr('other.txt','x');z.close();sys.stdout.buffer.write(b.getvalue())`]))); } catch (e) { noEntry = e instanceof X.UserError; }
ok('zip without X1700FW.BIN gives a friendly error', noEntry);

// things that must be refused
throwsUser('wrong size is refused', () => X.buildImage(orig.subarray(0, 1000), P));
const tampered = orig.slice(); tampered[0x20000] ^= 1;
throwsUser('modified firmware (1 bit) is refused', () => X.buildImage(tampered, P));
throwsUser('already patched firmware is refused', () => X.buildImage(r.image, P));
const zeros = new Uint8Array(X.SIZE);
throwsUser('wrong 4 MiB file is refused', () => X.buildImage(zeros, P));

console.log(fails ? `\n${fails} FAILED` : '\nALL PASS');
process.exit(fails ? 1 : 0);
