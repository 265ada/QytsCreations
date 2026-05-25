using System.IO;
using Newtonsoft.Json;
using QytCroRec.Models;

namespace QytCroRec.Services;

public class StorageService
{
    private readonly string _dir;
    private readonly string _groupsPath;
    private readonly string _shortcutsPath;

    public StorageService()
    {
        _dir = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
            ".macro_recorder");
        Directory.CreateDirectory(_dir);
        _groupsPath   = Path.Combine(_dir, "groups.json");
        _shortcutsPath = Path.Combine(_dir, "shortcuts.json");
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
            Console.WriteLine($"[storage] LoadGroups error: {ex.Message}");
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
            Console.WriteLine($"[storage] SaveGroups error: {ex.Message}");
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
}
