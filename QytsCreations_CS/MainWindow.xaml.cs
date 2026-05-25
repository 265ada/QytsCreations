using System.Collections.ObjectModel;
using System.IO;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media.Animation;
using System.Windows.Threading;
using QytCroRec.Models;
using QytCroRec.Services;

namespace QytCroRec;

public partial class MainWindow : Window
{
    // â”€â”€ services â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    private readonly StorageService       _storage;

    private readonly GlobalHotkeyService  _hotkeys;
    private RecorderService?              _recorder;
    private PlayerService?                _player;

    // â”€â”€ state â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    private readonly ObservableCollection<MacroGroup> _groups = new();
    private MacroGroup? _curGroup;
    private int         _curLane;   // 0=Primary,1=Secondary,2=Third
    private bool        _recording;
    private bool        _playing;
    private bool        _suppressUi; // block change handlers during load

    // hotkey IDs
    private int _hkRecord  = -1;
    private int _hkPlay    = -1;
    private int _hkStopAll = -1;

    // pid-refresh timer
    private readonly DispatcherTimer _pidTimer;

    public MainWindow()
    {
        InitializeComponent();

        _storage = new StorageService();
        _hotkeys = new GlobalHotkeyService(this);

        GroupList.ItemsSource = _groups;

        _pidTimer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(2) };
        _pidTimer.Tick += (_, _) => RefreshPidLabels();
        _pidTimer.Start();

