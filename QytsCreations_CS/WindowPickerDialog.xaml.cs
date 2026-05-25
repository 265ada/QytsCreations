using System.Windows;
using System.Windows.Controls;
using QytCroRec.Services;

namespace QytCroRec;

public partial class WindowPickerDialog : Window
{
    public IntPtr  SelectedHwnd  { get; private set; }
    public string? SelectedTitle { get; private set; }

    public WindowPickerDialog()
    {
        InitializeComponent();
        Loaded += (_, _) => Refresh();
    }

    private void Refresh()
    {
        var rows = Win32.FindWindowsByTitle("")
            .Select(x => new WindowRow
            {
                Title   = x.title,
                Pid     = x.pid,
                Hwnd    = x.hwnd,
                HwndHex = $"0x{x.hwnd:X8}"
            })
            .OrderBy(r => r.Title)
            .ToList();
        WindowGrid.ItemsSource = rows;
    }

    private void Confirm()
    {
        if (WindowGrid.SelectedItem is WindowRow row)
        {
            SelectedHwnd  = row.Hwnd;
            SelectedTitle = row.Title;
            DialogResult  = true;
            Close();
        }
    }

    private void BtnRefresh_Click(object s, RoutedEventArgs e) => Refresh();
    private void BtnOk_Click(object s, RoutedEventArgs e)      => Confirm();
    private void BtnCancel_Click(object s, RoutedEventArgs e)  => Close();
    private void WindowGrid_MouseDoubleClick(object s, System.Windows.Input.MouseButtonEventArgs e) => Confirm();
}

file class WindowRow
{
    public string  Title   { get; set; } = "";
    public uint    Pid     { get; set; }
    public IntPtr  Hwnd    { get; set; }
    public string  HwndHex { get; set; } = "";
}
