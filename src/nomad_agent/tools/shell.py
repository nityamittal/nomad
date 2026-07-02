"""Shell tool: run a command inside the workspace with a hard timeout."""

from __future__ import annotations

import subprocess

from .base import Tool, ToolResult
from .workspace import Workspace

MAX_TIMEOUT_S = 300


class RunCommandTool(Tool):
    name = "run_command"
    description = (
        "Run a shell command from the workspace root. Returns stdout, stderr "
        "and the exit code. Commands are killed after the timeout."
    )
    gated = True
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "timeout_s": {"type": "integer", "description": "seconds, default 60, max 300"},
        },
        "required": ["command"],
    }

    def __init__(self, workspace: Workspace):
        self.workspace = workspace

    def preview(self, args: dict) -> str:
        return f"run_command: $ {args.get('command', '')}"

    def execute(self, args: dict) -> ToolResult:
        timeout = min(int(args.get("timeout_s", 60)), MAX_TIMEOUT_S)
        try:
            proc = subprocess.run(
                args["command"],
                shell=True,
                cwd=self.workspace.root,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(f"Command timed out after {timeout}s", error=True)
        parts = []
        if proc.stdout:
            parts.append(proc.stdout.rstrip("\n"))
        if proc.stderr:
            parts.append(f"[stderr]\n{proc.stderr.rstrip(chr(10))}")
        parts.append(f"[exit code: {proc.returncode}]")
        return ToolResult("\n".join(parts), error=proc.returncode != 0)
