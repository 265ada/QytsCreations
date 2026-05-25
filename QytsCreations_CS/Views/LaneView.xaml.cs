using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using QytCroRec.Dialogs;
using QytCroRec.Models;
using QytCroRec.Services;

namespace QytCroRec.Views;

public partial class LaneView : UserControl
{
    public event Action? RecordClick;
    public event Action? PlayClick;
    public event Action? StopClick;
    public event Action? ChangedAndSave;
    public event Action<string>? Log;

    public int LaneIndex { get; private set; } = 0;
    private Macro? _macro;
    private MacroGroup? _group;
    private List<Macro> _guardMacros = new();
    private bool _suppress;

    public LaneView()
    {
        InitializeComponent();
    }

    public Macro? Macro => _macro;

    public void Bind(MacroGroup g, int laneIndex, List<Macro> guardMacros)
    {
        _group        = g;
        LaneIndex     = laneIndex;
        _macro        = g.Lanes[laneIndex];
        _guardMacros  = guardMacros;
        Reload();
    }

    public void Reload()
    {
        if (_macro == null) return;
        _suppress = true;
        try
        {
            LaneNameLabel.Text = LaneIndex switch
            {
                0 => "⭐ Primary",
                1 => "＋ Secondary",
                2 => "＋ Third",
                _ => $"Lane {LaneIndex + 1}"
            };
            TxtName.Text         = _macro.Name;
            TxtRepeat.Text       = _macro.RepeatCount == 0 ? "∞" : _macro.RepeatCount.ToString();
            TxtSpeed.Text        = _macro.SpeedMultiplier.ToString("F2");
            ChkRecMove.IsChecked = _macro.RecordMouseMove;

            ChkUseTargetWindow.IsChecked = _macro.UseTargetWindow;
            TxtWindowTitle.Text          = _macro.TargetWindowTitle ?? "";

            // Backend dropdown
            string backend = (_macro.InputBackend ?? "auto").ToLowerInvariant();
            foreach (var item in CmbBackend.Items.OfType<ComboBoxItem>())
            {
                if ((item.Tag as string) == backend) { CmbBackend.SelectedItem = item; break; }
            }
            if (CmbBackend.SelectedItem == null) CmbBackend.SelectedIndex = 0;

            // Lane enabled toggle (hidden for primary)
            if (LaneIndex == 0)
            {
                ChkLaneEnabled.IsChecked = true;
                ChkLaneEnabled.IsEnabled = false;
                ChkLaneEnabled.Visibility = Visibility.Collapsed;
            }
            else
            {
                ChkLaneEnabled.IsChecked = _macro.LaneEnabled;
                ChkLaneEnabled.IsEnabled = true;
                ChkLaneEnabled.Visibility = Visibility.Visible;
            }

            // Events grid
            EventGrid.ItemsSource = _macro.Events;
            EventGrid.Items.Refresh();
            EventCount.Text = $"{_macro.Events.Count} events";

            // Guard tab
            ChkGuardEnabled.IsChecked = _macro.PixelGuardEnabled;
            TxtGuardX.Text = _macro.PixelGuardCapXPct.ToString("F3");
            TxtGuardY.Text = _macro.PixelGuardCapYPct.ToString("F3");
            TxtGuardW.Text = _macro.PixelGuardCapWPct.ToString("F3");
            TxtGuardH.Text = _macro.PixelGuardCapHPct.ToString("F3");
            TxtGuardCorrectionKey.Text = _macro.PixelGuardCorrectionKey ?? "";

            CmbGuardCorrectionMacro.Items.Clear();
            CmbGuardCorrectionMacro.Items.Add(new ComboBoxItem { Content = "(none)", Tag = "" });
            foreach (var gm in _guardMacros)
                CmbGuardCorrectionMacro.Items.Add(new ComboBoxItem { Content = gm.Name, Tag = gm.Id });
            string targetMacro = _macro.PixelGuardCorrectionMacro ?? "";
            foreach (var item in CmbGuardCorrectionMacro.Items.OfType<ComboBoxItem>())
                if ((item.Tag as string) == targetMacro) { CmbGuardCorrectionMacro.SelectedItem = item; break; }
            if (CmbGuardCorrectionMacro.SelectedItem == null) CmbGuardCorrectionMacro.SelectedIndex = 0;

            RefreshPidStatus();
        }
        finally { _suppress = false; }
    }

