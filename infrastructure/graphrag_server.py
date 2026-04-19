"""FastAPI wrapper around agents.icm_graphrag.answer()."""
from fastapi import FastAPI
from pydantic import BaseModel

from agents import answer

app = FastAPI(title="IcM GraphRAG Agent")


class ChatIn(BaseModel):
    question: str


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/chat")
def chat(inp: ChatIn):
    return {"answer": answer(inp.question)}
