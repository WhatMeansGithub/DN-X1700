# USB / macOS notes

> Note for readers of this repository: files referenced below under `captures/`, `analysis/` and `docs/vendor/` (raw logs, disassembly
> listings, Denon's manuals and driver) are not published here. They are working material or Denon's property. The findings stand on their own.

Legend: [C] = confirmed by data, [H] = hypothesis.

## macOS test 1 (2026-11-05)
Setup: MacBook (arm64), macOS 27.0.1 (build 26A434), X1700 in USB B mode, Utility PC/MAC = MAC, direct USB-B cable.
Captures: `captures/macos-ioreg-tree.txt`, `captures/macos-ioreg-usb-modeB-mac.txt`, `captures/macos-audio-logs.txt`,
`captures/macos-live-config-desc.bin`, `captures/macos-live-device-desc.bin`.

- [C] Device enumerates: VID 0x154E / PID 0x500A, "DN-X1700", vendor "D&M--", high speed, config 1 active.
- [C] **No audio device is published.** `usbaudiod` logs `Sending enumeration-failure event` (level E) with the
  device and config descriptors, on every attach. The log gives no reason.
- [C] The descriptors macOS received (base64 in the log) are **byte-identical** to the static tables in flash
  (device @0xd098a, config @0xd36a0, 385 bytes). So in this mode (MAC) the firmware does not alter them.
  Still to test: PC mode, to see whether the setting changes anything at runtime [H: it does not].
- [C] Other interfaces bind fine: IF4 MIDIStreaming is taken by MIDIServer (CoreMIDI parses jacks 1/17 and 3/19,
  endpoints 0x03 OUT / 0x82 IN), IF5 HID is bound (AppleUserUSBHostHIDDevice). IF0/IF3 get AppleUSBAudioControlNub.
- [H] Cause is in the audio function (IAD 0 with bFunctionClass 0xFF, adaptive IN endpoint 0x85, bCategory 0,
  no feature units). Not yet isolated; log does not name the failing field.
- [C] **MIDI works at the CoreMIDI level.** Queried via the CoreMIDI API: device "DN-X1700" (driver
  com.apple.AppleMIDIUSBDriver) is online (offline=0) with entities "Port 1" and "Port 2", each 1 source + 1 dest.
  MIDIServer log: `StartIO() = true`, "bringing initialized device online". So MIDI is not broken; it just isn't
  visible in the Audio Devices window of Audio MIDI Setup (MIDI devices are in Window > Show MIDI Studio, Cmd+2).
  Not yet verified: actual MIDI data flow (knob moves arriving in a MIDI monitor).

## Descriptor walk (live == flash)
IAD0 @9: `08 0b 00 03 ff 00 20 00` (ifaces 0-2, class 0xFF). IF0 AC UAC2, IF1/IF2 AS (alt1 = 1 EP each),
IAD1 @228: ifaces 3-4 class 01/00/00, IF3 UAC1 AC stub, IF4 MIDI, bulk EP 0x03/0x82, IF5 HID, EP 0x84 int IN.

## Notes
- In zsh, `log` is a builtin: use `/usr/bin/log show ...`.
- Apple side: the audio daemon is `/System/Library/Audio/Plug-Ins/usbaudio.bundle/Contents/MacOS/usbaudiod`
  (universal; AUASDCore is linked in, Swift, stripped). Analysed the arm64e slice in REA/Ghidra (copy in scratchpad).
  [C] The names `missingAssociation`, `AUAErrorCode`, `IOUSBInterfaceAssociationDescriptor`, `bFunctionClass`,
  `interfaceAssociation` exist as Swift metadata strings. [C] The enum-case string has a single xref, in metadata
  (0x1001399c4), so the throw site is unnamed Swift code and is **not yet located**. `USBDevice.init(boxUID:with:)`
  is an ObjC stub that reports "unimplemented initializer"; the real init is a separate Swift function.

## macOS test 2: PC mode (2026-11-05)
- [C] Descriptors (device and 385-byte config) identical to MAC mode. Same ioreg binding. Same CoreMIDI result.
  **The PC/MAC setting does not change the USB descriptors**, nor the macOS outcome.
- [C] The log gives a reason: `AUAError(error: AUASDCore.AUAErrorCode.missingAssociation)` in `USBDevice.init`
  and `addDevice` (usbaudiod). "Missing association" points at the Interface Association Descriptor: usbaudiod
  finds no audio-class IAD, because IAD 0 has bFunctionClass 0xFF. [H, strong] fix = IAD0 class 0xFF -> 0x01
  (byte at config offset 13, flash 0xd36ad).

## Clone test (Black Pill STM32F411, `clone-f411/`, 2026-11-05)
- [C] v0 (descriptors byte-identical to the real unit): usbaudiod fails with `missingAssociation`, exactly as on the real X1700.
  The clone is a faithful test bed for the descriptor-parsing stage. (`captures/macos-clone-v0-log.txt`)
- [C] **v1 (only IAD0 bFunctionClass 0xFF -> 0x01): `missingAssociation` is gone.** usbaudiod accepts the audio function,
  creates the clock sources/selector and streaming interfaces, sets the sampling rate, and queries terminal/AS controls.
  It also logs "Ignoring usbInterface with number 3 because it is not the first audio function" (IAD1, harmless).
  (`captures/macos-clone-v1-log.txt`)
- [C] After that the clone's stub stalls requests it doesn't implement (terminal latency/cluster, AS interface controls),
  and usbaudiod tears the device down. This is the clone's limitation; what the REAL unit answers to those requests is unknown.
- [H] If the real firmware stalls the same requests, macOS may still reject it after the IAD fix.

## Real X1700 control-request probe (2026-11-05, `tools/probe_uac2.swift`, `captures/probe-uac2-real-x1700.txt`)
Read-only GET requests via the IOUSBHost framework (no root, no capture; libusb gets "Access denied" even with sudo on macOS 27).
- [C] clock 5 rate GET_CUR -> `ff ff ff ff`; GET_RANGE -> **one** range, 44100..44100 (only the current System rate is offered).
- [C] clock 5 validity (cs 2): STALL (consistent: descriptor bmControls=0x03, validity not claimed).
- [C] clock selector 7 GET_CUR -> `0xf9` (out of range, pins are 1..2) [H: firmware bug/stale byte].
- [C] STALL on: terminal latency (cs 7) for entities 1 and 3, the "cluster" request (a1 01 0200 0200 0006), and the four
  `a1 01 0200 0001 000N` requests macOS sent to the clone. The real unit answers none of what usbaudiod asked after the IAD fix.
- [C] Descriptor claims vs behaviour: IT bmControls 0x0555 (6 read-only controls), OT 0x0155 (5), AS_GENERAL bmControls 0x05
  (active alt + valid alt settings, read-only), CS_EP bmControls 0x15 (pitch/overrun/underrun). Firmware stalls requests for them.
- [H] IAD fix alone is not enough; zeroing those bmControls fields may be. Test on the clone.

## Clone test 2: stub mimics real answers (2026-11-05)
- [C] v0 again fails with `missingAssociation` (twice, per attach).
- [C] v1 (IAD fix) with the real unit's answers: usbaudiod now fails earlier than before, at
  `Control Request Stalled: 21 01 0100 0700 0001` = **SET_CUR on clock selector 7** (macOS tries to fix the out-of-range
  pin 0xf9 the unit reports). The stub stalled that write because the real unit's reaction is UNKNOWN (never probed:
  it is a write). `captures/macos-clone2-v1-log.txt`.
