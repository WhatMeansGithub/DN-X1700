#!/usr/bin/env python3
"""Execute the patched USB clock-request paths of patch 002 in a tiny SH-2 interpreter and check the results.

Starts at the three patched sites in the patched RAM image, runs the original site instructions and the caves,
stubs the firmware's rate getter (0x0C021C3A) and reply routine (0x0C03904A), and stops at the return points.
Only the opcodes these paths use are implemented; anything else raises.
"""
import pathlib
import struct
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import gen_patch002 as g  # noqa: E402

img = bytearray(g.IMG)
for o, old, new, _ in g.edits:
    assert img[o:o + len(old)] == old
    img[o:o + len(new)] = new
RAM0, APP0, APP1 = 0x0C000000, 0x10000, 0xD3908
mem = {}                                            # sparse writes (stack, request struct)


def rd8(a):
    if a in mem: return mem[a]
    f = a - RAM0 + APP0
    assert APP0 <= f < APP1, f"read outside image {a:#x}"
    return img[f]


def rd32(a): return int.from_bytes(bytes(rd8(a + i) for i in range(4)), "big")
def wr32(a, v): [mem.__setitem__(a + i, b) for i, b in enumerate(v.to_bytes(4, "big"))]
def s8(v): return v - 256 if v & 0x80 else v


def run(pc, regs, idx, stop):
    r = regs; T = 0; calls = []; steps = 0
    pr = 0xDEAD0000

    def step(pc):
        nonlocal T, pr
        op = int.from_bytes(bytes([rd8(pc), rd8(pc + 1)]), "big")
        n, m = (op >> 8) & 15, (op >> 4) & 15
        if op == 0x0009: return pc + 2, None
        if op >> 12 == 0xD: return pc + 2, ("set", n, rd32(((pc + 4) & ~3) + (op & 0xFF) * 4))
        if op & 0xFF00 == 0x8400: return pc + 2, ("set", 0, s8(rd8(r[m] + (op & 15))) & 0xFFFFFFFF)
        if op & 0xFF00 == 0x8800: T = int(r[0] == (s8(op & 0xFF) & 0xFFFFFFFF)); return pc + 2, None
        if op & 0xFF00 == 0x8900: return (pc + 4 + s8(op & 0xFF) * 2 if T else pc + 2), None
        if op & 0xFF00 == 0x8B00: return (pc + 4 + s8(op & 0xFF) * 2 if not T else pc + 2), None
        if op >> 12 == 0xE: return pc + 2, ("set", n, s8(op & 0xFF) & 0xFFFFFFFF)
        if op >> 12 == 0x7: return pc + 2, ("set", n, (r[n] + s8(op & 0xFF)) & 0xFFFFFFFF)
        if op & 0xF00F == 0x6003: return pc + 2, ("set", n, r[m])
        if op & 0xF00F == 0x2002: wr32(r[n], r[m]); return pc + 2, None             # mov.l Rm,@Rn
        if op >> 12 == 0xA:
            d = op & 0xFFF; d = d - 0x1000 if d & 0x800 else d
            return ("delay", pc + 4 + d * 2, None), None
        if op & 0xF0FF == 0x402B: return ("delay", r[n], None), None
        if op & 0xF0FF == 0x400B: return ("delay", r[n], pc + 4), None
        raise NotImplementedError(f"{pc:#x}: {op:04x}")

    while pc not in stop:
        steps += 1; assert steps < 500
        if pc == g.GET_RATE_IDX:                       # stub: r0 = index, clobber what the real getter clobbers
            r[0] = idx; r[1] = r[2] = r[4] = r[6] = 0xBADBAD00; pc = pr; continue
        if pc == g.REPLY:                              # stub: record (pointer, length), return 1
            calls.append((r[4], r[5])); r[0] = 1; pc = pr; continue
        nxt, eff = step(pc)
        if eff: r[eff[1]] = eff[2]
        if isinstance(nxt, tuple):                     # delayed branch: run the slot, then jump
            _, target, link = nxt
            s_nxt, s_eff = step(pc + 2)
            assert not isinstance(s_nxt, tuple), "branch in delay slot"
            if s_eff: r[s_eff[1]] = s_eff[2]
            if link: pr = link
            pc = target
        else:
            pc = nxt
    return pc, r, calls


REQ = 0x0C5252BE
ok = True
def check(name, cond, info):
    global ok
    ok &= cond
    print(f"{'PASS' if cond else 'FAIL'}  {name:42s} {info}")

rate_hz = {0: 44100, 1: 48000, 2: 96000}
for idx in (0, 1, 2):
    for ent in (5, 6):
        mem.clear(); mem[REQ + 4] = ent
        pc, r, _ = run(0x0C038ECA, {i: 0 for i in range(16)} | {7: REQ, 15: 0x0C700000}, idx, {g.BACK_TAIL})
        data = bytes(rd8(r[2] + i) for i in range(4))
        want = 44100 if ent == 5 else (96000 if idx == 2 else 48000)
        check(f"GET_CUR rate  setting={rate_hz[idx]} clock={ent}", pc == g.BACK_TAIL and r[1] == 4 and r[7] == REQ
              and int.from_bytes(data, "little") == want, f"-> {int.from_bytes(data, 'little')} Hz, len {r[1]}")
    mem.clear()
    pc, r, _ = run(0x0C038F14, {i: 0 for i in range(16)} | {7: REQ, 15: 0x0C700000}, idx, {g.BACK_TAIL})
    blk = bytes(rd8(r[2] + i) for i in range(14))
    n, lo, hi, res = struct.unpack("<HIII", blk)
    want = 96000 if idx == 2 else 48000
    check(f"GET_RANGE clock6 setting={rate_hz[idx]}", pc == g.BACK_TAIL and r[1] == 14 and (n, lo, hi, res) == (1, want, want, 0),
          f"-> {n} range {lo}..{hi}, len {r[1]}")
    mem.clear()
    pc, r, calls = run(0x0C038F4C, {i: 0 for i in range(16)} | {1: REQ, 15: 0x0C700000}, idx, {g.BACK_SEL})
    pin = rd8(calls[0][0]) if calls else None
    check(f"GET_CUR selector setting={rate_hz[idx]}", pc == g.BACK_SEL and len(calls) == 1 and calls[0][1] == 1
          and pin == (1 if idx == 0 else 2) and r[2] == 1, f"-> pin {pin}, len {calls[0][1] if calls else None}")
print("ALL PASS" if ok else "FAILURES")
sys.exit(0 if ok else 1)
