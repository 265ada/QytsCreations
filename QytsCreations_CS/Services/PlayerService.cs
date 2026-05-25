using QytCroRec.Models;

namespace QytCroRec.Services;

/// <summary>
/// Plays back a single lane's events with backend-aware routing:
///   • "detours"      → DetoursService named-pipe (DirectInput-safe, background)
///   • "postmessage"  → PostMessage to target HWND (background, no real mouse)
///   • "sendinput"    → SendInput (system-wide — hijacks real mouse/keyboard)
///   • "auto"         → detours if pipe up, else postmessage if hwnd, else sendinput
/// </summary>
public class PlayerService
{
    public event Action<int, int>? Progress;
    public event Action?           Completed;
    public event Action<string>?   StatusEvent;

    private CancellationTokenSource? _cts;
    private Task?                    _task;
    private DetoursService?          _detours;
    private string                   _resolvedBackend = "sendinput";
    private IntPtr                   _hwnd;

    public bool IsPlaying => _task is { IsCompleted: false };

    public void Play(Macro macro, IntPtr hwnd = default)
    {
        Stop();
        _hwnd = hwnd;
        _resolvedBackend = ResolveBackend(macro, hwnd);
        StatusEvent?.Invoke($"backend={_resolvedBackend}  hwnd=0x{hwnd:X8}");

        _cts = new CancellationTokenSource();
        var token = _cts.Token;
        _task = Task.Run(() => PlayLoop(macro, hwnd, token), token);
    }

    public void Stop()
    {
        _cts?.Cancel();
        try { _task?.Wait(500); } catch { }
        try { _detours?.Dispose(); } catch { }
        _detours = null;
        _cts = null; _task = null;
    }

    /// <summary>Pixel-guard correction: single key tap. Routes through current backend if active,
    /// else PostMessage when hwnd, else nothing (refuses to hijack real input).</summary>
    public void SendKeyTap(string keyChar)
    {
        if (string.IsNullOrEmpty(keyChar)) return;
        int vk = CharToVk(keyChar);
        if (vk == 0) return;
        FireKey(vk, down: true);
        Thread.Sleep(40);
        FireKey(vk, down: false);
    }

    /// <summary>Inline play of a guard correction macro using the active backend.</summary>
    public void PlayInline(Macro macro, IntPtr hwnd)
    {
        double prevTs = 0;
        foreach (var ev in macro.Events)
        {
            double delay = (ev.Timestamp - prevTs) / Math.Max(0.01, macro.SpeedMultiplier);
            if (delay > 0.001) Thread.Sleep(Math.Min((int)(delay * 1000), 30_000));
            prevTs = ev.Timestamp;
            FireEvent(ev);
        }
    }

    // ═══════════════════════════════════════════════════════════════════════
    //  BACKEND RESOLUTION
    // ═══════════════════════════════════════════════════════════════════════
    private string ResolveBackend(Macro macro, IntPtr hwnd)
    {
        string choice = (macro.InputBackend ?? "auto").ToLowerInvariant();

        if (choice == "detours")
        {
            int pid = HwndToPid(hwnd);
            if (pid > 0)
            {
                _detours = new DetoursService(pid);
                _detours.StatusEvent += t => StatusEvent?.Invoke($"[detours] {t}");
                if (!DetoursService.PipeExists(pid))
                {
                    var (ok, msg) = _detours.Inject();
                    StatusEvent?.Invoke($"[detours] inject: {msg}");
                    if (!ok) { _detours.Dispose(); _detours = null; return FallbackBackground(hwnd); }
                }
                if (_detours.Open()) return "detours";
                _detours = null;
            }
            // detours selected but unusable — fall back to background, NEVER sendinput
            return FallbackBackground(hwnd);
        }

        if (choice == "postmessage") return hwnd != IntPtr.Zero ? "postmessage" : "sendinput-explicit-fail";
        if (choice == "sendinput")   return "sendinput";

        // auto: detours if pipe up → postmsg if hwnd → sendinput (last resort)
        int autoPid = HwndToPid(hwnd);
        if (autoPid > 0 && DetoursService.PipeExists(autoPid))
        {
            _detours = new DetoursService(autoPid);
            if (_detours.Open()) return "detours";
            _detours = null;
        }
        if (hwnd != IntPtr.Zero) return "postmessage";
        return "sendinput";
    }

    private string FallbackBackground(IntPtr hwnd)
    {
        // When user picked a background backend (detours) but it can't be used,
        // prefer PostMessage if we at least have hwnd. Refuse SendInput to avoid
        // hijacking the real mouse — user can switch backend manually.
        if (hwnd != IntPtr.Zero) return "postmessage";
        return "sendinput-refused";
    }

