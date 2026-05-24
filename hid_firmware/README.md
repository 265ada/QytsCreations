# Macro Recorder — Hardware HID Backend

This directory contains firmware that turns a small USB microcontroller into
a **real USB keyboard + mouse**, controlled by `macro_recorder.py` over a
serial port.

This is the most robust input backend the macro recorder supports. Because
the OS sees a genuine HID device (no kernel hooks, no message injection,
no SendInput), the input is indistinguishable from real hardware — which
matters for games that read DirectInput / Raw Input and ignore window
messages.

## Important — use cases & limits

- **Intended uses:** accessibility, automated testing of your own apps,
  productivity automation, single-player game macros, automation rigs,
  development.
- **Do not** use this to bypass anti-cheat in commercial multiplayer games.
  Even though the OS can't tell the difference, many publishers' Terms of
  Service prohibit automated input. This is **your** responsibility.
- The firmware does nothing sneaky — it's a generic HID device with a
  documented ASCII protocol.

## Pick a board

| Board                   | Firmware file                  | Cost  | Notes                                 |
|-------------------------|--------------------------------|-------|---------------------------------------|
| **Raspberry Pi Pico**   | `pico_circuitpython.py`        | ~$5   | Easiest. CircuitPython, drag-and-drop |
| **Arduino Pro Micro**   | `promicro_arduino.ino`         | ~$8   | Smallest. ATmega32U4-based            |
| **Arduino Leonardo**    | `promicro_arduino.ino`         | ~$15  | Same chip, full-size                  |
| **Teensy 2.0 / LC / 4** | adapt `promicro_arduino.ino`   | ~$15+ | Identical Keyboard.h / Mouse.h API    |

A second machine or a "key-cloner" device with a USB host port can also
work, but those are out of scope for this firmware.

## Quick start (Pi Pico)

1. Flash CircuitPython 9.x: download `.uf2` from https://circuitpython.org/board/raspberry_pi_pico/
   then drag onto the **RPI-RP2** drive that appears when you hold BOOTSEL + plug in.
2. Download the Adafruit CircuitPython bundle from https://circuitpython.org/libraries
   and copy `adafruit_hid/` into `CIRCUITPY/lib/`.
3. Copy `pico_circuitpython.py` to `CIRCUITPY/code.py`.
4. The Pico reboots and now appears as **two** devices: a USB keyboard+mouse,
   and a USB serial port. Note the COM port number (Device Manager → Ports).
5. In `macro_recorder.py`, set:
   ```python
   SERIAL_HID_PORT = "COM7"   # whatever your Pico is on
   ```
   *or* set env var `MACRO_SERIAL_PORT=COM7` before launching.
6. In the macro recorder UI, set **Input Backend → Serial HID — real USB device**
   for the macros you want delivered via hardware.

## Quick start (Arduino Pro Micro / Leonardo)

1. Open `promicro_arduino.ino` in the Arduino IDE.
2. Tools → Board → **Arduino Leonardo** (works for Pro Micro too).
3. Tools → Port → the board's COM port.
4. Click Upload.
5. After upload, the board reboots as a real keyboard+mouse + serial.
   Note the COM port (it may renumber after upload — usually one above the
   programming port).
6. Configure `macro_recorder.py` as above.

## Wire protocol

The host sends ASCII lines, terminated by `\n`. The firmware replies only
to `PING` (with `PONG`). One command per line:

| Command          | Meaning                                         |
|------------------|-------------------------------------------------|
| `KD <vk>`        | Key down — `vk` is a Windows virtual-key code   |
| `KU <vk>`        | Key up                                          |
| `MA <x> <y>`     | Mouse move **absolute** (host screen pixels)    |
| `MR <dx> <dy>`   | Mouse move **relative**                          |
| `MD L\|R\|M`     | Mouse button down — Left, Right, Middle         |
| `MU L\|R\|M`     | Mouse button up                                 |
| `MW <dx> <dy>`   | Mouse wheel scroll (vertical = `dy`)            |
| `PING`           | Replies `PONG\n` — used by the host to detect   |
| `RESET`          | Release all keys / buttons (panic / safety)     |

The firmware emits a single `READY\n` line on boot.

## Wiring & physical setup

No wiring required beyond the USB cable. Plug the microcontroller into your
PC via USB. It will appear as:

- A composite HID device (keyboard + mouse), which the OS uses for *real input*
- A USB CDC serial port, which `macro_recorder.py` uses for *commands*

Both run over the same USB cable simultaneously.

## Troubleshooting

- **"No serial port configured"** — Edit `SERIAL_HID_PORT` at the top of
  `macro_recorder.py`, or set the `MACRO_SERIAL_PORT` environment variable.
- **The port number changes** — Windows can renumber CDC ports. Use
  Device Manager to confirm; or pick a friendly USB hub port and reuse it.
- **Pico keeps remounting CIRCUITPY** — that's normal. The HID part is
  unaffected.
- **Cursor "snaps" oddly** — both firmwares walk the cursor in -127..+127
  steps. If you want pixel-perfect absolute positioning on Windows, you'd
  need to extend the HID descriptors to advertise *Absolute* mouse axes
  (more involved; not done here).
- **Game doesn't react** — confirm the board is listed as an HID device:
  Device Manager → Human Interface Devices → "HID Keyboard Device" and
  "HID-compliant mouse" with the Pico/Arduino as the parent.

## Why not just `SendInput` or Interception?

| Backend       | Mouse moves visibly? | Hooks driver? | Works for raw-input games? | Anti-cheat-safe? |
|---------------|----------------------|---------------|----------------------------|------------------|
| Window Msgs   | No                   | No            | No                         | Yes              |
| pynput        | Yes                  | No            | Yes                        | Yes              |
| Interception  | No                   | **Yes**       | Yes                        | Often **flagged**|
| **Serial HID**| Yes (real device)    | No            | **Yes**                    | OS-trusted; ToS varies |

The hardware route is the only one that produces input the OS can't tell
from a real device, without installing kernel drivers.
