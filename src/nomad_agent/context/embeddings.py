"""Local semantic search: Ollama embeddings + a SQLite vector store.

Free stack: `nomic-embed-text` via Ollama for vectors, SQLite for storage,
cosine similarity in plain Python. Everything degrades gracefully — if the
embedding model is unreachable, retrieval falls back to keyword search.
"""

from __future__ import annotations

import json
import math
import sqlite3
import urllib.request
from pathlib import Path
from typing import Protocol

from .indexer import FileIndex

# Localhost service; bypass any HTTP(S) proxy from the environment.
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class OllamaEmbeddings:
    def __init__(self, model: str = "nomic-embed-text", base_url: str = "http://localhost:11434"):
        self.model = model
        self.base_url = base_url

    def embed(self, texts: list[str]) -> list[list[float]]:
        request = urllib.request.Request(
            f"{self.base_url}/api/embed",
            data=json.dumps({"model": self.model, "input": texts}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with _opener.open(request, timeout=120) as resp:
            return json.loads(resp.read())["embeddings"]


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def chunk_text(text: str, lines_per_chunk: int = 40, overlap: int = 5) -> list[str]:
    lines = text.splitlines()
    if not lines:
        return []
    chunks = []
    step = max(1, lines_per_chunk - overlap)
    for start in range(0, len(lines), step):
        chunk = "\n".join(lines[start : start + lines_per_chunk])
        if chunk.strip():
            chunks.append(chunk)
        if start + lines_per_chunk >= len(lines):
            break
    return chunks


class VectorStore:
    def __init__(self, db_path: str | Path):
        self.conn = sqlite3.connect(db_path)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS chunks ("
            " path TEXT, chunk_index INTEGER, text TEXT, vector TEXT,"
            " PRIMARY KEY (path, chunk_index))"
        )

    def replace_file(self, path: str, chunks: list[str], vectors: list[list[float]]) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM chunks WHERE path = ?", (path,))
            self.conn.executemany(
                "INSERT INTO chunks VALUES (?, ?, ?, ?)",
                [
                    (path, i, chunk, json.dumps(vector))
                    for i, (chunk, vector) in enumerate(zip(chunks, vectors))
                ],
            )

    def search(self, vector: list[float], k: int = 8) -> list[tuple[str, str, float]]:
        """Return (path, chunk_text, similarity), best first."""
        scored = []
        for path, text, raw in self.conn.execute("SELECT path, text, vector FROM chunks"):
            scored.append((path, text, cosine(vector, json.loads(raw))))
        scored.sort(key=lambda item: -item[2])
        return scored[:k]

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]


class SemanticIndex:
    def __init__(self, store: VectorStore, embedder: Embedder):
        self.store = store
        self.embedder = embedder

    def build(self, index: FileIndex, batch_size: int = 16) -> int:
        """(Re)embed every indexable file. Returns the number of chunks stored."""
        total = 0
        for relative in index.files():
            chunks = chunk_text(index.read(relative))
            if not chunks:
                continue
            vectors: list[list[float]] = []
            for start in range(0, len(chunks), batch_size):
                vectors.extend(self.embedder.embed(chunks[start : start + batch_size]))
            self.store.replace_file(str(relative), chunks, vectors)
            total += len(chunks)
        return total

    def search(self, query: str, k: int = 8) -> list[tuple[str, str, float]]:
        vector = self.embedder.embed([query])[0]
        return self.store.search(vector, k)
