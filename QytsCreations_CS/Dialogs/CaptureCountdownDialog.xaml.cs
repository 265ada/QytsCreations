using System.Windows;
using System.Windows.Threading;
using QytCroRec.Services;

namespace QytCroRec.Dialogs;

public partial class CaptureCountdownDialog : Window
{
    public IntPtr  CapturedHwnd  { get; private set; }
    public string  CapturedTitle { get; private set; } = "";

    private int _remaining = 3;
    private DispatcherTimer? _timer;

    public CaptureCountdownDialog()
    {
        InitializeComponent();
        Loaded += (_, _) => Start();
    }

    private void Start()
    {
        _timer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(1) };
        _timer.Tick += (_, _) =>
        {
            _remaining--;
            if (_remaining <= 0)
            {
                _timer!.Stop();
                CaptureForeground();
                DialogResult = true;
                Close();
            }
            else
            {
                CountdownText.Text = _remaining.ToString();
            }
        };
        _timer.Start();
    }

    private void CaptureForeground()
    {
        var hwnd = Win32.GetForegroundWindow();
        if (hwnd != IntPtr.Zero)
        {
            CapturedHwnd  = hwnd;
            CapturedTitle = Win32.GetWindowTitle(hwnd);
        }
    }
}
