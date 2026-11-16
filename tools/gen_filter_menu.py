#!/usr/bin/env python3
"""Filter edition: Utility-menu control of the Parameters filter -> patches/filter/101-filter-menu.yaml.

Adds four rows to Utility > Parameters Filter Setting, after Denon's OFF Filter / ON Filter:
  LPF Reso   0..100 % in 2 % steps   default 28 % (LPF resonance 0.25; Denon's stock value is 0.46, about 52 %)
  HPF Reso   0..100 % in 2 % steps   default 22 % (HPF resonance 0.196)
  HPF Gate   Off, 100..1000 Hz       default 400 Hz: the HPF resonance starts at this cutoff and fades in over 10 knob steps
  HPF Range  Stock, 20-150 .. 20-2500  default 20-600: HPF cutoff 20 Hz at the centre detent, the shown frequency at half
             of the HPF travel, 20 kHz at full travel. "Stock" is Denon's own curve.
The settings use bytes 92..95 of the 126-byte settings record (92 LPF reso, 93 gate, 94 HPF reso, 95 range). Those bytes are
unused by Denon's firmware, are saved with the settings and are part of Preset Export / Import. Stored value = choice + 1;
0 or out of range = the default. Denon's own code copies the menu's work copy to the applied copy when Utility is left.

How it works: the patch adds one small program to unused flash (after the end of the application) and re-points nine pointers
in Denon's code. At start-up, in normal mode only, the program copies itself to RAM (the DSP loader's staging buffer, which is
idle after start-up) and waits for the DSP to finish its own initialisation. From then on it runs before every Parameters-filter
update: when a setting has changed it builds the damping table (resonance and gate) and the HPF cutoff coefficients and writes
them to the DSP over its host port, using Denon's own write routine. Four pointers go to the filter message builder, five to the
Utility menu (row count, row label, draw, encoder, ENTER); the editors reuse Denon's own value-editor screen. Nothing is patched
in Denon's DSP package. In Version UP Mode and the service modes nothing is installed.
Safety: the HPF Range is only written if 18 of the DSP's own cutoff coefficients match the values computed here (+-8 ulp);
otherwise the row has no effect and everything else still works. See docs/filter-edition.md.

Needs: firmware/original/X1700FW.BIN, numpy (exact float32 powf), GNU binutils for sh-elf (sh-elf-as, sh-elf-objcopy, sh-elf-nm; set
SH_ELF_BIN to their folder, or put them on PATH). The checks (an SH-2A emulator running the generated code against models of
Denon's routines, plus static checks) run on every generation. Users of the patch do not need any of this: the result is the patch file.
"""
import json
import math
import os
import pathlib
import shutil
import struct
import subprocess
import tempfile
import zlib

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
ORIG = (ROOT / "firmware/original/X1700FW.BIN").read_bytes()
SH_ELF_BIN = os.environ.get("SH_ELF_BIN")


def sh_tool(name):
    path = shutil.which(name, path=SH_ELF_BIN) if SH_ELF_BIN else shutil.which(name)
    if not path:
        raise SystemExit(f"{name} not found: install GNU binutils for sh-elf and set SH_ELF_BIN to its bin folder")
    return path


NMAX = 50                                     # resonance levels 0..50 = 0..100 % in 2 % steps
RMAX = 0.25 / 0.28                            # resonance at 100 % (0.893; just below self-oscillation); 28 % = 0.25
LDEF, HDEF = 14, 11                           # 28 % (LPF 0.25), 22 % (HPF 0.196)
STOCK_RAMP = [0.0, 0.05, 0.09, 0.14, 0.18, 0.23, 0.28, 0.32, 0.37, 0.41, 0.46]
GATE_HZ = [100, 105, 110, 115, 120, 125, 130, 140, 150, 160, 170, 180, 190, 200, 210, 220, 230, 240, 250, 270, 280, 300,
           320, 340, 360, 380, 400, 420, 450, 470, 500, 530, 560, 600, 630, 670, 700, 750, 800, 850, 900, 950, 1000]
GATE_OFF = 128
MIDS = [150, 200, 250, 300, 350, 400, 450, 500, 550, 600, 700, 800, 900, 1000, 1200, 1400, 1600, 1800, 2000, 2500]
CURVES = ["Stock"] + MIDS                     # range options
CDEF = CURVES.index(600)
GDEF = 1 + GATE_HZ.index(400)

HOOK_FLASH, BLOB_FLASH = 0xD3A00, 0xD4000
RES_RAM = 0x0C0C4200                          # cached; uncached mirror = +0x20000000 (idle after start-up)
STAGE_LO, STAGE_HI = 0x0C0C41B0, 0x0C0C41B0 + 0x1FFC8
SLOT = 0x0C006C00
FILTER_SLOTS = [0x0C0014D8, 0x0C0016E4, 0x0C001F64, 0x0C002260]
ORIG_FN, FILTER_FN = 0x0C000C04, 0x0C0041E8
UHPI_READ, UHPI_WRITE, DELAY, SET_FLG = 0x0C000274, 0x0C0000B6, 0x0C032628, 0x0C02DCE6
MODE_BYTE = 0x0C525310
WORK_BASE, APPLIED_BASE = 0x0C51ACD6, 0x0C51AC58
assert WORK_BASE - APPLIED_BASE == 126
ROWS = [  # menu row 2..5: (label, settings byte, number of values, default)
    ("LPF Reso", 92, NMAX + 1, LDEF),
    ("HPF Reso", 94, NMAX + 1, HDEF),
    ("HPF Gate", 93, 1 + len(GATE_HZ), GDEF),
    ("HPF Range", 95, len(CURVES), CDEF),
]
AD31 = WORK_BASE + 91                                     # work copy 'ON Filter' value (Denon's row 1)
DSP_DONE, DSP_CHECK = 0x10035594, 0x818F3018 + 20 * 4
CHECK_LO, CHECK_HI = 0x3EB33333, 0x3EB5C28F       # STOCK DSP: LPF damping[20] = damp(0.46) = 0x3EB4AF90 once its init is done
TARGETS = [0x818F3018, 0x818F4018, 0x818F5018, 0x1002F280]          # damping LPF+HPF (2 KB): 3 rate sets + working copy
FH = [0x818F0418 + r * 0x1000 + 0x200 for r in range(3)]            # fH[128..255] per rate
FHB = [0x818F0818 + r * 0x1000 + 0x200 for r in range(3)]           # fH(B)[128..255] per rate
W_FH, W_FHB = 0x1002E680 + 0x200, 0x1002EA80 + 0x200                # working copy (current rate)
FL_J = 64                                                            # fL index used to identify the current rate
W_FL, S_FL = 0x1002E280 + 4 * FL_J, 0x818F0018 + 4 * FL_J            # working fL[J]; SDRAM fL[J] of rate 0 (+0x1000 per rate)
TRIES = 300
# Utility menu engine (decoded from the firmware, not published as disassembly)
SCREEN, CURSOR = 0x0C51AC4C, 0x0C51AF05
STK_CUR2, STK_DISP2, STK_DEPTH = 0x0C51B23A, 0x0C51B240, 0x0C51B244
ENTER_FN, ENC_FN, DRAW_FN, COUNT_FN, LABEL_FN = 0x0C0224C8, 0x0C023C00, 0x0C03652C, 0x0C023A14, 0x0C03471A
NAV_FN, SCROLL_FN, STEP_FN, TEXT_FN = 0x0C022174, 0x0C021D98, 0x0C021CA4, 0x0C027DCE
MENU_SLOTS = [(0x0C03CC1C, ENTER_FN, "m_enter"), (0x0C03C6E0, ENC_FN, "m_enc"), (0x0C0134E0, DRAW_FN, "m_draw"),
              (0x0C03672C, COUNT_FN, "m_count"), (0x0C036744, LABEL_FN, "m_label")]
VALUE_X, ROW0_Y, ROW_DY, WHITE = 0xEC, 0x50, 0x18, 0xFFFF
fl = lambda r: r - 0x0C000000 + 0x10000
be32 = lambda b, o: struct.unpack(">I", b[o:o + 4])[0]


def _dsp_sections():
    """Denon's DSP package (flash 0x1041A8): header + zlib sections stored as byte-swapped 32-bit words.
    Returns the load ranges (dst, dst + size) of all sections and the unpacked section 3 (the .cinit tables)."""
    p2 = ORIG[0x1041A8:0x11FFC8]
    magic, count, _entry = struct.unpack(">3I", p2[:12])
    assert magic == 0x15223759 and count == 9
    rng, sec3 = [], None
    for k in range(count):
        src, dst, size, packed = struct.unpack(">4I", p2[12 + 16 * k:28 + 16 * k])
        rng.append((dst, dst + size))
        if k == 3:
            raw = p2[src - 0x80000000:src - 0x80000000 + ((packed + 3) & ~3)]
            sec3 = zlib.decompress(b"".join(raw[i:i + 4][::-1] for i in range(0, len(raw), 4)))
            assert len(sec3) == size and dst == 0x100321C8
    return rng, sec3


