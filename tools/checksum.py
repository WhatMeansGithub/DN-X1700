#!/usr/bin/env python3
"""X1700FW.BIN / X1600FW.BIN integrity word.

The updater (X1700: RAM 0x0C04061C, 4 x 1 MiB; X1600: RAM 0x0C039598, 2 x 1 MiB; both `cmp/eq #-1` after the read loop)
requires
    sum(little-endian 32-bit words of the whole image) mod 2**32 == 0xFFFFFFFF
The last word of the image (X1700 0x3FFFFC, X1600 0x1FFFFC) balances it.

usage: checksum.py IMAGE            verify
"""
import struct
import sys

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


if __name__ == "__main__":
    data = open(sys.argv[1], "rb").read()
    ok = len(data) in SIZES and word_sum(data) == TARGET
    print(f"{sys.argv[1]}: size {len(data):#x}, sum {word_sum(data):#010x} -> {'OK' if ok else 'BAD'}")
    sys.exit(0 if ok else 1)
