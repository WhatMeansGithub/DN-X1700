# Effects: the Parameters filter

> Note for readers of this repository: files referenced below under `analysis/` and `docs/vendor/` are not published here.
> The findings stand on their own.

Legend: [C] = confirmed (seen in code/data), [H] = hypothesis. Addresses are RAM (flash = RAM - 0x0C000000 + 0x10000).
Sources: owner's manual, Denon's v2.000 addendum and release notes, Ghidra decompile of the SH app
(`analysis/coef-decomp.c`, `analysis/mathlib-decomp.c`, raw listing `analysis/coef-region.asm`). Not yet measured on the unit.

## What the controls do (from Denon's documents) [C]
- Each effect unit (EFX 1 / EFX 2) has a **PARAMETER** knob with a centre click (service manual: centre detent at
  raw 435..589 of 0..1023), a **Parameter ON/OFF** button and a **DRY/WET** knob.
- v2.000 added Utility → **11 Parameters Filter Setting**: for each state of the Parameter ON/OFF button you choose the
  filter type: **Isolator Filter / Crossover Filter / Through**. Defaults: **OFF = Crossover, ON = Isolator**.
  (Menu strings "OFF Filter", "ON Filter", "Isolator", "Xover", "Through" at 0x0C0BF968..; pointer table at 0x0C0C2E3C.)
- The "5.Filter" effect is a different thing: an LFO sweeping the cutoff at the beat setting.

## How the filter is computed [C unless marked]
The SH (main CPU) computes the coefficients; the DSP only runs the filter. The routine is **0x0C0041E8**
(`FUN_0c0041e8(unit, knob)`), knob = 0..255 with 0x7F at the centre.

- **Cutoff:** a 256-entry float table at 0x0C056D60, exponential.
  - Knob left half (0..127) = **low-pass**, cutoff 60 Hz (full left) rising to ~20 kHz at the centre.
  - Right half (128..255) = **high-pass**, cutoff 60 Hz just right of centre rising to ~20 kHz at full right
    (table 0x0C056D60 + 0x400 + knob*4).
  - About 8.4 octaves in each half-turn, so the sweep is fast.
  - Knob = 0x7F (centre) or type = Through sets a flag bit (|4) in the DSP message, [H] bypass.
- **Filter type:** it is a **Chamberlin state-variable filter, double-sampled**. The coefficient code is an exact
  match for the well-known "stable double-sampled SVF" formulas:
  - `freq = 2 * sin(pi * fc / (2 * fs))` (fs from the 1/44100, 1/48000, 1/96000 table at 0x0C0BC538, chosen by the
    rate getter 0x0C021C3A).
  - `damp = min(2 * (1 - pow(res, 0.25)), min(2, 2/freq - freq/2))` (pow = 0x0C046DF4, a powf wrapper that sets errno).
  - A state-variable filter is **2-pole: 12 dB/octave** [H until measured; the DSP kernel itself is not disassembled].
- **Resonance is a fixed constant: `res = 0.46`** (float 0x3EEB851F), literal at **0x0C004658** (low-pass branch) and
  **0x0C004978** (high-pass branch). That gives damp 0.353, **Q ≈ 2.8, a resonant peak of about +9 dB** at the cutoff.
  - Near the low end of each sweep (LP knob 0..9, HP knob 128..137) `res` is taken from a ramp instead,
    0.00, 0.05 .. 0.41 (table 0x0C056D60 + 0x800, a copy also at 0x0C057560 ending in 0.46), so the very lowest
    cutoffs have little or no resonance.
- **Message to the DSP:** 5 floats `[freq, 0, damp, (value), 0]` plus header bytes (unit, instance, flags, LP=1/HP=0),
  byte-swapped to little-endian by 0x0C002062 (the C67x DSP is little-endian).
- **Crossover vs Isolator:** the type comes from a per-unit byte looked up in 0x0C51ACB2. Type 1 sets flags = 3 and
  multiplies two gain values by 1.83 and 1.17 (doubles 0x3FEA9DCF.., 0x3FF2CC61..). A second message (instance 1) uses
  gains from tables indexed by `0x80 - knob`, i.e. the mirror image of the knob.
  [H] Crossover splits the signal at the cutoff: one side goes through the effect, the other stays dry, so it can
  sound like a band-pass. Isolator filters the signal itself. Not decoded further; measuring will settle it.
- `res` 0.46 also appears at 0x0C0058D8 inside 0x0C004B30 (4.4 KB, [H] the "5.Filter" LFO effect or the crossover path).

## What changing `res` would do (computed from the formulas above)
| res | Q | low-pass peak |
|----:|----:|----:|
| 0.00 | 0.50 | none (soft knee) |
| 0.01 | 0.71 | none (Butterworth, flattest) |
| 0.10 | 1.1 | +2 dB |
| 0.20 | 1.5 | +4 dB |
| 0.30 | 1.9 | +6 dB |
| **0.46** | **2.8** | **+9 dB (stock)** |
| 0.60 | 4.2 | +12.5 dB |
| 0.70 | 5.9 | +15 dB |
| 0.80 | 9.2 | +19 dB |

The slope far from the cutoff stays 12 dB/oct whatever `res` is. A "less aggressive" filter means a lower `res`
(smaller peak) and/or a slower knob curve (the cutoff table). A steeper or gentler slope would need DSP changes.

## Patch options
1. **Fixed resonance (data only, a few bytes):** change the two 0.46 literals, and scale the low-end ramp tables so
   there is no jump at knob 10 / 138. Proof of concept; one flash.
2. **Knob curve (data only):** rewrite the cutoff table, e.g. spend more of the knob on the musical range.
3. **Resonance in the Utility menu or live on a button:** needs SH code (menu entry or button handling, display,
   knob pickup) and code space.

## Open
- Measure the real response (USB loopback) for LP, HP, Isolator and Crossover, to confirm 12 dB/oct, the Q and
  what Crossover does.
- Confirm the DSP kernel (disassemble `dsp.bin`, C67x).
- Identify 0x0C004B30 and where the per-unit type byte comes from.
