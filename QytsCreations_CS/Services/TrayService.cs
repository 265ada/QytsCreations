using System.Drawing;
using System.Windows;
using WinForms = System.Windows.Forms;

namespace QytCroRec.Services;

/// <summary>System tray icon with show/quit/quick-stop menu.</summary>
public class TrayService : IDisposable
{
    private readonly Window _owner;
    private WinForms.NotifyIcon? _icon;
    public event Action? RequestStopAll;

    public TrayService(Window owner)
    {
        _owner = owner;
    }

    public void Initialize()
    {
        _icon = new WinForms.NotifyIcon
        {
            Icon    = LoadAppIcon(),
            Visible = true,
            Text    = "QytCroRec"
        };
        _icon.DoubleClick += (_, _) => Show();

        var menu = new WinForms.ContextMenuStrip();
        menu.Items.Add("Show / Restore", null, (_, _) => Show());
        menu.Items.Add("Stop All Macros", null, (_, _) => RequestStopAll?.Invoke());
        menu.Items.Add(new WinForms.ToolStripSeparator());
        menu.Items.Add("Quit", null, (_, _) =>
        {
            try { _icon!.Visible = false; _icon.Dispose(); } catch { }
            if (_owner is MainWindow mw) mw.ForceClose();
            else { Application.Current.Shutdown(); }
        });
        _icon.ContextMenuStrip = menu;
    }

    public void HideToTray()
    {
        _owner.Hide();
        _icon?.ShowBalloonTip(1500, "QytCroRec", "Running in tray. Double-click icon to restore.",
            WinForms.ToolTipIcon.Info);
    }

    public void Show()
    {
        _owner.Show();
        _owner.WindowState = WindowState.Normal;
        _owner.Activate();
    }

    public void Dispose()
    {
        if (_icon != null) { _icon.Visible = false; _icon.Dispose(); _icon = null; }
    }

    private static Icon LoadAppIcon()
    {
        try
        {
            var uri = new Uri("pack://application:,,,/Resources/icon.ico");
            using var stream = System.Windows.Application.GetResourceStream(uri)?.Stream;
            if (stream != null) return new Icon(stream);
        }
        catch { }
        return SystemIcons.Application;
    }
}
