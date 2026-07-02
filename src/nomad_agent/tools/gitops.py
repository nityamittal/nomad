"""Git tool: a whitelisted subset of git run inside the workspace.

Read-only subcommands run ungated; anything that mutates history or the
working tree goes through the approval gate.
"""

from __future__ import annotations

import subprocess

from .base import Tool, ToolResult
from .workspace import Workspace

READ_ONLY = {"status", "diff", "log", "show", "branch"}
MUTATING = {"add", "commit", "checkout", "restore", "stash"}
ALLOWED = sorted(READ_ONLY | MUTATING)


class GitTool(Tool):
    name = "git"
    description = (
        "Run a git subcommand in the workspace. "
        f"Allowed subcommands: {', '.join(ALLOWED)}."
    )
    parameters = {
        "type": "object",
        "properties": {
            "subcommand": {"type": "string", "enum": ALLOWED},
            "args": {
                "type": "array",
                "items": {"type": "string"},
                "description": "extra arguments, e.g. ['-m', 'message'] for commit",
            },
        },
        "required": ["subcommand"],
    }

    def __init__(self, workspace: Workspace):
        self.workspace = workspace

    def is_gated(self, args: dict) -> bool:
        return args.get("subcommand") in MUTATING

    def preview(self, args: dict) -> str:
        extra = " ".join(args.get("args", []))
        return f"git: $ git {args.get('subcommand', '')} {extra}".rstrip()

    def execute(self, args: dict) -> ToolResult:
        sub = args["subcommand"]
        if sub not in ALLOWED:
            return ToolResult(
                f"git subcommand '{sub}' not allowed. Allowed: {', '.join(ALLOWED)}",
                error=True,
            )
        extra = [str(a) for a in args.get("args", [])]
        try:
            proc = subprocess.run(
                ["git", sub, *extra],
                cwd=self.workspace.root,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            return ToolResult("git command timed out", error=True)
        output = (proc.stdout + proc.stderr).strip() or "(no output)"
        return ToolResult(output, error=proc.returncode != 0)
