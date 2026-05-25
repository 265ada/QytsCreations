// ─────────────────────────────────────────────────────────────────────────
// dinput_hook.cpp — Detours-based DirectInput / Raw-Input hook DLL
//
// Once injected into a target game process, this DLL:
//   1. Spawns a named-pipe server at \\.\pipe\macro_hook_<pid>
//   2. Listens for KD/KU/MD/MU/MA/MR/MW commands from macro_recorder.py
//   3. Maintains an internal "fake input state"
//   4. Hooks IDirectInputDevice8::GetDeviceState  — overlays fake key /
//      mouse state on whatever the game would normally read
//   5. Hooks GetAsyncKeyState and GetKeyState     — same idea for Win32
//      polling games
//
// Build (from a "Developer Command Prompt for VS"):
//   1. Get Detours:  git clone https://github.com/microsoft/Detours
//   2. cd Detours && nmake                   (builds detours.lib)
//   3. Set environment:
//        set DETOURS=C:\path\to\Detours
//   4. Run build.bat (in this folder).  It produces dinput_hook.dll
//
// Anti-cheat note: do NOT use this against multiplayer / commercial games
// where it would violate the publisher's ToS or trigger their anti-cheat
// (BattlEye, EAC, etc.).  Use it for single-player automation, accessibility,
// QA harnesses against your own apps, private servers — that sort of thing.
// ─────────────────────────────────────────────────────────────────────────

#define INITGUID
#include <windows.h>
#include <dinput.h>
#include <detours.h>
#include <cstdio>
#include <cstring>
#include <mutex>
#include <string>
#include <thread>
#include <vector>
#include <cstdarg>

#pragma comment(lib, "dinput8.lib")
#pragma comment(lib, "dxguid.lib")
#pragma comment(lib, "detours.lib")

// ─── Shared fake state ──────────────────────────────────────────────────
static std::mutex   g_state_mtx;
static BYTE         g_fake_keys[256]    = {0};   // 0x80 = pressed (Win32 style)
static BYTE         g_fake_mbtn[8]      = {0};
// Win32 wParam state for WM_MOUSEMOVE — bit field of MK_LBUTTON / MK_RBUTTON
// / MK_MBUTTON.  Updated by MD/MU so dragging works correctly.
static WPARAM       g_mouse_btn_state   = 0;
// True while a macro is feeding input.  When set, real-hardware input is
// SUPPRESSED inside this process (mouse moves the user is doing with their
// physical mouse won't drag the game's cursor around).  Cleared on RESET.
static volatile bool g_macro_active     = false;
static DWORD        g_last_cmd_tick     = 0;
static LONG         g_fake_mouse_x      = 0;
static LONG         g_fake_mouse_y      = 0;
static LONG         g_fake_mouse_dx     = 0;
static LONG         g_fake_mouse_dy     = 0;
static LONG         g_fake_mouse_wheel  = 0;
static volatile bool g_running          = true;

// DirectInput uses a different keyboard table (DIK_*) — we keep a parallel
// 256-byte buffer for it.  Mapping VK→DIK is non-trivial; for this skeleton
// we apply VK letters/digits/function-keys with the standard mapping below.
static BYTE g_fake_di_keys[256] = {0};

// Cached top-level window of THIS process — used to PostMessage WM_KEYDOWN
// / WM_KEYUP for games that read keyboard via WindowProc rather than DInput.
static HWND g_target_window = NULL;

// Pipe + worker thread handles so DllMain can shut them down on unload.
static HANDLE g_pipe_handle = INVALID_HANDLE_VALUE;
static HANDLE g_pipe_thread = NULL;

// ─── Watchdog ───────────────────────────────────────────────────────────
// Tracks the PID of whichever process last connected to our named pipe.
// When that process dies (e.g. macro_recorder.py crashes / Task Mgr kills
// it) the watchdog thread self-unloads this DLL so the game's input APIs
// return to vanilla without the user having to restart the game.
static HMODULE g_self_module    = NULL;
static HANDLE  g_watchdog_thread = NULL;
static volatile DWORD g_last_client_pid = 0;
static volatile DWORD g_client_died_at  = 0;   // GetTickCount when we first saw it dead

struct FindWindowCtx { DWORD pid; HWND hwnd; };
static BOOL CALLBACK FindWindowEnumProc(HWND h, LPARAM lp) {
    FindWindowCtx* ctx = (FindWindowCtx*)lp;
    DWORD wpid = 0;
    GetWindowThreadProcessId(h, &wpid);
    if (wpid == ctx->pid && IsWindowVisible(h) && GetWindow(h, GW_OWNER) == NULL) {
        char title[256] = {0};
        GetWindowTextA(h, title, sizeof(title));
        if (title[0]) {
            ctx->hwnd = h;
            return FALSE;
        }
    }
    return TRUE;
}

static HWND FindOurMainWindow() {
    FindWindowCtx ctx = { GetCurrentProcessId(), NULL };
    EnumWindows(FindWindowEnumProc, (LPARAM)&ctx);
    return ctx.hwnd;
}