_SECS, _s3 = _dsp_sections()
for t, n in [(t, 0x800) for t in TARGETS] + [(t, 0x200) for t in FH + FHB + [W_FH, W_FHB]]:
    assert not any(lo < t + n and t < hi for lo, hi in _SECS), f"{t:#x} overlaps DSP code/data sections"
    assert 0x818F0018 <= t and t + n <= 0x818F6018 or 0x1002E280 <= t and t + n <= 0x1003028C

# ------------------------------------------------------------- cutoff tables
LP256 = struct.unpack("<256f", _s3[8:8 + 1024])
HP_STOCK = list(struct.unpack("<256f", _s3[0x408 + 8:0x408 + 8 + 1024]))[128:]


def hp_curve(fmid):
    """HPF cutoff (Hz) for HPF knob indices 128..255 with fmid Hz at half travel: 20 Hz at the detent, 20 kHz at full travel,
    a quadratic in log-frequency; values below 60 Hz in 10 Hz steps, above snapped to the nearest value of Denon's own table"""
    o1, om = math.log2(20000 / 20), math.log2(fmid / 20)
    qb = (om - 0.5 * o1) / (0.25 - 0.5)
    qa = o1 - qb
    ideal = [20 * 2 ** (qa * (i / 127) + qb * (i / 127) ** 2) for i in range(128)]
    hp = [float(max(20.0, 10 * round(f / 10))) if f < 59 else min(LP256[:128], key=lambda v: abs(math.log(v / f)))
          for f in ideal]
    hp[-1] = 20000.0
    assert all(y >= x for x, y in zip(hp, hp[1:])), fmid
    return hp


HPS = [HP_STOCK] + [hp_curve(m) for m in MIDS]


F32 = np.float32
INV_FS = [F32(1 / 44100), F32(1 / 48000), F32(1 / 96000)]       # DSP data 0x10034944


def fcoef(fc, r):
    """DSP coefficient f for cutoff fc at rate r (main 0x81B03284): x = pi*fc*k/fs (double), tanf if r != 0, f = 2 sinf(x)"""
    k = 0.5 if r == 2 else 0.25
    x = float(F32(float(F32(F32(math.pi) * F32(fc))) * float(INV_FS[r]) * k))
    if r:
        x = float(F32(math.tan(x)))
    return float(F32(2.0 * float(F32(math.sin(x)))))


fbits = lambda x: struct.unpack(">I", struct.pack(">f", x))[0]
CURVE_DATA = b"".join(struct.pack(">128f", *[fcoef(fc, r) for fc in hp]) for hp in HPS for r in range(3))
assert len(CURVE_DATA) == len(CURVES) * 1536
# clamp check: 2/f - f/2 > 2 for every coefficient we may write, so the DSP's damping clamps never depend on the curve
assert all(2 / f - f / 2 > 2 for f in struct.unpack(f">{len(CURVE_DATA) // 4}f", CURVE_DATA))


def gate_index(c, opt):
    if opt == 0:
        return GATE_OFF
    hz = GATE_HZ[opt - 1]
    return next(128 + i for i, v in enumerate(HPS[c]) if v >= hz)


GIDX = b"".join(bytes(gate_index(c, g) for g in range(1 + len(GATE_HZ))).ljust(64, b"\0") for c in range(len(CURVES)))
assert max(GIDX) + 10 <= 256 and gate_index(CDEF, GDEF) == 184
VCHECK = [(base[r] + 4 * (i - 128), fbits(fcoef(HPS[0][i - 128], r))) for r in range(3) for base in (FH, FHB)
          for i in (140, 184, 230)]

# ------------------------------------------------------------- damping data
damp = lambda r: float(F32(2.0) * (F32(1.0) - F32(r) ** F32(0.25)))


def level_words(n):
    """11 damping values of level n: fade-in steps 0..9 (stock fade-in shape scaled to this level), full value"""
    r = round(RMAX * n / NMAX, 6)
    ramp = [round(v * r / 0.46, 2) for v in STOCK_RAMP]
    return [damp(ramp[i]) for i in range(10)] + [damp(r)]


def table(a, b, g):
    la, lb = level_words(a), level_words(b)
    return [la[i] if i < 10 else la[10] for i in range(256)] + \
           [damp(0.0) if i < g else lb[i - g] if i < g + 10 else lb[10] for i in range(256)]


def value(stored, count, default):
    return stored - 1 if 1 <= stored <= count else default


LEVELS = b"".join(struct.pack(">11f", *level_words(n)) for n in range(NMAX + 1))
STRS = [[f"{2 * n}%" for n in range(NMAX + 1)], [f"{2 * n}%" for n in range(NMAX + 1)],
        ["Off"] + [f"{h} Hz" for h in GATE_HZ], ["Stock"] + [f"20-{m}" for m in MIDS]]
STRB = lambda lst: b"".join(s.ljust(8).encode().ljust(16, b"\0") for s in lst)   # padded to 8 like Denon's value strings
asm_bytes = lambda b: "\n".join("        .byte   " + ", ".join(str(x) for x in b[k:k + 32]) for k in range(0, len(b), 32))
assert damp(0.0) == 2.0 and fbits(2.0) == 0x40000000 and all(len(s) <= 8 for l in STRS for s in l)
A = lambda sym: f"{RES_RAM:#x} + {sym} - res_entry"
MASKED_CALL = """
        stc     sr,r11
        mov     r11,r0
        or      #0xF0,r0
        ldc     r0,sr
        mov.l   {fn},r1
        jsr     @r1
        nop
        ldc     r11,sr"""          # hook only (the hook saves r11)


def masked(fn_lit):
    return f"""
        sts.l   pr,@-r15
        mov.l   r8,@-r15
        stc     sr,r8
        mov     r8,r0
        or      #0xF0,r0
        ldc     r0,sr
        mov.l   {fn_lit},r1
        jsr     @r1
        nop
        ldc     r8,sr
        mov.l   @r15+,r8
        lds.l   @r15+,pr
        rts
        nop"""


