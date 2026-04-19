using Neo4j.Driver;

namespace IcM.MemoryOrchestrator.Services;

public sealed class GraphClient : IAsyncDisposable
{
    private readonly IDriver _driver;

    public GraphClient(IConfiguration cfg)
    {
        var uri  = cfg["Neo4j:Uri"]  ?? Environment.GetEnvironmentVariable("NEO4J_URI")  ?? "bolt://localhost:7687";
        var user = cfg["Neo4j:User"] ?? Environment.GetEnvironmentVariable("NEO4J_USER") ?? "neo4j";
        var pass = cfg["Neo4j:Pass"] ?? Environment.GetEnvironmentVariable("NEO4J_PASS") ?? "neo4j";
        _driver  = GraphDatabase.Driver(uri, AuthTokens.Basic(user, pass));
    }

    public async Task<IReadOnlyList<Dictionary<string, object?>>> ThreeHopRcaAsync(string incidentId)
    {
        const string q = """
        MATCH (i:Incident {id:$id})-[:AFFECTS]->(s)-[:DEPENDS_ON]->(d)-[:CAUSED_BY]->(r:RootCause)
        RETURN i.id AS incident, s.name AS affected, d.name AS depends_on, r.type AS rootCause
        """;
        await using var session = _driver.AsyncSession();
        var result = await session.RunAsync(q, new { id = incidentId });
        var rows   = await result.ToListAsync();
        return rows.Select(r => r.Values.ToDictionary(k => k.Key, v => (object?)v.Value)).ToList();
    }

    public async Task<IReadOnlyList<Dictionary<string, object?>>> BlastRadiusAsync(string id, int maxHops)
    {
        var q = $"""
        MATCH path=(i:Incident {{id:$id}})-[:AFFECTS*1..{Math.Min(maxHops, 6)}]->(s)
        RETURN DISTINCT s.name AS service, length(path) AS hops ORDER BY hops
        """;
        await using var session = _driver.AsyncSession();
        var result = await session.RunAsync(q, new { id });
        var rows   = await result.ToListAsync();
        return rows.Select(r => r.Values.ToDictionary(k => k.Key, v => (object?)v.Value)).ToList();
    }

    public async ValueTask DisposeAsync() => await _driver.DisposeAsync();
}