- [H] Whether the real firmware accepts that SET_CUR decides if macOS gets any further. Clone stub now accepts it to expose
  the rest of the sequence.

## Clone test 3: which descriptor changes stop the teardown (2026-11-05, `captures/macos-clone3-*.txt`)
Stub mimics the real unit (selector write accepted, everything unprobed stalls).
| variant | changes (cumulative) | result |
|---|---|---|
| v1 | IAD0 class 0xFF->0x01 | association ok, then fatal teardown after stalls on `a1 01 0200 0001 000N` (AS-interface controls) |
| v2 | + IT/OT bmControls -> 0 | "cluster" stall gone, AS stalls remain, teardown |
| v3 | + AS_GENERAL bmControls 0x05 -> 0 | **no fatal teardown, device created** (listed by CoreAudio as "DN-X1700"); only latency (cs 7) stalls remain, non-fatal; BUT `AUAStream publishFormats could not find a valid format` -> no streams |
| v4 | + CS endpoint bmControls -> 0 | same as v3 |
- [H] "no valid format" is a full-speed artifact of the clone: 8ch x 24bit x 44.1k = 1058 B/ms (FS, bInterval 1 = 1 ms) > 312 B
  max packet; on the real HS unit (125 us) it is 132 B (288 B at 96k), which fits. Test: v6 (2ch/16bit = 176 B/ms).
