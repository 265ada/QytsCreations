using System.Diagnostics;
using System.IO;
using System.Windows;

namespace QytCroRec.Dialogs;

public partial class FeedbackDialog : Window
{
    private readonly string _diagLogPath;
    private readonly string _version;

    public FeedbackDialog(string diagLogPath, string version)
    {
        InitializeComponent();
        _diagLogPath = diagLogPath;
        _version     = version;
    }

    private string BuildBody()
    {
        var sb = new System.Text.StringBuilder();
        sb.AppendLine("**Version:** " + _version + " (C# build)");
        sb.AppendLine("**OS:** " + Environment.OSVersion);
        sb.AppendLine();
        sb.AppendLine(TxtFeedback.Text);
        if (ChkAttachLog.IsChecked == true)
        {
            sb.AppendLine();
            sb.AppendLine("---");
            sb.AppendLine("**Diag log (last 50 lines):**");
            sb.AppendLine("```");
            try
            {
                if (File.Exists(_diagLogPath))
                {
                    var lines = File.ReadAllLines(_diagLogPath);
                    int from = Math.Max(0, lines.Length - 50);
                    for (int i = from; i < lines.Length; i++) sb.AppendLine(lines[i]);
                }
            }
            catch { }
            sb.AppendLine("```");
        }
        return sb.ToString();
    }

    private void BtnIssues_Click(object s, RoutedEventArgs e)
    {
        try
        {
            string url = "https://github.com/265ada/QytsCreations/issues/new"
                + $"?title={Uri.EscapeDataString("[C#] Feedback")}"
                + $"&body={Uri.EscapeDataString(BuildBody())}";
            Process.Start(new ProcessStartInfo { FileName = url, UseShellExecute = true });
        }
        catch (Exception ex) { MessageBox.Show($"Failed: {ex.Message}"); }
    }

    private void BtnCopy_Click(object s, RoutedEventArgs e)
    {
        try { Clipboard.SetText(BuildBody()); MessageBox.Show("Copied!"); } catch { }
    }

    private void BtnClose_Click(object s, RoutedEventArgs e) => Close();
}
