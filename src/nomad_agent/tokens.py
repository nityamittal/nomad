"""Token estimation without model-specific tokenizers.

The ~4 chars/token heuristic is deliberately conservative and dependency-free;
every budget in the agent treats it as an estimate, not an exact count.
"""

from __future__ import annotations


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


def truncate_to_tokens(text: str, max_tokens: int, marker: str = "\n[... output truncated: {n} more characters ...]") -> str:
    """Cut `text` to roughly `max_tokens`, appending a marker that tells the
    model how much was dropped so it knows to narrow its request."""
    max_chars = max_tokens * 4
    if len(text) <= max_chars:
        return text
    kept = text[:max_chars]
    dropped = len(text) - max_chars
    return kept + marker.format(n=dropped)
