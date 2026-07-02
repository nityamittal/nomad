import json
from pathlib import Path

from nomad_agent.config import AgentConfig, Config
from nomad_agent.conversation import Session
from nomad_agent.evals import (
    BenchmarkTask,
    VerifiedLoop,
    Verifier,
    detect_verify_command,
    run_benchmark,
)
from nomad_agent.loop import AgentLoop
from nomad_agent.models import MockClient
from nomad_agent.models.base import ModelResponse, ToolCall
from nomad_agent.sandbox import CommandSandbox
from nomad_agent.tools import Workspace, default_registry


def test_detect_verify_command(tmp_path):
    assert detect_verify_command(tmp_path) is None
    (tmp_path / "Makefile").write_text("build:\n\techo hi\n")
    assert detect_verify_command(tmp_path) is None  # no test target
    (tmp_path / "Makefile").write_text("test:\n\techo hi\n")
    assert detect_verify_command(tmp_path) == "make test"
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    assert detect_verify_command(tmp_path) == "python3 -m pytest -q"
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "jest"}}))
    assert detect_verify_command(tmp_path) == "npm test --silent"


def test_verifier_pass_and_fail(tmp_path):
    sandbox = CommandSandbox(Workspace(tmp_path))
    ok = Verifier(sandbox, command="echo '2 passed in 0.1s'; exit 0").run()
    assert ok.passed
    assert ok.summary == "2 passed in 0.1s"
    bad = Verifier(sandbox, command="echo boom; exit 1").run()
    assert not bad.passed
    assert Verifier(sandbox, command=None).command is None
    assert Verifier(sandbox, command=None).run() is None


def _agent(workspace, script):
    return AgentLoop(MockClient(script), default_registry(workspace), AgentConfig())


def test_verified_loop_passes_through_when_green(tmp_path):
    (tmp_path / "ok.txt").write_text("x")
    workspace = Workspace(tmp_path)
    verifier = Verifier(CommandSandbox(workspace), command="test -f ok.txt")
    loop = VerifiedLoop(_agent(workspace, ["done"]), verifier)
    result = loop.run(Session(tmp_path / "state"), "trivial task")
    assert result.startswith("done")
    assert "[verified:" in result


def test_verified_loop_feeds_failure_back_and_fixes(tmp_path):
    workspace = Workspace(tmp_path)
    verifier = Verifier(CommandSandbox(workspace), command="test -f ok.txt")
    # attempt 1: model claims done (but ok.txt missing) -> failure fed back,
    # attempt 2: model actually creates the file.
    script = [
        "done (allegedly)",
        ModelResponse(tool_calls=[ToolCall("write_file", {"path": "ok.txt", "content": "x"})]),
        "created the file, done",
    ]
    session = Session(tmp_path / "state")
    result = VerifiedLoop(_agent(workspace, script), verifier).run(session, "make ok.txt exist")
    assert "[verified:" in result
    fix_requests = [
        m for m in session.messages if m["role"] == "user" and "Verification failed" in m["content"]
    ]
    assert len(fix_requests) == 1


def test_verified_loop_refuses_to_claim_success(tmp_path):
    workspace = Workspace(tmp_path)
    verifier = Verifier(CommandSandbox(workspace), command="exit 1")
    script = ["done!", "fixed!", "really fixed!"]
    result = VerifiedLoop(_agent(workspace, script), verifier, max_fix_rounds=2).run(
        Session(tmp_path / "state"), "impossible"
    )
    assert result.startswith("[NOT verified]")
    assert "Do not treat this task as done" in result


def _scripted_factory(scripts):
    """Yield a fresh MockClient per task from a queue of scripts."""
    queue = list(scripts)

    def factory():
        return MockClient(queue.pop(0))

    return factory


def _plain_loop_factory(cfg, client):
    workspace = Workspace(cfg.project_root)
    return AgentLoop(client, default_registry(workspace), cfg.agent)


def test_run_benchmark_mixed_results():
    tasks = [
        BenchmarkTask(
            name="creates-file",
            prompt="create hello.txt saying hi",
            check={"type": "file_contains", "path": "hello.txt", "text": "hi"},
        ),
        BenchmarkTask(
            name="lazy-model-fails",
            prompt="create absent.txt",
            check={"type": "file_contains", "path": "absent.txt", "text": "x"},
        ),
        BenchmarkTask(
            name="command-check",
            prompt="make a python file",
            files={"mod.py": "value = 41\n"},
            check={"type": "command_succeeds", "command": "python3 -c 'import mod; exit(0 if mod.value == 42 else 1)'"},
        ),
    ]
    scripts = [
        [
            ModelResponse(tool_calls=[ToolCall("write_file", {"path": "hello.txt", "content": "hi"})]),
            "done",
        ],
        ["I did it (no I didn't)"],
        [
            ModelResponse(
                tool_calls=[ToolCall("edit_file", {"path": "mod.py", "old_string": "41", "new_string": "42"})]
            ),
            "fixed",
        ],
    ]
    report = run_benchmark(
        tasks,
        client_factory=_scripted_factory(scripts),
        model_label="mock:test",
        loop_factory=_plain_loop_factory,
    )
    assert report.total == 3
    assert report.passed == 2
    by_name = {r.name: r for r in report.results}
    assert by_name["creates-file"].passed
    assert not by_name["lazy-model-fails"].passed
    assert "not created" in by_name["lazy-model-fails"].detail
    assert by_name["command-check"].passed
    assert "2/3 passed" in report.render()


def test_builtin_suite_loads():
    path = Path(__file__).parent.parent / "src" / "nomad_agent" / "evals" / "tasks.json"
    tasks = BenchmarkTask.load_suite(path)
    assert len(tasks) >= 3
    assert all(t.check.get("type") in ("file_contains", "command_succeeds") for t in tasks)


def test_config_smoke_for_benchmark_uses_auto_permissions():
    cfg = Config()
    cfg.permissions.mode = "auto"
    assert cfg.permissions.mode == "auto"
