"""Approval gate + audit log: no gated action runs without control (Phase 3).

Gate modes:
- "prompt": show the tool's preview (a diff for file edits, the command line
  for shell) and ask y/n/a. "a" persists an always-allow for that tool.
- "auto": approve everything (benchmarks/CI only).
- "deny": refuse every gated call.

Every tool call — approved, denied, or failed — lands in the audit log.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable


class ApprovalGate:
    def __init__(
        self,
        mode: str = "prompt",
        state_path: Path | None = None,
        input_fn: Callable[[str], str] = input,
        print_fn: Callable[[str], None] = print,
    ):
        if mode not in ("prompt", "auto", "deny"):
            raise ValueError(f"Unknown permissions mode: {mode!r}")
        self.mode = mode
        self.input_fn = input_fn
        self.print_fn = print_fn
        self.store = state_path / "permissions.json" if state_path else None
        self.always_allow: set[str] = set()
        if self.store and self.store.is_file():
            data = json.loads(self.store.read_text())
            self.always_allow = set(data.get("always_allow", []))

    def __call__(self, tool: object, args: dict, preview: str) -> bool:
        if self.mode == "auto":
            return True
        if self.mode == "deny":
            return False
        name = getattr(tool, "name", "?")
        if name in self.always_allow:
            return True
        self.print_fn(f"\n--- approval required: {name} ---\n{preview}\n")
        while True:
            answer = self.input_fn("Allow? [y]es / [n]o / [a]lways for this tool: ").strip().lower()
            if answer in ("y", "yes"):
                return True
            if answer in ("n", "no", ""):
                return False
            if answer in ("a", "always"):
                self.always_allow.add(name)
                self._save()
                return True

    def _save(self) -> None:
        if self.store:
            self.store.parent.mkdir(parents=True, exist_ok=True)
            self.store.write_text(json.dumps({"always_allow": sorted(self.always_allow)}))


class AuditLog:
    """Append-only JSONL record of every tool call and its outcome."""

    def __init__(self, state_path: Path):
        state_path.mkdir(parents=True, exist_ok=True)
        self.path = state_path / "audit.jsonl"

    def record(self, entry: dict) -> None:
        line = json.dumps({"ts": time.time(), **entry}, ensure_ascii=False, default=repr)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def read_all(self) -> list[dict]:
        if not self.path.is_file():
            return []
        return [json.loads(l) for l in self.path.read_text().splitlines() if l.strip()]
