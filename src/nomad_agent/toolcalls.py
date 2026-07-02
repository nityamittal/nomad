"""Fallback tool-call parsing for models without native function calling.

Accepted shapes, anywhere in the assistant text:

    ```json
    {"tool": "read_file", "arguments": {"path": "main.py"}}
    ```

or the whole message being that JSON object (with or without a fence).
Malformed JSON is ignored here; the loop tells the model what went wrong.
"""

from __future__ import annotations

import json
import re

from .models.base import ToolCall

_FENCE_RE = re.compile(r"```(?:json|tool)?\s*\n(.*?)```", re.DOTALL)

FALLBACK_INSTRUCTIONS = (
    "If you cannot emit native tool calls, call a tool by replying with only "
    'a fenced json block: {"tool": "<name>", "arguments": {...}}'
)


def _from_obj(obj: object) -> ToolCall | None:
    if not isinstance(obj, dict):
        return None
    name = obj.get("tool") or obj.get("name")
    if not isinstance(name, str) or not name:
        return None
    arguments = obj.get("arguments", obj.get("args", {}))
    if not isinstance(arguments, dict):
        return None
    return ToolCall(name=name, arguments=arguments)


def parse_text_tool_calls(text: str) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for block in _FENCE_RE.findall(text):
        try:
            parsed = json.loads(block)
        except json.JSONDecodeError:
            continue
        call = _from_obj(parsed)
        if call:
            calls.append(call)
    if not calls:
        stripped = text.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            try:
                call = _from_obj(json.loads(stripped))
                if call:
                    calls.append(call)
            except json.JSONDecodeError:
                pass
    return calls
