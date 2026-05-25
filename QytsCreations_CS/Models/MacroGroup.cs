using Newtonsoft.Json;

namespace QytCroRec.Models;

public class MacroGroup
{
    [JsonProperty("id")]
    public string Id { get; set; } = Guid.NewGuid().ToString();

    [JsonProperty("name")]
    public string Name { get; set; } = "New Group";

    [JsonProperty("lanes")]
    public List<Macro> Lanes { get; set; } = new();

    [JsonProperty("shared_guard_lane")]
    public int SharedGuardLane { get; set; } = -1;

    [JsonProperty("shared_guard_users")]
    public int SharedGuardUsers { get; set; } = 0b111;

    [JsonProperty("enabled")]
    public bool Enabled { get; set; } = true;

    [JsonIgnore]
    public string StatusTag => Enabled ? "●" : "";

    /// <summary>Ensure exactly 3 lanes exist (Primary, Secondary, Third).</summary>
    public void EnsureLanes()
    {
        while (Lanes.Count < 3)
        {
            string[] names = ["Primary", "Secondary", "Third"];
            Lanes.Add(new Macro
            {
                Name = names[Lanes.Count],
                LaneEnabled = Lanes.Count == 0
            });
        }
    }

    public Macro Primary   => Lanes[0];
    public Macro Secondary => Lanes.Count > 1 ? Lanes[1] : Primary;
    public Macro Third     => Lanes.Count > 2 ? Lanes[2] : Primary;

    public MacroGroup Clone()
    {
        return new MacroGroup
        {
            Id      = Guid.NewGuid().ToString(),
            Name    = Name,
            Enabled = Enabled,
            SharedGuardLane  = SharedGuardLane,
            SharedGuardUsers = SharedGuardUsers,
            Lanes   = Lanes.Select(l => l.Clone()).ToList()
        };
    }
}