RES_ASM = f"""
        .text
res_entry:                              ! replaces 0x0C0041E8 in the 4 slots; r4/r5 = builder args, pr = caller's return
        mov.l   r4,@-r15
        mov.l   r5,@-r15
        mov.l   r6,@-r15
        mov.l   r7,@-r15
        sts.l   pr,@-r15
        mov     #0,r4
        bsr     apply
        nop
        lds.l   @r15+,pr
        mov.l   @r15+,r7
        mov.l   @r15+,r6
        mov.l   @r15+,r5
        mov.l   @r15+,r4
        mov.l   R_orig,r3
        jmp     @r3
        nop

apply:                                  ! r4 = force (boot): damping table + HPF range, written only when changed
        sts.l   pr,@-r15
        mov.l   r8,@-r15
        mov.l   r9,@-r15
        mov.l   r10,@-r15
        mov.l   r11,@-r15
        mov.l   r12,@-r15
        mov.l   r13,@-r15
        mov.l   r14,@-r15
        mov     r4,r14
        tst     r14,r14
        bt      ap_set
        bsr     verify                  ! boot: is the DSP's own stock fH what this script computed?
        nop
        mov.l   R_cok,r1
        mov.l   r0,@r1
ap_set: mov     #0,r0
        bsr     app_val
        nop
        mov     r0,r8                   ! r8 = LPF level
        mov     #1,r0
        bsr     app_val
        nop
        mov     r0,r9                   ! r9 = HPF level
        mov     #3,r0
        bsr     app_val
        nop
        mov     r0,r11                  ! r11 = range option
        mov     #2,r0
        bsr     app_val
        nop
        mov     r11,r1                  ! r10 = gate knob index = gidx[range * 64 + gate option]
        shll2   r1
        shll2   r1
        shll2   r1
        add     r1,r0
        mov.l   R_gidx,r1
        mov.b   @(r0,r1),r10
        extu.b  r10,r10
        mov     r10,r12                 ! damping key = gate << 16 | HPF << 8 | LPF
        shll8   r12
        or      r9,r12
        shll8   r12
        or      r8,r12
        mov.l   R_lastd,r1
        tst     r14,r14
        bf      ap_d
        mov.l   @r1,r0
        cmp/eq  r12,r0
        bt      ap_curve
ap_d:   mov.l   r12,@r1
        bsr     build
        nop
        mov.l   R_targets,r12
        mov     #4,r13
ap_dl:  mov.l   @r12+,r4
        mov.l   R_buf,r5
        mov.l   R_2k,r6
        bsr     uhpi_w
        nop
        dt      r13
        bf      ap_dl
ap_curve: mov.l R_cok,r1
        mov.l   @r1,r0
        tst     r0,r0
        bt      ap_out
        mov.l   R_lastc,r1
        tst     r14,r14
        bf      ap_c
        mov.l   @r1,r0
        cmp/eq  r11,r0
        bt      ap_out
ap_c:   mov.l   r11,@r1
        mov     r11,r12                 ! r12 = range data = curves + 1536 * range
        shll8   r12
        shll    r12
        mov     r12,r0
        shll    r0
        add     r0,r12
        mov.l   R_curves,r1
        add     r1,r12
        mov.l   R_fsets,r13             ! SDRAM: fH, fH(B) for rates 0, 1, 2
        mov     #3,r11
ap_cr:  mov.l   @r13+,r4
        mov     r12,r5
        mov.l   R_512,r6
        bsr     uhpi_w
        nop
        mov.l   @r13+,r4
        mov     r12,r5
        mov.l   R_512,r6
        bsr     uhpi_w
        nop
        mov.l   R_512,r0
        add     r0,r12
        dt      r11
        bf      ap_cr
        bsr     rate_of                 ! working copy: only for the rate the DSP is running at
        nop
        mov     #2,r1
        cmp/hi  r1,r0
        bt      ap_out
        shll8   r0
        shll    r0
        mov.l   R_1536,r1
        sub     r1,r12
        add     r0,r12
        mov.l   R_wfh,r4
        mov     r12,r5
        mov.l   R_512,r6
        bsr     uhpi_w
        nop
        mov.l   R_wfhb,r4
        mov     r12,r5
        mov.l   R_512,r6
        bsr     uhpi_w
        nop
ap_out: mov.l   @r15+,r14
        mov.l   @r15+,r13
        mov.l   @r15+,r12
        mov.l   @r15+,r11
        mov.l   @r15+,r10
        mov.l   @r15+,r9
        mov.l   @r15+,r8
        lds.l   @r15+,pr
        rts
        nop

app_val:                                ! r0 = row 0..3 -> r0 = applied value of that row (row descriptor work byte - 126)
        shll2   r0
        shll2   r0
        mov.l   R_desc,r3
        add     r0,r3
        mov.l   @r3,r1
        add     #-126,r1
        bra     val_at
        nop

build:                                  ! r8 = LPF level, r9 = HPF level, r10 = gate index -> 512 words in buf
        sts.l   pr,@-r15
        mov     r8,r0
        bsr     lvlptr
        nop
        mov.l   R_buf,r2
        mov     r0,r1
        mov     #10,r5
b_1:    mov.l   @r1+,r0
        mov.l   r0,@r2
        add     #4,r2
        dt      r5
        bf      b_1
        mov.l   @r1,r0
        mov.l   R_246,r5
b_2:    mov.l   r0,@r2
        add     #4,r2
        dt      r5
        bf      b_2
        mov     r9,r0
        bsr     lvlptr
        nop
        mov     r0,r3
        mov.l   R_two,r0                ! HPF: 2.0 (no resonance) below the gate
        mov     r10,r5
b_3:    mov.l   r0,@r2
        add     #4,r2
        dt      r5
        bf      b_3
        mov     r3,r1                   ! fade-in over the next 10 steps
        mov     #10,r5
b_4:    mov.l   @r1+,r0
        mov.l   r0,@r2
        add     #4,r2
        dt      r5
        bf      b_4
        mov.l   @r1,r0                  ! then the full HPF value
        mov.l   R_246,r5
        sub     r10,r5
b_5:    mov.l   r0,@r2
        add     #4,r2
        dt      r5
        bf      b_5
        lds.l   @r15+,pr
        rts
        nop

lvlptr:                                 ! r0 = level -> r0 = levels + 44 * level; clobbers r1
        mov     r0,r1
        shll2   r1
        mov     r1,r0
        shll    r0
        add     r0,r1
        shll2   r0
        add     r0,r1
        mov.l   R_levels,r0
        add     r1,r0
        rts
        nop

verify:                                 ! -> r0 = 1 if all check words are within +-8 ulp of the DSP's own values
        sts.l   pr,@-r15
        mov.l   r8,@-r15
        mov.l   r9,@-r15
        mov.l   r10,@-r15
        add     #-4,r15
        mov.l   R_vlist,r8
        mov     #{len(VCHECK)},r9
        mov     #1,r10
v_l:    mov.l   @r8+,r4
        mov     r15,r5
        bsr     uhpi_r
        nop
        mov.l   @r15,r0
        mov.l   @r8+,r1
        sub     r1,r0
        add     #8,r0
        mov     #16,r1
        cmp/hi  r1,r0
        bf      v_ok
        mov     #0,r10
v_ok:   dt      r9
        bf      v_l
        mov     r10,r0
        add     #4,r15
        mov.l   @r15+,r10
        mov.l   @r15+,r9
        mov.l   @r15+,r8
        lds.l   @r15+,pr
        rts
        nop

rate_of:                                ! -> r0 = rate 0..2 whose SDRAM fL[J] equals the working fL[J], else -1
        sts.l   pr,@-r15
        mov.l   r8,@-r15
        mov.l   r9,@-r15
        add     #-8,r15
        mov.l   R_wfl,r4
        mov     r15,r5
        bsr     uhpi_r
        nop
        mov.l   R_sfl,r8
        mov     #0,r9
rt_l:   mov     r8,r4
        mov     r15,r5
        add     #4,r5
        bsr     uhpi_r
        nop
        mov.l   @r15,r0
        mov.l   @(4,r15),r1
        cmp/eq  r1,r0
        bt      rt_hit
        mov.l   R_4k,r0
        add     r0,r8
        add     #1,r9
        mov     r9,r0
        cmp/eq  #3,r0
        bf      rt_l
        mov     #-1,r9
rt_hit: mov     r9,r0
        add     #8,r15
        mov.l   @r15+,r9
        mov.l   @r15+,r8
        lds.l   @r15+,pr
        rts
        nop

uhpi_w:                                 ! Denon's UHPI write (r4 DSP address, r5 source, r6 bytes) with interrupts masked{masked("R_wr")}
uhpi_r:                                 ! Denon's UHPI read of 4 bytes (r4 DSP address, r5 destination), interrupts masked
        mov     #4,r6{masked("R_rd")}

val_at:                                 ! r1 = setting byte, r3 = row descriptor -> r0 = value (0 / out of range -> default)
        mov.b   @r1,r0
        extu.b  r0,r0
        add     #-1,r0
        mov.l   @(4,r3),r1
        cmp/hs  r1,r0
        bf      va_ok
        mov.l   @(8,r3),r0
va_ok:  rts
        nop

        .align  2
R_orig:    .long {FILTER_FN:#x}
R_wr:      .long {UHPI_WRITE:#x}
R_rd:      .long {UHPI_READ:#x}
R_cok:     .long {A("curve_ok")}
R_lastd:   .long {A("last_d")}
R_lastc:   .long {A("last_c")}
R_desc:    .long {A("desc")}
R_gidx:    .long {A("gidx")}
R_levels:  .long {A("levels")}
R_curves:  .long {A("curves")}
R_buf:     .long {A("buf")}
R_targets: .long {A("targets")}
R_fsets:   .long {A("fsets")}
R_vlist:   .long {A("vlist")}
R_246:     .long 246
R_two:     .long 0x40000000
R_2k:      .long 0x800
R_512:     .long 0x200
R_1536:    .long 1536
R_4k:      .long 0x1000
R_wfh:     .long {W_FH:#x}
R_wfhb:    .long {W_FHB:#x}
R_wfl:     .long {W_FL:#x}
R_sfl:     .long {S_FL:#x}

desc_of:                                ! r0 = menu row 2..5 -> r3 = row descriptor; clobbers r0
        add     #-2,r0
        shll2   r0
        shll2   r0
        mov.l   M_desc,r3
        add     r0,r3
        rts
        nop

is_res:                                 ! r0 = screen -> T = one of our editors open; then r2 = row, r3 = descriptor
        mov.l   M_14a,r1
        cmp/eq  r1,r0
        bf      ir_no
        mov.l   M_depth,r1
        mov.b   @r1,r0
        cmp/eq  #3,r0
        bf      ir_no
        mov.l   M_cur2,r1
        mov.b   @r1,r0
        extu.b  r0,r0
        mov     r0,r2
        add     #-2,r0
        mov     #3,r1
        cmp/hi  r1,r0
        bt      ir_no
        shll2   r0
        shll2   r0
        mov.l   M_desc,r3
        add     r0,r3
        sett
        rts
        nop
ir_no:  clrt
        rts
        nop

m_count:                                ! item count: 6 rows on screen 0x148, otherwise Denon's
        mov.l   M_148,r1
        cmp/eq  r1,r4
        bf      mc_orig
        mov     #6,r0
        rts
        nop
mc_orig: mov.l  M_count,r1
        jmp     @r1
        nop

m_label:                                ! row labels of screen 0x148: rows 2..5 ours, otherwise Denon's
        mov     r4,r0
        add     #-2,r0
        mov     #3,r1
        cmp/hi  r1,r0
        bt      ml_orig
        shll2   r0
        mov.l   M_labt,r1
        mov.l   @(r0,r1),r0
        rts
        nop
ml_orig: mov.l  M_label,r1
        jmp     @r1
        nop

m_enter:                                ! ENTER key in the Utility menu
        sts.l   pr,@-r15
        mov.l   M_screen,r1
        mov.l   @r1,r0
        mov.l   M_148,r1
        cmp/eq  r1,r0
        bf      mn_res
        mov.l   M_cursor,r1
        mov.b   @r1,r0
        extu.b  r0,r0
        mov     r0,r2
        add     #-2,r0
        mov     #3,r1
        cmp/hi  r1,r0
        bt      mn_orig
        mov     r2,r0
        bsr     desc_of
        nop
        mov.l   r3,@-r15
        mov     #0,r4                   ! open the editor exactly like Denon's 'enter child' path (0x0C0232BE..0x0C0232D6)
        mov.l   M_nav,r1
        jsr     @r1
        nop
        mov.l   M_screen,r1
        mov.l   M_14a,r0
        mov.l   r0,@r1
        mov.l   @r15+,r3
        mov.l   @r3,r1
        bsr     val_at
        nop
        mov.l   M_cursor,r1
        mov.b   r0,@r1
        lds.l   @r15+,pr
        rts
        nop
mn_res: bsr     is_res
        nop
        bf      mn_orig
        mov.l   M_cursor,r2             ! confirm: setting = value + 1, then Denon's 0x14A case stores the UNCHANGED
        mov.b   @r2,r0                  !   ON Filter value and does its usual commit + return to 0x148
        extu.b  r0,r0
        mov.l   @(4,r3),r1
        add     #-1,r1
        cmp/hi  r1,r0
        bf      mn_ok
        mov     r1,r0
mn_ok:  add     #1,r0
        mov.l   @r3,r1
        mov.b   r0,@r1
        mov.l   M_ad31,r1
        mov.b   @r1,r0
        mov.b   r0,@r2
mn_orig: lds.l  @r15+,pr
        mov.l   M_enter,r1
        jmp     @r1
        nop

m_enc:                                  ! encoder in the Utility menu (r4 = direction, r5 = steps)
        sts.l   pr,@-r15
        mov.l   r8,@-r15
        mov.l   r9,@-r15
        mov.l   r10,@-r15
        mov     r4,r8
        mov     r5,r9
        mov     #6,r10
        mov.l   M_screen,r1
        mov.l   @r1,r0
        mov.l   M_148,r1
        cmp/eq  r1,r0
        bt      me_go
        bsr     is_res
        nop
        bf      me_orig
        mov.l   @(4,r3),r10
        bra     me_go
        nop
me_orig: mov    r8,r4                   ! not ours: Denon's handler with the original r4/r5/pr
        mov     r9,r5
        mov.l   @r15+,r10
        mov.l   @r15+,r9
        mov.l   @r15+,r8
        lds.l   @r15+,pr
        mov.l   M_enc,r1
        jmp     @r1
        nop
me_go:  extu.w  r8,r4                   ! the same two calls as Denon's handler (0x0C023C20..0x0C023C4A), our count
        extu.w  r9,r5
        mov.l   M_cursor,r1
        mov.b   @r1,r6
        mov     r10,r7
        mov.l   M_scroll,r1
        jsr     @r1
        nop
        mov     #1,r1
        mov.l   r1,@-r15
        mov.l   r10,@-r15
        mov     r8,r4
        mov     r9,r5
        mov.l   M_cursor,r1
        mov.b   @r1,r6
        extu.b  r6,r6
        mov     #0,r7
        mov.l   M_step,r1
        jsr     @r1
        nop
        add     #8,r15
        mov.l   M_cursor,r1
        mov.b   r0,@r1
        mov.l   @r15+,r10
        mov.l   @r15+,r9
        mov.l   @r15+,r8
        lds.l   @r15+,pr
        rts
        nop

m_draw:                                 ! draw of screens 0x148/0x149/0x14A (r4 = screen, r5 = cursor/value, r6 = row)
        sts.l   pr,@-r15
        mov.l   r8,@-r15
        mov.l   r9,@-r15
        mov.l   r10,@-r15
        mov.l   r11,@-r15
        mov.l   r12,@-r15
        mov     r4,r8
        mov     r5,r9
        mov     r6,r10
        mov.l   M_draw,r1               ! Denon's draw (6 rows and the row 2..5 labels come from m_count / m_label)
        jsr     @r1
        nop
        mov     #-1,r11                 ! r11 = row being edited by us (none)
        extu.b  r11,r11
        mov.l   M_148,r1
        cmp/eq  r1,r8
        bf      md_edit
        extu.b  r9,r0                   ! list: top row = cursor - row
        extu.b  r10,r10
        sub     r10,r0
        mov     r0,r10
        bra     md_rows
        nop
md_edit: mov.l  M_cur2,r1               ! editor: top row = parent cursor - parent row (as Denon's draw)
        mov.b   @r1,r0
        extu.b  r0,r0
        mov.l   M_disp2,r1
        mov.b   @r1,r2
        extu.b  r2,r2
        sub     r2,r0
        mov     r0,r10
        mov     r8,r0
        bsr     is_res
        nop
        bf      md_rows
        mov     r2,r11
md_rows: mov    #2,r12
md_loop: mov    r12,r0
        bsr     desc_of
        nop
        cmp/eq  r11,r12
        bf      md_stored
        extu.b  r9,r0                   ! our editor: the value being edited (clamped)
        mov.l   @(4,r3),r1
        add     #-1,r1
        cmp/hi  r1,r0
        bf      md_show
        mov     r1,r0
        bra     md_show
        nop
md_stored: mov.l @r3,r1                 ! otherwise the stored setting
        bsr     val_at
        nop
md_show: shll2  r0
        shll2   r0
        mov.l   @(12,r3),r4
        add     r0,r4
        mov     r12,r2                  ! screen position = row - top row, must be 0..5
        sub     r10,r2
        mov     #5,r1
        cmp/hi  r1,r2
        bt      md_next
        mov     r2,r6
        shll2   r6
        shll    r6
        shll2   r2
        shll2   r2
        add     r2,r6
        add     #{ROW0_Y},r6
        mov.l   M_x,r5
        mov.l   M_white,r7
        mov.l   M_text,r1
        jsr     @r1
        nop
md_next: add    #1,r12
        mov     r12,r0
        cmp/eq  #6,r0
        bf      md_loop
        mov.l   @r15+,r12
        mov.l   @r15+,r11
        mov.l   @r15+,r10
        mov.l   @r15+,r9
        mov.l   @r15+,r8
        lds.l   @r15+,pr
        rts
        nop

        .align  2
M_desc:    .long {A("desc")}
M_labt:    .long {A("labt")}
M_148:     .long 0x148
M_14a:     .long 0x14A
M_screen:  .long {SCREEN:#x}
M_cursor:  .long {CURSOR:#x}
M_cur2:    .long {STK_CUR2:#x}
M_disp2:   .long {STK_DISP2:#x}
M_depth:   .long {STK_DEPTH:#x}
M_ad31:    .long {AD31:#x}
M_enter:   .long {ENTER_FN:#x}
M_enc:     .long {ENC_FN:#x}
M_draw:    .long {DRAW_FN:#x}
M_count:   .long {COUNT_FN:#x}
M_label:   .long {LABEL_FN:#x}
M_nav:     .long {NAV_FN:#x}
M_scroll:  .long {SCROLL_FN:#x}
M_step:    .long {STEP_FN:#x}
M_text:    .long {TEXT_FN:#x}
M_x:       .long {VALUE_X:#x}
M_white:   .long {WHITE:#x}
code_end:
curve_ok:  .long 0
last_d:    .long 0xFFFFFFFF
last_c:    .long 0xFFFFFFFF
desc:
{chr(10).join(f"        .long   {WORK_BASE + b:#x}, {n}, {d}, {A('str' + str(k))}" for k, (_, b, n, d) in enumerate(ROWS))}
labt:      .long {', '.join(A('lab' + str(k)) for k in range(len(ROWS)))}
targets:   .long {', '.join(hex(t) for t in TARGETS)}
fsets:     .long {', '.join(f"{FH[r]:#x}, {FHB[r]:#x}" for r in range(3))}
vlist:     .long {', '.join(f"{a:#x}, {w:#x}" for a, w in VCHECK)}
{chr(10).join(f'lab{k}:      .asciz "{l}"' for k, (l, _, _, _) in enumerate(ROWS))}
        .align  2
gidx:
{asm_bytes(GIDX)}
        .align  4
{chr(10).join(f"str{k}:{chr(10)}{asm_bytes(STRB(s))}" for k, s in enumerate(STRS))}
levels:
{asm_bytes(LEVELS)}
        .align  4
curves:
{asm_bytes(CURVE_DATA)}
buf:
        .space  2048
        .align  4
blob_end:
"""


