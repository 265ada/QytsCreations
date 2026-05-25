using System.Runtime.InteropServices;
using QytCroRec.Models;

namespace QytCroRec.Services;

/// <summary>Records keyboard and mouse input into MacroEvent lists using low-level hooks.</summary>
public class RecorderService : IDisposable
{
    public event Action<MacroEvent>? EventCaptured;
    public event Action? RecordingEnded;

    private bool _recording;
    private double _startTime;
    private IntPtr _kbHook = IntPtr.Zero;
    private IntPtr _msHook = IntPtr.Zero;
    private Win32.HookProc? _kbProc, _msProc;
    private readonly bool _recordMouseMove;
    private readonly IntPtr _targetHwnd;
    private readonly HashSet<string> _filterKeys;

    public RecorderService(bool recordMouseMove = true,
                           IntPtr targetHwnd = default,
                           HashSet<string>? filterKeys = null)
    {
        _recordMouseMove = recordMouseMove;
        _targetHwnd = targetHwnd;
        _filterKeys = filterKeys ?? new();
    }

    public void Start()
    {
        if (_recording) return;
        _recording = true;
        _startTime = Stopwatch();

        using var proc = System.Diagnostics.Process.GetCurrentProcess();
        using var mod  = proc.MainModule!;
        var hMod = Win32.GetModuleHandle(mod.ModuleName);

        _kbProc = KbHook;
        _msProc = MsHook;
        _kbHook = Win32.SetWindowsHookEx(Win32.WH_KEYBOARD_LL, _kbProc, hMod, 0);
        _msHook = Win32.SetWindowsHookEx(Win32.WH_MOUSE_LL,    _msProc, hMod, 0);
    }

    public void Stop()
    {
        if (!_recording) return;
        _recording = false;
        if (_kbHook != IntPtr.Zero) { Win32.UnhookWindowsHookEx(_kbHook); _kbHook = IntPtr.Zero; }
        if (_msHook != IntPtr.Zero) { Win32.UnhookWindowsHookEx(_msHook); _msHook = IntPtr.Zero; }
        RecordingEnded?.Invoke();
    }

    private double Stopwatch() =>
        (double)System.Diagnostics.Stopwatch.GetTimestamp() /
        System.Diagnostics.Stopwatch.Frequency;

    private double Elapsed() => Stopwatch() - _startTime;

    private IntPtr KbHook(int nCode, IntPtr wParam, IntPtr lParam)
    {
        if (nCode >= 0 && _recording)
        {
            var ks = Marshal.PtrToStructure<Win32.KBDLLHOOKSTRUCT>(lParam);
            bool isDown = wParam == Win32.WM_KEYDOWN || wParam == Win32.WM_SYSKEYDOWN;
            string keyName = ((System.Windows.Forms.Keys)ks.vkCode).ToString().ToLower();

            if (_filterKeys.Contains(keyName))
                return Win32.CallNextHookEx(_kbHook, nCode, wParam, lParam);

            Emit(new MacroEvent
            {
                Timestamp = Elapsed(),
                EventType = isDown ? "key_down" : "key_up",
                Data = new() { ["key"] = keyName, ["vk"] = (int)ks.vkCode }
            });
        }
        return Win32.CallNextHookEx(_kbHook, nCode, wParam, lParam);
    }

    private IntPtr MsHook(int nCode, IntPtr wParam, IntPtr lParam)
    {
        if (nCode >= 0 && _recording)
        {
            var ms = Marshal.PtrToStructure<Win32.MSLLHOOKSTRUCT>(lParam);
            int msg = (int)wParam;
            string? evType = msg switch
            {
                Win32.WM_LBUTTONDOWN => "mouse_down",
                Win32.WM_LBUTTONUP   => "mouse_up",
                Win32.WM_RBUTTONDOWN => "mouse_down",
                Win32.WM_RBUTTONUP   => "mouse_up",
                Win32.WM_MBUTTONDOWN => "mouse_down",
                Win32.WM_MBUTTONUP   => "mouse_up",
                Win32.WM_MOUSEMOVE   => _recordMouseMove ? "mouse_move" : null,
                Win32.WM_MOUSEWHEEL  => "mouse_scroll",
                _ => null
            };
            if (evType == null)
            {
                return Win32.CallNextHookEx(_msHook, nCode, wParam, lParam);
            }

            string button = msg switch
            {
                Win32.WM_RBUTTONDOWN or Win32.WM_RBUTTONUP => "right",
                Win32.WM_MBUTTONDOWN or Win32.WM_MBUTTONUP => "middle",
                _ => "left"
            };

            int sx = ms.pt.X, sy = ms.pt.Y;
            int cx = sx, cy = sy;
            string coordSpace = "screen";

            if (_targetHwnd != IntPtr.Zero && evType != "mouse_scroll")
            {
                var pt = new Win32.POINT { X = sx, Y = sy };
                Win32.ScreenToClient(_targetHwnd, ref pt);
                Win32.GetClientRect(_targetHwnd, out var cr);
                if (cr.Right > 0 && cr.Bottom > 0)
                {
                    cx = (int)(pt.X * 10000.0 / cr.Right);
                    cy = (int)(pt.Y * 10000.0 / cr.Bottom);
                    coordSpace = "client";
                }
            }

            var data = new Dictionary<string, object?>
            {
                ["x"] = cx, ["y"] = cy, ["coord_space"] = coordSpace
            };
            if (evType == "mouse_down" || evType == "mouse_up")
                data["button"] = button;
            if (evType == "mouse_scroll")
                data["dy"] = (short)(ms.mouseData >> 16) / 120;

            Emit(new MacroEvent { Timestamp = Elapsed(), EventType = evType, Data = data });
        }
        return Win32.CallNextHookEx(_msHook, nCode, wParam, lParam);
    }

    private void Emit(MacroEvent ev) => EventCaptured?.Invoke(ev);

    public void Dispose() => Stop();
}