static BYTE VkToDik(int vk) {
    static const struct { int vk; BYTE dik; } table[] = {
        {0x1B, 0x01}, // ESC
        {'1', 0x02}, {'2', 0x03}, {'3', 0x04}, {'4', 0x05}, {'5', 0x06},
        {'6', 0x07}, {'7', 0x08}, {'8', 0x09}, {'9', 0x0A}, {'0', 0x0B},
        {0xBD, 0x0C}, {0xBB, 0x0D}, {0x08, 0x0E}, {0x09, 0x0F},   // - = BS TAB
        {'Q', 0x10}, {'W', 0x11}, {'E', 0x12}, {'R', 0x13}, {'T', 0x14},
        {'Y', 0x15}, {'U', 0x16}, {'I', 0x17}, {'O', 0x18}, {'P', 0x19},
        {0xDB, 0x1A}, {0xDD, 0x1B}, {0x0D, 0x1C}, {0x11, 0x1D},   // [ ] ENTER LCTRL
        {'A', 0x1E}, {'S', 0x1F}, {'D', 0x20}, {'F', 0x21}, {'G', 0x22},
        {'H', 0x23}, {'J', 0x24}, {'K', 0x25}, {'L', 0x26},
        {0xBA, 0x27}, {0xDE, 0x28}, {0xC0, 0x29}, {0x10, 0x2A},   // ; ' ` LSHIFT
        {0xDC, 0x2B},
        {'Z', 0x2C}, {'X', 0x2D}, {'C', 0x2E}, {'V', 0x2F}, {'B', 0x30},
        {'N', 0x31}, {'M', 0x32},
        {0xBC, 0x33}, {0xBE, 0x34}, {0xBF, 0x35},
        {0x12, 0x38}, {0x20, 0x39}, {0x14, 0x3A},                  // LALT SPACE CAPS
        {0x70, 0x3B}, {0x71, 0x3C}, {0x72, 0x3D}, {0x73, 0x3E},   // F1..F4
        {0x74, 0x3F}, {0x75, 0x40}, {0x76, 0x41}, {0x77, 0x42},   // F5..F8
        {0x78, 0x43}, {0x79, 0x44}, {0x7A, 0x57}, {0x7B, 0x58},   // F9..F12
        {0x25, 0xCB}, {0x27, 0xCD}, {0x26, 0xC8}, {0x28, 0xD0},   // arrows
        {0x24, 0xC7}, {0x23, 0xCF}, {0x21, 0xC9}, {0x22, 0xD1},   // home/end/pgup/pgdn
        {0x2D, 0xD2}, {0x2E, 0xD3},                                // INS DEL
    };
    for (auto& e : table) if (e.vk == vk) return e.dik;
    return 0;
}

// ─── Hooked function pointers (Detours fills these in) ──────────────────
static SHORT (WINAPI* Real_GetAsyncKeyState)(int) = GetAsyncKeyState;
static SHORT (WINAPI* Real_GetKeyState)(int)      = GetKeyState;
static BOOL  (WINAPI* Real_GetKeyboardState)(PBYTE) = GetKeyboardState;
static HWND  (WINAPI* Real_GetForegroundWindow)()  = GetForegroundWindow;
static HWND  (WINAPI* Real_GetActiveWindow)()      = GetActiveWindow;
static HWND  (WINAPI* Real_GetFocus)()             = GetFocus;
static BOOL  (WINAPI* Real_GetCursorPos)(LPPOINT)  = GetCursorPos;

// IDirectInputDevice8 vtable functions (patched at runtime — see InstallDInputHook).
typedef HRESULT (STDMETHODCALLTYPE *PFN_GetDeviceState)(IDirectInputDevice8* This, DWORD, LPVOID);
typedef HRESULT (STDMETHODCALLTYPE *PFN_GetDeviceData)(IDirectInputDevice8* This, DWORD, LPDIDEVICEOBJECTDATA, LPDWORD, DWORD);
typedef HRESULT (STDMETHODCALLTYPE *PFN_SetDataFormat)(IDirectInputDevice8* This, LPCDIDATAFORMAT);
static PFN_GetDeviceState Real_GetDeviceState = nullptr;
static PFN_GetDeviceData  Real_GetDeviceData  = nullptr;
static PFN_SetDataFormat  Real_SetDataFormat  = nullptr;