def assemble(src):
    with tempfile.TemporaryDirectory() as t:
        s, o, b = (pathlib.Path(t) / n for n in ("a.s", "a.o", "a.bin"))
        s.write_text(src)
        subprocess.run([sh_tool("sh-elf-as"), "-isa=sh2a", "-big", "-o", o, s], check=True)
        syms = subprocess.run([sh_tool("sh-elf-nm"), o], check=True, capture_output=True, text=True).stdout
        subprocess.run([sh_tool("sh-elf-objcopy"), "-O", "binary", "-j", ".text", o, b], check=True)
        return b.read_bytes(), {l.split()[2]: int(l.split()[0], 16) for l in syms.splitlines() if len(l.split()) == 3}


RES_CODE, RES_SYMS = assemble(RES_ASM)
assert len(RES_CODE) % 16 == 0 and RES_SYMS["blob_end"] == len(RES_CODE)
BLOB = RES_CODE
NWORDS = len(BLOB) // 4
SYM = lambda n: RES_RAM + RES_SYMS[n]
SLOTS = [(s, FILTER_FN, SYM("res_entry")) for s in FILTER_SLOTS] + [(s, o, SYM(n)) for s, o, n in MENU_SLOTS]

HOOK_ASM = f"""
        .text
hook:   sts.l   pr,@-r15
        mov.l   r8,@-r15
        mov.l   r9,@-r15
        mov.l   r10,@-r15
        mov.l   r11,@-r15
        add     #-8,r15
        mov.l   L_orig,r1
        jsr     @r1
        nop
        mov     r0,r10
        mov.l   L_mode,r1
        mov.b   @r1,r0
        tst     r0,r0
        bf      out
        mov.l   L_tries,r8
poll:   mov.l   L_done,r4
        mov     r15,r5
        mov     #4,r6{MASKED_CALL.format(fn="L_rd")}
        mov.l   @r15,r0
        and     #0xFF,r0
        cmp/eq  #1,r0
        bf      wait
        mov.l   L_chk,r4
        mov     r15,r5
        mov     #4,r6{MASKED_CALL.format(fn="L_rd")}
        mov.l   @r15,r0
        mov.l   L_lo,r1
        cmp/hs  r1,r0
        bf      wait
        mov.l   L_hi,r1
        cmp/hi  r1,r0
        bf      install
wait:   dt      r8
        bt      out
        mov     #10,r4
        mov.l   L_dly,r1
        jsr     @r1
        nop
        bra     poll
        nop
install: mov.l  L_src,r1
        mov.l   L_dstc,r2
        mov.l   L_dstu,r3
        mov.l   L_nw,r8
cp:     mov.l   @r1+,r0
        mov.l   r0,@r2
        mov.l   r0,@r3
        add     #4,r2
        add     #4,r3
        dt      r8
        bf      cp
        mov     #1,r4
        mov.l   L_apply,r1
        jsr     @r1
        nop
        mova    slots,r0                ! (slot, Denon's value, new value): rewrite only if the slot still holds Denon's value
        mov     r0,r9
        mov     #{len(SLOTS)},r8
ps:     mov.l   @r9+,r3
        mov.l   @r9+,r11
        mov.l   @r9+,r2
        mov.l   @r3,r0
        cmp/eq  r11,r0
        bf      nx
        mov.l   r2,@r3
nx:     dt      r8
        bf      ps
        mov     #5,r4
        mov     #1,r5
        mov.l   L_setflg,r1
        jsr     @r1
        nop
out:    mov     r10,r0
        add     #8,r15
        mov.l   @r15+,r11
        mov.l   @r15+,r10
        mov.l   @r15+,r9
        mov.l   @r15+,r8
        lds.l   @r15+,pr
        rts
        nop
        .align  2
L_orig:   .long {ORIG_FN:#x}
L_mode:   .long {MODE_BYTE:#x}
L_tries:  .long {TRIES}
L_done:   .long {DSP_DONE:#x}
L_chk:    .long {DSP_CHECK:#x}
L_lo:     .long {CHECK_LO:#x}
L_hi:     .long {CHECK_HI:#x}
L_rd:     .long {UHPI_READ:#x}
L_dly:    .long {DELAY:#x}
L_setflg: .long {SET_FLG:#x}
L_src:    .long {BLOB_FLASH:#x}
L_dstc:   .long {RES_RAM:#x}
L_dstu:   .long {RES_RAM + 0x20000000:#x}
L_nw:     .long {NWORDS}
L_apply:  .long {SYM("apply"):#x}
slots:    .long {', '.join(f"{s:#x}, {o:#x}, {n:#x}" for s, o, n in SLOTS)}
"""
HOOK, _ = assemble(HOOK_ASM)


