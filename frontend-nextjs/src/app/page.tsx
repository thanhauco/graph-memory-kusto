"use client";

/**
 * Next.js entry point.
 *
 * The production UI lives as a 15-panel React app in `/public/demo.html`
 * (the original self-contained React+Tailwind CDN build). During migration
 * this page embeds the demo in an iframe so nothing breaks — replace with
 * proper component imports as each panel is ported to TSX.
 */
export default function Home() {
  return (
    <main className="min-h-screen">
      <header className="border-b border-slate-800 bg-slate-950/60 backdrop-blur px-5 py-3 flex items-center justify-between">
        <div>
          <div className="font-semibold">GraphRAG + MemMachine</div>
          <div className="text-xs text-slate-400">Hybrid Memory · Microsoft IcM</div>
        </div>
        <a href="/api/health"
           className="text-xs text-slate-400 hover:text-white underline">
          backend health
        </a>
      </header>
      <iframe src="/demo.html" className="w-full" style={{ height: "calc(100vh - 57px)", border: 0 }} />
    </main>
  );
}
