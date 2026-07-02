"""Tool interface and registry.

A Tool = name + description + JSON schema + execute(). Tools that mutate
state (`gated`) go through the approval gate (Phase 3) before running; their
preview() gives the user something meaningful to approve (e.g. a diff).

Validation is deliberately forgiving about extra keys but strict about
required ones and types — malformed calls from small local models are the
normal case, and the error strings are written to be fed back to the model.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ToolResult:
    output: str
    error: bool = False


class Tool(ABC):
    name: str = ""
    description: str = ""
    parameters: dict = {"type": "object", "properties": {}}
    gated: bool = False

    def is_gated(self, args: dict) -> bool:
        """Per-call gating; tools may override (e.g. git: only mutating subcommands)."""
        return self.gated

    def preview(self, args: dict) -> str:
        """Human-readable description of what would happen, shown at the approval gate."""
        return f"{self.name}({json.dumps(args, ensure_ascii=False)})"

    @abstractmethod
    def execute(self, args: dict) -> ToolResult:
        raise NotImplementedError


_JSON_TYPES = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
}


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def schemas(self) -> list[dict]:
        """Function-calling schemas in the format Ollama/OpenAI expect."""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in self._tools.values()
        ]

    def validate(self, name: str, args: dict) -> str | None:
        """Return an error message suitable for feeding back to the model, or None."""
        tool = self.get(name)
        if tool is None:
            return f"Unknown tool '{name}'. Available tools: {', '.join(self.names())}"
        if not isinstance(args, dict):
            return f"Tool '{name}' arguments must be a JSON object, got {type(args).__name__}"
        schema = tool.parameters
        properties = schema.get("properties", {})
        for required in schema.get("required", []):
            if required not in args:
                return f"Tool '{name}' missing required argument '{required}'"
        for key, value in args.items():
            spec = properties.get(key)
            if spec is None:
                continue
            expected = _JSON_TYPES.get(spec.get("type", ""))
            if expected and not isinstance(value, expected):
                return (
                    f"Tool '{name}' argument '{key}' should be {spec['type']}, "
                    f"got {type(value).__name__}"
                )
            if isinstance(value, bool) and spec.get("type") in ("integer", "number"):
                return f"Tool '{name}' argument '{key}' should be {spec['type']}, got boolean"
            allowed = spec.get("enum")
            if allowed and value not in allowed:
                return f"Tool '{name}' argument '{key}' must be one of {allowed}"
        return None