def delay_slots_are_nops(code, base):
    """static check: every delayed branch (bra/bsr/jmp/jsr/rts/braf/bsrf, bt/s, bf/s) is followed by nop"""
    words = [struct.unpack(">H", code[i:i + 2])[0] for i in range(0, len(code) - 1, 2)]
    end = len(words)
    for k, hw in enumerate(words[:end - 1]):
        delayed = (hw >> 12 in (0xA, 0xB) or (hw >> 12 == 4 and hw & 0xFF in (0x0B, 0x2B)) or hw == 0x000B
                   or (hw >> 12 == 0 and hw & 0xFF in (0x23, 0x03)) or hw >> 8 in (0x8D, 0x8F))
        if delayed and words[k + 1] != 0x0009:
            return f"{base + 2 * k:#x}: {hw:04x} followed by {words[k + 1]:04x}"
    return None


for code, base, n in ((RES_CODE[:RES_SYMS["R_orig"]], RES_RAM, "resident"), (RES_CODE[RES_SYMS["desc_of"]:RES_SYMS["M_desc"]], RES_RAM + RES_SYMS["desc_of"], "menu"), (HOOK[:HOOK.index(bytes.fromhex("000b0009"))
                                                                                        + 4], HOOK_FLASH, "hook")):
    assert delay_slots_are_nops(code, base) is None, (n, delay_slots_are_nops(code, base))
assert HOOK_FLASH + len(HOOK) <= BLOB_FLASH and BLOB_FLASH + len(BLOB) <= 0x100000
assert set(ORIG[HOOK_FLASH:BLOB_FLASH + len(BLOB)]) == {0xFF}
assert STAGE_LO <= RES_RAM and RES_RAM + len(BLOB) <= STAGE_HI
assert be32(ORIG, fl(SLOT)) == ORIG_FN and all(be32(ORIG, fl(s)) == o for s, o, _ in SLOTS)


# ------------------------------------------------------------- SH-2A emulator (instructions used above)
class Stop(Exception):
    pass


S32 = lambda v: v - (1 << 32) if v & 0x80000000 else v
SEXT8 = lambda v: (v - 0x100 if v & 0x80 else v) & 0xFFFFFFFF


