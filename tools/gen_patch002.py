#!/usr/bin/env python3
"""Generate patches/002-usb-real-rate.yaml: make the UAC2 clock answers report the unit's real sample rate.

usage: gen_patch002.py [--model X1700|X1600]      (default X1700 -> patches/, X1600 -> patches/x1600/)

Firmware bug (X1700 v2.000, X1600 v2.000: same code): the GET_CUR handlers for the clock-source sample rate and the
clock selector pass the VALUE of a variable to the reply routine, which expects a POINTER. The host gets the bytes
found at that address (selector: byte at address 5 = 0xF9). Fix: three small code blocks ("caves") placed over the
lowercase copy of the serial-command table (155 bytes, only used by the service UART parser):

  cave1  GET_CUR sample rate: entity 5 -> 44100, entity 6 -> 48000 or 96000 from the unit's real setting
  cave2  GET_CUR clock selector: pin 1 if the unit runs 44.1 kHz, else pin 2
  cave3  GET_RANGE clock 6: only the real rate (48 or 96 kHz), so the host cannot pick a rate the unit is not running

The real setting comes from the firmware's own getter (leaf, no stack; returns 0=44.1k, 1=48k, else 96k), the same
call the USB init uses to fill the current-rate variable. Per-model addresses live in tools/models.py; make() checks
every anchor in the image before it emits a single edit.
"""
import argparse
import hashlib
import struct
import sys
import types

import models
from models import flash, ram


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
                        out += b"\x00"; addr += 1
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

# Original code at the three sites. Identical in every supported model (only PC-relative, no absolute addresses);
# make() requires an exact match before patching.
SITE_RATE_CODE = bytes.fromhex("6262a023")                  # at site_rate+2: mov.l @r6,r2 ; bra tail
SITE_RANGE_CODE = bytes.fromhex("e11ad25a")                 # at site_range:  mov #26,r1 ; mov.l @(lit),r2
SITE_SEL_CODE = bytes.fromhex("d64e346180008513b0796503")   # at site_sel:    mov.l @(lit),r6 ; movu.b ; mov.w ; bsr reply ; mov


def H(a):
    return "0x%08X" % a


def literal_slot(img, r):
    """Address of the literal read by the mov.l @(disp,PC),Rn at RAM address r."""
    hw = struct.unpack(">H", img[flash(r):flash(r) + 2])[0]
    assert hw >> 12 == 0xD, f"{H(r)}: not a mov.l @(disp,PC) ({hw:04x})"
    return ((r + 4) & ~3) + (hw & 0xFF) * 4


def pc_loads(img, slot, app_end):
    """Every mov.l @(disp,PC),Rn in the app that reads the literal at RAM address `slot` (disp reaches 1020 bytes back)."""
    out = []
    for o in range(max(models.APP0, flash(slot) - 1020), min(flash(slot), app_end), 2):
        hw = struct.unpack(">H", img[o:o + 2])[0]
        if hw >> 12 == 0xD and ((ram(o) + 4) & ~3) + (hw & 0xFF) * 4 == slot:
            out.append(ram(o))
    return out


