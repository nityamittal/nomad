from nomad_agent.config import Config
from nomad_agent.conversation import Session
from nomad_agent.loop import AgentLoop
from nomad_agent.memory import ProjectMemory, RememberTool
from nomad_agent.models import MockClient
from nomad_agent.models.base import ModelResponse, ToolCall


def test_read_missing_and_empty(tmp_path):
    memory = ProjectMemory(tmp_path)
    assert memory.read() is None
    (tmp_path / "NOMAD.md").write_text("   \n")
    assert memory.read() is None


def test_append_creates_file_and_sections(tmp_path):
    memory = ProjectMemory(tmp_path)
    memory.append_note("tests run with pytest", section="Conventions")
    memory.append_note("use 4-space indents", section="Conventions")
    memory.append_note("v2 API is deprecated", section="Decisions")
    content = (tmp_path / "NOMAD.md").read_text()
    assert content.startswith("# NOMAD.md")
    conventions = content.split("## Conventions")[1].split("## Decisions")[0]
    assert "- tests run with pytest" in conventions
    assert "- use 4-space indents" in conventions
    assert "- v2 API is deprecated" in content.split("## Decisions")[1]


def test_append_inserts_before_next_section(tmp_path):
    (tmp_path / "NOMAD.md").write_text("# NOMAD.md\n\n## Notes\n\n- old note\n\n## Other\n\n- x\n")
    ProjectMemory(tmp_path).append_note("new note")
    content = (tmp_path / "NOMAD.md").read_text()
    notes_section = content.split("## Notes")[1].split("## Other")[0]
    assert "- old note" in notes_section
    assert "- new note" in notes_section


def test_remember_tool(tmp_path):
    memory = ProjectMemory(tmp_path)
    result = RememberTool(memory).execute({"note": "ship it"})
    assert not result.error
    assert "ship it" in (tmp_path / "NOMAD.md").read_text()


def _configured_loop(project_root, script):
    cfg = Config(project_root=project_root)
    cfg.permissions.mode = "auto"
    cfg.ensure_state_dirs()
    return AgentLoop.from_config(cfg, MockClient(script)), cfg


def test_agent_remembers_and_fresh_session_recalls(tmp_path):
    # Session 1: the model files a memory via the remember tool.
    loop, cfg = _configured_loop(
        tmp_path,
        [
            ModelResponse(
                tool_calls=[
                    ToolCall("remember", {"note": "run tests with pytest -q", "section": "Conventions"})
                ]
            ),
            "noted",
        ],
    )
    assert loop.run(Session(cfg.state_path), "remember our test convention") == "noted"
    assert "pytest -q" in (tmp_path / "NOMAD.md").read_text()

    # Session 2 (fresh): memory is injected into context exactly once.
    loop2, _ = _configured_loop(tmp_path, ["recalled", "again"])
    session = Session(cfg.state_path)
    loop2.run(session, "what are our conventions?")
    memory_messages = [
        m
        for m in session.messages
        if m["role"] == "system" and "Project memory" in m["content"]
    ]
    assert len(memory_messages) == 1
    assert "pytest -q" in memory_messages[0]["content"]

    # a second turn in the same session must not re-inject it
    loop2.run(session, "and the indent style?")
    memory_messages = [
        m
        for m in session.messages
        if m["role"] == "system" and "Project memory" in m["content"]
    ]
    assert len(memory_messages) == 1