- [H] Minimal set so far for the real firmware: IAD class + AS_GENERAL bmControls (v5 tests this without terminals/EP).

## Clone test 4 (2026-11-05, `captures/macos-clone5-log.txt`)
Variants stepped on the Black Pill: v0 -> v6. All mimic the real unit's control-request answers.
| variant | changes | result |
|---|---|---|
| v0 | none | `missingAssociation` |
| v1 | IAD class | fatal teardown (AS-interface control stalls) |
| v2 | + terminal controls cleared | fatal teardown |
| v3 | + AS_GENERAL bmControls -> 0 | no teardown; device added; "no valid format" |
| v4 | + CS endpoint controls cleared | same as v3 |
| **v5** | **IAD class + AS_GENERAL bmControls only** | no fatal error, device added; "no valid format"; cluster + latency stalls non-fatal. **Minimal patch set (3 bytes in the config)** |
| v6 | v5 + 2ch/16-bit | **formats published** (`44100 lpcm, 4 B/packet`): confirms "no valid format" was the clone's full-speed 312-byte limit |
- [C] Needed for macOS on the clone: IAD0 class 0xFF->0x01 and AS_GENERAL bmControls 0x05->0 (both AS interfaces).
  Terminal and endpoint control clears are not needed. Async IN / bCategory were never needed.
- [H] On the real HS unit 8ch/24bit/44.1k = 132 B per 125 us (<= 312) so formats should be valid; the real unit advertises only 44.1 kHz.
- [open] Real firmware's reaction to the clock-selector SET_CUR (macOS writes pin 1 because the unit reads 0xf9) is untested.

## Vendor docs added (2026-11-05)
- `docs/vendor/dnx1700-notice-mac-users.pdf` (cdn.inmusicbrands.com/denondj/legacy/dnx1700/): "verified with Mac OS 10.5.7 ...
  operates using an audio driver compliant with the Core Audio format" = class driver, no vendor driver. Supports the regression theory.
- Denon's legacy page lists only `firmware-dnx1700-v2000.zip` (same as ours). No older firmware found online
  (Denon CDN guesses v1000..v1950 -> 404; web.archive.org not reachable from the tooling).

## Patch 001 built (2026-11-05) and the selector risk
- [C] `patches/001-usb-macos.yaml` + `tools/build.py`: zero-patch build == original (sha256 identical); patched image differs in
  exactly 7 bytes: 0xd36ad ff->01, 0xd372c 05->00, 0xd3763 05->00, plus checksum word 0x3ffffc 66805a7c->6b7e5b81.
  Output: `firmware/builds/X1700FW-2026-11-05-usb-macos.BIN` (+ .diff.txt, .manifest.json). Not flashed.
- [C] The clock selector descriptor (entity 7) declares bmControls = 0x01 = **read-only**. macOS nevertheless writes pin 1 because the
  unit reads back 0xF9 (invalid). On the clone, a stalled write is fatal (enumeration failure). If the real firmware stalls
  the write (plausible for a read-only control), the descriptor patch alone will not be enough.
- [open] Handler code for UAC2 class requests not located: the USB stack region is around RAM 0x0C042000-0x0C045900 (callback pointer
  table at flash 0xd3860, descriptor registration at 0x0C042F48); the `mov #-7` hits there are generic USB-driver error codes, not the selector.

## Real unit: clock selector write probe (2026-11-05, `tools/probe_selector_write.swift`, `captures/probe-selector-write-real-x1700.txt`)
- [C] GET_CUR selector 7 -> 0xf9; **SET_CUR pin 1 -> accepted (no stall)**; GET_CUR again -> 0xf9. The unit accepts the write and ignores it.
- [H] macOS probably proceeds (it did on the clone when the write was accepted), unless it re-verifies the selector. The clone
  stub now mimics this exactly (accept + ignore, always read 0xf9) and boots into v5 (the real patch set).

## Clone test 5: faithful selector behaviour (2026-11-05, `captures/macos-clone7-log.txt`)
Stub = real unit: selector GET_CUR always 0xf9, SET_CUR accepted and ignored. Clone boots into v5.
- [C] v5 (IAD class + AS controls, i.e. patch 001): macOS reads selector 249, writes pin 1, proceeds; no fatal error; device added.
  "no valid format" remains (full-speed limit of the clone, see v6).
