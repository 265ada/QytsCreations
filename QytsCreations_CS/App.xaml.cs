using System.IO;
using System.Windows;
using System.Windows.Threading;

namespace QytCroRec;

public partial class App : Application
{
    protected override void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);

        // Single-window app — quit when the main window closes.
        ShutdownMode = ShutdownMode.OnMainWindowClose;

        // UI-thread exceptions: show, log, then DIE. Don't keep the process zombied.
        DispatcherUnhandledException += OnDispatcherException;

        // Non-UI-thread exceptions: log and force-exit.
        AppDomain.CurrentDomain.UnhandledException += (_, args) =>
        {
            try
            {
                string txt = args.ExceptionObject?.ToString() ?? "(unknown)";
                LogCrash(txt);
                MessageBox.Show("Fatal background error — see crash.log\n\n" + txt,
                    "QytCroRec crashed", MessageBoxButton.OK, MessageBoxImage.Error);
            }
            catch { }
            Environment.Exit(1);
        };

        // Catch unobserved Task exceptions too
        System.Threading.Tasks.TaskScheduler.UnobservedTaskException += (_, args) =>
        {
            try { LogCrash("UnobservedTask: " + args.Exception); } catch { }
            args.SetObserved();
        };
    }

    private void OnDispatcherException(object sender, DispatcherUnhandledExceptionEventArgs e)
    {
        try
        {
            LogCrash(e.Exception.ToString());
            MessageBox.Show(e.Exception.ToString(), "Unhandled Error",
                MessageBoxButton.OK, MessageBoxImage.Error);
        }
        catch { }
        e.Handled = true;       // suppress the WPF "stopped working" dialog
        Shutdown(1);            // and end the app cleanly
    }

    protected override void OnExit(ExitEventArgs e)
    {
        // Force every background timer/thread/notifyicon dead.
        try { foreach (Window w in Windows) w.Close(); } catch { }
        base.OnExit(e);
        // Belt + braces: if anything still has the process alive after Shutdown, kill it.
        Task.Run(() => { Thread.Sleep(800); Environment.Exit(e.ApplicationExitCode); });
    }

    private static void LogCrash(string msg)
    {
        try
        {
            string dir = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
                ".macro_recorder");
            Directory.CreateDirectory(dir);
            File.AppendAllText(Path.Combine(dir, "crash.log"),
                $"[{DateTime.Now:yyyy-MM-dd HH:mm:ss}] {msg}{Environment.NewLine}{Environment.NewLine}");
        }
        catch { }
    }
}