    public void SetStatus(string text, bool running)
    {
        LaneStatusLabel.Text       = text;
        LaneStatusLabel.Foreground = running
            ? (System.Windows.Media.Brush?)FindResource("GreenBrush")
            : (System.Windows.Media.Brush?)FindResource("Subtext0Brush");
    }

    public void RefreshPidStatus()
    {
        if (_macro == null) { LblPidStatus.Text = ""; return; }
        if (!_macro.UseTargetWindow || string.IsNullOrEmpty(_macro.TargetWindowTitle))
        {
            LblPidStatus.Text = "";
            return;
        }

        // skip-chain: skip HWNDs of prior lanes
        var skip = new HashSet<IntPtr>();
        if (_group != null)
            for (int i = 0; i < LaneIndex; i++)
            {
                var lane = _group.Lanes[i];
                if (!lane.UseTargetWindow || string.IsNullOrEmpty(lane.TargetWindowTitle)) continue;
                var ph = Win32.ResolveWindowHwnd(lane.TargetWindowTitle, lane.TargetWindowInstance, skip);
                if (ph != IntPtr.Zero) skip.Add(ph);
            }

        var hwnd = Win32.ResolveWindowHwnd(_macro.TargetWindowTitle, _macro.TargetWindowInstance, skip);
        if (hwnd == IntPtr.Zero)
        {
            string tag = _macro.TargetWindowInstance > 0 ? $" (#{_macro.TargetWindowInstance + 1})" : "";
            LblPidStatus.Text = $"⚠ Window not found{tag}";
            return;
        }

        Win32.GetWindowThreadProcessId(hwnd, out uint pid);
        string hook = "";
        if ((_macro.InputBackend ?? "").Equals("detours", StringComparison.OrdinalIgnoreCase))
            hook = "  Hook: " + (DetoursService.PipeExists((int)pid) ? "✓ loaded" : "✗ not loaded");
        string inst = _macro.TargetWindowInstance > 0 ? $"  #{_macro.TargetWindowInstance + 1}" : "";
        LblPidStatus.Text = $"PID {pid}  HWND 0x{hwnd:X8}{inst}{hook}";
    }

    // ───── handlers ─────
    private void BtnRecord_Click(object s, RoutedEventArgs e) => RecordClick?.Invoke();
    private void BtnPlay_Click(object s, RoutedEventArgs e)   => PlayClick?.Invoke();
    private void BtnStop_Click(object s, RoutedEventArgs e)   => StopClick?.Invoke();

    private void TxtName_Changed(object s, TextChangedEventArgs e)
    {
        if (_suppress || _macro == null) return;
        _macro.Name = TxtName.Text;
        ChangedAndSave?.Invoke();
    }

    private void TxtRepeat_Changed(object s, TextChangedEventArgs e)
    {
        if (_suppress || _macro == null) return;
        string t = TxtRepeat.Text.Trim();
        if (t == "∞" || t.Equals("inf", StringComparison.OrdinalIgnoreCase) || t == "0")
            _macro.RepeatCount = 0;
        else if (int.TryParse(t, out int v))
            _macro.RepeatCount = Math.Max(0, v);
        ChangedAndSave?.Invoke();
    }

    private void TxtSpeed_Changed(object s, TextChangedEventArgs e)
    {
        if (_suppress || _macro == null) return;
        if (double.TryParse(TxtSpeed.Text, System.Globalization.NumberStyles.Float,
            System.Globalization.CultureInfo.InvariantCulture, out double v))
            _macro.SpeedMultiplier = Math.Max(0.01, v);
        ChangedAndSave?.Invoke();
    }

    private void ChkRecMove_Changed(object s, RoutedEventArgs e)
    {
        if (_suppress || _macro == null) return;
        _macro.RecordMouseMove = ChkRecMove.IsChecked == true;
        ChangedAndSave?.Invoke();
    }

    private void ChkUseTargetWindow_Changed(object s, RoutedEventArgs e)
    {
        if (_suppress || _macro == null) return;
        _macro.UseTargetWindow = ChkUseTargetWindow.IsChecked == true;
        RefreshPidStatus();
        ChangedAndSave?.Invoke();
    }