// ─── Diagnostic log ────────────────────────────────────────────────────
// Writes to %USERPROFILE%\.macro_recorder\dll_hook.log so the host can read it.
// First few calls only — we don't want to fill the disk if the game polls
// thousands of times per second.
static HANDLE g_log = INVALID_HANDLE_VALUE;
static volatile LONG g_log_remaining = 2000;
static void LogF(const char* fmt, ...) {
    if (InterlockedDecrement(&g_log_remaining) < 0) return;
    if (g_log == INVALID_HANDLE_VALUE) return;
    char buf[256]; va_list ap; va_start(ap, fmt);
    int n = vsnprintf(buf, sizeof(buf) - 2, fmt, ap); va_end(ap);
    if (n < 0) return;
    if (n > (int)sizeof(buf) - 2) n = sizeof(buf) - 2;
    buf[n++] = '\n'; buf[n] = 0;
    DWORD w = 0; WriteFile(g_log, buf, (DWORD)n, &w, NULL);
}
static void OpenLog() {
    char path[MAX_PATH], home[MAX_PATH];
    if (!GetEnvironmentVariableA("USERPROFILE", home, MAX_PATH)) return;
    sprintf_s(path, MAX_PATH, "%s\\.macro_recorder\\dll_hook.log", home);
    g_log = CreateFileA(path, FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE,
                        NULL, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (g_log == INVALID_HANDLE_VALUE) return;
    SetFilePointer(g_log, 0, NULL, FILE_END);
    LogF("=== hook DLL loaded into PID %lu ===", GetCurrentProcessId());
    // Pre-discover the target window so KD/KU commands can post to it instantly.
    g_target_window = FindOurMainWindow();
    LogF("target window for WM_KEYDOWN/UP: %p", g_target_window);
}

// ─── Hook implementations ───────────────────────────────────────────────
SHORT WINAPI Hooked_GetAsyncKeyState(int vKey) {
    if (!g_macro_active) return Real_GetAsyncKeyState(vKey);
    std::lock_guard<std::mutex> _(g_state_mtx);
    bool fake = (vKey >= 0 && vKey < 256 && (g_fake_keys[vKey] & 0x80));
    return fake ? (SHORT)0x8001 : (SHORT)0;
}

SHORT WINAPI Hooked_GetKeyState(int vKey) {
    if (!g_macro_active) return Real_GetKeyState(vKey);
    std::lock_guard<std::mutex> _(g_state_mtx);
    bool fake = (vKey >= 0 && vKey < 256 && (g_fake_keys[vKey] & 0x80));
    return fake ? (SHORT)0x8001 : (SHORT)0;
}

HRESULT STDMETHODCALLTYPE Hooked_GetDeviceState(IDirectInputDevice8* This,
                                                DWORD cbData,
                                                LPVOID lpvData) {
    if (!g_macro_active) return Real_GetDeviceState(This, cbData, lpvData);
    HRESULT hr = Real_GetDeviceState(This, cbData, lpvData);
    if (FAILED(hr) || !lpvData) return hr;

    std::lock_guard<std::mutex> _(g_state_mtx);
    if (cbData == 256) {                                  // Keyboard (DIKEYBOARD)
        BYTE* keys = (BYTE*)lpvData;
        for (int i = 0; i < 256; ++i)
            if (g_fake_di_keys[i] & 0x80) keys[i] = 0x80;
    } else if (cbData == sizeof(DIMOUSESTATE2)) {         // Mouse w/ 8 buttons
        DIMOUSESTATE2* m = (DIMOUSESTATE2*)lpvData;
        m->lX += g_fake_mouse_dx;
        m->lY += g_fake_mouse_dy;
        m->lZ += g_fake_mouse_wheel;
        for (int i = 0; i < 8; ++i)
            if (g_fake_mbtn[i] & 0x80) m->rgbButtons[i] = 0x80;
        g_fake_mouse_dx = g_fake_mouse_dy = g_fake_mouse_wheel = 0;   // consume
    } else if (cbData == sizeof(DIMOUSESTATE)) {          // 4-button mouse
        DIMOUSESTATE* m = (DIMOUSESTATE*)lpvData;
        m->lX += g_fake_mouse_dx;
        m->lY += g_fake_mouse_dy;
        m->lZ += g_fake_mouse_wheel;
        for (int i = 0; i < 4; ++i)
            if (g_fake_mbtn[i] & 0x80) m->rgbButtons[i] = 0x80;
        g_fake_mouse_dx = g_fake_mouse_dy = g_fake_mouse_wheel = 0;
    }
    return hr;
}

// Hook GetDeviceData — buffered DInput.  Many UE3 games (including MHO)
// use this exclusively for keyboard input.  We append fake events into
// the caller's buffer, respecting its capacity.
static std::vector<DIDEVICEOBJECTDATA> g_di_queue;
static DWORD g_di_sequence = 0;

HRESULT STDMETHODCALLTYPE Hooked_GetDeviceData(IDirectInputDevice8* This,
                                               DWORD cbObjectData,
                                               LPDIDEVICEOBJECTDATA rgdod,
                                               LPDWORD pdwInOut,
                                               DWORD dwFlags) {
    // Fast pass-through when no macro is active — eliminates per-frame
    // overhead and shrinks our footprint during normal gameplay.
    if (!g_macro_active) {
        return Real_GetDeviceData(This, cbObjectData, rgdod, pdwInOut, dwFlags);
    }

    // Capture the caller's maximum BEFORE the real call (real call will
    // overwrite *pdwInOut with the count actually returned).
    DWORD requestedMax = (pdwInOut) ? *pdwInOut : 0;

    HRESULT hr = Real_GetDeviceData(This, cbObjectData, rgdod, pdwInOut, dwFlags);
    if (FAILED(hr) || !pdwInOut) return hr;

    DWORD have = *pdwInOut;
    if (!rgdod || cbObjectData == 0) return hr;          // probe-only call

    // While a macro is active, DROP the user's real hardware events so they
    // don't fight with the injected events.  This is the difference between
    // "mouse moves working but real mouse interferes" and true background.
    if (g_macro_active && have > 0) {
        have = 0;
        *pdwInOut = 0;
    }
    // Log a few real events (when not suppressing) so we can diagnose format.
    else if (have > 0) {
        DWORD entries = (have <= 2) ? have : 2;
        for (DWORD i = 0; i < entries; ++i) {
            LogF("REAL event[%lu]: dwOfs=%lu dwData=0x%lx ts=%lu seq=%lu",
                 i, rgdod[i].dwOfs, rgdod[i].dwData,
                 rgdod[i].dwTimeStamp, rgdod[i].dwSequence);
        }
    }

    std::lock_guard<std::mutex> _(g_state_mtx);
    if (g_di_queue.empty()) {
        // Only log occasionally to avoid swamping the log
        static LONG idle_log = 0;
        if ((InterlockedIncrement(&idle_log) & 0x3F) == 0)
            LogF("GetDeviceData idle: inOut=%lu have=%lu queue=0", requestedMax, have);
        return hr;
    }

    DWORD room = (requestedMax > have) ? (requestedMax - have) : 0;
    DWORD toInject = (DWORD)g_di_queue.size();
    if (toInject > room) toInject = room;

    LogF("GetDeviceData INJECT: inOut=%lu have=%lu room=%lu queue=%zu toInject=%lu",
         requestedMax, have, room, g_di_queue.size(), toInject);

    if (toInject == 0) return hr;   // no room this poll; events stay queued

    for (DWORD i = 0; i < toInject; ++i) {
        rgdod[have + i] = g_di_queue[i];
    }
    *pdwInOut = have + toInject;
    if (!(dwFlags & DIGDD_PEEK)) {
        g_di_queue.erase(g_di_queue.begin(), g_di_queue.begin() + toInject);
    }
    return hr;
}

// Hook SetDataFormat — find out exactly what data layout the game is using.
HRESULT STDMETHODCALLTYPE Hooked_SetDataFormat(IDirectInputDevice8* This,
                                               LPCDIDATAFORMAT lpdf) {
    HRESULT hr = Real_SetDataFormat(This, lpdf);
    if (lpdf) {
        LogF("SetDataFormat: dwSize=%lu dwObjSize=%lu dwFlags=0x%lx "
             "dwDataSize=%lu dwNumObjs=%lu  (DIDF_RELAXIS=2 ABS=1)",
             lpdf->dwSize, lpdf->dwObjSize, lpdf->dwFlags,
             lpdf->dwDataSize, lpdf->dwNumObjs);
        // Dump first ~16 objects to see if dwOfs values are DIK_* codes
        DWORD n = (lpdf->dwNumObjs < 16) ? lpdf->dwNumObjs : 16;
        for (DWORD i = 0; i < n; ++i) {
            const DIOBJECTDATAFORMAT& o = lpdf->rgodf[i];
            LogF("  obj[%lu]: dwOfs=%lu dwType=0x%lx dwFlags=0x%lx",
                 i, o.dwOfs, o.dwType, o.dwFlags);
        }
    } else {
        LogF("SetDataFormat: lpdf is NULL");
    }
    return hr;
}

// Hook GetKeyboardState — UE3 and many native Win32 games poll this.
// We OR our fake key state into the buffer the game just got.
BOOL WINAPI Hooked_GetKeyboardState(PBYTE lpKeyState) {
    if (!g_macro_active) return Real_GetKeyboardState(lpKeyState);
    BOOL ok = Real_GetKeyboardState(lpKeyState);
    if (ok && lpKeyState) {
        std::lock_guard<std::mutex> _(g_state_mtx);
        memset(lpKeyState, 0, 256);
        for (int i = 0; i < 256; ++i) {
            if (g_fake_keys[i] & 0x80) lpKeyState[i] |= 0x80;
        }
    }
    return ok;
}

// ─── Focus-spoofing hooks ───────────────────────────────────────────────
// Many games filter input by checking if their window is foreground.
// When QytCroRec runs the macro, the game IS background — so the game's
// own input handler would normally discard everything we inject.  By
// returning the game's own HWND from these APIs, we tell the game "you
// ARE the foreground window," and it accepts the synthetic input.

HWND WINAPI Hooked_GetForegroundWindow() {
    if (g_macro_active && g_target_window) return g_target_window;
    return Real_GetForegroundWindow();
}

HWND WINAPI Hooked_GetActiveWindow() {
    if (g_macro_active && g_target_window) return g_target_window;
    return Real_GetActiveWindow();
}

HWND WINAPI Hooked_GetFocus() {
    if (g_macro_active && g_target_window) return g_target_window;
    return Real_GetFocus();
}

// Return our fake cursor position to anything in the target process that
// reads GetCursorPos directly.  While macro_active is true, ALWAYS return
// the fake position — this is what stops the user's real mouse from
// shifting where clicks land in the game.
BOOL WINAPI Hooked_GetCursorPos(LPPOINT lpPoint) {
    BOOL ok = Real_GetCursorPos(lpPoint);
    if (ok && lpPoint && g_macro_active) {
        std::lock_guard<std::mutex> _(g_state_mtx);
        lpPoint->x = g_fake_mouse_x;
        lpPoint->y = g_fake_mouse_y;
    }
    return ok;
}

// ─── DirectInput vtable patch ───────────────────────────────────────────
// We can't statically know the address of IDirectInputDevice8::GetDeviceState;
// instead we create a dummy device, read its vtable, and patch the slot.
static void InstallDInputHook() {
    HRESULT hr;
    IDirectInput8* di = nullptr;
    hr = DirectInput8Create(GetModuleHandle(NULL), DIRECTINPUT_VERSION,
                            IID_IDirectInput8, (void**)&di, NULL);
    if (FAILED(hr) || !di) return;

    IDirectInputDevice8* dev = nullptr;
    hr = di->CreateDevice(GUID_SysKeyboard, &dev, NULL);
    if (SUCCEEDED(hr) && dev) {
        void** vt = *(void***)dev;
        // IDirectInputDevice8 vtable:
        //   index 9  = GetDeviceState  (immediate)
        //   index 10 = GetDeviceData   (buffered — what UE3 uses)
        Real_GetDeviceState = (PFN_GetDeviceState)vt[9];
        Real_GetDeviceData  = (PFN_GetDeviceData) vt[10];
        Real_SetDataFormat  = (PFN_SetDataFormat) vt[11];
        LogF("DInput vt[9]=%p vt[10]=%p vt[11]=%p", vt[9], vt[10], vt[11]);

        DetourTransactionBegin();
        DetourUpdateThread(GetCurrentThread());
        DetourAttach(&(PVOID&)Real_GetDeviceState, (PVOID)Hooked_GetDeviceState);
        DetourAttach(&(PVOID&)Real_GetDeviceData,  (PVOID)Hooked_GetDeviceData);
        DetourAttach(&(PVOID&)Real_SetDataFormat,  (PVOID)Hooked_SetDataFormat);
        LONG err = DetourTransactionCommit();
        LogF("DInput DetourAttach commit = %ld (0 = ok)", err);

        dev->Release();
    }
    di->Release();
}

// ─── Named-pipe command parser ──────────────────────────────────────────
static void HandleLine(const std::string& line) {
    char op[8] = {0};
    int  a = 0, b = 0;
    char btn = 0;
    if (sscanf_s(line.c_str(), "%7s", op, (unsigned)sizeof(op)) != 1) return;

    std::lock_guard<std::mutex> _(g_state_mtx);

    // Any non-RESET command counts as macro activity — start suppressing
    // real hardware input that would otherwise leak into the game.
    if (strcmp(op, "RESET") != 0) {
        if (!g_macro_active) LogF("MACRO ACTIVE — real input will be suppressed");
        g_macro_active = true;
        g_last_cmd_tick = GetTickCount();
    }

    if (!strcmp(op, "KD")) {
        if (sscanf_s(line.c_str(), "KD %d", &a) == 1 && a >= 0 && a < 256) {
            g_fake_keys[a] = 0x80;
            BYTE dik = VkToDik(a);
            // ── Win32-message path (what MHO and most UE3 games use) ──
            // Post WM_KEYDOWN to the game's own window.  Lazy-discover the
            // window the first time we need it.
            if (!g_target_window) g_target_window = FindOurMainWindow();
            LogF("CMD KD vk=%d dik=%u  hwnd=%p", a, dik, g_target_window);
            if (g_target_window) {
                LPARAM lp = (LPARAM)(MapVirtualKeyA(a, 0) << 16) | 1;
                PostMessageW(g_target_window, WM_KEYDOWN, (WPARAM)a, lp);
            }
            // ── DInput path (still useful for games that DO poll DI keyboard) ──
            if (dik) {
                g_fake_di_keys[dik] = 0x80;
                DIDEVICEOBJECTDATA e = {0};
                e.dwOfs = dik; e.dwData = 0x80;
                e.dwTimeStamp = GetTickCount();
                e.dwSequence  = ++g_di_sequence;
                g_di_queue.push_back(e);
            }
        }
    } else if (!strcmp(op, "KU")) {
        if (sscanf_s(line.c_str(), "KU %d", &a) == 1 && a >= 0 && a < 256) {
            g_fake_keys[a] = 0;
            BYTE dik = VkToDik(a);
            if (!g_target_window) g_target_window = FindOurMainWindow();
            LogF("CMD KU vk=%d dik=%u  hwnd=%p", a, dik, g_target_window);
            if (g_target_window) {
                // KEYUP lparam: scancode + bit 30 (previous state) + bit 31 (transition)
                LPARAM lp = (LPARAM)(MapVirtualKeyA(a, 0) << 16) | 1
                            | (1 << 30) | (1u << 31);
                PostMessageW(g_target_window, WM_KEYUP, (WPARAM)a, lp);
            }
            if (dik) {
                g_fake_di_keys[dik] = 0;
                DIDEVICEOBJECTDATA e = {0};
                e.dwOfs = dik; e.dwData = 0;
                e.dwTimeStamp = GetTickCount();
                e.dwSequence  = ++g_di_sequence;
                g_di_queue.push_back(e);
            }
        }
    } else if (!strcmp(op, "MA")) {
        if (sscanf_s(line.c_str(), "MA %d %d", &a, &b) == 2) {
            static bool s_seen_first_ma = false;
            int dx = a - g_fake_mouse_x;
            int dy = b - g_fake_mouse_y;
            g_fake_mouse_x = a; g_fake_mouse_y = b;

            // ── Background-safe mouse delivery ─────────────────────────
            // NO SetCursorPos here — that would hijack the real cursor.
            // Instead we PostMessage WM_MOUSEMOVE directly to the game
            // window with CLIENT coords.  WindowProcs fire regardless of
            // focus, so the game's mouse handler processes the message.
            if (!g_target_window) g_target_window = FindOurMainWindow();
            if (g_target_window) {
                POINT pt = { a, b };
                ScreenToClient(g_target_window, &pt);
                LPARAM lp = ((LONG)(pt.y & 0xFFFF) << 16) | (LONG)(pt.x & 0xFFFF);
                PostMessageW(g_target_window, WM_MOUSEMOVE,
                             g_mouse_btn_state, lp);
            }

            if (!s_seen_first_ma) {
                // First MA: position only, no DInput delta (would spin camera).
                s_seen_first_ma = true;
                LogF("CMD MA INITIAL=%d,%d (WM_MOUSEMOVE only, no DI delta)", a, b);
            } else {
                // Clamp + coalesce DInput deltas so the queue doesn't lag.
                if (dx >  200) dx =  200;
                if (dx < -200) dx = -200;
                if (dy >  200) dy =  200;
                if (dy < -200) dy = -200;
                g_fake_mouse_dx += dx;
                g_fake_mouse_dy += dy;
                auto add_or_merge = [](DWORD ofs, int delta) {
                    if (!delta) return;
                    if (!g_di_queue.empty() && g_di_queue.back().dwOfs == ofs) {
                        // Merge into trailing same-axis event — reduces queue
                        // pressure during fast camera sweeps.
                        g_di_queue.back().dwData =
                            (DWORD)((int)g_di_queue.back().dwData + delta);
                        g_di_queue.back().dwTimeStamp = GetTickCount();
                        return;
                    }
                    DIDEVICEOBJECTDATA e = {0};
                    e.dwOfs = ofs; e.dwData = (DWORD)delta;
                    e.dwTimeStamp = GetTickCount(); e.dwSequence = ++g_di_sequence;
                    g_di_queue.push_back(e);
                };
                add_or_merge(0, dx);
                add_or_merge(4, dy);
                LogF("CMD MA target=%d,%d dx=%d dy=%d (q=%zu)",
                     a, b, dx, dy, g_di_queue.size());
            }
        }
    } else if (!strcmp(op, "MR")) {
        if (sscanf_s(line.c_str(), "MR %d %d", &a, &b) == 2) {
            g_fake_mouse_dx += a; g_fake_mouse_dy += b;
            g_fake_mouse_x  += a; g_fake_mouse_y  += b;
            if (a) {
                DIDEVICEOBJECTDATA e = {0};
                e.dwOfs = 0; e.dwData = (DWORD)a;
                e.dwTimeStamp = GetTickCount(); e.dwSequence = ++g_di_sequence;
                g_di_queue.push_back(e);
            }
            if (b) {
                DIDEVICEOBJECTDATA e = {0};
                e.dwOfs = 4; e.dwData = (DWORD)b;
                e.dwTimeStamp = GetTickCount(); e.dwSequence = ++g_di_sequence;
                g_di_queue.push_back(e);
            }
        }
    } else if (!strcmp(op, "MD")) {
        if (sscanf_s(line.c_str(), "MD %c", &btn, 1) == 1) {
            int idx = btn == 'L' ? 0 : btn == 'R' ? 1 : 2;
            g_fake_mbtn[idx] = 0x80;
            // Mouse buttons in c_dfDIMouse2 are at dwOfs 12..19 (DIMOFS_BUTTON0+i)
            DIDEVICEOBJECTDATA e = {0};
            e.dwOfs = 12 + idx; e.dwData = 0x80;
            e.dwTimeStamp = GetTickCount(); e.dwSequence = ++g_di_sequence;
            g_di_queue.push_back(e);
            // Mark this button as held in the wParam state used by future
            // WM_MOUSEMOVE events (so drag operations send the right mask).
            WPARAM mask = (idx == 0) ? MK_LBUTTON
                        : (idx == 1) ? MK_RBUTTON : MK_MBUTTON;
            g_mouse_btn_state |= mask;
            // Post the WM_*BUTTONDOWN message with CLIENT coords.
            if (!g_target_window) g_target_window = FindOurMainWindow();
            if (g_target_window) {
                UINT m = (idx == 0) ? WM_LBUTTONDOWN
                       : (idx == 1) ? WM_RBUTTONDOWN : WM_MBUTTONDOWN;
                POINT pt = { g_fake_mouse_x, g_fake_mouse_y };
                ScreenToClient(g_target_window, &pt);
                LPARAM lp = ((LONG)(pt.y & 0xFFFF) << 16) | (LONG)(pt.x & 0xFFFF);
                PostMessageW(g_target_window, m, g_mouse_btn_state, lp);
            }
            LogF("CMD MD btn=%c at screen=%d,%d", btn, g_fake_mouse_x, g_fake_mouse_y);
        }
    } else if (!strcmp(op, "MU")) {
        if (sscanf_s(line.c_str(), "MU %c", &btn, 1) == 1) {
            int idx = btn == 'L' ? 0 : btn == 'R' ? 1 : 2;
            g_fake_mbtn[idx] = 0;
            DIDEVICEOBJECTDATA e = {0};
            e.dwOfs = 12 + idx; e.dwData = 0;
            e.dwTimeStamp = GetTickCount(); e.dwSequence = ++g_di_sequence;
            g_di_queue.push_back(e);
            // Release the button bit from the wParam state.
            WPARAM mask = (idx == 0) ? MK_LBUTTON
                        : (idx == 1) ? MK_RBUTTON : MK_MBUTTON;
            g_mouse_btn_state &= ~mask;
            if (!g_target_window) g_target_window = FindOurMainWindow();
            if (g_target_window) {
                UINT m = (idx == 0) ? WM_LBUTTONUP
                       : (idx == 1) ? WM_RBUTTONUP : WM_MBUTTONUP;
                POINT pt = { g_fake_mouse_x, g_fake_mouse_y };
                ScreenToClient(g_target_window, &pt);
                LPARAM lp = ((LONG)(pt.y & 0xFFFF) << 16) | (LONG)(pt.x & 0xFFFF);
                PostMessageW(g_target_window, m, g_mouse_btn_state, lp);
            }
            LogF("CMD MU btn=%c at screen=%d,%d", btn, g_fake_mouse_x, g_fake_mouse_y);
        }
    } else if (!strcmp(op, "MW")) {
        if (sscanf_s(line.c_str(), "MW %d %d", &a, &b) == 2) {
            g_fake_mouse_wheel += b * 120;
        }
    } else if (!strcmp(op, "RESET")) {
        memset(g_fake_keys, 0, sizeof(g_fake_keys));
        memset(g_fake_di_keys, 0, sizeof(g_fake_di_keys));
        memset(g_fake_mbtn, 0, sizeof(g_fake_mbtn));
        g_mouse_btn_state = 0;
        g_di_queue.clear();
        // Stop suppressing real hardware input — macro is done.
        g_macro_active = false;
        LogF("RESET — real input passthrough restored");
    }
}

static DWORD WINAPI PipeThread(LPVOID) {
    char pipeName[64];
    sprintf_s(pipeName, sizeof(pipeName),
              "\\\\.\\pipe\\macro_hook_%lu", GetCurrentProcessId());

    // CREATE THE PIPE INSTANCE ONCE — we reuse the same handle across many
    // client connections so the pipe is *always* visible to clients (no
    // race window between disconnect and recreate, which used to cause
    // ENOENT on the host side).
    HANDLE h = CreateNamedPipeA(
        pipeName,
        PIPE_ACCESS_DUPLEX,
        PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT,
        1, 4096, 4096, 0, NULL);
    if (h == INVALID_HANDLE_VALUE) return 1;
    g_pipe_handle = h;

    while (g_running) {
        BOOL ok = ConnectNamedPipe(h, NULL);
        if (!g_running) break;     // DllMain closed the pipe — bail.
        if (!ok && GetLastError() != ERROR_PIPE_CONNECTED) {
            Sleep(50);
            continue;
        }
        // Record the client's PID so the watchdog can detect if it dies
        // without disconnecting cleanly (crash / TaskMgr kill).
        ULONG clientPid = 0;
        if (GetNamedPipeClientProcessId(h, &clientPid)) {
            g_last_client_pid = (DWORD)clientPid;
            g_client_died_at  = 0;          // reset death-clock on fresh connect
            LogF("client connected pid=%lu", g_last_client_pid);
        }
        // Client is connected — read commands until they disconnect.
        char buf[256]; DWORD got = 0;
        std::string line;
        while (g_running && ReadFile(h, buf, sizeof(buf), &got, NULL) && got) {
            for (DWORD i = 0; i < got; ++i) {
                if (buf[i] == '\n') { HandleLine(line); line.clear(); }
                else if (buf[i] != '\r') line += buf[i];
            }
        }
        if (!g_running) break;
        DisconnectNamedPipe(h);
    }
    // Note: DllMain may have already closed h via g_pipe_handle, in which
    // case CloseHandle below is a no-op on an invalid handle (safe).
    if (g_pipe_handle != INVALID_HANDLE_VALUE) {
        CloseHandle(h);
        g_pipe_handle = INVALID_HANDLE_VALUE;
    }
    return 0;
}

// ─── Watchdog: self-unload if the macro recorder dies ──────────────────
// Polls g_last_client_pid every second.  Once the process matching that
// PID has exited and stayed exited for >=5 s, we call
// FreeLibraryAndExitThread on ourselves — DLL_PROCESS_DETACH runs, all
// Detours are reverted, and the DLL leaves the game's address space.
// Game input APIs go back to vanilla; no game restart needed.
static DWORD WINAPI WatchdogThread(LPVOID) {
    const DWORD GRACE_MS = 5000;
    while (g_running) {
        Sleep(1000);
        DWORD pid = g_last_client_pid;
        if (!pid) continue;                           // no client ever connected
        HANDLE hp = OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION,
                                FALSE, pid);
        bool alive = false;
        if (hp) {
            DWORD ec = STILL_ACTIVE;
            if (GetExitCodeProcess(hp, &ec) && ec == STILL_ACTIVE)
                alive = (WaitForSingleObject(hp, 0) == WAIT_TIMEOUT);
            CloseHandle(hp);
        }
        if (alive) { g_client_died_at = 0; continue; }
        // Client process not running.
        DWORD now = GetTickCount();
        if (g_client_died_at == 0) {
            g_client_died_at = now;
            LogF("watchdog: client pid=%lu gone, grace=%lums", pid, (unsigned long)GRACE_MS);
            continue;
        }
        if (now - g_client_died_at < GRACE_MS) continue;
        LogF("watchdog: self-unloading (client pid=%lu dead for %lums)",
             pid, (unsigned long)(now - g_client_died_at));
        // Wake the pipe thread out of ConnectNamedPipe so it can exit
        // before DLL_PROCESS_DETACH waits on it.
        g_running = false;
        HANDLE p = (HANDLE)InterlockedExchangePointer(
            (PVOID*)&g_pipe_handle, (PVOID)INVALID_HANDLE_VALUE);
        if (p != INVALID_HANDLE_VALUE) CloseHandle(p);
        // FreeLibraryAndExitThread triggers DLL_PROCESS_DETACH → the cleanup
        // path below (DetourDetach + thread join) runs → DLL is unloaded
        // from the game process.  This thread does not return.
        FreeLibraryAndExitThread(g_self_module, 0);
    }
    return 0;
}

