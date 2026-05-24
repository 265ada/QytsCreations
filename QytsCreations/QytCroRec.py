#!/usr/bin/env python3
"""
QytCroRec v1.10  (formerly Macro Recorder)
Background keyboard/mouse macro recorder and player.
  • Pluggable input backends: PostMessage, SendMessage (winmsg),
    API Hook (Detours), pynput, Interception driver, Serial HID.
  • Pick-window dialog lists every open window (HWND + PID + title).
  • Mouse coordinates are recorded in WINDOW-CLIENT space when a target
    window is set — playback is clamped to the window's client area.
  • Recording filters out keys currently bound as app shortcuts.
  • App shortcuts work GLOBALLY (pynput) — fire from any window.
  • Every settings change is saved immediately and re-applied live.
  • Auto-update on startup from a remote URL.
Install (minimum):     pip install PyQt6 pynput
Install (Interception):pip install interception-python   + driver from
                       https://github.com/oblitum/Interception
Install (Serial HID):  pip install pyserial             + flash firmware
                       from ./hid_firmware/ onto a Pi Pico or Arduino
"""

__version__ = "1.21"

# ── AUTO-UPDATE CONFIGURATION ────────────────────────────────────────────────
# Set these two URLs to enable auto-update.  See README at bottom of file.
UPDATE_VERSION_URL = "https://raw.githubusercontent.com/265ada/QytsCreations/main/QytsCreations/version.json"
UPDATE_SCRIPT_URL  = "https://raw.githubusercontent.com/265ada/QytsCreations/main/QytsCreations/QytCroRec.py"
AUTO_UPDATE_ENABLED = True   # set False to disable startup check

import sys, json, time, uuid, copy, threading, os, urllib.request, urllib.error
import winsound, subprocess, ctypes, ctypes.wintypes, dataclasses, faulthandler, traceback
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional

# Crash diagnostics — write any low-level segfault to a file we can read later.
_CRASH_LOG_PATH = Path.home() / ".macro_recorder" / "crash.log"
try:
    _CRASH_LOG_PATH.parent.mkdir(exist_ok=True)
    _crash_fp = open(_CRASH_LOG_PATH, "a", buffering=1, encoding="utf-8")
    _crash_fp.write(f"\n=== QytCroRec started {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
    faulthandler.enable(file=_crash_fp)
except Exception as _e:
    print(f"[crashlog] couldn't open {_CRASH_LOG_PATH}: {_e}")

def _log_crash(msg: str):
    """Append a Python-level error to the crash log."""
    try:
        with open(_CRASH_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    except Exception: pass

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QListWidget, QListWidgetItem, QLabel, QPushButton,
    QLineEdit, QSpinBox, QDoubleSpinBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QSystemTrayIcon, QMenu, QCheckBox, QTabWidget,
    QStatusBar, QMessageBox, QGroupBox, QKeySequenceEdit, QComboBox,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QRect
from PyQt6.QtGui import (
    QIcon, QColor, QFont, QPixmap, QPainter, QAction,
    QShortcut, QKeySequence,
)
from pynput import keyboard as kb_lib, mouse as ms_lib
from pynput.keyboard import Key, KeyCode
from pynput.mouse import Button


# ══════════════════════════════════════════════════════════════════════════════
# WIN32 SETUP
# ══════════════════════════════════════════════════════════════════════════════

_u32 = ctypes.windll.user32
_k32 = ctypes.windll.kernel32

# Set correct argtypes for functions that take POINT by value
_u32.VkKeyScanW.argtypes               = [ctypes.c_wchar]
_u32.VkKeyScanW.restype                = ctypes.c_short
_u32.WindowFromPoint.argtypes          = [ctypes.wintypes.POINT]
_u32.WindowFromPoint.restype           = ctypes.wintypes.HWND
_u32.ChildWindowFromPointEx.argtypes   = [ctypes.wintypes.HWND,
                                           ctypes.wintypes.POINT, ctypes.c_uint]
_u32.ChildWindowFromPointEx.restype    = ctypes.wintypes.HWND
_u32.ScreenToClient.argtypes           = [ctypes.wintypes.HWND,
                                           ctypes.POINTER(ctypes.wintypes.POINT)]
_u32.ScreenToClient.restype            = ctypes.c_bool
_u32.ClientToScreen.argtypes           = [ctypes.wintypes.HWND,
                                           ctypes.POINTER(ctypes.wintypes.POINT)]
_u32.ClientToScreen.restype            = ctypes.c_bool
_u32.GetClientRect.argtypes            = [ctypes.wintypes.HWND,
                                           ctypes.POINTER(ctypes.wintypes.RECT)]
_u32.GetClientRect.restype             = ctypes.c_bool
_u32.GetWindowThreadProcessId.argtypes = [ctypes.wintypes.HWND,
                                           ctypes.POINTER(ctypes.wintypes.DWORD)]
_u32.GetWindowThreadProcessId.restype  = ctypes.wintypes.DWORD
_k32.OpenProcess.argtypes              = [ctypes.wintypes.DWORD, ctypes.c_bool,
                                           ctypes.wintypes.DWORD]
_k32.OpenProcess.restype               = ctypes.wintypes.HANDLE
_k32.CloseHandle.argtypes              = [ctypes.wintypes.HANDLE]
_k32.CloseHandle.restype               = ctypes.c_bool
_k32.IsWow64Process.argtypes           = [ctypes.wintypes.HANDLE,
                                           ctypes.POINTER(ctypes.c_int)]
_k32.IsWow64Process.restype            = ctypes.c_bool
_k32.WaitNamedPipeW.argtypes           = [ctypes.c_wchar_p, ctypes.wintypes.DWORD]
_k32.WaitNamedPipeW.restype            = ctypes.c_bool
_u32.SendMessageW.restype              = ctypes.c_long
_u32.PostMessageW.restype              = ctypes.c_bool


# ══════════════════════════════════════════════════════════════════════════════
# DATA MODEL
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class Macro:
    id:                  str   = field(default_factory=lambda: str(uuid.uuid4()))
    name:                str   = "New Macro"
    events:              list  = field(default_factory=list)
    trigger_hotkey:      str   = ""
    repeat_count:        int   = 1       # 0 = loop forever
    speed_multiplier:    float = 1.0
    record_mouse_move:   bool  = True
    target_window_title: str   = ""
    target_window_2:     str   = ""
    target_window_3:     str   = ""
    use_target_window:   bool  = False
    input_backend:       str   = "auto"    # "auto" | "winmsg" | "pynput" | "interception" | "serial_hid"
    created_at:          float = field(default_factory=time.time)
    run_count:           int   = 0

    def clone(self) -> "Macro":
        m = copy.deepcopy(self)
        m.id = str(uuid.uuid4())
        m.name = f"{m.name} (copy)"
        m.created_at = time.time()
        return m


# ══════════════════════════════════════════════════════════════════════════════
# SHORTCUT CONFIG
# ══════════════════════════════════════════════════════════════════════════════

SHORTCUT_DEFS = [
    ("new_macro",       "New Macro",                  "Ctrl+N"),
    ("dup_macro",       "Duplicate Macro",             "Ctrl+D"),
    ("del_macro",       "Delete Macro",                "Ctrl+Delete"),
    ("undo_delete",     "Undo Delete Macro",           "Ctrl+Z"),
    ("toggle_record",   "Record / Stop Recording",     "Ctrl+R"),
    ("play",            "Play Macro",                  "F5"),
    ("stop",            "Stop Current Macro",          "F6"),
    ("stop_all",        "Stop All Macros",             "Ctrl+F6"),
    ("clear_events",    "Clear All Events",            "Ctrl+L"),
    ("capture_window",  "Capture Target Window (3 s)", "Ctrl+W"),
    ("del_events",      "Delete Selected Events",      "Delete"),
]
DEFAULT_SHORTCUTS = {k: v for k, _, v in SHORTCUT_DEFS}


# ══════════════════════════════════════════════════════════════════════════════
# PERSISTENCE
# ══════════════════════════════════════════════════════════════════════════════

_MACRO_FIELDS = {f.name for f in dataclasses.fields(Macro)}

class Storage:
    def __init__(self):
        self.dir = Path.home() / ".macro_recorder"
        self.dir.mkdir(exist_ok=True)
        self.macros_path    = self.dir / "macros.json"
        self.shortcuts_path = self.dir / "shortcuts.json"

    def save_macros(self, macros: list):
        data = json.dumps([asdict(m) for m in macros], indent=2)
        # Keep a rolling backup before overwriting
        bak = self.macros_path.with_suffix(".bak.json")
        if self.macros_path.exists():
            import shutil
            shutil.copy2(self.macros_path, bak)
        self.macros_path.write_text(data, encoding="utf-8")

    def load_macros(self) -> list:
        if not self.macros_path.exists():
            return []
        try:
            result = []
            for d in json.loads(self.macros_path.read_text(encoding="utf-8")):
                events = d.pop("events", [])
                m = Macro(**{k: v for k, v in d.items() if k in _MACRO_FIELDS})
                m.events = events
                result.append(m)
            return result
        except Exception as e:
            print(f"Macro load error: {e}")
            return []

    def save_shortcuts(self, sc: dict):
        self.shortcuts_path.write_text(json.dumps(sc, indent=2), encoding="utf-8")

    def load_shortcuts(self) -> dict:
        if not self.shortcuts_path.exists():
            return dict(DEFAULT_SHORTCUTS)
        try:
            saved = json.loads(self.shortcuts_path.read_text(encoding="utf-8"))
            # Merge with defaults so new actions always have a key
            out = dict(DEFAULT_SHORTCUTS)
            out.update(saved)
            return out
        except Exception:
            return dict(DEFAULT_SHORTCUTS)


# ══════════════════════════════════════════════════════════════════════════════
# KEY / BUTTON HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def key_to_str(key) -> str:
    if hasattr(key, "char") and key.char:
        return key.char
    return str(key)

def str_to_key(s: str):
    if not s: return None
    if len(s) == 1: return KeyCode.from_char(s)
    try: return getattr(Key, s.removeprefix("Key."))
    except AttributeError: return None

def str_to_button(s: str) -> Button:
    try: return getattr(Button, s.removeprefix("Button."))
    except AttributeError: return Button.left

_HOTKEY_ALIASES = {
    "ctrl":"ctrl","control":"ctrl","alt":"alt","shift":"shift",
    "win":"cmd","cmd":"cmd","meta":"cmd",
    "esc":"esc","escape":"esc",
    "del":"delete","delete":"delete",
    "return":"enter","enter":"enter",
    "ins":"insert","insert":"insert",
    "pgup":"page_up","pageup":"page_up",
    "pgdn":"page_down","pagedown":"page_down",
    "space":"space","backspace":"backspace","tab":"tab",
    "home":"home","end":"end",
    "up":"up","down":"down","left":"left","right":"right",
}

def user_hotkey_to_pynput(s: str) -> str:
    """Convert a user/Qt key string ('ctrl+f5', 'F8', 'Del') to pynput format."""
    if not s: return ""
    out = []
    for p in s.lower().strip().split("+"):
        p = p.strip()
        if not p: continue
        p = _HOTKEY_ALIASES.get(p, p)
        out.append(p if len(p) == 1 else f"<{p}>")
    return "+".join(out)

# Qt key sequences look the same as user input, so re-use the same converter
qt_to_pynput_hotkey = user_hotkey_to_pynput

def event_summary(ev: dict) -> str:
    d, t = ev["data"], ev["event_type"]
    if t in ("key_press", "key_release"): return d.get("key", "?")
    tag = "client" if d.get("coord_space") == "client" else "screen"
    xy  = f"({d['x']}, {d['y']}) [{tag}]"
    if t == "mouse_move": return xy
    if t == "mouse_click":
        arr = "↓" if d.get("pressed") else "↑"
        return f"{str(d.get('button','')).removeprefix('Button.')}{arr}  {xy}"
    if t == "mouse_scroll":
        return f"Δ({d.get('dx',0)}, {d.get('dy',0)})  @ {xy}"
    return str(d)


# ══════════════════════════════════════════════════════════════════════════════
# WINDOW HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def get_foreground_title() -> str:
    hwnd = _u32.GetForegroundWindow()
    n = _u32.GetWindowTextLengthW(hwnd)
    if n == 0: return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    _u32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value

def find_window_hwnd(title: str) -> Optional[int]:
    matches: list = []
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.c_long)
    def _cb(hwnd, _):
        if _u32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(256)
            _u32.GetWindowTextW(hwnd, buf, 256)
            if title.lower() in buf.value.lower() and buf.value:
                matches.append(hwnd)
        return True
    _u32.EnumWindows(WNDENUMPROC(_cb), 0)
    return matches[0] if matches else None

def enumerate_windows() -> list:
    """Return [(hwnd, pid, title), …] for every visible top-level window with a title."""
    results: list = []
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.c_long)
    def _cb(hwnd, _):
        if _u32.IsWindowVisible(hwnd):
            n = _u32.GetWindowTextLengthW(hwnd)
            if n > 0:
                buf = ctypes.create_unicode_buffer(n + 1)
                _u32.GetWindowTextW(hwnd, buf, n + 1)
                if buf.value.strip():
                    pid = get_window_pid(hwnd)
                    results.append((int(hwnd), int(pid), buf.value))
        return True
    _u32.EnumWindows(WNDENUMPROC(_cb), 0)
    # Sort by title for predictable ordering
    results.sort(key=lambda r: r[2].lower())
    return results

def screen_to_client(hwnd: int, sx: int, sy: int):
    pt = ctypes.wintypes.POINT(sx, sy)
    _u32.ScreenToClient(hwnd, ctypes.byref(pt))
    return pt.x, pt.y

def client_to_screen(hwnd: int, cx: int, cy: int):
    pt = ctypes.wintypes.POINT(cx, cy)
    _u32.ClientToScreen(hwnd, ctypes.byref(pt))
    return pt.x, pt.y

def client_rect(hwnd: int):
    """Return (width, height) of the target window's client area."""
    r = ctypes.wintypes.RECT()
    _u32.GetClientRect(hwnd, ctypes.byref(r))
    return r.right, r.bottom

def get_window_pid(hwnd: int) -> int:
    pid = ctypes.wintypes.DWORD(0)
    _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value

def pack_lparam(x: int, y: int) -> int:
    return ((y & 0xFFFF) << 16) | (x & 0xFFFF)


# ══════════════════════════════════════════════════════════════════════════════
# TTS + SOUND
# ══════════════════════════════════════════════════════════════════════════════

