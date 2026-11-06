#!/usr/bin/env python3
"""X1700FW.BIN / X1600FW.BIN integrity word.

The updater (X1700: RAM 0x0C04061C, 4 x 1 MiB; X1600: RAM 0x0C039598, 2 x 1 MiB; both `cmp/eq #-1` after the read loop)
requires
    sum(little-endian 32-bit words of the whole image) mod 2**32 == 0xFFFFFFFF
The last word of the image (X1700 0x3FFFFC, X1600 0x1FFFFC) balances it.

DN-X600 (v1.030) uses a different rule, found in Denon's DDJ Updater 2.0.0 (ddj_updater.exe, validator at 0x4075B0) [C]:
    crc32(image[:-4] + 00 00 00 00) == big-endian u32 in the last 4 bytes      (standard zlib CRC-32)
The last 32 bytes are a header: 0x11 B name ("DN-X600"), u8, u16 VID 0x154E, u16 PID, u16 version, u32 size (BE), u32 CRC (BE).
The updater also rejects a file whose size field differs from the real size, and one whose name/PID does not match the device.

usage: checksum.py IMAGE [lesum|crc32]     verify (default lesum)
"""
import struct
import sys
import zlib

SIZE = 0x400000                 # X1700; the X1600 image is 0x200000 (tools/models.py)
SIZES = (0x400000, 0x200000)
TARGET = 0xFFFFFFFF


def word_sum(img: bytes) -> int:
    return sum(struct.unpack(f"<{len(img) // 4}I", img)) & 0xFFFFFFFF


def fix(img: bytes) -> bytes:
    """Return img with the last word rewritten so the sum is TARGET."""
    assert len(img) in SIZES
    body = img[:-4]
    last = (TARGET - word_sum(body + b"\0\0\0\0")) & 0xFFFFFFFF
    return body + struct.pack("<I", last)


def crc32_word(img: bytes) -> int:
    return zlib.crc32(img[:-4] + b"\0\0\0\0") & 0xFFFFFFFF


def fix_crc32(img: bytes) -> bytes:
    return img[:-4] + struct.pack(">I", crc32_word(img))


def ok_crc32(img: bytes) -> bool:
    return struct.unpack(">I", img[-4:])[0] == crc32_word(img) and struct.unpack(">I", img[-8:-4])[0] == len(img)


def ok_lesum(img: bytes) -> bool:
    return len(img) in SIZES and word_sum(img) == TARGET


# integrity rule per model (tools/models.py key "integrity")
RULES = {"lesum": (ok_lesum, fix), "crc32": (ok_crc32, fix_crc32)}


if __name__ == "__main__":
    data = open(sys.argv[1], "rb").read()
    rule = sys.argv[2] if len(sys.argv) > 2 else "lesum"
    ok = RULES[rule][0](data)
    print(f"{sys.argv[1]}: size {len(data):#x}, rule {rule} -> {'OK' if ok else 'BAD'}")
    sys.exit(0 if ok else 1)
