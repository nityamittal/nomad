"""Workspace: every file/command tool operates inside one project root.

resolve() is the single choke point that stops path escapes (`../`, absolute
paths outside the root, symlinks pointing out). Phase 3's sandbox and audit
build on this same boundary.
"""

from __future__ import annotations

from pathlib import Path


class WorkspaceError(Exception):
    pass


class Workspace:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise WorkspaceError(f"Workspace root does not exist: {self.root}")

    def resolve(self, relative: str) -> Path:
        """Resolve a path inside the workspace; refuse anything that lands outside."""
        candidate = (self.root / relative).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise WorkspaceError(
                f"Path '{relative}' escapes the workspace root {self.root}"
            )
        return candidate
