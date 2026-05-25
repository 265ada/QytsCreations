using System.Diagnostics;
using System.IO;
using System.Net.Http;
using Newtonsoft.Json;

namespace QytCroRec.Services;

/// <summary>Checks GitHub for newer version, downloads exe, swaps it via hidden batch.</summary>
public class AutoUpdateService
{
    private const string VersionUrl =
        "https://raw.githubusercontent.com/265ada/QytsCreations/main/QytsCreations_CS/version.json";
    private const string ExeUrl =
        "https://github.com/265ada/QytsCreations/releases/latest/download/QytCroRec-CS.exe";

    public string CurrentVersion { get; }
    public event Action<string>? StatusUpdate;
    public event Action<string, string>? UpdateAvailable; // (newVer, changelog)

    public AutoUpdateService(string currentVersion)
    {
        CurrentVersion = currentVersion;
    }

    public async Task<(bool isNewer, string newVer, string changelog)> CheckAsync()
    {
        try
        {
            using var http = new HttpClient { Timeout = TimeSpan.FromSeconds(8) };
            var json = await http.GetStringAsync(VersionUrl);
            var info = JsonConvert.DeserializeObject<Dictionary<string, string>>(json) ?? new();
            string newVer    = info.GetValueOrDefault("version",   "");
            string changelog = info.GetValueOrDefault("changelog", "");
            if (string.IsNullOrEmpty(newVer)) return (false, "", "");
            bool newer = CompareVersions(newVer, CurrentVersion) > 0;
            if (newer) UpdateAvailable?.Invoke(newVer, changelog);
            return (newer, newVer, changelog);
        }
        catch (Exception ex)
        {
            StatusUpdate?.Invoke($"Update check failed: {ex.Message}");
            return (false, "", "");
        }
    }

    public async Task<bool> DownloadAndApplyAsync()
    {
        try
        {
            StatusUpdate?.Invoke("Downloading update…");
            string currentExe = Process.GetCurrentProcess().MainModule!.FileName;
            string tmpDir     = Path.GetTempPath();
            string newExe     = Path.Combine(tmpDir, "QytCroRec-CS.new.exe");
            string backupExe  = currentExe + ".bak";

            using var http = new HttpClient { Timeout = TimeSpan.FromMinutes(5) };
            var bytes = await http.GetByteArrayAsync(ExeUrl);
            await File.WriteAllBytesAsync(newExe, bytes);

            string batchPath = Path.Combine(tmpDir, $"_qyt_cs_upd_{Process.GetCurrentProcess().Id}.bat");
            string batch = BuildUpdateBatch(currentExe, newExe, backupExe);
            await File.WriteAllTextAsync(batchPath, batch);

            // Launch hidden via PowerShell Start-Process so cmd window is invisible on Win11
            string psCmd =
                $"Start-Process -FilePath 'cmd.exe' " +
                $"-ArgumentList '/c \"{batchPath}\"' " +
                $"-WindowStyle Hidden";
            Process.Start(new ProcessStartInfo
            {
                FileName        = "powershell.exe",
                Arguments       = $"-NoProfile -NonInteractive -WindowStyle Hidden -Command \"{psCmd}\"",
                UseShellExecute = false,
                CreateNoWindow  = true,
                WindowStyle     = ProcessWindowStyle.Hidden
            });

            // Exit so batch can swap files
            Environment.Exit(0);
            return true;
        }
        catch (Exception ex)
        {
            StatusUpdate?.Invoke($"Update failed: {ex.Message}");
            return false;
        }
    }

    private static string BuildUpdateBatch(string currentExe, string newExe, string backupExe)
    {
        string pid = Process.GetCurrentProcess().Id.ToString();
        return
            "@echo off\r\n" +
            $"set EXE_RAW={currentExe}\r\n" +
            $"set NEW_RAW={newExe}\r\n" +
            $"set BAK_RAW={backupExe}\r\n" +
            ":WAIT_PROC\r\n" +
            "set CHK=%TEMP%\\_qyt_cs_chk.txt\r\n" +
            $"tasklist /FI \"PID eq {pid}\" /FO CSV 2>nul > %CHK%\r\n" +
            $"find /I \"{pid}\" %CHK% >nul 2>&1\r\n" +
            "if not errorlevel 1 ( timeout /t 1 /nobreak >nul & goto WAIT_PROC )\r\n" +
            "del /f /q %CHK% >nul 2>&1\r\n" +
            "if exist \"%EXE_RAW%\" copy /Y \"%EXE_RAW%\" \"%BAK_RAW%\" >nul 2>&1\r\n" +
            "move /Y \"%NEW_RAW%\" \"%EXE_RAW%\" >nul 2>&1\r\n" +
            "if errorlevel 1 ( " +
                "powershell -NoProfile -NonInteractive -WindowStyle Hidden -Command " +
                "\"Start-Process -FilePath '%BAK_RAW%'\" & exit /b 1 )\r\n" +
            "powershell -NoProfile -NonInteractive -WindowStyle Hidden -Command " +
            "\"Start-Process -FilePath '%EXE_RAW%'\" >nul 2>&1\r\n" +
            "del \"%~f0\" >nul 2>&1\r\n";
    }

    private static int CompareVersions(string a, string b)
    {
        int[] PA = a.Split('.').Select(s => int.TryParse(s, out int n) ? n : 0).ToArray();
        int[] PB = b.Split('.').Select(s => int.TryParse(s, out int n) ? n : 0).ToArray();
        int len  = Math.Max(PA.Length, PB.Length);
        for (int i = 0; i < len; i++)
        {
            int va = i < PA.Length ? PA[i] : 0;
            int vb = i < PB.Length ? PB[i] : 0;
            if (va != vb) return va.CompareTo(vb);
        }
        return 0;
    }
}
