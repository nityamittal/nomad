"""Benchmark suite: representative tasks with machine-checkable outcomes.

Each task sets up a scratch project, gives the agent a prompt, and checks the
result mechanically (file contents or a passing command) — never by asking
the model whether it succeeded. Running the same suite across providers is
Phase 10's model comparison.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..config import Config
from ..conversation import Session
from ..loop import AgentLoop
from ..models.base import ModelClient
from ..sandbox import CommandSandbox
from ..tools.workspace import Workspace


@dataclass
class BenchmarkTask:
    name: str
    prompt: str
    files: dict[str, str] = field(default_factory=dict)
    # check: {"type": "file_contains", "path": ..., "text": ...}
    #     or {"type": "command_succeeds", "command": ...}
    check: dict = field(default_factory=dict)

    @staticmethod
    def load_suite(path: str | Path) -> list["BenchmarkTask"]:
        data = json.loads(Path(path).read_text())
        return [BenchmarkTask(**item) for item in data]


@dataclass
class TaskResult:
    name: str
    passed: bool
    detail: str = ""
    answer: str = ""


@dataclass
class BenchmarkReport:
    model: str
    results: list[TaskResult] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def total(self) -> int:
        return len(self.results)

    def render(self) -> str:
        lines = [f"Benchmark — model: {self.model}"]
        for r in self.results:
            mark = "PASS" if r.passed else "FAIL"
            lines.append(f"  [{mark}] {r.name}" + (f" — {r.detail}" if r.detail else ""))
        lines.append(f"  {self.passed}/{self.total} passed")
        return "\n".join(lines)


def _run_check(check: dict, root: Path) -> tuple[bool, str]:
    kind = check.get("type")
    if kind == "file_contains":
        target = root / check["path"]
        if not target.is_file():
            return False, f"{check['path']} was not created"
        if check["text"] in target.read_text():
            return True, ""
        return False, f"{check['path']} does not contain {check['text']!r}"
    if kind == "command_succeeds":
        result = CommandSandbox(Workspace(root)).run(check["command"], timeout_s=120)
        if result.returncode == 0:
            return True, ""
        return False, f"`{check['command']}` exited {result.returncode}"
    return False, f"unknown check type {kind!r}"


def run_benchmark(
    tasks: list[BenchmarkTask],
    client_factory: Callable[[], ModelClient],
    model_label: str = "?",
    loop_factory: Callable[[Config, ModelClient], AgentLoop] | None = None,
) -> BenchmarkReport:
    """client_factory returns a FRESH client per task so state never leaks."""
    report = BenchmarkReport(model=model_label)
    for task in tasks:
        with tempfile.TemporaryDirectory(prefix="nomad-bench-") as tmp:
            root = Path(tmp)
            for rel, content in task.files.items():
                target = root / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
            cfg = Config(project_root=root)
            cfg.permissions.mode = "auto"  # benchmarks run headless
            cfg.ensure_state_dirs()
            client = client_factory()
            loop = (
                loop_factory(cfg, client)
                if loop_factory
                else AgentLoop.from_config(cfg, client)
            )
            try:
                answer = loop.run(Session(cfg.state_path), task.prompt)
            except Exception as exc:  # a crashing task is a failing task
                report.results.append(TaskResult(task.name, False, f"crashed: {exc!r}"))
                continue
            passed, detail = _run_check(task.check, root)
            report.results.append(TaskResult(task.name, passed, detail, answer=answer))
    return report
