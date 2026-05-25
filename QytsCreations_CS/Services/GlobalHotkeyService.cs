using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Interop;

namespace QytCroRec.Services;

/// <summary>Registers global hotkeys using RegisterHotKey / WM_HOTKEY.</summary>
public class GlobalHotkeyService : IDisposable
{
    [DllImport("user32.dll")] static extern bool RegisterHotKey(IntPtr hWnd, int id, uint fsModifiers, uint vk);
    [DllImport("user32.dll")] static extern bool UnregisterHotKey(IntPtr hWnd, int id);

    private const uint MOD_NONE  = 0x0000;
    private const uint MOD_ALT   = 0x0001;
    private const uint MOD_CTRL  = 0x0002;
    private const uint MOD_SHIFT = 0x0004;
    private const uint MOD_WIN   = 0x0008;
    private const uint MOD_NOREPEAT = 0x4000;

    private readonly Window _owner;
    private IntPtr _hwnd;
    private HwndSource? _source;
    private readonly Dictionary<int, Action> _actions = new();
    private int _nextId = 9000;

    public GlobalHotkeyService(Window owner)
    {
        _owner = owner;
        owner.SourceInitialized += (_, _) =>
        {
            _hwnd = new WindowInteropHelper(owner).Handle;
            _source = HwndSource.FromHwnd(_hwnd);
            _source?.AddHook(WndProc);
        };
    }

    /// <summary>Register a hotkey string like "F8", "Ctrl+F6", "End".</summary>
    public int Register(string keyString, Action action)
    {
        if (string.IsNullOrEmpty(keyString)) return -1;
        ParseKey(keyString, out uint mods, out uint vk);
        if (vk == 0) return -1;
        int id = _nextId++;
        if (RegisterHotKey(_hwnd, id, mods | MOD_NOREPEAT, vk))
            _actions[id] = action;
        return id;
    }

    public void Unregister(int id)
    {
        if (_actions.Remove(id))
            UnregisterHotKey(_hwnd, id);
    }

    public void UnregisterAll()
    {
        foreach (var id in _actions.Keys.ToList())
            UnregisterHotKey(_hwnd, id);
        _actions.Clear();
    }

    private IntPtr WndProc(IntPtr hwnd, int msg, IntPtr wParam, IntPtr lParam, ref bool handled)
    {
        if (msg == 0x0312 && _actions.TryGetValue((int)wParam, out var action))
        {
            action();
            handled = true;
        }
        return IntPtr.Zero;
    }

    private static void ParseKey(string s, out uint mods, out uint vk)
    {
        mods = MOD_NONE;
        vk   = 0;
        var parts = s.Split('+', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
        foreach (var p in parts)
        {
            switch (p.ToUpperInvariant())
            {
                case "CTRL": case "CONTROL": mods |= MOD_CTRL;  break;
                case "ALT":                  mods |= MOD_ALT;   break;
                case "SHIFT":                mods |= MOD_SHIFT; break;
                case "WIN": case "META":     mods |= MOD_WIN;   break;
                default:
                    if (Enum.TryParse<System.Windows.Forms.Keys>(p, true, out var k))
                        vk = (uint)k;
                    break;
            }
        }
    }

    public void Dispose()
    {
        UnregisterAll();
        _source?.RemoveHook(WndProc);
    }
}
