from .base import Tool, ToolRegistry, ToolResult
from .files import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
from .gitops import GitTool
from .search import WebSearchTool
from .shell import RunCommandTool
from .workspace import Workspace, WorkspaceError

__all__ = [
    "Tool",
    "ToolRegistry",
    "ToolResult",
    "Workspace",
    "WorkspaceError",
    "default_registry",
]


def default_registry(workspace: Workspace) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in (
        ReadFileTool(workspace),
        WriteFileTool(workspace),
        EditFileTool(workspace),
        ListDirTool(workspace),
        RunCommandTool(workspace),
        WebSearchTool(),
        GitTool(workspace),
    ):
        registry.register(tool)
    return registry
