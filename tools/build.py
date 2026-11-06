#!/usr/bin/env python3
"""Apply patches/NNN-name.yaml to the original image, fix the updater checksum, write image + manifest.

usage: build.py [--model X1700|X1600] [--patches NAME ...] [--date YYYY-MM-DD] [--allow-boot]
  --model       which mixer (default X1700). Original image, hash, size and patch folder come from tools/models.py
                (X1700: patches/, X1600: patches/x1600/).
  no --patches  -> every NNN-*.yaml of that model in order. `--patches none` -> zero patches (must reproduce the original).
A patch is applied only if the bytes at its offset equal `expect`. Writes
  firmware/builds/<X1700FW|X1600FW>-<date>-<patchset>.BIN, <same>.manifest.json and <same>.diff.txt.
Nothing here flashes anything.
"""
import argparse
import datetime
import hashlib
import json
import pathlib
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import checksum  # noqa: E402
import models  # noqa: E402

BOOT_END = 0x10000          # boot block + padding: the updater rewrites it from the file, so a mistake here bricks the unit


def sha(b):
    return hashlib.sha256(b).hexdigest()


ap = argparse.ArgumentParser()
ap.add_argument("--model", default="X1700")
ap.add_argument("--patches", nargs="*")
ap.add_argument("--date", default=datetime.date.today().isoformat())
ap.add_argument("--allow-boot", action="store_true")
a = ap.parse_args()

M = models.get(a.model)
ORIG_SHA, SIZE = M["sha256"], M["size"]
orig = M["image"].read_bytes()
assert sha(orig) == ORIG_SHA and len(orig) == SIZE, "original image hash mismatch"
CK_OK, CK_FIX = checksum.RULES[M["integrity"]]
assert CK_OK(orig), "original image fails its own updater checksum"

files = sorted(M["patch_dir"].glob("[0-9][0-9][0-9]-*.yaml"))
if a.patches is not None:
    want = [] if a.patches == ["none"] else a.patches
    files = [f for f in files if f.stem.split("-", 1)[1] in want or f.stem in want]
    missing = set(want) - {f.stem.split("-", 1)[1] for f in files} - {f.stem for f in files}
    if missing:
        sys.exit(f"unknown patch(es): {sorted(missing)}")

img = bytearray(orig)
touched, applied = [], []
for f in files:
    p = yaml.safe_load(f.read_text())
    for e in p["patches"]:
        off, exp, new = e["offset"], bytes.fromhex(e["expect"]), bytes.fromhex(e["new"])
        if len(exp) != len(new):
            sys.exit(f"{f.name} @{off:#x}: expect/new length differ")
        if off < BOOT_END and not a.allow_boot:
            sys.exit(f"{f.name} @{off:#x}: touches the boot area (< {BOOT_END:#x}); refusing without --allow-boot")
        if off + len(new) > SIZE - (0x20 if M["integrity"] == "crc32" else 4):
            sys.exit(f"{f.name} @{off:#x}: would overlap the checksum word / file header")
        if bytes(img[off:off + len(exp)]) != exp:
            sys.exit(f"{f.name} @{off:#x}: expected {exp.hex()} but found {bytes(img[off:off + len(exp)]).hex()}; not applying")
        img[off:off + len(new)] = new
        touched.append((off, exp, new, e["reason"].strip()))
    applied.append(f.stem)

old_word = bytes(img[-4:])
out = CK_FIX(bytes(img))
assert CK_OK(out) and len(out) == SIZE

# independent verification: the only differences are the listed patch bytes plus the checksum word
diff = [i for i in range(len(orig)) if orig[i] != out[i]]
allowed = {o + k for o, _, n, _ in touched for k in range(len(n))} | set(range(SIZE - 4, SIZE))
assert set(diff) <= allowed, "unexpected differences vs original"

name = "+".join(s.split("-", 1)[1] for s in applied) or "stock"
dest = ROOT / "firmware/builds"
dest.mkdir(exist_ok=True)
base = dest / f"{M['fw_name'].removesuffix('.BIN')}-{a.date}-{name}"
base.with_suffix(".BIN").write_bytes(out)

lines = [f"{base.name}.BIN  sha256 {sha(out)}", f"original sha256 {ORIG_SHA}",
         f"patches: {applied or 'none (zero-patch build)'}", ""]
for o, e, n, r in touched:
    lines.append(f"@{o:#08x}  {e.hex()} -> {n.hex()}   {r}")
lines.append(f"@{SIZE - 4:#08x}  {old_word.hex()} -> {out[-4:].hex()}   updater checksum word (LE sum of all words == 0xFFFFFFFF)")
lines.append(f"\nbytes differing from original: {len(diff)}")
base.with_suffix(".diff.txt").write_text("\n".join(lines) + "\n")
base.with_suffix(".manifest.json").write_text(json.dumps({
    "image": base.name + ".BIN", "sha256": sha(out), "size": len(out), "original_sha256": ORIG_SHA, "date": a.date,
    "patches": applied, "changes": [{"offset": hex(o), "old": e.hex(), "new": n.hex()} for o, e, n, _ in touched],
    "checksum_word": {"old": old_word.hex(), "new": out[-4:].hex()}, "bytes_differing": len(diff),
    "identical_to_original": out == orig,
}, indent=2) + "\n")
print("\n".join(lines))
print("identical to original:", out == orig)
