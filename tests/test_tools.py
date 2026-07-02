import pytest

from nomad_agent.tools import Workspace, WorkspaceError, default_registry
from nomad_agent.tools.files import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
from nomad_agent.tools.gitops import GitTool
from nomad_agent.tools.search import WebSearchTool, parse_ddg_html
from nomad_agent.tools.shell import RunCommandTool


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "main.py").write_text("def main():\n    pass\n")
    return Workspace(tmp_path)


def test_workspace_blocks_escape(workspace):
    with pytest.raises(WorkspaceError):
        workspace.resolve("../outside.txt")
    with pytest.raises(WorkspaceError):
        workspace.resolve("/etc/passwd")


def test_read_file(workspace):
    result = ReadFileTool(workspace).execute({"path": "main.py"})
    assert not result.error
    assert "1\tdef main():" in result.output


def test_read_file_slice(workspace):
    result = ReadFileTool(workspace).execute({"path": "main.py", "start_line": 2, "end_line": 2})
    assert result.output.endswith("2\t    pass")


def test_read_missing_file(workspace):
    result = ReadFileTool(workspace).execute({"path": "nope.py"})
    assert result.error


def test_write_file_and_diff_preview(workspace):
    tool = WriteFileTool(workspace)
    preview = tool.preview({"path": "new.txt", "content": "hello\n"})
    assert "+hello" in preview
    result = tool.execute({"path": "new.txt", "content": "hello\n"})
    assert "Created" in result.output
    assert (workspace.root / "new.txt").read_text() == "hello\n"


def test_edit_file(workspace):
    tool = EditFileTool(workspace)
    preview = tool.preview({"path": "main.py", "old_string": "pass", "new_string": "return 1"})
    assert "-    pass" in preview and "+    return 1" in preview
    result = tool.execute({"path": "main.py", "old_string": "pass", "new_string": "return 1"})
    assert not result.error
    assert "return 1" in (workspace.root / "main.py").read_text()


def test_edit_file_requires_unique_match(workspace):
    (workspace.root / "dup.txt").write_text("x\nx\n")
    result = EditFileTool(workspace).execute(
        {"path": "dup.txt", "old_string": "x", "new_string": "y"}
    )
    assert result.error
    assert "2 times" in result.output


def test_edit_file_not_found_message(workspace):
    result = EditFileTool(workspace).execute(
        {"path": "main.py", "old_string": "missing", "new_string": "y"}
    )
    assert result.error
    assert "not found" in result.output


def test_list_dir(workspace):
    result = ListDirTool(workspace).execute({})
    assert "main.py" in result.output


def test_run_command(workspace):
    result = RunCommandTool(workspace).execute({"command": "echo hi"})
    assert "hi" in result.output
    assert "[exit code: 0]" in result.output
    assert not result.error


def test_run_command_failure_and_stderr(workspace):
    result = RunCommandTool(workspace).execute({"command": "echo oops >&2; exit 3"})
    assert result.error
    assert "[stderr]" in result.output
    assert "[exit code: 3]" in result.output


def test_run_command_timeout(workspace):
    result = RunCommandTool(workspace).execute({"command": "sleep 5", "timeout_s": 1})
    assert result.error
    assert "timed out" in result.output


def test_git_tool_status_and_gating(tmp_path):
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    workspace = Workspace(tmp_path)
    tool = GitTool(workspace)
    assert not tool.is_gated({"subcommand": "status"})
    assert tool.is_gated({"subcommand": "commit"})
    result = tool.execute({"subcommand": "status"})
    assert not result.error


def test_git_tool_rejects_unknown_subcommand(workspace):
    result = GitTool(workspace).execute({"subcommand": "push"})
    assert result.error


DDG_FIXTURE = """
<div class="result">
  <a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fdocs&amp;rut=abc">Example &amp; Docs</a>
  <a class="result__snippet" href="#">The <b>best</b> documentation.</a>
</div>
<div class="result">
  <a rel="nofollow" class="result__a" href="https://plain.example.org">Plain Result</a>
  <a class="result__snippet" href="#">Another snippet.</a>
</div>
"""


def test_parse_ddg_html():
    results = parse_ddg_html(DDG_FIXTURE)
    assert results[0]["title"] == "Example & Docs"
    assert results[0]["url"] == "https://example.com/docs"
    assert results[0]["snippet"] == "The best documentation."
    assert results[1]["url"] == "https://plain.example.org"


def test_web_search_tool_formats_results(monkeypatch):
    tool = WebSearchTool()
    monkeypatch.setattr(tool, "_fetch", lambda q: DDG_FIXTURE)
    result = tool.execute({"query": "docs"})
    assert "1. Example & Docs" in result.output
    assert "https://example.com/docs" in result.output


def test_registry_validation(workspace):
    registry = default_registry(workspace)
    assert registry.validate("read_file", {"path": "a"}) is None
    assert "missing required" in registry.validate("read_file", {})
    assert "should be string" in registry.validate("read_file", {"path": 3})
    assert "Unknown tool" in registry.validate("nope", {})
    assert "must be one of" in registry.validate("git", {"subcommand": "push"})
    assert registry.schemas()[0]["type"] == "function"
