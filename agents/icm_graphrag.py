"""IcM GraphRAG agent — hybrid retrieval + graph traversal + LLM.

KV cache-optimized (§9): `prompt_cache.build_messages` emits a byte-identical
4,200-token IcM prefix so Azure OpenAI's automatic prefix cache can hit
(target 71% → ~0.54× token cost).
"""
from __future__ import annotations

import os
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

try:
    from openai import AzureOpenAI  # type: ignore
except Exception:  # pragma: no cover
    AzureOpenAI = None  # type: ignore

from .prompt_cache import build_messages, stable_prefix, prefix_stats

try:
    from graph_service import GraphService         # type: ignore
    from vector_service import embed_one           # type: ignore
    from episodic_memory import vector_top_k       # type: ignore
except Exception:  # pragma: no cover
    GraphService = None                            # type: ignore
    embed_one = lambda _t: [0.0] * 1536             # type: ignore
    vector_top_k = lambda *_a, **_kw: []            # type: ignore


def _dynamic_context(question: str) -> str:
    bits: list[str] = []
    try:
        episodes = vector_top_k(embed_one(question), k=5)
        if episodes:
            bits.append("Retrieved episodes (top-5 ANN):\n" + "\n".join(
                f"- {r.episode.episode_id} (conf {r.episode.confidence:.2f}): "
                f"{r.episode.hop_path}"
                for r in episodes
            ))
    except Exception:
        pass
    if GraphService is not None:
        try:
            gs = GraphService()
            try:
                cycles = gs.cycles()
                regressions = gs.regressions(days=7)
            finally:
                gs.close()
            bits.append(f"Recent cycles: {cycles}")
            bits.append(f"Regressions (7d): {regressions}")
        except Exception:
            pass
    return "\n\n".join(bits)


def answer(question: str) -> str:
    if AzureOpenAI is None:
        raise RuntimeError("openai package not installed; cannot call LLM")
    client = AzureOpenAI(
        api_key=os.environ["AZURE_OPENAI_KEY"],
        api_version="2024-06-01",
        azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
    )
    messages = build_messages(question, dynamic_context=_dynamic_context(question))
    resp = client.chat.completions.create(
        model=os.getenv("AOAI_CHAT_DEPLOY", "gpt-4o"),
        messages=messages,
        temperature=0.2,
    )
    return resp.choices[0].message.content or ""


SYSTEM_PROMPT = stable_prefix()


if __name__ == "__main__":
    s = prefix_stats()
    print(f"[prefix] ~{s['approx_tokens']} tok  sha={s['sha256']}  "
          f"target_ok={s['meets_target']}")
    q = " ".join(sys.argv[1:]) or "Why did ServiceA fail in INC-456?"
    try:
        print(answer(q))
    except KeyError:
        print("(AZURE_OPENAI_KEY/ENDPOINT not set — skipping live call)")
