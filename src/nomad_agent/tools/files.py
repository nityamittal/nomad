"""File tools: read, write, edit, list. Mutating tools preview as diffs."""

from __future__ import annotations

import difflib

from .base import Tool, ToolResult
from .workspace import Workspace, WorkspaceError


def _diff(old: str, new: str, path: str) -> str:
    lines = difflib.unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
    )
    return "".join(lines) or "(no changes)"


class ReadFileTool(Tool):
    name = "read_file"
    description = (
        "Read a file in the workspace. Returns numbered lines. "
        "Use start_line/end_line to read a slice of a large file."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "path relative to the workspace root"},
            "start_line": {"type": "integer", "description": "1-based first line to read"},
            "end_line": {"type": "integer", "description": "1-based last line to read"},
        },
        "required": ["path"],
    }

    def __init__(self, workspace: Workspace):
        self.workspace = workspace

    def execute(self, args: dict) -> ToolResult:
        try:
            target = self.workspace.resolve(args["path"])
            if not target.is_file():
                return ToolResult(f"File not found: {args['path']}", error=True)
            lines = target.read_text(errors="replace").splitlines()
        except WorkspaceError as exc:
            return ToolResult(str(exc), error=True)
        except UnicodeDecodeError:
            return ToolResult(f"Not a text file: {args['path']}", error=True)
        start = max(1, int(args.get("start_line", 1)))
        end = min(len(lines), int(args.get("end_line", len(lines))))
        numbered = [f"{i}\t{lines[i - 1]}" for i in range(start, end + 1)]
        header = f"{args['path']} ({len(lines)} lines total, showing {start}-{end})\n"
        return ToolResult(header + "\n".join(numbered))


class WriteFileTool(Tool):
    name = "write_file"
    description = "Create or overwrite a file in the workspace with the given content."
    gated = True
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["path", "content"],
    }

    def __init__(self, workspace: Workspace):
        self.workspace = workspace

    def preview(self, args: dict) -> str:
        try:
            target = self.workspace.resolve(args["path"])
        except WorkspaceError as exc:
            return str(exc)
        old = target.read_text(errors="replace") if target.is_file() else ""
        return f"write_file {args['path']}:\n{_diff(old, args['content'], args['path'])}"

    def execute(self, args: dict) -> ToolResult:
        try:
            target = self.workspace.resolve(args["path"])
        except WorkspaceError as exc:
            return ToolResult(str(exc), error=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        existed = target.is_file()
        target.write_text(args["content"])
        action = "Overwrote" if existed else "Created"
        return ToolResult(f"{action} {args['path']} ({len(args['content'])} bytes)")


class EditFileTool(Tool):
    name = "edit_file"
    description = (
        "Replace an exact string in a file. old_string must appear exactly once "
        "(include surrounding lines to disambiguate). Read the file first."
    )
    gated = True
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old_string": {"type": "string"},
            "new_string": {"type": "string"},
        },
        "required": ["path", "old_string", "new_string"],
    }

    def __init__(self, workspace: Workspace):
        self.workspace = workspace

    def _apply(self, args: dict) -> tuple[str, str] | str:
        """Return (old_text, new_text) or an error message."""
        try:
            target = self.workspace.resolve(args["path"])
        except WorkspaceError as exc:
            return str(exc)
        if not target.is_file():
            return f"File not found: {args['path']}"
        text = target.read_text(errors="replace")
        count = text.count(args["old_string"])
        if count == 0:
            return (
                f"old_string not found in {args['path']}. "
                "Read the file and copy the exact text, including whitespace."
            )
        if count > 1:
            return (
                f"old_string appears {count} times in {args['path']}; "
                "add surrounding lines to make it unique."
            )
        return text, text.replace(args["old_string"], args["new_string"])

    def preview(self, args: dict) -> str:
        result = self._apply(args)
        if isinstance(result, str):
            return result
        old, new = result
        return f"edit_file {args['path']}:\n{_diff(old, new, args['path'])}"

    def execute(self, args: dict) -> ToolResult:
        result = self._apply(args)
        if isinstance(result, str):
            return ToolResult(result, error=True)
        _, new = result
        self.workspace.resolve(args["path"]).write_text(new)
        return ToolResult(f"Edited {args['path']}")


class ListDirTool(Tool):
    name = "list_dir"
    description = "List files and directories at a path in the workspace."
    parameters = {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "defaults to workspace root"}},
    }

    def __init__(self, workspace: Workspace):
        self.workspace = workspace

    def execute(self, args: dict) -> ToolResult:
        try:
            target = self.workspace.resolve(args.get("path", "."))
        except WorkspaceError as exc:
            return ToolResult(str(exc), error=True)
        if not target.is_dir():
            return ToolResult(f"Not a directory: {args.get('path', '.')}", error=True)
        entries = sorted(
            f"{p.name}/" if p.is_dir() else p.name
            for p in target.iterdir()
            if p.name not in (".git", "__pycache__")
        )
        return ToolResult("\n".join(entries) or "(empty)")