class Emu:
    def __init__(s, mem, dsp, stubs):
        s.m = dict(mem); s.r = [0] * 16; s.pr = s.sr = s.t = 0
        s.dsp = dsp; s.calls = []; s.dspmem = {}; s.n = 0; s.stubs = stubs

    def rd(s, a, n):
        return int.from_bytes(bytes(s.m.get(a + k, 0) for k in range(n)), "big")

    def wr(s, a, v, n):
        for k in range(n):
            s.m[a + k] = (v >> (8 * (n - 1 - k))) & 0xFF

    def cstr(s, a):
        out = b""
        while s.m.get(a, 0):
            out += bytes([s.m[a]]); a += 1
        return out.decode()

    def clob(s):
        for k in range(1, 8):
            s.r[k] = 0xDEAD0000 + k

    def run(s, pc, stop_at, limit=3000000):
        for _ in range(limit):
            if pc in stop_at:
                return pc
            hw = s.rd(pc, 2); n, m = (hw >> 8) & 0xF, (hw >> 4) & 0xF
            i8 = hw & 0xFF; si = i8 - 0x100 if i8 & 0x80 else i8
            nxt, jump = pc + 2, None
            if hw == 0x0009: pass
            elif hw == 0x0018: s.t = 1                                                        # sett
            elif hw == 0x0008: s.t = 0                                                        # clrt
            elif hw >> 12 == 2 and hw & 0xF == 0xB: s.r[n] |= s.r[m]                         # or
            elif hw >> 12 == 0 and hw & 0xF == 0xC: s.r[n] = SEXT8(s.rd(s.r[0] + s.r[m], 1))  # mov.b @(r0,rm),rn
            elif hw >> 12 == 5: s.r[n] = s.rd(s.r[m] + 4 * (hw & 0xF), 4)                   # mov.l @(disp,rm),rn
            elif hw >> 12 == 0 and hw & 0xF == 0xE: s.r[n] = s.rd(s.r[0] + s.r[m], 4)          # mov.l @(r0,rm),rn
            elif hw == 0x000B: jump = s.pr
            elif hw >> 12 == 2 and hw & 0xF == 6: s.r[n] -= 4; s.wr(s.r[n], s.r[m], 4)
            elif hw >> 12 == 2 and hw & 0xF == 2: s.wr(s.r[n], s.r[m], 4)
            elif hw >> 12 == 2 and hw & 0xF == 0: s.wr(s.r[n], s.r[m] & 0xFF, 1)                 # mov.b rm,@rn
            elif hw >> 12 == 6 and hw & 0xF == 6:
                v = s.rd(s.r[m], 4)
                if n != m: s.r[m] += 4
                s.r[n] = v
            elif hw >> 12 == 6 and hw & 0xF == 2: s.r[n] = s.rd(s.r[m], 4)
            elif hw >> 12 == 6 and hw & 0xF == 0: s.r[n] = SEXT8(s.rd(s.r[m], 1))
            elif hw >> 12 == 6 and hw & 0xF == 0xC: s.r[n] = s.r[m] & 0xFF                 # extu.b
            elif hw >> 12 == 6 and hw & 0xF == 0xD: s.r[n] = s.r[m] & 0xFFFF               # extu.w
            elif hw >> 12 == 6 and hw & 0xF == 3: s.r[n] = s.r[m]
            elif hw >> 12 == 3 and hw & 0xF == 0xC: s.r[n] = (s.r[n] + s.r[m]) & 0xFFFFFFFF  # add
            elif hw >> 12 == 3 and hw & 0xF == 0x8: s.r[n] = (s.r[n] - s.r[m]) & 0xFFFFFFFF  # sub
            elif hw >> 12 == 4 and hw & 0xFF == 0x22: s.r[n] -= 4; s.wr(s.r[n], s.pr, 4)
            elif hw >> 12 == 4 and hw & 0xFF == 0x26: s.pr = s.rd(s.r[n], 4); s.r[n] += 4
            elif hw >> 12 == 4 and hw & 0xFF == 0x18: s.r[n] = (s.r[n] << 8) & 0xFFFFFFFF   # shll8
            elif hw >> 12 == 4 and hw & 0xFF == 0x08: s.r[n] = (s.r[n] << 2) & 0xFFFFFFFF   # shll2
            elif hw >> 12 == 4 and hw & 0xFF == 0x00:
                s.t = s.r[n] >> 31; s.r[n] = (s.r[n] << 1) & 0xFFFFFFFF                         # shll
            elif hw >> 12 == 0 and hw & 0xFF == 0x02: s.r[n] = s.sr | s.t
            elif hw >> 12 == 4 and hw & 0xFF == 0x0E: s.sr = s.r[n] & ~1; s.t = s.r[n] & 1
            elif hw >> 8 == 0xCB: s.r[0] |= i8
            elif hw >> 8 == 0xC9: s.r[0] &= i8
            elif hw >> 8 == 0x88: s.t = int(S32(s.r[0]) == si)
            elif hw >> 12 == 0xD: s.r[n] = s.rd(((pc + 4) & ~3) + i8 * 4, 4)
            elif hw >> 8 == 0xC7: s.r[0] = ((pc + 4) & ~3) + i8 * 4
            elif hw >> 12 == 4 and hw & 0xFF == 0x0B: jump = ("call", s.r[n])
            elif hw >> 12 == 4 and hw & 0xFF == 0x2B: jump = s.r[n]
            elif hw >> 12 == 2 and hw & 0xF == 8: s.t = int((s.r[n] & s.r[m]) == 0)
            elif hw >> 12 == 3 and hw & 0xF == 0: s.t = int(s.r[n] == s.r[m])
            elif hw >> 12 == 3 and hw & 0xF == 2: s.t = int(s.r[n] >= s.r[m])
            elif hw >> 12 == 3 and hw & 0xF == 6: s.t = int(s.r[n] > s.r[m])
            elif hw >> 12 == 7: s.r[n] = (s.r[n] + si) & 0xFFFFFFFF
            elif hw >> 12 == 0xE: s.r[n] = si & 0xFFFFFFFF
            elif hw >> 12 == 4 and hw & 0xFF == 0x10: s.r[n] = (s.r[n] - 1) & 0xFFFFFFFF; s.t = int(s.r[n] == 0)
            elif hw >> 8 == 0x8B:
                if not s.t: nxt = pc + 4 + 2 * si
            elif hw >> 8 == 0x89:
                if s.t: nxt = pc + 4 + 2 * si
            elif hw >> 12 in (0xA, 0xB):
                d = hw & 0xFFF; d -= 0x1000 if d & 0x800 else 0; tgt = pc + 4 + 2 * d
                jump = ("call", tgt) if hw >> 12 == 0xB else tgt
            else:
                raise Stop(("unimplemented", hex(pc), hex(hw)))
            if jump is not None:
                if s.rd(pc + 2, 2) != 0x0009:
                    raise Stop(("delay slot must be nop", hex(pc)))
                call = isinstance(jump, tuple)
                tgt = jump[1] if call else jump
                if call:
                    s.pr = pc + 4
                if tgt in s.stubs:                       # Denon function: run its model, return to pr
                    s.stubs[tgt](s); pc = s.pr
                elif tgt in stop_at and not call:
                    return tgt
                else:
                    pc = tgt
                continue
            pc = nxt
        raise Stop("instruction limit")


# ------------------------------------------------------------- models of Denon's functions (from the decoded code)
def boot_stubs():
    def orig(e):
        e.calls.append(("orig", e.r[4])); e.clob(); e.r[0] = 0x55AA

    def rd(e):
        assert e.r[6] == 4 and (e.sr >> 4) & 0xF == 0xF
        e.calls.append(("rd", e.r[4])); v = e.dspmem.get(e.r[4]); e.wr(e.r[5], e.dsp(e.r[4], e.n) if v is None else v, 4); e.n += 1; e.clob()

    def wr(e):
        assert (e.sr >> 4) & 0xF == 0xF
        dst, src, n = e.r[4], e.r[5], e.r[6]; e.calls.append(("wr", dst, src, n))
        for k in range(0, n, 4):
            e.dspmem[dst + k] = e.rd(src + k, 4)
        e.clob()

    def dly(e):
        assert (e.sr >> 4) & 0xF == 0; e.calls.append(("dly", e.r[4])); e.clob()

    def setflg(e):
        e.calls.append(("set_flg", e.r[4], e.r[5])); e.clob()
    return {ORIG_FN: orig, UHPI_READ: rd, UHPI_WRITE: wr, DELAY: dly, SET_FLG: setflg}


STK = 0x0C51B238


def nav(e, pop):                                 # 0x0C022174
    d = e.m[STK + 12]
    if pop:
        d -= 1; e.m[STK + 12] = d; e.m[CURSOR] = e.m[STK + d]; e.m[CURSOR + 1] = e.m[STK + 6 + d]
    else:
        e.m[STK + d] = e.m[CURSOR]; e.m[STK + 6 + d] = e.m[CURSOR + 1]; e.m[STK + 12] = d + 1; e.m[CURSOR + 1] = 0


