"""
Macro Recorder — Pi Pico HID firmware  (CircuitPython)
======================================================

This firmware turns a Raspberry Pi Pico (RP2040 / RP2350) into a real USB
HID keyboard + mouse that obeys serial commands from the macro_recorder.py
running on your PC.

INSTALL
-------
1. Install CircuitPython 9.x on the Pico (https://circuitpython.org/board/raspberry_pi_pico/).
   Hold BOOTSEL, plug in via USB, drag the .uf2 file onto the RPI-RP2 drive.
   The Pico reboots and shows up as the CIRCUITPY drive.

2. Copy these files into CIRCUITPY/lib/  (download Adafruit bundle from
   https://circuitpython.org/libraries):
     - adafruit_hid/        (the whole folder)

3. Save THIS file as `code.py` on CIRCUITPY (the root of the drive).
   The Pico will reboot and start running it automatically.

4. Find which COM port the Pico's CDC (serial) interface uses:
     - Windows: Device Manager → Ports (COM & LPT) → "USB Serial Device"
     - Note the COMx number — that's MACRO_SERIAL_PORT in the host script.

5. In macro_recorder.py set SERIAL_HID_PORT = "COMx" or set the env var:
       set MACRO_SERIAL_PORT=COM7    (Windows CMD)
       $env:MACRO_SERIAL_PORT="COM7" (PowerShell)

WIRE PROTOCOL  (host → device, one line each, terminated by \n)
---------------------------------------------------------------
   KD <vk>            key down  (vk = Windows virtual-key code, decimal)
   KU <vk>            key up
   MA <x> <y>         mouse move absolute  (screen pixels — host scales)
   MR <dx> <dy>       mouse move relative  (small deltas, +/- 127 ea.)
   MD L|R|M           mouse button down
   MU L|R|M           mouse button up
   MW <dx> <dy>       mouse wheel scroll
   PING               replies "PONG\n" — used by host to detect the device

The firmware echoes "READY\n" once on boot.

NOTES
-----
   * The Pico exposes itself as a STANDARD HID keyboard + mouse.  Windows
     trusts it like any other USB device.  This is what lets games and
     anti-cheat-aware apps see real input.
   * Absolute mouse positioning is implemented by tracking the device's
     "current" cursor position internally and sending RELATIVE deltas
     scaled to fit -127..+127 per HID report.  The host sends ABSOLUTE
     screen coords; the firmware walks the cursor there in steps.
"""

import time
import sys
import usb_hid
import supervisor

from adafruit_hid.keyboard import Keyboard
from adafruit_hid.mouse    import Mouse

# ── Setup HID devices ─────────────────────────────────────────────────────
keyboard = Keyboard(usb_hid.devices)
mouse    = Mouse(usb_hid.devices)

# ── Map Windows virtual-key codes to Adafruit HID keycodes ────────────────
# Only the most common keys are mapped; extend as needed.
from adafruit_hid.keycode import Keycode as K

VK_MAP = {
    # Letters
    **{0x41 + i: getattr(K, chr(ord("A") + i)) for i in range(26)},
    # Digits
    **{0x30 + i: getattr(K, f"_{i}" if i == 0 else f"_{i}") if hasattr(K, f"_{i}") else getattr(K, str(i), None)
       for i in range(10)},
    # Function keys
    **{0x70 + i: getattr(K, f"F{i+1}") for i in range(12) if hasattr(K, f"F{i+1}")},
    # Common keys
    0x08: K.BACKSPACE, 0x09: K.TAB,        0x0D: K.ENTER,
    0x10: K.SHIFT,     0x11: K.CONTROL,    0x12: K.ALT,
    0x14: K.CAPS_LOCK, 0x1B: K.ESCAPE,     0x20: K.SPACE,
    0x21: K.PAGE_UP,   0x22: K.PAGE_DOWN,  0x23: K.END,    0x24: K.HOME,
    0x25: K.LEFT_ARROW,0x26: K.UP_ARROW,   0x27: K.RIGHT_ARROW,
    0x28: K.DOWN_ARROW,
    0x2D: K.INSERT,    0x2E: K.DELETE,
    0x5B: K.LEFT_GUI,  0x5C: K.RIGHT_GUI,
    0xBA: K.SEMICOLON, 0xBB: K.EQUALS,
    0xBC: K.COMMA,     0xBD: K.MINUS,      0xBE: K.PERIOD,
    0xBF: K.FORWARD_SLASH,                 0xC0: K.GRAVE_ACCENT,
    0xDB: K.LEFT_BRACKET,                  0xDC: K.BACKSLASH,
    0xDD: K.RIGHT_BRACKET,                 0xDE: K.QUOTE,
}

MOUSE_BTN = {"L": Mouse.LEFT_BUTTON, "R": Mouse.RIGHT_BUTTON, "M": Mouse.MIDDLE_BUTTON}

# ── Absolute-cursor tracker  (Pico has no idea where the real cursor is,
#    but if we send only relative deltas the OS keeps a coherent position
#    once we move from a known origin — the host should send a centering
#    nudge if it cares about absolute positions across reboots.)
_cur_x, _cur_y = 0, 0


def goto_abs(target_x: int, target_y: int):
    """Walk the cursor from our tracked position to (target_x, target_y)."""
    global _cur_x, _cur_y
    dx = target_x - _cur_x
    dy = target_y - _cur_y
    # Send in chunks of -127..+127 per HID report
    while dx or dy:
        step_x = max(-127, min(127, dx))
        step_y = max(-127, min(127, dy))
        mouse.move(x=step_x, y=step_y)
        dx -= step_x; dy -= step_y
    _cur_x, _cur_y = target_x, target_y


def handle(line: str):
    global _cur_x, _cur_y
    parts = line.strip().split()
    if not parts: return
    cmd = parts[0].upper()
    try:
        if   cmd == "KD" and len(parts) == 2:
            kc = VK_MAP.get(int(parts[1]))
            if kc is not None: keyboard.press(kc)
        elif cmd == "KU" and len(parts) == 2:
            kc = VK_MAP.get(int(parts[1]))
            if kc is not None: keyboard.release(kc)
        elif cmd == "MA" and len(parts) == 3:
            goto_abs(int(parts[1]), int(parts[2]))
        elif cmd == "MR" and len(parts) == 3:
            dx, dy = int(parts[1]), int(parts[2])
            mouse.move(x=max(-127, min(127, dx)), y=max(-127, min(127, dy)))
            _cur_x += dx; _cur_y += dy
        elif cmd == "MD" and len(parts) == 2:
            b = MOUSE_BTN.get(parts[1].upper())
            if b: mouse.press(b)
        elif cmd == "MU" and len(parts) == 2:
            b = MOUSE_BTN.get(parts[1].upper())
            if b: mouse.release(b)
        elif cmd == "MW" and len(parts) == 3:
            mouse.move(wheel=int(parts[2]))   # vertical only on standard HID
        elif cmd == "PING":
            print("PONG")
        elif cmd == "RESET":
            keyboard.release_all(); mouse.release_all()
    except Exception as e:
        print("ERR", e)


# ── Main loop ─────────────────────────────────────────────────────────────
print("READY")
buf = ""
while True:
    n = supervisor.runtime.serial_bytes_available
    if n:
        chunk = sys.stdin.read(n)
        buf += chunk
        while "\n" in buf:
            line, buf = buf.split("\n", 1)
            handle(line)
    else:
        time.sleep(0.001)
