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

__version__ = "1.25"

# ── AUTO-UPDATE CONFIGURATION ────────────────────────────────────────────────
# Set these two URLs to enable auto-update.  See README at bottom of file.
UPDATE_VERSION_URL = "https://raw.githubusercontent.com/265ada/QytsCreations/main/QytsCreations/version.json"
UPDATE_SCRIPT_URL  = "https://raw.githubusercontent.com/265ada/QytsCreations/main/QytsCreations/QytCroRec.py"
UPDATE_EXE_URL     = "https://github.com/265ada/QytsCreations/releases/latest/download/QytCroRec.exe"
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
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QRect, QRectF
from PyQt6.QtGui import (
    QIcon, QColor, QFont, QPixmap, QPainter, QAction,
    QShortcut, QKeySequence,
)
from pynput import keyboard as kb_lib, mouse as ms_lib
from pynput.keyboard import Key, KeyCode
from pynput.mouse import Button

# ── Optional OCR for Pixel Bot Guard ─────────────────────────────────────────
try:
    from PIL import Image as _Image, ImageGrab as _ImageGrab, ImageFilter as _ImageFilter
    _PILLOW_OK = True
except ImportError:
    _PILLOW_OK = False

try:
    import pytesseract as _pytesseract
    _TESSERACT_OK = True
except ImportError:
    _TESSERACT_OK = False

def _configure_bundled_tesseract():
    """
    When running as a PyInstaller single-file exe, tesseract.exe and tessdata/
    are extracted to sys._MEIPASS.  Point pytesseract + TESSDATA_PREFIX there
    so OCR works out-of-the-box without any user installation.
    """
    if not _TESSERACT_OK:
        return
    meipass = getattr(sys, "_MEIPASS", None)
    if not meipass:
        return   # not a bundled exe — use whatever is on PATH
    tess_exe = Path(meipass) / "tesseract.exe"
    tessdata = Path(meipass) / "tessdata"
    if tess_exe.exists():
        _pytesseract.pytesseract.tesseract_cmd = str(tess_exe)
    if tessdata.exists():
        # TESSDATA_PREFIX must point to the *parent* of the tessdata folder
        os.environ.setdefault("TESSDATA_PREFIX", meipass)

_configure_bundled_tesseract()


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
    # Pixel Bot Guard settings
    pixel_guard_enabled:           bool  = False
    pixel_guard_red_flags:         list  = field(default_factory=list)
    pixel_guard_correction_key:    str   = "b"
    pixel_guard_correction_macro:  str   = ""   # macro id, or "" = use key
    pixel_guard_cap_x_pct:         float = 0.75
    pixel_guard_cap_y_pct:         float = 0.02
    pixel_guard_cap_w_pct:         float = 0.24
    pixel_guard_cap_h_pct:         float = 0.06

    # Lane-level enable/disable (lane 0 = primary, always enabled)
    lane_enabled: bool  = True

    def clone(self) -> "Macro":
        m = copy.deepcopy(self)
        m.id = str(uuid.uuid4())
        m.name = f"{m.name} (copy)"
        m.created_at = time.time()
        return m


# ──────────────────────────────────────────────────────────────────────────────
# MACRO GROUP — primary lane + up to 2 optional secondary lanes.
#   Lane 0 = Primary (always runs, controls repeat).
#   Lane 1 = Secondary 1 (runs after Primary if lane_enabled=True).
#   Lane 2 = Secondary 2 (runs after Secondary 1 if lane_enabled=True).
# ──────────────────────────────────────────────────────────────────────────────

_GROUP_FIELDS: set = set()   # filled after dataclass defined

@dataclass
class MacroGroup:
    id:   str  = field(default_factory=lambda: str(uuid.uuid4()))
    name: str  = "New Group"
    # Serialised as a list of Macro dicts; held as list[Macro] at runtime.
    lanes: list = field(default_factory=list)

    def get_lane(self, idx: int) -> Optional["Macro"]:
        if 0 <= idx < len(self.lanes):
            return self.lanes[idx]
        return None

    def ensure_lanes(self, n: int = 3):
        """Pad lanes list to at least n entries with blank Macros."""
        labels = ["Primary", "Secondary 1", "Secondary 2"]
        while len(self.lanes) < n:
            i = len(self.lanes)
            m = Macro(name=labels[i] if i < len(labels) else f"Lane {i+1}")
            if i > 0:
                m.lane_enabled = False
            self.lanes.append(m)

    def active_lanes(self) -> list:
        """Return lanes that should run (lane 0 always, others only if enabled+has events)."""
        out = []
        for i, lane in enumerate(self.lanes):
            if i == 0 or (lane.lane_enabled and lane.events):
                out.append(lane)
        return out


_GROUP_FIELDS = {f.name for f in dataclasses.fields(MacroGroup)}


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

def _macro_from_dict(d: dict) -> Macro:
    events = d.pop("events", [])
    m = Macro(**{k: v for k, v in d.items() if k in _MACRO_FIELDS})
    m.events = events
    return m


class Storage:
    def __init__(self):
        self.dir = Path.home() / ".macro_recorder"
        self.dir.mkdir(exist_ok=True)
        self.groups_path    = self.dir / "groups.json"
        self.macros_path    = self.dir / "macros.json"   # legacy
        self.shortcuts_path = self.dir / "shortcuts.json"

    # ── Groups (new primary format) ───────────────────────────────────────────

    def save_groups(self, groups: list):
        """Serialise list[MacroGroup] → groups.json."""
        def _ser_group(g: MacroGroup) -> dict:
            return {
                "id":    g.id,
                "name":  g.name,
                "lanes": [asdict(lane) for lane in g.lanes],
            }
        bak = self.groups_path.with_suffix(".bak.json")
        if self.groups_path.exists():
            import shutil; shutil.copy2(self.groups_path, bak)
        self.groups_path.write_text(
            json.dumps([_ser_group(g) for g in groups], indent=2), encoding="utf-8")

    def load_groups(self) -> list:
        """Load groups.json; if absent, migrate old macros.json (each macro → 1-lane group)."""
        if self.groups_path.exists():
            try:
                groups = []
                for gd in json.loads(self.groups_path.read_text(encoding="utf-8")):
                    g = MacroGroup(id=gd.get("id", str(uuid.uuid4())),
                                   name=gd.get("name", "Group"))
                    for ld in gd.get("lanes", []):
                        ld2 = dict(ld)
                        g.lanes.append(_macro_from_dict(ld2))
                    g.ensure_lanes()
                    groups.append(g)
                return groups
            except Exception as e:
                print(f"Groups load error: {e}")
                return []

        # ── Migrate from legacy macros.json ──────────────────────────────────
        if self.macros_path.exists():
            try:
                groups = []
                for d in json.loads(self.macros_path.read_text(encoding="utf-8")):
                    d2 = dict(d)
                    m = _macro_from_dict(d2)
                    g = MacroGroup(id=str(uuid.uuid4()), name=m.name)
                    g.lanes.append(m)
                    g.ensure_lanes()
                    groups.append(g)
                return groups
            except Exception as e:
                print(f"Legacy macro load error: {e}")
        return []

    # ── Legacy helpers (kept so existing call-sites don't break) ─────────────

    def save_macros(self, macros: list):
        """Back-compat shim — wraps each Macro in a group and calls save_groups."""
        groups = []
        for m in macros:
            g = MacroGroup(id=str(uuid.uuid4()), name=m.name)
            g.lanes.append(m); g.ensure_lanes()
            groups.append(g)
        self.save_groups(groups)

    def load_macros(self) -> list:
        """Back-compat — returns flat list of primary-lane Macros."""
        return [g.lanes[0] for g in self.load_groups() if g.lanes]

    # ── Shortcuts ─────────────────────────────────────────────────────────────

    def save_shortcuts(self, sc: dict):
        self.shortcuts_path.write_text(json.dumps(sc, indent=2), encoding="utf-8")

    def load_shortcuts(self) -> dict:
        if not self.shortcuts_path.exists():
            return dict(DEFAULT_SHORTCUTS)
        try:
            saved = json.loads(self.shortcuts_path.read_text(encoding="utf-8"))
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

# ══════════════════════════════════════════════════════════════════════════════
# PIXEL BOT GUARD — screen OCR helpers
# ══════════════════════════════════════════════════════════════════════════════

def _pixel_ocr_region(hwnd: Optional[int],
                      cap_x_pct: float, cap_y_pct: float,
                      cap_w_pct: float, cap_h_pct: float) -> str:
    """
    Capture the configured sub-region of hwnd (or foreground window if None)
    and return pytesseract OCR text.  Returns '' on any failure.
    """
    if not _PILLOW_OK or not _TESSERACT_OK:
        return ""
    try:
        if not hwnd:
            hwnd = _u32.GetForegroundWindow()
        rect = ctypes.wintypes.RECT()
        _u32.GetWindowRect(hwnd, ctypes.byref(rect))
        wx, wy = rect.left, rect.top
        ww = max(rect.right  - rect.left, 1)
        wh = max(rect.bottom - rect.top,  1)
        x1 = wx + int(ww * cap_x_pct)
        y1 = wy + int(wh * cap_y_pct)
        x2 = x1 + max(int(ww * cap_w_pct), 4)
        y2 = y1 + max(int(wh * cap_h_pct), 4)
        img = _ImageGrab.grab(bbox=(x1, y1, x2, y2))
        # Scale 3× for better OCR accuracy on small HUD text
        img = img.resize((img.width * 3, img.height * 3), _Image.LANCZOS)
        img = img.convert("L")  # grayscale
        img = img.filter(_ImageFilter.SHARPEN)
        text = _pytesseract.image_to_string(img, config="--psm 7 --oem 3").strip()
        return text
    except Exception as e:
        _log_crash(f"[pixel_guard] OCR error: {e}")
        return ""


def _pixel_flags_match(text: str, red_flags: list) -> Optional[str]:
    """Return the matched red-flag string, or None if no match."""
    tl = text.lower()
    for flag in red_flags:
        f = flag.strip()
        if f and f.lower() in tl:
            return f
    return None


class PlayerThread(QThread):
    started_sig  = pyqtSignal(str)
    stopped_sig  = pyqtSignal(str)
    progress_sig = pyqtSignal(str, int, int)
    # mode signal payload is the backend name actually used (e.g. "winmsg")
    mode_sig     = pyqtSignal(str, str)
    # Pixel Guard matched — carries (macro_id, matched_flag_text)
    guard_sig    = pyqtSignal(str, str)

    def __init__(self, macro: Macro, all_macros: Optional[list] = None):
        super().__init__()
        self.macro      = macro
        self.all_macros = all_macros or []
        self._stop      = threading.Event()

    def stop(self): self._stop.set()

    def run(self):
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
        if backend is None:
            self.mode_sig.emit(m.id, f"error:{_LAST_BACKEND_ERROR}")
            self.stopped_sig.emit(m.id)
            return
        self.mode_sig.emit(m.id, backend.name)

        self._target_hwnd: Optional[int] = None
        if m.use_target_window and m.target_window_title:
            self._target_hwnd = find_window_hwnd(m.target_window_title)

        events = m.events
        total  = len(events)
        speed  = max(0.01, m.speed_multiplier)
        reps   = m.repeat_count or 10_000_000

        try:
            rep = 0
            while rep < reps and not self._stop.is_set():
                t0 = time.perf_counter()
                i  = 0
                skip_initial_kb = False   # set True after a guard-triggered restart

                while i < total and not self._stop.is_set():
                    ev = events[i]

                    # On restart: skip leading keyboard events until first
                    # non-keyboard event so repeated setup presses are avoided.
                    if skip_initial_kb:
                        if ev["event_type"] in ("key_press", "key_release"):
                            i += 1
                            continue
                        else:
                            skip_initial_kb = False   # resume normally

                    _sleep_until(t0 + ev["timestamp"] / speed, self._stop)
                    if self._stop.is_set():
                        break

                    # ── Pixel Guard check ────────────────────────────────────
                    if self._pixel_guard_check(ev, m, backend):
                        # Guard triggered: restart this rep from the top
                        i = 0
                        skip_initial_kb = True
                        t0 = time.perf_counter()
                        continue

                    self._fire(ev, backend)
                    self.progress_sig.emit(m.id, i, total)
                    i += 1

                rep += 1
        finally:
            try: backend.close()
            except Exception: pass

        self.stopped_sig.emit(m.id)

    # ── Pixel Guard ───────────────────────────────────────────────────────────

    def _pixel_guard_check(self, ev: dict, m: Macro, backend: InputBackend) -> bool:
        """
        If this event has pixel_guard=True AND the macro has pixel_guard_enabled:
          - OCR the configured window region
          - If a red-flag matches: run correction, emit guard_sig, return True
        Returns True  → caller should restart from top.
        Returns False → proceed normally.
        """
        if not ev.get("pixel_guard"):
            return False
        if not getattr(m, "pixel_guard_enabled", False):
            return False
        red_flags = getattr(m, "pixel_guard_red_flags", [])
        if not red_flags:
            return False

        text  = _pixel_ocr_region(
            self._target_hwnd,
            getattr(m, "pixel_guard_cap_x_pct", 0.75),
            getattr(m, "pixel_guard_cap_y_pct", 0.02),
            getattr(m, "pixel_guard_cap_w_pct", 0.24),
            getattr(m, "pixel_guard_cap_h_pct", 0.06),
        )
        matched = _pixel_flags_match(text, red_flags)
        if matched is None:
            return False   # location OK — continue

        # ── Red flag matched ─────────────────────────────────────────────────
        _log_crash(f"[pixel_guard] matched '{matched}' in '{text}' — running correction")
        self.guard_sig.emit(m.id, matched)

        corr_id = getattr(m, "pixel_guard_correction_macro", "")
        if corr_id:
            corr = next((x for x in self.all_macros if x.id == corr_id), None)
            if corr and corr.events:
                self._run_inline(corr, backend)
                return True

        # Default: press & release the correction key (B by default)
        key = getattr(m, "pixel_guard_correction_key", "b") or "b"
        try:
            backend.key_down(key)
            time.sleep(0.05)
            backend.key_up(key)
        except Exception as e:
            _log_crash(f"[pixel_guard] correction key press failed: {e}")
        return True

    def _run_inline(self, m: Macro, backend: InputBackend):
        """Run a correction macro's events inline — no guard recursion, no rep loop."""
        speed = max(0.01, m.speed_multiplier)
        t0    = time.perf_counter()
        for ev in m.events:
            if self._stop.is_set(): break
            _sleep_until(t0 + ev["timestamp"] / speed, self._stop)
            if not self._stop.is_set():
                self._fire(ev, backend)

    # ── Coordinate + event helpers ────────────────────────────────────────────

    def _resolve_xy(self, d: dict) -> tuple:
        x, y = int(d["x"]), int(d["y"])
        if d.get("coord_space") == "client" and self._target_hwnd:
            cw, ch = client_rect(self._target_hwnd)
            if cw > 0 and ch > 0:
                x = max(0, min(x, cw - 1))
                y = max(0, min(y, ch - 1))
            sx, sy = client_to_screen(self._target_hwnd, x, y)
            return sx, sy
        return x, y

    def _fire(self, ev: dict, b: InputBackend):
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
# CHAIN PLAYER THREAD
#   Runs all enabled lanes of a MacroGroup in sequence.
#   Lane 0 (Primary) controls the repeat count.  Secondary lanes run once per
#   Primary repeat.  Stop propagates to the currently running lane.
# ══════════════════════════════════════════════════════════════════════════════

