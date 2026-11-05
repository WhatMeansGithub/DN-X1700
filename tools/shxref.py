#!/usr/bin/env python3
"""Find code that loads the address of a string/object via PC-relative mov.l in the SH app.

usage: shxref.py NEEDLE [NEEDLE...]      (NEEDLE = text in the image, or 0xFLASHOFFSET)
Flash offset F (0x10000..0xd3908) lives at RAM 0x0C000000 + F - 0x10000.
"""
import struct
import sys

IMG = "firmware/original/X1700FW.BIN"
APP0, APP1 = 0x10000, 0xD3908
d = open(IMG, "rb").read()
ram = lambda f: 0x0C000000 + f - APP0
flash = lambda r: r - 0x0C000000 + APP0

# index: literal-pool word value -> list of literal flash offsets
lits = {}
for o in range(APP0, APP1 - 3, 4):
    lits.setdefault(struct.unpack(">I", d[o:o + 4])[0], []).append(o)

def loads_of(lit_off):
    """Flash offsets of mov.l @(disp,PC),Rn instructions that read the literal at lit_off."""
    out = []
    for o in range(max(APP0, lit_off - 1024), lit_off, 2):
        hw = struct.unpack(">H", d[o:o + 2])[0]
        if hw >> 12 == 0xD:
            pc = ram(o)
            tgt = ((pc + 4) & ~3) + (hw & 0xFF) * 4
            if flash(tgt) == lit_off:
                out.append((o, (hw >> 8) & 0xF))
    return out

for n in sys.argv[1:]:
    off = int(n, 0) if n.startswith("0x") else d.find(n.encode())
    r = ram(off)
    print(f"{n!r}: flash {off:#x} ram {r:#010x}")
    for lo in lits.get(r, []):
        for o, reg in loads_of(lo):
            print(f"    literal@{lo:#x} loaded by flash {o:#x} (ram {ram(o):#010x}) -> r{reg}")