def make(model="X1700"):
    m = models.get(model)
    img = m["image"].read_bytes()
    app_end = m["app_end"]
    rd = lambda r, n: img[flash(r):flash(r) + n]
    rd32 = lambda r: struct.unpack(">I", rd(r, 4))[0]

    # ---- anchors: every assumption is checked against this image ----
    assert hashlib.sha256(img).hexdigest() == m["sha256"], f"{m['name']}: original image hash mismatch"
    site_rate, site_range, site_sel = m["site_rate"], m["site_range"], m["site_sel"]
    assert rd(site_rate + 2, 4) == SITE_RATE_CODE, "rate GET_CUR site code differs"
    assert rd(site_range, 4) == SITE_RANGE_CODE, "range site code differs"
    assert rd(site_sel, 12) == SITE_SEL_CODE, "selector site code differs"
    bsr = struct.unpack(">H", rd(site_sel + 8, 2))[0] & 0xFFF                    # bsr reply in the selector site
    assert site_sel + 8 + 4 + 2 * (bsr - 0x1000 if bsr & 0x800 else bsr) == m["reply"], "reply routine address"
    assert m["back_tail"] == site_range + 4 and rd(m["back_tail"], 2) == b"\x2f\x22", "range tail (mov.l r2,@r15)"
    assert rd(m["back_sel"], 4) == bytes.fromhex("4f26027b"), "selector epilogue (lds.l @r15+,pr ; rtv/n r2)"
    slot_rate = literal_slot(img, site_rate)
    slot_range = literal_slot(img, site_range + 2)
    assert rd32(slot_range) == m["range_tbl"] + 14, "range literal must point at the clock-6 block (range table + 14)"
    for slot, user in ((slot_rate, site_rate), (slot_range, site_range + 2)):
        loads = pc_loads(img, slot, app_end)
        assert loads == [user], f"literal {H(slot)} is read by {[H(x) for x in loads]}, expected only {H(user)}"
    assert rd(m["range_tbl"], 14) == struct.pack("<HIII", 1, 44100, 44100, 0), "44.1k range block"
    le44 = m["range_tbl"] + 2                                   # existing little-endian 44100 in the firmware's range table
    cfg_byte_02 = ram(m["cfg_desc"]) + 1                       # config descriptor byte 1 (bDescriptorType = 0x02)
    assert rd(ram(m["cfg_desc"]), 4) == bytes.fromhex("09028101"), "configuration descriptor"
    getter = m["get_rate_idx"]
    assert rd(getter, 2)[0] == 0xD6 and rd(getter + 2, 12) == bytes.fromhex("6262e4010100ff0044182219"), "rate getter code"
    # neighbouring getters have the same code but read other settings: the rate one reads base+0x2C / base+0xAA
    base = rd32(literal_slot(img, getter))
    assert (rd32(literal_slot(img, getter + 0x12)), rd32(literal_slot(img, getter + 0x18))) == (base + 0x2C, base + 0xAA), \
        "rate getter must read settings base+0x2C / +0xAA"
    cave, cave_end = m["cave"], m["cave"] + m["cave_len"]
    assert rd(cave, 6) == b"de su " and rd(cave_end, 1) == b"\0", "lowercase command table"
    assert rd32(m["cmd_ptrs"] + 4) == cave, "command-table pointer array"
    refs = [ram(o) for o in range(models.APP0, app_end, 4) if cave - 4 <= struct.unpack(">I", img[o:o + 4])[0] < cave_end]
    assert refs == [m["cmd_ptrs"] + 4], f"cave table referenced from {[H(x) for x in refs]}"

    # ---- caves ----
    a = Asm(cave)
    # cave1: GET_CUR sample rate (entered by jmp from site_rate+2; r7 = request struct, keep it)
    a.label("cave1")
    a.op(MOVB_DISP_R0(4, 7))          # r0 = entity id (5 or 6)
    a.op(CMP_EQ_IMM(5))
    a.op(br(0x8B00, "c1_6", 8))       # bf -> clock 6
    a.op(movl_pc(2, "L_p44"))         # clock 5: 44100
    a.op(br(0xA000, "tailjmp", 12)); a.op(MOV_IMM(4, 1))       # r1 = 4 (delay slot)
    a.label("c1_6")
    a.op(br(0xA000, "common", 12)); a.op(MOV_IMM(4, 5))        # r5 = 4: want the 4-byte rate (delay slot)

    # cave3: GET_RANGE clock 6 (entered by jmp from site_range+2; that jmp's delay slot runs back_tail)
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

    # cave2: GET_CUR clock selector (entered by jmp from site_sel+2)
    a.label("cave2")
    a.op(movl_pc(1, "L_getidx")); a.op(JSR(1)); a.op(NOP)
    a.op(CMP_EQ_IMM(0)); a.op(br(0x8900, "c2_p1", 8))
    a.op(movl_pc(4, "L_pin2")); a.op(br(0xA000, "c2_call", 12)); a.op(NOP)
    a.label("c2_p1"); a.op(movl_pc(4, "L_pin1"))
    a.label("c2_call"); a.op(movl_pc(0, "L_reply")); a.op(JSR(0)); a.op(MOV_IMM(1, 5))  # reply(r4, len 1)
    a.op(MOV_RR(0, 2)); a.op(movl_pc(1, "L_retsel")); a.op(JMP(1)); a.op(NOP)

    # literals and data
    a.align4()
    for name, val in (("L_getidx", getter), ("L_tail", m["back_tail"]), ("L_p44", le44), ("L_reply", m["reply"]),
                      ("L_retsel", m["back_sel"]), ("L_pin2", cfg_byte_02)):
        a.label(name); a.word(val)
    for name, lab in (("L_b48", "blk48"), ("L_b96", "blk96"), ("L_pin1", "blk48")):   # blk48 starts with byte 0x01
        a.label(name); a.word(lambda L, lab=lab: L[lab])
    a.label("blk48"); a.bytes_(struct.pack("<HIII", 1, 48000, 48000, 0))     # one sub-range: 48 kHz
    a.label("blk96"); a.bytes_(struct.pack("<HIII", 1, 96000, 96000, 0))     # one sub-range: 96 kHz
    code, L = a.assemble()
    assert cave + len(code) <= cave_end, f"cave too big: {len(code)} > {m['cave_len']}"

    # ---- site edits (flash offset, expected original bytes, new bytes, reason) ----
    edits = []

    def edit(r, new, why):
        o = flash(r); edits.append((o, img[o:o + len(new)], new, why))

    edit(site_rate + 2, bytes.fromhex("462b0009"), "rate GET_CUR: jmp @r6 (r6 = cave1 via patched literal); nop")
    edit(slot_rate, struct.pack(">I", L["cave1"]), f"literal used only by {H(site_rate)}: &cur_rate -> cave1")
    edit(site_range, bytes.fromhex("d25a422b"),
         f"range clock 6: r2 = cave3 (patched literal); jmp @r2 (delay slot = {H(m['back_tail'])})")
    edit(slot_range, struct.pack(">I", L["cave3"]),
         f"literal used only by {H(site_range + 2)}: range block {H(m['range_tbl'] + 14)} -> cave3")
    edit(site_sel, bytes.fromhex("d601462b00090009") + struct.pack(">I", L["cave2"]),
         f"selector GET_CUR: r6 = cave2 (inline literal at {H(site_sel + 8)}); jmp @r6; nop; nop")
    edit(cave, code, "caves 1-3 over the lowercase serial-command table (service UART only)")
    for o, old, new, _ in edits:
        assert len(old) == len(new)

    return types.SimpleNamespace(model=m, IMG=img, edits=edits, code=code, L=L, GET_RATE_IDX=getter, REPLY=m["reply"],
                                 BACK_TAIL=m["back_tail"], BACK_SEL=m["back_sel"], SITE_RATE=site_rate,
                                 SITE_RANGE=site_range, SITE_SEL=site_sel)


if __name__ == "__main__":
    import yaml
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="X1700")
    g = make(ap.parse_args().model)
    doc = {
        "name": "usb-real-rate",
        "description": __doc__.strip().splitlines()[0],
        "status": "generated by tools/gen_patch002.py; NOT flashed",
        "patches": [{"offset": o, "expect": old.hex(), "new": new.hex(), "reason": why} for o, old, new, why in g.edits],
    }
    out = g.model["patch_dir"] / "002-usb-real-rate.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.safe_dump(doc, sort_keys=False, width=200))
    print(f"wrote {out.relative_to(models.ROOT)}: {len(g.edits)} edits, cave code {len(g.code)} bytes at {g.model['cave']:#x}")
    for k in ("cave1", "cave2", "cave3", "blk48", "blk96"):
        print(f"  {k:6s} {g.L[k]:#010x}")
    sys.exit(0)