// ─── DLL entry ──────────────────────────────────────────────────────────
BOOL APIENTRY DllMain(HMODULE hMod, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        g_self_module = hMod;
        DisableThreadLibraryCalls(hMod);

        OpenLog();

        // Hook Win32 polling APIs + focus-spoofing + cursor-position
        DetourTransactionBegin();
        DetourUpdateThread(GetCurrentThread());
        DetourAttach(&(PVOID&)Real_GetAsyncKeyState,    (PVOID)Hooked_GetAsyncKeyState);
        DetourAttach(&(PVOID&)Real_GetKeyState,         (PVOID)Hooked_GetKeyState);
        DetourAttach(&(PVOID&)Real_GetKeyboardState,    (PVOID)Hooked_GetKeyboardState);
        DetourAttach(&(PVOID&)Real_GetForegroundWindow, (PVOID)Hooked_GetForegroundWindow);
        DetourAttach(&(PVOID&)Real_GetActiveWindow,     (PVOID)Hooked_GetActiveWindow);
        DetourAttach(&(PVOID&)Real_GetFocus,            (PVOID)Hooked_GetFocus);
        DetourAttach(&(PVOID&)Real_GetCursorPos,        (PVOID)Hooked_GetCursorPos);
        LONG err = DetourTransactionCommit();
        LogF("Win32 DetourAttach commit = %ld (0 = ok)", err);

        // Hook DirectInput8 GetDeviceState + GetDeviceData
        InstallDInputHook();

        // Start the named-pipe command server.  Keep the thread handle so
        // we can wait for it to exit on detach.
        g_pipe_thread = CreateThread(NULL, 0, PipeThread, NULL, 0, NULL);
        // Start the watchdog (self-unload when the macro recorder dies).
        g_watchdog_thread = CreateThread(NULL, 0, WatchdogThread, NULL, 0, NULL);
    } else if (reason == DLL_PROCESS_DETACH) {
        // ── Clean shutdown to avoid crashes on FreeLibrary ──
        // 1. Tell the pipe thread to stop.
        g_running = false;
        g_macro_active = false;

        // 2. Close the pipe to wake the thread out of ConnectNamedPipe / ReadFile.
        HANDLE p = (HANDLE)InterlockedExchangePointer(
            (PVOID*)&g_pipe_handle, (PVOID)INVALID_HANDLE_VALUE);
        if (p != INVALID_HANDLE_VALUE) { CloseHandle(p); }

        // 3. Wait briefly for the pipe thread to exit.  Bounded so we never
        // hang the host process on unload — if the thread is stuck, we
        // accept the small leak rather than deadlocking.
        if (g_pipe_thread) {
            WaitForSingleObject(g_pipe_thread, 500);
            CloseHandle(g_pipe_thread);
            g_pipe_thread = NULL;
        }
        // 3b. If the watchdog itself triggered this detach, we ARE the
        // watchdog thread (FreeLibraryAndExitThread re-entered DllMain).
        // Don't try to wait on ourselves.  Otherwise, signal + join.
        if (g_watchdog_thread &&
            GetThreadId(g_watchdog_thread) != GetCurrentThreadId())
        {
            WaitForSingleObject(g_watchdog_thread, 200);
            CloseHandle(g_watchdog_thread);
            g_watchdog_thread = NULL;
        }

        // 4. Restore the original function bytes (un-hook everything).
        DetourTransactionBegin();
        DetourUpdateThread(GetCurrentThread());
        DetourDetach(&(PVOID&)Real_GetAsyncKeyState,    (PVOID)Hooked_GetAsyncKeyState);
        DetourDetach(&(PVOID&)Real_GetKeyState,         (PVOID)Hooked_GetKeyState);
        DetourDetach(&(PVOID&)Real_GetKeyboardState,    (PVOID)Hooked_GetKeyboardState);
        DetourDetach(&(PVOID&)Real_GetForegroundWindow, (PVOID)Hooked_GetForegroundWindow);
        DetourDetach(&(PVOID&)Real_GetActiveWindow,     (PVOID)Hooked_GetActiveWindow);
        DetourDetach(&(PVOID&)Real_GetFocus,            (PVOID)Hooked_GetFocus);
        DetourDetach(&(PVOID&)Real_GetCursorPos,        (PVOID)Hooked_GetCursorPos);
        if (Real_GetDeviceState)
            DetourDetach(&(PVOID&)Real_GetDeviceState, (PVOID)Hooked_GetDeviceState);
        if (Real_GetDeviceData)
            DetourDetach(&(PVOID&)Real_GetDeviceData,  (PVOID)Hooked_GetDeviceData);
        if (Real_SetDataFormat)
            DetourDetach(&(PVOID&)Real_SetDataFormat,  (PVOID)Hooked_SetDataFormat);
        DetourTransactionCommit();

        // 5. Give any in-flight hook calls a moment to return through the
        // now-restored trampolines before the OS unmaps us.
        Sleep(100);

        if (g_log != INVALID_HANDLE_VALUE) {
            CloseHandle(g_log);
            g_log = INVALID_HANDLE_VALUE;
        }
    }
    return TRUE;
}
