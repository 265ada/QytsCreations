using System.Runtime.InteropServices;
using System.Text;

namespace QytCroRec.Services;

/// <summary>P/Invoke declarations for all Win32 APIs used throughout the app.</summary>
public static class Win32
{
    // ── Window enumeration / title ─────────────────────────────────────────
    public delegate bool EnumWindowsProc(IntPtr hwnd, IntPtr lParam);

    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hwnd);
    [DllImport("user32.dll")] public static extern int  GetWindowTextLength(IntPtr hwnd);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    public static extern int GetWindowText(IntPtr hwnd, StringBuilder lpString, int nMaxCount);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hwnd);
    [DllImport("user32.dll")] public static extern IntPtr GetAncestor(IntPtr hwnd, uint gaFlags);
    [DllImport("user32.dll")] public static extern IntPtr GetParent(IntPtr hwnd);

    // ── PID from HWND ──────────────────────────────────────────────────────
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint lpdwProcessId);

    // ── SendInput ──────────────────────────────────────────────────────────
    [DllImport("user32.dll", SetLastError = true)]
    public static extern uint SendInput(uint nInputs, INPUT[] pInputs, int cbSize);

    [DllImport("user32.dll")] public static extern short VkKeyScan(char ch);
    [DllImport("user32.dll")] public static extern uint MapVirtualKey(uint uCode, uint uMapType);

    // ── PostMessage / SendMessage ──────────────────────────────────────────
    [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr hwnd, uint msg, IntPtr wParam, IntPtr lParam);
    [DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr hwnd, uint msg, IntPtr wParam, IntPtr lParam);

    // ── Client / screen coords ────────────────────────────────────────────
    [DllImport("user32.dll")] public static extern bool ClientToScreen(IntPtr hwnd, ref POINT lpPoint);
    [DllImport("user32.dll")] public static extern bool ScreenToClient(IntPtr hwnd, ref POINT lpPoint);
    [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr hwnd, out RECT lpRect);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hwnd, out RECT lpRect);

    // ── Keyboard hook ─────────────────────────────────────────────────────
    [DllImport("user32.dll", SetLastError = true)]
    public static extern IntPtr SetWindowsHookEx(int idHook, HookProc lpfn, IntPtr hMod, uint dwThreadId);
    [DllImport("user32.dll", SetLastError = true)]
    public static extern bool UnhookWindowsHookEx(IntPtr hhk);
    [DllImport("user32.dll")] public static extern IntPtr CallNextHookEx(IntPtr hhk, int nCode, IntPtr wParam, IntPtr lParam);
    [DllImport("kernel32.dll", CharSet = CharSet.Auto)]
    public static extern IntPtr GetModuleHandle(string? lpModuleName);

    public delegate IntPtr HookProc(int nCode, IntPtr wParam, IntPtr lParam);

    // ── Beep ──────────────────────────────────────────────────────────────
    [DllImport("kernel32.dll")] public static extern bool Beep(uint dwFreq, uint dwDuration);

    // Constants
    public const int WH_KEYBOARD_LL = 13;
    public const int WH_MOUSE_LL    = 14;
    public const int WM_KEYDOWN   = 0x0100;
    public const int WM_KEYUP     = 0x0101;
    public const int WM_SYSKEYDOWN = 0x0104;
    public const int WM_SYSKEYUP   = 0x0105;
    public const int WM_LBUTTONDOWN = 0x0201; public const int WM_LBUTTONUP = 0x0202;
    public const int WM_RBUTTONDOWN = 0x0204; public const int WM_RBUTTONUP = 0x0205;
    public const int WM_MBUTTONDOWN = 0x0207; public const int WM_MBUTTONUP = 0x0208;
    public const int WM_MOUSEMOVE   = 0x0200;
    public const int WM_MOUSEWHEEL  = 0x020A;
    public const uint GA_ROOT = 2;

    // ── Structures ────────────────────────────────────────────────────────
    [StructLayout(LayoutKind.Sequential)]
    public struct POINT { public int X; public int Y; }

    [StructLayout(LayoutKind.Sequential)]
    public struct RECT  { public int Left, Top, Right, Bottom; }

    [StructLayout(LayoutKind.Sequential)]
    public struct KBDLLHOOKSTRUCT
    {
        public uint vkCode, scanCode, flags, time;
        public IntPtr dwExtraInfo;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct MSLLHOOKSTRUCT
    {
        public POINT pt;
        public uint mouseData, flags, time;
        public IntPtr dwExtraInfo;
    }

    // SendInput structures
    public const int INPUT_MOUSE    = 0;
    public const int INPUT_KEYBOARD = 1;
    public const uint KEYEVENTF_KEYUP       = 0x0002;
    public const uint KEYEVENTF_SCANCODE    = 0x0008;
    public const uint KEYEVENTF_EXTENDEDKEY = 0x0001;
    public const uint MOUSEEVENTF_MOVE        = 0x0001;
    public const uint MOUSEEVENTF_LEFTDOWN    = 0x0002;
    public const uint MOUSEEVENTF_LEFTUP      = 0x0004;
    public const uint MOUSEEVENTF_RIGHTDOWN   = 0x0008;
    public const uint MOUSEEVENTF_RIGHTUP     = 0x0010;
    public const uint MOUSEEVENTF_MIDDLEDOWN  = 0x0020;
    public const uint MOUSEEVENTF_MIDDLEUP    = 0x0040;
    public const uint MOUSEEVENTF_WHEEL       = 0x0800;
    public const uint MOUSEEVENTF_ABSOLUTE    = 0x8000;

    [StructLayout(LayoutKind.Sequential)]
    public struct INPUT
    {
        public int type;
        public InputUnion U;
    }

    [StructLayout(LayoutKind.Explicit)]
    public struct InputUnion
    {
        [FieldOffset(0)] public MOUSEINPUT mi;
        [FieldOffset(0)] public KEYBDINPUT ki;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct MOUSEINPUT
    {
        public int dx, dy;
        public uint mouseData, dwFlags, time;
        public IntPtr dwExtraInfo;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct KEYBDINPUT
    {
        public ushort wVk, wScan;
        public uint dwFlags, time;
        public IntPtr dwExtraInfo;
    }

    // ── Helpers ───────────────────────────────────────────────────────────

    public static uint GetWindowPid(IntPtr hwnd)
    {
        GetWindowThreadProcessId(hwnd, out uint pid);
        return pid;
    }

    public static string GetWindowTitle(IntPtr hwnd)
    {
        int len = GetWindowTextLength(hwnd);
        if (len == 0) return "";
        var sb = new StringBuilder(len + 1);
        GetWindowText(hwnd, sb, sb.Capacity);
        return sb.ToString();
    }

    /// <summary>Return all visible top-level windows matching title substring,
    /// sorted by (PID asc, HWND asc) for stable ordering.</summary>
    public static List<(IntPtr hwnd, uint pid, string title)> FindWindowsByTitle(string titleSubstring)
    {
        var results = new List<(IntPtr hwnd, uint pid, string title)>();
        string lower = titleSubstring.ToLowerInvariant();
        EnumWindows((hwnd, _) =>
        {
            if (!IsWindowVisible(hwnd)) return true;
            string t = GetWindowTitle(hwnd);
            if (t.Length > 0 && t.ToLowerInvariant().Contains(lower))
            {
                uint pid = GetWindowPid(hwnd);
                results.Add((hwnd, pid, t));
            }
            return true;
        }, IntPtr.Zero);
        results.Sort((a, b) => a.pid != b.pid
            ? a.pid.CompareTo(b.pid)
            : a.hwnd.ToInt64().CompareTo(b.hwnd.ToInt64()));
        return results;
    }

    /// <summary>Find the Nth matching window (0-indexed), skipping skipHwnds.</summary>
    public static IntPtr ResolveWindowHwnd(string titleSubstring, int instance, HashSet<IntPtr>? skipHwnds = null)
    {
        if (string.IsNullOrEmpty(titleSubstring)) return IntPtr.Zero;
        var all = FindWindowsByTitle(titleSubstring).Select(x => x.hwnd).ToList();
        var avail = skipHwnds is null ? all : all.Where(h => !skipHwnds.Contains(h)).ToList();
        if (avail.Count == 0) return IntPtr.Zero;
        // Try exact instance from full list
        if (instance >= 0 && instance < all.Count && avail.Contains(all[instance]))
            return all[instance];
        // Fall back to instance-th of available
        if (instance >= 0 && instance < avail.Count) return avail[instance];
        return avail[0];
    }
}
