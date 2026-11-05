#!/usr/bin/env python3
"""Disassemble a range of the X1700 image as SH-2A big-endian.

usage: shdis.py START END [--base ADDR] [--img PATH]
START/END are flash offsets. --base is the address START executes at (default: START,
i.e. boot block; use 0x0C000000 + (off - 0x10000) for the app, which is copied to SDRAM).
"""
import argparse
import capstone as cs

p = argparse.ArgumentParser()
p.add_argument("start", type=lambda x: int(x, 0))
p.add_argument("end", type=lambda x: int(x, 0))
p.add_argument("--base", type=lambda x: int(x, 0))
p.add_argument("--img", default="firmware/original/X1700FW.BIN")
a = p.parse_args()

d = open(a.img, "rb").read()
base = a.start if a.base is None else a.base
md = cs.Cs(cs.CS_ARCH_SH, cs.CS_MODE_SH2A | cs.CS_MODE_SHFPU | cs.CS_MODE_BIG_ENDIAN)
import re

def literal(i):
    """Value behind a PC-relative mov.l/mov.w (capstone prints the literal's address, not its value)."""
    m = re.fullmatch(r"(0x[0-9a-f]+),(r\d+)", i.op_str)
    if not m or i.mnemonic not in ("mov.l", "mov.w"):
        return ""
    off = a.start + int(m.group(1), 16) - base
    if not 0 <= off <= len(d) - 4:
        return ""
    if i.mnemonic == "mov.w":
        return f"  ; = {d[off:off+2].hex()}"
    val = int.from_bytes(d[off:off+4], "big")
    note = ""
    if 0x0C000000 <= val < 0x0C0D3908:          # pointer into the app image: show text if it looks like a string
        s = d[val - 0x0C000000 + 0x10000:][:48].split(b"\0")[0]
        if len(s) >= 4 and all(32 <= c < 127 for c in s):
            note = f"  \"{s.decode()}\""
    return f"  ; = {val:08x}{note}"

pos = a.start
while pos < a.end:
    for i in md.disasm(d[pos:a.end], base + pos - a.start):
        o = a.start + i.address - base
        print(f"{i.address:08x}: {d[o:o+i.size].hex()}  {i.mnemonic} {i.op_str}{literal(i)}")
        pos = o + i.size
    if pos < a.end:                              # undecodable halfword (data in the stream): emit it and resync
        print(f"{base + pos - a.start:08x}: {d[pos:pos+2].hex()}  .word")
        pos += 2