    private static int HwndToPid(IntPtr hwnd)
    {
        if (hwnd == IntPtr.Zero) return 0;
        Win32.GetWindowThreadProcessId(hwnd, out uint pid);
        return (int)pid;
    }

    // ═══════════════════════════════════════════════════════════════════════
    //  PLAY LOOP
    // ═══════════════════════════════════════════════════════════════════════
    private void PlayLoop(Macro macro, IntPtr hwnd, CancellationToken ct)
    {
        int reps = macro.RepeatCount == 0 ? int.MaxValue : macro.RepeatCount;
        double speed = Math.Max(0.01, macro.SpeedMultiplier);

        for (int rep = 0; rep < reps && !ct.IsCancellationRequested; rep++)
        {
            var events = macro.Events;
            double prevTs = 0;
            for (int i = 0; i < events.Count && !ct.IsCancellationRequested; i++)
            {
                var ev = events[i];
                double delay = (ev.Timestamp - prevTs) / speed;
                if (delay > 0.001)
                {
                    int ms = (int)(delay * 1000);
                    if (ms > 0) Thread.Sleep(Math.Min(ms, 30_000));
                }
                prevTs = ev.Timestamp;
                FireEvent(ev);
                Progress?.Invoke(i + 1, events.Count);
            }
        }
        Completed?.Invoke();
    }

    private void FireEvent(MacroEvent ev)
    {
        try
        {
            switch (ev.EventType)
            {
                case "key_down":
                {
                    if (TryGetInt(ev, "vk", out int vk)) FireKey(vk, down: true);
                    break;
                }
                case "key_up":
                {
                    if (TryGetInt(ev, "vk", out int vk)) FireKey(vk, down: false);
                    break;
                }
                case "mouse_move":   FireMouse(ev, "move"); break;
                case "mouse_down":   FireMouse(ev, "down"); break;
                case "mouse_up":     FireMouse(ev, "up");   break;
                case "mouse_scroll": FireScroll(ev); break;
                case "delay": break;
            }
        }
        catch (Exception ex) { StatusEvent?.Invoke($"[play] event err: {ex.Message}"); }
    }

    private void FireKey(int vk, bool down)
    {
        switch (_resolvedBackend)
        {
            case "detours":
                if (down) _detours?.KeyDown(vk); else _detours?.KeyUp(vk);
                break;

            case "postmessage":
                if (_hwnd == IntPtr.Zero) return;
                uint msg = down ? (uint)Win32.WM_KEYDOWN : (uint)Win32.WM_KEYUP;
                uint scan = Win32.MapVirtualKey((uint)vk, 0);
                uint lpVal = 1u | (scan << 16) | (down ? 0u : (0x80000000u | 0x40000000u));
                Win32.PostMessage(_hwnd, msg, (IntPtr)vk, (IntPtr)(int)lpVal);
                break;

            case "sendinput":
                uint scanSI = Win32.MapVirtualKey((uint)vk, 0);
                var input = new Win32.INPUT
                {
                    type = Win32.INPUT_KEYBOARD,
                    U = new Win32.InputUnion
                    {
                        ki = new Win32.KEYBDINPUT
                        {
                            wVk = (ushort)vk,
                            wScan = (ushort)scanSI,
                            dwFlags = down ? 0 : Win32.KEYEVENTF_KEYUP
                        }
                    }
                };
                Win32.SendInput(1, [input], MarshalSize<Win32.INPUT>());
                break;

            // refused/explicit-fail: log once, skip
            default:
                StatusEvent?.Invoke($"[play] key skipped (backend={_resolvedBackend})");
                break;
        }
    }

