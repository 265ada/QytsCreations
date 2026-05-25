namespace QytCroRec.Services;

public static class Shortcuts
{
    public record Def(string Action, string Label, string Default);

    public static readonly List<Def> Defs = new()
    {
        new("new_macro",      "New Macro",                  "Ctrl+N"),
        new("dup_macro",      "Duplicate Macro",             "Ctrl+D"),
        new("del_macro",      "Delete Macro",                "Ctrl+Delete"),
        new("undo_delete",    "Undo Delete Macro",           "Ctrl+Z"),
        new("toggle_record",  "Record / Stop Recording",     "Ctrl+R"),
        new("play",           "Play Macro",                  "F5"),
        new("stop",           "Stop Current Macro",          "F6"),
        new("stop_all",       "Stop All Macros",             "End"),
        new("play_chain",     "Play Chain (all lanes)",      "F8"),
        new("clear_events",   "Clear All Events",            "Ctrl+L"),
        new("capture_window", "Capture Target Window (3 s)", "Ctrl+W"),
        new("del_events",     "Delete Selected Events",      "Delete"),
    };

    public static Dictionary<string, string> Defaults =>
        Defs.ToDictionary(d => d.Action, d => d.Default);

    public static string Get(Dictionary<string, string> cfg, string action) =>
        cfg.TryGetValue(action, out var k) && !string.IsNullOrWhiteSpace(k)
            ? k
            : Defaults.GetValueOrDefault(action, "");
}
