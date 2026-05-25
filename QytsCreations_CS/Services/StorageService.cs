using System.IO;
using Newtonsoft.Json;
using QytCroRec.Models;

namespace QytCroRec.Services;

public class StorageService
{
    public readonly string Dir;
    private readonly string _groupsPath;
    private readonly string _shortcutsPath;
    private readonly string _guardMacrosPath;
    private readonly string _settingsPath;
    private readonly string _diagLogPath;
    private readonly string _crashLogPath;

    public string DiagLogPath  => _diagLogPath;
    public string CrashLogPath => _crashLogPath;

    public StorageService()
    {
        Dir = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
            ".macro_recorder");
        Directory.CreateDirectory(Dir);
        _groupsPath      = Path.Combine(Dir, "groups.json");
        _shortcutsPath   = Path.Combine(Dir, "shortcuts.json");
        _guardMacrosPath = Path.Combine(Dir, "guard_macros.json");
        _settingsPath    = Path.Combine(Dir, "settings_cs.json");  // CS-specific
        _diagLogPath     = Path.Combine(Dir, "diag.log");
        _crashLogPath    = Path.Combine(Dir, "crash.log");
    }

    // ── Groups ────────────────────────────────────────────────────────────────

    public List<MacroGroup> LoadGroups()
    {
        if (!File.Exists(_groupsPath)) return new();
        try
        {
            var json = File.ReadAllText(_groupsPath);
            var groups = JsonConvert.DeserializeObject<List<MacroGroup>>(json) ?? new();
            foreach (var g in groups) g.EnsureLanes();
            return groups;
        }
        catch (Exception ex)
        {
            DiagLog($"LoadGroups error: {ex.Message}");
            return new();
        }
    }

    public void SaveGroups(IEnumerable<MacroGroup> groups)
    {
        var tmp = _groupsPath + ".tmp";
        var bak = _groupsPath + ".bak.json";
        try
        {
            var json = JsonConvert.SerializeObject(groups, Formatting.Indented);
            File.WriteAllText(tmp, json);
            if (File.Exists(_groupsPath))
                File.Copy(_groupsPath, bak, overwrite: true);
            File.Move(tmp, _groupsPath, overwrite: true);
        }
        catch (Exception ex)
        {
            DiagLog($"SaveGroups error: {ex.Message}");
        }
    }

    public Task SaveGroupsAsync(IEnumerable<MacroGroup> groups) =>
        Task.Run(() => SaveGroups(groups));

    // ── Shortcuts ─────────────────────────────────────────────────────────────

    public Dictionary<string, string> LoadShortcuts()
    {
        if (!File.Exists(_shortcutsPath)) return new();
        try
        {
            var json = File.ReadAllText(_shortcutsPath);
            return JsonConvert.DeserializeObject<Dictionary<string, string>>(json) ?? new();
        }
        catch { return new(); }
    }

    public void SaveShortcuts(Dictionary<string, string> shortcuts)
    {
        File.WriteAllText(_shortcutsPath,
            JsonConvert.SerializeObject(shortcuts, Formatting.Indented));
    }

    // ── Guard Macros ──────────────────────────────────────────────────────────

    public List<Macro> LoadGuardMacros()
    {
        if (!File.Exists(_guardMacrosPath)) return new();
        try
        {
            var json = File.ReadAllText(_guardMacrosPath);
            return JsonConvert.DeserializeObject<List<Macro>>(json) ?? new();
        }
        catch (Exception ex)
        {
            DiagLog($"LoadGuardMacros error: {ex.Message}");
            return new();
        }
    }

    public void SaveGuardMacros(IEnumerable<Macro> macros)
    {
        var tmp = _guardMacrosPath + ".tmp";
        try
        {
            var json = JsonConvert.SerializeObject(macros, Formatting.Indented);
            File.WriteAllText(tmp, json);
            File.Move(tmp, _guardMacrosPath, overwrite: true);
        }
        catch (Exception ex)
        {
            DiagLog($"SaveGuardMacros error: {ex.Message}");
        }
    }

    // ── App settings (C#-specific) ────────────────────────────────────────────

    public AppSettings LoadSettings()
    {
        if (!File.Exists(_settingsPath)) return new();
        try
        {
            return JsonConvert.DeserializeObject<AppSettings>(File.ReadAllText(_settingsPath))
                ?? new();
        }
        catch { return new(); }
    }

    public void SaveSettings(AppSettings s)
    {
        try { File.WriteAllText(_settingsPath, JsonConvert.SerializeObject(s, Formatting.Indented)); }
        catch (Exception ex) { DiagLog($"SaveSettings error: {ex.Message}"); }
    }

    // ── Diag / Crash logs ─────────────────────────────────────────────────────

    private static readonly object _logLock = new();
    public void DiagLog(string msg)
    {
        try
        {
            lock (_logLock)
            {
                File.AppendAllText(_diagLogPath,
                    $"[{DateTime.Now:yyyy-MM-dd HH:mm:ss}] {msg}{Environment.NewLine}");
            }
        }
        catch { }
    }

    public void CrashLog(string msg)
    {
        try
        {
            lock (_logLock)
            {
                File.AppendAllText(_crashLogPath,
                    $"[{DateTime.Now:yyyy-MM-dd HH:mm:ss}] {msg}{Environment.NewLine}");
            }
        }
        catch { }
    }
}

public class AppSettings
{
    public bool   AutoUpdate         { get; set; } = true;
    public bool   MinimizeToTray     { get; set; } = false;
    public bool   StartMinimized     { get; set; } = false;
    public bool   TtsEnabled         { get; set; } = true;
    public bool   RecordMouseMove    { get; set; } = true;
    public string PreferredBackend   { get; set; } = "auto";
}
