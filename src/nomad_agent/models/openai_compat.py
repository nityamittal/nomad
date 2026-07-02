"""OpenAI-compatible adapter: llama.cpp server, vLLM, LM Studio, LocalAI...

All of these free/local servers speak the same /v1/chat/completions SSE
protocol, so one adapter covers them. Same ModelClient contract as Ollama —
swapping backends is a config change (provider = "openai-compat").
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Iterator

from ..config import ModelConfig
from ..logging_setup import TraceLog
from .base import GenerationCancelled, ModelClient, ModelError, ModelResponse, ToolCall

logger = logging.getLogger("nomad.model.openai_compat")

_local_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class OpenAICompatClient(ModelClient):
    name = "openai-compat"

    def __init__(self, config: ModelConfig, trace: TraceLog | None = None):
        self.config = config
        self.trace = trace

    def send(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        on_token: Callable[[str], None] | None = None,
    ) -> ModelResponse:
        payload: dict = {
            "model": self.config.name,
            "messages": [self._wire_message(m) for m in messages],
            "temperature": self.config.temperature,
            "stream": True,
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
                        },
                    )
                return response
            except GenerationCancelled:
                raise
            except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt < self.config.max_retries:
                    delay = 2**attempt
                    logger.warning("request failed (%s), retrying in %ds", exc, delay)
                    time.sleep(delay)
        raise ModelError(f"OpenAI-compatible request failed after retries: {last_error}") from last_error

    @staticmethod
    def _wire_message(message: dict) -> dict:
        """Translate our message dicts to OpenAI wire format (tool results
        use role 'tool' + content; we drop our internal tool_name key)."""
        wire = {"role": message["role"], "content": message.get("content", "")}
        if message.get("role") == "tool":
            wire["content"] = message.get("content", "")
        return wire

    def _post_stream(self, payload: dict) -> Iterator[dict]:
        """POST and yield parsed SSE JSON events. Isolated for tests."""
        base = self.config.base_url.rstrip("/")
        url = f"{base}/v1/chat/completions"
        host = urllib.parse.urlparse(base).hostname or ""
        opener = (
            _local_opener
            if host in ("localhost", "127.0.0.1", "::1")
            else urllib.request.build_opener()
        )
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with opener.open(request, timeout=self.config.request_timeout_s) as resp:
            for raw in resp:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[len("data:") :].strip()
                if data == "[DONE]":
                    return
                yield json.loads(data)

    def _consume(
        self, events: Iterator[dict], on_token: Callable[[str], None] | None
    ) -> ModelResponse:
        content_parts: list[str] = []
        # tool calls stream as fragments keyed by index; arguments arrive as
        # partial JSON strings that only parse once fully assembled
        pending: dict[int, dict] = {}
        prompt_tokens = completion_tokens = 0
        try:
            for event in events:
                if "error" in event:
                    raise ModelError(f"Server error: {event['error']}")
                usage = event.get("usage") or {}
                prompt_tokens = usage.get("prompt_tokens", prompt_tokens)
                completion_tokens = usage.get("completion_tokens", completion_tokens)
                for choice in event.get("choices", []):
                    delta = choice.get("delta", {})
                    token = delta.get("content")
                    if token:
                        content_parts.append(token)
                        if on_token:
                            on_token(token)
                    for fragment in delta.get("tool_calls", []) or []:
                        slot = pending.setdefault(
                            fragment.get("index", 0), {"name": "", "arguments": ""}
                        )
                        fn = fragment.get("function", {})
                        slot["name"] += fn.get("name", "") or ""
                        slot["arguments"] += fn.get("arguments", "") or ""
        except KeyboardInterrupt:
            raise GenerationCancelled() from None

        tool_calls = []
        for index in sorted(pending):
            slot = pending[index]
            try:
                arguments = json.loads(slot["arguments"] or "{}")
            except json.JSONDecodeError:
                arguments = {"_raw": slot["arguments"]}
            tool_calls.append(ToolCall(name=slot["name"], arguments=arguments))
        return ModelResponse(
            content="".join(content_parts),
            tool_calls=tool_calls,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
