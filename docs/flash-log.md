# Flash log
Every image flashed to the mixer: date, sha256, result. The owner flashes; nothing is flashed without a decision recorded here.

| date | image | sha256 | result |
|---|---|---|---|
| (factory/current) | firmware/original/X1700FW.BIN (v2.000) | 24ef3d26a116118d27787c337a8a89d67cb6b314cca38f6f22d906b3785556f3 | running on the unit |
| 2026-11-05 | firmware/builds/X1700FW-2026-11-05-usb-macos.BIN (patch 001) | 01a3f5a486f44485ee834dcb9a7e4fc4bf0e05c0b13847d21588ea8d1e53b4ed | flashed via USB-stick updater, completed; unit boots. macOS: works in MAC mode after power cycle (8in/8out 44.1 kHz). PC mode unchanged (IAD 0xFF) |
| 2026-11-05 | firmware/builds/X1700FW-2026-11-05-usb-macos+usb-real-rate.BIN (patches 001+002) | aa9c868e2b3bdacfad198fc0d77edcc0e420dd6dcd16957726882bf73d7d6540 | flashed, completed, boots. MAC mode 96 kHz: correct clock answers, macOS picks 96 kHz, drift ~7 ppm, audio OK. 44.1 kHz OK (selector 01, ~7 ppm). 48 kHz OK (selector 02). Windows PC mode OK. Mode switch PC<->MAC needs power cycle |

## Update stick (2026-11-05)
- SanDisk 3.2Gen1 64 GB repartitioned: MBR, partition 1 = 1 GB FAT32 "UPDATE" (4 KB clusters), rest unallocated. Was empty.
- `X1700FW.BIN` on the stick = firmware/builds/X1700FW-2026-11-05-usb-macos.BIN: 4194304 bytes, sha256 01a3f5a4...b4ed, cmp-identical,
  updater checksum OK. Unit's own "USB Device Check" in Version UP Mode passed with this stick (before repartitioning, 61.5 GB volume).
- macOS left hidden `._X1700FW.BIN`, `.Spotlight-V100`, `.fseventsd` in the root (harmless, different names). Not flashed yet.
- Preset export to USB did not work with the 64 GB volume (USB A mode needed; retry with the 1 GB volume).
