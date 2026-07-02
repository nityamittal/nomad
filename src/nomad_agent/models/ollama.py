"""Ollama adapter: streams /api/chat over stdlib HTTP with retries.

num_ctx is always sent explicitly — Ollama's default context window is far
smaller than most models support, and relying on it causes silent truncation.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Callable, Iterator

from ..config import ModelConfig
from ..logging_setup import TraceLog
from .base import GenerationCancelled, ModelClient, ModelError, ModelResponse, ToolCall

logger = logging.getLogger("nomad.model.ollama")

# Ollama runs on localhost; never route it through an HTTP(S) proxy from env.
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class OllamaClient(ModelClient):
    name = "ollama"

    def __init__(self, config: ModelConfig, trace: TraceLog | None = None):
        self.config = config
        self.trace = trace

    def send(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        on_token: Callable[[str], None] | None = None,
    ) -> ModelResponse:
        payload = {
            "model": self.config.name,
            "messages": messages,
            "stream": True,
            "options": {
                "num_ctx": self.config.num_ctx,
                "temperature": self.config.temperature,
            },
        }
        if tools:
            payload["tools"] = tools
        if self.trace:
            self.trace.record("model_request", payload)

        last_error: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            try:
                response = self._consume(self._post_stream(payload), on_token)
                if self.trace:
                    self.trace.record(
                        "model_response",
                        {
                            "content": response.content,
                            "tool_calls": [
                                {"name": t.name, "arguments": t.arguments}
                                for t in response.tool_calls
                            ],
                            "prompt_tokens": response.prompt_tokens,
                            "completion_tokens": response.completion_tokens,
                        },
                    )
                return response
            except GenerationCancelled:
                raise
            except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt < self.config.max_retries:
                    delay = 2**attempt
                    logger.warning(
                        "model request failed (%s), retry %d/%d in %ds",
                        exc,
                        attempt + 1,
                        self.config.max_retries,
                        delay,
                    )
                    time.sleep(delay)
        raise ModelError(f"Ollama request failed after retries: {last_error}") from last_error

    def _post_stream(self, payload: dict) -> Iterator[dict]:
        """POST to /api/chat and yield parsed NDJSON chunks. Isolated so tests
        can substitute a scripted stream without touching HTTP."""
        request = urllib.request.Request(
            f"{self.config.base_url}/api/chat",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with _opener.open(request, timeout=self.config.request_timeout_s) as resp:
            for line in resp:
                line = line.strip()
                if line:
                    yield json.loads(line)

    def _consume(
        self, chunks: Iterator[dict], on_token: Callable[[str], None] | None
    ) -> ModelResponse:
        content_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        prompt_tokens = completion_tokens = 0
        last_chunk: dict = {}
        try:
            for chunk in chunks:
                if "error" in chunk:
                    raise ModelError(f"Ollama error: {chunk['error']}")
                last_chunk = chunk
                message = chunk.get("message", {})
                token = message.get("content", "")
                if token:
                    content_parts.append(token)
                    if on_token:
                        on_token(token)
                for call in message.get("tool_calls", []) or []:
                    fn = call.get("function", {})
                    args = fn.get("arguments", {})
                    if isinstance(args, str):
                        args = json.loads(args or "{}")
                    tool_calls.append(ToolCall(name=fn.get("name", ""), arguments=args))
                if chunk.get("done"):
                    prompt_tokens = chunk.get("prompt_eval_count", 0)
                    completion_tokens = chunk.get("eval_count", 0)
        except KeyboardInterrupt:
            raise GenerationCancelled() from None
        return ModelResponse(
            content="".join(content_parts),
            tool_calls=tool_calls,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            raw=last_chunk,
        )