    private void TxtWindowTitle_Changed(object s, TextChangedEventArgs e)
    {
        if (_suppress || _macro == null) return;
        _macro.TargetWindowTitle = TxtWindowTitle.Text;
        // Reset instance when title changes manually
        _macro.TargetWindowInstance = 0;
        RefreshPidStatus();
        ChangedAndSave?.Invoke();
    }

    private void CmbBackend_Changed(object s, SelectionChangedEventArgs e)
    {
        if (_suppress || _macro == null) return;
        if (CmbBackend.SelectedItem is ComboBoxItem ci)
            _macro.InputBackend = (ci.Tag as string) ?? "auto";
        RefreshPidStatus();
        ChangedAndSave?.Invoke();
    }

    private void ChkLaneEnabled_Changed(object s, RoutedEventArgs e)
    {
        if (_suppress || _macro == null) return;
        _macro.LaneEnabled = ChkLaneEnabled.IsChecked == true;
        ChangedAndSave?.Invoke();
    }

    // window picking
    private void BtnPick_Click(object s, RoutedEventArgs e)
    {
        var dlg = new WindowPickerDialog { Owner = Window.GetWindow(this) };
        if (dlg.ShowDialog() == true && dlg.SelectedHwnd != IntPtr.Zero)
            ApplyPickedWindow(dlg.SelectedTitle ?? "", dlg.SelectedHwnd);
    }

    private void BtnCapture3s_Click(object s, RoutedEventArgs e)
    {
        var dlg = new CaptureCountdownDialog { Owner = Window.GetWindow(this) };
        if (dlg.ShowDialog() == true && dlg.CapturedHwnd != IntPtr.Zero)
            ApplyPickedWindow(dlg.CapturedTitle, dlg.CapturedHwnd);
    }

    private void BtnDrag_Click(object s, RoutedEventArgs e)
    {
        var dlg = new DragPickDialog { Owner = Window.GetWindow(this) };
        if (dlg.ShowDialog() == true && dlg.SelectedHwnd != IntPtr.Zero)
            ApplyPickedWindow(dlg.SelectedTitle, dlg.SelectedHwnd);
    }

    private void ApplyPickedWindow(string title, IntPtr hwnd)
    {
        if (_macro == null) return;
        var allMatches = Win32.FindWindowsByTitle(title).Select(x => x.hwnd).ToList();
        int inst = allMatches.IndexOf(hwnd);
        if (inst < 0) inst = 0;

        _macro.TargetWindowTitle    = title;
        _macro.TargetWindowInstance = inst;
        _macro.UseTargetWindow      = true;

        _suppress = true;
        TxtWindowTitle.Text         = title;
        ChkUseTargetWindow.IsChecked = true;
        _suppress = false;

        RefreshPidStatus();
        ChangedAndSave?.Invoke();
        Log?.Invoke($"Lane {LaneIndex+1} → window '{title}' (#{inst+1})");
    }

    private void BtnHookStatus_Click(object s, RoutedEventArgs e)
    {
        if (_macro == null) return;
        if (!_macro.UseTargetWindow || string.IsNullOrEmpty(_macro.TargetWindowTitle))
        {
            MessageBox.Show("Set a target window first.", "Hook Status"); return;
        }
        var hwnd = Win32.ResolveWindowHwnd(_macro.TargetWindowTitle, _macro.TargetWindowInstance, null);
        if (hwnd == IntPtr.Zero) { MessageBox.Show("Window not found."); return; }
        Win32.GetWindowThreadProcessId(hwnd, out uint pid);
        bool loaded = DetoursService.PipeExists((int)pid);
        var (dll, injector, arch) = DetoursService.ArtifactsForPid((int)pid);
        string msg = $"PID: {pid}\nArch: {arch}\nDLL: {dll}\nInjector: {injector}\n\n" +
                     $"Pipe: {(loaded ? "✓ exists (hook loaded)" : "✗ not found (hook not loaded)")}\n\n" +
                     $"Use 'Reload Hook' to force re-inject.";
        var r = MessageBox.Show(msg + "\n\nForce reload now?", "Hook Status", MessageBoxButton.YesNo);
        if (r == MessageBoxResult.Yes)
        {
            using var d = new DetoursService((int)pid);
            var (ok, m) = d.Inject(forceReload: true);
            Log?.Invoke($"[hook] {(ok ? "OK" : "FAIL")}: {m}");
            MessageBox.Show(m, ok ? "Hook OK" : "Hook FAIL");
        }
    }

