# Initial findings (2026-11-01)
Sources: X1700FW.BIN v2.000 (sha256 24ef3d26…56f3), service manual ver-2, addendum IM_007.
Legend: [C] = confirmed by data, [H] = hypothesis to verify.

## Hardware (from service manual)
| Ref | Part | Role |
|---|---|---|
| IC104 | **Renesas SH7263** (R5S72630P200FP), SH-2A + FPU, 200 MHz, ROM-less | Main CPU: UI, LCD, MIDI, **USB 2.0 HS device/host on-chip**, loads DSP and FPGA |
| IC103 | **EN29LV320AB-70TCP** (4 MB NOR, TSOP48, bottom boot), "X1700 ROM ASSY" | Holds the whole firmware; X1700FW.BIN is a 4 MB raw image of it [C] |
| IC107 | W9864G2GH-6 SDRAM | SH main RAM (CS3, 0x0C000000) |
| IC501 | **TI "DA710"** (D710E001ZDH300, 256-BGA, 300 MHz), C672x-class floating point DSP | All audio DSP and effects. EMIF to IC502/503 SDRAM, McASP audio I/O, **UHPI host port to SH** |
| IC502/503 | M12L128168A SDRAM | DSP memory (delay/loop buffers) |
| IC301 | **Xilinx Spartan-3A XC3S400A** | Audio routing/glue (DIR, SRC, ADC/DAC, SSI to SH). Configured by SH (PE3 = CCLK, slave serial) [C manual] |
| — | PCM1804 ADC, PCM1796/AK4399 DAC, DIR9001, AK4125 SRC, AK4104 S/PDIF tx | Converters |
| IC702 | TS3USB221 | USB A/B switch |

SH7263 pins of interest: **SCIF2 PE6/RXD2 (pin 68), PE7/TXD2 (pin 69) = "UART (Serial Control)"**, the
debug-monitor candidate. JTAG/H-UDI pins 172–178, /ASEMD pin 61. USB 48 MHz crystal on pins 93/94.
SSI2/SSI3 (pins 124–136) = USB audio I2S to/from FPGA. CS6 (PE11) = UHPI select.

## Flash layout of X1700FW.BIN [C unless marked]
| Flash offset | Content |
|---|---|
| 0x000000–0x006097 | Boot block: SH vector table (reset PC 0x100, SP 0xFFF90000, big-endian), init, app copy loop. Load table around 0x5c0–0x630 |
| 0x010000–0x0d3907 | SH-2A main application, **copied to SDRAM at 0x0C000000** (flash 0x10000 ↔ RAM 0x0C000000; verified by the pointer 0x0C0C36A0 → USB config at flash 0xd36a0). Strings ~0xcd000–0xd3900. μITRON-style RTOS (task/mailbox names), Renesas USB stack "USBD.04.06.00", serial monitor "Hello !!! (DN-X1700 monitor)" |
| 0x100000–0x11ffc8 | **DSP image**, little-endian C67x (MVK/MVKH patterns visible). Load table gives size 0x1ffc8. Header word 0x000041a4 [H: boot-table format] |
| 0x130000–~0x145f39 | **FPGA bitstream** [H], table size 0x15f39. Bytes at 0x130020 look like a bit-reversed Xilinx sync/commands (5599 → AA99, 0c85 → 30A1). Leave it alone |
| 0x160000–~0x2f0000 | LCD graphics/fonts (RGB565 patterns) [H] |
| 0x2f0000–0x3fffff | Empty (0xFF) |

Ghidra: import the 4 MB raw as `SuperH:BE:32:SH-2A` at base 0x00000000 for the boot block, and add the
app (file 0x10000–0xd3908) as an overlay/memory block at **0x0C000000**. Add SH7263 peripheral regions
(0xFFFxxxxx) from the Renesas hardware manual.

Float constants pi, 2pi and 1/48000 (big-endian IEEE754) appear in the SH app. Coefficient maths likely
happens on the SH [H].

## Relevant strings
- Effects: "1.Delay … 5.Filter … 7.Reverb … 12.BeatScratch 13.SEND/RTN" @0xcd7a0
- Utility: "Parameters Filter Setting", "PC/MAC" @0xcf5f8, "USB A"/"USB B", "Version(SYS)", "Version(DSP)"
- Update: "A:\X1700FW.BIN" @0xd3570, "Version UP Mode/execute?/complete!/Error", "Read data compare error !!!",
  "erase time over?", "New Version = 00000000", "Update error [%2x]"
- Service menu tests: "USB R/W Test", "MIDI OUT TEST", "USB Mode Change", "USB Audio Input/Output"

## USB descriptors (static tables in flash) [C]
Device descriptor @0xd098a: `12 01 0002 EF 02 01 40 4E15 0A50 0001 01 02 00 01`
→ USB 2.0, IAD composite (EF/02/01), **VID 0x154E, PID 0x500A**, bcdDevice 1.00.

Config descriptor @0xd36a0 (RAM 0x0C0C36A0), wTotalLength 385, 6 interfaces, self-powered:
- **IAD 0: interfaces 0–2, bFunctionClass 0xFF (!), subclass 0x00, protocol 0x20**. Prime suspect for macOS: should be 0x01/0x00/0x20.
- IF0 AudioControl, **UAC2** (01/01/20). AC header bcdADC 2.00, **bCategory 0x00** (undefined).
  IT1 USB-streaming 8 ch → OT2 speaker. IT3 mic 8 ch → OT4 USB-streaming. Clock sources 5 and 6 (internal,
  freq r/w), clock selector 7. No feature units.
- IF1 alt1 playback: 8 ch PCM, 3-byte subslot / 24-bit, EP 0x01 OUT iso **adaptive** (0x09), 312 B, bInterval 1.
- IF2 alt1 record: 8 ch, 24-bit, EP 0x85 IN iso **adaptive** (0x09). Unusual for an IN endpoint [suspect].
- CS endpoint descriptors: bmControls 0x15 (odd but probably harmless).
- IAD 1: interfaces 3–4, class 01/00/00. IF3 UAC1 AudioControl stub. IF4 MIDIStreaming 1.0: 2 embedded +
  2 external jacks each way (cables 1–2), bulk EP 0x03 OUT / 0x82 IN, 64 B.
- IF5 HID, EP 0x84 interrupt IN 32 B, vendor usage page 0xFF00 report descriptor (follows the table).
Not yet checked: whether the PC/MAC setting rewrites any of this at runtime, and the string descriptors.

## macOS history [C]
`dnx1700-mac-audio-midi-config-guide.pdf` (2009) has Mac users pick DN-X1700 directly in Audio MIDI Setup with no
driver install, so the device was class-compliant on 2009-era Mac OS X. The failure on current macOS is
therefore a regression. Likely cause: the newer USB audio stack rejects the descriptor quirks above (IAD
class 0xFF first) [H].