- [C] v6 (v5 + 2ch/16bit): formats published (`44100 lpcm, 4 B/packet`); CoreAudio lists "DN-X1700" 2 in / 2 out, 44.1 kHz.
- [C] macOS does not re-verify the selector after the write.
- Remaining untested (needs the real HS unit): 8ch/24bit format validity, actual streaming, Windows regression.

## CORRECTION + first flash result (2026-11-05)
- [C] After flashing patch 001 the live descriptor (captured from the usbaudiod failure dump, `captures/macos-flashed-log.txt`) has
  the two AS bmControls bytes = 0x00 (patch live) but IAD0 class (config+13) = **0xFF**, although flash holds 0x01. Still `missingAssociation`.
- [C] **The firmware rewrites descriptor bytes at USB init** (RAM 0x0C042F58, flash 0x52F58; mode byte at RAM 0x0C52B588, set by
  the function at 0x0C0433C8 from its argument, which is called from 0x0C0449D0):
  mode == 1: config+13 (IAD0 class) = 0x01, config+161 and config+216 (EP 0x01 / 0x85 bmAttributes) = 0x0D (synchronous);
  otherwise: 0xFF and 0x09 (adaptive). So the firmware already has a class-compliant mode (almost certainly "PC/MAC" = MAC).
- [C] This supersedes "the PC/MAC setting does not change the descriptors" (macOS test 2): those tests never power-cycled the unit,
  and the descriptors are only (re)built when the USB stack is initialised.
- [H] mode byte == 1 corresponds to Utility PC/MAC = MAC, applied at power-up (and maybe on USB A/B switch). To verify: set MAC,
  power-cycle, capture live descriptors (expect config+13 = 0x01, EP bmAttributes 0x0D).
- [open] Fallback if MAC mode does not produce class 0x01: code patch at flash 0x52F6D (`mov #-1,r1` -> `mov #1,r1`) forces class 0x01
  always (would also affect the Windows vendor-driver mode, so only as a last resort).

## RESULT: patched firmware in MAC mode works on macOS (2026-11-05, 22:28)
- [C] Flashed patch 001 (stick: 1 GB FAT32). PC mode: still `missingAssociation` (runtime IAD class 0xFF). After setting PC/MAC = MAC:
  live descriptor (`tools/read_config_desc.swift`, `captures/live-config-after-flash-pcmode-or-mac.bin`): IAD0 class 0x01, EP 0x01/0x85 bmAttributes 0x0D
  (synchronous), AS bmControls 0x00 (patch). usbaudiod: AUAAudioDevice_init ok, formats published (44100, lpcm, 8 ch, 24 bit, 24 B/frame),
  device added. system_profiler: DN-X1700, 8 input + 8 output channels, 44100 Hz, USB.
- [C] Only non-fatal stalls remain (terminal latency, `a1 01 0700 0100 0004`, `...0300 0004`).
- [C] The patch byte at 0xd36ad (IAD class in flash) is overridden at runtime by the firmware (class comes from the PC/MAC mode byte), so it is
  redundant; the AS_GENERAL bmControls patches (0xd372c, 0xd3763) are the effective ones [H: unpatched MAC mode would stop at the AS-control stalls, as the clone did].
- [C] The unit applies PC/MAC at USB init: a full power cycle is needed after changing it.
- Remaining to verify by use: real audio in/out, MIDI, sample-rate changes (unit offers only its System Setting rate), Windows in PC mode.

## Audio does not flow in MAC mode (2026-11-05, 22:2x-22:4x)
- [C] Enumeration, formats and streaming setup work (see RESULT above); the Mac streams to the unit (device running, 44.1 kHz, 8 out ch,
  USB timing stable, no output-stream errors). A 3 s, -30 dBFS tone sent directly to DN-X1700 USB ch 1/2 (not via Ableton) produced
  no sound and no meter activity on the mixer (MASTER mode, USB B, digital out fs 44.1 kHz, no prompt on the display). Spotify to the system
  default output (= DN-X1700): nothing either.
- [C] usbaudiod's lock-delay logic: after startIO it receives 160 zero-length IN packets (max allowed 16) and keeps raising the lock delay
  (24 -> 100 -> 150 ... ms). The unit's IN endpoint (EP 0x85) never produces data: the unit's USB audio engine does not start (both directions).
- [C] No volume/mute controls exist (no feature units): macOS shows no slider. Expected, not a fault.
- [H] Firmware's USB audio start (SET_INTERFACE alt 1 -> DMA/FPGA route) is not triggered under macOS's class driver in MAC mode, or needs a
  request/state macOS does not provide. Not yet located in the code (USB callbacks table at flash 0xd3860).
