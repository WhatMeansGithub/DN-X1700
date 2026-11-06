# Testing the DN-X600 firmware fix

Thanks for offering to test this. Nobody has tried it on a real DN-X600 yet, so every result helps, including "it didn't work".

**What this is:** a small firmware change that should make the DN-X600's USB audio and MIDI work with current macOS. It also comes with a web page that can flash the mixer from
Chrome or Edge on a Mac or Windows PC, because Denon's own updater no longer runs on modern macOS. That web flasher is brand new and has only been tested against a simulation, which is why
we want to compare it with Denon's real updater first.

**What you need:** the DN-X600 and its USB cable, a Windows PC with Chrome or Edge, and about an hour. A Mac helps for the last step but isn't required.

## Please read first
- Flashing firmware always carries a small risk: if power or the USB cable is lost *while the mixer is writing its flash*, it may not start again, and the only fix is an external flash programmer.
  Use stable power and don't touch the cable or the switch during the write. Your mixer's settings will be reset.
- The tool sends the file first and only then tells the mixer to write. Stopping *before* the "Executing" message should be harmless (that's what the design suggests; it isn't proven yet).
- **You do the steps in order. The patched firmware comes last, and only if the earlier steps worked.**

## The plan
1. **Capture** what Denon's own updater sends (Windows only). This is the most valuable part.
2. **Dry run** the web flasher (sends nothing to the mixer).
3. Flash Denon's **original** firmware with the web flasher. If it works, nothing about the mixer changes.
4. Flash the **patched** firmware, then try it with a Mac (or at least check it still works on Windows).

---

## 1. Capture Denon's updater (Windows)

**Install**
1. Install **Wireshark** from <https://www.wireshark.org/download.html>. In the installer, make sure **USBPcap** is ticked. Restart Windows if it asks.
2. Download Denon's firmware package and unzip it: <https://cdn.inmusicbrands.com/denondj/legacy/dnx600/firmware-dnx600-v1030.zip>. Install **DDJ Updater** from the `.msi` inside.

**Put the mixer in update mode**
1. Mixer off, USB cable connected to the PC.
2. Hold **DUCKING** and **SELECT**, switch the mixer on, and release when the display shows **UP**. If Windows offers to install a driver from the unit, let it.
3. Open **Device Manager**, find the new device (try "Human Interface Devices", "Universal Serial Bus devices" or "Other devices"), right-click > **Properties** > **Details** > **Hardware Ids**,
   and copy the text. It looks like `USB\VID_154E&PID_xxxx`. Keep it for your report.

**Capture while updating**
1. Open **Wireshark** (as administrator if no USBPcap interfaces show). You'll see **USBPcap1**, **USBPcap2**, ... (one per USB hub). Click the small **gear icon** next to each to see which devices it holds;
   pick the one that contains the mixer.
2. In that window, untick "Capture from all devices connected to this root hub" and tick **only the mixer**. (This keeps your keyboard and mouse out of the file.) Click **Start**.
3. Open **DDJ Updater** (it says "Waiting"). Drag in `X600FW.BIN` from Denon's zip, click **Load**, then **OK**.
4. Click **Execute** and wait until the mixer shows **donE**. **Don't unplug or switch anything off while it runs.**
5. In Wireshark click the red **Stop** button, then **File > Save As...** and save `x600-ddj-updater.pcapng`. Zip it and share it through Google Drive, Dropbox, WeTransfer or similar (it may be tens of MB).
6. Switch the mixer off and on again. It should start normally.

If nothing appears in Wireshark while the updater runs, you picked the wrong hub: try the next one.

## 2. Dry run of the web flasher

Open <https://whatmeansgithub.github.io/DN-X1700/x600/> in **Chrome or Edge** (not Safari or Firefox). Nothing is uploaded; everything happens in your browser.

1. **Step 1:** download Denon's zip with the button (you already have it from part 1). **Step 2:** drop the zip onto the page.
2. Scroll to step 4, **Option A: flash straight from this page**. The **Firmware file** box now shows two green choices, **Original firmware ✓** and **Patched firmware ✓**.
3. Tick the acknowledgement and **dry run (send nothing)**, then press **Start**. It runs through the whole sequence without touching the mixer. Press **copy log** and save the text.

## 3. Flash Denon's original firmware with the web flasher

1. Put the mixer in update mode again (**DUCKING + SELECT** while powering on, display shows **UP**) with the USB cable connected.
2. Click **Connect to the mixer** and choose the Denon device in the browser's list. The page prints "Connected: ..." in the log. If no device appears, or Chrome can't open it, copy what you see and stop here
   (on Windows, the driver installed for Denon's updater can sometimes get in the way; if so, trying again from a Mac is a good next step).
3. Choose **Original firmware ✓**, untick *dry run*, tick the acknowledgement and press **Start**. It takes a few minutes. **Don't touch anything** once it says "Executing".
   (The **stop** link only works before that point.)
4. The mixer should show **donE**. Switch it off and on. Copy the log.

If this worked, the web flasher talks to your mixer correctly, and the mixer is exactly as before.

## 4. Flash the patched firmware and try it

1. Repeat step 3 but choose **Patched firmware ✓**. (Same procedure, same page; just load Denon's zip again if you reloaded the page.)
2. Switch the mixer off and on. It should start normally.
3. The fix only applies in **Mac mode**: unplug USB, hold **EFX CUE** and press **BEAT ◀ ▶** to enter the OS setting mode, choose **Mac OS**, press **TAP**, then **switch the mixer off and on**.
4. Connect it to a Mac. In **Audio MIDI Setup** it should show **DN-X600** with 8 in / 8 out. Test playback, recording and MIDI if you can.
   *Only have Windows?* Set it back to Windows (same key combination), power-cycle, and check that audio and MIDI still work as before with Denon's driver. That's a useful result too.

## What to send back
[Open an issue on GitHub](https://github.com/WhatMeansGithub/DN-X1700/issues) or reply in the forum thread with:
- Windows version and Chrome/Edge version (and the Mac model and macOS version if you used one).
- The link to your `.pcapng` capture and the **Hardware Ids** text from Device Manager.
- The copied logs: dry run, original firmware, patched firmware.
- What the mixer's display showed (`UP`, `donE`, `FAIL`) and whether it started normally after each flash.
- The Mac results: does it show up in Audio MIDI Setup, and do audio and MIDI work?
- Anything confusing or broken, however small.

## If something goes wrong
- An error or a stop **before** "Executing": switch the mixer off and on and it should be as it was. Send us the log.
- A failure **during** or after Executing, or the mixer not starting: don't keep retrying blindly. Try update mode with Denon's updater and the original file once more. If the display stays dark, the flash chip
  (an EN29LV160BB, IC103) needs an external programmer; see the [README](../README.md#if-it-does-not-start). Every firmware update has this risk, Denon's own included.
- The web page itself looks wrong or confusing: that's useful feedback too.

Technical background, for the curious: [x600.md](x600.md).
