using System.IO;
using System.Windows;
using System.Windows.Threading;

namespace QytCroRec;

public partial class App : Application
{
    private static SplashWindow? _splash;
    private static Dispatcher?   _splashDispatcher;

    protected override void OnStartup(StartupEventArgs e)
    {
        // ─── Splash on its OWN STA thread + dispatcher ────────────────────
        // Shows the moment managed code starts running (~50 ms after the
        // .NET host hands off). Stays responsive while the main thread is
        // busy loading WPF/JITting/extracting native libs.
        var splashReady = new System.Threading.ManualResetEventSlim(false);
        var splashThread = new System.Threading.Thread(() =>
        {
            try
            {
                _splash = new SplashWindow();
                _splashDispatcher = _splash.Dispatcher;
                _splash.Loaded += (_, _) => splashReady.Set();
                _splash.Show();
                Dispatcher.Run();
            }
            catch (Exception ex) { LogCrash("splash thread: " + ex); splashReady.Set(); }
        })
        {
            IsBackground = true,
            Name         = "SplashThread"
        };
        splashThread.SetApartmentState(System.Threading.ApartmentState.STA);
        splashThread.Start();
        splashReady.Wait(3000);

        // Safety net: even if MainWindow.OnLoaded never reaches CloseSplash
        // (rare — startup exception etc.), force-close after 15 seconds so the
        // splash can never stick around indefinitely.
        Task.Run(async () =>
        {
            await Task.Delay(TimeSpan.FromSeconds(15));
            if (_splash != null) CloseSplash();
        });
        // ──────────────────────────────────────────────────────────────────

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

    /// <summary>Called by MainWindow when it's actually visible + interactive.
    /// Synchronous close-then-shutdown so the splash actually disappears.</summary>
    public static void CloseSplash()
    {
        var splash     = _splash;
        var dispatcher = _splashDispatcher;
        _splash = null;
        _splashDispatcher = null;
        if (splash == null || dispatcher == null) return;

        try
        {
            // Step 1: hide + close the window ON the splash thread, BLOCKING.
            dispatcher.Invoke(() =>
            {
                try { splash.Topmost = false; } catch { }
                try { splash.Hide();           } catch { }
                try { splash.Close();          } catch { }
            });
        }
        catch { }

        // Step 2: end the splash thread's dispatcher loop so the thread exits.
        try { dispatcher.InvokeShutdown(); } catch { }
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