- Ideas: (1) test MAC mode on Windows with the inbox UAC2 driver to separate "firmware" from "macOS-specific"; (2) reverse the alt-setting/start path.

## Denon Windows driver (analysis/denon-win-driver/, from Denon's legacy page, `install-dnx1700-1-0-2.zip`) (2026-11-05)
- [C] Package = WiX bundle -> MSI32/MSI64 (extracted with 7-Zip, nothing executed). Components: DenonDJDN-X1700.sys 7.0.0.3502 (10/07/2015), ASIO DLL,
  Panel.exe (control panel), AudioDevMon.exe (service), DeviceInterface.dll, DriverInterface.exe, DriverReplacer.exe (candidates `USB\VID_154E&PID_500A`).
- [C] INF (`msi32/out/StreamingINF`): Class MEDIA, model line `USB\VID_154E&PID_500A` = binds to the WHOLE composite device (not per MI_xx function):
  a vendor WDM/KS driver (wave/midi/mixer via wdmaud) that does audio + MIDI itself, not usbaudio.sys. PC mode (IAD class 0xFF) exists to keep the inbox driver away.
- [H] So "Windows works only in PC mode" does not isolate the firmware: the Denon driver still preempts the inbox driver in MAC mode (same device ID) and
  probably expects the PC-mode descriptors. A real class-driver test needs the Denon driver uninstalled.
- [C] User report (Windows, PC mode): Denon driver exposes "Master" (Speakers) and Line devices; format had to be changed from 32-bit 96 kHz to 16-bit 96 kHz;
  audio plays straight to master. Note 96 kHz on Windows vs only 44.1 kHz offered to macOS (GET_RANGE) [open: unit rate setting vs advertised range].

## Dropouts on Mac: sample-rate mismatch (2026-11-05 23:1x, `captures/macos-dropouts-log.txt`)
- [C] Audio now flows on the Mac (no "excessive zero length packets"); periodic dropouts.
- [C] usbaudiod `AUAAudioDevice_updateTimeStamp ioDriftNS`: +20.34 ms every 11025 samples (250 ms host time) => device clock = 11025/(0.250-0.02034)
  = ~48,005 Hz. macOS runs the device at 44,100 Hz (it "chooses default 44100": GET_CUR rate = 0xffffffff, selector reads 0xf9 -> pin 1 = clock source 5).
  [H] The mixer really runs at 48 kHz (its System Setting) => 8.8 % rate mismatch => dropouts.
- [C] Firmware rate answers (flash 0xd3485.., class-request GET code at RAM 0x0C038EBC, flash 0x48EBC): three separate GET_RANGE responses
  (14 bytes each: wNumSubRanges=1, min, max, res=0): 44.1 kHz @0xd3485, 48 kHz @0xd3493, 96 kHz @0xd34a1. The code returns the 44.1 block for clock-source
  entity 5 and the 48 block for entity 6; GET_CUR returns a 4-byte variable at RAM 0x0C5252B0 (reads 0xffffffff = unset).
- [H] Design: clock source 5 = 44.1 kHz, 6 = 48 kHz (96 kHz handled elsewhere?), clock selector 7 picks the active one; the real rate follows the Utility System Setting.
  The selector read-back (0xf9) is invalid, so a class driver cannot know the true rate; SET_CUR is accepted but does not change anything.
- Next: (a) set the mixer's System Setting to 44.1 kHz and power-cycle: if dropouts vanish the diagnosis is confirmed; (b) firmware fix: make the selector
  GET_CUR (and rate GET_CUR) report the real rate so macOS picks the right clock source.

## Patch 002: report the unit's real sample rate (2026-11-05)
- [C] Root cause of the wrong rate on macOS: firmware bug in the UAC2 GET_CUR handlers. Sample rate (RAM 0x0C038ECA) and clock selector
  (0x0C038F4E) pass the VALUE of a variable to the reply routine 0x0C03904A (r4 = data POINTER, r5 = length; it stores the pointer and sends
  later). The host receives the bytes at that "address": selector entity 5 -> byte at address 5 = 0xF9 (vector table); current rate 44100/48000
  -> flash 0xAC44/0xBB80 = 0xFF padding -> ff ff ff ff. GET_RANGE passes real pointers and works.
