namespace IcM.MemoryOrchestrator.Services;

/// <summary>
/// Thin HTTP client that calls the Python IcM GraphRAG agent exposed as
/// a sidecar at GRAPHRAG_URL (default http://localhost:8000/chat).
/// </summary>
public sealed class GraphRagClient
{
    private readonly HttpClient _http;
    private readonly string _url;

    public GraphRagClient(HttpClient http, IConfiguration cfg)
    {
        _http = http;
        _url  = cfg["GraphRag:Url"]
              ?? Environment.GetEnvironmentVariable("GRAPHRAG_URL")
              ?? "http://localhost:8000/chat";
    }

    public async Task<string> AnswerAsync(string question)
    {
        var r = await _http.PostAsJsonAsync(_url, new { question });
        r.EnsureSuccessStatusCode();
        var payload = await r.Content.ReadFromJsonAsync<Dictionary<string, string>>();
        return payload?["answer"] ?? "";
    }
}
