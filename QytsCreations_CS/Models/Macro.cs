using Newtonsoft.Json;

namespace QytCroRec.Models;

public class Macro
{
    [JsonProperty("id")]
    public string Id { get; set; } = Guid.NewGuid().ToString();

    [JsonProperty("name")]
    public string Name { get; set; } = "New Macro";

    [JsonProperty("events")]
    public List<MacroEvent> Events { get; set; } = new();

    [JsonProperty("trigger_hotkey")]
    public string TriggerHotkey { get; set; } = "";

    [JsonProperty("repeat_count")]
    public int RepeatCount { get; set; } = 1;   // 0 = loop forever

    [JsonProperty("speed_multiplier")]
    public double SpeedMultiplier { get; set; } = 1.0;

    [JsonProperty("record_mouse_move")]
    public bool RecordMouseMove { get; set; } = true;

    [JsonProperty("target_window_title")]
    public string TargetWindowTitle { get; set; } = "";

    [JsonProperty("target_window_instance")]
    public int TargetWindowInstance { get; set; } = 0;

    [JsonProperty("use_target_window")]
    public bool UseTargetWindow { get; set; } = false;

    [JsonProperty("input_backend")]
    public string InputBackend { get; set; } = "auto";

    [JsonProperty("lane_enabled")]
    public bool LaneEnabled { get; set; } = true;

    [JsonProperty("run_count")]
    public int RunCount { get; set; } = 0;

    [JsonProperty("created_at")]
    public double CreatedAt { get; set; } = DateTimeOffset.UtcNow.ToUnixTimeSeconds();

    // Pixel guard
    [JsonProperty("pixel_guard_enabled")]
    public bool PixelGuardEnabled { get; set; } = false;

    [JsonProperty("pixel_guard_red_flags")]
    public List<string> PixelGuardRedFlags { get; set; } = new();

    [JsonProperty("pixel_guard_correction_key")]
    public string PixelGuardCorrectionKey { get; set; } = "b";

    [JsonProperty("pixel_guard_correction_macro")]
    public string PixelGuardCorrectionMacro { get; set; } = "";

    [JsonProperty("pixel_guard_cap_x_pct")]
    public double PixelGuardCapXPct { get; set; } = 0.75;

    [JsonProperty("pixel_guard_cap_y_pct")]
    public double PixelGuardCapYPct { get; set; } = 0.02;

    [JsonProperty("pixel_guard_cap_w_pct")]
    public double PixelGuardCapWPct { get; set; } = 0.24;

    [JsonProperty("pixel_guard_cap_h_pct")]
    public double PixelGuardCapHPct { get; set; } = 0.06;

    public Macro Clone()
    {
        var clone = (Macro)MemberwiseClone();
        clone.Id = Guid.NewGuid().ToString();
        clone.Name = $"{Name} (copy)";
        clone.Events = Events.Select(e => new MacroEvent
        {
            Timestamp = e.Timestamp,
            EventType = e.EventType,
            Data = new Dictionary<string, object?>(e.Data),
            PixelGuard = e.PixelGuard
        }).ToList();
        return clone;
    }
}
