using QytCroRec.Models;

namespace QytCroRec.Services;

/// <summary>Plays back a single lane's events with timing, speed, and window targeting.</summary>
public class PlayerService
{
    public event Action<int, int>? Progress;   // (current, total)
    public event Action? Completed;

    private CancellationTokenSource? _cts;
    private Task? _task;

    public bool IsPlaying => _task is { IsCompleted: false };

    public void Play(Macro macro, IntPtr hwnd = default)
    {
        Stop();
        _cts = new CancellationTokenSource();
        var token = _cts.Token;
        _task = Task.Run(() => PlayLoop(macro, hwnd, token), token);
    }

    public void Stop()
    {
        _cts?.Cancel();
        try { _task?.Wait(500); } catch { }
        _cts = null; _task = null;
    }

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
                FireEvent(ev, hwnd);
                Progress?.Invoke(i + 1, events.Count);
            }
        }
        Completed?.Invoke();
    }

    private static void FireEvent(MacroEvent ev, IntPtr hwnd)
    {
        try
        {
            switch (ev.EventType)
            {
                case "key_down": SendKey(ev, hwnd, down: true);  break;
                case "key_up":   SendKey(ev, hwnd, down: false); break;
                case "mouse_move":
                case "mouse_down":
                case "mouse_up":   SendMouse(ev, hwnd); break;
                case "mouse_scroll": SendScroll(ev, hwnd); break;
                case "delay": break;
            }
        }
        catch { /* swallow per-event errors */ }
    }

    private static void SendKey(MacroEvent ev, IntPtr hwnd, bool down)
    {
        if (!ev.Data.TryGetValue("vk", out var vkRaw)) return;
        int vk = Convert.ToInt32(vkRaw);

        if (hwnd != IntPtr.Zero)
        {
            uint msg = down ? (uint)Win32.WM_KEYDOWN : (uint)Win32.WM_KEYUP;
            uint scan = Win32.MapVirtualKey((uint)vk, 0);
            uint lpVal = 1u | (scan << 16) | (down ? 0u : (0x80000000u | 0x40000000u));
            IntPtr lp  = (IntPtr)(int)lpVal;
            Win32.PostMessage(hwnd, msg, (IntPtr)vk, lp);
        }
        else
        {
            uint scan = Win32.MapVirtualKey((uint)vk, 0);
            var input = new Win32.INPUT
            {
                type = Win32.INPUT_KEYBOARD,
                U = new Win32.InputUnion
                {
                    ki = new Win32.KEYBDINPUT
                    {
                        wVk = (ushort)vk,
                        wScan = (ushort)scan,
                        dwFlags = down ? 0 : Win32.KEYEVENTF_KEYUP
                    }
                }
            };
            Win32.SendInput(1, [input], Marshal.SizeOf<Win32.INPUT>());
        }
    }

    private static void SendMouse(MacroEvent ev, IntPtr hwnd)
    {
        ev.Data.TryGetValue("x", out var xRaw);
        ev.Data.TryGetValue("y", out var yRaw);
        int x = Convert.ToInt32(xRaw), y = Convert.ToInt32(yRaw);

        // Convert client % coords to screen coords if needed
        if (ev.Data.TryGetValue("coord_space", out var cs) && cs?.ToString() == "client" && hwnd != IntPtr.Zero)
        {
            Win32.GetClientRect(hwnd, out var cr);
            int cx = (int)(x * cr.Right / 10000.0);
            int cy = (int)(y * cr.Bottom / 10000.0);
            var pt = new Win32.POINT { X = cx, Y = cy };
            Win32.ClientToScreen(hwnd, ref pt);
            x = pt.X; y = pt.Y;
        }

        if (hwnd != IntPtr.Zero && ev.EventType != "mouse_move")
        {
            ev.Data.TryGetValue("button", out var btnRaw);
            string btn = btnRaw?.ToString() ?? "left";
            uint down = btn switch { "right" => (uint)Win32.WM_RBUTTONDOWN, "middle" => (uint)Win32.WM_MBUTTONDOWN, _ => (uint)Win32.WM_LBUTTONDOWN };
            uint up   = btn switch { "right" => (uint)Win32.WM_RBUTTONUP,   "middle" => (uint)Win32.WM_MBUTTONUP,   _ => (uint)Win32.WM_LBUTTONUP   };
            uint msg  = ev.EventType == "mouse_down" ? down : up;
            Win32.PostMessage(hwnd, msg, IntPtr.Zero, MakeLParam(x, y));
        }
        else
        {
            uint flags = ev.EventType == "mouse_down"
                ? Win32.MOUSEEVENTF_LEFTDOWN
                : ev.EventType == "mouse_up"
                    ? Win32.MOUSEEVENTF_LEFTUP
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
            Win32.SendInput(1, [input], Marshal.SizeOf<Win32.INPUT>());
        }
    }

    private static void SendScroll(MacroEvent ev, IntPtr hwnd)
    {
        ev.Data.TryGetValue("dy", out var dyRaw);
        int dy = Convert.ToInt32(dyRaw);
        uint wheelData = (uint)(dy * 120);
        if (hwnd != IntPtr.Zero)
            Win32.PostMessage(hwnd, (uint)Win32.WM_MOUSEWHEEL, (IntPtr)((int)wheelData << 16), IntPtr.Zero);
        else
        {
            var input = new Win32.INPUT
            {
                type = Win32.INPUT_MOUSE,
                U = new Win32.InputUnion
                {
                    mi = new Win32.MOUSEINPUT { mouseData = wheelData, dwFlags = Win32.MOUSEEVENTF_WHEEL }
                }
            };
            Win32.SendInput(1, [input], Marshal.SizeOf<Win32.INPUT>());
        }
    }

    private static IntPtr MakeLParam(int x, int y) => (IntPtr)((y << 16) | (x & 0xFFFF));
}

// Need this in the using block at top
file static class Marshal
{
    public static int SizeOf<T>() where T : struct => System.Runtime.InteropServices.Marshal.SizeOf<T>();
}
