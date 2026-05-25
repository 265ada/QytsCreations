using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Input;
using QytCroRec.Services;

namespace QytCroRec.Dialogs;

public partial class DragPickDialog : Window
{
    public IntPtr SelectedHwnd  { get; private set; }
    public string SelectedTitle { get; private set; } = "";

    [DllImport("user32.dll")] private static extern IntPtr WindowFromPoint(System.Drawing.Point p);
    [DllImport("user32.dll")] private static extern IntPtr GetAncestor(IntPtr hwnd, uint flags);
    [DllImport("user32.dll")] private static extern bool   GetCursorPos(out System.Drawing.Point lpPoint);
    private const uint GA_ROOT = 2;

    private bool _dragging;

    public DragPickDialog()
    {
        InitializeComponent();
    }

    private void BtnPick_Down(object sender, MouseButtonEventArgs e)
    {
        _dragging = true;
        BtnPick.CaptureMouse();
        Cursor = Cursors.Cross;
    }

    private void BtnPick_Move(object sender, MouseEventArgs e)
    {
        if (!_dragging) return;
        GetCursorPos(out var p);
        var hwnd = GetAncestor(WindowFromPoint(p), GA_ROOT);
        if (hwnd != IntPtr.Zero && hwnd != new System.Windows.Interop.WindowInteropHelper(this).Handle)
        {
            HoverInfo.Text = $"{Win32.GetWindowTitle(hwnd)}\nHWND 0x{hwnd:X8}";
        }
    }

    private void BtnPick_Up(object sender, MouseButtonEventArgs e)
    {
        _dragging = false;
        BtnPick.ReleaseMouseCapture();
        Cursor = Cursors.Arrow;

        GetCursorPos(out var p);
        var hwnd = GetAncestor(WindowFromPoint(p), GA_ROOT);
        var ownHwnd = new System.Windows.Interop.WindowInteropHelper(this).Handle;
        if (hwnd != IntPtr.Zero && hwnd != ownHwnd)
        {
            SelectedHwnd  = hwnd;
            SelectedTitle = Win32.GetWindowTitle(hwnd);
            DialogResult  = true;
            Close();
        }
    }
}