class ChainPlayerThread(QThread):
    # (group_id, lane_index, lane_macro_id)
    lane_started  = pyqtSignal(str, int, str)
    lane_stopped  = pyqtSignal(str, int, str)
    chain_stopped = pyqtSignal(str)          # group_id
    # (group_id, lane_idx, event_idx, total)
    progress_sig  = pyqtSignal(str, int, int, int)
    # (group_id, lane_idx, backend_name)
    mode_sig      = pyqtSignal(str, int, str)
    # (group_id, lane_idx, flag_text)
    guard_sig     = pyqtSignal(str, int, str)
    # shared human-readable log
    log_sig       = pyqtSignal(str)

    def __init__(self, group: "MacroGroup", all_groups: list):
        super().__init__()
        self.group      = group
        self.all_groups = all_groups
        self._stop      = threading.Event()
        self._cur_player: Optional[PlayerThread] = None

    def stop(self):
        self._stop.set()
        if self._cur_player:
            self._cur_player.stop()

    # Flatten all macros from all groups for pixel-guard correction lookup
    def _all_macros(self) -> list:
        out = []
        for g in self.all_groups:
            out.extend(g.lanes)
        return out

    def run(self):
        try:
            self._run_chain()
        except Exception as e:
            _log_crash(f"ChainPlayerThread crashed: {e}\n{traceback.format_exc()}")
        finally:
            self.chain_stopped.emit(self.group.id)

    def _run_chain(self):
        g          = self.group
        primary    = g.lanes[0]
        reps       = primary.repeat_count or 10_000_000
        all_macros = self._all_macros()

        for rep in range(reps):
            if self._stop.is_set():
                break
            for lane_idx, lane in enumerate(g.lanes):
                if self._stop.is_set():
                    break
                # Lane 0 always runs; others only if enabled and have events
                if lane_idx > 0 and (not lane.lane_enabled or not lane.events):
                    continue

                self.lane_started.emit(g.id, lane_idx, lane.id)
                self.log_sig.emit(
                    f"[{g.name}] Lane {lane_idx} '{lane.name}' starting "
                    f"(rep {rep+1}/{reps if primary.repeat_count else '∞'})")

                ok = self._run_one_lane(lane_idx, lane, all_macros)

                self.lane_stopped.emit(g.id, lane_idx, lane.id)
                if not ok:
                    self.log_sig.emit(
                        f"[{g.name}] Lane {lane_idx} '{lane.name}' stopped early.")
                    break   # stop remaining lanes in this rep if primary aborted
                self.log_sig.emit(
                    f"[{g.name}] Lane {lane_idx} '{lane.name}' complete.")

            if self._stop.is_set():
                break

    def _run_one_lane(self, lane_idx: int, lane: "Macro",
                      all_macros: list) -> bool:
        """Run a single lane to completion. Returns True on normal finish."""
        backend = create_backend(lane)
        if backend is None:
            self.mode_sig.emit(self.group.id, lane_idx,
                               f"error:{_LAST_BACKEND_ERROR}")
            return False

        self.mode_sig.emit(self.group.id, lane_idx, backend.name)

        # Log attach info
        hwnd = None
        pid  = 0
        proc_name = ""
        if lane.use_target_window and lane.target_window_title:
            hwnd = find_window_hwnd(lane.target_window_title)
            if hwnd:
                pid = get_window_pid(hwnd)
                try:
                    import psutil
                    proc_name = psutil.Process(pid).name()
                except Exception:
                    proc_name = ""
        hook_status = ""
        if lane.input_backend == "detours" and pid:
            hook_status = "Hook: Loaded" if _pipe_exists(pid) else "Hook: Not loaded"
        self.log_sig.emit(
            f"  Attached — PID {pid}  Window: {lane.target_window_title or '(any)'}  "
            f"Process: {proc_name}  Backend: {backend.name}  {hook_status}")

        # Build a one-shot PlayerThread (repeat=1) and run it synchronously
        # by driving its inner logic on THIS thread.
        class _StopProxy:
            def __init__(self, outer): self._outer = outer
            def is_set(self): return self._outer._stop.is_set()

        stop_proxy  = _StopProxy(self)
        target_hwnd = hwnd
        events      = lane.events
        total       = len(events)
        speed       = max(0.01, lane.speed_multiplier)

        try:
            i  = 0
            skip_initial_kb = False
            t0 = time.perf_counter()

            while i < total and not self._stop.is_set():
                ev = events[i]
                if skip_initial_kb:
                    if ev["event_type"] in ("key_press", "key_release"):
                        i += 1; continue
                    else:
                        skip_initial_kb = False

                _sleep_until(t0 + ev["timestamp"] / speed, self._stop)
                if self._stop.is_set():
                    break

                # Pixel guard
                guard_result = self._pixel_guard(ev, lane, backend, target_hwnd,
                                                  all_macros, lane_idx)
                if guard_result:
                    i = 0; skip_initial_kb = True; t0 = time.perf_counter()
                    continue

                self._fire(ev, backend, target_hwnd)
                self.progress_sig.emit(self.group.id, lane_idx, i, total)
                i += 1

        finally:
            try: backend.close()
            except Exception: pass

        return not self._stop.is_set()

    def _pixel_guard(self, ev, lane, backend, target_hwnd,
                     all_macros, lane_idx) -> bool:
        if not ev.get("pixel_guard"): return False
        if not getattr(lane, "pixel_guard_enabled", False): return False
        red_flags = getattr(lane, "pixel_guard_red_flags", [])
        if not red_flags: return False
        text = _pixel_ocr_region(
            target_hwnd,
            getattr(lane, "pixel_guard_cap_x_pct", 0.75),
            getattr(lane, "pixel_guard_cap_y_pct", 0.02),
            getattr(lane, "pixel_guard_cap_w_pct", 0.24),
            getattr(lane, "pixel_guard_cap_h_pct", 0.06),
        )
        matched = _pixel_flags_match(text, red_flags)
        if matched is None: return False
        self.guard_sig.emit(self.group.id, lane_idx, matched)
        self.log_sig.emit(
            f"  🛡 Guard matched '{matched}' — running correction + restart")
        corr_id = getattr(lane, "pixel_guard_correction_macro", "")
        if corr_id:
            corr = next((m for m in all_macros if m.id == corr_id), None)
            if corr and corr.events:
                self._run_inline(corr, backend, target_hwnd); return True
        key = getattr(lane, "pixel_guard_correction_key", "b") or "b"
        try: backend.key_down(key); time.sleep(0.05); backend.key_up(key)
        except Exception: pass
        return True

    def _run_inline(self, m: "Macro", backend, target_hwnd):
        speed = max(0.01, m.speed_multiplier)
        t0 = time.perf_counter()
        for ev in m.events:
            if self._stop.is_set(): break
            _sleep_until(t0 + ev["timestamp"] / speed, self._stop)
            if not self._stop.is_set():
                self._fire(ev, backend, target_hwnd)

    def _fire(self, ev: dict, b: "InputBackend", target_hwnd):
        if self._stop.is_set(): return
        d, t = ev["data"], ev["event_type"]
        def _xy():
            x, y = int(d["x"]), int(d["y"])
            if d.get("coord_space") == "client" and target_hwnd:
                cw, ch = client_rect(target_hwnd)
                if cw > 0 and ch > 0:
                    x = max(0, min(x, cw-1)); y = max(0, min(y, ch-1))
                x, y = client_to_screen(target_hwnd, x, y)
            return x, y
        try:
            if   t == "key_press":   b.key_down(d["key"])
            elif t == "key_release": b.key_up(d["key"])
            elif t == "mouse_move":
                x, y = _xy(); b.mouse_move(x, y)
            elif t == "mouse_click":
                x, y = _xy(); b.mouse_button(x, y, d["button"], d["pressed"])
            elif t == "mouse_scroll":
                x, y = _xy(); b.mouse_scroll(x, y, int(d["dx"]), int(d["dy"]))
        except Exception as e:
            _log_crash(f"[chain fire] {t}: {e}")


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
    def __init__(self, current_version: str, version_url: str,
                 script_url: str, exe_url: str = ""):
        self.current     = current_version
        self.version_url = version_url
        self.script_url  = script_url
        self.exe_url     = exe_url

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

    def download_and_install(self, target_path: Path, timeout: float = 60.0) -> bool:
        """Download update and swap in.  Returns True on success.
        Exe mode: downloads new exe to a temp path + writes a batch swap script.
        Script mode: classic .new.py swap.
        """
        if getattr(sys, "frozen", False):
            # ── EXE MODE ─────────────────────────────────────────────────────
            if not self.exe_url:
                print("No EXE update URL configured.")
                return False
            current_exe = Path(sys.executable)
            new_exe     = current_exe.with_name("QytCroRec_update.exe")
            batch_path  = current_exe.with_name("_qyt_update.bat")
            try:
                with urllib.request.urlopen(self.exe_url, timeout=timeout) as r:
                    data = r.read()
            except Exception as e:
                print(f"Exe update download failed: {e}")
                return False
            if len(data) < 1024 * 100:   # < 100 KB — almost certainly wrong
                print("Exe update sanity check failed (too small).")
                return False
            new_exe.write_bytes(data)
            # Batch script: wait, swap, relaunch, self-delete
            batch_path.write_text(
                "@echo off\r\n"
                "timeout /t 2 /nobreak >nul\r\n"
                f"move /y \"{new_exe}\" \"{current_exe}\"\r\n"
                f"start \"\" \"{current_exe}\"\r\n"
                f"del /q \"{batch_path}\"\r\n",
                encoding="ascii"
            )
            subprocess.Popen(
                ["cmd", "/c", str(batch_path)],
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                close_fds=True,
            )
            return True   # caller should quit after this
        else:
            # ── SCRIPT MODE ──────────────────────────────────────────────────
            try:
                with urllib.request.urlopen(self.script_url, timeout=timeout) as r:
                    data = r.read()
            except Exception as e:
                print(f"Script update download failed: {e}")
                return False
            if len(data) < 1024 or b"Macro Recorder" not in data:
                print("Script update content sanity check failed.")
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
# BACKGROUND WIDGET — draws Marvel image + dark overlay behind everything
# ══════════════════════════════════════════════════════════════════════════════

def _resolve_asset(name: str) -> str:
    """Return absolute path to a bundled asset whether running as script or frozen exe."""
    if getattr(sys, 'frozen', False):
        base = Path(sys._MEIPASS)
    else:
        base = Path(__file__).parent
    return str(base / "assets" / name)


