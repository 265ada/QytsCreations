using System.Diagnostics;
using System.IO;
using System.Windows;

namespace QytCroRec.Dialogs;

public partial class LogViewerDialog : Window
{
    private readonly string _path;

    public LogViewerDialog(string title, string filePath)
    {
        InitializeComponent();
        Title       = title;
        HeaderText.Text = $"{title} — {filePath}";
        _path       = filePath;
        Loaded     += (_, _) => Reload();
    }

    private void Reload()
    {
        try
        {
            LogBox.Text = File.Exists(_path)
                ? File.ReadAllText(_path)
                : "(log file does not exist yet)";
            LogBox.ScrollToEnd();
        }
        catch (Exception ex) { LogBox.Text = $"Read error: {ex.Message}"; }
    }

    private void BtnRefresh_Click(object s, RoutedEventArgs e) => Reload();

    private void BtnOpenFolder_Click(object s, RoutedEventArgs e)
    {
        try
        {
            string dir = Path.GetDirectoryName(_path) ?? "";
            if (Directory.Exists(dir)) Process.Start("explorer.exe", dir);
        }
        catch { }
    }

    private void BtnClear_Click(object s, RoutedEventArgs e)
    {
        if (MessageBox.Show("Clear this log file?", "Confirm",
            MessageBoxButton.YesNo) != MessageBoxResult.Yes) return;
        try { if (File.Exists(_path)) File.WriteAllText(_path, ""); Reload(); }
        catch (Exception ex) { MessageBox.Show($"Failed: {ex.Message}"); }
    }

    private void BtnCopy_Click(object s, RoutedEventArgs e)
    {
        try { Clipboard.SetText(LogBox.Text ?? ""); }
        catch { }
    }

    private void BtnClose_Click(object s, RoutedEventArgs e) => Close();
}
