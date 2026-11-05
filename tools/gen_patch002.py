#!/usr/bin/env python3
"""Generate patches/002-usb-real-rate.yaml: make the UAC2 clock answers report the unit's real sample rate.

Firmware bug (v2.000): the GET_CUR handlers for the clock-source sample rate (RAM 0x0C038ECA) and the clock selector
(0x0C038F4C) pass the VALUE of a variable to the reply routine 0x0C03904A, which expects a POINTER. The host gets
the bytes found at that address (selector: byte at address 5 = 0xF9). Fix: three small code blocks ("caves") placed
over the lowercase copy of the serial-command table (RAM 0x0C0BEA48, 155 bytes, only used by the service UART parser):

  cave1  GET_CUR sample rate: entity 5 -> 44100, entity 6 -> 48000 or 96000 from the unit's real setting
  cave2  GET_CUR clock selector: pin 1 if the unit runs 44.1 kHz, else pin 2
  cave3  GET_RANGE clock 6: only the real rate (48 or 96 kHz), so the host cannot pick a rate the unit is not running

The real setting comes from the firmware's own getter 0x0C021C3A (leaf, no stack; returns 0=44.1k, 1=48k, else 96k),
the same call the USB init uses to fill the current-rate variable.
"""
import pathlib
import struct

ROOT = pathlib.Path(__file__).resolve().parent.parent
IMG = (ROOT / "firmware/original/X1700FW.BIN").read_bytes()
ram = lambda f: 0x0C000000 + f - 0x10000
flash = lambda r: r - 0x0C000000 + 0x10000

GET_RATE_IDX = 0x0C021C3A      # returns r0 = 0 (44.1k), 1 (48k), other (96k); clobbers r1 r2 r4 r6
REPLY = 0x0C03904A             # reply(r4 = data pointer, r5 = length)
LE44 = 0x0C0C3487              # existing little-endian 44100 in the firmware's range table
BACK_TAIL = 0x0C038F18         # function A tail: stores r2, clamps r1 to wLength, sends
BACK_SEL = 0x0C038F6E          # selector function epilogue: lds.l @r15+,pr ; rtv/n r2
CAVE_BASE = 0x0C0BEA48         # lowercase serial-command table, 155 bytes
CAVE_END = CAVE_BASE + 155


class Asm:
    """Minimal SH-2 assembler for the handful of instructions used here (two passes, label + literal support)."""

    def __init__(self, org):
        self.org, self.items = org, []

    def op(self, f):            # f(addr, labels) -> 16-bit opcode
        self.items.append(("op", f))

    def label(self, name):
        self.items.append(("label", name))

    def align4(self):
        self.items.append(("align", None))

    def word(self, v):
        self.items.append(("word", v))

    def bytes_(self, b):
        self.items.append(("bytes", b))

    def assemble(self):
        labels = {}
        for pass_ in (0, 1):
            addr, out = self.org, bytearray()
            for kind, v in self.items:
                if kind == "label":
                    labels[v] = addr
                elif kind == "op":
                    out += struct.pack(">H", v(addr, labels) if pass_ else 0); addr += 2
                elif kind == "align":
                    while addr % 4:
                        out += b"\x00\x09"[addr % 2:addr % 2 + 1] if False else b"\x00"; addr += 1
                elif kind == "word":
                    out += struct.pack(">I", v(labels) if callable(v) else v) if pass_ else b"\0\0\0\0"; addr += 4
                elif kind == "bytes":
                    out += v; addr += len(v)
        return bytes(out), labels


def movl_pc(n, lab):            # mov.l @(disp,PC),Rn
    def f(a, L):
        t = L[lab]; base = (a + 4) & ~3; d = (t - base) // 4
        assert t % 4 == 0 and 0 <= d < 256 and base + 4 * d == t, (hex(a), lab)
        return 0xD000 | n << 8 | d
    return f


def br(opc, lab, bits):          # bt/bf (8-bit) or bra (12-bit), target = pc + 4 + disp*2
    def f(a, L):
        d = (L[lab] - (a + 4)) // 2
        lim = 1 << (bits - 1)
        assert -lim <= d < lim
        return opc | (d & ((1 << bits) - 1))
    return f


I = lambda v: (lambda a, L: v)
NOP = I(0x0009)
MOV_IMM = lambda imm, n: I(0xE000 | n << 8 | (imm & 0xFF))
CMP_EQ_IMM = lambda imm: I(0x8800 | (imm & 0xFF))
MOVB_DISP_R0 = lambda disp, m: I(0x8400 | m << 4 | disp)    # mov.b @(disp,Rm),R0
MOV_RR = lambda m, n: I(0x6003 | n << 8 | m << 4)          # mov Rm,Rn
JSR = lambda m: I(0x400B | m << 8)
JMP = lambda m: I(0x402B | m << 8)

ADD_IMM = lambda imm, n: I(0x7000 | n << 8 | (imm & 0xFF))
CFG_BYTE_02 = 0x0C0C36A1       # config descriptor byte 1 (bDescriptorType = 0x02), never modified at runtime

a = Asm(CAVE_BASE)
# ---- cave1: GET_CUR sample rate (entered by jmp from 0x0C038ECC; r7 = request struct, keep it) ----
a.label("cave1")
a.op(MOVB_DISP_R0(4, 7))          # r0 = entity id (5 or 6)
a.op(CMP_EQ_IMM(5))
a.op(br(0x8B00, "c1_6", 8))       # bf -> clock 6
a.op(movl_pc(2, "L_p44"))         # clock 5: 44100
a.op(br(0xA000, "tailjmp", 12)); a.op(MOV_IMM(4, 1))       # r1 = 4 (delay slot)
a.label("c1_6")
a.op(br(0xA000, "common", 12)); a.op(MOV_IMM(4, 5))        # r5 = 4: want the 4-byte rate (delay slot)

