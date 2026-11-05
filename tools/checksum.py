#!/usr/bin/env python3
"""X1700FW.BIN integrity word.

The updater (flash fn at 0x0C04061C, `cmp/eq #-1` after the 4 x 1 MB read loop) requires
    sum(little-endian 32-bit words of the whole 4 MiB image) mod 2**32 == 0xFFFFFFFF
The last word of the image (flash 0x3FFFFC) balances it.

usage: checksum.py IMAGE            verify
"""
import struct
import sys

SIZE = 0x400000
TARGET = 0xFFFFFFFF


def word_sum(img: bytes) -> int:
    return sum(struct.unpack(f"<{len(img) // 4}I", img)) & 0xFFFFFFFF


def fix(img: bytes) -> bytes:
    """Return img with the last word rewritten so the sum is TARGET."""
    assert len(img) == SIZE
    body = img[:-4]
    last = (TARGET - word_sum(body + b"\0\0\0\0")) & 0xFFFFFFFF
    return body + struct.pack("<I", last)


if __name__ == "__main__":
    data = open(sys.argv[1], "rb").read()
    ok = len(data) == SIZE and word_sum(data) == TARGET
    print(f"{sys.argv[1]}: size {len(data):#x}, sum {word_sum(data):#010x} -> {'OK' if ok else 'BAD'}")
    sys.exit(0 if ok else 1)