class _BgWidget(QWidget):
    """Central widget that paints the Marvel background image + dark overlay."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._bg: Optional[QPixmap] = None
        path = _resolve_asset("marvel_bg.jpg")
        if Path(path).exists():
            px = QPixmap(path)
            if not px.isNull():
                self._bg = px

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = self.rect()
        if self._bg and not self._bg.isNull():
            # Scale image to fill widget, centered crop
            scaled = self._bg.scaled(
                r.width(), r.height(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = (r.width()  - scaled.width())  // 2
            y = (r.height() - scaled.height()) // 2
            p.drawPixmap(x, y, scaled)
            # Dark overlay so text/panels stay readable
            p.fillRect(r, QColor(8, 8, 20, 195))
        else:
            # Fallback: solid dark background
            p.fillRect(r, QColor(0x1e, 0x1e, 0x2e))
        p.end()


# ══════════════════════════════════════════════════════════════════════════════

STYLE = """
/* ── Base: transparent so Marvel BG shows through ───────────────────────── */
QMainWindow { background: transparent; }
QWidget     { background: transparent; color: #e8eaf6;
              font-family: 'Segoe UI', sans-serif; font-size: 13px; }

/* ── Opaque panels ───────────────────────────────────────────────────────── */
QListWidget {
    background: rgba(12, 12, 24, 0.82); border: 1px solid rgba(139,180,248,0.30);
    border-radius: 8px; padding: 4px; color: #e8eaf6;
}
QListWidget::item { padding: 7px 10px; border-radius: 4px; }
QListWidget::item:selected { background: rgba(69,71,90,0.90); }
QListWidget::item:hover:!selected { background: rgba(42,42,62,0.80); }

QTableWidget {
    background: rgba(12, 12, 24, 0.82); border: 1px solid rgba(139,180,248,0.25);
    border-radius: 6px; gridline-color: rgba(69,71,90,0.50); color: #e8eaf6;
}
QTableWidget::item { padding: 3px 8px; }
QTableWidget::item:selected { background: rgba(69,71,90,0.90); }

QHeaderView::section {
    background: rgba(30,30,46,0.90); color: #a6adc8; padding: 5px 8px;
    border: none; border-bottom: 1px solid rgba(69,71,90,0.60);
    font-weight: bold; font-size: 11px;
}

QGroupBox {
    background: rgba(18,18,36,0.72); border: 1px solid rgba(139,180,248,0.25);
    border-radius: 8px; margin-top: 10px; padding-top: 6px; color: #a6adc8; font-weight: bold;
}
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; font-size: 11px; }

QTabWidget::pane {
    border: 1px solid rgba(139,180,248,0.25); border-radius: 6px;
    background: rgba(18,18,36,0.80); margin-top: -1px;
}
QTabBar::tab {
    background: rgba(24,24,37,0.85); color: #a6adc8; padding: 7px 18px;
    border: 1px solid rgba(69,71,90,0.55); border-bottom: none;
    border-top-left-radius: 5px; border-top-right-radius: 5px; margin-right: 2px;
}
QTabBar::tab:selected { background: rgba(49,50,68,0.95); color: #e8eaf6; }
QTabBar::tab:hover:!selected { background: rgba(42,42,62,0.85); }

QStatusBar {
    background: rgba(12,12,20,0.90); color: #a6adc8;
    border-top: 1px solid rgba(69,71,90,0.50);
}

QSplitter::handle { background: rgba(69,71,90,0.40); width: 1px; height: 1px; }

QScrollBar:vertical { background: rgba(24,24,37,0.60); width: 8px; border-radius: 4px; }
QScrollBar::handle:vertical { background: rgba(100,110,160,0.75); border-radius: 4px; min-height: 20px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }

QMenu {
    background: rgba(22,22,38,0.96); border: 1px solid rgba(139,180,248,0.30);
    border-radius: 4px; padding: 4px; color: #e8eaf6;
}
QMenu::item { padding: 6px 16px; border-radius: 3px; }
QMenu::item:selected { background: rgba(69,71,90,0.90); }

QToolTip {
    background: rgba(49,50,68,0.97); color: #cdd6f4;
    border: 1px solid rgba(139,180,248,0.40); border-radius: 4px; padding: 4px 8px;
}

/* ── Input fields ────────────────────────────────────────────────────────── */
QLineEdit, QSpinBox, QDoubleSpinBox, QKeySequenceEdit {
    background: rgba(12,12,24,0.88); border: 1px solid rgba(69,71,90,0.70);
    border-radius: 4px; padding: 4px 8px; color: #e8eaf6;
}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QKeySequenceEdit:focus { border-color: #89b4fa; }

QComboBox {
    background: rgba(12,12,24,0.88); border: 1px solid rgba(69,71,90,0.70);
    border-radius: 4px; padding: 4px 8px; color: #e8eaf6;
}
QComboBox::drop-down { border: none; }
QComboBox QAbstractItemView {
    background: rgba(22,22,38,0.97); color: #e8eaf6;
    border: 1px solid rgba(139,180,248,0.30); selection-background-color: rgba(69,71,90,0.90);
}

QCheckBox { spacing: 6px; color: #e8eaf6; }
QCheckBox::indicator {
    width: 15px; height: 15px; border-radius: 3px;
    border: 1px solid rgba(100,110,160,0.80); background: rgba(12,12,24,0.80);
}
QCheckBox::indicator:checked { background: #89b4fa; border-color: #89b4fa; }

/* ── Generic buttons (glass-dark look) ──────────────────────────────────── */
QPushButton {
    background: rgba(49,50,68,0.82); color: #e8eaf6;
    border: 1px solid rgba(139,180,248,0.30); border-radius: 6px;
    padding: 6px 14px; font-weight: 600;
}
QPushButton:hover   { background: rgba(69,71,90,0.92); border-color: rgba(139,180,248,0.55); }
QPushButton:pressed { background: rgba(24,24,37,0.95); }
QPushButton:disabled { background: rgba(30,30,46,0.45); color: rgba(140,145,170,0.50);
                        border-color: rgba(69,71,90,0.25); }

/* ── RECORD button — vivid red, always visible ───────────────────────────── */
QPushButton#btn_record {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 #ff4e7e, stop:1 #c9284d);
    color: #ffffff; border: 2px solid #ff6b9a;
    border-radius: 7px; font-weight: bold; font-size: 13px;
    padding: 7px 18px;
}
QPushButton#btn_record:hover  { background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
    stop:0 #ff6f96, stop:1 #e03060); border-color: #ffaac8; }
QPushButton#btn_record:pressed { background: #a01f3a; }
QPushButton#btn_record:disabled {
    background: rgba(180,40,70,0.28); color: rgba(255,150,180,0.45);
    border: 1px solid rgba(200,60,90,0.25);
}

/* ── PLAY button — vivid green, always visible ───────────────────────────── */
QPushButton#btn_play {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 #3de87a, stop:1 #1db954);
    color: #0a1a0e; border: 2px solid #55f090;
    border-radius: 7px; font-weight: bold; font-size: 13px;
    padding: 7px 18px;
}
QPushButton#btn_play:hover  { background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
    stop:0 #5af090, stop:1 #28d060); border-color: #90ffb8; }
QPushButton#btn_play:pressed { background: #138a3a; }
QPushButton#btn_play:disabled {
    background: rgba(30,150,70,0.25); color: rgba(100,220,140,0.42);
    border: 1px solid rgba(50,180,90,0.22);
}

/* ── STOP button — vivid orange, always visible ──────────────────────────── */
QPushButton#btn_stop {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 #ffaa44, stop:1 #e07010);
    color: #1a0a00; border: 2px solid #ffc060;
    border-radius: 7px; font-weight: bold; font-size: 13px;
    padding: 7px 18px;
}
QPushButton#btn_stop:hover  { background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
    stop:0 #ffc060, stop:1 #f08020); border-color: #ffe090; }
QPushButton#btn_stop:pressed { background: #a05010; }
QPushButton#btn_stop:disabled {
    background: rgba(200,100,20,0.25); color: rgba(255,180,80,0.42);
    border: 1px solid rgba(220,120,30,0.22);
}

