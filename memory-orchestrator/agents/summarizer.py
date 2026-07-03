"""Summarize a list of incidents / graph paths into a compact natural-language
digest suitable for episodic storage."""
from __future__ import annotations

import os
from openai import AzureOpenAI

_SYS = (
    "You compress IcM findings into <=120 tokens. "
    "Structure: (1) root cause (2) affected services (3) remediation. "
    "Never fabricate service names."
)


def run(hop_path: str, context: str) -> str:
    nvidia_key = os.getenv("NVIDIA_API_KEY")
    if nvidia_key:
        from openai import OpenAI

        client = OpenAI(
            api_key=nvidia_key,
            base_url=os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"),
        )
        model = os.getenv("NVIDIA_MODEL", "z-ai/glm-5.2")
    else:
        client = AzureOpenAI(
            api_key=os.environ["AZURE_OPENAI_KEY"],
            api_version="2024-06-01",
            azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        )
        model = os.getenv("AOAI_CHAT_DEPLOY", "gpt-4o")
    r = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _SYS},
            {"role": "user",   "content": f"hop_path: {hop_path}\ncontext: {context}"},
        ],
        temperature=0.2,
    )
    return r.choices[0].message.content or ""