def menu_stubs():
    def denon_enter(e):                           # 0x0C0224C8, cases 0x148 / 0x149 / 0x14A only
        e.calls.append(("ENTER", e.rd(SCREEN, 4), e.m[CURSOR]))
        sc, c = e.rd(SCREEN, 4), e.m[CURSOR]
        if sc == 0x148:
            assert c in (0, 1), "Denon's ENTER on 0x148 with cursor 2 is undefined"
            nav(e, False); e.wr(SCREEN, 0x149 + c, 4); e.m[CURSOR] = e.m[0x0C51AD30 + c]
        elif sc in (0x149, 0x14A):
            e.m[0x0C51AD30 + sc - 0x149] = e.m[CURSOR]; nav(e, True); e.wr(SCREEN, 0x148, 4)
        e.clob(); e.r[0] = 0x1234

    def denon_enc(e):
        e.calls.append(("ENC", e.r[4], e.r[5])); e.clob()

    def denon_draw(e):
        e.calls.append(("DRAW", e.r[4], e.r[5], e.r[6])); e.clob()

    def denon_count(e):
        e.calls.append(("COUNT", e.r[4])); e.r[0] = {0x148: 2, 0x149: 3, 0x14A: 3, 0x14B: 5}.get(e.r[4], 0); e.r[1] = 0xBAD

    def denon_label(e):
        e.calls.append(("LABEL", e.r[4])); i = e.r[4]
        e.r[0] = e.rd(0x0C0C2E3C + 4 * (i if 0 <= S32(i) <= 1 else 0), 4)

    def navfn(e):
        nav(e, e.r[4] & 0xFF); e.calls.append(("NAV", e.r[4] & 0xFF)); e.clob()

    def scroll(e):
        e.calls.append(("SCROLL", e.r[4], e.r[5], e.r[6], e.r[7])); e.clob()

    def step(e):                                  # 0x0C021CA4 in mode 1 (clamp), args 5/6 on the stack
        cnt, mode = e.rd(e.r[15], 4), e.rd(e.r[15] + 4, 4)
        d, st, cur, lo = e.r[4] & 0xFF, S32(e.r[5]), e.r[6], S32(e.r[7])
        assert mode == 1 and cur < 256
        e.calls.append(("STEP", d, st, cur, lo, cnt))
        e.clob(); e.r[0] = min(cur + st, cnt - 1) if d == 1 else max(cur - st, lo)

    def text(e):
        e.calls.append(("TEXT", e.cstr(e.r[4]), e.r[5], e.r[6], e.r[7])); e.clob()
    return {ENTER_FN: denon_enter, ENC_FN: denon_enc, DRAW_FN: denon_draw, COUNT_FN: denon_count, LABEL_FN: denon_label,
            NAV_FN: navfn, SCROLL_FN: scroll, STEP_FN: step, TEXT_FN: text}


def image():
    img = bytearray(ORIG)
    img[HOOK_FLASH:HOOK_FLASH + len(HOOK)] = HOOK
    img[BLOB_FLASH:BLOB_FLASH + len(BLOB)] = BLOB
    img[fl(SLOT):fl(SLOT) + 4] = struct.pack(">I", HOOK_FLASH)
    return img


def mem_of(img):
    mem = {0x0C000000 + a - 0x10000: img[a] for a in range(0x10000, 0xD3908)}
    mem.update({a: img[a] for a in range(0xD3908, 0x100000)})
    return mem




def words_at(e, a, n):
    return [e.dspmem.get(a + 4 * k) for k in range(n)]


def damp_written(e, a, b, g):
    exp = [fbits(x) for x in table(a, b, g)]
    for t in TARGETS:
        assert words_at(e, t, 512) == exp, (hex(t), a, b, g)


def curve_words(c, r):
    o = c * 1536 + r * 512
    return list(struct.unpack(">128I", CURVE_DATA[o:o + 512]))


def curve_written(e, c, rate):
    for r in range(3):
        assert words_at(e, FH[r], 128) == curve_words(c, r) and words_at(e, FHB[r], 128) == curve_words(c, r), (c, r)
    if rate is not None:
        assert words_at(e, W_FH, 128) == curve_words(c, rate) and words_at(e, W_FHB, 128) == curve_words(c, rate)


RET = 0x0C123456


def call(e, entry, regs, stop=(RET,)):
    """call a resident routine like Denon's code does; check the SH calling convention afterwards"""
    e.calls.clear(); e.r = [0x22220000 + k for k in range(16)]
    for k, v in regs.items():
        e.r[k] = v
    e.r[15] = 0x0C700000; e.pr = RET; before = list(e.r)
    pc = e.run(entry, set(stop))
    assert pc == RET and e.r[8:16] == before[8:16] and e.pr == RET and e.sr == 0, (hex(entry), e.r, hex(e.pr))
    return e.calls


def dsp_model(rate=1, skew=0):
    """the STOCK DSP after its own boot: Denon curve in fH/fH(B) for 3 rates, distinct fL[J] per rate, working copy = rate"""
    m = {}
    for r in range(3):
        for k, w in enumerate(curve_words(0, r)):                      # stock DSP: Denon's own curve
            m[FH[r] + 4 * k] = w; m[FHB[r] + 4 * k] = w
        m[S_FL + r * 0x1000] = 0x3E100000 + 77 * r
    m[FH[2] + 4 * (184 - 128)] += skew
    if rate is not None:
        m[W_FL] = m[S_FL + rate * 0x1000]
    else:
        m[W_FL] = 0x12345678
    return m


def boot(img, stored, dsp, mode=0):
    ready = lambda a, k: {DSP_DONE: 1, DSP_CHECK: 0x3EB4AF90}[a]
    mem = mem_of(img); mem[MODE_BYTE] = mode
    for byte, v in stored.items():
        mem[APPLIED_BASE + byte] = v
    e = Emu(mem, ready, boot_stubs() | menu_stubs()); e.dspmem = dict(dsp)
    e.r = [0x11110000 + k for k in range(16)]; e.r[4] = 1; e.r[15] = 0x0C700000; e.pr = 0x0C006B9A
    before = list(e.r)
    e.run(HOOK_FLASH, {0x0C006B9A})
    assert e.r[8:16] == before[8:16] and e.pr == 0x0C006B9A and e.sr == 0
    return e


def knob(e):
    e.calls.clear(); e.r = [0x22220000 + k for k in range(16)]; e.r[15] = 0x0C700000; e.pr = RET; b = list(e.r)
    pc = e.run(SYM("res_entry"), {FILTER_FN})
    assert pc == FILTER_FN and e.r[4:16] == b[4:16] and e.pr == RET and e.sr == 0
    return [c[1] for c in e.calls if c[0] == "wr"]