QPushButton#btn_del { color: #f38ba8; }
"""

EVENT_COLORS = {
    "key_press":    "#89b4fa",
    "key_release":  "#74c7ec",
    "mouse_move":   "#6c7086",
    "mouse_click":  "#a6e3a1",
    "mouse_scroll": "#f9e2af",
}


# ══════════════════════════════════════════════════════════════════════════════
# LANE WIDGET
#   Self-contained widget for one macro lane.  Holds its own Macro reference
#   and exposes signals for the parent MainWindow to coordinate.
# ══════════════════════════════════════════════════════════════════════════════

class LaneWidget(QWidget):
    """Full-featured per-lane panel: settings + events + guard."""

    changed       = pyqtSignal(str)   # macro id — any field changed
    record_req    = pyqtSignal(str)   # macro id
    play_req      = pyqtSignal(str)   # macro id
    stop_req      = pyqtSignal(str)   # macro id
    window_picked = pyqtSignal(str, int, str)  # macro_id, slot, title

    def __init__(self, macro: Macro, lane_index: int,
                 all_macros_fn,        # callable → list[Macro]
                 parent=None):
        super().__init__(parent)
        self._macro        = macro
        self._lane_index   = lane_index
        self._all_macros   = all_macros_fn   # late-bound so it sees current list
        self._is_primary   = (lane_index == 0)
        self._row_event_idx: list = []
        self._groups: dict = {}
        self._ev_filters: dict = {}
        self._recording    = False
        self._playing      = False
        self._build()

    @property
    def macro(self) -> Macro:
        return self._macro

    def set_macro(self, macro: Macro):
        self._macro = macro
        self._load()

    def set_playing(self, playing: bool):
        self._playing = playing
        self._btn_play.setEnabled(not playing)
        self._btn_stop.setEnabled(playing)
        self._btn_record.setEnabled(not playing and not self._recording)
        if playing:
            self._status_lbl.setText("▶ Playing")
            self._status_lbl.setStyleSheet("color: #a6e3a1; font-weight: bold;")
        else:
            self._status_lbl.setText("Idle")
            self._status_lbl.setStyleSheet("color: #585b70;")

    def set_recording(self, recording: bool):
        self._recording = recording
        if recording:
            self._btn_record.setText("⏹ Stop Rec")
            self._status_lbl.setText("● REC")
            self._status_lbl.setStyleSheet("color: #f38ba8; font-weight: bold;")
        else:
            self._btn_record.setText("⏺ Record")
            self._status_lbl.setText("Idle")
            self._status_lbl.setStyleSheet("color: #585b70;")

    def add_event(self, ev: dict):
        """Append one live-captured event to the table (during recording)."""
        self._macro.events.append(ev)
        row = self._table.rowCount()
        self._table.insertRow(row)
        n = len(self._macro.events)
        self._table.setItem(row, 0, QTableWidgetItem(str(n)))
        self._table.setItem(row, 1, QTableWidgetItem(f"{ev['timestamp']:.3f}"))
        ti = QTableWidgetItem(ev["event_type"])
        ti.setForeground(QColor(EVENT_COLORS.get(ev["event_type"], "#cdd6f4")))
        self._table.setItem(row, 2, ti)
        self._table.setItem(row, 3, QTableWidgetItem(event_summary(ev)))
        gitem = QTableWidgetItem()
        gitem.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable |
                       Qt.ItemFlag.ItemIsUserCheckable)
        gitem.setCheckState(Qt.CheckState.Unchecked)
        self._table.setItem(row, 4, gitem)
        self._row_event_idx.append(n - 1)
        self._table.scrollToBottom()
        self._ev_count.setText(f"{n} events")

    def highlight_event(self, idx: int):
        if 0 <= idx < self._table.rowCount():
            self._table.selectRow(idx)
            self._table.scrollTo(self._table.model().index(idx, 0))

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build(self):
        from PyQt6.QtWidgets import QScrollArea
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Lane header ───────────────────────────────────────────────────────
        hdr = QWidget()
        hdr.setStyleSheet("background: #252535; border-bottom: 1px solid #313244;")
        hdr_lay = QHBoxLayout(hdr)
        hdr_lay.setContentsMargins(10, 6, 10, 6)
        hdr_lay.setSpacing(8)

        if not self._is_primary:
            self._enable_chk = QCheckBox("Enabled")
            self._enable_chk.setChecked(self._macro.lane_enabled)
            self._enable_chk.setToolTip(
                "When checked this lane runs after the previous lane completes.")
            self._enable_chk.stateChanged.connect(self._on_enabled_changed)
            hdr_lay.addWidget(self._enable_chk)
        else:
            lbl = QLabel("⭐ Primary")
            lbl.setStyleSheet("color: #f9e2af; font-weight: bold;")
            hdr_lay.addWidget(lbl)

        self._status_lbl = QLabel("Idle")
        self._status_lbl.setStyleSheet("color: #585b70;")
        hdr_lay.addWidget(self._status_lbl)
        hdr_lay.addStretch()

        # Record / Play / Stop
        self._btn_record = QPushButton("⏺ Record")
        self._btn_record.setObjectName("btn_record")
        self._btn_record.setMaximumWidth(110)
        self._btn_record.clicked.connect(lambda: self.record_req.emit(self._macro.id))

        self._btn_play = QPushButton("▶ Play")
        self._btn_play.setObjectName("btn_play")
        self._btn_play.setMaximumWidth(80)
        self._btn_play.clicked.connect(lambda: self.play_req.emit(self._macro.id))

        self._btn_stop = QPushButton("■ Stop")
        self._btn_stop.setObjectName("btn_stop")
        self._btn_stop.setMaximumWidth(80)
        self._btn_stop.setEnabled(False)
        self._btn_stop.clicked.connect(lambda: self.stop_req.emit(self._macro.id))

        for b in (self._btn_record, self._btn_play, self._btn_stop):
            hdr_lay.addWidget(b)

        root.addWidget(hdr)

        # ── Tabs: Settings / Events / Guard ───────────────────────────────────
        tabs = QTabWidget()
        tabs.setStyleSheet(
            "QTabWidget::pane { border: none; }"
            "QTabBar::tab { padding: 5px 14px; font-size: 12px; }")
        tabs.addTab(self._build_settings(), "⚙ Settings")
        tabs.addTab(self._build_events(),   "⏺ Events")
        tabs.addTab(self._build_guard(),    "🛡 Guard")
        root.addWidget(tabs, 1)

    def _build_settings(self) -> QWidget:
        from PyQt6.QtWidgets import QScrollArea
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        # Name + hotkey
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("Name:"))
        self._name_edit = QLineEdit(placeholderText="Lane name…")
        self._name_edit.setText(self._macro.name)
        self._name_edit.textChanged.connect(self._on_name_changed)
        r1.addWidget(self._name_edit)
        r1.addSpacing(12)
        r1.addWidget(QLabel("Hotkey:"))
        self._hotkey_edit = QLineEdit(placeholderText="e.g. ctrl+f5")
        self._hotkey_edit.setMaximumWidth(130)
        self._hotkey_edit.setText(self._macro.trigger_hotkey)
        self._hotkey_edit.editingFinished.connect(self._on_hotkey_changed)
        r1.addWidget(self._hotkey_edit)
        lay.addLayout(r1)

        # Repeat + speed + mouse
        r2 = QHBoxLayout()
        r2.addWidget(QLabel("Repeat:"))
        self._repeat_spin = QSpinBox()
        self._repeat_spin.setRange(0, 99999); self._repeat_spin.setValue(self._macro.repeat_count)
        self._repeat_spin.setSpecialValueText("∞"); self._repeat_spin.setMaximumWidth(80)
        self._repeat_spin.valueChanged.connect(self._on_repeat_changed)
        r2.addWidget(self._repeat_spin); r2.addSpacing(12)
        r2.addWidget(QLabel("Speed:"))
        self._speed_spin = QDoubleSpinBox()
        self._speed_spin.setRange(0.1, 20.0); self._speed_spin.setSingleStep(0.25)
        self._speed_spin.setValue(self._macro.speed_multiplier); self._speed_spin.setSuffix("×")
        self._speed_spin.setMaximumWidth(90)
        self._speed_spin.valueChanged.connect(self._on_speed_changed)
        r2.addWidget(self._speed_spin); r2.addSpacing(12)
        self._move_chk = QCheckBox("Record Mouse Moves")
        self._move_chk.setChecked(self._macro.record_mouse_move)
        self._move_chk.stateChanged.connect(self._on_move_chk_changed)
        r2.addWidget(self._move_chk); r2.addStretch()
        lay.addLayout(r2)

        # Target window
        win_lbl_style = (
            "color: #585b70; font-size: 12px; background: #181825;"
            "border: 1px solid #313244; border-radius: 4px; padding: 3px 10px;")

        self._use_target_chk = QCheckBox("Force Target Window")
        self._use_target_chk.setChecked(self._macro.use_target_window)
        self._use_target_chk.stateChanged.connect(self._on_use_target_changed)
        lay.addWidget(self._use_target_chk)

        rw = QHBoxLayout(); rw.setContentsMargins(20, 0, 0, 0)
        self._win_lbl = QLabel(self._macro.target_window_title or "(none)")
        self._win_lbl.setStyleSheet(win_lbl_style); self._win_lbl.setMinimumWidth(200)
        rw.addWidget(self._win_lbl, 1)
        self._pick_btn = QPushButton("Pick…"); self._pick_btn.setMaximumWidth(60)
        self._pick_btn.clicked.connect(lambda: self._pick_window(1))
        rw.addWidget(self._pick_btn)
        self._capture_btn = QPushButton("Capture 3s"); self._capture_btn.setMaximumWidth(90)
        self._capture_btn.clicked.connect(self._capture_window)
        rw.addWidget(self._capture_btn)
        self._drag_btn = QPushButton("🎯"); self._drag_btn.setMaximumWidth(36)
        self._drag_btn.setToolTip("Drag-pick: click any window to select it")
        self._drag_btn.clicked.connect(lambda: self._start_drag_pick(1))
        rw.addWidget(self._drag_btn)
        lay.addLayout(rw)

        # PID / process name readout
        self._pid_lbl = QLabel("")
        self._pid_lbl.setStyleSheet("color: #585b70; font-size: 11px; padding-left: 20px;")
        lay.addWidget(self._pid_lbl)
        self._refresh_pid_label()

        # Backend + hook
        r4 = QHBoxLayout()
        r4.addWidget(QLabel("Backend:"))
        self._backend_combo = QComboBox()
        for name, desc, _ in BACKEND_CHOICES:
            lbl2 = desc
            if name == "interception" and not InterceptionBackend.available():
                lbl2 += "   [not installed]"
            elif name == "serial_hid" and not SerialHIDBackend.available():
                lbl2 += "   [pyserial not installed]"
            elif name == "detours":
                lbl2 += "   [requires hook DLL]"
            self._backend_combo.addItem(lbl2, name)
        idx = next((i for i, (n, _, _) in enumerate(BACKEND_CHOICES)
                    if n == (self._macro.input_backend or "auto")), 0)
        self._backend_combo.setCurrentIndex(idx)
        self._backend_combo.setMinimumWidth(280)
        self._backend_combo.currentIndexChanged.connect(self._on_backend_changed)
        r4.addWidget(self._backend_combo)
        self._hook_btn = QPushButton("Hook Status")
        self._hook_btn.clicked.connect(self._show_hook_status)
        r4.addWidget(self._hook_btn)
        self._hook_status_lbl = QLabel("")
        self._hook_status_lbl.setStyleSheet("color: #a6adc8; font-size: 12px;")
        r4.addWidget(self._hook_status_lbl)
        r4.addStretch()
        lay.addLayout(r4)

        self._update_window_btn_states()
        lay.addStretch()

        scroll = QScrollArea()
        scroll.setWidget(inner); scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; }")
        return scroll

    def _build_events(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w); lay.setContentsMargins(8, 8, 8, 8); lay.setSpacing(5)

        filt_row = QHBoxLayout()
        filt_row.addWidget(QLabel("Show:"))
        for et, label in [("key_press","key press"),("key_release","key release"),
                          ("mouse_move","mouse move"),("mouse_click","mouse click"),
                          ("mouse_scroll","mouse scroll")]:
            cb = QCheckBox(label); cb.setChecked(True)
            cb.setStyleSheet(f"QCheckBox {{ color: {EVENT_COLORS.get(et,'#cdd6f4')}; }}")
            cb.stateChanged.connect(self._apply_filter)
            filt_row.addWidget(cb)
            self._ev_filters[et] = cb
        filt_row.addStretch()
        lay.addLayout(filt_row)

        self._table = QTableWidget()
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels(["#", "Time (s)", "Type", "Details", "🛡"])
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        hh.resizeSection(0, 50); hh.resizeSection(1, 100); hh.resizeSection(2, 120)
        hh.resizeSection(4, 34)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setStyleSheet("QTableWidget { alternate-background-color: #1a1a2a; }")
        self._table.verticalHeader().setVisible(True)
        self._table.verticalHeader().setSectionsMovable(True)
        self._table.verticalHeader().sectionMoved.connect(self._on_section_moved)
        self._table.cellClicked.connect(self._on_cell_clicked)
        self._table.itemChanged.connect(self._on_guard_item_changed)
        lay.addWidget(self._table)

        ea = QHBoxLayout()
        btn_clr = QPushButton("Clear All"); btn_clr.clicked.connect(self._clear_events)
        btn_del = QPushButton("Delete Selected"); btn_del.clicked.connect(self._del_selected_events)
        ea.addWidget(btn_clr); ea.addWidget(btn_del); ea.addStretch()
        self._ev_count = QLabel("0 events")
        self._ev_count.setStyleSheet("color: #585b70; font-size: 11px;")
        ea.addWidget(self._ev_count)
        lay.addLayout(ea)
        return w

    def _build_guard(self) -> QWidget:
        from PyQt6.QtWidgets import QScrollArea
        inner = QWidget()
        lay = QVBoxLayout(inner); lay.setContentsMargins(10, 10, 10, 10); lay.setSpacing(8)

        grp_en = QGroupBox("Pixel Bot Guard")
        gl_en = QVBoxLayout(grp_en)
        self._guard_enabled_chk = QCheckBox("Enable Pixel Bot Guard for this lane")
        self._guard_enabled_chk.setChecked(getattr(self._macro, "pixel_guard_enabled", False))
        self._guard_enabled_chk.stateChanged.connect(self._on_guard_enabled_changed)
        gl_en.addWidget(self._guard_enabled_chk)
        lay.addWidget(grp_en)

        grp_rf = QGroupBox("Red-Flag Locations (comma-separated)")
        gl_rf = QVBoxLayout(grp_rf)
        self._guard_flags_edit = QLineEdit(placeholderText="e.g.  The Raft, Danger Zone")
        self._guard_flags_edit.setText(", ".join(getattr(self._macro, "pixel_guard_red_flags", [])))
        self._guard_flags_edit.editingFinished.connect(self._on_guard_flags_changed)
        gl_rf.addWidget(self._guard_flags_edit)
        lay.addWidget(grp_rf)

        grp_cap = QGroupBox("Capture Region (% of target window)")
        gl_cap = QVBoxLayout(grp_cap)
        cap_row = QHBoxLayout()
        for label, attr, default in [("X", "_guard_cap_x", 0.75),
                                     ("Y", "_guard_cap_y", 0.02),
                                     ("W", "_guard_cap_w", 0.24),
                                     ("H", "_guard_cap_h", 0.06)]:
            cap_row.addWidget(QLabel(f"{label}:"))
            sp = QDoubleSpinBox()
            sp.setRange(0.0, 1.0); sp.setSingleStep(0.01); sp.setValue(default)
            sp.setDecimals(3); sp.setMaximumWidth(80)
            sp.valueChanged.connect(self._on_guard_cap_changed)
            setattr(self, attr + "_spin", sp)
            cap_row.addWidget(sp)
        btn_test = QPushButton("🔍 Test OCR"); btn_test.clicked.connect(self._test_ocr)
        cap_row.addWidget(btn_test); cap_row.addStretch()
        gl_cap.addLayout(cap_row)
        lay.addWidget(grp_cap)

        grp_cor = QGroupBox("Correction")
        gl_cor = QVBoxLayout(grp_cor)
        cor_row = QHBoxLayout()
        cor_row.addWidget(QLabel("Key:"))
        self._guard_key_edit = QLineEdit("b"); self._guard_key_edit.setMaximumWidth(50)
        self._guard_key_edit.setText(getattr(self._macro, "pixel_guard_correction_key", "b") or "b")
        self._guard_key_edit.editingFinished.connect(self._on_guard_key_changed)
        cor_row.addWidget(self._guard_key_edit)
        cor_row.addSpacing(16); cor_row.addWidget(QLabel("OR Macro:"))
        self._guard_macro_combo = QComboBox(); self._guard_macro_combo.setMinimumWidth(180)
        self._guard_macro_combo.currentIndexChanged.connect(self._on_guard_macro_changed)
        cor_row.addWidget(self._guard_macro_combo); cor_row.addStretch()
        gl_cor.addLayout(cor_row)
        lay.addWidget(grp_cor)

        lay.addStretch()
        self._load_guard_spinners()
        self.refresh_guard_combo()

        scroll = QScrollArea()
        scroll.setWidget(inner); scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; }")
        return scroll

    # ── Load ──────────────────────────────────────────────────────────────────

    def _load(self):
        m = self._macro
        for w, v in [(self._name_edit, m.name),
                     (self._hotkey_edit, m.trigger_hotkey)]:
            w.blockSignals(True); w.setText(v); w.blockSignals(False)
        for w, v in [(self._repeat_spin, m.repeat_count),
                     (self._speed_spin, m.speed_multiplier)]:
            w.blockSignals(True); w.setValue(v); w.blockSignals(False)
        for w, v in [(self._move_chk, m.record_mouse_move),
                     (self._use_target_chk, m.use_target_window)]:
            w.blockSignals(True); w.setChecked(v); w.blockSignals(False)
        if not self._is_primary:
            self._enable_chk.blockSignals(True)
            self._enable_chk.setChecked(m.lane_enabled)
            self._enable_chk.blockSignals(False)
        self._win_lbl.setText(m.target_window_title or "(none)")
        idx = next((i for i, (n, _, _) in enumerate(BACKEND_CHOICES)
                    if n == (m.input_backend or "auto")), 0)
        self._backend_combo.blockSignals(True)
        self._backend_combo.setCurrentIndex(idx)
        self._backend_combo.blockSignals(False)
        self._update_window_btn_states()
        self._refresh_pid_label()
        self._load_guard_spinners()
        self.refresh_guard_combo()
        self._fill_table()

    def _load_guard_spinners(self):
        m = self._macro
        for attr, sp_attr, default in [
            ("pixel_guard_enabled", None, False),
            ("pixel_guard_cap_x_pct", "_guard_cap_x_spin", 0.75),
            ("pixel_guard_cap_y_pct", "_guard_cap_y_spin", 0.02),
            ("pixel_guard_cap_w_pct", "_guard_cap_w_spin", 0.24),
            ("pixel_guard_cap_h_pct", "_guard_cap_h_spin", 0.06),
        ]:
            if sp_attr:
                sp = getattr(self, sp_attr, None)
                if sp:
                    sp.blockSignals(True)
                    sp.setValue(getattr(m, attr, default))
                    sp.blockSignals(False)
        self._guard_enabled_chk.blockSignals(True)
        self._guard_enabled_chk.setChecked(getattr(m, "pixel_guard_enabled", False))
        self._guard_enabled_chk.blockSignals(False)
        self._guard_flags_edit.blockSignals(True)
        self._guard_flags_edit.setText(", ".join(getattr(m, "pixel_guard_red_flags", [])))
        self._guard_flags_edit.blockSignals(False)
        self._guard_key_edit.blockSignals(True)
        self._guard_key_edit.setText(getattr(m, "pixel_guard_correction_key", "b") or "b")
        self._guard_key_edit.blockSignals(False)

    def refresh_guard_combo(self):
        combo = self._guard_macro_combo
        combo.blockSignals(True); combo.clear()
        combo.addItem("(none — use key)", "")
        cur_id = getattr(self._macro, "pixel_guard_correction_macro", "")
        sel = 0
        for i, mac in enumerate(self._all_macros(), start=1):
            if mac.id == self._macro.id: continue
            combo.addItem(mac.name, mac.id)
            if mac.id == cur_id: sel = i
        combo.setCurrentIndex(sel); combo.blockSignals(False)

    # ── Events table ──────────────────────────────────────────────────────────

    GROUP_THRESHOLD = 3

    def _fill_table(self):
        m = self._macro
        self._table.blockSignals(True)
        self._table.setRowCount(0)
        self._row_event_idx = []
        self._groups = {}

        groups = self._build_groups(m.events)
        gbs = {g[0]: g for g in groups}

        def _guard_item(ev_dict):
            it = QTableWidgetItem()
            it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable |
                        Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(
                Qt.CheckState.Checked if ev_dict.get("pixel_guard") else Qt.CheckState.Unchecked)
            return it

        i = 0
        while i < len(m.events):
            if i in gbs:
                start, count, et = gbs[i]
                row = self._table.rowCount()
                self._table.insertRow(row)
                hdr = QTableWidgetItem(f"▶  ×{count}")
                hdr.setForeground(QColor(EVENT_COLORS.get(et, "#cdd6f4")))
                hdr.setBackground(QColor("#252535"))
                hdr.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self._table.setItem(row, 0, hdr)
                t0 = m.events[start]["timestamp"]
                t1 = m.events[start+count-1]["timestamp"]
                self._table.setItem(row, 1, QTableWidgetItem(f"{t0:.3f}—{t1:.3f}"))
                tit = QTableWidgetItem(et)
                tit.setForeground(QColor(EVENT_COLORS.get(et, "#cdd6f4")))
                self._table.setItem(row, 2, tit)
                self._table.setItem(row, 3, QTableWidgetItem(
                    f"({count} similar events — click ▶ to expand)"))
                ph = QTableWidgetItem("—"); ph.setFlags(Qt.ItemFlag.ItemIsEnabled)
                ph.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._table.setItem(row, 4, ph)
                self._row_event_idx.append(-1)
                self._groups[row] = {"start": start, "count": count,
                                     "et": et, "collapsed": True}
                for j in range(count):
                    ev = m.events[i+j]
                    r = self._table.rowCount(); self._table.insertRow(r)
                    self._table.setItem(r, 0, QTableWidgetItem(str(i+j+1)))
                    self._table.setItem(r, 1, QTableWidgetItem(f"{ev['timestamp']:.3f}"))
                    ti = QTableWidgetItem(ev["event_type"])
                    ti.setForeground(QColor(EVENT_COLORS.get(ev["event_type"], "#cdd6f4")))
                    self._table.setItem(r, 2, ti)
                    self._table.setItem(r, 3, QTableWidgetItem(event_summary(ev)))
                    self._table.setItem(r, 4, _guard_item(ev))
                    self._row_event_idx.append(i+j)
                    self._table.setRowHidden(r, True)
                i += count
            else:
                ev = m.events[i]
                row = self._table.rowCount(); self._table.insertRow(row)
                self._table.setItem(row, 0, QTableWidgetItem(str(i+1)))
                self._table.setItem(row, 1, QTableWidgetItem(f"{ev['timestamp']:.3f}"))
                ti = QTableWidgetItem(ev["event_type"])
                ti.setForeground(QColor(EVENT_COLORS.get(ev["event_type"], "#cdd6f4")))
                self._table.setItem(row, 2, ti)
                self._table.setItem(row, 3, QTableWidgetItem(event_summary(ev)))
                self._table.setItem(row, 4, _guard_item(ev))
                self._row_event_idx.append(i)
                i += 1

        self._table.blockSignals(False)
        suffix = f"  ({len(groups)} groups)" if groups else ""
        self._ev_count.setText(f"{len(m.events)} events{suffix}")
        self._apply_filter()

    def _build_groups(self, events):
        out, i, N = [], 0, len(events)
        while i < N:
            et = events[i]["event_type"]; j = i
            while j < N and events[j]["event_type"] == et: j += 1
            if j - i >= self.GROUP_THRESHOLD: out.append((i, j-i, et))
            i = j
        return out

    def _apply_filter(self):
        allowed = {et for et, cb in self._ev_filters.items() if cb.isChecked()}
        shown = 0
        for row in range(self._table.rowCount()):
            if row in self._groups:
                g = self._groups[row]
                self._table.setRowHidden(row, g["et"] not in allowed)
                continue
            idx = self._row_event_idx[row] if 0 <= row < len(self._row_event_idx) else -1
            if idx < 0 or idx >= len(self._macro.events): continue
            ev = self._macro.events[idx]
            in_collapsed = any(
                hr < row <= hr + g["count"] and g["collapsed"]
                for hr, g in self._groups.items())
            vis = (ev["event_type"] in allowed) and not in_collapsed
            self._table.setRowHidden(row, not vis)
            if vis: shown += 1

    def _on_cell_clicked(self, row, col):
        if row in self._groups:
            g = self._groups[row]
            g["collapsed"] = not g["collapsed"]
            hdr = self._table.item(row, 0)
            if hdr: hdr.setText(f"{'▶' if g['collapsed'] else '▼'}  ×{g['count']}")
            allowed = {et for et, cb in self._ev_filters.items() if cb.isChecked()}
            for offset in range(1, g["count"]+1):
                r = row+offset
                if r < self._table.rowCount():
                    vis = (not g["collapsed"]) and (g["et"] in allowed)
                    self._table.setRowHidden(r, not vis)

    def _on_guard_item_changed(self, item):
        if item.column() != 4 or not self._macro: return
        row = item.row()
        if row in self._groups: return
        idx = self._row_event_idx[row] if 0 <= row < len(self._row_event_idx) else -1
        if idx < 0 or idx >= len(self._macro.events): return
        self._macro.events[idx]["pixel_guard"] = (
            item.checkState() == Qt.CheckState.Checked)
        self.changed.emit(self._macro.id)

    def _on_section_moved(self, logical_idx, old_visual, new_visual):
        if not self._macro: return
        order = []
        vh = self._table.verticalHeader()
        for vrow in range(self._table.rowCount()):
            logical = vh.logicalIndex(vrow)
            ev_idx = self._row_event_idx[logical] if 0 <= logical < len(self._row_event_idx) else -1
            if ev_idx >= 0: order.append(ev_idx)
        if not order: return
        try:
            self._macro.events = [self._macro.events[i] for i in order]
        except IndexError: return
        vh.blockSignals(True)
        for i in range(self._table.rowCount()): vh.moveSection(vh.visualIndex(i), i)
        vh.blockSignals(False)
        self._fill_table()
        self.changed.emit(self._macro.id)

    def _clear_events(self):
        if QMessageBox.question(
            self, "Clear", "Clear all events for this lane?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) == QMessageBox.StandardButton.Yes:
            self._macro.events = []; self._table.setRowCount(0)
            self._ev_count.setText("0 events")
            self.changed.emit(self._macro.id)

    def _del_selected_events(self):
        idxs = set()
        for item in self._table.selectedIndexes():
            r = item.row()
            if 0 <= r < len(self._row_event_idx):
                e = self._row_event_idx[r]
                if e >= 0: idxs.add(e)
        for e in sorted(idxs, reverse=True):
            if e < len(self._macro.events): self._macro.events.pop(e)
        self._fill_table()
        self.changed.emit(self._macro.id)

    # ── Settings handlers ─────────────────────────────────────────────────────

    def _on_enabled_changed(self, s):
        self._macro.lane_enabled = bool(s)
        self.changed.emit(self._macro.id)

    def _on_name_changed(self, text):
        self._macro.name = text
        self.changed.emit(self._macro.id)

    def _on_hotkey_changed(self):
        self._macro.trigger_hotkey = self._hotkey_edit.text().strip()
        self.changed.emit(self._macro.id)

    def _on_repeat_changed(self, v):
        self._macro.repeat_count = v
        self.changed.emit(self._macro.id)

    def _on_speed_changed(self, v):
        self._macro.speed_multiplier = v
        self.changed.emit(self._macro.id)

    def _on_move_chk_changed(self, s):
        self._macro.record_mouse_move = bool(s)
        self.changed.emit(self._macro.id)

    def _on_use_target_changed(self, s):
        self._macro.use_target_window = bool(s)
        self._update_window_btn_states()
        self._refresh_pid_label()
        self.changed.emit(self._macro.id)

    def _on_backend_changed(self, idx):
        self._macro.input_backend = self._backend_combo.itemData(idx) or "auto"
        self._update_hook_status()
        self.changed.emit(self._macro.id)

    def _update_window_btn_states(self):
        en = self._macro.use_target_window
        for btn in (self._pick_btn, self._capture_btn, self._drag_btn):
            btn.setEnabled(en)

    def _refresh_pid_label(self):
        m = self._macro
        if not m.use_target_window or not m.target_window_title:
            self._pid_lbl.setText(""); return
        hwnd = find_window_hwnd(m.target_window_title)
        if not hwnd:
            self._pid_lbl.setText("⚠ Window not found"); return
        pid = get_window_pid(hwnd)
        try:
            import psutil; pname = psutil.Process(pid).name()
        except Exception: pname = ""
        hook_txt = ""
        if m.input_backend == "detours":
            hook_txt = "  Hook: " + ("✓ loaded" if _pipe_exists(pid) else "✗ not loaded")
        self._pid_lbl.setText(
            f"PID {pid}  {pname}  HWND 0x{hwnd:08X}{hook_txt}")

    def _update_hook_status(self):
        m = self._macro
        if m.input_backend != "detours" or not m.use_target_window:
            self._hook_status_lbl.setText(""); return
        hwnd = find_window_hwnd(m.target_window_title) if m.target_window_title else None
        pid  = get_window_pid(hwnd) if hwnd else 0
        if pid and _pipe_exists(pid):
            self._hook_status_lbl.setText("✓ Hook loaded")
            self._hook_status_lbl.setStyleSheet("color: #a6e3a1; font-size: 12px;")
        elif pid:
            self._hook_status_lbl.setText("✗ Not injected")
            self._hook_status_lbl.setStyleSheet("color: #f38ba8; font-size: 12px;")
        else:
            self._hook_status_lbl.setText("✗ Window not found")
            self._hook_status_lbl.setStyleSheet("color: #fab387; font-size: 12px;")

    def _show_hook_status(self):
        self._refresh_pid_label()
        self._update_hook_status()
        QMessageBox.information(self, "Hook Status",
            self._pid_lbl.text() or "No target window set.")

    # ── Window capture ────────────────────────────────────────────────────────

    def _pick_window(self, slot: int = 1):
        from PyQt6.QtWidgets import QDialog, QListWidget, QDialogButtonBox, QVBoxLayout
        windows = enumerate_windows()
        if not windows:
            QMessageBox.information(self, "Pick Window", "No visible windows found.")
            return
        dlg = QDialog(self.window()); dlg.setWindowTitle("Pick Target Window")
        dlg.setMinimumSize(600, 420)
        v = QVBoxLayout(dlg)
        lw = QListWidget()
        lw.setStyleSheet(
            "QListWidget { background:#181825; border:1px solid #313244; "
            "border-radius:6px; font-family:Consolas,monospace; }")
        cur = self._macro.target_window_title
        for hwnd, pid, title in windows:
            it = QListWidgetItem(f"0x{hwnd:08X}   PID {pid:>6}   {title}")
            it.setData(Qt.ItemDataRole.UserRole, (hwnd, pid, title))
            lw.addItem(it)
            if title == cur: lw.setCurrentItem(it)
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
        self._macro.target_window_title = title
        self._win_lbl.setText(title)
        self._refresh_pid_label()
        self.changed.emit(self._macro.id)

    def _capture_window(self):
        self._capture_btn.setEnabled(False)
        self._capture_ctr = 3
        self._tick_capture()

    def _tick_capture(self):
        if self._capture_ctr > 0:
            self._capture_btn.setText(f"Capturing {self._capture_ctr}s…")
            self._capture_ctr -= 1
            QTimer.singleShot(1000, self._tick_capture)
        else:
            title = get_foreground_title()
            self._macro.target_window_title = title
            self._win_lbl.setText(title or "(none)")
            self._capture_btn.setText("Capture 3s")
            self._capture_btn.setEnabled(self._macro.use_target_window)
            self._refresh_pid_label()
            self.changed.emit(self._macro.id)

    def _start_drag_pick(self, slot: int):
        parent_win = self.window()
        parent_win.showMinimized()
        from PyQt6.QtWidgets import QDialog, QVBoxLayout
        hint = QDialog(parent_win)
        hint.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        lbl = QLabel(f"  🎯  Click any window to set as target for lane '{self._macro.name}'.\n  ESC to cancel.  ")
        lbl.setStyleSheet(
            "background:#313244;color:#cdd6f4;font-size:14px;padding:18px;"
            "border:2px solid #89b4fa;border-radius:8px;")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        QVBoxLayout(hint).addWidget(lbl)
        hint.adjustSize(); hint.move(100, 100); hint.show()
        result: list = [None]
        def _on_click(x, y, button, pressed):
            if not pressed or button != ms_lib.Button.left: return
            hwnd = _u32.WindowFromPoint(ctypes.wintypes.POINT(int(x), int(y)))
            while True:
                p = _u32.GetParent(hwnd)
                if not p: break
                hwnd = p
            n = _u32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n+1)
                _u32.GetWindowTextW(hwnd, buf, n+1)
                result[0] = buf.value
            ml.stop(); return False
        def _on_key(key):
            if key == Key.esc: ml.stop()
            return False
        ml = ms_lib.Listener(on_click=_on_click)
        kl = kb_lib.Listener(on_press=_on_key)
        def _wait():
            ml.join(); kl.stop(); QTimer.singleShot(0, _done)
        def _done():
            hint.close()
            parent_win.showNormal(); parent_win.raise_(); parent_win.activateWindow()
            if result[0]:
                self._macro.target_window_title = result[0]
                self._win_lbl.setText(result[0])
                self._refresh_pid_label()
                self.changed.emit(self._macro.id)
        ml.start(); kl.start()
        threading.Thread(target=_wait, daemon=True).start()

    # ── Guard handlers ────────────────────────────────────────────────────────

    def _on_guard_enabled_changed(self, s):
        self._macro.pixel_guard_enabled = bool(s)
        self.changed.emit(self._macro.id)

    def _on_guard_flags_changed(self):
        raw = self._guard_flags_edit.text()
        self._macro.pixel_guard_red_flags = [f.strip() for f in raw.split(",") if f.strip()]
        self.changed.emit(self._macro.id)

    def _on_guard_key_changed(self):
        self._macro.pixel_guard_correction_key = self._guard_key_edit.text().strip() or "b"
        self.changed.emit(self._macro.id)

    def _on_guard_macro_changed(self, _):
        self._macro.pixel_guard_correction_macro = self._guard_macro_combo.currentData() or ""
        self.changed.emit(self._macro.id)

    def _on_guard_cap_changed(self):
        self._macro.pixel_guard_cap_x_pct = self._guard_cap_x_spin.value()
        self._macro.pixel_guard_cap_y_pct = self._guard_cap_y_spin.value()
        self._macro.pixel_guard_cap_w_pct = self._guard_cap_w_spin.value()
        self._macro.pixel_guard_cap_h_pct = self._guard_cap_h_spin.value()
        self.changed.emit(self._macro.id)

    def _test_ocr(self):
        m = self._macro
        hwnd = find_window_hwnd(m.target_window_title) if (
            m.use_target_window and m.target_window_title) else None
        text = _pixel_ocr_region(hwnd,
            self._guard_cap_x_spin.value(), self._guard_cap_y_spin.value(),
            self._guard_cap_w_spin.value(), self._guard_cap_h_spin.value())
        matched = _pixel_flags_match(text, getattr(m, "pixel_guard_red_flags", []))
        color = "#f38ba8" if matched else "#a6e3a1"
        result = (f"OCR: '{text or '(empty)'}'\n" +
                  (f"🚩 Matched: '{matched}'" if matched else "✓ No flags matched."))
        msg = QMessageBox(self.window())
        msg.setWindowTitle("OCR Test")
        msg.setText(f"<span style='color:{color}'>{result}</span>")
        msg.exec()


# ══════════════════════════════════════════════════════════════════════════════
# MAIN WINDOW
# ══════════════════════════════════════════════════════════════════════════════

class MainWindow(QMainWindow):

    _global_shortcut_sig = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"QytCroRec v{__version__}")
        self.resize(1200, 750)
        self.setMinimumSize(900, 560)

        self._storage      = Storage()
        self._groups: list[MacroGroup]            = self._storage.load_groups()
        self._sc_config: dict[str, str]           = self._storage.load_shortcuts()
        self._cur_group: Optional[MacroGroup]     = None
        self._lane_widgets: list[LaneWidget]      = []   # [primary, sec1, sec2]
        self._recorders: dict[str, RecorderThread]= {}   # macro_id → recorder
        self._chain_players: dict[str, ChainPlayerThread] = {}  # group_id → chain player
        self._qtshortcuts:   dict[str, QShortcut] = {}
        self._shortcut_btns: dict[str, list[QPushButton]] = {}
        self._play_failure_msg: dict[str, str]    = {}
        self._deleted_group: Optional[MacroGroup] = None
        self._flash_timer = QTimer(timeout=self._flash_record_btn)
        self._flash_state = False
        self._play_start_times: dict = {}
        self._recording_lane: Optional[str] = None   # macro id currently recording

        # Ensure every group has 3 lanes
        for g in self._groups:
            g.ensure_lanes()
        if not self._groups:
            self._groups.append(self._new_group_obj("Group 1"))

        self._hotkeys     = HotkeyManager(self._hotkey_fired)
        self._app_hotkeys = HotkeyManager(self._app_hotkey_fired_raw)
        self._global_shortcut_sig.connect(self._app_hotkey_fired_gui)

        self._build_ui()
        self._apply_shortcuts()
        self.setStyleSheet(STYLE)
        self._refresh_group_list()
        self._group_list.setCurrentRow(0)

        self._autosave = QTimer(timeout=self._autosave_fn)
        self._autosave.start(30_000)
        self._runtime_timer = QTimer(timeout=self._tick_runtime)

        self._setup_tray()
        self._rebuild_hotkeys()
        self._update_active_label()

        if AUTO_UPDATE_ENABLED:
            threading.Thread(target=self._check_for_updates, daemon=True).start()

    def _autosave_fn(self):
        self._storage.save_groups(self._groups)

    def _all_macros(self) -> list:
        out = []
        for g in self._groups:
            out.extend(g.lanes)
        return out

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION: Keyboard Shortcuts
    #   All application shortcuts live here.
    #   _apply_shortcuts() wires QShortcut objects from _sc_config.
    #   The Shortcuts tab lets the user edit _sc_config live.
    # ══════════════════════════════════════════════════════════════════════════

    def _shortcut_actions(self) -> dict:
        return {
            "new_macro":      self._new_group,
            "dup_macro":      self._dup_group,
            "del_macro":      self._del_group,
            "undo_delete":    self._undo_delete,
            "toggle_record":  self._toggle_record,
            "play":           self._play_chain,
            "stop":           lambda: None,   # individual lane stop via lane btn
            "stop_all":       self._stop_all,
            "clear_events":   self._del_events_if_focused,
            "capture_window": lambda: None,
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
        upd = AutoUpdater(__version__, UPDATE_VERSION_URL, UPDATE_SCRIPT_URL,
                          exe_url=UPDATE_EXE_URL)
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
        self._set_status("Downloading update…", "#f9e2af")
        # Run download off the GUI thread so UI doesn't freeze
        def _do_download():
            target = Path(__file__).resolve()
            ok = upd.download_and_install(target)
            QTimer.singleShot(0, lambda: self._on_update_done(ok, upd))
        threading.Thread(target=_do_download, daemon=True).start()

    def _on_update_done(self, ok: bool, upd: "AutoUpdater"):
        if ok:
            self._set_status("Update downloaded — restarting…", "#a6e3a1")
            QTimer.singleShot(600, self._restart_app)
        else:
            QMessageBox.warning(self, "Update Failed",
                "Could not download the update.  See console for details.")

    def _restart_app(self):
        """Relaunch and quit this instance.  Handles both exe and script mode."""
        self._storage.save_macros(self._macros)
        if getattr(sys, "frozen", False):
            # Exe mode: batch swap script already launched by AutoUpdater —
            # just quit so the batch can overwrite the exe while it's not running.
            QApplication.quit()
        else:
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
        root_w = _BgWidget()
        self.setCentralWidget(root_w)
        root = QVBoxLayout(root_w)
        root.setContentsMargins(10, 10, 10, 8)
        root.setSpacing(6)

        root.addLayout(self._build_topbar())

        # Main splitter: groups list | lane tabs
        main_sp = QSplitter(Qt.Orientation.Horizontal)
        main_sp.setHandleWidth(1)
        main_sp.addWidget(self._build_left())
        main_sp.addWidget(self._build_center())
        main_sp.setSizes([200, 1000])
        main_sp.setStretchFactor(0, 0)
        main_sp.setStretchFactor(1, 1)

        # Vertical splitter: main area | shared log
        vert_sp = QSplitter(Qt.Orientation.Vertical)
        vert_sp.setHandleWidth(3)
        vert_sp.addWidget(main_sp)
        vert_sp.addWidget(self._build_log_pane())
        vert_sp.setSizes([600, 140])
        root.addWidget(vert_sp, 1)

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

    def _build_topbar(self) -> QHBoxLayout:
        row = QHBoxLayout(); row.setSpacing(6)
        btn_new = QPushButton("＋ New Group")
        btn_dup = QPushButton("⎘ Duplicate")
        btn_del = QPushButton("✕ Delete"); btn_del.setObjectName("btn_del")
        btn_new.clicked.connect(self._new_group)
        btn_dup.clicked.connect(self._dup_group)
        btn_del.clicked.connect(self._del_group)
        self._register_btn("new_macro", btn_new, "New group")
        self._register_btn("dup_macro", btn_dup, "Duplicate group")
        self._register_btn("del_macro", btn_del, "Delete group")
        for b in (btn_new, btn_dup, btn_del): row.addWidget(b)
        row.addStretch()
        btn_diag = QPushButton("📋 Diag Log")
        btn_diag.setStyleSheet(
            "QPushButton{background:#313244;color:#a6adc8;border:1px solid #45475a;"
            "border-radius:5px;padding:4px 10px;font-size:12px;}"
            "QPushButton:hover{background:#45475a;color:#cdd6f4;}")
        btn_diag.clicked.connect(self._copy_diag_log)
        row.addWidget(btn_diag)
        btn_crash = QPushButton("📋 Crash Log")
        btn_crash.setStyleSheet(
            "QPushButton{background:#313244;color:#f38ba8;border:1px solid #45475a;"
            "border-radius:5px;padding:4px 10px;font-size:12px;}"
            "QPushButton:hover{background:#45475a;color:#f5a3bb;}")
        btn_crash.clicked.connect(self._copy_crash_log)
        row.addWidget(btn_crash)
        row.addSpacing(12)
        btn_tray = QPushButton("⊟ Tray")
        btn_tray.setToolTip("Minimize to system tray")
        btn_tray.setStyleSheet(
            "QPushButton{background:#313244;color:#a6adc8;border:1px solid #45475a;"
            "border-radius:5px;padding:4px 10px;font-size:12px;}"
            "QPushButton:hover{background:#45475a;color:#cdd6f4;}")
        btn_tray.clicked.connect(self._minimize_to_tray)
        row.addWidget(btn_tray)
        row.addSpacing(6)
        title = QLabel(f"QytCroRec v{__version__}")
        title.setStyleSheet("font-size:16px;font-weight:bold;color:#89b4fa;")
        row.addWidget(title)
        return row

    def _build_left(self) -> QWidget:
        w = QWidget(); w.setMinimumWidth(170); w.setMaximumWidth(230)
        lay = QVBoxLayout(w); lay.setContentsMargins(0, 0, 6, 0); lay.setSpacing(4)
        hdr = QLabel("MACRO GROUPS")
        hdr.setStyleSheet("color:#585b70;font-size:11px;font-weight:bold;padding:2px 0;")
        lay.addWidget(hdr)
        self._group_list = QListWidget()
        self._group_list.currentRowChanged.connect(self._on_group_row_changed)
        lay.addWidget(self._group_list)
        return w

    def _build_center(self) -> QWidget:
        """Stacked widget: lane tabs per group, plus Shortcuts tab."""
        w = QWidget()
        lay = QVBoxLayout(w); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(0)

        # Lane tab widget (Primary | Lane 2 | Lane 3 | ⌨ Shortcuts)
        self._lane_tabs = QTabWidget()
        self._lane_tabs.setStyleSheet(
            "QTabBar::tab { padding:7px 20px; font-size:13px; }"
            "QTabBar::tab:selected { background:#313244; color:#cdd6f4; }")
        # Placeholder lane widgets — replaced when a group is selected
        for i, label in enumerate(["⭐ Primary", "➕ Lane 2", "➕ Lane 3"]):
            placeholder = QWidget()
            self._lane_tabs.addTab(placeholder, label)
        self._lane_tabs.addTab(self._build_tab_shortcuts(), "⌨ Shortcuts")
        lay.addWidget(self._lane_tabs)
        return w

    def _build_log_pane(self) -> QWidget:
        from PyQt6.QtWidgets import QTextEdit
        w = QWidget()
        lay = QVBoxLayout(w); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(2)
        hdr = QHBoxLayout()
        lbl = QLabel("Activity Log")
        lbl.setStyleSheet("color:#585b70;font-size:11px;font-weight:bold;")
        hdr.addWidget(lbl)
        btn_clear = QPushButton("Clear"); btn_clear.setMaximumWidth(60)
        btn_clear.setStyleSheet(
            "QPushButton{background:#313244;color:#a6adc8;border:1px solid #45475a;"
            "border-radius:4px;padding:2px 8px;font-size:11px;}"
            "QPushButton:hover{background:#45475a;}")
        btn_clear.clicked.connect(lambda: self._log.clear())
        hdr.addWidget(btn_clear); hdr.addStretch()
        lay.addLayout(hdr)
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(200)
        self._log.setStyleSheet(
            "QTextEdit{background:#181825;border:1px solid #313244;border-radius:4px;"
            "font-family:Consolas,monospace;font-size:11px;color:#a6adc8;padding:4px;}")
        lay.addWidget(self._log)
        return w

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

    # ── Tab: Guard ────────────────────────────────────────────────────────────

    def _build_tab_guard(self) -> QWidget:
        from PyQt6.QtWidgets import QScrollArea
        inner = QWidget()
        lay   = QVBoxLayout(inner); lay.setContentsMargins(10, 10, 10, 10); lay.setSpacing(10)

        # ── OCR availability notice ──────────────────────────────────────────
        _bundled = bool(getattr(sys, "_MEIPASS", None))
        if not _PILLOW_OK or not _TESSERACT_OK:
            if _bundled:
                # Running from exe but OCR imports still failed — unusual
                note_text = (
                    "⚠  OCR libraries failed to load inside the bundle.\n"
                    "   Try rebuilding with  build_setup.bat  to ensure\n"
                    "   Pillow, pytesseract, and Tesseract are included.")
            else:
                missing = []
                if not _PILLOW_OK:    missing.append("Pillow          →  pip install Pillow")
                if not _TESSERACT_OK: missing.append(
                    "pytesseract     →  pip install pytesseract\n"
                    "   Tesseract binary  →  run  build_setup.bat  (bundles it automatically)")
                note_text = "⚠  Pixel Bot Guard requires:\n" + "\n".join(
                    f"   • {m}" for m in missing)
            note = QLabel(note_text)
            note.setStyleSheet(
                "color: #f38ba8; background: #2a1a1a; border: 1px solid #f38ba8; "
                "border-radius: 5px; padding: 8px; font-size: 12px;")
            note.setWordWrap(True)
            lay.addWidget(note)

        # ── Enable ───────────────────────────────────────────────────────────
        grp_en = QGroupBox("Pixel Bot Guard")
        gl_en  = QVBoxLayout(grp_en); gl_en.setSpacing(8)

        r_en = QHBoxLayout()
        self._guard_enabled_chk = QCheckBox("Enable Pixel Bot Guard for this macro")
        self._guard_enabled_chk.setToolTip(
            "When enabled, events with '🛡' checked will trigger an OCR read\n"
            "of the configured screen region before executing.  If a red-flag\n"
            "location is detected the correction runs and the macro restarts.")
        self._guard_enabled_chk.stateChanged.connect(self._on_guard_enabled_changed)
        r_en.addWidget(self._guard_enabled_chk); r_en.addStretch()
        gl_en.addLayout(r_en)
        lay.addWidget(grp_en)

        # ── Red-flag locations ───────────────────────────────────────────────
        grp_rf = QGroupBox("Red-Flag Locations")
        gl_rf  = QVBoxLayout(grp_rf); gl_rf.setSpacing(6)
        rf_note = QLabel(
            "Comma-separated location names.  If the OCR text contains any of\n"
            "these (case-insensitive), the correction triggers and the macro\n"
            "restarts from the beginning (skipping leading keyboard events).")
        rf_note.setStyleSheet("color: #a6adc8; font-size: 12px;")
        rf_note.setWordWrap(True)
        gl_rf.addWidget(rf_note)
        self._guard_flags_edit = QLineEdit(placeholderText="e.g.  The Raft, Danger Zone, Hostile Area")
        self._guard_flags_edit.editingFinished.connect(self._on_guard_flags_changed)
        gl_rf.addWidget(self._guard_flags_edit)
        lay.addWidget(grp_rf)

        # ── Capture region ───────────────────────────────────────────────────
        grp_cap = QGroupBox("Screen Capture Region  (% of target window)")
        gl_cap  = QVBoxLayout(grp_cap); gl_cap.setSpacing(6)
        cap_note = QLabel(
            "Defines the rectangle read by OCR.  Values are fractions of the\n"
            "target window size (0.0–1.0).  Default covers top-right HUD area.")
        cap_note.setStyleSheet("color: #a6adc8; font-size: 12px;")
        gl_cap.addWidget(cap_note)

        cap_row = QHBoxLayout()
        for label, attr, default in [
            ("X offset", "_guard_cap_x", 0.75),
            ("Y offset", "_guard_cap_y", 0.02),
            ("Width",    "_guard_cap_w", 0.24),
            ("Height",   "_guard_cap_h", 0.06),
        ]:
            cap_row.addWidget(QLabel(f"{label}:"))
            sp = QDoubleSpinBox()
            sp.setRange(0.0, 1.0); sp.setSingleStep(0.01); sp.setValue(default)
            sp.setDecimals(3); sp.setMaximumWidth(90)
            sp.valueChanged.connect(self._on_guard_cap_changed)
            setattr(self, attr + "_spin", sp)
            cap_row.addWidget(sp)
            cap_row.addSpacing(8)

        self._guard_test_btn = QPushButton("🔍 Test OCR")
        self._guard_test_btn.setToolTip(
            "Capture the region NOW and show the OCR result — useful for\n"
            "verifying the region covers the location text.")
        self._guard_test_btn.clicked.connect(self._test_guard_ocr)
        cap_row.addWidget(self._guard_test_btn)
        cap_row.addStretch()
        gl_cap.addLayout(cap_row)
        lay.addWidget(grp_cap)

        # ── Correction ───────────────────────────────────────────────────────
        grp_cor = QGroupBox("Correction Action  (runs when red-flag detected)")
        gl_cor  = QVBoxLayout(grp_cor); gl_cor.setSpacing(6)

        cor_row1 = QHBoxLayout()
        cor_row1.addWidget(QLabel("Press key:"))
        self._guard_key_edit = QLineEdit("b")
        self._guard_key_edit.setMaximumWidth(60)
        self._guard_key_edit.setToolTip(
            "Single key pressed when a red-flag is detected (default: b).\n"
            "Ignored if a Correction Macro is selected below.")
        self._guard_key_edit.editingFinished.connect(self._on_guard_key_changed)
        cor_row1.addWidget(self._guard_key_edit)
        cor_row1.addSpacing(20)
        cor_row1.addWidget(QLabel("OR  Correction Macro:"))
        self._guard_macro_combo = QComboBox()
        self._guard_macro_combo.setMinimumWidth(200)
        self._guard_macro_combo.setToolTip(
            "Run this macro's events inline instead of a single key press.\n"
            "Leave as '(none)' to use the key press above.")
        self._guard_macro_combo.currentIndexChanged.connect(self._on_guard_macro_changed)
        cor_row1.addWidget(self._guard_macro_combo)
        cor_row1.addStretch()
        gl_cor.addLayout(cor_row1)

        cor_note = QLabel(
            "After correction: macro restarts from event 0, skipping leading\n"
            "keyboard-only events (so initial setup keys are not repeated).")
        cor_note.setStyleSheet("color: #a6adc8; font-size: 12px;")
        gl_cor.addWidget(cor_note)
        lay.addWidget(grp_cor)

        lay.addStretch()

        # Wrap in a scroll area so it survives small windows
        scroll = QScrollArea()
        scroll.setWidget(inner); scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; }")
        return scroll

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
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels(["#", "Time (s)", "Type", "Details", "🛡"])
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        hh.resizeSection(0, 50); hh.resizeSection(1, 100); hh.resizeSection(2, 120)
        hh.resizeSection(4, 34)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setStyleSheet("QTableWidget { alternate-background-color: #1a1a2a; }")
        self._table.verticalHeader().setVisible(True)   # needed for drag handle
        self._table.verticalHeader().setSectionsMovable(True)
        self._table.verticalHeader().sectionMoved.connect(self._on_section_moved)
        self._table.cellClicked.connect(self._on_table_cell_clicked)
        self._table.itemChanged.connect(self._on_guard_item_changed)
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

        self._btn_play_chain = QPushButton("▶▶  Play Chain")
        self._btn_play_chain.setObjectName("btn_play")
        self._btn_play_chain.setMinimumWidth(130)
        self._btn_play_chain.setToolTip(
            "Run Primary → Lane 2 → Lane 3 in sequence.\n"
            "Repeats according to Primary's repeat setting.")
        self._btn_play_chain.clicked.connect(self._play_chain)
        self._register_btn("play", self._btn_play_chain, "Play chain (all lanes)")

        self._btn_stop_all = QPushButton("■  Stop All")
        self._btn_stop_all.setObjectName("btn_stop")
        self._btn_stop_all.setEnabled(False)
        self._btn_stop_all.clicked.connect(self._stop_all)
        self._register_btn("stop_all", self._btn_stop_all, "Stop all running chains")

        for b in (self._btn_play_chain, self._btn_stop_all):
            row.addWidget(b)
        row.addStretch()

        sc_hint = QLabel()
        sc_hint.setStyleSheet("color: #45475a; font-size: 11px;")
        def _update_hint():
            sc = self._sc_config
            sc_hint.setText(
                f"Rec: {sc.get('toggle_record','?')}   "
                f"Play: {sc.get('play','?')}   "
                f"Stop All: {sc.get('stop_all','?')}")
        _update_hint()
        self._sc_hint_updater = _update_hint
        row.addWidget(sc_hint)
        return row

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION: Group Management
    # ══════════════════════════════════════════════════════════════════════════

    def _new_group_obj(self, name: str = "New Group") -> MacroGroup:
        g = MacroGroup(name=name)
        g.ensure_lanes()
        return g

    def _new_group(self):
        g = self._new_group_obj(f"Group {len(self._groups)+1}")
        self._groups.append(g)
        self._refresh_group_list()
        self._group_list.setCurrentRow(len(self._groups)-1)
        self._storage.save_groups(self._groups)

    def _dup_group(self):
        if not self._cur_group: return
        import copy
        g2 = copy.deepcopy(self._cur_group)
        g2.id = str(uuid.uuid4())
        g2.name = f"{g2.name} (copy)"
        for lane in g2.lanes:
            lane.id = str(uuid.uuid4())
        self._groups.append(g2)
        self._refresh_group_list()
        self._group_list.setCurrentRow(len(self._groups)-1)
        self._storage.save_groups(self._groups)

    def _del_group(self):
        if not self._cur_group: return
        if QMessageBox.question(
            self, "Delete", f"Delete group '{self._cur_group.name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes: return
        gid = self._cur_group.id
        if gid in self._chain_players:
            self._chain_players[gid].stop()
        self._deleted_group = self._cur_group
        self._groups = [g for g in self._groups if g.id != gid]
        self._cur_group = None
        if not self._groups:
            self._groups.append(self._new_group_obj("Group 1"))
        self._refresh_group_list()
        self._group_list.setCurrentRow(0)
        self._storage.save_groups(self._groups)
        self._set_status(f"Deleted '{self._deleted_group.name}' — Ctrl+Z to undo", "#fab387")

    def _undo_delete(self):
        if not self._deleted_group: return
        self._groups.append(self._deleted_group)
        self._deleted_group = None
        self._refresh_group_list()
        self._group_list.setCurrentRow(len(self._groups)-1)
        self._storage.save_groups(self._groups)
        self._set_status("Delete undone.", "#a6e3a1")

    def _refresh_group_list(self):
        self._group_list.blockSignals(True)
        cur_id = self._cur_group.id if self._cur_group else None
        self._group_list.clear(); restore = 0
        for i, g in enumerate(self._groups):
            playing = g.id in self._chain_players
            item = QListWidgetItem(f"{'▶ ' if playing else '   '}{g.name}")
            item.setData(Qt.ItemDataRole.UserRole, g.id)
            if playing: item.setForeground(QColor("#a6e3a1"))
            self._group_list.addItem(item)
            if g.id == cur_id: restore = i
        self._group_list.blockSignals(False)
        if self._groups: self._group_list.setCurrentRow(restore)

    def _on_group_row_changed(self, row: int):
        if 0 <= row < len(self._groups):
            self._load_group(self._groups[row])

    def _load_group(self, g: MacroGroup):
        self._cur_group = g
        g.ensure_lanes()
        # Rebuild the lane tabs for this group
        # Keep shortcuts tab (index 3)
        shortcuts_widget = self._lane_tabs.widget(3)
        # Remove lane tabs (indices 0,1,2)
        while self._lane_tabs.count() > 1:
            self._lane_tabs.removeTab(0)
        # Remove shortcuts too, we'll re-add it
        self._lane_tabs.removeTab(0)

        self._lane_widgets = []
        lane_labels = ["⭐ Primary", "➕ Lane 2", "➕ Lane 3"]
        for i, lane in enumerate(g.lanes):
            lw = LaneWidget(
                macro=lane,
                lane_index=i,
                all_macros_fn=self._all_macros,
                parent=self)
            lw.changed.connect(lambda mid: self._on_lane_changed())
            lw.record_req.connect(self._on_record_req)
            lw.play_req.connect(self._on_lane_play_req)
            lw.stop_req.connect(self._on_lane_stop_req)
            self._lane_widgets.append(lw)
            self._lane_tabs.addTab(lw, lane_labels[i])

        self._lane_tabs.addTab(shortcuts_widget, "⌨ Shortcuts")
        self._lane_tabs.setCurrentIndex(0)
        self._rebuild_hotkeys()
        self._update_play_btns()

    def _on_lane_changed(self):
        self._storage.save_groups(self._groups)
        self._rebuild_hotkeys()
        # Refresh group name from primary lane name if group name matches default
        if self._cur_group and self._lane_widgets:
            pname = self._lane_widgets[0].macro.name
            # Sync group list item display
            self._refresh_group_list()

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

    # (old macro list / CRUD replaced by group management above)

    def _rebuild_hotkeys(self):
        hmap = {}
        for g in self._groups:
            for lane in g.lanes:
                if lane.trigger_hotkey.strip():
                    try:
                        hmap[user_hotkey_to_pynput(lane.trigger_hotkey.strip())] = (
                            g.id, lane.id)
                    except Exception: pass
        # Store mapping for lookup in _hotkey_fired
        self._hotkey_map = hmap
        self._hotkeys.update({k: v for k, v in hmap.items()})

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
    # SECTION: Per-lane Recording
    # ══════════════════════════════════════════════════════════════════════════

    def _toggle_record(self):
        """Global shortcut: toggle recording on the currently visible lane tab."""
        if not self._lane_widgets: return
        tab = self._lane_tabs.currentIndex()
        if 0 <= tab < len(self._lane_widgets):
            lw = self._lane_widgets[tab]
            mid = lw.macro.id
            if mid in self._recorders:
                self._stop_recording(mid)
            else:
                self._on_record_req(mid)

    def _on_record_req(self, macro_id: str):
        """LaneWidget record button clicked."""
        # Stop if already recording this lane
        if macro_id in self._recorders:
            self._stop_recording(macro_id); return
        # Stop any other active recording first
        for mid in list(self._recorders.keys()):
            self._stop_recording(mid)
        # Find the lane widget
        lw = self._find_lane_widget(macro_id)
        if not lw: return
        m = lw.macro
        m.events = []; lw._fill_table()
        target_hwnd = None
        if m.use_target_window and m.target_window_title:
            target_hwnd = find_window_hwnd(m.target_window_title)
        rec = RecorderThread(
            record_mouse_move=m.record_mouse_move,
            filter_keys=self._build_shortcut_filter(),
            target_hwnd=target_hwnd)
        rec.captured.connect(lambda ev, lw_=lw: lw_.add_event(ev))
        rec.done.connect(lambda mid=macro_id: self._on_rec_done(mid))
        self._recorders[macro_id] = rec
        lw.set_recording(True)
        self._recording_lane = macro_id
        self._flash_timer.start(600)
        sound_record_start()
        self._set_status(f"● REC lane '{m.name}'", "#f38ba8")
        rec.begin()

    def _stop_recording(self, macro_id: str):
        rec = self._recorders.get(macro_id)
        if rec: rec.end()

    def _on_rec_done(self, macro_id: str):
        self._recorders.pop(macro_id, None)
        self._flash_timer.stop()
        lw = self._find_lane_widget(macro_id)
        if lw:
            lw.set_recording(False)
            lw._fill_table()
            n = len(lw.macro.events)
            self._set_status(f"Rec done — {n} events in '{lw.macro.name}'", "#a6adc8")
        self._recording_lane = None
        sound_record_stop()
        self._storage.save_groups(self._groups)

    def _build_shortcut_filter(self) -> set:
        skip = set()
        alias = {"del":"delete","return":"enter","esc":"escape",
                 "ins":"insert","pgup":"page_up","pgdn":"page_down"}
        for binding in self._sc_config.values():
            if not binding: continue
            final = binding.split("+")[-1].strip().lower()
            if not final or final in ("ctrl","control","alt","shift","meta","cmd","win"):
                continue
            final = alias.get(final, final)
            skip.add(final if len(final) == 1 else f"Key.{final}")
        return skip

    def _flash_record_btn(self):
        self._flash_state = not self._flash_state
        lw = self._find_lane_widget(self._recording_lane) if self._recording_lane else None
        if lw:
            lw._btn_record.setText(
                "⏹ Stop Rec ●" if self._flash_state else "⏹ Stop Rec  ")

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION: Playback — Chain + single-lane
    # ══════════════════════════════════════════════════════════════════════════

    def _play_chain(self):
        """Play button: run entire chain for the current group."""
        if not self._cur_group or self._recorders: return
        gid = self._cur_group.id
        if gid in self._chain_players: return
        if not self._cur_group.lanes[0].events:
            self._set_status("Primary lane has no events.", "#fab387"); return
        cp = ChainPlayerThread(self._cur_group, self._groups)
        cp.lane_started.connect(self._on_lane_started)
        cp.lane_stopped.connect(self._on_lane_stopped_sig)
        cp.chain_stopped.connect(self._on_chain_stopped)
        cp.progress_sig.connect(self._on_chain_progress)
        cp.mode_sig.connect(self._on_chain_mode)
        cp.guard_sig.connect(self._on_chain_guard)
        cp.log_sig.connect(self._append_log)
        self._chain_players[gid] = cp
        self._play_start_times[gid] = time.perf_counter()
        if not self._runtime_timer.isActive():
            self._runtime_timer.start(200)
        cp.start()
        self._update_play_btns()
        self._refresh_group_list()
        self._set_status(f"▶▶ Chain '{self._cur_group.name}' started", "#a6e3a1")
        sound_play_start()
        speak(self._cur_group.name)

    def _on_lane_play_req(self, macro_id: str):
        """Single-lane play from LaneWidget button (plays just that lane once)."""
        if self._recorders: return
        lane = self._find_lane(macro_id)
        if not lane or not lane.events:
            self._set_status("No events in lane.", "#fab387"); return
        # Build a 1-lane temp group
        tmp = MacroGroup(name=f"[{lane.name}]")
        tmp.lanes = [lane]
        if macro_id in self._chain_players: return
        cp = ChainPlayerThread(tmp, self._groups)
        cp.lane_started.connect(self._on_lane_started)
        cp.lane_stopped.connect(self._on_lane_stopped_sig)
        cp.chain_stopped.connect(self._on_chain_stopped)
        cp.progress_sig.connect(self._on_chain_progress)
        cp.mode_sig.connect(self._on_chain_mode)
        cp.guard_sig.connect(self._on_chain_guard)
        cp.log_sig.connect(self._append_log)
        self._chain_players[macro_id] = cp   # key = macro_id for single-lane
        self._play_start_times[macro_id] = time.perf_counter()
        if not self._runtime_timer.isActive():
            self._runtime_timer.start(200)
        cp.start()
        lw = self._find_lane_widget(macro_id)
        if lw: lw.set_playing(True)
        self._update_play_btns()
        self._set_status(f"▶ Lane '{lane.name}' playing", "#a6e3a1")
        sound_play_start()

    def _on_lane_stop_req(self, macro_id: str):
        cp = self._chain_players.get(macro_id)
        if cp: cp.stop()
        # Also stop the group chain if the macro is part of active group
        if self._cur_group:
            cp2 = self._chain_players.get(self._cur_group.id)
            if cp2: cp2.stop()

    def _stop_all(self):
        for cp in list(self._chain_players.values()): cp.stop()
        for rec in list(self._recorders.values()): rec.end()

    # ── Chain signals ─────────────────────────────────────────────────────────

    def _on_lane_started(self, gid: str, lane_idx: int, mid: str):
        lw = self._find_lane_widget(mid)
        if lw: lw.set_playing(True)
        self._update_active_label()

    def _on_lane_stopped_sig(self, gid: str, lane_idx: int, mid: str):
        lw = self._find_lane_widget(mid)
        if lw: lw.set_playing(False)

    def _on_chain_stopped(self, gid: str):
        self._chain_players.pop(gid, None)
        self._play_start_times.pop(gid, None)
        if not self._chain_players:
            self._runtime_timer.stop()
            self._runtime_lbl.setText("")
        self._update_play_btns()
        self._refresh_group_list()
        for lw in self._lane_widgets:
            lw.set_playing(False)
        self._update_active_label()
        self._set_status("Chain complete.", "#a6adc8")
        sound_play_stop(); speak("done")
        self._storage.save_groups(self._groups)

    def _on_chain_progress(self, gid: str, lane_idx: int, idx: int, total: int):
        if self._cur_group and self._cur_group.id == gid:
            if 0 <= lane_idx < len(self._lane_widgets):
                self._lane_widgets[lane_idx].highlight_event(idx)

    def _on_chain_mode(self, gid: str, lane_idx: int, backend_name: str):
        if backend_name.startswith("error:"):
            reason = backend_name[6:]
            self._append_log(f"  ⚠ Lane {lane_idx} backend error: {reason}")
            self._set_status(f"Lane {lane_idx} backend failed: {reason}", "#f38ba8")
        else:
            self._append_log(f"  Backend: {backend_name}")

    def _on_chain_guard(self, gid: str, lane_idx: int, flag: str):
        self._set_status(f"🛡 Guard lane {lane_idx} — '{flag}' — correcting…", "#f9e2af")

    def _update_play_btns(self):
        any_p = bool(self._chain_players)
        self._btn_stop_all.setEnabled(any_p)
        self._btn_play_chain.setEnabled(
            not any_p and bool(self._cur_group))

    def _update_active_label(self):
        n = len(self._chain_players)
        total = sum(lane.run_count for g in self._groups for lane in g.lanes)
        self._active_lbl.setText(
            f"  ▶ {total} execution{'s' if total != 1 else ''}" if total else "")
        self._active_lbl.setToolTip(f"{n} chain(s) running" if n else "No chains running")

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
    # SECTION: Hotkey Callbacks + Utilities
    # ══════════════════════════════════════════════════════════════════════════

    def _hotkey_fired(self, payload):
        """payload is (group_id, macro_id) tuple from _hotkey_map."""
        if self._recorders: return
        if not isinstance(payload, tuple): return
        gid, mid = payload
        g = next((x for x in self._groups if x.id == gid), None)
        if not g: return
        if gid in self._chain_players:
            self._chain_players[gid].stop()
        else:
            if self._cur_group and self._cur_group.id == gid:
                self._play_chain()

    def _find_lane(self, macro_id: str) -> Optional[Macro]:
        for g in self._groups:
            for lane in g.lanes:
                if lane.id == macro_id: return lane
        return None

    def _find_lane_widget(self, macro_id: str) -> Optional[LaneWidget]:
        for lw in self._lane_widgets:
            if lw.macro.id == macro_id: return lw
        return None

    def _append_log(self, msg: str):
        ts = time.strftime("%H:%M:%S")
        self._log.append(f"[{ts}] {msg}")

    def _set_status(self, msg: str, color: str = "#a6adc8"):
        self._status.setText(msg)
        self._status.setStyleSheet(f"color: {color};")

    def _del_events_if_focused(self):
        # Delegate to active lane widget
        for lw in self._lane_widgets:
            if lw._table.hasFocus():
                lw._del_selected_events(); return

    def _minimize_to_tray(self):
        self._storage.save_groups(self._groups)
        self.hide()
        self._tray.showMessage("QytCroRec",
            "Running in system tray. Double-click to restore.",
            QSystemTrayIcon.MessageIcon.Information, 2500)

    def _quit_app(self):
        self._stop_all()
        deadline = time.perf_counter() + 1.5
        while self._chain_players and time.perf_counter() < deadline:
            time.sleep(0.05)
        self._hotkeys.stop(); self._app_hotkeys.stop()
        self._storage.save_groups(self._groups)
        try:
            faulthandler.disable(); _crash_fp.close()
        except Exception: pass
        QApplication.quit()

    def closeEvent(self, event):
        self._storage.save_groups(self._groups)
        msg = QMessageBox(self)
        msg.setWindowTitle("QytCroRec")
        msg.setText("What would you like to do?")
        btn_tray = msg.addButton("Minimize to Tray", QMessageBox.ButtonRole.AcceptRole)
        btn_quit = msg.addButton("Close Application", QMessageBox.ButtonRole.DestructiveRole)
        msg.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        msg.exec()
        clicked = msg.clickedButton()
        if clicked == btn_quit:
            event.accept()
            self._quit_app()
        elif clicked == btn_tray:
            event.ignore()
            self.hide()
            self._tray.showMessage("QytCroRec",
                "Running in system tray. Double-click to restore.",
                QSystemTrayIcon.MessageIcon.Information, 2500)
        else:
            event.ignore()


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

