# DN-X600: how to test (and how to capture USB packets)

Thank you for testing. Nobody has flashed this on a real DN-X600 yet, so every report is valuable, including "it didn't work".
This page has two jobs: **(1)** record what Denon's own updater sends, so the browser flasher can be checked against it, and **(2)** try the browser flasher.
Do them in this order. Part 1 only uses Denon's own updater with Denon's own, unmodified file.

**What you need:** a Windows PC (Part 1 needs Windows), the DN-X600 with its USB cable, Chrome or Edge, and about an hour.
A Mac also works for Part 2. Background and technical details: [x600.md](x600.md).

> **Risk, honestly:** flashing firmware can leave a mixer unable to start if power or the cable is lost while it writes. Only the last step
> (Execute) writes to the flash. If you'd rather not flash anything, Part 2 can be done as a **dry run** that sends nothing, and
> a failure before the "Executing" message is harmless.

## Part 1: capture what Denon's updater sends (Windows)

### 1. Install the tools
1. Download Wireshark for Windows from <https://www.wireshark.org/download.html> and run the installer.
2. When the installer lists optional components, make sure **USBPcap** is ticked. (It installs a small capture driver. If Windows asks to restart afterwards, restart.)
3. Download Denon's X600 firmware package and unzip it somewhere you can find it. It contains `DDJUpdater-2.0.msi` (the updater) and `X600FW.BIN` (the firmware):
   <https://cdn.inmusicbrands.com/denondj/legacy/dnx600/firmware-dnx600-v1030.zip>. Install DDJ Updater from the `.msi`.

### 2. Put the mixer in update mode and find it
1. Mixer **off**, USB cable connected to the PC.
2. Hold **DUCKING** and **SELECT**, switch the mixer **on**, release when the display shows **UP** (the USB indicator is blue).
   If Windows offers to install a driver from the unit, let it.
3. Optional but very useful: open **Device Manager**, find the new device (look under "Human Interface Devices", "Universal Serial Bus devices" or "Other devices"),
   right-click > **Properties** > **Details** > **Hardware Ids**, and copy the lines (they look like `USB\VID_154E&PID_xxxx`). Put them in your report.

### 3. Start the capture
1. Start **Wireshark** (right-click > Run as administrator if no USBPcap interfaces appear).
2. You will see interfaces named **USBPcap1**, **USBPcap2**, ... (one per USB root hub). The mixer is on only one of them. Click the small **gear icon** (capture options) next to each one:
   it shows a tree of the devices attached to that hub. Use the one that contains the mixer (a Denon / inMusic or "DN-X600" entry).
3. In that window, **untick "Capture from all devices connected to this root hub"** and tick **only the mixer**, and untick "Capture from newly connected devices".
   This keeps your keyboard and mouse out of the file, which matters for your privacy.
4. Click **Start** to begin capturing. If nothing at all appears once the updater runs, you picked the wrong hub: stop and try the next one.

### 4. Run Denon's updater exactly as Denon describes
1. Start **DDJ Updater**. It says "Waiting".
2. Drag `X600FW.BIN` (the **original** file from Denon's zip) into the window. Click **Load**, then **OK**. Wait for it to finish.
3. Click **Execute** and wait until the mixer's display says **donE**. **Do not unplug anything or switch the mixer off while it runs.**
4. Switch the mixer off and on again. It should start normally.

Flashing Denon's own original file is the same as a normal firmware update; it resets the mixer's settings.

### 5. Save and send the capture
1. In Wireshark, click the red **Stop** button, then **File > Save As...** and save as `x600-ddj-updater.pcapng`.
2. The file may be tens of MB. Zip it and share the zip through any file-sharing service (Google Drive, Dropbox, WeTransfer ...), then put the link in your report.

## Part 2: try the browser flasher

Open <https://whatmeansgithub.github.io/DN-X1700/x600/> in **Chrome or Edge** (Safari and Firefox can't talk to USB HID devices). This works on Windows and on a Mac.

1. **Steps 1 and 2 on that page:** download Denon's zip (link on the page) and drop it on the page (or click the box and choose it). That is the only place a file is chosen: the page checks it and builds the patched version from it in your browser (nothing is uploaded). The flasher in step 4 then shows "Loaded ..." and offers both the original and the patched version of that same file.
2. Scroll to step 4 and open **"Option A: flash straight from this page"**.
3. **Dry run first.** Tick *dry run*, tick the acknowledgement, press **Start**. Nothing is sent to the mixer. Copy the log with *copy log* and keep it.
4. Put the mixer in update mode (hold **DUCKING + SELECT** while powering on, display shows **UP**), connect the USB cable, press **Connect to the mixer** and pick the Denon device in the browser's list.
   Copy what the page prints ("Connected: ...") into your report. If no device is listed, tell us what Device Manager (Windows) or System Information > USB (Mac) shows.
5. **Send Denon's original file first:** choose *original*, untick *dry run*, tick the acknowledgement, press **Start**. If the tool talks to your mixer correctly this changes nothing
   (it is the same file Denon's updater sends). You can press **stop** at any time before it says "Executing". Watch for **donE** on the mixer, then switch it off and on.
6. Only if step 5 worked: choose *patched* and repeat. Then set the mixer to **Mac OS** mode: unplug USB, hold **EFX CUE** and press **BEAT ◀ ▶** to enter the OS setting mode,
   choose Mac OS, press **TAP**, **power-cycle the mixer**, and connect it to the Mac. Check Audio MIDI Setup (should show 8 in / 8 out) and test playback, recording and MIDI.

If anything fails, copy the log text. A failure before "Executing" is harmless: the mixer has not been told to write anything, so switch it off and on again and it is as before.

## What to report

[Open an issue on GitHub](https://github.com/WhatMeansGithub/DN-X1700/issues) (or reply in the Engine DJ community thread) with:
- Your Windows and Chrome/Edge versions, or Mac model, macOS and Chrome/Edge version.
- Part 1: the link to the `.pcapng` zip, the Hardware Ids from Device Manager, and what the updater and the mixer displayed.
- Part 2: the copied logs (dry run, original file, patched file), what the mixer displayed (`UP`, `donE`, `FAIL`), and whether it started normally afterwards.
- For the Mac test: does **DN-X600** show up in Audio MIDI Setup with 8 inputs and 8 outputs, and do playback, recording and MIDI work?

## If the mixer doesn't start after an update
Don't keep retrying blindly. Try the update-mode procedure with Denon's updater and the original file once more. If the display stays dark, the flash chip (an EN29LV160BB, IC103)
needs to be rewritten with an external programmer; see the main [README](../README.md#if-it-does-not-start). This is a risk of every firmware update, with Denon's own updater too.
