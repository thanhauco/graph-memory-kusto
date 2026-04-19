import "./globals.css";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "GraphRAG + MemMachine — IcM Memory",
  description: "Hybrid memory (episodic + graph + vector) for Microsoft IcM.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen">{children}</body>
    </html>
  );
}
