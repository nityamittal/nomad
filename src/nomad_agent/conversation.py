"""Session persistence: every conversation is saved to disk and resumable."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path


class Session:
    def __init__(self, state_path: Path, session_id: str | None = None):
        self.dir = state_path / "sessions"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.id = session_id or f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
        self.path = self.dir / f"{self.id}.json"
        self.messages: list[dict] = []
        self.created_at = time.time()
        if self.path.is_file():
            data = json.loads(self.path.read_text())
            self.messages = data.get("messages", [])
            self.created_at = data.get("created_at", self.created_at)

    def append(self, message: dict) -> None:
        self.messages.append(message)
        self.save()

    def save(self) -> None:
        self.path.write_text(
            json.dumps(
                {
                    "id": self.id,
                    "created_at": self.created_at,
                    "updated_at": time.time(),
                    "messages": self.messages,
                },
                indent=2,
                ensure_ascii=False,
            )
        )

    @classmethod
    def latest(cls, state_path: Path) -> "Session | None":
        sessions_dir = state_path / "sessions"
        if not sessions_dir.is_dir():
            return None
        files = sorted(sessions_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
        if not files:
            return None
        return cls(state_path, session_id=files[-1].stem)