        Loaded  += OnLoaded;
        Closing += OnClosing;
    }

    // â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    //  STARTUP / SHUTDOWN
    // â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    private void OnLoaded(object sender, RoutedEventArgs e)
    {
        LoadGroups();
        RegisterHotkeys();
        string ver = GetVersion();
        VersionLabel.Text = $"v{ver}";
        Log($"QytCroRec {ver} loaded â€” {_groups.Count} group(s)");
    }

    private void OnClosing(object? sender, System.ComponentModel.CancelEventArgs e)
    {
        StopAll();
        _hotkeys.Dispose();
        _pidTimer.Stop();
        SaveCurrentGroup();
    }

    // â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    //  STORAGE
    // â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
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

    // â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    //  HOTKEYS
    // â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    private void RegisterHotkeys()
    {
        _hkRecord  = _hotkeys.Register("Ctrl+R", ToggleRecord);
        _hkStopAll = _hotkeys.Register("End",    StopAll);
        _hkPlay    = _hotkeys.Register("F5",     TogglePlay);
        UpdateHotkeyHint();
    }

    private void UpdateHotkeyHint() =>
        HotkeyHint.Text = "Record: Ctrl+R  |  Play: F5  |  Stop: End";

    // â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    //  RECORD
    // â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    private void ToggleRecord()
    {
        Dispatcher.Invoke(() =>
        {
            if (_recording) StopRecord();
            else            StartRecord();
        });
    }

    private void StartRecord()
    {
        if (_curGroup == null || _recording) return;
        var lane = CurrentMacro();
        if (lane == null) return;

        _recording = true;
        BtnRecord.Content = "â¹  Stop Rec";
        SetStatus("Recordingâ€¦");
        TtsService.Speak($"Recording {lane.Name}");
        Log($"Recording started â€” {lane.Name}");

        lane.Events.Clear();
        RefreshCurrentGrid();

        bool recMove = true; // TODO: expose in settings
        IntPtr hwnd  = IntPtr.Zero;
        if (lane.UseTargetWindow && !string.IsNullOrEmpty(lane.TargetWindowTitle))
            hwnd = Win32.FindWindowsByTitle(lane.TargetWindowTitle ?? "").Select(x => x.hwnd).FirstOrDefault();

        _recorder = new RecorderService(recMove, hwnd);
        _recorder.EventCaptured += ev =>
            Dispatcher.InvokeAsync(() =>
            {
                lane.Events.Add(ev);
                RefreshCurrentGrid();
            });
        _recorder.RecordingEnded += () => Dispatcher.InvokeAsync(StopRecord);
        _recorder.Start();
    }

    private void StopRecord()
    {
        if (!_recording) return;
        _recording = false;
        _recorder?.Stop();
        _recorder = null;

        BtnRecord.Content = "âº  Record";
        SetStatus("Ready");
        TtsService.Speak("Recording complete");
        SaveCurrentGroup();
        Log("Recording stopped");

        // auto-switch to next empty lane
        if (_curGroup != null && _curLane == 0)
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

    // â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    //  PLAY
    // â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    private void TogglePlay()
    {
        Dispatcher.Invoke(() =>
        {
            if (_playing) StopAll();
            else          StartPlay();
        });
    }

    private void StartPlay()
    {
        if (_curGroup == null || _playing) return;
        var activeLanes = _curGroup.Lanes.Where(l => l.LaneEnabled && l.Events.Count > 0).ToList();
        if (activeLanes.Count == 0) { Log("No events to play."); return; }

        _playing = true;
        BtnPlay.Content = "â¸  Pause";
        SetStatus("Playingâ€¦");
        Log($"Playback started â€” {activeLanes.Count} lane(s)");

        // resolve HWNDs with instance-aware skip chain
        var claimed = new HashSet<IntPtr>();
        var players = new List<(PlayerService svc, Macro macro, IntPtr hwnd)>();

        foreach (var lane in activeLanes)
        {
            var hwnd = Win32.ResolveWindowHwnd(lane.TargetWindowTitle,
                        lane.UseTargetWindow ? lane.TargetWindowInstance : -1, claimed);
            if (hwnd != IntPtr.Zero) claimed.Add(hwnd);

            var svc = new PlayerService();
            svc.Completed += () => Dispatcher.InvokeAsync(CheckAllDone);
            players.Add((svc, lane, hwnd));
        }

        // barrier-start all lanes simultaneously
        var barrier = new System.Threading.Barrier(players.Count);
        foreach (var (svc, macro, hwnd) in players)
        {
            var m = macro; var h = hwnd; var b = barrier;
            Task.Run(() =>
            {
                try { b.SignalAndWait(10_000); } catch { return; }
                svc.Play(m, h);
            });
        }

        _player = players.FirstOrDefault().svc; // keep ref for stop
    }

    private void CheckAllDone()
    {
        // simple: stop when primary done
        StopAll();
    }

    private void StopAll()
    {
        _player?.Stop();
        _player   = null;
        _playing  = false;
        Dispatcher.InvokeAsync(() =>
        {
            BtnPlay.Content = "â–¶  Play";
            SetStatus("Ready");
        });
    }

    private void StopRecord_And_Play()
    {
        if (_recording) StopRecord();
        StopAll();
    }

    // â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    //  GROUP MANAGEMENT
    // â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    private void BtnAddGroup_Click(object s, RoutedEventArgs e)
    {
        var g = new MacroGroup { Name = $"Group {_groups.Count + 1}" };
        g.EnsureLanes();
        _groups.Add(g);
        GroupList.SelectedItem = g;
        SaveCurrentGroup();
    }

    private void GroupList_SelectionChanged(object s, SelectionChangedEventArgs e)
    {
        if (GroupList.SelectedItem is MacroGroup g)
        {
            _curGroup = g;
            LoadGroupToUi(g);
        }
    }

    private void GroupList_MouseDoubleClick(object s, MouseButtonEventArgs e)
    {
        if (_curGroup == null) return;
        string? name = InputDialog("Rename Group", "Name:", _curGroup.Name);
        if (name != null) { _curGroup.Name = name; RefreshGroupList(); SaveCurrentGroup(); }
    }

    private void GroupCtx_Rename(object s, RoutedEventArgs e)
    {
        if (_curGroup == null) return;
        string? name = InputDialog("Rename Group", "Name:", _curGroup.Name);
        if (name != null) { _curGroup.Name = name; RefreshGroupList(); SaveCurrentGroup(); }
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

    private void RefreshGroupList() => GroupList.Items.Refresh();

    // â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    //  LOAD GROUP INTO UI
    // â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    private void LoadGroupToUi(MacroGroup g)
    {
        _suppressUi = true;
        GroupNameLabel.Text      = g.Name;
        ChkGroupEnabled.IsChecked = g.Enabled;
        TxtRepeat.Text           = g.Primary.RepeatCount.ToString();
        TxtSpeed.Text            = g.Primary.SpeedMultiplier.ToString("F2");

        // primary lane
        ChkPrimaryWindow.IsChecked = g.Primary.UseTargetWindow;
        TxtPrimaryWindow.Text      = g.Primary.TargetWindowTitle ?? "";

        // secondary lane
        ChkSecEnabled.IsChecked  = g.Secondary.LaneEnabled;
        ChkSecWindow.IsChecked   = g.Secondary.UseTargetWindow;
        TxtSecWindow.Text        = g.Secondary.TargetWindowTitle ?? "";

        // third lane
        ChkThirdEnabled.IsChecked = g.Third.LaneEnabled;
        ChkThirdWindow.IsChecked  = g.Third.UseTargetWindow;
        TxtThirdWindow.Text       = g.Third.TargetWindowTitle ?? "";

        RefreshAllGrids();
        RefreshPidLabels();
        RefreshRunsLabel();
        _suppressUi = false;
    }

    // â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    //  DATA GRIDS
    // â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    private void RefreshAllGrids()
    {
        if (_curGroup == null) return;
        GridPrimary.ItemsSource   = _curGroup.Primary.Events;
        GridSecondary.ItemsSource = _curGroup.Secondary.Events;
        GridThird.ItemsSource     = _curGroup.Third.Events;
    }

    private void RefreshCurrentGrid()
    {
        if (_curGroup == null) return;
        var grid = _curLane switch { 1 => GridSecondary, 2 => GridThird, _ => GridPrimary };
        grid.Items.Refresh();
    }

    private void EventGrid_SelectionChanged(object s, SelectionChangedEventArgs e) { }

    private void EventGrid_KeyDown(object s, KeyEventArgs e)
    {
        if (e.Key == Key.Delete && s is DataGrid dg && dg.SelectedItem is MacroEvent ev)
        {
            CurrentMacro()?.Events.Remove(ev);
            dg.Items.Refresh();
            SaveCurrentGroup();
        }
    }

    // â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    //  TABS
    // â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    private void LaneTabs_SelectionChanged(object s, SelectionChangedEventArgs e)
    {
        _curLane = LaneTabs.SelectedIndex;
    }

    private Macro? CurrentMacro() =>
        _curGroup == null ? null : _curGroup.Lanes[Math.Clamp(_curLane, 0, _curGroup.Lanes.Count - 1)];

    // â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    //  LANE CONTROLS â€” PRIMARY
    // â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    private void ChkPrimaryWindow_Changed(object s, RoutedEventArgs e)
    {
        if (_suppressUi || _curGroup == null) return;
        _curGroup.Primary.UseTargetWindow = ChkPrimaryWindow.IsChecked == true;
        SaveCurrentGroup();
    }

    private void TxtPrimaryWindow_Changed(object s, TextChangedEventArgs e)
    {
        if (_suppressUi || _curGroup == null) return;
        _curGroup.Primary.TargetWindowTitle = TxtPrimaryWindow.Text;
        RefreshPidLabels();
        SaveCurrentGroup();
    }

    private void BtnPrimaryPick_Click(object s, RoutedEventArgs e) => PickWindow(0);

    // â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    //  LANE CONTROLS â€” SECONDARY
    // â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    private void ChkSecEnabled_Changed(object s, RoutedEventArgs e)
    {
        if (_suppressUi || _curGroup == null) return;
        _curGroup.Secondary.LaneEnabled = ChkSecEnabled.IsChecked == true;
        SaveCurrentGroup();
    }

    private void ChkSecWindow_Changed(object s, RoutedEventArgs e)
    {
        if (_suppressUi || _curGroup == null) return;
        _curGroup.Secondary.UseTargetWindow = ChkSecWindow.IsChecked == true;
        SaveCurrentGroup();
    }

    private void TxtSecWindow_Changed(object s, TextChangedEventArgs e)
    {
        if (_suppressUi || _curGroup == null) return;
        _curGroup.Secondary.TargetWindowTitle = TxtSecWindow.Text;
        RefreshPidLabels();
        SaveCurrentGroup();
    }

    private void BtnSecPick_Click(object s, RoutedEventArgs e) => PickWindow(1);

    // â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    //  LANE CONTROLS â€” THIRD
    // â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    private void ChkThirdEnabled_Changed(object s, RoutedEventArgs e)
    {
        if (_suppressUi || _curGroup == null) return;
        _curGroup.Third.LaneEnabled = ChkThirdEnabled.IsChecked == true;
        SaveCurrentGroup();
    }

    private void ChkThirdWindow_Changed(object s, RoutedEventArgs e)
    {
        if (_suppressUi || _curGroup == null) return;
        _curGroup.Third.UseTargetWindow = ChkThirdWindow.IsChecked == true;
        SaveCurrentGroup();
    }

    private void TxtThirdWindow_Changed(object s, TextChangedEventArgs e)
    {
        if (_suppressUi || _curGroup == null) return;
        _curGroup.Third.TargetWindowTitle = TxtThirdWindow.Text;
        RefreshPidLabels();
        SaveCurrentGroup();
    }

    private void BtnThirdPick_Click(object s, RoutedEventArgs e) => PickWindow(2);

    // â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    //  WINDOW PICKER
    // â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    private void PickWindow(int laneIndex)
    {
        if (_curGroup == null) return;
        var dlg = new WindowPickerDialog { Owner = this };
        if (dlg.ShowDialog() != true || dlg.SelectedHwnd == IntPtr.Zero) return;

        var lane  = _curGroup.Lanes[laneIndex];
        var title = dlg.SelectedTitle ?? "";
        var hwnd  = dlg.SelectedHwnd;

        // compute instance (skip chain of prior lanes)
        var claimed = new HashSet<IntPtr>();
        for (int i = 0; i < laneIndex; i++)
        {
            var ph = Win32.ResolveWindowHwnd(
                _curGroup.Lanes[i].TargetWindowTitle,
                _curGroup.Lanes[i].UseTargetWindow ? _curGroup.Lanes[i].TargetWindowInstance : -1,
                claimed);
            if (ph != IntPtr.Zero) claimed.Add(ph);
        }
        var allMatches = Win32.FindWindowsByTitle(title).Select(x => x.hwnd).ToList();
        int instance   = allMatches.IndexOf(hwnd);
        if (instance < 0) instance = 0;

        lane.TargetWindowTitle    = title;
        lane.TargetWindowInstance = instance;
        lane.UseTargetWindow      = true;

        _suppressUi = true;
        switch (laneIndex)
        {
            case 0: TxtPrimaryWindow.Text = title; ChkPrimaryWindow.IsChecked = true; break;
            case 1: TxtSecWindow.Text     = title; ChkSecWindow.IsChecked     = true; break;
            case 2: TxtThirdWindow.Text   = title; ChkThirdWindow.IsChecked   = true; break;
        }
        _suppressUi = false;
        RefreshPidLabels();
        SaveCurrentGroup();
    }

    // â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    //  GROUP FIELDS
    // â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    private void ChkGroupEnabled_Changed(object s, RoutedEventArgs e)
    {
        if (_suppressUi || _curGroup == null) return;
        _curGroup.Enabled = ChkGroupEnabled.IsChecked == true;
        SaveCurrentGroup();
    }

    private void TxtRepeat_Changed(object s, TextChangedEventArgs e)
    {
        if (_suppressUi || _curGroup == null) return;
        if (int.TryParse(TxtRepeat.Text, out int v))
            _curGroup.Primary.RepeatCount = Math.Max(0, v);
        SaveCurrentGroup();
    }

    private void TxtSpeed_Changed(object s, TextChangedEventArgs e)
    {
        if (_suppressUi || _curGroup == null) return;
        if (double.TryParse(TxtSpeed.Text, System.Globalization.NumberStyles.Float,
            System.Globalization.CultureInfo.InvariantCulture, out double v))
        {
            double spd = Math.Max(0.01, v);
            foreach (var lane in _curGroup.Lanes) lane.SpeedMultiplier = spd;
        }
        SaveCurrentGroup();
    }

    // â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    //  PID LABELS
    // â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    private void RefreshPidLabels()
    {
        if (_curGroup == null)
        {
            LblPrimaryPid.Text = LblSecPid.Text = LblThirdPid.Text = "";
            return;
        }

        var claimed = new HashSet<IntPtr>();

        LblPrimaryPid.Text = BuildPidText(_curGroup.Primary, claimed);
        if (_curGroup.Primary.UseTargetWindow)
        {
            var h = Win32.ResolveWindowHwnd(_curGroup.Primary.TargetWindowTitle,
                        _curGroup.Primary.TargetWindowInstance, null);
            if (h != IntPtr.Zero) claimed.Add(h);
        }

        LblSecPid.Text = BuildPidText(_curGroup.Secondary, claimed);
        if (_curGroup.Secondary.UseTargetWindow)
        {
            var h = Win32.ResolveWindowHwnd(_curGroup.Secondary.TargetWindowTitle,
                        _curGroup.Secondary.TargetWindowInstance, claimed);
            if (h != IntPtr.Zero) claimed.Add(h);
        }

        LblThirdPid.Text = BuildPidText(_curGroup.Third, claimed);
    }

    private static string BuildPidText(Macro lane, HashSet<IntPtr> claimed)
    {
        if (!lane.UseTargetWindow || string.IsNullOrEmpty(lane.TargetWindowTitle))
            return "";

        var hwnd = Win32.ResolveWindowHwnd(lane.TargetWindowTitle, lane.TargetWindowInstance, claimed);
        if (hwnd == IntPtr.Zero)
        {
            string tag = lane.TargetWindowInstance > 0 ? $" (#{lane.TargetWindowInstance + 1})" : "";
            return $"âš  Not found{tag}";
        }

        Win32.GetWindowThreadProcessId(hwnd, out uint pid);
        string instTag = lane.TargetWindowInstance > 0 ? $"  #{lane.TargetWindowInstance + 1}" : "";
        return $"PID {pid}  HWND 0x{hwnd:X8}{instTag}";
    }

    // â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    //  RUNS LABEL
    // â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    private void RefreshRunsLabel()
    {
        int total = _groups.Sum(g => g.Lanes.Sum(l => l.RunCount));
        RunsLabel.Text  = $"Runs: {total}";
        ExecLabel.Text  = $"Executions: {total}";
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
        if (sender is FrameworkElement fe)
            ctx.PlacementTarget = fe;
        ctx.IsOpen = true;
    }

    // â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    //  TOAST
    // â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    private void SwitchLaneWithToast(int laneIndex)
    {
        LaneTabs.SelectedIndex = laneIndex;
        string name = laneIndex switch { 1 => "Secondary", 2 => "Third", _ => "Primary" };
        ShowToast($"ðŸŽ™  {name} ready â€” press  Ctrl+R  to record\nThis lane has no events yet.", 3000);
    }

    private void ShowToast(string message, int durationMs)
    {
        ToastText.Text      = message;
        ToastBorder.Opacity = 0;
        ToastBorder.Visibility = Visibility.Visible;

        var fadeIn = new DoubleAnimation(0, 1, TimeSpan.FromMilliseconds(300));
        ToastBorder.BeginAnimation(OpacityProperty, fadeIn);

        var timer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(durationMs) };
        timer.Tick += (_, _) =>
        {
            timer.Stop();
            var fadeOut = new DoubleAnimation(1, 0, TimeSpan.FromMilliseconds(400));
            fadeOut.Completed += (_, _) => ToastBorder.Visibility = Visibility.Collapsed;
            ToastBorder.BeginAnimation(OpacityProperty, fadeOut);
        };
        timer.Start();
    }

    // â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    //  ACTIVITY LOG
    // â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    private void Log(string msg)
    {
        string line = $"[{DateTime.Now:HH:mm:ss}]  {msg}";
        Dispatcher.InvokeAsync(() =>
        {
            ActivityLog.Items.Add(line);
            ActivityLog.ScrollIntoView(line);
        });
    }

    private void BtnClearLog_Click(object s, RoutedEventArgs e) => ActivityLog.Items.Clear();

    // â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    //  TOP BAR BUTTONS
    // â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    private void BtnRecord_Click(object s, RoutedEventArgs e) => ToggleRecord();
    private void BtnPlay_Click(object s, RoutedEventArgs e)   => TogglePlay();
    private void BtnStop_Click(object s, RoutedEventArgs e)   => StopRecord_And_Play();

    private void BtnSettings_Click(object s, RoutedEventArgs e)
    {
        MessageBox.Show("Settings dialog coming soon.", "Settings",
            MessageBoxButton.OK, MessageBoxImage.Information);
    }

    // â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    //  HELPERS
    // â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    private void SetStatus(string msg) => StatusLabel.Text = msg;

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

    private static string? InputDialog(string title, string prompt, string? defaultValue = "")
    {
        var dlg  = new Window
        {
            Title = title, Width = 360, Height = 140,
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
