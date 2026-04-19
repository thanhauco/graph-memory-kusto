using IcM.MemoryOrchestrator.Services;

var builder = WebApplication.CreateBuilder(args);
builder.Services.AddEndpointsApiExplorer();
builder.Services.AddSwaggerGen();
builder.Services.AddSingleton<GraphClient>();
builder.Services.AddSingleton<EpisodicClient>();
builder.Services.AddHttpClient<GraphRagClient>();

var app = builder.Build();
if (app.Environment.IsDevelopment())
{
    app.UseSwagger(); app.UseSwaggerUI();
}

app.MapGet("/health", () => Results.Ok(new { status = "ok" }));

// 3-hop RCA for an incident
app.MapGet("/incidents/{id}/rca", async (string id, GraphClient g) =>
    Results.Ok(await g.ThreeHopRcaAsync(id)));

// Blast radius
app.MapGet("/incidents/{id}/blast", async (string id, int? maxHops, GraphClient g) =>
    Results.Ok(await g.BlastRadiusAsync(id, maxHops ?? 3)));

// Episodic retrieval
app.MapGet("/episodes", async (string? incident, EpisodicClient ep) =>
    Results.Ok(await ep.ListAsync(incident)));

// Agent chat — delegates to the Python IcM GraphRAG agent over HTTP
app.MapPost("/chat", async (ChatRequest req, GraphRagClient rag) =>
    Results.Ok(new { answer = await rag.AnswerAsync(req.Question) }));

app.Run();

public record ChatRequest(string Question);
