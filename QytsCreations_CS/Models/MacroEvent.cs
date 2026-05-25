using Newtonsoft.Json;

namespace QytCroRec.Models;

public class MacroEvent
{
    [JsonProperty("timestamp")]
    public double Timestamp { get; set; }

    [JsonProperty("event_type")]
    public string EventType { get; set; } = "";

    [JsonProperty("data")]
    public Dictionary<string, object?> Data { get; set; } = new();

    [JsonProperty("pixel_guard")]
    public bool PixelGuard { get; set; }

    [Newtonsoft.Json.JsonIgnore]
    public string DataSummary =>
        string.Join("  ", Data.Select(kv => $"{kv.Key}={kv.Value}"));
}