    private void FireMouse(MacroEvent ev, string action)
    {
        if (!TryGetInt(ev, "x", out int x) || !TryGetInt(ev, "y", out int y)) return;

        // Convert client-% coords to screen coords once if needed.
        if (ev.Data.TryGetValue("coord_space", out var cs) && cs?.ToString() == "client" && _hwnd != IntPtr.Zero)
        {
            Win32.GetClientRect(_hwnd, out var cr);
            int cx = (int)(x * cr.Right / 10000.0);
            int cy = (int)(y * cr.Bottom / 10000.0);
            var pt = new Win32.POINT { X = cx, Y = cy };
            Win32.ClientToScreen(_hwnd, ref pt);
            x = pt.X; y = pt.Y;
        }

        string btn = "left";
        if (ev.Data.TryGetValue("button", out var btnRaw)) btn = btnRaw?.ToString() ?? "left";

        switch (_resolvedBackend)
        {
            case "detours":
                if (action == "move") _detours?.MouseMove(x, y);
                else                  _detours?.MouseButton(x, y, btn, pressed: action == "down");
                break;

            case "postmessage":
                if (_hwnd == IntPtr.Zero) return;
                if (action == "move")
                {
                    Win32.PostMessage(_hwnd, (uint)Win32.WM_MOUSEMOVE, IntPtr.Zero, MakeLParam(x, y));
                }
                else
                {
                    uint downMsg = btn switch { "right" => (uint)Win32.WM_RBUTTONDOWN, "middle" => (uint)Win32.WM_MBUTTONDOWN, _ => (uint)Win32.WM_LBUTTONDOWN };
                    uint upMsg   = btn switch { "right" => (uint)Win32.WM_RBUTTONUP,   "middle" => (uint)Win32.WM_MBUTTONUP,   _ => (uint)Win32.WM_LBUTTONUP   };
                    uint msg     = action == "down" ? downMsg : upMsg;
                    Win32.PostMessage(_hwnd, msg, IntPtr.Zero, MakeLParam(x, y));
                }
                break;

            case "sendinput":
                uint flags = action == "down"
                    ? (btn switch {
                        "right"  => Win32.MOUSEEVENTF_RIGHTDOWN,
                        "middle" => Win32.MOUSEEVENTF_MIDDLEDOWN,
                        _        => Win32.MOUSEEVENTF_LEFTDOWN })
                    : action == "up"
                        ? (btn switch {
                            "right"  => Win32.MOUSEEVENTF_RIGHTUP,
                            "middle" => Win32.MOUSEEVENTF_MIDDLEUP,
                            _        => Win32.MOUSEEVENTF_LEFTUP })
                        : Win32.MOUSEEVENTF_MOVE;
                int sx = (int)((x + 0.5) * 65535 / System.Windows.SystemParameters.PrimaryScreenWidth);
                int sy = (int)((y + 0.5) * 65535 / System.Windows.SystemParameters.PrimaryScreenHeight);
                var input = new Win32.INPUT
                {
                    type = Win32.INPUT_MOUSE,
                    U = new Win32.InputUnion
                    {
                        mi = new Win32.MOUSEINPUT { dx = sx, dy = sy, dwFlags = flags | Win32.MOUSEEVENTF_ABSOLUTE }
                    }
                };
                Win32.SendInput(1, [input], MarshalSize<Win32.INPUT>());
                break;

            default:
                StatusEvent?.Invoke($"[play] mouse skipped (backend={_resolvedBackend})");
                break;
        }
    }

    private void FireScroll(MacroEvent ev)
    {
        if (!TryGetInt(ev, "dy", out int dy)) return;
        switch (_resolvedBackend)
        {
            case "detours":
                _detours?.MouseWheel(0, dy);
                break;
            case "postmessage":
                if (_hwnd == IntPtr.Zero) return;
                uint wheelData = (uint)(dy * 120);
                Win32.PostMessage(_hwnd, (uint)Win32.WM_MOUSEWHEEL,
                    (IntPtr)((int)wheelData << 16), IntPtr.Zero);
                break;
            case "sendinput":
                var input = new Win32.INPUT
                {
                    type = Win32.INPUT_MOUSE,
                    U = new Win32.InputUnion
                    {
                        mi = new Win32.MOUSEINPUT { mouseData = (uint)(dy * 120), dwFlags = Win32.MOUSEEVENTF_WHEEL }
                    }
                };
                Win32.SendInput(1, [input], MarshalSize<Win32.INPUT>());
                break;
        }
    }

    // ── helpers ────────────────────────────────────────────────────────────
    private static bool TryGetInt(MacroEvent ev, string key, out int val)
    {
        val = 0;
        if (!ev.Data.TryGetValue(key, out var raw) || raw == null) return false;
        try { val = Convert.ToInt32(raw); return true; } catch { return false; }
    }
    private static int CharToVk(string s)
    {
        if (string.IsNullOrEmpty(s)) return 0;
        char c = char.ToUpperInvariant(s[0]);
        if (c >= 'A' && c <= 'Z') return c;
        if (c >= '0' && c <= '9') return c;
        return 0;
    }
    private static IntPtr MakeLParam(int x, int y) => (IntPtr)((y << 16) | (x & 0xFFFF));
    private static int    MarshalSize<T>() where T : struct =>
        System.Runtime.InteropServices.Marshal.SizeOf<T>();
}
