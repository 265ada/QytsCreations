using System.Diagnostics;
using System.IO;
using System.IO.Pipes;
using System.Runtime.InteropServices;
using System.Text;

namespace QytCroRec.Services;

/// <summary>
/// Talks to the detours hook DLL via named pipe \\.\pipe\macro_hook_&lt;pid&gt;.
/// Reuses the existing injector_x64.exe + dinput_hook_x64.dll from Python project.
/// </summary>
public class DetoursService : IDisposable
{
    public const string PipePrefix = "macro_hook";

    private readonly int _pid;
    private NamedPipeClientStream? _pipe;
    private readonly object _writeLock = new();
    private bool _broken;
    public event Action<string>? StatusEvent;

    public DetoursService(int targetPid)
    {
        _pid = targetPid;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool WaitNamedPipe(string lpNamedPipeName, uint nMaxWait);

    [DllImport("kernel32.dll")]
    private static extern bool IsWow64Process(IntPtr hProcess, out bool wow64Process);

    [DllImport("kernel32.dll")]
    private static extern IntPtr OpenProcess(uint dwAccess, bool bInherit, int pid);

    [DllImport("kernel32.dll")]
    private static extern bool CloseHandle(IntPtr hObject);

    [DllImport("kernel32.dll")]
    private static extern int GetLastError();

    private const uint PROCESS_QUERY_LIMITED_INFORMATION = 0x1000;

    /// <summary>Non-disruptive pipe-exists check (does NOT consume a server slot).</summary>
    public static bool PipeExists(int pid)
    {
        if (pid <= 0) return false;
        string name = $@"\\.\pipe\{PipePrefix}_{pid}";
        if (WaitNamedPipe(name, 0)) return true;
        return GetLastError() == 121; // ERROR_SEM_TIMEOUT — exists but busy
    }

    public static bool Is64BitProcess(int pid)
    {
        IntPtr h = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid);
        if (h == IntPtr.Zero) return true; // assume host arch
        try
        {
            if (!IsWow64Process(h, out bool wow)) return true;
            return !wow;
        }
        finally { CloseHandle(h); }
    }

    public static string HookDir
    {
        get
        {
            string baseDir = AppDomain.CurrentDomain.BaseDirectory;
            // Try alongside exe first, then ../QytsCreations/hooks (dev layout)
            var candidates = new[]
            {
                Path.Combine(baseDir, "hooks", "dinput_hook"),
                Path.Combine(baseDir, "..", "QytsCreations", "hooks", "dinput_hook"),
                Path.Combine(Path.GetDirectoryName(Process.GetCurrentProcess().MainModule!.FileName)!,
                    "hooks", "dinput_hook"),
            };
            foreach (var c in candidates)
                if (Directory.Exists(c)) return Path.GetFullPath(c);
            return candidates[0];
        }
    }

    public static (string dll, string injector, string arch) ArtifactsForPid(int pid)
    {
        bool is64 = Is64BitProcess(pid);
        string suffix = is64 ? "x64" : "x86";
        return (
            Path.Combine(HookDir, $"dinput_hook_{suffix}.dll"),
            Path.Combine(HookDir, $"injector_{suffix}.exe"),
            suffix);
    }

    public (bool ok, string msg) Inject(bool forceReload = false)
    {
        var (dll, injector, arch) = ArtifactsForPid(_pid);
        if (!File.Exists(dll))      return (false, $"{Path.GetFileName(dll)} not found (target is {arch}). Look in {HookDir}");
        if (!File.Exists(injector)) return (false, $"{Path.GetFileName(injector)} not found");

        if (PipeExists(_pid) && !forceReload) return (true, $"Hook already injected ({arch}) — reusing.");

        if (forceReload) Eject(silent: true);

        try
        {
            var psi = new ProcessStartInfo(injector, $"{_pid} \"{dll}\"")
            {
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
            };
            using var p = Process.Start(psi);
            if (p == null) return (false, "Failed to start injector");
            if (!p.WaitForExit(4000)) { try { p.Kill(); } catch { } return (false, "Injector timeout"); }
            if (p.ExitCode != 0)
                return (false, $"Injector exit {p.ExitCode}: {p.StandardError.ReadToEnd()}");
        }
        catch (Exception ex) { return (false, $"Injector launch failed: {ex.Message}"); }

        for (int i = 0; i < 20; i++)
        {
            if (PipeExists(_pid)) return (true, $"Hook injected ({arch}) and pipe is up.");
            Thread.Sleep(100);
        }
        return (false, $"Injector ran but pipe never appeared (arch {arch} — try other build).");
    }

    public (bool ok, string msg) Eject(bool silent = false)
    {
        var (dll, injector, arch) = ArtifactsForPid(_pid);
        if (!File.Exists(injector)) return (false, "Injector not found");
        try
        {
            var psi = new ProcessStartInfo(injector, $"--eject {_pid} \"{Path.GetFileName(dll)}\"")
            {
                UseShellExecute = false,
                CreateNoWindow  = true,
                RedirectStandardOutput = true,
                RedirectStandardError  = true,
            };
            using var p = Process.Start(psi)!;
            if (!p.WaitForExit(4000)) { try { p.Kill(); } catch { } return (false, "Eject timeout"); }
            return (p.ExitCode is 0 or 4, p.StandardOutput.ReadToEnd().Trim());
        }
        catch (Exception ex) { return (false, ex.Message); }
    }

    public bool Open()
    {
        try
        {
            _pipe = new NamedPipeClientStream(".", $"{PipePrefix}_{_pid}",
                PipeDirection.Out, PipeOptions.None);
            _pipe.Connect(2000);
            return true;
        }
        catch (Exception ex)
        {
            StatusEvent?.Invoke($"Pipe open failed: {ex.Message}");
            return false;
        }
    }

    private void Send(string line)
    {
        if (_broken || _pipe == null) return;
        try
        {
            lock (_writeLock)
            {
                var bytes = Encoding.ASCII.GetBytes(line + "\n");
                _pipe.Write(bytes, 0, bytes.Length);
                _pipe.Flush();
            }
        }
        catch (Exception ex)
        {
            _broken = true;
            StatusEvent?.Invoke($"Detours pipe broken: {ex.Message}");
        }
    }

    public void KeyDown(int vk) => Send($"KD {vk}");
    public void KeyUp(int vk)   => Send($"KU {vk}");

    public void MouseMove(int sx, int sy) => Send($"MA {sx} {sy}");

    public void MouseButton(int sx, int sy, string button, bool pressed)
    {
        Send($"MA {sx} {sy}");
        char b = button.ToLowerInvariant() switch { "right" => 'R', "middle" => 'M', _ => 'L' };
        Send($"{(pressed ? "MD" : "MU")} {b}");
    }

    public void MouseWheel(int dx, int dy) => Send($"MW {dx} {dy}");

    public void Dispose()
    {
        try { _pipe?.Dispose(); } catch { }
        _pipe = null;
    }
}
