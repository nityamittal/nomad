import pytest

from nomad_agent.config import AgentConfig, Config
from nomad_agent.conversation import Session
from nomad_agent.loop import AgentLoop
from nomad_agent.models import MockClient
from nomad_agent.models.base import ModelResponse, ToolCall
from nomad_agent.permissions import ApprovalGate, AuditLog
from nomad_agent.sandbox import CommandSandbox
from nomad_agent.tools import Workspace, default_registry


class FakeTool:
    name = "write_file"


def test_gate_auto_and_deny(tmp_path):
    assert ApprovalGate("auto", tmp_path)(FakeTool(), {}, "p") is True
    assert ApprovalGate("deny", tmp_path)(FakeTool(), {}, "p") is False
    with pytest.raises(ValueError):
        ApprovalGate("wat")


def test_gate_prompt_yes_no(tmp_path):
    answers = iter(["y", "n", "banana", "no"])
    shown = []
    gate = ApprovalGate("prompt", tmp_path, input_fn=lambda _: next(answers), print_fn=shown.append)
    assert gate(FakeTool(), {}, "the diff") is True
    assert gate(FakeTool(), {}, "the diff") is False
    # invalid answer re-asks, then "no"
    assert gate(FakeTool(), {}, "the diff") is False
    assert "the diff" in shown[0]


def test_gate_always_allow_persists(tmp_path):
    answers = iter(["a"])
    gate = ApprovalGate("prompt", tmp_path, input_fn=lambda _: next(answers), print_fn=lambda s: None)
    assert gate(FakeTool(), {}, "p") is True
    # no more scripted answers needed: always-allow kicks in
    assert gate(FakeTool(), {}, "p") is True

    fresh = ApprovalGate("prompt", tmp_path, input_fn=lambda _: 1 / 0, print_fn=lambda s: None)
    assert fresh(FakeTool(), {}, "p") is True  # loaded from disk


def test_audit_log_records_tool_calls(tmp_path):
    (tmp_path / "proj").mkdir()
    workspace = Workspace(tmp_path / "proj")
    audit = AuditLog(tmp_path / "state")
    script = [
        ModelResponse(tool_calls=[ToolCall("write_file", {"path": "a.txt", "content": "hi"})]),
        "done",
    ]
    loop = AgentLoop(
        MockClient(script),
        default_registry(workspace),
        AgentConfig(),
        approver=lambda t, a, p: True,
        audit=audit.record,
    )
    loop.run(Session(tmp_path / "state"), "write a file")
    entries = audit.read_all()
    assert len(entries) == 1
    assert entries[0]["tool"] == "write_file"
    assert entries[0]["error"] is False
    assert "ts" in entries[0]


def test_sandbox_runs_and_scrubs_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_TOKEN", "hunter2")
    sandbox = CommandSandbox(Workspace(tmp_path))
    result = sandbox.run("echo ${SECRET_TOKEN:-unset}; pwd")
    assert result.returncode == 0
    assert "unset" in result.stdout
    assert str(tmp_path.resolve()) in result.stdout


def test_sandbox_timeout_kills_process_tree(tmp_path):
    sandbox = CommandSandbox(Workspace(tmp_path))
    result = sandbox.run("sleep 30 & sleep 30", timeout_s=1)
    assert result.timed_out
    assert result.returncode == -9


def test_from_config_wires_gate_and_audit(tmp_path):
    cfg = Config(project_root=tmp_path)
    cfg.permissions.mode = "deny"
    cfg.ensure_state_dirs()
    client = MockClient(
        [
            ModelResponse(tool_calls=[ToolCall("write_file", {"path": "x", "content": "y"})]),
            "acknowledged",
        ]
    )
    loop = AgentLoop.from_config(cfg, client)
    session = Session(cfg.state_path)
    assert loop.run(session, "write x") == "acknowledged"
    assert not (tmp_path / "x").exists()  # deny mode blocked the write
    audit = AuditLog(cfg.state_path).read_all()
    assert audit[0]["tool"] == "write_file"
    assert audit[0]["error"] is True
