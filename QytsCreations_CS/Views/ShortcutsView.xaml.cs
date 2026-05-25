using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Runtime.CompilerServices;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using QytCroRec.Services;

namespace QytCroRec.Views;

public partial class ShortcutsView : UserControl
{
    public event Action<Dictionary<string, string>>? Changed;

    public ObservableCollection<ShortcutRow> Rows { get; } = new();

    public ShortcutsView()
    {
        InitializeComponent();
        ShortcutList.ItemsSource = Rows;
    }

    public void LoadConfig(Dictionary<string, string> cfg)
    {
        Rows.Clear();
        foreach (var def in Shortcuts.Defs)
        {
            var row = new ShortcutRow
            {
                Action = def.Action,
                Label  = def.Label,
                Key    = cfg.TryGetValue(def.Action, out var k) ? k : def.Default
            };
            row.PropertyChanged += (_, _) => FireChanged();
            Rows.Add(row);
        }
    }

    private void FireChanged() =>
        Changed?.Invoke(Rows.ToDictionary(r => r.Action, r => r.Key));

    private void ShortcutBox_GotFocus(object s, RoutedEventArgs e)
    {
        if (s is TextBox tb) HintText.Text = "Press combo to bind. ESC to cancel. BACKSPACE to clear.";
    }

    private void ShortcutBox_KeyDown(object s, KeyEventArgs e)
    {
        if (s is not TextBox tb) return;
        e.Handled = true;

        if (e.Key == Key.Escape) { Keyboard.ClearFocus(); return; }
        if (e.Key == Key.Back)
        {
            tb.Text = "";
            return;
        }

        // Skip standalone modifier presses
        if (e.Key is Key.LeftCtrl or Key.RightCtrl
            or Key.LeftAlt or Key.RightAlt
            or Key.LeftShift or Key.RightShift
            or Key.LWin or Key.RWin) return;

        var mods = new List<string>();
        if (Keyboard.Modifiers.HasFlag(ModifierKeys.Control)) mods.Add("Ctrl");
        if (Keyboard.Modifiers.HasFlag(ModifierKeys.Alt))     mods.Add("Alt");
        if (Keyboard.Modifiers.HasFlag(ModifierKeys.Shift))   mods.Add("Shift");
        if (Keyboard.Modifiers.HasFlag(ModifierKeys.Windows)) mods.Add("Win");

        string keyName = KeyToName(e.Key);
        if (string.IsNullOrEmpty(keyName)) return;
        mods.Add(keyName);
        tb.Text = string.Join("+", mods);
    }

    private static string KeyToName(Key k)
    {
        if (k >= Key.F1 && k <= Key.F24) return $"F{(int)(k - Key.F1) + 1}";
        if (k >= Key.D0 && k <= Key.D9)  return $"D{(int)(k - Key.D0)}";
        if (k >= Key.A  && k <= Key.Z)   return k.ToString();
        return k switch
        {
            Key.End => "End", Key.Home => "Home",
            Key.PageUp => "PageUp", Key.PageDown => "PageDown",
            Key.Insert => "Insert", Key.Delete => "Delete",
            Key.Up => "Up", Key.Down => "Down", Key.Left => "Left", Key.Right => "Right",
            Key.Space => "Space", Key.Enter => "Enter", Key.Tab => "Tab",
            Key.Back => "Backspace", Key.Escape => "Escape",
            _ => k.ToString()
        };
    }

    private void BtnReset_Click(object s, RoutedEventArgs e)
    {
        if (MessageBox.Show("Reset all shortcuts to defaults?", "Confirm",
            MessageBoxButton.YesNo) != MessageBoxResult.Yes) return;
        foreach (var row in Rows)
            row.Key = Shortcuts.Defaults.GetValueOrDefault(row.Action, "");
        FireChanged();
    }
}

public class ShortcutRow : INotifyPropertyChanged
{
    public string Action { get; set; } = "";
    public string Label  { get; set; } = "";
    private string _key  = "";
    public string Key
    {
        get => _key;
        set { if (_key != value) { _key = value; OnPropertyChanged(); } }
    }
    public event PropertyChangedEventHandler? PropertyChanged;
    void OnPropertyChanged([CallerMemberName] string? n = null) =>
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(n));
}
