"""Context assembly: pick the right files for a request, under a token budget.

Keyword hits come first (cheap, precise on identifiers); semantic chunks fill
in when available. Every decision — included, excluded, budget — is traced,
because "why didn't the agent see that file?" is a daily debugging question.
"""

from __future__ import annotations

from ..logging_setup import TraceLog
from ..tokens import estimate_tokens, truncate_to_tokens
from .embeddings import SemanticIndex
from .indexer import FileIndex
from .retrieval import keyword_search

PER_FILE_TOKEN_CAP = 1500


class ContextAssembler:
    def __init__(
        self,
        index: FileIndex,
        budget_tokens: int,
        semantic: SemanticIndex | None = None,
        trace: TraceLog | None = None,
    ):
        self.index = index
        self.budget = budget_tokens
        self.semantic = semantic
        self.trace = trace

    def build(self, query: str, messages: list[dict]) -> str | None:
        """Return a context block for the request, or None if nothing relevant."""
        remaining = self.budget
        included: list[str] = []
        excluded: list[str] = []
        sections: list[str] = []
        seen: set[str] = set()

        for hit in keyword_search(self.index, query):
            path = str(hit.path)
            if path in seen:
                continue
            seen.add(path)
            content = truncate_to_tokens(self.index.read(hit.path), PER_FILE_TOKEN_CAP)
            cost = estimate_tokens(content) + 20
            if cost > remaining:
                excluded.append(path)
                continue
            remaining -= cost
            included.append(path)
            sections.append(f"--- {path} ---\n{content}")

        if self.semantic is not None:
            try:
                for path, chunk, score in self.semantic.search(query):
                    if path in seen or score < 0.3:
                        continue
                    seen.add(path)
                    cost = estimate_tokens(chunk) + 20
                    if cost > remaining:
                        excluded.append(path)
                        continue
                    remaining -= cost
                    included.append(f"{path} (semantic)")
                    sections.append(f"--- {path} (excerpt) ---\n{chunk}")
            except OSError:
                pass  # embedding backend down: keyword results already collected

        if self.trace:
            self.trace.record(
                "context_assembly",
                {
                    "query": query,
                    "budget_tokens": self.budget,
                    "used_tokens": self.budget - remaining,
                    "included": included,
                    "excluded_over_budget": excluded,
                },
            )
        if not sections:
            return None
        return (
            "Relevant project files (auto-retrieved; read-only snapshot):\n\n"
            + "\n\n".join(sections)
        )