def check():
    img = image()
    for mode in (2, 3, 4):
        e = boot(img, {}, dsp_model(), mode)
        assert all(e.rd(s, 4) == o for s, o, _ in SLOTS) and [c for c in e.calls if c[0] == "wr"] == []
        print(f"boot, mode {mode}: OK (nothing installed, all {len(SLOTS)} slots untouched, nothing written)")
    # normal boot: LPF 16, everything else unset -> defaults; DSP at 48 kHz
    e = boot(img, {92: 17}, dsp_model(rate=1))
    assert all(e.rd(s, 4) == n for s, _, n in SLOTS) and e.calls[-1] == ("set_flg", 5, 1)
    assert bytes(e.m[RES_RAM + 0x20000000 + k] for k in range(len(BLOB))) == BLOB
    assert e.rd(SYM("curve_ok"), 4) == 1
    damp_written(e, 16, HDEF, 184); curve_written(e, CDEF, 1)
    print("boot, mode 0: OK (DSP check passed; damping LPF 32 % / HPF 22 % / gate 400 Hz; range 20-600 rewritten, 48 kHz copy)")
    assert knob(e) == []
    print("runtime, nothing changed: OK (no DSP write)")
    e.m[APPLIED_BASE + 95] = 1                                         # range -> Stock: gate 400 Hz moves to the stock knob step
    e.dspmem = dsp_model(rate=2) | {k: v for k, v in e.dspmem.items() if k not in dsp_model(rate=2)}
    w = knob(e); g = gate_index(0, GDEF)
    assert w == TARGETS + [FH[0], FHB[0], FH[1], FHB[1], FH[2], FHB[2], W_FH, W_FHB], [hex(x) for x in w]
    damp_written(e, 16, HDEF, g); curve_written(e, 0, 2)
    print(f"runtime, range -> Stock at 96 kHz: OK (gate 400 Hz -> knob {g}; damping + 6 SDRAM + 2 working writes)")
    e.m[APPLIED_BASE + 94] = 41; e.m[APPLIED_BASE + 92] = 1
    w = knob(e); assert w == TARGETS; damp_written(e, 0, 40, g)
    print("runtime, LPF 0 % / HPF 80 %: OK (damping only, range untouched)")
    e.m[APPLIED_BASE + 93] = 1
    assert knob(e) == TARGETS; damp_written(e, 0, 40, GATE_OFF)
    e.m[APPLIED_BASE + 93] = 1 + len(GATE_HZ); knob(e); damp_written(e, 0, 40, gate_index(0, len(GATE_HZ)))
    print("runtime, gate Off / 1000 Hz: OK")
    e.dspmem[W_FL] = 0x12345678; e.m[APPLIED_BASE + 95] = 1 + len(MIDS)
    w = knob(e); assert W_FH not in w and FH[2] in w; curve_written(e, len(MIDS), None)
    print("runtime, rate not identifiable: OK (SDRAM sets written, working copy left alone)")
    for st, lv in ((0, LDEF), (52, LDEF), (200, LDEF), (51, 50)):
        assert value(st, NMAX + 1, LDEF) == lv
    # DSP check
    e = boot(img, {}, dsp_model(rate=0, skew=5))
    assert e.rd(SYM("curve_ok"), 4) == 1
    e = boot(img, {95: 3}, dsp_model(rate=0, skew=100))
    assert e.rd(SYM("curve_ok"), 4) == 0 and all(c[1] in TARGETS for c in e.calls if c[0] == "wr")
    damp_written(e, LDEF, HDEF, gate_index(2, GDEF))
    e.m[APPLIED_BASE + 95] = 5; w = knob(e)
    assert w == TARGETS and e.dspmem[FH[0]] == curve_words(0, 0)[0]   # untouched: still Denon stock
    print("DSP check (stock curve): OK (5 ulp accepted; 100 ulp off -> range never written, resonance/gate still work)")

    # ---- menu
    e = boot(img, {92: 17}, dsp_model())
    sc = lambda: e.rd(SCREEN, 4)
    e.wr(SCREEN, 0x148, 4); e.m[CURSOR] = 0; e.m[CURSOR + 1] = 0; e.m[STK + 12] = 2
    e.m[STK + 0], e.m[STK + 1], e.m[STK + 6], e.m[STK + 7] = 0, 10, 0, 5
    for b, v in ((90, 1), (91, 0), (92, 17), (93, 0), (94, 0), (95, 0)):
        e.m[WORK_BASE + b] = v
    enc = lambda d, st: call(e, SYM("m_enc"), {4: d, 5: st})
    enter = lambda: call(e, SYM("m_enter"), {})
    draw = lambda: call(e, SYM("m_draw"), {4: sc(), 5: e.m[CURSOR], 6: e.m[CURSOR + 1]})
    texts = lambda c: [x[1].strip() for x in c if x[0] == "TEXT"]
    ys = lambda c: [x[3] for x in c if x[0] == "TEXT"]
    assert call(e, SYM("m_count"), {4: 0x148}) == [] and e.r[0] == 6
    assert call(e, SYM("m_count"), {4: 0x14B}) == [("COUNT", 0x14B)] and e.r[0] == 5
    for i, (l, _, _, _) in enumerate(ROWS):
        assert call(e, SYM("m_label"), {4: i + 2}) == [] and e.cstr(e.r[0]) == l
    for i, s in ((0, "OFF Filter"), (1, "ON Filter ")):
        assert call(e, SYM("m_label"), {4: i}) == [("LABEL", i)] and e.cstr(e.r[0]) == s
    print("count/label: OK (6 rows: OFF Filter, ON Filter, " + ", ".join(r[0] for r in ROWS) + ")")
    for k in range(5):
        assert ("STEP", 1, 1, k, 0, 6) in enc(1, 1); e.m[CURSOR + 1] = e.m[CURSOR]
    enc(1, 5); assert e.m[CURSOR] == 5
    c = draw(); assert texts(c) == ["32%", "22%", "400 Hz", "20-600"] and ys(c) == [ROW0_Y + k * ROW_DY for k in (2, 3, 4, 5)], c
    print("list: OK (cursor reaches row 5 and stops; values 32 % / 22 % / 400 Hz / 20-600 on rows 2..5)")

    def edit(row, moves, expect_text, expect_stored):
        e.m[CURSOR] = row; e.m[CURSOR + 1] = row
        assert enter() == [("NAV", 0)] and sc() == 0x14A and e.m[STK + 2] == row
        for d, st in moves:
            enc(d, st)
        t = texts(draw()); assert t[row - 2] == expect_text, (row, t)
        assert enter() == [("ENTER", 0x14A, 0)] and sc() == 0x148 and e.m[CURSOR] == row
        assert e.m[WORK_BASE + ROWS[row - 2][1]] == expect_stored, (row, e.m[WORK_BASE + ROWS[row - 2][1]])
    edit(2, [(1, 99)], "100%", 51)
    edit(3, [(0, 99), (1, 3)], "6%", 4)
    edit(4, [(0, 99)], "Off", 1)
    edit(4, [(1, 99)], "1000 Hz", 1 + len(GATE_HZ))
    edit(5, [(0, 99)], "Stock", 1)
    edit(5, [(1, 99)], "20-2500", len(CURVES))
    edit(5, [(0, len(CURVES) - 1 - CDEF)], "20-600", CDEF + 1)
    assert e.m[WORK_BASE + 91] == 0 and e.m[WORK_BASE + 90] == 1
    c = draw(); assert texts(c) == ["100%", "6%", "1000 Hz", "20-600"]
    print("editors: OK (each row opens at its value, clamps at both ends, confirm stores value+1, OFF/ON Filter untouched)")
    e.m[CURSOR] = 3; e.m[CURSOR + 1] = 3; enter(); enc(1, 2); nav(e, True); e.wr(SCREEN, 0x148, 4)
    assert e.m[WORK_BASE + 94] == 4 and e.m[CURSOR] == 3
    e.m[CURSOR] = 1; e.m[CURSOR + 1] = 1
    c = enter(); assert c[0] == ("ENTER", 0x148, 1) and sc() == 0x14A and e.m[STK + 2] == 1
    assert enc(1, 1) == [("ENC", 1, 1)]
    assert texts(draw()) == ["100%", "6%", "1000 Hz", "20-600"]
    e.m[CURSOR] = 2; c = enter(); assert c[0] == ("ENTER", 0x14A, 2) and e.m[AD31] == 2
    print("back + ON Filter row: OK (Denon's paths)")
    e.wr(SCREEN, 0x105, 4)
    assert enc(0, 3) == [("ENC", 0, 3)] and enter()[0] == ("ENTER", 0x105, e.m[CURSOR])
    e.wr(SCREEN, 0x14A, 4); e.m[STK + 2] = 3; e.m[STK + 12] = 4
    assert enc(1, 1) == [("ENC", 1, 1)]
    e.m[STK + 12] = 3; e.m[STK + 2] = 6
    assert enc(1, 1) == [("ENC", 1, 1)]
    print("other screens: OK (Denon's encoder/ENTER with the original registers)")
    return img


if __name__ == "__main__":
    img = check()
    print(f"hook {len(HOOK)} B @ flash {HOOK_FLASH:#x}; blob {len(BLOB)} B @ {BLOB_FLASH:#x} -> RAM {RES_RAM:#x}..{RES_RAM + len(BLOB):#x}")
    spans = [(HOOK_FLASH, img[HOOK_FLASH:HOOK_FLASH + len(HOOK)]), (BLOB_FLASH, img[BLOB_FLASH:BLOB_FLASH + len(BLOB)]),
             (fl(SLOT), img[fl(SLOT):fl(SLOT) + 4])]
    reasons = [
        "Start-up hook (normal mode only): waits for the DSP's own initialisation, copies the resident program to RAM, redirects "
        "nine pointers and starts the filter. Runs in place of the 4-byte pointer patched below; in Version UP Mode and the service "
        "modes it does nothing.",
        "The resident program: filter menu (4 rows), settings storage logic, the damping-table builder, the HPF range tables and "
        "the DSP writes. Unused flash after the end of the application; copied to RAM at start-up.",
        "Pointer to the start-up hook (Denon's code calls this slot after the DSP firmware has been loaded; the original pointer "
        "was 0x0C000C04, which the hook calls first).",
    ]
    lines = ["name: filter-menu", "description: >",
             "  Filter edition. Utility > Parameters Filter Setting gets four more rows: LPF Reso and HPF Reso (0..100 %), HPF Gate",
             "  (Off, 100..1000 Hz) and HPF Range (Stock, 20-150 .. 20-2500). Stored in settings bytes 92..95 (unused by Denon,",
             "  saved with the settings and in Preset Export/Import). A small program in unused flash is copied to RAM at start-up",
             "  (normal mode only) and writes the damping table and the HPF cutoff coefficients into the DSP when a setting changes.",
             "  Nothing in Denon's DSP package is patched. Generated and emulator-checked by tools/gen_filter_menu.py.",
             "  Details: docs/filter-edition.md.",
             "status: experimental, tested by one owner on one unit", "patches:"]
    for (off, new), reason in zip(spans, reasons):
        lines += [f"  - offset: {off:#x}", f'    expect: "{ORIG[off:off + len(new)].hex()}"', f'    new: "{bytes(new).hex()}"',
                  f"    reason: {json.dumps(reason)}"]
    (ROOT / "patches/filter/101-filter-menu.yaml").write_text("\n".join(lines) + "\n")
    print("wrote patches/filter/101-filter-menu.yaml")
