# DN-X1700 descriptor clone (Black Pill, STM32F411)

Presents the real X1700's USB descriptors (captured from macOS, `captures/macos-live-*.bin`) so we can test which
descriptor changes make macOS's `usbaudiod` accept the device, **without touching the mixer**.
Full-speed only (F411 has no HS port), so it answers "is `missingAssociation` gone?", not "does audio stream".

## Variants (cumulative; regenerate with `tools/gen_clone_desc.py`)
| # | LED blinks | Change | Expectation |
|---|---|---|---|
| v0 | 1 | none (original) | **must reproduce** `missingAssociation` (control: proves the clone is faithful) |
| v1 | 2 | IAD0 bFunctionClass 0xFF -> 0x01 | the hypothesis |
| v2 | 3 | v1 + IT/OT terminal bmControls -> 0 | macOS still tears the device down (clone result) |
| v3 | 4 | v2 + AS_GENERAL bmControls 0x05 -> 0 | device created; "no valid format" (FS artifact?) |
| v4 | 5 | v3 + CS iso endpoint bmControls -> 0 | same as v3 |
| v5 | 6 | v1 + AS_GENERAL bmControls -> 0 only (minimal set) | does it match v3? |
| v6 | 7 | v5 + 2ch/16bit (FS bandwidth sanity, NOT for the real unit) | formats valid? then "no valid format" was the FS limit |

The control-request stub mimics the real unit's answers (rate 44.1 kHz only, selector 0xf9, everything else STALL),
captured with `tools/probe_uac2.swift`. Press KEY to step v1 -> v2 -> ... after each check. Reflash needed: stub and variants changed.

KEY button (PA0) = next variant; the board soft-disconnects and re-enumerates. Boots into v0.

## Build and flash
```
cd clone-f411
~/.platformio/penv/bin/pio run                 # builds .pio/build/blackpill_f411ce/firmware.bin
# DFU mode: hold BOOT0, tap NRST, release BOOT0, plug USB (the F411 shows up as "STM32 BOOTLOADER")
~/.platformio/penv/bin/pio run -t upload
# then press NRST (or replug) to run it
```

## Test on the Mac (unplug the real X1700 first, it uses the same VID/PID)
After each variant: `/usr/bin/log show --last 2m --info --debug --style compact --predicate 'process == "usbaudiod"'`
and look for `missingAssociation` / `enumeration-failure`; also check Audio MIDI Setup (Audio Devices).
