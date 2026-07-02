"""Model adapter registry. Swapping models is a config change, nothing more."""

from __future__ import annotations

from ..config import Config
from ..logging_setup import TraceLog
from .base import GenerationCancelled, ModelClient, ModelError, ModelResponse, ToolCall
from .mock import MockClient
from .ollama import OllamaClient

__all__ = [
    "ModelClient",
    "ModelResponse",
    "ModelError",
    "GenerationCancelled",
    "ToolCall",
    "MockClient",
    "OllamaClient",
    "create_client",
]


def create_client(config: Config, trace: TraceLog | None = None) -> ModelClient:
    provider = config.model.provider
    if provider == "ollama":
        return OllamaClient(config.model, trace)
    if provider == "openai-compat":
        from .openai_compat import OpenAICompatClient

        return OpenAICompatClient(config.model, trace)
    if provider == "mock":
        return MockClient()
    raise ValueError(f"Unknown model provider: {provider!r}")
