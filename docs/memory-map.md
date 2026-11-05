# Memory map, boot and update integrity

> Note for readers of this repository: files referenced below under `captures/`, `analysis/` and `docs/vendor/` (raw logs, disassembly
> listings, Denon's manuals and driver) are not published here. They are working material or Denon's property. The findings stand on their own.

Legend: [C] = confirmed (seen in code/data), [H] = hypothesis.
Tools: `tools/shdis.py` (SH-2A disassembler, resolves literals), `tools/shxref.py` (string xrefs),
`tools/checksum.py`. Listings: `analysis/updater-flash.asm`, `analysis/updater-load-verify.asm`.
Address mapping: flash offset F in 0x10000..0xd3908 lives at RAM 0x0C000000 + F - 0x10000 [C].

## Boot code (flash 0x100..) [C]
1. Reset PC 0x100, SP 0xFFF90000. Copies 0x21 words from flash 0 to on-chip RAM 0xFFF80000 (vector table), sets VBR.
2. Copies a small routine (0x26 words, flash 0x6000) to on-chip RAM 0xFFF86210 and calls it.
3. Copies the app: flash 0x10000 -> SDRAM 0x0C000000, 0x30460 words (0xC1180 bytes), then **compares the copy
   against flash word by word**. Mismatch -> `bra .` (hang at 0x382). This only guards the SDRAM copy; it is
   not an integrity check of the image.
4. Runs an SDRAM pattern test (0xAAAAAAAA / 0x55555555) up to 0x0C735000, then jumps to **0x0C0252B4** (app entry).
- [C] The boot code does **not** check the image checksum. A patched app boots if the SH app itself works.
- [H] Table at 0x5bc..0x630 also describes the DSP (flash 0x100000, 0x1ffc8) and FPGA (0x130000, 0x15f39) loads.

## Updater ("Version UP Mode") [C unless marked]
Code lives in the app (copied to SDRAM, so it keeps running while flash is erased).
UI/flow strings @0x25a34.., flash routines @0x50500..0x50d00 (RAM 0x0C040500..).

Sequence of the update function (RAM 0x0C040A54):
1. Read flash autoselect IDs (AA/55/90): the **device ID must be 0x22F9** (EN29LV320AB bottom boot), else
   "Unknown flash chip ID".
2. Read `A:\X1700FW.BIN` from the USB stick in 4 x 1 MiB chunks (function 0x0C04061C). **Integrity check:
   the sum of all little-endian 32-bit words of the 4 MiB file must be 0xFFFFFFFF**, else error -25.
   The original image satisfies this (checked). The last word (flash 0x3FFFFC) balances the sum.
3. "New Version" = the word at file offset 0x2000 (v2.000 image: 0x00002000). That 8 KB block
   (file 0x2000..0x3FFF) is held back in RAM and the flash's own 0x4000-range block is saved; [H] this is a
   "version word is written last" scheme so an interrupted update has no valid version.
4. "=>Flash Erase": sends AA 55 80 AA 55 **10** = **CHIP ERASE** of the whole device, incl. the boot block, polling
   DQ7/DQ5 ("erase time over?"). Up to 3 attempts.
5. "Done =>Program": word-programs (AA 55 A0) the 4 MiB from the file, with read-back checks of each word.
6. "Done =>Verify": reads the flash back and compares ("Read data compare error !!!"). Retries up to 3 times,
   then "Update error [xx]".
- Pre-checks: file must deliver 4 x 1 MiB.
- Power cut between step 4 and the end leaves a blank or partial flash. Boot code is inside the erased area,
  so the SH7263 (ROM-less) cannot start. **An external programmer on IC103 is the only recovery.**

## Consequences for patching
- A patched image must keep size 4 MiB and the LE word-sum 0xFFFFFFFF. `tools/checksum.py fix()` rewrites the
  last word. Example: IAD0 class byte at 0xd36ad (0xFF -> 0x01) changes 3 bytes in total (0xd36ad,
  0x3ffffd, 0x3ffffe) [C, computed in memory, no image written].
- Because the updater rewrites everything, the boot block comes from the file too: **never touch 0x0..0x6097**
  unless needed, and every image must pass the checksum, or the updater refuses it (safe) / the unit may not boot.
- Presets are reset by an update (Denon release notes); [H] the non-empty tail at 0x2f0000..0x3fffff
  (6819 non-FF bytes) is user/factory data erased and rewritten by the update. Not yet analysed.
- The updater itself is in the app, so a patch that keeps the app working keeps the unit re-flashable
  with the original image through the same USB-stick route.

## Open items
- Exact meaning of the 8 KB "save" blocks (0x2000 in file, 0x4000 in flash) around the program loop.
- What the Utility/PC-MAC code touches in RAM (descriptors confirmed unchanged at USB level).
- Contents of flash 0x2f0000..0x3fffff and the load table at 0x5bc..0x630.

## Version gating (checked 2026-11-05)
- [C] No version comparison found. The UI state machine (RAM 0x0C015A00.., `analysis/updater-ui.asm`) shows "Ver.%04x >>> Ver.%04x"
  (old >>> new) and waits for [Master EFX INS 2]; its only `cmp` instructions test state numbers. The update function
  (0x0C040A54) reads the new version from file offset 0x2000, prints it, and gates only on flash ID 0x22F9 and the
  checksum. So a downgrade or same-version image is accepted, and a patched image does not need a bumped version.
- [H] Other app code may read the version word (settings/preset migration). Not checked. Keep 0x2000 unchanged for the first patch.