- [C] Clock model in the firmware: clock source 5 = 44.1 kHz (range block @0xd3485, 1 sub-range), clock source 6 = 48 + 96 kHz (block @0xd3493,
  2 sub-ranges, 26 bytes), selector 7 picks the source (pin->entity table @0xd34ac). Vars: cur rate 0x0C5252B0, clk5 0x..B4, clk6 0x..B8,
  selected entity 0x..BC. USB init (0x0C038C2A) sets cur rate from the real setting via getter 0x0C021C3A (0=44.1k, 1=48k, else 96k).
- Fix (`tools/gen_patch002.py` -> `patches/002-usb-real-rate.yaml`): 148 bytes of code/data over the lowercase serial-command table
  (RAM 0x0C0BEA48, duplicate of the uppercase table, service UART parser only) + 3 site redirects (literal slots verified single-use):
  GET_CUR rate: clock 5 -> 44100, clock 6 -> 48000 or 96000 from the real setting; GET_CUR selector: pin 1 at 44.1 kHz, else pin 2;
  GET_RANGE clock 6: only the real rate (48 or 96), so macOS cannot pick a rate the unit is not running.
- [C] Simulated (`tools/sim_patch002.py`, tiny SH-2 interpreter over the patched image, getter + reply stubbed): 12/12 cases pass.
- [C] Build: `firmware/builds/X1700FW-2026-11-05-usb-macos+usb-real-rate.BIN`, sha256 aa9c868e2b3bdacfad198fc0d77edcc0e420dd6dcd16957726882bf73d7d6540,
  180 bytes differ from the original, checksum OK. NOT flashed.
- Side effect: lowercase commands on the internal service UART no longer parse (uppercase table untouched).
- Expectation [H]: rate is chosen on the mixer (Utility System Setting), power-cycle, macOS follows automatically. Clock 5 still advertises 44.1 kHz,
  so Audio MIDI Setup may still list 44.1 kHz when the mixer runs 48/96 kHz: do not change the rate on the Mac.

## MIDI check (2026-11-05)
- [C] CoreMIDI listener (45 s): 1599 messages from DN-X1700 Port 1: CC on ch 1 (`b0 1f xx`), MIDI clock start `fa` + ticks `f8`. Owner confirms MIDI works in Ableton.

## RESULT patch 002 on the unit (2026-11-05 23:3x), System Setting 96 kHz, MAC mode
- [C] Flashed `X1700FW-2026-11-05-usb-macos+usb-real-rate.BIN`. Unit boots. Probe: selector GET_CUR = 0x02 (was 0xf9), clock 5 GET_CUR = 44 ac 00 00 (44100, was ff ff ff ff).
  CoreAudio nominal rate 96000 (picked automatically). ioDriftNS ~0.5 ms over ~70 s of audio = ~7 ppm (normal crystal offset; was 8.8 %). Owner: audio works.
- [C] 44.1 kHz (23:40): selector GET_CUR = 0x01, clock 5 = 44100, CoreAudio nominal 44100, ioDriftNS ~0.3 ms over ~45 s (~7 ppm). Owner: audio works.
- [C] 48 kHz (23:40): selector = 0x02, macOS "Clock Selector 7 has CUR value of 2", clock 6 getSamplingRate 48000, formats published at 48000, nominal 48000.
- [C] All three rates: at stream start macOS retries the lock 1-3 times ("excessive zero length packets", lock delay 24 -> 100-200 ms) before the unit
  sends IN data, then streams normally. Seen at 44.1/48/96 kHz alike; the unit simply needs ~0.2 s to start. Harmless.
- Open: Windows PC-mode check with the Denon driver after patch 002 (the GET_CUR answers changed from garbage to correct values).
- [C] Windows, PC mode, Denon driver 7.0.0.3502, after patch 002: audio works (owner).
- [C] Switching PC <-> MAC needs a power cycle of the mixer; without it audio skips. Expected: the descriptors (IAD class, iso endpoint sync type
  adaptive 0x09 vs synchronous 0x0D) are only rebuilt at USB init (RAM 0x0C042F58), so host and unit disagree until the next power-up.

## Status (2026-11-05): Workstream B (macOS USB audio + MIDI) DONE
Image: firmware/builds/X1700FW-2026-11-05-usb-macos+usb-real-rate.BIN (patches 001+002). macOS: MAC mode, 44.1/48/96 kHz follow the mixer's
System Setting automatically, 8 in / 8 out 24-bit, MIDI OK. Windows: PC mode with Denon driver OK. Rule: change mode or rate on the mixer, then power-cycle.
