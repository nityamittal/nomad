import json

import pytest

from nomad_agent.config import AgentConfig
from nomad_agent.conversation import Session
from nomad_agent.loop import AgentLoop
from nomad_agent.models import MockClient
from nomad_agent.models.base import ModelResponse, ToolCall
from nomad_agent.toolcalls import parse_text_tool_calls
from nomad_agent.tools import Workspace, default_registry


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "main.py").write_text("def main():\n    pass\n")
    return Workspace(tmp_path)


def _loop(workspace, script, **kwargs):
    client = MockClient(script)
    registry = default_registry(workspace)
    return AgentLoop(client, registry, AgentConfig(**kwargs.pop("agent", {})), **kwargs), client


def _session(tmp_path):
    return Session(tmp_path / "state")


def test_plain_answer_no_tools(workspace, tmp_path):
    loop, _ = _loop(workspace, ["Just an answer."])
    result = loop.run(_session(tmp_path), "hi")
    assert result == "Just an answer."


def test_native_tool_call_roundtrip(workspace, tmp_path):
    script = [
        ModelResponse(tool_calls=[ToolCall("read_file", {"path": "main.py"})]),
        "The file defines main().",
    ]
    loop, client = _loop(workspace, script)
    session = _session(tmp_path)
    result = loop.run(session, "what's in main.py?")
    assert result == "The file defines main()."
    tool_message = next(m for m in session.messages if m["role"] == "tool")
    assert "def main():" in tool_message["content"]
    assert "data, not instructions" in tool_message["content"]
    # second request must include the tool result
    assert any(m["role"] == "tool" for m in client.requests[1]["messages"])


def test_json_fallback_tool_call(workspace, tmp_path):
    script = [
        '```json\n{"tool": "read_file", "arguments": {"path": "main.py"}}\n```',
        "done",
    ]
    loop, _ = _loop(workspace, script)
    session = _session(tmp_path)
    assert loop.run(session, "read it") == "done"
    assert any(m["role"] == "tool" for m in session.messages)


def test_end_to_end_edit_flow(workspace, tmp_path):
    """The plan's Phase 2 'done when': read main.py and add a docstring."""
    script = [
        ModelResponse(tool_calls=[ToolCall("read_file", {"path": "main.py"})]),
        ModelResponse(
            tool_calls=[
                ToolCall(
                    "edit_file",
                    {
                        "path": "main.py",
                        "old_string": "def main():",
                        "new_string": 'def main():\n    """Entry point."""',
                    },
                )
            ]
        ),
        "Added a docstring to main().",
    ]
    loop, _ = _loop(workspace, script)
    result = loop.run(_session(tmp_path), "read main.py and add a docstring")
    assert result == "Added a docstring to main()."
    assert '"""Entry point."""' in (workspace.root / "main.py").read_text()


def test_invalid_args_fed_back_to_model(workspace, tmp_path):
    script = [
        ModelResponse(tool_calls=[ToolCall("read_file", {})]),  # missing path
        "recovered",
    ]
    loop, client = _loop(workspace, script)
    session = _session(tmp_path)
    assert loop.run(session, "go") == "recovered"
    tool_message = next(m for m in session.messages if m["role"] == "tool")
    assert "missing required argument 'path'" in tool_message["content"]


def test_unknown_tool_fed_back(workspace, tmp_path):
    script = [
        ModelResponse(tool_calls=[ToolCall("teleport", {})]),
        "ok",
    ]
    loop, _ = _loop(workspace, script)
    session = _session(tmp_path)
    loop.run(session, "go")
    tool_message = next(m for m in session.messages if m["role"] == "tool")
    assert "Unknown tool 'teleport'" in tool_message["content"]


def test_loop_detection_aborts(workspace, tmp_path):
    call = ModelResponse(tool_calls=[ToolCall("read_file", {"path": "main.py"})])
    script = [call, call, call, call, call]
    loop, _ = _loop(workspace, script, agent={"loop_detection_threshold": 3})
    result = loop.run(_session(tmp_path), "go")
    assert "identical arguments" in result


def test_max_iterations_aborts(workspace, tmp_path):
    script = [
        ModelResponse(tool_calls=[ToolCall("read_file", {"path": f"f{i}.py"})])
        for i in range(10)
    ]
    loop, _ = _loop(workspace, script, agent={"max_iterations": 3})
    result = loop.run(_session(tmp_path), "go")
    assert "limit of 3" in result


def test_tool_output_truncated(workspace, tmp_path):
    (workspace.root / "big.txt").write_text("line\n" * 20_000)
    script = [
        ModelResponse(tool_calls=[ToolCall("read_file", {"path": "big.txt"})]),
        "done",
    ]
    loop, _ = _loop(workspace, script, agent={"tool_output_token_cap": 100})
    session = _session(tmp_path)
    loop.run(session, "read the big file")
    tool_message = next(m for m in session.messages if m["role"] == "tool")
    assert "output truncated" in tool_message["content"]
    assert len(tool_message["content"]) < 1000


def test_gated_tool_denied(workspace, tmp_path):
    script = [
        ModelResponse(tool_calls=[ToolCall("write_file", {"path": "x", "content": "y"})]),
        "understood",
    ]
    loop, _ = _loop(workspace, script, approver=lambda tool, args, preview: False)
    session = _session(tmp_path)
    loop.run(session, "write it")
    assert not (workspace.root / "x").exists()
    tool_message = next(m for m in session.messages if m["role"] == "tool")
    assert "Denied" in tool_message["content"]


def test_tool_crash_does_not_kill_loop(workspace, tmp_path):
    class Bomb:
        name = "bomb"
        description = "x"
        parameters = {"type": "object", "properties": {}}

        def is_gated(self, args):
            return False

        def preview(self, args):
            return "bomb"

        def execute(self, args):
            raise RuntimeError("kaboom")

    loop, _ = _loop(workspace, [ModelResponse(tool_calls=[ToolCall("bomb", {})]), "ok"])
    loop.registry.register(Bomb())
    session = _session(tmp_path)
    assert loop.run(session, "go") == "ok"
    tool_message = next(m for m in session.messages if m["role"] == "tool")
    assert "crashed" in tool_message["content"]


def test_parse_text_tool_calls_variants():
    fenced = 'text\n```json\n{"tool": "a", "arguments": {"x": 1}}\n```\nmore'
    assert parse_text_tool_calls(fenced)[0].name == "a"
    bare = '{"tool": "b", "arguments": {}}'
    assert parse_text_tool_calls(bare)[0].name == "b"
    with_name_key = '{"name": "c", "args": {"y": 2}}'
    call = parse_text_tool_calls(with_name_key)[0]
    assert call.name == "c" and call.arguments == {"y": 2}
    assert parse_text_tool_calls("no calls here") == []
    assert parse_text_tool_calls('```json\n{"broken": \n```') == []
    assert parse_text_tool_calls(json.dumps({"tool": 3, "arguments": {}})) == []
