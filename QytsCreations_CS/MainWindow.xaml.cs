using System.Collections.ObjectModel;
using System.ComponentModel;
using System.IO;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media.Animation;
using System.Windows.Threading;
using QytCroRec.Dialogs;
using QytCroRec.Models;
using QytCroRec.Services;
using QytCroRec.Views;

namespace QytCroRec;

public partial class MainWindow : Window
{
    // ── services ──────────────────────────────────────────────
    private readonly StorageService      _storage;
    private readonly GlobalHotkeyService _hotkeys;
    private readonly AutoUpdateService   _updater;
    private TrayService?                 _tray;

    private RecorderService?    _recorder;
    private readonly Dictionary<int, PlayerService> _runningPlayers = new();
    private readonly Dictionary<int, PixelGuardService> _activeGuards = new();

    // ── state ─────────────────────────────────────────────────
    private readonly ObservableCollection<MacroGroup> _groups       = new();
    private List<Macro>                               _guardMacros  = new();
    private Dictionary<string, string>                _shortcutsCfg = Shortcuts.Defaults;
    private AppSettings                               _settings     = new();
    private MacroGroup?                               _curGroup;
    private int                                       _currentLaneTab;
    private bool                                      _recording;
    private int                                       _recordLaneIndex;

    private readonly LaneView _viewP = new(), _viewS = new(), _viewT = new();
    private readonly ShortcutsView _viewShortcuts = new();

    private readonly DispatcherTimer _pidTimer;

    public MainWindow()
    {
        InitializeComponent();
        _storage = new StorageService();
        _hotkeys = new GlobalHotkeyService(this);
        _updater = new AutoUpdateService(GetVersion());

        GroupList.ItemsSource = _groups;

        LaneViewPrimary.Content   = _viewP;
        LaneViewSecondary.Content = _viewS;
        LaneViewThird.Content     = _viewT;
        ShortcutsView.Content     = _viewShortcuts;

        WireLaneView(_viewP, 0);
        WireLaneView(_viewS, 1);
        WireLaneView(_viewT, 2);

        _viewShortcuts.Changed += cfg =>
        {
            _shortcutsCfg = cfg;
            _storage.SaveShortcuts(cfg);
            RegisterHotkeys();
            UpdateHotkeyHint();
        };

        _pidTimer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(2) };
        _pidTimer.Tick += (_, _) =>
        {
            _viewP.RefreshPidStatus();
            _viewS.RefreshPidStatus();
            _viewT.RefreshPidStatus();
        };
        _pidTimer.Start();

