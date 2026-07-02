"""Keyword retrieval: grep-style scoring, no index required.

This is the first retrieval tier (cheap, always available); semantic search
(embeddings.py) is layered on top when an embedding model is reachable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .indexer import FileIndex

_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


@dataclass
class SearchHit:
    path: Path
    score: float
    preview: str = ""


def query_terms(query: str) -> list[str]:
    return [w.lower() for w in _WORD_RE.findall(query)]


def keyword_search(index: FileIndex, query: str, max_files: int = 10) -> list[SearchHit]:
    terms = query_terms(query)
    if not terms:
        return []
    hits: list[SearchHit] = []
    for relative in index.files():
        name = str(relative).lower()
        try:
            content = index.read(relative).lower()
        except OSError:
            continue
        score = 0.0
        preview_lines: list[str] = []
        for term in terms:
            count = content.count(term)
            if count:
                score += min(count, 10)  # cap so one spammy file doesn't dominate
                if len(preview_lines) < 3:
                    for line in content.splitlines():
                        if term in line:
                            preview_lines.append(line.strip()[:120])
                            break
            if term in name:
                score += 15  # filename matches are strong signals
        if score > 0:
            hits.append(SearchHit(relative, score, "\n".join(preview_lines)))
    hits.sort(key=lambda h: (-h.score, str(h.path)))
    return hits[:max_files]
