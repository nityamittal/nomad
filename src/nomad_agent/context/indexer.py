"""File indexing: directory walk with ignore rules and binary/size filters."""

from __future__ import annotations

import fnmatch
from pathlib import Path

DEFAULT_IGNORES = [
    ".git",
    ".nomad",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    ".pytest_cache",
    "dist",
    "build",
    "*.egg-info",
    "*.pyc",
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.gif",
    "*.pdf",
    "*.zip",
    "*.gz",
    "*.so",
    "*.bin",
    "*.lock",
]

MAX_FILE_BYTES = 200_000


class FileIndex:
    def __init__(self, root: str | Path, extra_ignores: list[str] | None = None):
        self.root = Path(root).resolve()
        self.patterns = list(DEFAULT_IGNORES) + list(extra_ignores or [])
        gitignore = self.root / ".gitignore"
        if gitignore.is_file():
            for line in gitignore.read_text().splitlines():
                line = line.strip().rstrip("/")
                if line and not line.startswith("#") and not line.startswith("!"):
                    self.patterns.append(line.lstrip("/"))

    def _ignored(self, relative: Path) -> bool:
        for pattern in self.patterns:
            for part in relative.parts:
                if fnmatch.fnmatch(part, pattern):
                    return True
            if fnmatch.fnmatch(str(relative), pattern):
                return True
        return False

    def files(self) -> list[Path]:
        """All indexable text files, as paths relative to the root."""
        found: list[Path] = []
        for path in sorted(self.root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(self.root)
            if self._ignored(relative):
                continue
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    continue
                with open(path, "rb") as fh:
                    if b"\0" in fh.read(1024):
                        continue
            except OSError:
                continue
            found.append(relative)
        return found

    def read(self, relative: Path) -> str:
        return (self.root / relative).read_text(errors="replace")