    // events tab
    private void BtnClearAll_Click(object s, RoutedEventArgs e)
    {
        if (_macro == null) return;
        if (MessageBox.Show($"Clear all {_macro.Events.Count} events?", "Confirm",
            MessageBoxButton.YesNo) != MessageBoxResult.Yes) return;
        _macro.Events.Clear();
        EventGrid.Items.Refresh();
        EventCount.Text = "0 events";
        ChangedAndSave?.Invoke();
    }

    private void BtnDeleteSelected_Click(object s, RoutedEventArgs e)
    {
        if (_macro == null) return;
        var sel = EventGrid.SelectedItems.OfType<MacroEvent>().ToList();
        foreach (var ev in sel) _macro.Events.Remove(ev);
        EventGrid.Items.Refresh();
        EventCount.Text = $"{_macro.Events.Count} events";
        ChangedAndSave?.Invoke();
    }

    private void EventGrid_KeyDown(object s, KeyEventArgs e)
    {
        if (e.Key == Key.Delete) BtnDeleteSelected_Click(s, e);
    }

    // guard tab
    private void ChkGuardEnabled_Changed(object s, RoutedEventArgs e)
    {
        if (_suppress || _macro == null) return;
        _macro.PixelGuardEnabled = ChkGuardEnabled.IsChecked == true;
        ChangedAndSave?.Invoke();
    }

    private void TxtGuardRegion_Changed(object s, TextChangedEventArgs e)
    {
        if (_suppress || _macro == null) return;
        var ci = System.Globalization.CultureInfo.InvariantCulture;
        var ns = System.Globalization.NumberStyles.Float;
        if (double.TryParse(TxtGuardX.Text, ns, ci, out double x)) _macro.PixelGuardCapXPct = x;
        if (double.TryParse(TxtGuardY.Text, ns, ci, out double y)) _macro.PixelGuardCapYPct = y;
        if (double.TryParse(TxtGuardW.Text, ns, ci, out double w)) _macro.PixelGuardCapWPct = w;
        if (double.TryParse(TxtGuardH.Text, ns, ci, out double h)) _macro.PixelGuardCapHPct = h;
        ChangedAndSave?.Invoke();
    }

    private void TxtGuardCorrectionKey_Changed(object s, TextChangedEventArgs e)
    {
        if (_suppress || _macro == null) return;
        _macro.PixelGuardCorrectionKey = TxtGuardCorrectionKey.Text;
        ChangedAndSave?.Invoke();
    }

    private void CmbGuardCorrectionMacro_Changed(object s, SelectionChangedEventArgs e)
    {
        if (_suppress || _macro == null) return;
        if (CmbGuardCorrectionMacro.SelectedItem is ComboBoxItem ci)
            _macro.PixelGuardCorrectionMacro = (ci.Tag as string) ?? "";
        ChangedAndSave?.Invoke();
    }

    private void BtnGuardTest_Click(object s, RoutedEventArgs e)
    {
        if (_macro == null) return;
        if (!_macro.UseTargetWindow || string.IsNullOrEmpty(_macro.TargetWindowTitle))
        {
            MessageBox.Show("Set a target window first."); return;
        }
        var hwnd = Win32.ResolveWindowHwnd(_macro.TargetWindowTitle, _macro.TargetWindowInstance, null);
        if (hwnd == IntPtr.Zero) { MessageBox.Show("Window not found."); return; }

        // Quick one-shot capture + detection
        using var pg = new PixelGuardService();
        bool triggered = false;
        pg.Triggered    += _ => triggered = true;
        pg.StatusEvent  += t => Log?.Invoke($"[guard-test] {t}");
        pg.Start(_macro, hwnd, _ => { }, _ => { }, _guardMacros, intervalMs: 100);
        Thread.Sleep(400);
        pg.Stop();
        MessageBox.Show(triggered
            ? "✓ Red detected in capture region."
            : "No red detected in capture region.", "Guard Test");
    }
}
