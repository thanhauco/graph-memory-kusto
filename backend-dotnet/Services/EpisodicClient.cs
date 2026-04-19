using Npgsql;

namespace IcM.MemoryOrchestrator.Services;

public sealed class EpisodicClient
{
    private readonly string _conn;
    public EpisodicClient(IConfiguration cfg)
        => _conn = cfg["Episodic:Dsn"]
                   ?? Environment.GetEnvironmentVariable("EPISODIC_DB_DSN")
                   ?? "Host=localhost;Username=memuser;Password=mempass;Database=memdb";

    public async Task<IReadOnlyList<Dictionary<string, object?>>> ListAsync(string? incident)
    {
        await using var c = new NpgsqlConnection(_conn);
        await c.OpenAsync();
        var sql = "SELECT episode_id, incident, query, hop_path, hops, outcome, confidence, tag " +
                  "FROM episodes" + (incident is null ? "" : " WHERE incident=@i") +
                  " ORDER BY created_at DESC LIMIT 50";
        await using var cmd = new NpgsqlCommand(sql, c);
        if (incident is not null) cmd.Parameters.AddWithValue("i", incident);
        var rdr = await cmd.ExecuteReaderAsync();
        var rows = new List<Dictionary<string, object?>>();
        while (await rdr.ReadAsync())
        {
            var row = new Dictionary<string, object?>();
            for (var i = 0; i < rdr.FieldCount; i++) row[rdr.GetName(i)] = rdr.IsDBNull(i) ? null : rdr.GetValue(i);
            rows.Add(row);
        }
        return rows;
    }
}
