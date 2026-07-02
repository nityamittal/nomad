import pytest

from nomad_agent.config import Config
from nomad_agent.conversation import Session
from nomad_agent.loop import AgentLoop
from nomad_agent.models import MockClient
from nomad_agent.models.base import ModelResponse, ToolCall
from nomad_agent.orchestrator import (
    BUILTIN_ROLES,
    DelegateTool,
    Orchestrator,
    SubAgentFactory,
    filtered_registry,
)
from nomad_agent.tools import Workspace, default_registry


@pytest.fixture
def cfg(tmp_path):
    (tmp_path / "proj").mkdir()
    (tmp_path / "proj" / "app.py").write_text("def run():\n    return 1\n")
    config = Config(project_root=tmp_path / "proj")
    config.permissions.mode = "auto"
    config.ensure_state_dirs()
    return config


def _factory(cfg, script):
    workspace = Workspace(cfg.project_root)
    registry = default_registry(workspace)
    return SubAgentFactory(cfg, MockClient(script), registry)


def test_filtered_registry_restricts_tools(cfg):
    base = default_registry(Workspace(cfg.project_root))
    reviewer_tools = filtered_registry(base, BUILTIN_ROLES["reviewer"].allowed_tools)
    assert "read_file" in reviewer_tools.names()
    assert "write_file" not in reviewer_tools.names()
    assert "run_command" not in reviewer_tools.names()
    unrestricted = filtered_registry(base, None)
    assert "write_file" in unrestricted.names()


def test_subagent_runs_in_fresh_context_with_role_prompt(cfg):
    factory = _factory(cfg, ["reviewed: looks fine. APPROVE"])
    summary = factory.run("reviewer", "review app.py")
    assert summary.startswith("reviewed")
    request = factory.client.requests[0]["messages"]
    assert request[0]["role"] == "system"
    assert "code reviewer" in request[0]["content"]
    # fresh context: only the role prompt and the task
    assert len(request) == 2
    assert request[1]["content"] == "review app.py"


def test_subagent_unknown_role(cfg):
    factory = _factory(cfg, [])
    assert "Unknown role" in factory.run("wizard", "abracadabra")


def test_reviewer_cannot_write(cfg):
    script = [
        ModelResponse(tool_calls=[ToolCall("write_file", {"path": "x", "content": "y"})]),
        "fine, I only reviewed. APPROVE",
    ]
    factory = _factory(cfg, script)
    factory.run("reviewer", "review and sneakily edit")
    assert not (cfg.project_root / "x").exists()
    # the denial came back as an unknown-tool error (tool absent from registry)
    tool_feedback = [
        m for m in factory.client.requests[1]["messages"] if m["role"] == "tool"
    ]
    assert "Unknown tool 'write_file'" in tool_feedback[0]["content"]


def test_code_and_review_pipeline(cfg):
    """Phase 8 'done when': the reviewer critiques the coder's output."""
    script = [
        # coder
        ModelResponse(
            tool_calls=[ToolCall("write_file", {"path": "feature.py", "content": "def f():\n    return 2\n"})]
        ),
        "Implemented feature.py with f() returning 2.",
        # reviewer
        ModelResponse(tool_calls=[ToolCall("read_file", {"path": "feature.py"})]),
        "REQUEST_CHANGES: f() lacks a docstring.",
    ]
    factory = _factory(cfg, script)
    result = Orchestrator(factory).code_and_review("add feature.py")
    assert (cfg.project_root / "feature.py").is_file()
    assert "Implemented feature.py" in result["implementation"]
    assert "REQUEST_CHANGES" in result["review"]
    # the reviewer was told what the coder did
    reviewer_task = factory.client.requests[2]["messages"][1]["content"]
    assert "Implemented feature.py" in reviewer_task


def test_run_jobs_sequential_preserves_order(cfg):
    factory = _factory(cfg, ["first result", "second result"])
    results = Orchestrator(factory).run_jobs(
        [("planner", "plan a"), ("planner", "plan b")]
    )
    assert results == ["first result", "second result"]


def test_delegate_tool_from_main_loop(cfg):
    """Main agent delegates to a reviewer; sub-agent shares the same client script."""
    script = [
        # main agent decides to delegate
        ModelResponse(tool_calls=[ToolCall("delegate", {"role": "reviewer", "task": "review app.py"})]),
        # sub-agent (same mock client) answers
        "APPROVE — clean code.",
        # main agent wraps up with the sub-agent's result in context
        "The reviewer approved.",
    ]
    client = MockClient(script)
    loop = AgentLoop.from_config(cfg, client)
    session = Session(cfg.state_path)
    result = loop.run(session, "have someone review app.py")
    assert result == "The reviewer approved."
    tool_message = next(m for m in session.messages if m["role"] == "tool")
    assert "[reviewer agent] APPROVE" in tool_message["content"]


def test_subagents_never_get_delegate_tool(cfg):
    base = default_registry(Workspace(cfg.project_root))

    class FakeOrchestrator:
        def delegate(self, role, task):
            return "nope"

    base.register(DelegateTool(FakeOrchestrator()))
    sub = filtered_registry(base, None)
    assert "delegate" not in sub.names()