# ---- cave3: GET_RANGE clock 6 (entered by jmp from 0x0C038F16; that jmp's delay slot runs 0x0C038F18) ----
a.label("cave3")
a.op(MOV_IMM(14, 5))              # r5 = 14: want the 14-byte range block
a.label("common")                 # r2 = 48k or 96k block from the unit's real setting; r1 = r5; 4-byte case -> +2
a.op(movl_pc(1, "L_getidx")); a.op(JSR(1)); a.op(NOP)      # r0 = real rate index (clobbers r1 r2 r4 r6)
a.op(movl_pc(2, "L_b48"))
a.op(CMP_EQ_IMM(0)); a.op(br(0x8900, "sel", 8))
a.op(CMP_EQ_IMM(1)); a.op(br(0x8900, "sel", 8))
a.op(movl_pc(2, "L_b96"))
a.label("sel")
a.op(MOV_RR(5, 1))                # r1 = length
a.op(MOV_RR(5, 0)); a.op(CMP_EQ_IMM(4)); a.op(br(0x8B00, "tailjmp", 8))
a.op(ADD_IMM(2, 2))               # skip wNumSubRanges: point at the little-endian rate
a.label("tailjmp")
a.op(movl_pc(0, "L_tail")); a.op(JMP(0)); a.op(NOP)

# ---- cave2: GET_CUR clock selector (entered by jmp from 0x0C038F4E) ----
a.label("cave2")
a.op(movl_pc(1, "L_getidx")); a.op(JSR(1)); a.op(NOP)
a.op(CMP_EQ_IMM(0)); a.op(br(0x8900, "c2_p1", 8))
a.op(movl_pc(4, "L_pin2")); a.op(br(0xA000, "c2_call", 12)); a.op(NOP)
a.label("c2_p1"); a.op(movl_pc(4, "L_pin1"))
a.label("c2_call"); a.op(movl_pc(0, "L_reply")); a.op(JSR(0)); a.op(MOV_IMM(1, 5))  # reply(r4, len 1)
a.op(MOV_RR(0, 2)); a.op(movl_pc(1, "L_retsel")); a.op(JMP(1)); a.op(NOP)

# ---- literals and data ----
a.align4()
for name, val in (("L_getidx", GET_RATE_IDX), ("L_tail", BACK_TAIL), ("L_p44", LE44), ("L_reply", REPLY),
                  ("L_retsel", BACK_SEL), ("L_pin2", CFG_BYTE_02)):
    a.label(name); a.word(val)
for name, lab in (("L_b48", "blk48"), ("L_b96", "blk96"), ("L_pin1", "blk48")):   # blk48 starts with byte 0x01
    a.label(name); a.word(lambda L, lab=lab: L[lab])
a.label("blk48"); a.bytes_(struct.pack("<HIII", 1, 48000, 48000, 0))     # one sub-range: 48 kHz
a.label("blk96"); a.bytes_(struct.pack("<HIII", 1, 96000, 96000, 0))     # one sub-range: 96 kHz
code, L = a.assemble()
assert CAVE_BASE + len(code) <= CAVE_END, f"cave too big: {len(code)} > 155"

# ---- site edits (flash offset, expected original bytes, new bytes, reason) ----
edits = []
def edit(r, new, why):
    o = flash(r); edits.append((o, IMG[o:o + len(new)], new, why))

edit(0x0C038ECC, bytes.fromhex("462b0009"), "rate GET_CUR: jmp @r6 (r6 = cave1 via patched literal); nop")
edit(0x0C038F08, struct.pack(">I", L["cave1"]), "literal used only by 0x0C038ECA: &cur_rate -> cave1")
edit(0x0C038F14, bytes.fromhex("d25a422b"), "range clock 6: r2 = cave3 (patched literal); jmp @r2 (delay slot = 0x0C038F18)")
edit(0x0C039080, struct.pack(">I", L["cave3"]), "literal used only by 0x0C038F16: range block 0x0C0C3493 -> cave3")
edit(0x0C038F4C, bytes.fromhex("d601462b00090009") + struct.pack(">I", L["cave2"]),
     "selector GET_CUR: r6 = cave2 (inline literal at 0x0C038F54); jmp @r6; nop; nop")
edit(CAVE_BASE, code, "caves 1-3 over the lowercase serial-command table (service UART only)")

if __name__ == "__main__":
    import yaml
    for o, old, new, _ in edits:
        assert len(old) == len(new)
    doc = {
        "name": "usb-real-rate",
        "description": __doc__.strip().splitlines()[0],
        "status": "generated by tools/gen_patch002.py; NOT flashed",
        "patches": [{"offset": o, "expect": old.hex(), "new": new.hex(), "reason": why} for o, old, new, why in edits],
    }
    out = ROOT / "patches/002-usb-real-rate.yaml"
    out.write_text(yaml.safe_dump(doc, sort_keys=False, width=200))
    print(f"wrote {out.relative_to(ROOT)}: {len(edits)} edits, cave code {len(code)} bytes at {CAVE_BASE:#x}")
    for k in ("cave1", "cave2", "cave3", "blk48", "blk96"):
        print(f"  {k:6s} {L[k]:#010x}")
