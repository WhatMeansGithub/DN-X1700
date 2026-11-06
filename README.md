# DN-X1700 USB audio fix for current macOS

Firmware patches for the **Denon DJ DN-X1700** mixer (system software v2.000) that make its USB audio and MIDI work with the
built-in class driver in current macOS, with no vendor driver: 8 in / 8 out, 24-bit, at 44.1, 48 or 96 kHz.

> **DN-X1600 owners:** there is an **experimental, untested** version of this fix for your mixer. See
> [DN-X1600 (experimental)](#dn-x1600-experimental-not-tested-on-a-real-unit) below, or go straight to the
> [web tool for the DN-X1600](https://whatmeansgithub.github.io/DN-X1700/x1600/).

> **This repository contains no Denon firmware.** You download the official firmware from Denon, and a script on your own
> computer applies the patches and builds the image. Nothing here is from, or endorsed by, Denon DJ or inMusic.

---

## READ THIS FIRST: disclaimer and risks

**Flashing firmware can permanently destroy your mixer. You do this entirely at your own risk.**

- This project is provided **"as is", without warranty of any kind**. The author(s) and contributors **accept no
  responsibility and no liability** for any damage, including a bricked mixer, data or settings loss, damaged equipment,
  lost income, or a voided warranty. By using anything in this repository you accept that.
- The mixer's updater **erases the entire flash chip and rewrites it**, including the code needed to start up. If power is
  lost, or the stick or the chip misbehaves during the ~30 second write, the mixer **will not start again**. The only
  recovery is reprogramming the flash chip (IC103, EN29LV320AB, TSOP48) with an external programmer, which usually means
  desoldering the chip. If you are not comfortable with that, do not flash.
- **Your settings will be reset.** The update resets all presets and settings to factory defaults (Denon's own release notes
  say so too). Write down everything you care about **before** you start. Do not count on the mixer's preset export: it
  failed on the 64 GB stick used during development. Photograph the settings screens instead.
- Tested by one person, on **one unit**, with macOS 27 on Apple Silicon (Ableton Live for audio and MIDI) and Windows
  (Denon's driver 7.0.0.3502). That is the whole test matrix. Your unit, OS or software may behave differently.
- Modifying the firmware may breach Denon's licence terms and may void any warranty. You are responsible for making sure
  what you do is legal where you live.

If a patched mixer still starts but misbehaves, you can flash the original firmware back (see
[Going back to stock](#going-back-to-stock)). If it does not start at all, see [If it does not start](#if-it-does-not-start).

---

## What this project is

The DN-X1700 (2009) was class-compliant on old macOS versions, but on current macOS it either does not show up as an audio
device, or (after one fix) drops out every few seconds. Windows works with Denon's own driver. This project found out why,
and fixes it **in the mixer's firmware**, so the Mac needs no driver, no kernel extension and no changes to system security.

Two groups of changes, both written as data in `patches/`:

| Patch | What it changes | Why |
|---|---|---|
| `001-usb-macos` | 3 data bytes in the USB descriptors (2 effective) | macOS's audio daemon (`usbaudiod`) drops the device because the audio streaming interfaces claim controls the firmware never answers |
| `002-usb-real-rate` | 148 bytes of new code plus 3 redirects | A firmware bug makes the mixer report its sample rate and clock source wrongly; macOS guesses 44.1 kHz even when the mixer runs 48 or 96 kHz, and audio drops out every few seconds |

Plus a 4-byte update checksum that the updater requires. In total **180 bytes** of the 4 MB image differ from the original.

### What you get

- macOS sees an **8-in / 8-out, 24-bit** audio device; macOS **follows the mixer's sample rate** (44.1, 48, 96 kHz) automatically.
- USB MIDI works (it already did).
- The mixer still works with Denon's driver on Windows in PC mode. Mac mode is class-compliant and was also reported to work on
  Windows.

### What does not change

- No new features, effects or settings. The firmware version number is unchanged (the mixer shows v2.000).
- No volume slider in macOS: the mixer exposes no volume controls. Use the mixer's levels and your software's.
- Switching **PC/MAC** or the **sample rate** still requires a power cycle of the mixer (see below).

---

## How it works

You do not need this to use the patch, but it explains why the steps are what they are.

**The mixer's brain.** An SH7263 processor with no internal storage boots from an external 4 MB flash chip. The firmware file
`X1700FW.BIN` is a raw 4 MB image of that chip. The updater ("Version UP Mode") runs from RAM, checks the file, erases the
whole chip, programs all 4 MB, and verifies them.

**The update checksum.** The updater only accepts an image where the sum of all little-endian 32-bit words equals
`0xFFFFFFFF`. The last word of the file balances it. `tools/build.py` recomputes that word, so a patched image passes the
same check as Denon's own.

**Why macOS rejected it (patch 001).** The mixer describes its USB audio function in descriptor tables stored in flash.
macOS's audio daemon asks for every control those tables claim; this firmware answers none of the "alternate setting"
controls, so the daemon gives up and drops the device. Patch 001 clears those claims.

**PC and MAC mode.** The firmware builds its USB description at power-up depending on **Utility, System Setting, PC/MAC**:
PC mode presents a vendor-specific audio function (so only Denon's Windows driver binds); MAC mode presents a class-compliant
one that built-in drivers accept. That is why changing the setting needs a power cycle: the computer and the mixer would
otherwise disagree about how audio is clocked, and audio skips.

**Why audio dropped out (patch 002).** The mixer has two clock sources: one for 44.1 kHz, one for 48 and 96 kHz. At start-up it
sets the right rate from its own setting, but when the computer asks "what rate are you running?" the answer handler passes
the *value* of a variable to a routine that expects a *pointer*. The computer receives whatever bytes sit at that address:
`0xFF` padding for the rate, and a byte from the start of the firmware (`0xF9`) for the clock selector. macOS then fell back
to 44.1 kHz, even when the mixer ran at 48 kHz, an 8.8 % mismatch, hence dropouts. Patch 002 makes these answers correct and
only offers the rate the mixer actually runs.

**Where the new code lives.** The firmware has no free space, so the patch puts its 148 bytes over a duplicate (lowercase)
copy of a command table that only the internal service serial port uses. Side effect: lowercase commands on that port no
longer work. Normal use is unaffected.

**How it was checked.** The patched code paths were run in a small SH-2 simulator (`tools/sim_patch002.py`) against the real
patched image for every rate and clock, then tested on a real mixer. The macOS side was explored with a microcontroller
(`clone-f411/`) that imitates the mixer's USB description, so descriptor changes could be tested without flashing the mixer.

Full details, evidence and dead ends are in `docs/`.

---

## Before you flash: checklist

- [ ] You have read the [disclaimer](#read-this-first-disclaimer-and-risks) and accept the risk.
- [ ] You have written down or photographed **all** your settings (they will be reset).
- [ ] The mixer is on **stable mains power** that nobody will interrupt (a UPS is a good idea).
- [ ] You have a **FAT32** USB stick with an **MBR** partition table. A small one (a 1 GB partition) is safest. A 64 GB stick
      also passed the mixer's own check, but smaller is the lower-risk choice.
- [ ] You know where to get the original firmware (below), so you can flash back if needed.

---

## Get the patched firmware

The patched firmware itself is **not** hosted in this repository: it is Denon's code with a few bytes changed, so it can't be
shared. You make it from Denon's own download, on your own computer, in one of three ways. **All three produce exactly the same
file** (sha256 `aa9c868e2b3bdacfad198fc0d77edcc0e420dd6dcd16957726882bf73d7d6540`), and all three refuse to patch anything
that is not the original Denon v2.000 firmware.

### Option 1: the web tool (easiest, nothing to install)

Open **https://whatmeansgithub.github.io/DN-X1700/**. It links to Denon's official download; you drop the zip on the page and
download the patched `X1700FW.BIN`. Everything runs in your browser: **your file is never uploaded**. The page checks the
original byte for byte, applies the patches, fixes the updater checksum, and verifies the result against the reference build.

### Option 2: the program (downloads the firmware for you)

Needs Python 3 (nothing else to install):

```
python3 tools/x1700_patcher.py
```

It downloads Denon's firmware, verifies and patches it, and writes `X1700FW.BIN` to the current folder. Useful options:
`--file firmware-dnx1700-v2000.zip` (use a file you already have), `--stick /Volumes/UPDATE` (also copy it to your USB stick and
verify the copy byte for byte), `--out folder`. It asks you to confirm that you have read the warning.

### Option 3: by hand (for the careful)

You need Python 3 and `pyyaml` (`pip install -r requirements.txt`).

1. Download the official firmware from Denon's legacy downloads page and unzip it:
   `https://cdn.inmusicbrands.com/denondj/legacy/dnx1700/firmware-dnx1700-v2000.zip`
2. Copy `X1700FW.BIN` to `firmware/original/X1700FW.BIN` in this repository and check it:
   ```
   shasum -a 256 firmware/original/X1700FW.BIN
   ```
   It **must** be `24ef3d26a116118d27787c337a8a89d67cb6b314cca38f6f22d906b3785556f3`.
3. Build:
   ```
   python3 tools/build.py
   ```
   `firmware/builds/` now holds `X1700FW-<date>-usb-macos+usb-real-rate.BIN`, a `.diff.txt` that lists every changed byte, and
   a manifest. The script refuses to patch if any byte of your original differs from what the patches expect.
4. Check the result. Its sha256 must be
   `aa9c868e2b3bdacfad198fc0d77edcc0e420dd6dcd16957726882bf73d7d6540`, and
   `python3 tools/checksum.py <the built file>` must say `OK`. If either differs, **stop**.

---

## Flash it

These are Denon's own update steps, with the patched file.

1. Format the stick FAT32 (MBR). Copy the built image to the **root** of the stick and **rename it exactly `X1700FW.BIN`**.
   Only that file should be needed on the stick. (On a Mac, hidden `._` files are harmless; to avoid them use
   `COPYFILE_DISABLE=1 cp`.) Eject the stick properly, and confirm the file is exactly 4,194,304 bytes.
2. Mixer **off** (rear switch). Insert the stick into the **rear USB-A port**.
3. Hold **MIC2** and **DUCKING**, switch the mixer **on** and keep holding until the display says **"Version UP Mode"**.
4. Press **Master EFX INS 1**. The mixer checks the stick, then shows **"Version UP execute?"** and
   **"Ver.2000 >>> Ver.2000"** (the version number does not change).
   **To cancel safely, switch the mixer off here. Nothing has been written yet.** This is also a good dry run.
5. Press **Master EFX INS 2**. The display shows **"Software updating keep power on"**.
   **Do not touch the mixer, the stick or the power until "Version UP complete! Shut the power OFF" appears (about 30 s).**
6. Switch the mixer off and on again. It should start normally, with factory settings. Re-enter your settings.

If the display shows **"Update error [xx]"** or **"Version UP Error"**, write down the code and do **not** switch off at once:
the updater retries a few times on its own. If it ends in an error, the flash may be blank.

---

## Use it with a Mac

1. On the mixer: **Utility, System Setting: PC/MAC = MAC**, **USB mode = USB B**, and choose the **sampling frequency**
   (44.1, 48 or 96 kHz).
2. **Power-cycle the mixer.** It reads these settings only at power-up.
3. Connect the USB-B port to the Mac. In Audio MIDI Setup you should see **DN-X1700** with 8 inputs and 8 outputs. macOS picks the
   mixer's rate by itself.
4. Do not change the rate in Audio MIDI Setup or in your software to something else. The mixer does not follow the computer.
   Change the rate **on the mixer**, power-cycle, and the Mac follows.
5. Routing on the mixer: press **USB ASSIGN CH/MASTER**. MASTER mode sends USB 1/2 to the master bus (easiest to test).
   CH INPUT mode sends USB 1/2, 3/4, 5/6, 7/8 to channels 1 to 4; set each channel's input selector knob to USB.

### With Windows

- **PC mode** with Denon's driver (`Install_Denon_DJ_DN-X1700_1.0.2.exe` from Denon's page) is the original way and still works.
- Mac mode was also reported to work on Windows.
- Always power-cycle after switching between PC and MAC.

---

## Known limitations and observations

- **Power-cycle required** after changing PC/MAC or the sample rate (otherwise skipping audio).
- **No volume slider** in macOS (no volume controls in the device).
- **Audio Start:** when audio starts, macOS waits a fraction of a second for the mixer to begin sending; this is normal.
- **Effects display lag (unverified).** One tester noticed the mixer's effects controls and display feeling less responsive while
  USB audio is active, in PC mode as well as Mac mode. It is not known whether the stock firmware behaves the same way, and
  it has not been measured. If you can compare against an unpatched mixer, please report it.
- Lowercase commands on the internal service serial port no longer work (patch code location).
- Audio MIDI Setup may list 44.1 kHz even when the mixer runs 48 or 96 kHz; leave the rate alone.
- Only one unit and one Mac/Windows setup have been tested. Linux has not been tested.

---

## Going back to stock

Put the original `X1700FW.BIN` (the Denon file, hash above) on the stick and repeat the flash steps. The updater does not
compare versions, so same-version and "downgrade" images are accepted. This works as long as the mixer still starts. Settings are
reset again.

## If it does not start

If the mixer shows nothing at power-up after an update, the flash chip was left blank or damaged. Recovery means reprogramming
the chip (IC103, EN29LV320AB, 4 MB parallel NOR in a TSOP48 package) with an external programmer (for example an XGecu T48 with
a TSOP48 adapter), typically after desoldering it. The data to write is the original 4 MB `X1700FW.BIN` (the updater rewrites
the whole chip from that file). This repository does not provide support for that.

---

## DN-X1600 (experimental, not tested on a real unit)

**Web tool: https://whatmeansgithub.github.io/DN-X1700/x1600/**

The DN-X1600 has the same USB audio problem as the DN-X1700, and its firmware (system software v2.000) is built from the same
code, so the fix is the same one, ported. **It has not been tested on a real DN-X1600.** Nobody, including the author, has run it
on a DN-X1600 yet. Treat it as experimental, and everything in [the disclaimer above](#read-this-first-disclaimer-and-risks)
applies to it, including **no guarantees**.

**Why it should work, in theory:**

- The DN-X1600's USB descriptor is byte for byte the same as the DN-X1700's, so patch `001` applies unchanged (same three bytes).
- The faulty sample-rate code that patch `002` fixes is the same code at different addresses, and so are the updater and its checksum
  rule (the DN-X1600 image is 2 MiB instead of 4 MiB). Linux has carried a special case for this mixer since 2021 because
  "switching rates via software is currently not working", which is the same bug.
- The patched image passed the same checks as the working DN-X1700 one: every address is verified against the original file, the
  added code runs in a simulator at 44.1, 48 and 96 kHz (12 of 12 pass), and the web tool, the build script and the tests all
  produce the same file (sha256 `c8209396c80fee970bf9692ecf96717ae39c27f54b1567156d369bb533e4d1bd`).

**What the checks cannot show:** the simulator stands in for the mixer's own sample-rate reader, so only a real DN-X1600 can
confirm it works.

**If it doesn't work**, the likely outcome is that USB audio on the Mac still doesn't work. The changed code only runs when a
computer asks the mixer about its USB audio settings. The start-up code, the updater, the effects and the mixing are not touched.
You can flash Denon's original `X1600FW.BIN` back the same way. The real risk is the same as for any firmware update: a power cut
during the ~30 second write can leave the mixer needing an external flash programmer, and your settings and presets are reset. Note that the DN-X1600 has a **different, smaller flash chip** than the DN-X1700 (see below), so the part number in [If it does not start](#if-it-does-not-start) does not apply to it.

**Flash chip:** the DN-X1600 has a **2 MB flash chip, not the DN-X1700's 4 MB one**. Denon's service manual lists it as IC103,
an **EN29LV160BB-70TIP** (Eon, 16 Mbit, bottom boot, 48-pin TSOP), and the DN-X1600's updater only accepts a flash chip with device
ID `0x2249` (EN29LV160 bottom boot) or `0x22C4` (top boot); the DN-X1700's updater expects `0x22F9`, the 4 MB EN29LV320AB. The
DN-X1600 firmware image is 2 MiB to match. If a flash ever has to be reprogrammed externally, the chip is the same kind of part
(TSOP48) but a different type: use the original `X1600FW.BIN` and the programmer setting for the EN29LV160, not the EN29LV320.

**Using it:** the web tool works like the DN-X1700 one: download Denon's DN-X1600 v2.000 zip, drop it on the page, save the
patched `X1600FW.BIN`. Flashing differs from the DN-X1700: hold **MIC2** and **DUCKING** while switching on until the display says
"Ver. UP", then press **UTILITY**, then **MIDI CLK** (the page shows each step). On the Mac, set **PC/MAC** to **MAC** and
power-cycle the mixer after changing it or the sample rate, as on the DN-X1700.

**Please report back**, whether it worked or not: [open an issue](https://github.com/WhatMeansGithub/DN-X1700/issues) or reply in
the [Engine DJ community thread](https://community.enginedj.com/t/fixed-the-dn-x1700s-usb-audio-on-modern-macos-free-firmware-patch-no-driver-needed/71550) with your Mac model and macOS version, whether the mixer shows up in Audio MIDI Setup as 8 in /
8 out, playback and recording at 44.1, 48 and 96 kHz, and MIDI. Technical details: [docs/x1600.md](docs/x1600.md).

The DN-X600 is built from the same code but updates differently (Denon's updater app over the USB cable), so it is **not**
supported yet.

---

## Repository contents

| Path | What it is |
|---|---|
| `patches/` | The patches as data: offset, expected original bytes, new bytes, reason |
| `site/` | The web tool (`index.html`, `patcher.js`, generated `patches.js`), the experimental DN-X1600 page (`x1600/`) and the tests for both (`test.mjs`, run with Node) |
| `patches/x1600/`, `tools/models.py` | DN-X1600 patch data (experimental, untested on hardware) and per-model addresses; `gen_patch002.py`, `sim_patch002.py` and `build.py` take `--model X1600` |
| `tools/x1700_patcher.py` | One-command program: downloads or loads the firmware, verifies, patches, optionally copies to the stick |
| `tools/gen_site_data.py` | Generates the data used by the web tool and the program from `patches/*.yaml` (run it after changing a patch) |
| `tools/build.py` | Applies patches only if the original matches byte for byte; fixes the updater checksum; writes image, diff and manifest |
| `tools/checksum.py` | The updater's integrity rule |
| `tools/gen_patch002.py`, `tools/sim_patch002.py` | Generates patch 002's code; simulates it in a small SH-2 interpreter |
| `tools/shdis.py`, `tools/shxref.py` | SH-2A disassembly helpers used for the analysis |
| `tools/*.swift` | Read-only macOS probes that ask the mixer's USB clock for its answers |
| `clone-f411/` | STM32F411 "Black Pill" firmware that imitates the mixer's USB description, for testing macOS acceptance without flashing |
| `docs/` | Findings: hardware, memory map, the updater, the USB investigation, flash log |

## License

Copyright (C) 2026 WhatMeansGithub

This project's own work (the tools, patch definitions, patch code, the Black Pill firmware and the documentation) is free
software: you can redistribute it and/or modify it under the terms of the **GNU General Public License, version 3 or (at your
option) any later version**. It is distributed in the hope that it will be useful, but **without any warranty**, without even
the implied warranty of merchantability or fitness for a particular purpose. See the [LICENSE](LICENSE) file.

In short: you may use and change it freely, but if you share a modified version you must share it under the same terms,
with its source. Using it privately, for your own mixer, requires nothing.

**This license covers only the work in this repository. It grants no rights in Denon's firmware or software**, which stays
Denon's (see Legal below). The Denon firmware that the build script patches is not part of this project.

## Legal

Not affiliated with Denon DJ, inMusic or D+M. "Denon", "DN-X1700" and related names belong to their owners. This repository
contains **no Denon firmware, drivers or manuals**, only patch descriptions and original tooling. Do not publish or
redistribute Denon's firmware or patched images built from it.

The patches describe changes to Denon's software, whose licence terms restrict modification. Whether that is allowed
for interoperability depends on your country's law. That is your responsibility to check.
