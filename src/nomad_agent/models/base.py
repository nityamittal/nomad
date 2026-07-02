"""The ModelClient interface — the seam the whole project hangs on (Phase 10).

Every subsystem talks to a ModelClient; none may import a concrete adapter.
Messages are plain dicts ({"role": ..., "content": ...}) so they serialize
to disk and to every provider wire format without translation layers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class ToolCall:
    name: str
    arguments: dict
    id: str = ""


@dataclass
class ModelResponse:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    raw: dict = field(default_factory=dict)


class ModelError(Exception):
    """Raised when the model backend fails after retries."""


class GenerationCancelled(Exception):
    """Raised when the user interrupts a generation (Ctrl+C mid-stream)."""


class ModelClient(ABC):
    """send() is the only entry point. `on_token` streams content chunks as
    they arrive; the full response is still returned at the end."""

    name: str = "base"

    @abstractmethod
    def send(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        on_token: Callable[[str], None] | None = None,
    ) -> ModelResponse:
        raise NotImplementedError
