"""Debug logging, including a JSONL trace of every raw model request/response.

The trace log is the primary debugging tool for the whole project ("you will
live in these logs"). Each line is one event: {ts, kind, payload}.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path


def setup_logging(state_path: Path, level: int = logging.INFO) -> logging.Logger:
    log_dir = state_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("nomad")
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG)
    file_handler = logging.FileHandler(log_dir / "nomad.log")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    logger.addHandler(file_handler)
    logger.addHandler(console)
    return logger


class TraceLog:
    """Append-only JSONL trace of raw model traffic and tool activity."""

    def __init__(self, state_path: Path, name: str = "trace"):
        log_dir = state_path / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        self.path = log_dir / f"{name}.jsonl"
        self._lock = threading.Lock()

    def record(self, kind: str, payload: object) -> None:
        line = json.dumps(
            {"ts": time.time(), "kind": kind, "payload": payload},
            ensure_ascii=False,
            default=repr,
        )
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    def read_all(self) -> list[dict]:
        if not self.path.is_file():
            return []
        return [
            json.loads(line)
            for line in self.path.read_text().splitlines()
            if line.strip()
        ]
