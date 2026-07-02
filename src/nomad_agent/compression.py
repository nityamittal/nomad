"""Context compression & caching (Phase 9).

Three pieces:
- HistoryCompressor: when the conversation outgrows its token budget, the old
  middle is summarized ONCE into a compact block; the recent tail stays
  verbatim. The compacted view is cached and reused turn after turn, so the
  prefix sent to the model stays byte-stable — rewriting early messages every
  turn would force Ollama to re-process the whole KV cache and crawl.
- CachingClient: a ModelClient wrapper with a content-addressed disk cache.
  Identical (model, messages, tools) requests skip inference entirely —
  meaningful with slow local models, and free determinism for benchmarks.
- hierarchical_summary: summarize text of any size by chunking, summarizing
  chunks, then summarizing the summaries.

The session file on disk always keeps the FULL history; compression only
changes what is sent to the model.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Callable

from .logging_setup import TraceLog
from .models.base import ModelClient, ModelResponse, ToolCall
from .tokens import estimate_tokens

SUMMARIZE_PROMPT = (
    "Summarize this conversation excerpt for an AI coding agent's memory. "
    "Preserve: decisions made, files touched and how, commands run and their "
    "outcomes, and any constraints discovered. Be dense; use bullet points.\n\n"
)

SUMMARY_MARKER = "[compressed history]"


def _messages_tokens(messages: list[dict]) -> int:
    return sum(estimate_tokens(str(m.get("content", ""))) + 8 for m in messages)


def _render(messages: list[dict]) -> str:
    return "\n".join(f"{m.get('role')}: {m.get('content', '')}" for m in messages)


def _hash_messages(messages: list[dict]) -> str:
    return hashlib.sha256(
        json.dumps(messages, sort_keys=True, default=str).encode()
    ).hexdigest()


class HistoryCompressor:
    def __init__(
        self,
        client: ModelClient,
        budget_tokens: int,
        keep_recent: int = 8,
        trace: TraceLog | None = None,
    ):
        self.client = client
        self.budget = budget_tokens
        self.keep_recent = keep_recent
        self.trace = trace
        # one live compaction: (hash of the exact span it covers, span length, summary message)
        self._compacted: tuple[str, int, dict] | None = None

    def __call__(self, messages: list[dict]) -> list[dict]:
        view = self._apply_existing(messages)
        if _messages_tokens(view) <= self.budget:
            return view
        return self._compact(messages)

    def _apply_existing(self, messages: list[dict]) -> list[dict]:
        if self._compacted is None:
            return messages
        span_hash, span_len, summary_message = self._compacted
        if len(messages) >= span_len and _hash_messages(messages[:span_len]) == span_hash:
            head = [messages[0]] if messages and messages[0].get("role") == "system" else []
            return head + [summary_message] + messages[span_len:]
        return messages  # history diverged (new session); drop the stale compaction

    def _compact(self, messages: list[dict]) -> list[dict]:
        head = [messages[0]] if messages and messages[0].get("role") == "system" else []
        tail_start = max(len(head), len(messages) - self.keep_recent)
        middle = messages[len(head) : tail_start]
        if not middle:
            return messages
        response = self.client.send(
            [{"role": "user", "content": SUMMARIZE_PROMPT + _render(middle)}]
        )
        summary_message = {
            "role": "system",
            "content": f"{SUMMARY_MARKER} Summary of {len(middle)} earlier messages:\n"
            f"{response.content}",
        }
        self._compacted = (_hash_messages(messages[:tail_start]), tail_start, summary_message)
        if self.trace:
            self.trace.record(
                "history_compressed",
                {
                    "messages_summarized": len(middle),
                    "tokens_before": _messages_tokens(messages),
                    "tokens_after": _messages_tokens(head + [summary_message] + messages[tail_start:]),
                },
            )
        return head + [summary_message] + messages[tail_start:]


class CachingClient(ModelClient):
    """Content-addressed response cache in front of any ModelClient."""

    def __init__(self, inner: ModelClient, cache_dir: str | Path):
        self.inner = inner
        self.name = f"cached-{inner.name}"
        self.dir = Path(cache_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.hits = 0
        self.misses = 0

    def _key(self, messages: list[dict], tools: list[dict] | None) -> Path:
        digest = hashlib.sha256(
            json.dumps(
                {"name": self.inner.name, "messages": messages, "tools": tools},
                sort_keys=True,
                default=str,
            ).encode()
        ).hexdigest()
        return self.dir / f"{digest}.json"

    def send(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        on_token: Callable[[str], None] | None = None,
    ) -> ModelResponse:
        path = self._key(messages, tools)
        if path.is_file():
            self.hits += 1
            data = json.loads(path.read_text())
            response = ModelResponse(
                content=data["content"],
                tool_calls=[ToolCall(**c) for c in data["tool_calls"]],
                prompt_tokens=data.get("prompt_tokens", 0),
                completion_tokens=data.get("completion_tokens", 0),
            )
            if on_token and response.content:
                on_token(response.content)
            return response
        self.misses += 1
        response = self.inner.send(messages, tools=tools, on_token=on_token)
        path.write_text(
            json.dumps(
                {
                    "content": response.content,
                    "tool_calls": [
                        {"name": c.name, "arguments": c.arguments, "id": c.id}
                        for c in response.tool_calls
                    ],
                    "prompt_tokens": response.prompt_tokens,
                    "completion_tokens": response.completion_tokens,
                },
                ensure_ascii=False,
            )
        )
        return response


def hierarchical_summary(
    client: ModelClient, text: str, chunk_tokens: int = 1500, prompt: str = "Summarize:\n\n"
) -> str:
    """Summarize text of any size: chunk -> summarize -> summarize summaries."""
    if estimate_tokens(text) <= chunk_tokens:
        return client.send([{"role": "user", "content": prompt + text}]).content
    chunk_chars = chunk_tokens * 4
    chunks = [text[i : i + chunk_chars] for i in range(0, len(text), chunk_chars)]
    partials = [
        client.send([{"role": "user", "content": prompt + chunk}]).content for chunk in chunks
    ]
    combined = "\n\n".join(partials)
    return hierarchical_summary(client, combined, chunk_tokens, prompt="Combine these partial summaries into one:\n\n")
