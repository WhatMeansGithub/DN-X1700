"""Per-model facts for the Denon DN-X1700 / DN-X1600 firmware images.

All three mixers (DN-X1700, DN-X1600, DN-X600) are built from one code base. The X1700 and X1600 share the USB stack and
the updater; only addresses differ. Every value below was checked against the image (see docs/x1600.md for how):
  [C] = confirmed in code/data. The X1700 values are also confirmed on the real unit (patches 001 + 002 flashed).

Address mapping (both models, boot code literals): flash offset F in APP0..app_end lives at RAM 0x0C000000 + F - APP0.
"""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
APP0 = 0x10000
RAM0 = 0x0C000000


def ram(f):
    return RAM0 + f - APP0


def flash(r):
    return r - RAM0 + APP0


MODELS = {
    "X1700": dict(
        image=ROOT / "firmware/original/X1700FW.BIN",
        sha256="24ef3d26a116118d27787c337a8a89d67cb6b314cca38f6f22d906b3785556f3",
        size=0x400000,                # updater reads 4 x 1 MiB, LE word sum == 0xFFFFFFFF [C]
        fw_name="X1700FW.BIN",
        patch_dir=ROOT / "patches",   # X1700 patches stay at the top level (web tool + patcher program read them)
        app_end=0xD3908,              # first 0xFF padding after the app [C]
        usb_pid=0x500A,
        cfg_desc=0xD36A0,             # flash offset of the 385-byte configuration descriptor [C]
        # patch 002 anchors (RAM addresses) [C]
        get_rate_idx=0x0C021C3A,      # leaf getter: r0 = 0 (44.1k), 1 (48k), else 96k
        reply=0x0C03904A,             # reply(r4 = data pointer, r5 = length)
        site_rate=0x0C038ECA,         # GET_CUR sample rate: mov.l @(lit),r6 ; mov.l @r6,r2 ; bra tail ; mov #4,r1
        site_range=0x0C038F14,        # GET_RANGE clock 6: mov #26,r1 ; mov.l @(lit),r2 ; (tail)
        site_sel=0x0C038F4C,          # GET_CUR clock selector: mov.l @(lit),r6 ; movu.b @(0,r6),r4 ; ...
        back_tail=0x0C038F18,         # function A tail: stores r2, clamps r1 to wLength, sends
        back_sel=0x0C038F6E,          # selector epilogue: lds.l @r15+,pr ; rtv/n r2
        range_tbl=0x0C0C3485,         # firmware's 44.1k range block (wNumSubRanges=1, 44100, 44100, 0)
        cave=0x0C0BEA48,              # lowercase serial-command table (service UART parser only), 155 bytes + NUL
        cave_len=155,
        cmd_ptrs=0x0C0C17AC,          # pointer array: uppercase table, lowercase table, next table (one reference each)
    ),
    "X1600": dict(
        image=ROOT / "firmware/original/X1600FW.BIN",
        sha256="befd35c43411b39e796656f12aa850badee49d677522ce5f7a5db504e346c120",
        size=0x200000,                # updater (RAM 0x0C039598) reads 2 x 1 MiB, same LE word-sum rule (cmp/eq #-1) [C]
        fw_name="X1600FW.BIN",
        patch_dir=ROOT / "patches/x1600",
        app_end=0xCB350,
        usb_pid=0x500E,
        cfg_desc=0xCB0E8,             # byte-identical to the X1700's descriptor [C]
        get_rate_idx=0x0C01ED0E,      # same code + same settings offsets (+0x2C/+0xAA); loaded by the USB code at 0x0C031DCA [C]
        reply=0x0C0321FE,
        site_rate=0x0C03207E,         # USB class handler identical to the X1700's, shifted by -0x6E4C [C]
        site_range=0x0C0320C8,
        site_sel=0x0C032100,
        back_tail=0x0C0320CC,
        back_sel=0x0C032122,
        range_tbl=0x0C0BAF69,
        cave=0x0C0B926C,              # lowercase table, same 155 bytes + NUL ("cr" in place of the X1700's "dp") [C]
        cave_len=155,
        cmd_ptrs=0x0C0BAE60,
    ),
}


def get(name):
    try:
        m = dict(MODELS[name.upper()])
    except KeyError:
        raise SystemExit(f"unknown model {name!r}; known: {', '.join(MODELS)}")
    m["name"] = name.upper()
    return m