        Loaded     += OnLoaded;
        Closing    += OnClosing;
        StateChanged += OnStateChanged;
    }

    private void WireLaneView(LaneView v, int laneIndex)
    {
        v.RecordClick    += () => ToggleRecord(laneIndex);
        v.PlayClick      += () => StartPlay(laneIndex);
        v.StopClick      += () => StopLane(laneIndex);
        v.ChangedAndSave += () => SaveCurrentGroup();
        v.Log            += Log;
    }

    // ═══════════════════════════════════════════════════════════
    //  STARTUP / SHUTDOWN
    // ═══════════════════════════════════════════════════════════
    private void OnLoaded(object sender, RoutedEventArgs e)
    {
        _settings     = _storage.LoadSettings();
        _shortcutsCfg = _storage.LoadShortcuts();
        if (_shortcutsCfg.Count == 0) _shortcutsCfg = Shortcuts.Defaults;
        _guardMacros  = _storage.LoadGuardMacros();
        RefreshGuardMacroList();

        ChkAutoUpdate.IsChecked = _settings.AutoUpdate;
        _viewShortcuts.LoadConfig(_shortcutsCfg);

        LoadGroups();
        RegisterHotkeys();
        UpdateHotkeyHint();

        string ver = GetVersion();
        VersionLabel.Text = $"v{ver}";
        Log($"QytCroRec {ver} (C#) loaded — {_groups.Count} group(s)");
        _storage.DiagLog($"Started v{ver}");

        _tray = new TrayService(this);
        _tray.Initialize();
        _tray.RequestStopAll += () => Dispatcher.InvokeAsync(StopAll);

        if (_settings.AutoUpdate)
            _ = Task.Run(async () =>
            {
                var (newer, newVer, log) = await _updater.CheckAsync();
                if (newer) Dispatcher.InvokeAsync(() => PromptUpdate(newVer, log));
            });
    }

    private void OnClosing(object? sender, CancelEventArgs e)
    {
        if (_settings.MinimizeToTray && _tray != null)
        {
            e.Cancel = true;
            _tray.HideToTray();
            return;
        }
        StopAll();
        _hotkeys.Dispose();
        _tray?.Dispose();
        _pidTimer.Stop();
        SaveCurrentGroup();
    }

    private void OnStateChanged(object? s, EventArgs e)
    {
        if (WindowState == WindowState.Minimized && _settings.MinimizeToTray && _tray != null)
            _tray.HideToTray();
    }

    // ═══════════════════════════════════════════════════════════
    //  STORAGE / GROUPS
    // ═══════════════════════════════════════════════════════════
    private void LoadGroups()
    {
        var loaded = _storage.LoadGroups();
        _groups.Clear();
        foreach (var g in loaded) _groups.Add(g);
        if (_groups.Count == 0)
        {
            var g = new MacroGroup { Name = "Group 1" };
            g.EnsureLanes();
            _groups.Add(g);
        }
        GroupList.SelectedIndex = 0;
    }

    private void SaveCurrentGroup() => _storage.SaveGroups([.. _groups]);

    private void RefreshGuardMacroList()
    {
        GuardMacroList.Items.Clear();
        foreach (var gm in _guardMacros)
            GuardMacroList.Items.Add($"{gm.Name}  ({gm.Events.Count} ev)");
    }

    // ═══════════════════════════════════════════════════════════
    //  HOTKEYS
    // ═══════════════════════════════════════════════════════════
    private void RegisterHotkeys()
    {
        _hotkeys.UnregisterAll();
        Reg("toggle_record", () => ToggleRecord(_currentLaneTab));
        Reg("play",          () => StartPlay(_currentLaneTab));
        Reg("stop",          () => StopLane(_currentLaneTab));
        Reg("stop_all",      StopAll);
        Reg("play_chain",    PlayChain);
        Reg("capture_window", () => _viewP.Dispatcher.Invoke(() =>
        {
            var view = CurrentLaneView();
            view?.Dispatcher.Invoke(() => (view).GetType()
                .GetMethod("BtnCapture3s_Click",
                    System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance)
                ?.Invoke(view, new object[] { view, new RoutedEventArgs() }));
        }));
    }

    private void Reg(string action, Action handler)
    {
        string key = Shortcuts.Get(_shortcutsCfg, action);
        if (string.IsNullOrWhiteSpace(key)) return;
        _hotkeys.Register(key, () => Dispatcher.InvokeAsync(handler));
    }

    private void UpdateHotkeyHint() => HotkeyHint.Text =
        $"Rec: {Shortcuts.Get(_shortcutsCfg, "toggle_record")}  |  " +
        $"Play: {Shortcuts.Get(_shortcutsCfg, "play")}  |  " +
        $"Stop All: {Shortcuts.Get(_shortcutsCfg, "stop_all")}  |  " +
        $"Chain: {Shortcuts.Get(_shortcutsCfg, "play_chain")}";

    private LaneView? CurrentLaneView() => _currentLaneTab switch
    {
        0 => _viewP, 1 => _viewS, 2 => _viewT, _ => null
    };

    // ═══════════════════════════════════════════════════════════
    //  RECORD
    // ═══════════════════════════════════════════════════════════
    private void ToggleRecord(int laneIndex)
    {
        if (_recording) StopRecord();
        else            StartRecord(laneIndex);
    }

    private void StartRecord(int laneIndex)
    {
        if (_curGroup == null || _recording) return;
        if (laneIndex < 0 || laneIndex >= 3) return;
        var lane = _curGroup.Lanes[laneIndex];
        _recordLaneIndex = laneIndex;

        _recording = true;
        SetLaneStatus(laneIndex, "Recording…", true);
        TtsService.Speak($"Recording {lane.Name}");
        Log($"Recording started — lane {laneIndex + 1}: {lane.Name}");

        lane.Events.Clear();
        CurrentLaneView()?.Reload();

        IntPtr hwnd = IntPtr.Zero;
        if (lane.UseTargetWindow && !string.IsNullOrEmpty(lane.TargetWindowTitle))
            hwnd = Win32.ResolveWindowHwnd(lane.TargetWindowTitle, lane.TargetWindowInstance, null);

        _recorder = new RecorderService(lane.RecordMouseMove, hwnd);
        _recorder.EventCaptured += ev =>
            Dispatcher.InvokeAsync(() =>
            {
                lane.Events.Add(ev);
                CurrentLaneView()?.Reload();
            });
        _recorder.Start();
        LaneTabs.SelectedIndex = laneIndex;
    }

    private void StopRecord()
    {
        if (!_recording) return;
        _recording = false;
        _recorder?.Stop();
        _recorder = null;

        SetLaneStatus(_recordLaneIndex, "Idle", false);
        TtsService.Speak("Recording complete");
        SaveCurrentGroup();
        Log("Recording stopped");

        // auto-switch to next empty lane
        if (_curGroup != null && _recordLaneIndex == 0)
        {
            for (int ni = 1; ni < _curGroup.Lanes.Count; ni++)
            {
                var nl = _curGroup.Lanes[ni];
                if (nl.LaneEnabled && nl.Events.Count == 0)
                {
                    int capture = ni;
                    Dispatcher.InvokeAsync(() => SwitchLaneWithToast(capture));
                    break;
                }
            }
        }
    }

    private void SetLaneStatus(int laneIndex, string text, bool running)
    {
        switch (laneIndex)
        {
            case 0: _viewP.SetStatus(text, running); break;
            case 1: _viewS.SetStatus(text, running); break;
            case 2: _viewT.SetStatus(text, running); break;
        }
    }

    // ═══════════════════════════════════════════════════════════
    //  PLAY
    // ═══════════════════════════════════════════════════════════
    private void StartPlay(int laneIndex)
    {
        if (_curGroup == null) return;
        if (laneIndex < 0 || laneIndex >= 3) return;
        var lane = _curGroup.Lanes[laneIndex];
        if (lane.Events.Count == 0) { Log($"Lane {laneIndex + 1} has no events."); return; }

        // resolve hwnd (with skip-chain of prior lanes that are also playing)
        var skip = new HashSet<IntPtr>();
        foreach (var (_, _) in _runningPlayers) { /* nothing */ }
        IntPtr hwnd = lane.UseTargetWindow && !string.IsNullOrEmpty(lane.TargetWindowTitle)
            ? Win32.ResolveWindowHwnd(lane.TargetWindowTitle, lane.TargetWindowInstance, skip)
            : IntPtr.Zero;

        var svc = new PlayerService();
        svc.Completed += () => Dispatcher.InvokeAsync(() => LaneCompleted(laneIndex));
        _runningPlayers[laneIndex] = svc;
        SetLaneStatus(laneIndex, "▶ Playing", true);
        Log($"Lane {laneIndex + 1} playing → {lane.Events.Count} events");
        svc.Play(lane, hwnd);

        // start guard if enabled
        if (lane.PixelGuardEnabled && hwnd != IntPtr.Zero)
        {
            var pg = new PixelGuardService();
            pg.StatusEvent += t => Log($"[guard L{laneIndex+1}] {t}");
            pg.Start(lane, hwnd,
                key => svc.SendKeyTap(key),
                m   => svc.PlayInline(m, hwnd),
                _guardMacros);
            _activeGuards[laneIndex] = pg;
        }
    }

    private void LaneCompleted(int laneIndex)
    {
        SetLaneStatus(laneIndex, "Idle", false);
        _runningPlayers.Remove(laneIndex);
        if (_activeGuards.TryGetValue(laneIndex, out var pg))
        {
            pg.Stop();
            _activeGuards.Remove(laneIndex);
        }
        if (_curGroup != null)
        {
            _curGroup.Lanes[laneIndex].RunCount++;
            RefreshRunsLabel();
        }
    }

    private void StopLane(int laneIndex)
    {
        if (_runningPlayers.TryGetValue(laneIndex, out var svc))
        {
            svc.Stop();
            _runningPlayers.Remove(laneIndex);
        }
        if (_activeGuards.TryGetValue(laneIndex, out var pg))
        {
            pg.Stop();
            _activeGuards.Remove(laneIndex);
        }
        SetLaneStatus(laneIndex, "Idle", false);
    }

    private void PlayChain()
    {
        if (_curGroup == null) return;
        var active = _curGroup.Lanes
            .Select((l, i) => (lane: l, idx: i))
            .Where(t => t.lane.LaneEnabled && t.lane.Events.Count > 0)
            .ToList();
        if (active.Count == 0) { Log("No lanes to play."); return; }

        // pre-resolve hwnds with skip chain
        var claimed = new HashSet<IntPtr>();
        var hwnds   = new Dictionary<int, IntPtr>();
        foreach (var (lane, idx) in active)
        {
            if (lane.UseTargetWindow && !string.IsNullOrEmpty(lane.TargetWindowTitle))
            {
                var h = Win32.ResolveWindowHwnd(lane.TargetWindowTitle, lane.TargetWindowInstance, claimed);
                if (h != IntPtr.Zero) { claimed.Add(h); hwnds[idx] = h; }
            }
        }

        var barrier = new System.Threading.Barrier(active.Count);
        Log($"Play Chain → {active.Count} lane(s) launching in sync");

        foreach (var (lane, idx) in active)
        {
            var svc = new PlayerService();
            svc.Completed += () => Dispatcher.InvokeAsync(() => LaneCompleted(idx));
            _runningPlayers[idx] = svc;
            SetLaneStatus(idx, "▶ Playing", true);

            var h = hwnds.GetValueOrDefault(idx, IntPtr.Zero);
            var li = idx; var ln = lane;
            Task.Run(() =>
            {
                try { barrier.SignalAndWait(10_000); } catch { return; }
                svc.Play(ln, h);
            });

            if (lane.PixelGuardEnabled && h != IntPtr.Zero)
            {
                var pg = new PixelGuardService();
                pg.StatusEvent += t => Log($"[guard L{idx+1}] {t}");
                pg.Start(lane, h, key => svc.SendKeyTap(key), m => svc.PlayInline(m, h), _guardMacros);
                _activeGuards[idx] = pg;
            }
        }
    }

    private void StopAll()
    {
        foreach (var (_, svc) in _runningPlayers.ToList()) svc.Stop();
        _runningPlayers.Clear();
        foreach (var (_, pg) in _activeGuards.ToList()) pg.Stop();
        _activeGuards.Clear();
        if (_recording) StopRecord();
        SetLaneStatus(0, "Idle", false);
        SetLaneStatus(1, "Idle", false);
        SetLaneStatus(2, "Idle", false);
        Log("Stop All");
    }

    // ═══════════════════════════════════════════════════════════
    //  GROUP MANAGEMENT
    // ═══════════════════════════════════════════════════════════
    private void BtnAddGroup_Click(object s, RoutedEventArgs e)
    {
        var g = new MacroGroup { Name = $"Group {_groups.Count + 1}" };
        g.EnsureLanes();
        _groups.Add(g);
        GroupList.SelectedItem = g;
        SaveCurrentGroup();
    }

    private void BtnDuplicate_Click(object s, RoutedEventArgs e) => GroupCtx_Duplicate(s, e);
    private void BtnDelete_Click(object s, RoutedEventArgs e)    => GroupCtx_Delete(s, e);

    private void GroupList_SelectionChanged(object s, SelectionChangedEventArgs e)
    {
        if (GroupList.SelectedItem is MacroGroup g)
        {
            _curGroup = g;
            LoadGroupToUi(g);
        }
    }

    private void GroupList_MouseDoubleClick(object s, MouseButtonEventArgs e) =>
        GroupCtx_Rename(s, e);

    private void GroupCtx_Rename(object s, RoutedEventArgs e)
    {
        if (_curGroup == null) return;
        string? name = InputDialog.Prompt(this, "Rename Group", "Name:", _curGroup.Name);
        if (name != null) { _curGroup.Name = name; GroupList.Items.Refresh(); SaveCurrentGroup(); }
    }

    private void GroupCtx_Duplicate(object s, RoutedEventArgs e)
    {
        if (_curGroup == null) return;
        var clone = _curGroup.Clone();
        clone.Name += " (copy)";
        _groups.Add(clone);
        GroupList.SelectedItem = clone;
        SaveCurrentGroup();
    }

    private void GroupCtx_Delete(object s, RoutedEventArgs e)
    {
        if (_curGroup == null) return;
        if (_groups.Count == 1) { MessageBox.Show("Need at least one group."); return; }
        if (MessageBox.Show($"Delete '{_curGroup.Name}'?", "Confirm",
            MessageBoxButton.YesNo) == MessageBoxResult.No) return;
        _groups.Remove(_curGroup);
        GroupList.SelectedIndex = 0;
        SaveCurrentGroup();
    }

    // ═══════════════════════════════════════════════════════════
    //  LOAD GROUP INTO UI
    // ═══════════════════════════════════════════════════════════
    private void LoadGroupToUi(MacroGroup g)
    {
        g.EnsureLanes();
        _viewP.Bind(g, 0, _guardMacros);
        _viewS.Bind(g, 1, _guardMacros);
        _viewT.Bind(g, 2, _guardMacros);

        // pixel guard panel
        int src = Math.Clamp(g.SharedGuardLane >= 0 ? g.SharedGuardLane : 0, 0, 2);
        CmbGuardSource.SelectedIndex = src;
        ChkGuardPri.IsChecked = (g.SharedGuardUsers & 0b001) != 0;
        ChkGuardSec.IsChecked = (g.SharedGuardUsers & 0b010) != 0;
        ChkGuardThi.IsChecked = (g.SharedGuardUsers & 0b100) != 0;

        RefreshRunsLabel();
    }

    // ═══════════════════════════════════════════════════════════
    //  TABS
    // ═══════════════════════════════════════════════════════════
    private void LaneTabs_SelectionChanged(object s, SelectionChangedEventArgs e)
    {
        _currentLaneTab = LaneTabs.SelectedIndex;
    }

    // ═══════════════════════════════════════════════════════════
    //  PIXEL GUARD shared panel
    // ═══════════════════════════════════════════════════════════
    private void CmbGuardSource_Changed(object s, SelectionChangedEventArgs e)
    {
        if (_curGroup == null) return;
        _curGroup.SharedGuardLane = CmbGuardSource.SelectedIndex;
        SaveCurrentGroup();
    }

    private void ChkGuardUser_Changed(object s, RoutedEventArgs e)
    {
        if (_curGroup == null) return;
        int bits = 0;
        if (ChkGuardPri.IsChecked == true) bits |= 0b001;
        if (ChkGuardSec.IsChecked == true) bits |= 0b010;
        if (ChkGuardThi.IsChecked == true) bits |= 0b100;
        _curGroup.SharedGuardUsers = bits;
        SaveCurrentGroup();
    }

    // ═══════════════════════════════════════════════════════════
    //  GUARD MACROS panel
    // ═══════════════════════════════════════════════════════════
    private void BtnGuardAdd_Click(object s, RoutedEventArgs e)
    {
        string? name = InputDialog.Prompt(this, "New Guard Macro", "Name:", $"Guard {_guardMacros.Count + 1}");
        if (string.IsNullOrEmpty(name)) return;
        var gm = new Macro { Name = name };
        _guardMacros.Add(gm);
        _storage.SaveGuardMacros(_guardMacros);
        RefreshGuardMacroList();
    }

    private void BtnGuardDel_Click(object s, RoutedEventArgs e)
    {
        int idx = GuardMacroList.SelectedIndex;
        if (idx < 0 || idx >= _guardMacros.Count) return;
        var gm = _guardMacros[idx];
        if (MessageBox.Show($"Delete guard macro '{gm.Name}'?", "Confirm",
            MessageBoxButton.YesNo) != MessageBoxResult.Yes) return;
        _guardMacros.RemoveAt(idx);
        _storage.SaveGuardMacros(_guardMacros);
        RefreshGuardMacroList();
    }

    private void BtnGuardRec_Click(object s, RoutedEventArgs e)
    {
        int idx = GuardMacroList.SelectedIndex;
        if (idx < 0 || idx >= _guardMacros.Count) { MessageBox.Show("Select a guard macro first."); return; }
        var gm = _guardMacros[idx];
        gm.Events.Clear();
        Log($"Recording guard macro '{gm.Name}'…");
        TtsService.Speak($"Recording guard {gm.Name}");
        _recorder = new RecorderService(true, IntPtr.Zero);
        _recorder.EventCaptured += ev => Dispatcher.InvokeAsync(() => gm.Events.Add(ev));
        _recorder.Start();
    }

    private void BtnGuardStop_Click(object s, RoutedEventArgs e)
    {
        _recorder?.Stop();
        _recorder = null;
        _storage.SaveGuardMacros(_guardMacros);
        RefreshGuardMacroList();
        TtsService.Speak("Guard recording complete");
        Log("Guard macro recording stopped");
    }

    private void BtnGuardView_Click(object s, RoutedEventArgs e)
    {
        int idx = GuardMacroList.SelectedIndex;
        if (idx < 0 || idx >= _guardMacros.Count) return;
        var gm = _guardMacros[idx];
        var w = new Window
        {
            Title = $"Events — {gm.Name}", Width = 600, Height = 400,
            Owner = this,
            WindowStartupLocation = WindowStartupLocation.CenterOwner
        };
        var grid = new DataGrid { ItemsSource = gm.Events, AutoGenerateColumns = false };
        grid.Columns.Add(new DataGridTextColumn { Header = "Time (s)", Binding = new System.Windows.Data.Binding("Timestamp") { StringFormat = "F4" }, Width = 80 });
        grid.Columns.Add(new DataGridTextColumn { Header = "Type",     Binding = new System.Windows.Data.Binding("EventType"), Width = 120 });
        grid.Columns.Add(new DataGridTextColumn { Header = "Data",     Binding = new System.Windows.Data.Binding("DataSummary"), Width = new DataGridLength(1, DataGridLengthUnitType.Star) });
        w.Content = grid;
        w.ShowDialog();
    }

    // ═══════════════════════════════════════════════════════════
    //  TOP BAR: log/feedback/tray/update
    // ═══════════════════════════════════════════════════════════
    private void BtnDiagLog_Click(object s, RoutedEventArgs e) =>
        new LogViewerDialog("Diag Log", _storage.DiagLogPath) { Owner = this }.Show();

    private void BtnCrashLog_Click(object s, RoutedEventArgs e) =>
        new LogViewerDialog("Crash Log", _storage.CrashLogPath) { Owner = this }.Show();

    private void BtnFeedback_Click(object s, RoutedEventArgs e) =>
        new FeedbackDialog(_storage.DiagLogPath, GetVersion()) { Owner = this }.ShowDialog();

    private void BtnTray_Click(object s, RoutedEventArgs e) => _tray?.HideToTray();

    private void ChkAutoUpdate_Changed(object s, RoutedEventArgs e)
    {
        _settings.AutoUpdate = ChkAutoUpdate.IsChecked == true;
        _storage.SaveSettings(_settings);
    }

    private async void BtnCheckUpdate_Click(object s, RoutedEventArgs e)
    {
        Log("Checking for update…");
        var (newer, newVer, log) = await _updater.CheckAsync();
        if (newer) PromptUpdate(newVer, log);
        else        Log("No update available.");
    }

    private void PromptUpdate(string newVer, string changelog)
    {
        var msg = $"New version {newVer} is available.\n\n{changelog}\n\nUpdate now?";
        if (MessageBox.Show(msg, "Update Available", MessageBoxButton.YesNo,
            MessageBoxImage.Information) == MessageBoxResult.Yes)
            _ = _updater.DownloadAndApplyAsync();
    }

    // ═══════════════════════════════════════════════════════════
    //  PLAY CHAIN + STOP ALL BUTTONS
    // ═══════════════════════════════════════════════════════════
    private void BtnPlayChain_Click(object s, RoutedEventArgs e) => PlayChain();
    private void BtnStopAll_Click(object s, RoutedEventArgs e)   => StopAll();

    // ═══════════════════════════════════════════════════════════
    //  RUNS LABEL
    // ═══════════════════════════════════════════════════════════
    private void RefreshRunsLabel()
    {
        int total = _groups.Sum(g => g.Lanes.Sum(l => l.RunCount));
        RunsLabel.Text  = $"Runs: {total}";
        ExecLabel.Text  = $"Executions: {total}";
        RunsInline.Text = total == 0 ? "  Runs: (none yet)" : $"  Runs: {total}";
    }

    private void RunsLabel_RightClick(object s, MouseButtonEventArgs e) => ShowResetMenu(s);
    private void ExecLabel_RightClick(object s, MouseButtonEventArgs e) => ShowResetMenu(s);

    private void ShowResetMenu(object sender)
    {
        var ctx = new ContextMenu();
        var allItem = new MenuItem { Header = "Reset ALL counters" };
        allItem.Click += (_, _) =>
        {
            foreach (var g in _groups) foreach (var l in g.Lanes) l.RunCount = 0;
            SaveCurrentGroup(); RefreshRunsLabel();
        };
        ctx.Items.Add(allItem);
        ctx.Items.Add(new Separator());
        foreach (var g in _groups)
        {
            int tot = g.Lanes.Sum(l => l.RunCount);
            if (tot == 0) continue;
            var gi = g;
            var item = new MenuItem { Header = $"Reset [{g.Name}] ({tot} runs)" };
            item.Click += (_, _) =>
            {
                foreach (var l in gi.Lanes) l.RunCount = 0;
                SaveCurrentGroup(); RefreshRunsLabel();
            };
            ctx.Items.Add(item);
        }
        if (sender is FrameworkElement fe) ctx.PlacementTarget = fe;
        ctx.IsOpen = true;
    }

    // ═══════════════════════════════════════════════════════════
    //  TOAST
    // ═══════════════════════════════════════════════════════════
    private void SwitchLaneWithToast(int laneIndex)
    {
        LaneTabs.SelectedIndex = laneIndex;
        string name = laneIndex switch { 1 => "Secondary", 2 => "Third", _ => "Primary" };
        string recKey = Shortcuts.Get(_shortcutsCfg, "toggle_record");
        ShowToast($"🎙  {name} ready — press  {recKey}  to record\nThis lane has no events yet.", 3000);
    }

    private void ShowToast(string message, int durationMs)
    {
        ToastText.Text      = message;
        ToastBorder.Opacity = 0;
        ToastBorder.Visibility = Visibility.Visible;
        ToastBorder.BeginAnimation(OpacityProperty,
            new DoubleAnimation(0, 1, TimeSpan.FromMilliseconds(300)));
        var timer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(durationMs) };
        timer.Tick += (_, _) =>
        {
            timer.Stop();
            var fade = new DoubleAnimation(1, 0, TimeSpan.FromMilliseconds(400));
            fade.Completed += (_, _) => ToastBorder.Visibility = Visibility.Collapsed;
            ToastBorder.BeginAnimation(OpacityProperty, fade);
        };
        timer.Start();
    }

    // ═══════════════════════════════════════════════════════════
    //  ACTIVITY LOG
    // ═══════════════════════════════════════════════════════════
    private void Log(string msg)
    {
        string line = $"[{DateTime.Now:HH:mm:ss}]  {msg}";
        Dispatcher.InvokeAsync(() =>
        {
            ActivityLog.Items.Add(line);
            if (ActivityLog.Items.Count > 500) ActivityLog.Items.RemoveAt(0);
            ActivityLog.ScrollIntoView(line);
        });
        _storage.DiagLog(msg);
    }

    private void BtnClearLog_Click(object s, RoutedEventArgs e) => ActivityLog.Items.Clear();

    // ═══════════════════════════════════════════════════════════
    //  HELPERS
    // ═══════════════════════════════════════════════════════════
    private static string GetVersion()
    {
        try
        {
            string vf = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "version.json");
            if (File.Exists(vf))
            {
                var d = Newtonsoft.Json.JsonConvert.DeserializeObject<Dictionary<string, string>>(
                            File.ReadAllText(vf));
                return d?.GetValueOrDefault("version") ?? "1.0.0";
            }
        }
        catch { }
        return "1.0.0";
    }
}

internal static class InputDialog
{
    public static string? Prompt(Window owner, string title, string prompt, string? defaultValue = "")
    {
        var dlg  = new Window
        {
            Title = title, Width = 360, Height = 140, Owner = owner,
            WindowStartupLocation = WindowStartupLocation.CenterOwner,
            ResizeMode = ResizeMode.NoResize
        };
        var sp  = new StackPanel { Margin = new Thickness(12) };
        var lbl = new TextBlock { Text = prompt, Margin = new Thickness(0, 0, 0, 6) };
        var txt = new TextBox   { Text = defaultValue ?? "", Padding = new Thickness(6, 4, 6, 4) };
        var btn = new Button    { Content = "OK", Width = 80,
                                  HorizontalAlignment = HorizontalAlignment.Right,
                                  Margin = new Thickness(0, 8, 0, 0) };
        btn.Click += (_, _) => { dlg.DialogResult = true; dlg.Close(); };
        sp.Children.Add(lbl); sp.Children.Add(txt); sp.Children.Add(btn);
        dlg.Content = sp;
        txt.Focus();
        return dlg.ShowDialog() == true ? txt.Text : null;
    }
}
