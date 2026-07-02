"""Shell tool: run a command inside the workspace via the command sandbox."""

from __future__ import annotations

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

    def __init__(self, workspace: Workspace, sandbox=None):
        from ..sandbox import CommandSandbox

        self.workspace = workspace
        self.sandbox = sandbox or CommandSandbox(workspace)

    def preview(self, args: dict) -> str:
        return f"run_command: $ {args.get('command', '')}"

    def execute(self, args: dict) -> ToolResult:
        timeout = min(int(args.get("timeout_s", 60)), MAX_TIMEOUT_S)
        result = self.sandbox.run(args["command"], timeout_s=timeout)
        if result.timed_out:
            return ToolResult(f"Command timed out after {timeout}s", error=True)
        parts = []
        if result.stdout:
            parts.append(result.stdout.rstrip("\n"))
        if result.stderr:
            parts.append(f"[stderr]\n{result.stderr.rstrip(chr(10))}")
        parts.append(f"[exit code: {result.returncode}]")
        return ToolResult("\n".join(parts), error=result.returncode != 0)
