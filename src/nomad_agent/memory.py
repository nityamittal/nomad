"""Project memory: conventions and decisions that survive across sessions.

Memory lives in NOMAD.md at the project root — a plain, human-editable file
(the user owns it as much as the agent does). The agent reads it at session
start and appends to it via the `remember` tool.
"""

from __future__ import annotations

from pathlib import Path

from .tools.base import Tool, ToolResult

MEMORY_MARKER = "Project memory (NOMAD.md)"

DEFAULT_HEADER = (
    "# NOMAD.md — project memory\n\n"
    "Notes the agent keeps about this project: conventions, architecture,\n"
    "decisions. Edit freely; the agent reads this file at session start.\n"
)


class ProjectMemory:
    FILENAME = "NOMAD.md"

    def __init__(self, project_root: str | Path):
        self.path = Path(project_root) / self.FILENAME

    def read(self) -> str | None:
        if not self.path.is_file():
            return None
        content = self.path.read_text().strip()
        return content or None

    def append_note(self, note: str, section: str = "Notes") -> None:
        note = note.strip()
        if not note:
            return
        content = self.path.read_text() if self.path.is_file() else DEFAULT_HEADER
        heading = f"## {section}"
        entry = f"- {note}"
        if heading in content:
            head, _, tail = content.partition(heading)
            # insert at the end of this section (before the next heading, if any)
            lines = tail.splitlines()
            insert_at = len(lines)
            for i, line in enumerate(lines[1:], start=1):
                if line.startswith("## "):
                    insert_at = i
                    break
            lines.insert(insert_at, entry)
            content = head + heading + "\n".join(lines)
            if not content.endswith("\n"):
                content += "\n"
        else:
            content = content.rstrip("\n") + f"\n\n{heading}\n\n{entry}\n"
        self.path.write_text(content)

    def context_block(self) -> str | None:
        content = self.read()
        if content is None:
            return None
        return f"{MEMORY_MARKER} — conventions and prior decisions for this project:\n\n{content}"


class RememberTool(Tool):
    name = "remember"
    description = (
        "Persist a project note (convention, decision, gotcha) to NOMAD.md so "
        "future sessions recall it. Use for durable facts, not scratch state."
    )
    parameters = {
        "type": "object",
        "properties": {
            "note": {"type": "string", "description": "one concise fact worth remembering"},
            "section": {"type": "string", "description": "heading to file it under, default 'Notes'"},
        },
        "required": ["note"],
    }

    def __init__(self, memory: ProjectMemory):
        self.memory = memory

    def execute(self, args: dict) -> ToolResult:
        self.memory.append_note(args["note"], args.get("section", "Notes"))
        return ToolResult(f"Remembered in {self.memory.path.name}: {args['note']}")
