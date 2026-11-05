#!/usr/bin/env python3
"""DN-X1700 firmware patcher: downloads Denon's official v2.000 firmware (or loads it from a file), verifies it byte for byte,
applies the USB fixes and writes a ready-to-flash X1700FW.BIN. Python 3.8+ standard library only, no extra packages.

  python3 tools/x1700_patcher.py                       download from Denon, write ./X1700FW.BIN
  python3 tools/x1700_patcher.py --file firmware-dnx1700-v2000.zip      use a file you already have (zip or X1700FW.BIN)
  python3 tools/x1700_patcher.py --stick /Volumes/UPDATE                 also copy it to the root of your USB stick and verify

Nothing is flashed by this tool. Read the README first: flashing can permanently damage the mixer, and your settings will be reset.
"""
import argparse
import hashlib
import io
import json
import pathlib
import struct
import sys
import urllib.error
import urllib.request
import zipfile

FIRMWARE_URL = "https://cdn.inmusicbrands.com/denondj/legacy/dnx1700/firmware-dnx1700-v2000.zip"
SIZE, BOOT_END, TARGET = 0x400000, 0x10000, 0xFFFFFFFF
DATA = pathlib.Path(__file__).resolve().parent.parent / "patches" / "patches.json"

WARNING = """\
 READ THIS. Flashing firmware can permanently destroy your mixer. The mixer's updater erases the whole flash chip and rewrites
 it; if power is lost or anything goes wrong during that ~30 second write, the mixer will not start again and can only be
 recovered with an external flash programmer. This project is provided as is, WITHOUT WARRANTY, and the author accepts NO
 RESPONSIBILITY for bricked mixers or any other damage. Your settings and presets will be reset to factory defaults.
"""


class Refused(Exception):
    pass


def sha256(b):
    return hashlib.sha256(b).hexdigest()


def word_sum(b):
    return sum(struct.unpack(f"<{len(b) // 4}I", b)) & 0xFFFFFFFF


def download(url):
    print(f"Downloading {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "x1700-patcher"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read(64 * 1024 * 1024 + 1)
    except (urllib.error.URLError, OSError) as e:
        raise Refused(f"Could not download the firmware ({e}). Download it in a browser from the README's link and use --file.")
    if len(data) > 64 * 1024 * 1024:
        raise Refused("The download is far larger than expected; refusing it.")
    print(f"  {len(data):,} bytes")
    return data


def firmware_from(data):
    """Accept Denon's zip or the raw X1700FW.BIN; return the 4 MiB image bytes."""
    if data[:4] != b"PK\x03\x04":
        return data
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise Refused("That zip file is damaged.")
    names = [n for n in z.namelist() if n.replace("\\", "/").split("/")[-1].upper() == "X1700FW.BIN"]
    if not names:
        raise Refused("That zip does not contain X1700FW.BIN. Use Denon's firmware-dnx1700-v2000.zip.")
    return z.read(names[0])


def build(orig, P):
    """Same steps and checks as tools/build.py."""
    if len(orig) != SIZE:
        raise Refused(f"That file is {len(orig):,} bytes; the firmware must be exactly {SIZE:,} bytes (Denon's v2.000 X1700FW.BIN).")
    if sha256(orig) != P["originalSha256"]:
        raise Refused("This is not the original Denon v2.000 firmware, so it will not be patched "
                      f"(expected sha256 {P['originalSha256']}, got {sha256(orig)}). Files that were already patched or modified are refused.")
    if word_sum(orig) != TARGET:
        raise Refused("The firmware fails its own checksum, so it will not be patched.")
    img = bytearray(orig)
    for e in P["patches"]:
        old, new, off = bytes.fromhex(e["expect"]), bytes.fromhex(e["new"]), e["offset"]
        if len(old) != len(new) or off < BOOT_END or off + len(new) > SIZE - 4:
            raise Refused(f"Internal error: bad patch definition at {off:#x}")
        if bytes(img[off:off + len(old)]) != old:
            raise Refused(f"The firmware differs from the expected original at {off:#x}. Not patching.")
        img[off:off + len(new)] = new
    img[-4:] = b"\0\0\0\0"
    img[-4:] = struct.pack("<I", (TARGET - word_sum(bytes(img))) & 0xFFFFFFFF)          # balance the updater checksum
    img = bytes(img)
    if word_sum(img) != TARGET or sha256(img) != P["resultSha256"]:
        raise Refused("Internal error: the result does not match the reference build. Not writing it.")
    return img


def main():
    ap = argparse.ArgumentParser(description="Patch Denon's DN-X1700 firmware for USB audio on current macOS.")
    ap.add_argument("--file", help="use this firmware (Denon's zip or X1700FW.BIN) instead of downloading it")
    ap.add_argument("--out", default=".", help="folder for the patched X1700FW.BIN (default: current folder)")
    ap.add_argument("--stick", help="also copy it to the root of this mounted USB stick (e.g. /Volumes/UPDATE) and verify the copy")
    ap.add_argument("--yes", action="store_true", help="skip the confirmation question (you have read the warning)")
    ap.add_argument("--force", action="store_true", help="overwrite an existing X1700FW.BIN")
    a = ap.parse_args()

    print(WARNING)
    if not a.yes and input(' Type YES (capitals) to confirm you have read this and accept the risk: ').strip() != "YES":
        print("Cancelled. Nothing was done.")
        return 1
    try:
        P = json.loads(DATA.read_text())
        raw = pathlib.Path(a.file).read_bytes() if a.file else download(FIRMWARE_URL)
        img = build(firmware_from(raw), P)
    except FileNotFoundError as e:
        print(f"\nNot patched: file not found ({e.filename}).")
        return 1
    except Refused as e:
        print(f"\nNot patched: {e}")
        return 1

    out = pathlib.Path(a.out) / "X1700FW.BIN"
    if out.exists() and not a.force:
        print(f"\nNot written: {out} already exists. Move it or use --force.")
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(img)
    print(f"\nVerified and written: {out.resolve()}")
    print(f"  {len(img):,} bytes, sha256 {sha256(img)} (matches the reference build)")

    if a.stick:
        stick = pathlib.Path(a.stick)
        if not stick.is_dir():
            print(f"\nNot copied to the stick: {stick} is not a folder. Is the stick mounted?")
            return 1
        dst = stick / "X1700FW.BIN"
        dst.write_bytes(img)
        import os
        os.sync() if hasattr(os, "sync") else None
        if sha256(dst.read_bytes()) != sha256(img):
            print(f"\nThe copy on the stick does NOT match. Do not use it. Try another stick or port.")
            return 1
        print(f"Copied to {dst} and verified byte for byte. Eject the stick properly before unplugging it.")

    print("""
Next (details in the README):
  1. Put X1700FW.BIN in the root of a FAT32 (MBR) USB stick, if you have not used --stick.
  2. Mixer off, stick in the rear USB-A port. Hold MIC2 + DUCKING while switching on until "Version UP Mode".
  3. Press Master EFX INS 1: it shows "Version UP execute?" and "Ver.2000 >>> Ver.2000". Switch off here to cancel safely.
  4. Press Master EFX INS 2 and touch nothing until "Version UP complete! Shut the power OFF" (~30 s). Use stable mains power.
  5. Power-cycle. Set PC/MAC = MAC, USB B, pick the sample rate, and power-cycle again.""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
