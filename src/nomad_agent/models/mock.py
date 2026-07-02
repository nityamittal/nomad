"""Scripted model client for tests and offline development.

Feed it a list of ModelResponse objects (or strings); each send() pops the
next one. It records every request so tests can assert on what the agent
actually sent to the "model".
"""

from __future__ import annotations

from typing import Callable

from .base import ModelClient, ModelError, ModelResponse


class MockClient(ModelClient):
    name = "mock"

    def __init__(self, script: list[ModelResponse | str] | None = None):
        self.script = list(script or [])
        self.requests: list[dict] = []

    def send(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        on_token: Callable[[str], None] | None = None,
    ) -> ModelResponse:
        self.requests.append({"messages": [dict(m) for m in messages], "tools": tools})
        if not self.script:
            raise ModelError("MockClient script exhausted")
        item = self.script.pop(0)
        response = ModelResponse(content=item) if isinstance(item, str) else item
        if on_token and response.content:
            on_token(response.content)
        return response