def speak(text: str):
    safe = text.replace('"','').replace("'",'').replace('`','').replace('\\','')[:200]
    script = (
        "Add-Type -AssemblyName System.Speech; "
        f'(New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak("{safe}")'
    )
    subprocess.Popen(
        ["powershell", "-WindowStyle", "Hidden", "-NonInteractive", "-Command", script],
        creationflags=subprocess.CREATE_NO_WINDOW,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

def _beep(*pairs):
    threading.Thread(target=lambda: [winsound.Beep(f, d) for f, d in pairs], daemon=True).start()

sound_record_start  = lambda: _beep((600, 80), (900, 130))
sound_record_stop   = lambda: _beep((900, 80), (600, 130))
sound_play_start    = lambda: _beep((700, 120))
sound_play_stop     = lambda: _beep((450, 180))


# ══════════════════════════════════════════════════════════════════════════════
# INPUT BACKEND — pluggable abstraction
#   Every playback backend implements these five methods.  PlayerThread asks
#   the chosen backend to deliver each event.  This is what lets us swap
#   between window-message injection, pynput, kernel-level Interception, or
#   a real USB HID device (Arduino / Pi Pico) without touching playback code.
# ══════════════════════════════════════════════════════════════════════════════

from abc import ABC, abstractmethod

class InputBackend(ABC):
    name: str = "abstract"

    @abstractmethod
    def key_down  (self, key_str: str): ...
    @abstractmethod
    def key_up    (self, key_str: str): ...
    @abstractmethod
    def mouse_move(self, sx: int, sy: int): ...
    @abstractmethod
    def mouse_button(self, sx: int, sy: int, button_str: str, pressed: bool): ...
    @abstractmethod
    def mouse_scroll(self, sx: int, sy: int, dx: int, dy: int): ...

    def close(self): pass   # release any resources

    @staticmethod
    def available() -> bool:
        """Whether this backend can run on the current machine."""
        return True


# ══════════════════════════════════════════════════════════════════════════════
# BACKGROUND INPUT INJECTOR
#   Uses SendMessage + AttachThreadInput to deliver events directly into the
#   target window's message queue WITHOUT moving the mouse or stealing focus.
#
#   Keyboard: AttachThreadInput → GetFocus → find the focused child control →
#             SendMessage(WM_KEYDOWN + WM_CHAR + WM_KEYUP)
#   Mouse:    ChildWindowFromPointEx → find deepest child at click coords →
#             SendMessage(WM_LBUTTONDOWN / UP etc.)
#   Scroll:   PostMessage(WM_MOUSEWHEEL) — async is fine for scroll
#   Move:     PostMessage(WM_MOUSEMOVE)  — async is fine for move
# ══════════════════════════════════════════════════════════════════════════════

WM_KEYDOWN     = 0x0100
WM_KEYUP       = 0x0101
WM_CHAR        = 0x0102
WM_MOUSEMOVE   = 0x0200
WM_LBUTTONDOWN = 0x0201;  WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204;  WM_RBUTTONUP = 0x0205
WM_MBUTTONDOWN = 0x0207;  WM_MBUTTONUP = 0x0208
WM_MOUSEWHEEL  = 0x020A;  WM_MOUSEHWHEEL = 0x020E

_EXTENDED_VK = {
    0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28,
    0x2D, 0x2E, 0x5B, 0x5C, 0x5D, 0x6F, 0xA3, 0xA5,
}

_MOUSE_MSGS = {
    ("left",   True):  (WM_LBUTTONDOWN, 0x0001),
    ("left",   False): (WM_LBUTTONUP,   0),
    ("right",  True):  (WM_RBUTTONDOWN,  0x0002),
    ("right",  False): (WM_RBUTTONUP,    0),
    ("middle", True):  (WM_MBUTTONDOWN,  0x0010),
    ("middle", False): (WM_MBUTTONUP,    0),
}


def _key_str_to_vk(key_str: str) -> Optional[int]:
    if not key_str: return None
    if len(key_str) == 1:
        try:
            vk = KeyCode.from_char(key_str).vk
            if vk is not None: return vk
        except Exception: pass
        try:
            res = _u32.VkKeyScanW(key_str[0])
            return (res & 0xFF) if res != -1 else None
        except Exception: return None
    name = key_str.removeprefix("Key.")
    try:
        return getattr(getattr(Key, name).value, "vk", None)
    except AttributeError: return None


def _make_key_lparam(vk: int, is_up: bool) -> int:
    scan = _u32.MapVirtualKeyW(vk, 0)
    lp = 1 | ((scan & 0xFF) << 16)
    if vk in _EXTENDED_VK: lp |= (1 << 24)
    if is_up: lp |= (1 << 30) | (1 << 31)
    return lp


class BackgroundInjector(InputBackend):
    """
    Sends all events into a specific window via SendMessage.
    Physical mouse does NOT move.  User's keyboard focus is NOT stolen.
    Use this for normal Win32 / .NET / WPF apps.  Will NOT work for games
    that read DirectInput or Raw Input (use SerialHIDBackend instead).
    """
    name = "winmsg"

    def __init__(self, hwnd: int):
        self.hwnd = hwnd
        self._tid = _u32.GetWindowThreadProcessId(hwnd, None)

    # ── Find the correct child to target ─────────────────────────────────────

    def _keyboard_hwnd(self) -> int:
        """
        Return the control that currently has focus inside the target window.
        Uses AttachThreadInput so GetFocus returns the correct value for the
        target thread rather than our own.
        """
        my_tid = _k32.GetCurrentThreadId()
        if self._tid == my_tid:
            return _u32.GetFocus() or self.hwnd
        _u32.AttachThreadInput(my_tid, self._tid, True)
        h = _u32.GetFocus()
        _u32.AttachThreadInput(my_tid, self._tid, False)
        return h if h else self.hwnd

    def _click_hwnd(self, sx: int, sy: int):
        """
        Return (hwnd, client_x, client_y) for the DEEPEST child window
        visible at screen coordinates (sx, sy).  Walks the child tree
        recursively because ChildWindowFromPointEx only descends one level.
        """
        target = self.hwnd
        cx, cy = screen_to_client(target, sx, sy)
        # Recursive descent — many apps nest controls 3-4 levels deep
        for _ in range(8):    # bounded loop, never infinite
            try:
                pt = ctypes.wintypes.POINT(cx, cy)
                child = _u32.ChildWindowFromPointEx(target, pt, 1)  # skip invisible
            except Exception:
                break
            if not child or child == target:
                break
            target = child
            cx, cy = screen_to_client(target, sx, sy)
        return target, cx, cy

    # ── Event dispatchers ─────────────────────────────────────────────────────

    def key_down(self, key_str: str):
        vk = _key_str_to_vk(key_str)
        if not vk: return
        h   = self._keyboard_hwnd()
        lp  = _make_key_lparam(vk, False)
        _u32.SendMessageW(h, WM_KEYDOWN, vk, lp)
        # WM_CHAR delivers the printable character to text-processing controls
        if len(key_str) == 1:
            _u32.SendMessageW(h, WM_CHAR, ord(key_str), lp)

    def key_up(self, key_str: str):
        vk = _key_str_to_vk(key_str)
        if not vk: return
        _u32.SendMessageW(self._keyboard_hwnd(), WM_KEYUP, vk, _make_key_lparam(vk, True))

    def mouse_move(self, sx: int, sy: int):
        cx, cy = screen_to_client(self.hwnd, sx, sy)
        _u32.PostMessageW(self.hwnd, WM_MOUSEMOVE, 0, pack_lparam(cx, cy))

    def mouse_button(self, sx: int, sy: int, button_str: str, pressed: bool):
        h, cx, cy = self._click_hwnd(sx, sy)
        lp  = pack_lparam(cx, cy)
        btn = button_str.removeprefix("Button.")
        msg, wp = _MOUSE_MSGS.get((btn, pressed), (0, 0))
        if not msg: return
        # Many controls (buttons, hyperlinks) need a hover before they
        # accept a click — send a MOUSEMOVE first.
        if pressed:
            _u32.SendMessageW(h, WM_MOUSEMOVE, 0, lp)
        _u32.SendMessageW(h, msg, wp, lp)
        # WM_PARENTNOTIFY helps some controls (older common controls)
        # acknowledge the click came from a child.
        parent = _u32.GetParent(h)
        if parent and parent != h:
            _u32.SendMessageW(parent, 0x0210, msg, pack_lparam(sx, sy))  # WM_PARENTNOTIFY

    def mouse_scroll(self, sx: int, sy: int, dx: int, dy: int):
        if dy:
            delta = int(dy * 120)
            wp = ctypes.c_uint(((delta & 0xFFFF) << 16)).value
            lp = ctypes.c_uint(((sy & 0xFFFF) << 16) | (sx & 0xFFFF)).value
            _u32.PostMessageW(self.hwnd, WM_MOUSEWHEEL, wp, lp)
        if dx:
            delta = int(dx * 120)
            wp = ctypes.c_uint(((delta & 0xFFFF) << 16)).value
            lp = ctypes.c_uint(((sy & 0xFFFF) << 16) | (sx & 0xFFFF)).value
            _u32.PostMessageW(self.hwnd, WM_MOUSEHWHEEL, wp, lp)


# ══════════════════════════════════════════════════════════════════════════════
# POST-MESSAGE BACKEND — same as winmsg but ASYNC (PostMessage everywhere).
#
# Some apps & older game launchers process their message queue at their own
# cadence and reject SYNC (SendMessage) input from another thread (the call
# would block the target).  PostMessage just enqueues the message and
# returns immediately, which works better for these.  Trade-off: there's
# no confirmation the target processed each event, so timing-sensitive
# scripts may need a small extra delay between events.
# ══════════════════════════════════════════════════════════════════════════════

class PostMessageBackend(BackgroundInjector):
    name = "postmsg"

    def key_down(self, key_str: str):
        vk = _key_str_to_vk(key_str)
        if not vk: return
        h  = self._keyboard_hwnd()
        lp = _make_key_lparam(vk, False)
        _u32.PostMessageW(h, WM_KEYDOWN, vk, lp)
        if len(key_str) == 1:
            _u32.PostMessageW(h, WM_CHAR, ord(key_str), lp)

    def key_up(self, key_str: str):
        vk = _key_str_to_vk(key_str)
        if not vk: return
        _u32.PostMessageW(self._keyboard_hwnd(), WM_KEYUP, vk,
                          _make_key_lparam(vk, True))

    def mouse_button(self, sx: int, sy: int, button_str: str, pressed: bool):
        h, cx, cy = self._click_hwnd(sx, sy)
        lp  = pack_lparam(cx, cy)
        btn = button_str.removeprefix("Button.")
        msg, wp = _MOUSE_MSGS.get((btn, pressed), (0, 0))
        if not msg: return
        if pressed:
            _u32.PostMessageW(h, WM_MOUSEMOVE, 0, lp)
        _u32.PostMessageW(h, msg, wp, lp)


# ══════════════════════════════════════════════════════════════════════════════
# PYNPUT BACKEND — uses SendInput, drives the REAL system mouse / keyboard.
# Use when there is no target window or for games that need real input but
# the user does NOT mind their cursor moving during playback.
# ══════════════════════════════════════════════════════════════════════════════

class PynputBackend(InputBackend):
    name = "pynput"

    def __init__(self):
        self._kb = kb_lib.Controller()
        self._ms = ms_lib.Controller()

    def key_down(self, key_str):
        k = str_to_key(key_str)
        if k:
            try: self._kb.press(k)
            except Exception: pass

    def key_up(self, key_str):
        k = str_to_key(key_str)
        if k:
            try: self._kb.release(k)
            except Exception: pass

    def mouse_move(self, sx, sy):
        try: self._ms.position = (int(sx), int(sy))
        except Exception: pass

    def mouse_button(self, sx, sy, button_str, pressed):
        try:
            self._ms.position = (int(sx), int(sy))
            btn = str_to_button(button_str)
            (self._ms.press if pressed else self._ms.release)(btn)
        except Exception: pass

    def mouse_scroll(self, sx, sy, dx, dy):
        try: self._ms.scroll(int(dx), int(dy))
        except Exception: pass


# ══════════════════════════════════════════════════════════════════════════════
# INTERCEPTION BACKEND — kernel-level input via the Interception driver.
#
#   Anti-cheat NOTE: Interception is often flagged by anti-cheat systems.
#   Use this only for apps / games where you have authorization to automate
#   input (single-player, accessibility, private servers, testing rigs, etc.).
#
#   Install:
#     1. Download Interception (https://github.com/oblitum/Interception)
#        Run `install-interception.exe /install` from an admin terminal.
#        Reboot.
#     2. pip install interception-python      (wraps the driver)
# ══════════════════════════════════════════════════════════════════════════════

try:
    import interception as _interception_mod   # interception-python on PyPI
    _INTERCEPTION_OK = True
except Exception:
    _interception_mod = None
    _INTERCEPTION_OK = False


class InterceptionBackend(InputBackend):
    name = "interception"

    @staticmethod
    def available() -> bool:
        return _INTERCEPTION_OK

    def __init__(self):
        if not _INTERCEPTION_OK:
            raise RuntimeError(
                "interception-python is not installed.  Install the Interception "
                "driver (https://github.com/oblitum/Interception) and run "
                "`pip install interception-python`.")
        # Auto-capture keyboard + mouse devices the first time we use them
        _interception_mod.auto_capture_devices(keyboard=True, mouse=True)

    def key_down(self, key_str):
        try: _interception_mod.key_down(self._k(key_str))
        except Exception as e: print(f"[interception] key_down: {e}")

    def key_up(self, key_str):
        try: _interception_mod.key_up(self._k(key_str))
        except Exception as e: print(f"[interception] key_up: {e}")

    def mouse_move(self, sx, sy):
        try: _interception_mod.move_to(int(sx), int(sy))
        except Exception as e: print(f"[interception] move: {e}")

    def mouse_button(self, sx, sy, button_str, pressed):
        try:
            _interception_mod.move_to(int(sx), int(sy))
            btn = button_str.removeprefix("Button.")
            fn = (_interception_mod.mouse_down if pressed
                  else _interception_mod.mouse_up)
            fn(btn)
        except Exception as e: print(f"[interception] click: {e}")

    def mouse_scroll(self, sx, sy, dx, dy):
        try: _interception_mod.scroll("up" if dy >= 0 else "down", abs(int(dy)) or 1)
        except Exception as e: print(f"[interception] scroll: {e}")

    @staticmethod
    def _k(key_str: str) -> str:
        """Translate our key encoding to interception's key name."""
        if len(key_str) == 1: return key_str.lower()
        name = key_str.removeprefix("Key.").lower()
        return {"esc":"escape","return":"enter","del":"delete",
                "pgup":"page_up","pgdn":"page_down","ins":"insert"}.get(name, name)


# ══════════════════════════════════════════════════════════════════════════════
# SERIAL HID BACKEND — sends commands over USB serial to a microcontroller
# (Arduino Pro Micro, Teensy, Raspberry Pi Pico) that emulates a REAL USB
# keyboard + mouse.  The host OS sees genuine HID hardware.
#
#   This is the most robust option for games and for any app that ignores
#   software-injected input.  Firmware is provided in:
#       hid_firmware/pico_circuitpython.py   (Pi Pico — easiest)
#       hid_firmware/promicro_arduino.ino    (Arduino Pro Micro / Leonardo)
#
#   Wire protocol (one ASCII command per line):
#       KD <vk>            key down  (vk = Windows virtual-key code)
#       KU <vk>            key up
#       MA <x> <y>         mouse move absolute  (screen pixels)
#       MR <dx> <dy>       mouse move relative
#       MD <btn>           mouse button down    (L | R | M)
#       MU <btn>           mouse button up
#       MW <dx> <dy>       mouse wheel scroll
#
#   Setup:
#     pip install pyserial
#     Edit SERIAL_HID_PORT below (or set MACRO_SERIAL_PORT env var).
# ══════════════════════════════════════════════════════════════════════════════

SERIAL_HID_PORT     = ""        # e.g. "COM5"; empty = use $MACRO_SERIAL_PORT
SERIAL_HID_BAUDRATE = 115200

try:
    import serial as _pyserial   # pyserial
    _SERIAL_OK = True
except Exception:
    _pyserial = None
    _SERIAL_OK = False


class SerialHIDBackend(InputBackend):
    name = "serial_hid"
    _shared_port: Optional[object] = None    # one port shared across instances

    @staticmethod
    def available() -> bool:
        return _SERIAL_OK

    def __init__(self):
        if not _SERIAL_OK:
            raise RuntimeError("pyserial not installed.  Run: pip install pyserial")
        port = SERIAL_HID_PORT or os.environ.get("MACRO_SERIAL_PORT", "")
        if not port:
            raise RuntimeError(
                "No serial port configured.  Set SERIAL_HID_PORT at top of "
                "macro_recorder.py or the MACRO_SERIAL_PORT environment variable "
                "to your microcontroller's COM port (e.g. 'COM5').")
        # Reuse a single port if one is already open
        if SerialHIDBackend._shared_port is None:
            SerialHIDBackend._shared_port = _pyserial.Serial(
                port, SERIAL_HID_BAUDRATE, timeout=0.05, write_timeout=0.2)
            time.sleep(1.5)   # wait for board reset after open
        self._port = SerialHIDBackend._shared_port

    def _send(self, line: str):
        try:
            self._port.write((line + "\n").encode("ascii", "ignore"))
        except Exception as e:
            print(f"[serial] write failed: {e}")

    def key_down(self, key_str):
        vk = _key_str_to_vk(key_str)
        if vk: self._send(f"KD {vk}")

    def key_up(self, key_str):
        vk = _key_str_to_vk(key_str)
        if vk: self._send(f"KU {vk}")

    def mouse_move(self, sx, sy):
        self._send(f"MA {int(sx)} {int(sy)}")

    def mouse_button(self, sx, sy, button_str, pressed):
        self._send(f"MA {int(sx)} {int(sy)}")
        btn_map = {"left":"L","right":"R","middle":"M"}
        b = btn_map.get(button_str.removeprefix("Button."), "L")
        self._send(f"{'MD' if pressed else 'MU'} {b}")

    def mouse_scroll(self, sx, sy, dx, dy):
        self._send(f"MW {int(dx)} {int(dy)}")

    def close(self):
        # Don't close the shared port — other macros may still need it.
        pass


# ══════════════════════════════════════════════════════════════════════════════
# DETOURS / API-HOOK BACKEND
#
# This backend talks to a C++ hook DLL that has been injected into the
# target process.  The DLL uses Microsoft Detours to intercept DirectInput
# (dinput8.dll) calls (GetDeviceState / GetDeviceData) and Win32 raw-input
# polling (GetAsyncKeyState / GetKeyState) — returning a FAKE state derived
# from commands we send it over a per-process named pipe.
#
# Compared to other backends:
#   • Works for games that read DirectInput / RawInput (PostMessage doesn't).
#   • The fake input is delivered IN-PROCESS, so the real mouse never moves.
#   • Requires building a native DLL and injecting it (see hooks/dinput_hook/).
#   • Anti-cheat in commercial multiplayer games will flag this — DO NOT
#     use it to defeat anti-cheat.  Single-player, private servers,
#     accessibility, and automated testing of your own apps are fine.
#
# Pipe name:   \\.\pipe\macro_hook_<pid>
# Protocol:    same ASCII commands as Serial HID (KD/KU/MA/MR/MD/MU/MW).
# ══════════════════════════════════════════════════════════════════════════════

HOOK_PIPE_PREFIX  = r"\\.\pipe\macro_hook"

def _bundled_path(rel: str) -> Path:
    """
    Look first in the PyInstaller bundle (sys._MEIPASS), then alongside the
    script.  Lets the single-exe build find hooks/dinput_hook/*.dll and *.exe.
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        p = Path(meipass) / rel
        if p.exists(): return p
    return Path(__file__).resolve().parent / rel

HOOK_DIR          = _bundled_path("hooks/dinput_hook")
HOOK_DLL_X64      = HOOK_DIR / "dinput_hook_x64.dll"
HOOK_DLL_X86      = HOOK_DIR / "dinput_hook_x86.dll"
HOOK_INJECTOR_X64 = HOOK_DIR / "injector_x64.exe"
HOOK_INJECTOR_X86 = HOOK_DIR / "injector_x86.exe"


def _is_process_64bit(pid: int) -> Optional[bool]:
    """
    Return True if the target process is 64-bit, False if 32-bit, None if
    unknown.  Uses IsWow64Process — a WOW64 process is 32-bit running on
    64-bit Windows; everything else on this OS is 64-bit native.
    """
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    h = _k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        is_wow = ctypes.c_int(0)
        if not _k32.IsWow64Process(h, ctypes.byref(is_wow)):
            return None
        # If WOW64, target is 32-bit.  Otherwise it matches the OS — which on
        # this codebase (Win10/11 x64) means 64-bit.
        return not bool(is_wow.value)
    finally:
        _k32.CloseHandle(h)


def _artifacts_for_pid(pid: int) -> tuple:
    """Return (dll_path, injector_path, arch_label) for the target's bitness."""
    is64 = _is_process_64bit(pid)
    # Default to host arch when detection fails — host here is x64
    if is64 is None or is64:
        return HOOK_DLL_X64, HOOK_INJECTOR_X64, "x64"
    return HOOK_DLL_X86, HOOK_INJECTOR_X86, "x86"


def _pipe_exists(pid: int) -> bool:
    """
    NON-DISRUPTIVE check: does the named pipe for this PID exist?
    Uses WaitNamedPipeW(timeout=0) — does NOT open a client handle, so it
    can't accidentally consume the server's only pipe instance.  Returns
    True both when the pipe is available and when it exists but is busy
    (ERROR_SEM_TIMEOUT = 121).
    """
    if not pid: return False
    name = f"{HOOK_PIPE_PREFIX}_{pid}"
    if _k32.WaitNamedPipeW(name, 0):
        return True
    # ctypes.GetLastError reads the thread-local LastError set by the API
    return ctypes.GetLastError() == 121   # ERROR_SEM_TIMEOUT — exists but busy


def _eject_hook(pid: int, timeout: float = 4.0) -> tuple:
    """
    Try to unload our hook DLL from the target PID.  Returns (ok, msg).
    "Not loaded" is treated as success — we only care that the slot is clear.
    """
    dll, injector, arch = _artifacts_for_pid(pid)
    if not injector.exists():
        return False, f"{injector.name} not found"
    try:
        proc = subprocess.run(
            [str(injector), "--eject", str(pid), dll.name],
            capture_output=True, text=True, timeout=timeout)
    except Exception as e:
        _log_crash(f"[eject] launch failed: {e}")
        return False, f"eject launch failed: {e}"
    out = (proc.stdout or "").strip()
    _log_crash(f"[eject pid={pid}] exit={proc.returncode}  out={out!r}")
    # Exit codes:  0 = ejected, 4 = wasn't loaded (treat as success)
    if proc.returncode in (0, 4):
        # Wait for the pipe to actually disappear after DLL_PROCESS_DETACH.
        # Allow a generous window — Detours unhooks + thread shutdown can
        # take >100 ms in larger processes.
        gone = False
        for _ in range(30):
            if not _pipe_exists(pid):
                gone = True
                break
            time.sleep(0.1)
        _log_crash(f"[eject pid={pid}] pipe-gone-confirmed={gone}")
        return True, out or "ejected"
    return False, f"injector eject exit {proc.returncode}: {out}"


def _inject_hook(pid: int, timeout: float = 4.0, force_reload: bool = False) -> tuple:
    """
    Inject the matching-bitness hook DLL into the target PID.

    If the pipe already exists, the DLL is already loaded — we REUSE it.
    This avoids the FreeLibrary-race that crashes MHO on aggressive
    eject-then-inject cycles.

    Pass force_reload=True to explicitly eject + re-inject (e.g. after a
    DLL rebuild).  This still has a small crash risk but is opt-in.
    """
    dll, injector, arch = _artifacts_for_pid(pid)
    if not dll.exists():
        return False, (f"{dll.name} not found (target is {arch}).\n"
                       f"Build it: cd {HOOK_DIR}\n"
                       f"          (use the matching VS prompt for {arch})")
    if not injector.exists():
        return False, (f"{injector.name} not found.\n"
                       f"Build it: cd {HOOK_DIR}")

    if _pipe_exists(pid) and not force_reload:
        # DLL already loaded — reuse it.  Safe and avoids the unload race.
        return True, f"Hook already injected ({arch}) — reusing."

    if force_reload:
        _eject_hook(pid, timeout=timeout)

    try:
        proc = subprocess.run(
            [str(injector), str(pid), str(dll)],
            capture_output=True, text=True, timeout=timeout)
    except Exception as e:
        return False, f"{injector.name} failed to launch: {e}"
    if proc.returncode != 0:
        return False, (f"{injector.name} exit {proc.returncode}: "
                       f"{proc.stdout.strip()} {proc.stderr.strip()}")
    for _ in range(20):
        if _pipe_exists(pid):
            return True, f"Hook injected ({arch}) and pipe is up."
        time.sleep(0.1)
    return False, (f"Injector ran but pipe never appeared.  Target arch detected "
                   f"as {arch} — try the other build if this is wrong.")


class DetoursBackend(InputBackend):
    name = "detours"

    def __init__(self, target_pid: Optional[int] = None):
        if not target_pid:
            raise RuntimeError(
                "Detours backend needs a target window with a PID.  "
                "Set Force Target Window and pick one from the list.")
        # Truncate dll_hook.log so the user can read exactly what THIS run did.
        try:
            log = Path.home() / ".macro_recorder" / "dll_hook.log"
            if log.exists(): log.write_text("", encoding="utf-8")
        except Exception: pass
        # ALWAYS run inject — _inject_hook handles eject-then-inject so the
        # latest DLL build is what ends up loaded.  Skipping when the pipe
        # already exists would leave a stale DLL in place after a rebuild.
        ok, msg = _inject_hook(target_pid)
        if not ok:
            raise RuntimeError(msg)
        pipe_name = f"{HOOK_PIPE_PREFIX}_{target_pid}"
        # Retry-with-backoff: the server may briefly be between accepting
        # connections (DisconnectNamedPipe → next ConnectNamedPipe race).
        last_err = None
        for delay in (0, 0.05, 0.1, 0.2, 0.4, 0.8):
            if delay: time.sleep(delay)
            try:
                self._pipe = open(pipe_name, "w+b", buffering=0)
                return
            except FileNotFoundError as e:
                last_err = e
                continue
            except Exception as e:
                last_err = e
                break
        raise RuntimeError(f"Cannot open hook pipe {pipe_name}: {last_err}")

    def _send(self, line: str):
        # _broken flag: once a write fails (pipe died / game closed), don't
        # keep trying — that's how we used to crash on Stop.
        if getattr(self, "_broken", False): return
        try:
            self._pipe.write((line + "\n").encode("ascii", "ignore"))
            self._pipe.flush()
        except Exception as e:
            self._broken = True
            _log_crash(f"[detours] write failed: {e}")

    def key_down(self, key_str):
        vk = _key_str_to_vk(key_str)
        if vk: self._send(f"KD {vk}")

    def key_up(self, key_str):
        vk = _key_str_to_vk(key_str)
        if vk: self._send(f"KU {vk}")

    def mouse_move(self, sx, sy):
        # The hook DLL receives coords in whatever space the host sends.
        # PlayerThread already converted client→screen for us.
        self._send(f"MA {int(sx)} {int(sy)}")

    def mouse_button(self, sx, sy, button_str, pressed):
        self._send(f"MA {int(sx)} {int(sy)}")
        b = {"left":"L","right":"R","middle":"M"}.get(
            button_str.removeprefix("Button."), "L")
        self._send(f"{'MD' if pressed else 'MU'} {b}")

    def mouse_scroll(self, sx, sy, dx, dy):
        self._send(f"MW {int(dx)} {int(dy)}")

    def close(self):
        # First send RESET so the DLL releases all virtual keys / buttons —
        # otherwise a half-played macro can leave keys held in the game.
        try:
            if self._pipe and not getattr(self, "_broken", False):
                self._pipe.write(b"RESET\n")
                self._pipe.flush()
        except Exception as e:
            _log_crash(f"[detours] RESET on close failed: {e}")
        # Then close the file handle.  Guard against double-close.
        try:
            if self._pipe and not self._pipe.closed:
                self._pipe.close()
        except Exception as e:
            _log_crash(f"[detours] close failed: {e}")
        finally:
            self._pipe = None


# ══════════════════════════════════════════════════════════════════════════════
# MULTI-WINDOW BACKEND — fans out identical events to 2 or 3 target windows
# simultaneously.  Each sub-backend is a regular InputBackend; errors in one
# window do not stop delivery to the others.
# ══════════════════════════════════════════════════════════════════════════════

class MultiWindowBackend(InputBackend):
    """Dispatches every event to a list of concrete backends in order."""
    name = "multi"

    def __init__(self, backends: list):
        self._backends = [b for b in backends if b is not None]

    def key_down(self, key_str):
        for b in self._backends:
            try: b.key_down(key_str)
            except Exception: pass

    def key_up(self, key_str):
        for b in self._backends:
            try: b.key_up(key_str)
            except Exception: pass

    def mouse_move(self, sx, sy):
        for b in self._backends:
            try: b.mouse_move(sx, sy)
            except Exception: pass

    def mouse_button(self, sx, sy, button_str, pressed):
        for b in self._backends:
            try: b.mouse_button(sx, sy, button_str, pressed)
            except Exception: pass

    def mouse_scroll(self, sx, sy, dx, dy):
        for b in self._backends:
            try: b.mouse_scroll(sx, sy, dx, dy)
            except Exception: pass

    def close(self):
        for b in self._backends:
            try: b.close()
            except Exception: pass


# ══════════════════════════════════════════════════════════════════════════════
# BACKEND FACTORY — resolves the backend selected on a macro
# ══════════════════════════════════════════════════════════════════════════════

# (name, description, requires_target_window)
BACKEND_CHOICES = [
    ("auto",         "Auto (best available)",                                  False),
    ("postmsg",      "PostMessage — async queue, game windows",                True ),
    ("winmsg",       "Window Messages — silent, normal apps",                  True ),
    ("detours",      "API Hook (Detours) — DirectInput / RawInput games",      True ),
    ("pynput",       "pynput — moves real mouse, any app",                     False),
    ("interception", "Interception driver — kernel input",                     False),
    ("serial_hid",   "Serial HID — real USB device (Arduino / Pi Pico)",       False),
]

def _detours_pipe_ready(pid: int) -> bool:
    """True if the hook DLL is already injected and serving its pipe."""
    return _pipe_exists(pid)


# Module-global so PlayerThread can read the most recent failure reason.
_LAST_BACKEND_ERROR: str = ""


def create_backend(macro: "Macro") -> Optional[InputBackend]:
    """
    Resolve a Macro's input_backend setting to a concrete InputBackend.
    Returns None if no usable backend could be created.  The reason is
    stored in _LAST_BACKEND_ERROR so the UI can show something better than
    "Playback complete" when nothing actually played.
    """
    global _LAST_BACKEND_ERROR
    _LAST_BACKEND_ERROR = ""

    choice = (macro.input_backend or "auto").lower()

    def _hwnd_for_macro() -> Optional[int]:
        if not (macro.use_target_window and macro.target_window_title): return None
        return find_window_hwnd(macro.target_window_title)

    def _pid_for_macro() -> Optional[int]:
        h = _hwnd_for_macro()
        return get_window_pid(h) if h else None

    # Explicit-choice builders RAISE on failure so the reason propagates up.
    def _try_winmsg_explicit():
        h = _hwnd_for_macro()
        if not h:
            raise RuntimeError("Target window not found — pick a window or uncheck Force Target Window.")
        return BackgroundInjector(h)

    def _try_postmsg_explicit():
        h = _hwnd_for_macro()
        if not h:
            raise RuntimeError("Target window not found.")
        return PostMessageBackend(h)

    def _try_detours_explicit():
        """User explicitly picked Detours — auto-inject if needed."""
        pid = _pid_for_macro()
        if not pid:
            raise RuntimeError(
                "Detours needs a target window PID.  Tick 'Force Target Window' "
                "and pick a window first.")
        # DetoursBackend.__init__ injects the hook if the pipe isn't up.
        return DetoursBackend(pid)

    builders = {
        "winmsg":       _try_winmsg_explicit,
        "postmsg":      _try_postmsg_explicit,
        "detours":      _try_detours_explicit,
        "pynput":       lambda: PynputBackend(),
        "interception": lambda: InterceptionBackend() if InterceptionBackend.available() else None,
        "serial_hid":   lambda: SerialHIDBackend()    if SerialHIDBackend.available()    else None,
    }
    if choice in builders:
        try:
            b = builders[choice]()
            if b is None:
                _LAST_BACKEND_ERROR = f"Backend '{choice}' is not available on this machine."
                return None
            # If extra target windows are configured, wrap in MultiWindowBackend
            extra_titles = [t for t in (getattr(macro, "target_window_2", ""),
                                        getattr(macro, "target_window_3", "")) if t.strip()]
            if extra_titles and macro.use_target_window:
                extra_backends = []
                for title in extra_titles:
                    h2 = find_window_hwnd(title)
                    if h2:
                        try: extra_backends.append(BackgroundInjector(h2))
                        except Exception: pass
                if extra_backends:
                    return MultiWindowBackend([b] + extra_backends)
            return b
        except Exception as e:
            _LAST_BACKEND_ERROR = str(e) or repr(e)
            print(f"[backend:{choice}] init failed: {e}")
            return None

    # AUTO mode: try the most specific backend that already works for this macro.
    # We don't auto-inject DLLs in auto mode — that needs explicit consent.
    auto_attempts = []
    def _quiet(name, fn):
        try:
            b = fn()
            if b: return b
        except Exception as e:
            auto_attempts.append(f"{name}: {e}")
        return None
    pid = _pid_for_macro()
    candidates = [
        ("detours",      lambda: DetoursBackend(pid) if pid and _detours_pipe_ready(pid) else None),
        ("winmsg",       lambda: BackgroundInjector(_hwnd_for_macro()) if _hwnd_for_macro() else None),
        ("postmsg",      lambda: PostMessageBackend(_hwnd_for_macro()) if _hwnd_for_macro() else None),
        ("serial_hid",   lambda: SerialHIDBackend()    if SerialHIDBackend.available()    else None),
        ("interception", lambda: InterceptionBackend() if InterceptionBackend.available() else None),
        ("pynput",       lambda: PynputBackend()),
    ]
    for name, fn in candidates:
        b = _quiet(name, fn)
        if b: return b
    _LAST_BACKEND_ERROR = "No backend could be initialised. " + " | ".join(auto_attempts)
    return None


# ══════════════════════════════════════════════════════════════════════════════
# RECORDER THREAD
# ══════════════════════════════════════════════════════════════════════════════

class RecorderThread(QThread):
    captured = pyqtSignal(dict)
    done     = pyqtSignal()

    def __init__(self, record_mouse_move: bool = True,
                 filter_keys: Optional[set] = None,
                 target_hwnd: Optional[int] = None):
        super().__init__()
        self._active = False
        self.record_mouse_move = record_mouse_move
        # Lower-cased set of pynput-style key strings that must NOT be captured.
        self.filter_keys = {k.lower() for k in (filter_keys or set())}
        # If set, mouse events are stored in window-CLIENT coords so the
        # macro is portable across window positions.  Events get an extra
        # "coord_space":"client" tag; the player converts client→screen at
        # playback time using the target window's current position.
        self.target_hwnd = target_hwnd
        self._t0 = 0.0
        self._kb = self._ms = None

    def _to_coord(self, sx: int, sy: int) -> tuple:
        """Translate a captured screen coord to whichever coord space we record in."""
        if self.target_hwnd:
            cx, cy = screen_to_client(self.target_hwnd, sx, sy)
            return cx, cy, "client"
        return sx, sy, "screen"

    def begin(self):
        self._active = True; self._t0 = time.perf_counter(); self.start()

    def end(self):
        self._active = False
        if self._kb: self._kb.stop()
        if self._ms: self._ms.stop()

    def _ts(self): return time.perf_counter() - self._t0

    def _is_filtered(self, key_str: str) -> bool:
        return key_str.lower() in self.filter_keys

    def run(self):
        def kp(key):
            if not self._active: return False
            ks = key_to_str(key)
            if self._is_filtered(ks): return    # skip shortcut keys
            self.captured.emit({"timestamp": self._ts(), "event_type": "key_press",
                                 "data": {"key": ks}})
        def kr(key):
            if not self._active: return False
            ks = key_to_str(key)
            if self._is_filtered(ks): return
            self.captured.emit({"timestamp": self._ts(), "event_type": "key_release",
                                 "data": {"key": ks}})
        def mm(x, y):
            if not self._active or not self.record_mouse_move: return
            cx, cy, space = self._to_coord(x, y)
            self.captured.emit({"timestamp": self._ts(), "event_type": "mouse_move",
                                 "data": {"x": cx, "y": cy, "coord_space": space}})
        def mc(x, y, button, pressed):
            if not self._active: return
            cx, cy, space = self._to_coord(x, y)
            self.captured.emit({"timestamp": self._ts(), "event_type": "mouse_click",
                                 "data": {"x": cx, "y": cy, "coord_space": space,
                                          "button": str(button), "pressed": pressed}})
        def msc(x, y, dx, dy):
            if not self._active: return
            cx, cy, space = self._to_coord(x, y)
            self.captured.emit({"timestamp": self._ts(), "event_type": "mouse_scroll",
                                 "data": {"x": cx, "y": cy, "coord_space": space,
                                          "dx": dx, "dy": dy}})

        self._kb = kb_lib.Listener(on_press=kp, on_release=kr)
        self._ms = ms_lib.Listener(on_move=mm, on_click=mc, on_scroll=msc)
        self._kb.start(); self._ms.start()
        self._kb.join();  self._ms.join()
        self.done.emit()


# ══════════════════════════════════════════════════════════════════════════════
# PLAYER THREAD
# ══════════════════════════════════════════════════════════════════════════════

class PlayerThread(QThread):
    started_sig  = pyqtSignal(str)
    stopped_sig  = pyqtSignal(str)
    progress_sig = pyqtSignal(str, int, int)
    # mode signal payload is the backend name actually used (e.g. "winmsg")
    mode_sig     = pyqtSignal(str, str)

    def __init__(self, macro: Macro):
        super().__init__()
        self.macro = macro
        self._stop = threading.Event()

    def stop(self): self._stop.set()

    def run(self):
        # Outer try so any unexpected exception in playback logs + cleans up
        # instead of crashing the whole interpreter.
        try:
            self._run_inner()
        except Exception as e:
            _log_crash(f"PlayerThread.run crashed: {e}\n{traceback.format_exc()}")
            try: self.mode_sig.emit(self.macro.id, f"error:{e}")
            except Exception: pass
            try: self.stopped_sig.emit(self.macro.id)
            except Exception: pass

    def _run_inner(self):
        m = self.macro
        self.started_sig.emit(m.id)

        backend = create_backend(m)
        # Tell the UI which backend we actually got.  If creation failed,
        # pack the real reason after a colon so the UI can show it.
        if backend is None:
            self.mode_sig.emit(m.id, f"error:{_LAST_BACKEND_ERROR}")
            self.stopped_sig.emit(m.id)
            return
        self.mode_sig.emit(m.id, backend.name)

        # Resolve a target HWND for client→screen translation of any events
        # that were recorded in client space.  If the macro doesn't use a
        # target window, _target_hwnd stays None and screen coords pass through.
        self._target_hwnd: Optional[int] = None
        if m.use_target_window and m.target_window_title:
            self._target_hwnd = find_window_hwnd(m.target_window_title)

        events = m.events
        total  = len(events)
        speed  = max(0.01, m.speed_multiplier)
        reps   = m.repeat_count or 10_000_000

        try:
            for _ in range(reps):
                if self._stop.is_set(): break
                t0 = time.perf_counter()
                for i, ev in enumerate(events):
                    if self._stop.is_set(): break
                    _sleep_until(t0 + ev["timestamp"] / speed, self._stop)
                    if not self._stop.is_set():
                        self._fire(ev, backend)
                        self.progress_sig.emit(m.id, i, total)
        finally:
            try: backend.close()
            except Exception: pass

        self.stopped_sig.emit(m.id)

    def _resolve_xy(self, d: dict) -> tuple:
        """
        Translate a recorded event's (x, y) to screen coordinates,
        clamping client coords so they stay inside the target window.
        """
        x, y = int(d["x"]), int(d["y"])
        if d.get("coord_space") == "client" and self._target_hwnd:
            # Clamp to current client area so playback can never leak outside
            # the target window — even if the user resized it smaller.
            cw, ch = client_rect(self._target_hwnd)
            if cw > 0 and ch > 0:
                x = max(0, min(x, cw - 1))
                y = max(0, min(y, ch - 1))
            sx, sy = client_to_screen(self._target_hwnd, x, y)
            return sx, sy
        return x, y

    def _fire(self, ev: dict, b: InputBackend):
        # Bail fast if Stop was pressed — don't push more events into a backend
        # we're about to close.
        if self._stop.is_set(): return
        d, t = ev["data"], ev["event_type"]
        try:
            if   t == "key_press":    b.key_down(d["key"])
            elif t == "key_release":  b.key_up(d["key"])
            elif t == "mouse_move":
                x, y = self._resolve_xy(d)
                b.mouse_move(x, y)
            elif t == "mouse_click":
                x, y = self._resolve_xy(d)
                b.mouse_button(x, y, d["button"], d["pressed"])
            elif t == "mouse_scroll":
                x, y = self._resolve_xy(d)
                b.mouse_scroll(x, y, int(d["dx"]), int(d["dy"]))
        except Exception as e:
            _log_crash(f"[playback] {t}: {e}")


def _sleep_until(target: float, stop: threading.Event):
    while not stop.is_set():
        rem = target - time.perf_counter()
        if rem <= 0: break
        time.sleep(min(rem, 0.005))


# ══════════════════════════════════════════════════════════════════════════════
# HOTKEY MANAGER
# ══════════════════════════════════════════════════════════════════════════════

class HotkeyManager:
    """
    Wraps pynput.GlobalHotKeys so it can be rebuilt at any time.
    `hotkey_map` is {pynput_hotkey_string: payload}.  The payload is passed
    to the constructor's `on_trigger` callback when the hotkey fires.
    """
    def __init__(self, on_trigger):
        self._on_trigger = on_trigger
        self._listener = None
        self._lock = threading.Lock()

    def update(self, hotkey_map: dict):
        with self._lock:
            if self._listener: self._listener.stop(); self._listener = None
            if not hotkey_map: return
            actions = {hk: (lambda p=payload: self._on_trigger(p))
                       for hk, payload in hotkey_map.items()}
            try:
                self._listener = kb_lib.GlobalHotKeys(actions)
                self._listener.start()
            except Exception as e: print(f"Hotkey error: {e}")

    def stop(self):
        with self._lock:
            if self._listener: self._listener.stop(); self._listener = None


# ══════════════════════════════════════════════════════════════════════════════
# AUTO-UPDATER
#   Checks UPDATE_VERSION_URL on startup.  If a newer version is available,
#   downloads UPDATE_SCRIPT_URL and replaces the local file.  See README at
#   the bottom of this file for hosting instructions.
# ══════════════════════════════════════════════════════════════════════════════

class AutoUpdater:
    def __init__(self, current_version: str, version_url: str, script_url: str):
        self.current = current_version
        self.version_url = version_url
        self.script_url  = script_url

    @staticmethod
    def _vtuple(v: str):
        try: return tuple(int(p) for p in v.strip().split("."))
        except Exception: return (0,)

    def check(self, timeout: float = 4.0) -> Optional[dict]:
        """Return {'version': str, 'notes': str} if remote newer, else None."""
        if not self.version_url or "YOUR_USER" in self.version_url:
            return None
        try:
            with urllib.request.urlopen(self.version_url, timeout=timeout) as r:
                info = json.loads(r.read().decode("utf-8"))
        except Exception as e:
            print(f"Update check failed: {e}")
            return None
        remote = info.get("version", "0")
        if self._vtuple(remote) > self._vtuple(self.current):
            return {"version": remote, "notes": info.get("notes", "")}
        return None

    def download_and_install(self, target_path: Path, timeout: float = 15.0) -> bool:
        """Download new script, write `.new` then swap.  Returns True on success."""
        try:
            with urllib.request.urlopen(self.script_url, timeout=timeout) as r:
                data = r.read()
        except Exception as e:
            print(f"Update download failed: {e}")
            return False
        if len(data) < 1024 or b"Macro Recorder" not in data:
            print("Update content sanity check failed.")
            return False
        new_path = target_path.with_suffix(".new.py")
        bak_path = target_path.with_suffix(".prev.py")
        new_path.write_bytes(data)
        if target_path.exists():
            try: bak_path.unlink()
            except FileNotFoundError: pass
            target_path.replace(bak_path)
        new_path.replace(target_path)
        return True


# ══════════════════════════════════════════════════════════════════════════════
# STYLESHEET
# ══════════════════════════════════════════════════════════════════════════════

STYLE = """
QMainWindow, QWidget {
    background-color: #1e1e2e; color: #cdd6f4;
    font-family: 'Segoe UI', sans-serif; font-size: 13px;
}
QListWidget {
    background-color: #181825; border: 1px solid #313244;
    border-radius: 6px; padding: 4px;
}
QListWidget::item { padding: 7px 10px; border-radius: 4px; }
QListWidget::item:selected { background-color: #45475a; }
QListWidget::item:hover:!selected { background-color: #2a2a3e; }
QTableWidget {
    background-color: #181825; border: 1px solid #313244;
    border-radius: 6px; gridline-color: #2a2a3d;
}
QTableWidget::item { padding: 3px 8px; }
QTableWidget::item:selected { background-color: #45475a; }
QHeaderView::section {
    background-color: #1e1e2e; color: #a6adc8; padding: 5px 8px;
    border: none; border-bottom: 1px solid #313244;
    font-weight: bold; font-size: 11px;
}
QPushButton {
    background-color: #313244; color: #cdd6f4;
    border: 1px solid #45475a; border-radius: 6px;
    padding: 6px 14px; font-weight: 500;
}
QPushButton:hover   { background-color: #45475a; }
QPushButton:pressed { background-color: #181825; }
QPushButton:disabled { background-color: #1e1e2e; color: #585b70; border-color: #2a2a3d; }
QPushButton#btn_record { background-color: #f38ba8; color: #1e1e2e;
                          border-color: #f38ba8; font-weight: bold; }
QPushButton#btn_record:hover { background-color: #f5a3bb; }
QPushButton#btn_play   { background-color: #a6e3a1; color: #1e1e2e;
                          border-color: #a6e3a1; font-weight: bold; }
QPushButton#btn_play:hover  { background-color: #b8f0b3; }
QPushButton#btn_stop   { background-color: #fab387; color: #1e1e2e;
                          border-color: #fab387; font-weight: bold; }
QPushButton#btn_stop:hover  { background-color: #fcc9a5; }
QPushButton#btn_del { color: #f38ba8; }
QLineEdit, QSpinBox, QDoubleSpinBox, QKeySequenceEdit {
    background-color: #181825; border: 1px solid #313244;
    border-radius: 4px; padding: 4px 8px; color: #cdd6f4;
}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QKeySequenceEdit:focus { border-color: #89b4fa; }
QGroupBox {
    border: 1px solid #313244; border-radius: 6px;
    margin-top: 10px; padding-top: 6px;
    color: #a6adc8; font-weight: bold;
}
QGroupBox::title { subcontrol-origin: margin; left: 10px;
                   padding: 0 4px; font-size: 11px; }
QCheckBox { spacing: 6px; }
QCheckBox::indicator { width: 15px; height: 15px; border-radius: 3px;
                        border: 1px solid #45475a; background: #181825; }
QCheckBox::indicator:checked { background: #89b4fa; border-color: #89b4fa; }
QTabWidget::pane { border: 1px solid #313244; border-radius: 6px;
                   background: #1e1e2e; margin-top: -1px; }
QTabBar::tab { background: #181825; color: #a6adc8; padding: 7px 18px;
               border: 1px solid #313244; border-bottom: none;
               border-top-left-radius: 5px; border-top-right-radius: 5px;
               margin-right: 2px; }
QTabBar::tab:selected { background: #313244; color: #cdd6f4; }
QTabBar::tab:hover:!selected { background: #2a2a3e; }
QStatusBar { background: #181825; color: #a6adc8; border-top: 1px solid #313244; }
QSplitter::handle { background: #313244; width: 1px; height: 1px; }
QScrollBar:vertical { background: #181825; width: 8px; border-radius: 4px; }
QScrollBar::handle:vertical { background: #45475a; border-radius: 4px; min-height: 20px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QMenu { background: #1e1e2e; border: 1px solid #313244;
        border-radius: 4px; padding: 4px; }
QMenu::item { padding: 6px 16px; border-radius: 3px; }
QMenu::item:selected { background: #45475a; }
QToolTip { background: #313244; color: #cdd6f4; border: 1px solid #45475a;
           border-radius: 4px; padding: 4px 8px; }
"""

EVENT_COLORS = {
    "key_press":    "#89b4fa",
    "key_release":  "#74c7ec",
    "mouse_move":   "#6c7086",
    "mouse_click":  "#a6e3a1",
    "mouse_scroll": "#f9e2af",
}


# ══════════════════════════════════════════════════════════════════════════════
# MAIN WINDOW
# ══════════════════════════════════════════════════════════════════════════════

class MainWindow(QMainWindow):

    # ── Init ──────────────────────────────────────────────────────────────────

    # Cross-thread signal: pynput global hotkeys fire on a worker thread, but
    # Qt actions must run on the GUI thread.  We marshal via this signal.
    _global_shortcut_sig = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"QytCroRec v{__version__}")
        self.resize(1000, 700)
        self.setMinimumSize(780, 520)

        self._storage  = Storage()
        self._macros: list[Macro]               = self._storage.load_macros()
        self._sc_config: dict[str, str]         = self._storage.load_shortcuts()
        self._current:   Optional[Macro]        = None
        self._recorder:  Optional[RecorderThread] = None
        self._players:   dict[str, PlayerThread]  = {}
        self._qtshortcuts:  dict[str, QShortcut] = {}
        self._shortcut_btns: dict[str, list[QPushButton]] = {}  # action_id → buttons w/ dynamic tooltips
        self._recording   = False
        self._deleted_macro: Optional[Macro] = None   # undo buffer for last deleted macro
        self._capture_ctr = 0
        self._play_failure_msg: dict[str, str] = {}    # macro_id → persistent error
        self._row_event_idx: list = []                 # table_row → event_idx (or -1 = group header)
        self._groups: dict = {}                        # header_row → {start,count,et,collapsed}

        # Two hotkey managers — one for per-macro triggers, one for global app shortcuts
        self._hotkeys     = HotkeyManager(self._hotkey_fired)            # macro triggers (payload = macro id)
        self._app_hotkeys = HotkeyManager(self._app_hotkey_fired_raw)    # app shortcuts  (payload = action id)
        self._global_shortcut_sig.connect(self._app_hotkey_fired_gui)    # marshal to GUI thread

        self._build_ui()
        # Shortcuts are wired after all widgets exist
        self._apply_shortcuts()
        self.setStyleSheet(STYLE)
        self._refresh_list()
        if self._macros:
            self._macro_list.setCurrentRow(0)

        self._autosave    = QTimer(timeout=lambda: self._storage.save_macros(self._macros))
        self._autosave.start(30_000)
        self._flash_timer = QTimer(timeout=self._flash_record_btn)
        self._flash_state = False

        self._setup_tray()
        self._rebuild_hotkeys()
        self._update_active_label()

        if AUTO_UPDATE_ENABLED:
            # Run on a worker thread so startup isn't blocked by network
            threading.Thread(target=self._check_for_updates, daemon=True).start()

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION: Keyboard Shortcuts
    #   All application shortcuts live here.
    #   _apply_shortcuts() wires QShortcut objects from _sc_config.
    #   The Shortcuts tab lets the user edit _sc_config live.
    # ══════════════════════════════════════════════════════════════════════════

    # Map action id → callable
    def _shortcut_actions(self) -> dict:
        return {
            "new_macro":      self._new_macro,
            "dup_macro":      self._dup_macro,
            "del_macro":      self._del_macro,
            "undo_delete":    self._undo_delete,
            "toggle_record":  self._toggle_record,
            "play":           self._play_current,
            "stop":           self._stop_current,
            "stop_all":       self._stop_all,
            "clear_events":   self._clear_events,
            "capture_window": self._capture_window,
            "del_events":     self._del_events_if_focused,
        }

    def _apply_shortcuts(self):
        """
        Wire shortcuts in TWO ways at once:
          1. QShortcut (ApplicationShortcut) — fires when the app is focused.
          2. pynput GlobalHotKeys           — fires from ANY window, system-wide.
        Both read from _sc_config so editing the Shortcuts tab updates both.
        Also refreshes all dynamic tooltips so buttons reflect the live keybind.
        """
        actions = self._shortcut_actions()

        # 1. Local QShortcuts (visual feedback when app focused)
        for action, fn in actions.items():
            key = self._sc_config.get(action, DEFAULT_SHORTCUTS.get(action, ""))
            ks  = QKeySequence(key)
            if action in self._qtshortcuts:
                self._qtshortcuts[action].setKey(ks)
            else:
                sc = QShortcut(ks, self)
                sc.setContext(Qt.ShortcutContext.ApplicationShortcut)
                sc.activated.connect(fn)
                self._qtshortcuts[action] = sc

        # 2. Global pynput hotkeys (work from any window)
        gmap = {}
        for action in actions:
            key = self._sc_config.get(action, DEFAULT_SHORTCUTS.get(action, "")).strip()
            if not key: continue
            pk = qt_to_pynput_hotkey(key)
            if pk: gmap[pk] = action
        self._app_hotkeys.update(gmap)

        # 3. Refresh dynamic tooltips on all registered buttons
        self._refresh_tooltips()

    # ── Global-hotkey plumbing ────────────────────────────────────────────────

    def _app_hotkey_fired_raw(self, action: str):
        """Called from pynput worker thread — marshal to GUI thread via signal."""
        self._global_shortcut_sig.emit(action)

    def _app_hotkey_fired_gui(self, action: str):
        """Runs on GUI thread.  Dispatches to the same handler the QShortcut uses."""
        fn = self._shortcut_actions().get(action)
        if fn: fn()

    # ── Dynamic tooltips ──────────────────────────────────────────────────────

    def _register_btn(self, action: str, btn: QPushButton, label: str):
        """Track a button so its tooltip shows the current keybind for `action`."""
        btn.setProperty("_tt_label", label)
        self._shortcut_btns.setdefault(action, []).append(btn)

    def _refresh_tooltips(self):
        for action, btns in self._shortcut_btns.items():
            key = self._sc_config.get(action, DEFAULT_SHORTCUTS.get(action, ""))
            for btn in btns:
                label = btn.property("_tt_label") or ""
                btn.setToolTip(f"{label}   ({key})" if key else label)
        if hasattr(self, "_sc_hint_updater"):
            self._sc_hint_updater()

    # ── Auto-update ───────────────────────────────────────────────────────────

    def _check_for_updates(self):
        upd = AutoUpdater(__version__, UPDATE_VERSION_URL, UPDATE_SCRIPT_URL)
        info = upd.check()
        if not info: return
        # Marshal the prompt to the GUI thread
        QTimer.singleShot(0, lambda: self._prompt_update(upd, info))

    def _prompt_update(self, upd: "AutoUpdater", info: dict):
        msg = QMessageBox(self)
        msg.setWindowTitle("Update Available")
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setText(
            f"A new version is available: <b>v{info['version']}</b>  "
            f"(you have v{__version__}).<br><br>"
            f"{info.get('notes','').replace(chr(10),'<br>')}"
        )
        msg.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        msg.button(QMessageBox.StandardButton.Yes).setText("Update && Restart")
        msg.button(QMessageBox.StandardButton.No ).setText("Later")
        if msg.exec() != QMessageBox.StandardButton.Yes:
            return
        target = Path(__file__).resolve()
        if upd.download_and_install(target):
            self._set_status("Update installed — restarting…", "#a6e3a1")
            QTimer.singleShot(600, self._restart_app)
        else:
            QMessageBox.warning(self, "Update Failed",
                "Could not install the update.  See console for details.")

    def _restart_app(self):
        """Relaunch the script and quit this instance."""
        self._storage.save_macros(self._macros)
        try:
            subprocess.Popen([sys.executable, str(Path(__file__).resolve())])
        except Exception as e:
            print(f"Restart failed: {e}")
        QApplication.quit()

    # ── Misc ─────────────────────────────────────────────────────────────────

    def _del_events_if_focused(self):
        """Delete selected events only when the events table has keyboard focus."""
        if self._table.hasFocus():
            self._del_selected_events()

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION: UI Builders
    # ══════════════════════════════════════════════════════════════════════════

    def _build_ui(self):
        root_w = QWidget()
        self.setCentralWidget(root_w)
        root = QVBoxLayout(root_w)
        root.setContentsMargins(10, 10, 10, 8)
        root.setSpacing(8)

        root.addLayout(self._build_topbar())

        sp = QSplitter(Qt.Orientation.Horizontal)
        sp.setHandleWidth(1)
        sp.addWidget(self._build_left())
        sp.addWidget(self._build_right_tabs())
        sp.setSizes([210, 790])
        sp.setStretchFactor(0, 0)
        sp.setStretchFactor(1, 1)
        root.addWidget(sp)

        root.addLayout(self._build_controls())

        sb = QStatusBar()
        self.setStatusBar(sb)
        self._status     = QLabel("Ready")
        self._active_lbl = QLabel("")
        self._active_lbl.setStyleSheet("color: #a6e3a1; font-weight: bold;")
        self._runtime_lbl = QLabel("")
        self._runtime_lbl.setStyleSheet("color: #f9e2af; font-family: Consolas, monospace;")
        sb.addWidget(self._status)
        sb.addPermanentWidget(self._runtime_lbl)
        sb.addPermanentWidget(self._active_lbl)
        # Ticks every 200ms to update the runtime label during playback
        self._play_start_times: dict = {}
        self._runtime_timer = QTimer(timeout=self._tick_runtime)

    def _build_topbar(self) -> QHBoxLayout:
        row = QHBoxLayout(); row.setSpacing(6)
        btn_new = QPushButton("＋  New")
        btn_dup = QPushButton("⎘  Duplicate")
        btn_del = QPushButton("✕  Delete"); btn_del.setObjectName("btn_del")
        btn_new.clicked.connect(self._new_macro)
        btn_dup.clicked.connect(self._dup_macro)
        btn_del.clicked.connect(self._del_macro)
        # Register for dynamic tooltips — hover shows the current keybind
        self._register_btn("new_macro", btn_new, "New macro")
        self._register_btn("dup_macro", btn_dup, "Duplicate macro")
        self._register_btn("del_macro", btn_del, "Delete macro")
        for b in (btn_new, btn_dup, btn_del): row.addWidget(b)
        row.addStretch()
        # ── Log copy buttons ─────────────────────────────────────────────────
        _diag_log_path  = Path.home() / ".macro_recorder" / "dll_hook.log"
        _crash_log_path = _CRASH_LOG_PATH

        btn_diag = QPushButton("📋 Diag Log")
        btn_diag.setToolTip(f"Copy diagnostic log to clipboard\n{_diag_log_path}")
        btn_diag.setStyleSheet(
            "QPushButton { background: #313244; color: #a6adc8; "
            "border: 1px solid #45475a; border-radius: 5px; padding: 4px 10px; font-size: 12px; }"
            "QPushButton:hover { background: #45475a; color: #cdd6f4; }")
        btn_diag.clicked.connect(self._copy_diag_log)
        row.addWidget(btn_diag)

        btn_crash = QPushButton("📋 Crash Log")
        btn_crash.setToolTip(f"Copy crash log to clipboard\n{_crash_log_path}")
        btn_crash.setStyleSheet(
            "QPushButton { background: #313244; color: #f38ba8; "
            "border: 1px solid #45475a; border-radius: 5px; padding: 4px 10px; font-size: 12px; }"
            "QPushButton:hover { background: #45475a; color: #f5a3bb; }")
        btn_crash.clicked.connect(self._copy_crash_log)
        row.addWidget(btn_crash)

        row.addSpacing(12)
        title = QLabel(f"QytCroRec v{__version__}")
        title.setStyleSheet("font-size: 16px; font-weight: bold; color: #89b4fa;")
        row.addWidget(title)
        return row

    def _build_left(self) -> QWidget:
        w = QWidget(); w.setMinimumWidth(170); w.setMaximumWidth(250)
        lay = QVBoxLayout(w); lay.setContentsMargins(0, 0, 6, 0); lay.setSpacing(4)
        hdr = QLabel("MACROS")
        hdr.setStyleSheet("color: #585b70; font-size: 11px; font-weight: bold; padding: 2px 0;")
        lay.addWidget(hdr)
        self._macro_list = QListWidget()
        self._macro_list.currentRowChanged.connect(self._on_row_changed)
        lay.addWidget(self._macro_list)
        return w

    def _build_right_tabs(self) -> QTabWidget:
        tabs = QTabWidget()
        tabs.addTab(self._build_tab_macro(),     "⚙  Macro")
        tabs.addTab(self._build_tab_events(),    "⏺  Events")
        tabs.addTab(self._build_tab_shortcuts(), "⌨  Shortcuts")
        return tabs

    # ── Tab: Macro settings ───────────────────────────────────────────────────

    def _build_tab_macro(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w); lay.setContentsMargins(10, 10, 10, 10); lay.setSpacing(10)

        grp = QGroupBox("Macro Settings")
        gl = QVBoxLayout(grp); gl.setSpacing(8)

        # Row 1: name + trigger hotkey
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("Name:"))
        self._name_edit = QLineEdit(placeholderText="Macro name…")
        self._name_edit.textChanged.connect(self._on_name_changed)
        r1.addWidget(self._name_edit)
        r1.addSpacing(12)
        r1.addWidget(QLabel("Global Trigger Hotkey:"))
        self._hotkey_edit = QLineEdit(placeholderText="e.g. ctrl+f5")
        self._hotkey_edit.setMaximumWidth(140)
        self._hotkey_edit.setToolTip(
            "Fires from ANY window regardless of focus.\n"
            "Format: modifier+key  e.g.  ctrl+f5  alt+f1")
        self._hotkey_edit.editingFinished.connect(self._on_hotkey_changed)
        r1.addWidget(self._hotkey_edit)
        gl.addLayout(r1)

        # Row 2: repeat + speed + mouse toggle
        r2 = QHBoxLayout()
        r2.addWidget(QLabel("Repeat:"))
        self._repeat_spin = QSpinBox()
        self._repeat_spin.setRange(0, 99999); self._repeat_spin.setValue(1)
        self._repeat_spin.setSpecialValueText("∞"); self._repeat_spin.setToolTip("0 = loop forever")
        self._repeat_spin.setMaximumWidth(80)
        self._repeat_spin.valueChanged.connect(self._on_repeat_changed)
        r2.addWidget(self._repeat_spin); r2.addSpacing(12)
        r2.addWidget(QLabel("Speed:"))
        self._speed_spin = QDoubleSpinBox()
        self._speed_spin.setRange(0.1, 20.0); self._speed_spin.setSingleStep(0.25)
        self._speed_spin.setValue(1.0); self._speed_spin.setSuffix("×")
        self._speed_spin.setMaximumWidth(90)
        self._speed_spin.valueChanged.connect(self._on_speed_changed)
        r2.addWidget(self._speed_spin); r2.addSpacing(12)
        self._move_chk = QCheckBox("Record Mouse Moves")
        self._move_chk.setChecked(True)
        self._move_chk.stateChanged.connect(self._on_move_chk_changed)
        r2.addWidget(self._move_chk); r2.addStretch()
        gl.addLayout(r2)

        # Row 3: target windows (up to 3, only #1 required)
        _win_lbl_style = (
            "color: #585b70; font-size: 12px; background: #181825;"
            "border: 1px solid #313244; border-radius: 4px; padding: 3px 10px;")

        r3_hdr = QHBoxLayout()
        self._use_target_chk = QCheckBox("Force Target Window(s)")
        self._use_target_chk.setToolTip(
            "CHECKED  → Events are injected directly into the configured window(s)\n"
            "  via SendMessage + AttachThreadInput.  Mouse does NOT move and\n"
            "  keyboard focus is NOT stolen.  Up to 3 windows supported.\n\n"
            "UNCHECKED → Events go to whatever window is currently active\n"
            "  (uses SendInput — real mouse & keyboard WILL be affected).")
        self._use_target_chk.stateChanged.connect(self._on_use_target_changed)
        r3_hdr.addWidget(self._use_target_chk)
        r3_hdr.addStretch()
        gl.addLayout(r3_hdr)

        # ── Window slot 1 (required) ─────────────────────────────────────────
        r3a = QHBoxLayout(); r3a.setContentsMargins(20, 0, 0, 0)
        lbl1 = QLabel("Win 1 (required):")
        lbl1.setStyleSheet("color: #a6adc8; font-size: 12px;")
        lbl1.setFixedWidth(120)
        r3a.addWidget(lbl1)
        self._win_title_lbl = QLabel("(none)")
        self._win_title_lbl.setStyleSheet(_win_lbl_style)
        self._win_title_lbl.setMinimumWidth(180)
        r3a.addWidget(self._win_title_lbl, stretch=1); r3a.addSpacing(4)
        self._pick_btn = QPushButton("Pick…")
        self._pick_btn.setEnabled(False); self._pick_btn.setMaximumWidth(60)
        self._pick_btn.setToolTip("Choose target window from list")
        self._pick_btn.clicked.connect(lambda: self._pick_window(slot=1))
        r3a.addWidget(self._pick_btn)
        self._capture_btn = QPushButton("Capture (3s)")
        self._capture_btn.setEnabled(False); self._capture_btn.setMaximumWidth(100)
        self._capture_btn.clicked.connect(self._capture_window)
        self._register_btn("capture_window", self._capture_btn,
            "3-second countdown — switch to your target window before time runs out")
        r3a.addWidget(self._capture_btn)
        self._drag_pick_btn1 = QPushButton("🎯")
        self._drag_pick_btn1.setEnabled(False); self._drag_pick_btn1.setMaximumWidth(36)
        self._drag_pick_btn1.setToolTip("Drag-pick: click any window on screen to select it")
        self._drag_pick_btn1.clicked.connect(lambda: self._start_drag_pick(1))
        r3a.addWidget(self._drag_pick_btn1)
        gl.addLayout(r3a)

        # ── Window slot 2 (optional) ─────────────────────────────────────────
        r3b = QHBoxLayout(); r3b.setContentsMargins(20, 0, 0, 0)
        lbl2 = QLabel("Win 2 (optional):")
        lbl2.setStyleSheet("color: #585b70; font-size: 12px;")
        lbl2.setFixedWidth(120)
        r3b.addWidget(lbl2)
        self._win2_title_lbl = QLabel("(none)")
        self._win2_title_lbl.setStyleSheet(_win_lbl_style)
        self._win2_title_lbl.setMinimumWidth(180)
        r3b.addWidget(self._win2_title_lbl, stretch=1); r3b.addSpacing(4)
        self._pick_btn2 = QPushButton("Pick…")
        self._pick_btn2.setEnabled(False); self._pick_btn2.setMaximumWidth(60)
        self._pick_btn2.setToolTip("Choose second target window from list")
        self._pick_btn2.clicked.connect(lambda: self._pick_window(slot=2))
        r3b.addWidget(self._pick_btn2)
        self._clear_btn2 = QPushButton("Clear")
        self._clear_btn2.setEnabled(False); self._clear_btn2.setMaximumWidth(60)
        self._clear_btn2.clicked.connect(lambda: self._clear_window_slot(2))
        r3b.addWidget(self._clear_btn2)
        self._drag_pick_btn2 = QPushButton("🎯")
        self._drag_pick_btn2.setEnabled(False); self._drag_pick_btn2.setMaximumWidth(36)
        self._drag_pick_btn2.setToolTip("Drag-pick: click any window on screen to select it")
        self._drag_pick_btn2.clicked.connect(lambda: self._start_drag_pick(2))
        r3b.addWidget(self._drag_pick_btn2)
        gl.addLayout(r3b)

        # ── Window slot 3 (optional) ─────────────────────────────────────────
        r3c = QHBoxLayout(); r3c.setContentsMargins(20, 0, 0, 0)
        lbl3 = QLabel("Win 3 (optional):")
        lbl3.setStyleSheet("color: #585b70; font-size: 12px;")
        lbl3.setFixedWidth(120)
        r3c.addWidget(lbl3)
        self._win3_title_lbl = QLabel("(none)")
        self._win3_title_lbl.setStyleSheet(_win_lbl_style)
        self._win3_title_lbl.setMinimumWidth(180)
        r3c.addWidget(self._win3_title_lbl, stretch=1); r3c.addSpacing(4)
        self._pick_btn3 = QPushButton("Pick…")
        self._pick_btn3.setEnabled(False); self._pick_btn3.setMaximumWidth(60)
        self._pick_btn3.setToolTip("Choose third target window from list")
        self._pick_btn3.clicked.connect(lambda: self._pick_window(slot=3))
        r3c.addWidget(self._pick_btn3)
        self._clear_btn3 = QPushButton("Clear")
        self._clear_btn3.setEnabled(False); self._clear_btn3.setMaximumWidth(60)
        self._clear_btn3.clicked.connect(lambda: self._clear_window_slot(3))
        r3c.addWidget(self._clear_btn3)
        self._drag_pick_btn3 = QPushButton("🎯")
        self._drag_pick_btn3.setEnabled(False); self._drag_pick_btn3.setMaximumWidth(36)
        self._drag_pick_btn3.setToolTip("Drag-pick: click any window on screen to select it")
        self._drag_pick_btn3.clicked.connect(lambda: self._start_drag_pick(3))
        r3c.addWidget(self._drag_pick_btn3)
        gl.addLayout(r3c)

        # Row 4: input backend selector
        r4 = QHBoxLayout()
        r4.addWidget(QLabel("Input Backend:"))
        self._backend_combo = QComboBox()
        for name, desc, _ in BACKEND_CHOICES:
            label = desc
            # Mark unavailable backends so the user knows
            if name == "interception" and not InterceptionBackend.available():
                label += "   [not installed]"
            elif name == "serial_hid" and not SerialHIDBackend.available():
                label += "   [pyserial not installed]"
            elif name == "detours":
                # Only know at runtime once we have a target PID; show a hint.
                label += "   [requires injected hook DLL]"
            self._backend_combo.addItem(label, name)
        self._backend_combo.setToolTip(
            "How playback delivers events:\n"
            "  • Auto          — pick the best available backend\n"
            "  • Window Messages — silent, normal Win32 apps (requires Force Target Window)\n"
            "  • pynput        — drives real mouse / keyboard (visible)\n"
            "  • Interception  — kernel driver (anti-cheat may flag it)\n"
            "  • Serial HID    — real USB device (Arduino / Pi Pico firmware)")
        self._backend_combo.setMinimumWidth(330)
        self._backend_combo.currentIndexChanged.connect(self._on_backend_changed)
        r4.addWidget(self._backend_combo)
        # Helper button for the Detours backend — opens the build folder
        # if the DLL isn't there yet, or shows pipe status if it is.
        self._hook_btn = QPushButton("Hook DLL…")
        self._hook_btn.setToolTip(
            "Open the hooks/dinput_hook/ folder.\n"
            "Build the DLL there once, then the Detours backend will auto-inject\n"
            "into whatever target window you've selected.")
        self._hook_btn.clicked.connect(self._open_hook_dir)
        r4.addWidget(self._hook_btn)

        # Explicit reload — eject + reinject the hook DLL.  Use this AFTER
        # rebuilding the DLL so the freshly-compiled code is what's running
        # in the target process.  Normal Play does NOT do this (it has a
        # small crash risk), so this button is the safe escape hatch.
        self._reload_hook_btn = QPushButton("Reload Hook")
        self._reload_hook_btn.setToolTip(
            "Eject the currently-loaded hook DLL and inject the latest build.\n"
            "Use after you've rebuilt the DLL.  Small chance of crashing the\n"
            "target — close + reopen the game if that happens.")
        self._reload_hook_btn.clicked.connect(self._reload_hook)
        r4.addWidget(self._reload_hook_btn)

        r4.addStretch()
        gl.addLayout(r4)

        lay.addWidget(grp)
        lay.addStretch()
        return w

    # ── Tab: Events ───────────────────────────────────────────────────────────

    def _build_tab_events(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w); lay.setContentsMargins(10, 10, 10, 10); lay.setSpacing(6)

        # Event-type filter row — each checkbox toggles row visibility
        # without deleting the event.  All on by default.
        filt_row = QHBoxLayout()
        flbl = QLabel("Show:")
        flbl.setStyleSheet("color: #a6adc8; font-size: 12px;")
        filt_row.addWidget(flbl)
        self._ev_filters: dict[str, QCheckBox] = {}
        for et, label in [("key_press",    "key press"),
                          ("key_release",  "key release"),
                          ("mouse_move",   "mouse move"),
                          ("mouse_click",  "mouse click"),
                          ("mouse_scroll", "mouse scroll")]:
            cb = QCheckBox(label); cb.setChecked(True)
            # Color the checkbox label to match the event type's row color
            color = EVENT_COLORS.get(et, "#cdd6f4")
            cb.setStyleSheet(f"QCheckBox {{ color: {color}; }}")
            cb.stateChanged.connect(self._apply_event_filter)
            filt_row.addWidget(cb)
            self._ev_filters[et] = cb
        filt_row.addStretch()
        # Quick "all / none" helpers
        btn_all  = QPushButton("All");  btn_all .setMaximumWidth(56)
        btn_none = QPushButton("None"); btn_none.setMaximumWidth(56)
        btn_all .clicked.connect(lambda: self._set_all_filters(True))
        btn_none.clicked.connect(lambda: self._set_all_filters(False))
        filt_row.addWidget(btn_all); filt_row.addWidget(btn_none)
        lay.addLayout(filt_row)

        self._table = QTableWidget()
        self._table.setColumnCount(4)
        self._table.setHorizontalHeaderLabels(["#", "Time (s)", "Type", "Details"])
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        hh.resizeSection(0, 50); hh.resizeSection(1, 100); hh.resizeSection(2, 120)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setStyleSheet("QTableWidget { alternate-background-color: #1a1a2a; }")
        self._table.verticalHeader().setVisible(True)   # needed for drag handle
        self._table.verticalHeader().setSectionsMovable(True)
        self._table.verticalHeader().sectionMoved.connect(self._on_section_moved)
        self._table.cellClicked.connect(self._on_table_cell_clicked)
        lay.addWidget(self._table)

        ea = QHBoxLayout()
        btn_clr   = QPushButton("Clear All")
        btn_clr.clicked.connect(self._clear_events)
        self._register_btn("clear_events", btn_clr, "Clear all recorded events")
        btn_del_ev = QPushButton("Delete Selected")
        btn_del_ev.clicked.connect(self._del_selected_events)
        self._register_btn("del_events", btn_del_ev, "Delete selected rows")
        ea.addWidget(btn_clr); ea.addWidget(btn_del_ev); ea.addStretch()
        self._ev_count = QLabel("0 events")
        self._ev_count.setStyleSheet("color: #585b70; font-size: 11px;")
        ea.addWidget(self._ev_count)
        lay.addLayout(ea)
        return w

    def _set_all_filters(self, on: bool):
        for cb in self._ev_filters.values():
            cb.blockSignals(True); cb.setChecked(on); cb.blockSignals(False)
        self._apply_event_filter()

    def _apply_event_filter(self):
        """Hide/show rows in the events table according to the filter checkboxes."""
        if not self._current: return
        allowed = {et for et, cb in self._ev_filters.items() if cb.isChecked()}
        shown = 0
        for row in range(self._table.rowCount()):
            if row in getattr(self, "_groups", {}):
                # Header row: visible iff its event type passes the filter
                g = self._groups[row]
                self._table.setRowHidden(row, g["et"] not in allowed)
                continue
            idx = self._row_event_idx[row] if 0 <= row < len(self._row_event_idx) else -1
            if idx < 0 or idx >= len(self._current.events):
                continue
            ev = self._current.events[idx]
            # Find if this row belongs to a collapsed group — if so, keep hidden
            in_collapsed = False
            for hdr_row, g in self._groups.items():
                if hdr_row < row <= hdr_row + g["count"] and g["collapsed"]:
                    in_collapsed = True
                    break
            visible = (ev["event_type"] in allowed) and not in_collapsed
            self._table.setRowHidden(row, not visible)
            if visible: shown += 1
        total = len(self._current.events)
        groups_n = len(getattr(self, "_groups", {}))
        suffix = f"  ({groups_n} groups)" if groups_n else ""
        self._ev_count.setText(
            (f"{total} events" if shown == total else f"{shown} of {total} events") + suffix)

    # ── Tab: Shortcuts ────────────────────────────────────────────────────────

    def _build_tab_shortcuts(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w); lay.setContentsMargins(10, 10, 10, 10); lay.setSpacing(8)

        info = QLabel(
            "Click a shortcut field and press your new key combination.  "
            "Changes take effect immediately."
        )
        info.setStyleSheet("color: #a6adc8; font-size: 12px; padding: 2px 0 6px 0;")
        info.setWordWrap(True)
        lay.addWidget(info)

        tbl = QTableWidget(len(SHORTCUT_DEFS), 2)
        tbl.setHorizontalHeaderLabels(["Action", "Key Binding"])
        tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        tbl.horizontalHeader().resizeSection(1, 220)
        tbl.verticalHeader().setVisible(False)
        tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        tbl.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        tbl.setStyleSheet("QTableWidget { alternate-background-color: #1a1a2a; }")
        tbl.setAlternatingRowColors(True)
        tbl.setShowGrid(False)
        tbl.verticalHeader().setDefaultSectionSize(38)

        self._sc_edits: dict[str, QKeySequenceEdit] = {}
        for i, (action, label, _default) in enumerate(SHORTCUT_DEFS):
            lbl_item = QTableWidgetItem(f"  {label}")
            lbl_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            tbl.setItem(i, 0, lbl_item)

            edit = QKeySequenceEdit(QKeySequence(self._sc_config.get(action, "")))
            edit.setStyleSheet(
                "QKeySequenceEdit { background: #181825; border: 1px solid #313244;"
                " border-radius: 4px; padding: 3px 8px; color: #89b4fa; }")
            edit.keySequenceChanged.connect(lambda ks, a=action: self._on_sc_changed(a, ks))
            tbl.setCellWidget(i, 1, edit)
            self._sc_edits[action] = edit

        lay.addWidget(tbl)

        btn_row = QHBoxLayout()
        btn_reset = QPushButton("Reset All to Defaults")
        btn_reset.clicked.connect(self._reset_shortcuts)
        btn_row.addWidget(btn_reset); btn_row.addStretch()
        lay.addLayout(btn_row)
        return w

    # ── Bottom controls ────────────────────────────────────────────────────────

    def _build_controls(self) -> QHBoxLayout:
        row = QHBoxLayout(); row.setSpacing(8)

        self._btn_record = QPushButton("⏺  Record")
        self._btn_record.setObjectName("btn_record"); self._btn_record.setMinimumWidth(120)
        self._btn_record.clicked.connect(self._toggle_record)
        self._register_btn("toggle_record", self._btn_record, "Start / stop recording")

        self._btn_play = QPushButton("▶  Play")
        self._btn_play.setObjectName("btn_play"); self._btn_play.setMinimumWidth(90)
        self._btn_play.clicked.connect(self._play_current)
        self._register_btn("play", self._btn_play, "Play selected macro")

        self._btn_stop = QPushButton("■  Stop")
        self._btn_stop.setObjectName("btn_stop"); self._btn_stop.setMinimumWidth(90)
        self._btn_stop.setEnabled(False)
        self._btn_stop.clicked.connect(self._stop_current)
        self._register_btn("stop", self._btn_stop, "Stop current macro")

        self._btn_stop_all = QPushButton("Stop All")
        self._btn_stop_all.setEnabled(False)
        self._btn_stop_all.clicked.connect(self._stop_all)
        self._register_btn("stop_all", self._btn_stop_all, "Stop all running macros")

        for b in (self._btn_record, self._btn_play, self._btn_stop, self._btn_stop_all):
            row.addWidget(b)
        row.addStretch()

        # Show current shortcuts as a reminder
        sc_hint = QLabel()
        sc_hint.setStyleSheet("color: #45475a; font-size: 11px;")
        def _update_hint():
            sc = self._sc_config
            sc_hint.setText(
                f"Rec: {sc.get('toggle_record','?')}   "
                f"Play: {sc.get('play','?')}   "
                f"Stop: {sc.get('stop','?')}   "
                f"Stop All: {sc.get('stop_all','?')}"
            )
        _update_hint()
        self._sc_hint_updater = _update_hint
        row.addWidget(sc_hint)
        return row

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION: System Tray
    # ══════════════════════════════════════════════════════════════════════════

    def _setup_tray(self):
        px = QPixmap(32, 32); px.fill(QColor("#1e1e2e"))
        p = QPainter(px)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor("#89b4fa")); p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(4, 4, 24, 24)
        p.setPen(QColor("#1e1e2e")); p.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        p.drawText(QRect(0, 0, 32, 32), Qt.AlignmentFlag.AlignCenter, "Q"); p.end()
        self._tray = QSystemTrayIcon(QIcon(px), self)
        menu = QMenu()
        menu.addAction(QAction("Show", self, triggered=self.show))
        menu.addSeparator()
        menu.addAction(QAction("Quit", self, triggered=self._quit_app))
        self._tray.setContextMenu(menu); self._tray.setToolTip("QytCroRec")
        self._tray.activated.connect(
            lambda r: self.show() if r == QSystemTrayIcon.ActivationReason.DoubleClick else None)
        self._tray.show()

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION: Macro List
    # ══════════════════════════════════════════════════════════════════════════

    def _refresh_list(self):
        self._macro_list.blockSignals(True)
        cur_id = self._current.id if self._current else None
        self._macro_list.clear(); restore = 0
        for i, m in enumerate(self._macros):
            playing = m.id in self._players
            item = QListWidgetItem(f"{'▶ ' if playing else '   '}{m.name}")
            item.setData(Qt.ItemDataRole.UserRole, m.id)
            if playing: item.setForeground(QColor("#a6e3a1"))
            self._macro_list.addItem(item)
            if m.id == cur_id: restore = i
        self._macro_list.blockSignals(False)
        if self._macros: self._macro_list.setCurrentRow(restore)

    def _on_row_changed(self, row: int):
        if 0 <= row < len(self._macros): self._load_macro(self._macros[row])

    def _load_macro(self, m: Macro):
        self._current = m
        for w, v in [(self._name_edit, m.name), (self._hotkey_edit, m.trigger_hotkey)]:
            w.blockSignals(True); w.setText(v); w.blockSignals(False)
        for w, v in [(self._repeat_spin, m.repeat_count), (self._speed_spin, m.speed_multiplier)]:
            w.blockSignals(True); w.setValue(v); w.blockSignals(False)
        for w, v in [(self._move_chk, m.record_mouse_move),
                     (self._use_target_chk, m.use_target_window)]:
            w.blockSignals(True); w.setChecked(v); w.blockSignals(False)
        self._win_title_lbl .setText(m.target_window_title or "(none)")
        self._win2_title_lbl.setText(getattr(m, "target_window_2", "") or "(none)")
        self._win3_title_lbl.setText(getattr(m, "target_window_3", "") or "(none)")
        en = m.use_target_window
        for btn in (self._capture_btn, self._pick_btn, self._drag_pick_btn1,
                    self._pick_btn2, self._clear_btn2, self._drag_pick_btn2,
                    self._pick_btn3, self._clear_btn3, self._drag_pick_btn3):
            btn.setEnabled(en)
        # Load backend selection
        idx = next((i for i, (name, _, _) in enumerate(BACKEND_CHOICES)
                    if name == (m.input_backend or "auto")), 0)
        self._backend_combo.blockSignals(True)
        self._backend_combo.setCurrentIndex(idx)
        self._backend_combo.blockSignals(False)
        self._fill_table(m)
        self._update_play_btns()

    GROUP_THRESHOLD = 3   # collapse consecutive same-type runs of >=N events

    def _build_event_groups(self, events: list) -> list:
        """
        Return [(start_idx, count, event_type), …] for consecutive runs of
        the same event_type with length >= GROUP_THRESHOLD.
        """
        out, i, N = [], 0, len(events)
        while i < N:
            et = events[i]["event_type"]
            j = i
            while j < N and events[j]["event_type"] == et:
                j += 1
            if j - i >= self.GROUP_THRESHOLD:
                out.append((i, j - i, et))
            i = j
        return out

    def _fill_table(self, m: Macro):
        """
        Populate the events table.  Consecutive same-type runs become a
        collapsible group header (▶/▼) followed by the individual rows
        (hidden when collapsed).  Click the header row's '#' cell to toggle.
        """
        # Block signals during bulk population so drag-move handlers don't fire
        self._table.blockSignals(True)
        self._table.setRowCount(0)
        # State tracked per table:
        #   self._row_event_idx[row] = event index, or -1 for a group-header row
        #   self._groups[header_row] = {'start': event_idx, 'count': n, 'et': str, 'collapsed': bool}
        self._row_event_idx: list = []
        self._groups: dict = {}

        groups = self._build_event_groups(m.events)
        groups_by_start = {g[0]: g for g in groups}

        i = 0
        while i < len(m.events):
            if i in groups_by_start:
                start, count, et = groups_by_start[i]
                row = self._table.rowCount()
                self._table.insertRow(row)
                hdr = QTableWidgetItem(f"▶  ×{count}")
                hdr.setForeground(QColor(EVENT_COLORS.get(et, "#cdd6f4")))
                hdr.setBackground(QColor("#252535"))
                hdr.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self._table.setItem(row, 0, hdr)
                t0 = m.events[start]["timestamp"]; t1 = m.events[start + count - 1]["timestamp"]
                self._table.setItem(row, 1, QTableWidgetItem(f"{t0:.3f}—{t1:.3f}"))
                tit = QTableWidgetItem(et)
                tit.setForeground(QColor(EVENT_COLORS.get(et, "#cdd6f4")))
                self._table.setItem(row, 2, tit)
                self._table.setItem(row, 3, QTableWidgetItem(
                    f"({count} similar events — click ▶ to expand)"))
                self._row_event_idx.append(-1)
                self._groups[row] = {"start": start, "count": count,
                                     "et": et, "collapsed": True}
                for j in range(count):
                    ev = m.events[i + j]
                    r = self._table.rowCount()
                    self._table.insertRow(r)
                    self._table.setItem(r, 0, QTableWidgetItem(str(i + j + 1)))
                    self._table.setItem(r, 1, QTableWidgetItem(f"{ev['timestamp']:.3f}"))
                    ti = QTableWidgetItem(ev["event_type"])
                    ti.setForeground(QColor(EVENT_COLORS.get(ev["event_type"], "#cdd6f4")))
                    self._table.setItem(r, 2, ti)
                    self._table.setItem(r, 3, QTableWidgetItem(event_summary(ev)))
                    self._row_event_idx.append(i + j)
                    self._table.setRowHidden(r, True)   # start collapsed
                i += count
            else:
                ev = m.events[i]
                row = self._table.rowCount()
                self._table.insertRow(row)
                self._table.setItem(row, 0, QTableWidgetItem(str(i + 1)))
                self._table.setItem(row, 1, QTableWidgetItem(f"{ev['timestamp']:.3f}"))
                ti = QTableWidgetItem(ev["event_type"])
                ti.setForeground(QColor(EVENT_COLORS.get(ev["event_type"], "#cdd6f4")))
                self._table.setItem(row, 2, ti)
                self._table.setItem(row, 3, QTableWidgetItem(event_summary(ev)))
                self._row_event_idx.append(i)
                i += 1

        self._table.blockSignals(False)
        self._ev_count.setText(
            f"{len(m.events)} events" + (f"  ({len(groups)} groups)" if groups else ""))
        self._apply_event_filter()

    def _toggle_group(self, header_row: int):
        """Expand / collapse the group whose header is at header_row."""
        g = self._groups.get(header_row)
        if not g: return
        g["collapsed"] = not g["collapsed"]
        # Update header arrow
        hdr = self._table.item(header_row, 0)
        if hdr:
            hdr.setText(f"{'▶' if g['collapsed'] else '▼'}  ×{g['count']}")
        # Show / hide the child rows
        for offset in range(1, g["count"] + 1):
            r = header_row + offset
            if r < self._table.rowCount():
                # Still respect the event-type filter
                allowed = {et for et, cb in self._ev_filters.items() if cb.isChecked()}
                visible = (not g["collapsed"]) and (g["et"] in allowed)
                self._table.setRowHidden(r, not visible)

    def _on_table_cell_clicked(self, row: int, col: int):
        if row in self._groups:
            self._toggle_group(row)

    def _on_section_moved(self, logical_idx: int, old_visual: int, new_visual: int):
        """
        Translate a header drag (visual reorder) into an actual reorder of
        the underlying events list, then rebuild the table so display + data
        stay in sync.  Group-header rows are excluded — only leaf events
        can be moved.
        """
        if not self._current: return
        # Map visual rows → event indices, ignoring group headers
        # (visual order after the move)
        order = []
        vh = self._table.verticalHeader()
        for vrow in range(self._table.rowCount()):
            logical = vh.logicalIndex(vrow)
            ev_idx = self._row_event_idx[logical] if 0 <= logical < len(self._row_event_idx) else -1
            if ev_idx >= 0:
                order.append(ev_idx)
        if not order: return
        # Reorder events to match
        try:
            new_events = [self._current.events[i] for i in order]
        except IndexError:
            return
        self._current.events = new_events
        # Reset the visual-section mapping & rebuild from the new event order.
        vh.blockSignals(True)
        for i in range(self._table.rowCount()):
            vh.moveSection(vh.visualIndex(i), i)
        vh.blockSignals(False)
        self._fill_table(self._current)
        self._storage.save_macros(self._macros)
        self._set_status("Event order saved.", "#a6e3a1")

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION: Macro CRUD
    # ══════════════════════════════════════════════════════════════════════════

    def _new_macro(self):
        m = Macro(name=f"Macro {len(self._macros) + 1}")
        self._macros.append(m); self._refresh_list()
        self._macro_list.setCurrentRow(len(self._macros) - 1)
        self._storage.save_macros(self._macros)

    def _dup_macro(self):
        if not self._current: return
        m = self._current.clone(); self._macros.append(m); self._refresh_list()
        self._macro_list.setCurrentRow(len(self._macros) - 1)
        self._storage.save_macros(self._macros)

    def _del_macro(self):
        if not self._current: return
        if QMessageBox.question(
            self, "Delete", f"Delete '{self._current.name}'?  (Ctrl+Z to undo)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes: return
        mid = self._current.id
        if mid in self._players: self._players[mid].stop()
        self._deleted_macro = copy.deepcopy(self._current)   # undo buffer
        self._macros = [m for m in self._macros if m.id != mid]
        self._current = None; self._table.setRowCount(0)
        self._refresh_list(); self._storage.save_macros(self._macros)
        self._rebuild_hotkeys()
        self._set_status(f"Deleted '{self._deleted_macro.name}' — press Ctrl+Z to undo", "#fab387")

    def _undo_delete(self):
        if not self._deleted_macro: return
        self._macros.append(self._deleted_macro)
        self._deleted_macro = None
        self._refresh_list()
        self._macro_list.setCurrentRow(len(self._macros) - 1)
        self._storage.save_macros(self._macros)
        self._rebuild_hotkeys()
        self._set_status("Delete undone.", "#a6e3a1")

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION: Settings Handlers
    #   Every handler:
    #     1. Mutates the current macro's state
    #     2. Persists to disk immediately (no waiting for 30s autosave)
    #     3. Reapplies any side-effects (hotkeys, button state, list refresh)
    # ══════════════════════════════════════════════════════════════════════════

    def _persist_current(self):
        """Save macros to disk right now, with a tiny status pulse so the user
        can see the change took effect."""
        try:
            self._storage.save_macros(self._macros)
        except Exception as e:
            self._set_status(f"Save failed: {e}", "#f38ba8")

    def _on_name_changed(self, text: str):
        if not self._current: return
        self._current.name = text
        self._refresh_list()
        self._persist_current()

    def _on_hotkey_changed(self):
        if not self._current: return
        self._current.trigger_hotkey = self._hotkey_edit.text().strip()
        self._rebuild_hotkeys()
        self._persist_current()
        self._set_status(
            f"Trigger hotkey: {self._current.trigger_hotkey or '(none)'}", "#89b4fa")

    def _on_repeat_changed(self, v: int):
        if not self._current: return
        self._current.repeat_count = v
        self._persist_current()

    def _on_speed_changed(self, v: float):
        if not self._current: return
        self._current.speed_multiplier = v
        self._persist_current()

    def _on_move_chk_changed(self, s: int):
        if not self._current: return
        self._current.record_mouse_move = bool(s)
        self._persist_current()

    def _on_use_target_changed(self, s: int):
        en = bool(s)
        for btn in (self._capture_btn, self._pick_btn, self._drag_pick_btn1,
                    self._pick_btn2, self._clear_btn2, self._drag_pick_btn2,
                    self._pick_btn3, self._clear_btn3, self._drag_pick_btn3):
            btn.setEnabled(en)
        if not self._current: return
        self._current.use_target_window = en
        self._persist_current()

    def _pick_window(self, slot: int = 1):
        """
        Open a dialog listing every visible window with HWND, PID, and title.
        Selecting one sets it as the macro's target window for the given slot (1/2/3).
        """
        if not self._current: return
        from PyQt6.QtWidgets import QDialog, QListWidget, QDialogButtonBox, QVBoxLayout
        windows = enumerate_windows()
        if not windows:
            QMessageBox.information(self, "Pick Window", "No visible windows found.")
            return
        slot_label = {1: "Window 1 (required)", 2: "Window 2 (optional)", 3: "Window 3 (optional)"}
        dlg = QDialog(self); dlg.setWindowTitle(f"Pick Target — {slot_label.get(slot,'')}")
        dlg.setMinimumSize(640, 460)
        v = QVBoxLayout(dlg)
        lbl = QLabel(
            f"Select <b>{slot_label.get(slot,'')}</b>.  "
            "Columns:  HWND  ·  PID  ·  Title")
        lbl.setStyleSheet("color: #a6adc8;")
        v.addWidget(lbl)
        lw = QListWidget()
        lw.setStyleSheet(
            "QListWidget { background: #181825; border: 1px solid #313244; "
            "border-radius: 6px; padding: 4px; font-family: Consolas, monospace; }")
        cur_title = {1: self._current.target_window_title,
                     2: getattr(self._current, "target_window_2", ""),
                     3: getattr(self._current, "target_window_3", "")}.get(slot, "")
        for hwnd, pid, title in windows:
            it = QListWidgetItem(f"0x{hwnd:08X}   PID {pid:>6}   {title}")
            it.setData(Qt.ItemDataRole.UserRole, (hwnd, pid, title))
            lw.addItem(it)
            if title == cur_title:
                lw.setCurrentItem(it)
        lw.itemDoubleClicked.connect(lambda _i: dlg.accept())
        v.addWidget(lw, 1)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject)
        v.addWidget(bb)
        if dlg.exec() != QDialog.DialogCode.Accepted: return
        item = lw.currentItem()
        if not item: return
        hwnd, pid, title = item.data(Qt.ItemDataRole.UserRole)
        self._set_window_slot(slot, title)
        self._storage.save_macros(self._macros)
        self._set_status(f"Win{slot}: 0x{hwnd:08X}  PID {pid}  {title}", "#89b4fa")

    def _set_window_slot(self, slot: int, title: str):
        """Write title into the correct macro field + update the corresponding label."""
        if not self._current: return
        if slot == 1:
            self._current.target_window_title = title
            self._win_title_lbl.setText(title or "(none)")
        elif slot == 2:
            self._current.target_window_2 = title
            self._win2_title_lbl.setText(title or "(none)")
        elif slot == 3:
            self._current.target_window_3 = title
            self._win3_title_lbl.setText(title or "(none)")

    def _clear_window_slot(self, slot: int):
        """Clear an optional window slot (2 or 3)."""
        self._set_window_slot(slot, "")
        self._storage.save_macros(self._macros)
        self._set_status(f"Window {slot} cleared.", "#a6adc8")

    def _on_backend_changed(self, idx: int):
        if not self._current: return
        name = self._backend_combo.itemData(idx) or "auto"
        self._current.input_backend = name
        self._storage.save_macros(self._macros)
        # Show pretty name in status bar
        pretty = next((d for n, d, _ in BACKEND_CHOICES if n == name), name)
        self._set_status(f"Backend: {pretty}", "#89b4fa")
        # If Detours selected, give the user a heads-up on what's needed
        if name == "detours" and not (HOOK_DLL_X64.exists() or HOOK_DLL_X86.exists()):
            self._set_status(
                f"Backend: Detours — no hook DLL built yet.  Click 'Hook DLL…' for build steps.",
                "#fab387")

    def _reload_hook(self):
        """Manual eject + reinject the hook DLL into the current target."""
        if not self._current or not self._current.use_target_window or \
           not self._current.target_window_title:
            self._set_status("Reload Hook: pick a target window first.", "#fab387")
            return
        h = find_window_hwnd(self._current.target_window_title)
        if not h:
            self._set_status("Reload Hook: target window not found.", "#f38ba8")
            return
        pid = get_window_pid(h)
        self._set_status(f"Reloading hook into PID {pid}…", "#89b4fa")
        # Run in a thread so the UI doesn't freeze
        def _worker():
            ok, msg = _inject_hook(pid, force_reload=True)
            QTimer.singleShot(0, lambda:
                self._set_status(("Reload Hook OK: " if ok else "Reload Hook FAILED: ") + msg,
                                  "#a6e3a1" if ok else "#f38ba8"))
        threading.Thread(target=_worker, daemon=True).start()

    def _open_hook_dir(self):
        """Show hook-build status (both bitnesses) and open the hooks folder."""
        x64_dll = HOOK_DLL_X64.exists();  x64_inj = HOOK_INJECTOR_X64.exists()
        x86_dll = HOOK_DLL_X86.exists();  x86_inj = HOOK_INJECTOR_X86.exists()
        # Detect target context
        pid = None
        if self._current and self._current.use_target_window and self._current.target_window_title:
            h = find_window_hwnd(self._current.target_window_title)
            if h: pid = get_window_pid(h)
        arch_label = "(no target picked)"
        pipe_ok = False
        if pid:
            is64 = _is_process_64bit(pid)
            arch_label = "x64" if (is64 is None or is64) else "x86"
            pipe_ok = _pipe_exists(pid)

        msg = QMessageBox(self)
        msg.setWindowTitle("API Hook (Detours) Status")
        msg.setIcon(QMessageBox.Icon.Information)
        lines = [
            f"<b>Hook folder:</b> {HOOK_DIR}",
            "",
            "<b>x64 build</b>",
            f"&nbsp;&nbsp;dinput_hook_x64.dll  : {'✓ found' if x64_dll else '✗ NOT BUILT'}",
            f"&nbsp;&nbsp;injector_x64.exe     : {'✓ found' if x64_inj else '✗ NOT BUILT'}",
            "",
            "<b>x86 build</b>",
            f"&nbsp;&nbsp;dinput_hook_x86.dll  : {'✓ found' if x86_dll else '✗ NOT BUILT'}",
            f"&nbsp;&nbsp;injector_x86.exe     : {'✓ found' if x86_inj else '✗ NOT BUILT'}",
        ]
        if pid:
            lines += [
                "",
                f"<b>Current target:</b> PID {pid}  (detected {arch_label})",
                f"&nbsp;&nbsp;Pipe: {'✓ injected — ready' if pipe_ok else '✗ not injected (will auto-inject on Play)'}",
            ]
        if not (x64_dll and x64_inj) and not (x86_dll and x86_inj):
            lines += [
                "",
                "<b>To build:</b>",
                "1. Install Visual Studio Build Tools with 'Desktop C++'.",
                "2. git clone https://github.com/microsoft/Detours C:\\src\\Detours",
                "3. From the matching VS prompt, nmake in C:\\src\\Detours.",
                "4. Compile dinput_hook.cpp + injector.cpp against the matching detours.lib.",
            ]
        msg.setText("<br>".join(lines))
        msg.setStandardButtons(
            QMessageBox.StandardButton.Open | QMessageBox.StandardButton.Close)
        msg.button(QMessageBox.StandardButton.Open).setText("Open folder")
        if msg.exec() == QMessageBox.StandardButton.Open:
            try:
                os.startfile(str(HOOK_DIR))
            except Exception as e:
                self._set_status(f"Could not open folder: {e}", "#f38ba8")

    def _rebuild_hotkeys(self):
        hmap = {}
        for m in self._macros:
            if m.trigger_hotkey.strip():
                try: hmap[user_hotkey_to_pynput(m.trigger_hotkey.strip())] = m.id
                except Exception: pass
        self._hotkeys.update(hmap)

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION: Shortcut Editor
    # ══════════════════════════════════════════════════════════════════════════

    def _on_sc_changed(self, action: str, ks: QKeySequence):
        """
        Shortcut edited in the Shortcuts tab.  We immediately:
          1. Update _sc_config + save it to disk.
          2. Rebuild BOTH the QShortcut (in-app) AND the pynput global hotkey
             so the new binding fires from any window straight away.
          3. Refresh all tooltips and the hint bar.
        """
        self._sc_config[action] = ks.toString()
        self._storage.save_shortcuts(self._sc_config)
        # Rebuild everything that depends on shortcuts — this updates both
        # QShortcut keys AND pynput GlobalHotKeys at once.
        self._apply_shortcuts()
        label = next((l for a, l, _ in SHORTCUT_DEFS if a == action), action)
        self._set_status(f"Shortcut '{label}' → {ks.toString() or '(none)'}", "#89b4fa")

    def _reset_shortcuts(self):
        self._sc_config = dict(DEFAULT_SHORTCUTS)
        self._storage.save_shortcuts(self._sc_config)
        # Update QKeySequenceEdit widgets
        for action, edit in self._sc_edits.items():
            edit.blockSignals(True)
            edit.setKeySequence(QKeySequence(DEFAULT_SHORTCUTS.get(action, "")))
            edit.blockSignals(False)
        self._apply_shortcuts()
        if hasattr(self, "_sc_hint_updater"):
            self._sc_hint_updater()

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION: Window Capture
    # ══════════════════════════════════════════════════════════════════════════

    def _capture_window(self):
        if not self._current or self._capture_ctr > 0: return
        self._capture_ctr = 3
        self._capture_btn.setEnabled(False)
        self._tick_capture()

    def _tick_capture(self):
        n = self._capture_ctr
        if n > 0:
            self._capture_btn.setText(f"Capturing in {n}s…")
            self._capture_ctr -= 1
            QTimer.singleShot(1000, self._tick_capture)
        else:
            title = get_foreground_title()
            if self._current:
                self._current.target_window_title = title
                self._win_title_lbl.setText(title or "(none)")
                self._storage.save_macros(self._macros)
            self._capture_btn.setText("Capture (3s)")
            self._capture_btn.setEnabled(
                bool(self._current and self._current.use_target_window))
            self._set_status(f"Win1 set: {title or '(none)'}", "#89b4fa")

    # ── Drag-pick ─────────────────────────────────────────────────────────────

    def _start_drag_pick(self, slot: int):
        """
        Minimise, show a hint overlay, capture the next left-click via pynput,
        resolve the window under the cursor, then restore and set the slot.
        """
        if not self._current: return
        self.showMinimized()
        from PyQt6.QtWidgets import QDialog, QLabel, QVBoxLayout
        hint = QDialog(self)
        hint.setWindowTitle("Drag-Pick Window")
        hint.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.Tool)
        hint.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        lbl = QLabel(
            f"  🎯  Click any window to set it as <b>Window {slot}</b>.\n"
            "  Press  ESC  to cancel.  ")
        lbl.setStyleSheet(
            "background: #313244; color: #cdd6f4; font-size: 14px; padding: 18px; "
            "border: 2px solid #89b4fa; border-radius: 8px;")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        QVBoxLayout(hint).addWidget(lbl)
        hint.adjustSize()
        hint.move(100, 100)
        hint.show()

        result: list = [None]

        def _on_click(x, y, button, pressed):
            if not pressed or button != ms_lib.Button.left: return
            hwnd = _u32.WindowFromPoint(ctypes.wintypes.POINT(int(x), int(y)))
            # Walk to root (top-level) window
            while True:
                parent = _u32.GetParent(hwnd)
                if not parent: break
                hwnd = parent
            n = _u32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                _u32.GetWindowTextW(hwnd, buf, n + 1)
                result[0] = buf.value
            mouse_listener.stop()
            return False   # suppress the click from reaching the target

        def _on_key(key):
            if key == Key.esc:
                mouse_listener.stop()
            return False

        mouse_listener = ms_lib.Listener(on_click=_on_click)
        kb_listener    = kb_lib.Listener(on_press=_on_key)

        def _wait():
            mouse_listener.join()
            kb_listener.stop()
            QTimer.singleShot(0, _done)

        def _done():
            hint.close()
            self.showNormal(); self.raise_(); self.activateWindow()
            if result[0]:
                self._set_window_slot(slot, result[0])
                self._storage.save_macros(self._macros)
                self._set_status(f"Win{slot} set via drag-pick: {result[0]}", "#89b4fa")
            else:
                self._set_status("Drag-pick cancelled.", "#a6adc8")

        mouse_listener.start()
        kb_listener.start()
        threading.Thread(target=_wait, daemon=True).start()

    # ── Log copy helpers ──────────────────────────────────────────────────────

    def _copy_log_file(self, path: Path, label: str):
        """Read `path` and put its contents on the clipboard. Show status."""
        try:
            text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
        except Exception as e:
            self._set_status(f"Cannot read {label}: {e}", "#f38ba8"); return
        if not text.strip():
            self._set_status(f"{label} is empty.", "#fab387"); return
        QApplication.clipboard().setText(text)
        lines = text.count("\n")
        self._set_status(f"{label} copied ({lines} lines).", "#a6e3a1")

    def _copy_diag_log(self):
        self._copy_log_file(Path.home() / ".macro_recorder" / "dll_hook.log",
                            "Diagnostic log")

    def _copy_crash_log(self):
        # Flush the faulthandler file before copying so latest content is on disk
        try:
            _crash_fp.flush()
        except Exception: pass
        self._copy_log_file(_CRASH_LOG_PATH, "Crash log")

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION: Recording
    # ══════════════════════════════════════════════════════════════════════════

    def _toggle_record(self):
        if self._recording: self._stop_recording()
        else:               self._start_recording()

    def _build_shortcut_filter(self) -> set:
        """
        Build the set of pynput-style key strings the recorder must NOT capture.
        Includes the FINAL key of every currently bound app shortcut, so that
        pressing the record / play / stop hotkey never adds itself to the macro.
        Modifier keys (Ctrl/Alt/Shift/Meta) are intentionally NOT filtered.
        """
        skip = set()
        alias = {"del":"delete","return":"enter","esc":"escape",
                 "ins":"insert","pgup":"page_up","pgdn":"page_down"}
        for action, binding in self._sc_config.items():
            if not binding: continue
            final = binding.split("+")[-1].strip().lower()
            if not final or final in ("ctrl","control","alt","shift","meta","cmd","win"):
                continue
            final = alias.get(final, final)
            if len(final) == 1:
                skip.add(final)
            else:
                skip.add(f"Key.{final}")
        return skip

    def _start_recording(self):
        if not self._current: self._new_macro()
        self._recording = True
        self._current.events = []; self._fill_table(self._current)
        self._btn_record.setText("⏹  Stop Rec")
        self._set_status("● RECORDING — press the Record shortcut again to stop", "#f38ba8")
        self._flash_timer.start(600)
        sound_record_start()
        # Resolve target window for client-coord recording (portable macros)
        target_hwnd = None
        if self._current.use_target_window and self._current.target_window_title:
            target_hwnd = find_window_hwnd(self._current.target_window_title)
            if target_hwnd:
                self._set_status(
                    f"● RECORDING into window 0x{target_hwnd:08X} — "
                    "mouse coords are CLIENT-relative.  Press the Record shortcut to stop.",
                    "#f38ba8")
        self._recorder = RecorderThread(
            record_mouse_move=self._current.record_mouse_move,
            filter_keys=self._build_shortcut_filter(),
            target_hwnd=target_hwnd)
        self._recorder.captured.connect(self._on_captured)
        self._recorder.done.connect(self._on_rec_done)
        self._recorder.begin()

    def _stop_recording(self):
        if self._recorder: self._recorder.end()

    def _on_captured(self, ev: dict):
        if not self._current: return
        self._current.events.append(ev)
        # Append a normal row (without group computation during live capture —
        # grouping happens on next _fill_table, which runs after recording ends)
        row = self._table.rowCount()
        self._table.insertRow(row)
        n = len(self._current.events)
        self._table.setItem(row, 0, QTableWidgetItem(str(n)))
        self._table.setItem(row, 1, QTableWidgetItem(f"{ev['timestamp']:.3f}"))
        ti = QTableWidgetItem(ev["event_type"])
        ti.setForeground(QColor(EVENT_COLORS.get(ev["event_type"], "#cdd6f4")))
        self._table.setItem(row, 2, ti)
        self._table.setItem(row, 3, QTableWidgetItem(event_summary(ev)))
        self._row_event_idx.append(n - 1)
        if ev["event_type"] not in {et for et, cb in self._ev_filters.items() if cb.isChecked()}:
            self._table.setRowHidden(row, True)
        self._table.scrollToBottom()
        self._ev_count.setText(f"{n} events")

    def _on_rec_done(self):
        self._recording = False; self._flash_timer.stop()
        self._btn_record.setText("⏺  Record")
        n = len(self._current.events) if self._current else 0
        self._set_status(f"Recording stopped — {n} events captured.", "#a6adc8")
        sound_record_stop()
        self._storage.save_macros(self._macros)

    def _flash_record_btn(self):
        self._flash_state = not self._flash_state
        self._btn_record.setText("⏹  Stop Rec ●" if self._flash_state else "⏹  Stop Rec  ")

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION: Playback
    # ══════════════════════════════════════════════════════════════════════════

    def _play_current(self):
        if self._recording: return
        if self._current:   self._play_macro(self._current)

    def _play_macro(self, m: Macro):
        if not m.events:
            self._set_status("No events to play.", "#fab387"); return
        if m.id in self._players: return
        p = PlayerThread(m)
        p.started_sig.connect(self._on_play_started)
        p.stopped_sig.connect(self._on_play_stopped)
        p.progress_sig.connect(self._on_play_progress)
        p.mode_sig.connect(self._on_play_mode)
        self._players[m.id] = p; p.start()

    def _stop_current(self):
        if not self._current: return
        p = self._players.get(self._current.id)
        if p is None: return
        try:
            p.stop()
            self._set_status(f"Stopping '{self._current.name}'…", "#fab387")
        except Exception as e:
            _log_crash(f"_stop_current: {e}")

    def _stop_all(self):
        for p in list(self._players.values()): p.stop()

    def _on_play_started(self, mid: str):
        self._refresh_list(); self._update_play_btns()
        m = self._find(mid)
        if m:
            m.run_count += 1
            self._storage.save_macros(self._macros)
        self._set_status(f"▶ Playing: {m.name if m else mid}", "#a6e3a1")
        self._update_active_label()
        sound_play_start(); speak(m.name if m else mid)
        # Start the runtime timer
        self._play_start_times[mid] = time.perf_counter()
        if not self._runtime_timer.isActive():
            self._runtime_timer.start(200)

    def _on_play_mode(self, mid: str, backend_name: str):
        m = self._find(mid)
        label = m.name if m else mid
        # New: error reason packed as "error:<text>"
        if backend_name.startswith("error:"):
            reason = backend_name[6:] or "no reason given"
            # Remember so _on_play_stopped doesn't overwrite with "Playback complete"
            self._play_failure_msg[mid] = f"⚠ '{label}' — backend failed: {reason}"
            self._set_status(self._play_failure_msg[mid], "#f38ba8")
            return
        if backend_name == "none":
            self._set_status(f"⚠  '{label}' — no usable backend.", "#f38ba8")
        elif backend_name == "winmsg":
            self._set_status(f"▶ '{label}' — silent background injection (winmsg).", "#a6e3a1")
        elif backend_name == "postmsg":
            self._set_status(f"▶ '{label}' — PostMessage async injection.", "#a6e3a1")
        elif backend_name == "detours":
            self._set_status(
                f"▶ Playing '{label}' via API Hook (Detours).  Hook DLL log: "
                f"~/.macro_recorder/dll_hook.log",
                "#a6e3a1")
        elif backend_name == "pynput":
            self._set_status(
                f"⚠  '{label}' — pynput backend: your real mouse & keyboard WILL be used.",
                "#fab387")
        elif backend_name == "interception":
            self._set_status(f"▶ '{label}' — Interception driver (kernel input).", "#89b4fa")
        elif backend_name == "serial_hid":
            self._set_status(f"▶ '{label}' — Serial HID (real USB device).", "#cba6f7")

    def _on_play_stopped(self, mid: str):
        self._players.pop(mid, None); self._refresh_list(); self._update_play_btns()
        self._play_start_times.pop(mid, None)
        if not self._players and self._runtime_timer.isActive():
            self._runtime_timer.stop()
            self._runtime_lbl.setText("")
        # If the player aborted due to a backend failure, keep that warning
        # visible instead of overwriting with the cheerful "Playback complete".
        failed = self._play_failure_msg.pop(mid, None)
        if failed:
            self._set_status(failed, "#f38ba8")
        elif not self._players:
            self._set_status("Playback complete.", "#a6adc8")
        self._update_active_label()
        sound_play_stop()
        # Don't speak "MACRO stopped" for a failure — it's misleading
        if not failed: speak("MACRO stopped")

    def _on_play_progress(self, mid: str, idx: int, total: int):
        if self._current and self._current.id == mid:
            if idx < self._table.rowCount():
                self._table.selectRow(idx)
                self._table.scrollTo(self._table.model().index(idx, 0))

    def _update_play_btns(self):
        any_p = bool(self._players)
        cur_p = bool(self._current and self._current.id in self._players)
        self._btn_stop.setEnabled(cur_p)
        self._btn_stop_all.setEnabled(any_p)

    def _update_active_label(self):
        n = len(self._players)
        total = sum(m.run_count for m in self._macros)
        self._active_lbl.setText(f"  ▶ {total} execution{'s' if total != 1 else ''}" if total else "")
        self._active_lbl.setToolTip(f"{n} macro(s) running" if n else "No macros running")

    def _tick_runtime(self):
        """Refresh the runtime label while any macro is playing."""
        if not self._play_start_times:
            self._runtime_lbl.setText("")
            return
        now = time.perf_counter()
        # Show the longest-running playback (or just the first one)
        elapsed_max = max(now - t for t in self._play_start_times.values())
        m, s = divmod(int(elapsed_max), 60)
        h, m = divmod(m, 60)
        if h:
            self._runtime_lbl.setText(f"⏱ {h:d}:{m:02d}:{s:02d}")
        else:
            self._runtime_lbl.setText(f"⏱ {m:d}:{s:02d}")

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION: Event Editor
    # ══════════════════════════════════════════════════════════════════════════

    def _clear_events(self):
        if not self._current: return
        if QMessageBox.question(
            self, "Clear", "Clear all recorded events?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) == QMessageBox.StandardButton.Yes:
            self._current.events = []; self._table.setRowCount(0)
            self._ev_count.setText("0 events")
            self._storage.save_macros(self._macros)

    def _del_selected_events(self):
        if not self._current: return
        # Translate selected table rows → event indices (skip group headers)
        ev_indices = set()
        for idx in self._table.selectedIndexes():
            r = idx.row()
            if 0 <= r < len(self._row_event_idx):
                e = self._row_event_idx[r]
                if e >= 0: ev_indices.add(e)
        for e in sorted(ev_indices, reverse=True):
            if e < len(self._current.events):
                self._current.events.pop(e)
        self._fill_table(self._current)
        self._storage.save_macros(self._macros)

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION: Hotkey Callbacks  (called from pynput thread)
    # ══════════════════════════════════════════════════════════════════════════

    def _hotkey_fired(self, macro_id: str):
        if self._recording: return
        m = self._find(macro_id)
        if not m: return
        if macro_id in self._players: self._players[macro_id].stop()
        else: self._play_macro(m)

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION: Utilities
    # ══════════════════════════════════════════════════════════════════════════

    def _find(self, macro_id: str) -> Optional[Macro]:
        return next((m for m in self._macros if m.id == macro_id), None)

    def _set_status(self, msg: str, color: str = "#a6adc8"):
        self._status.setText(msg)
        self._status.setStyleSheet(f"color: {color};")

    def _quit_app(self):
        """Fully clean up then exit — avoids PyInstaller temp-dir removal error."""
        self._stop_all()
        # Give player threads up to 1 s to shut down cleanly
        deadline = time.perf_counter() + 1.0
        while self._players and time.perf_counter() < deadline:
            time.sleep(0.05)
        self._hotkeys.stop(); self._app_hotkeys.stop()
        self._storage.save_macros(self._macros)
        # Close the faulthandler file so PyInstaller can clean _MEI* on exit
        try:
            faulthandler.disable()
            _crash_fp.close()
        except Exception: pass
        QApplication.quit()

    def closeEvent(self, event):
        self._storage.save_macros(self._macros)
        event.ignore(); self.hide()
        self._tray.showMessage("QytCroRec",
            "Running in system tray. Double-click to restore.",
            QSystemTrayIcon.MessageIcon.Information, 2500)


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("QytCroRec")
    app.setQuitOnLastWindowClosed(False)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()


# ══════════════════════════════════════════════════════════════════════════════
# AUTO-UPDATE — HOSTING SETUP (one-time)
# ══════════════════════════════════════════════════════════════════════════════
#
# To make every installed copy auto-update when you push a new version:
#
# 1. Create a GitHub repository (public — for `raw.githubusercontent.com` URLs).
#
# 2. Put two files in the repo root:
#      • macro_recorder.py   — the script itself
#      • version.json        — small JSON, e.g.:
#            {"version": "1.5", "notes": "Global hotkeys, dynamic tooltips"}
#
# 3. Edit the two URLs near the top of this file:
#      UPDATE_VERSION_URL = "https://raw.githubusercontent.com/<user>/<repo>/main/version.json"
#      UPDATE_SCRIPT_URL  = "https://raw.githubusercontent.com/<user>/<repo>/main/macro_recorder.py"
#    Commit + push that change first so every existing copy already has the URLs.
#
# 4. Push updates: bump __version__ in the script AND in version.json, then
#    push both files.  Every running copy will see the new version at startup,
#    prompt the user to update, download the new script, swap it in, and
#    relaunch.  The previous version is kept as `macro_recorder.prev.py`.
#
# Notes:
#   • To disable on a particular machine: set AUTO_UPDATE_ENABLED = False.
#   • The download is verified to contain the string "Macro Recorder" and to
#     be at least 1 KB before installing — corrupted downloads are rejected.
#   • Update checks run on a background thread; if the URL is unreachable
#     they silently fail.  Startup never blocks waiting on the network.
# ══════════════════════════════════════════════════════════════════════════════

