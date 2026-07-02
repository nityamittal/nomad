from pathlib import Path

from nomad_agent.config import Config, load_config
from nomad_agent.logging_setup import TraceLog
from nomad_agent.tokens import estimate_tokens, truncate_to_tokens


def test_defaults_without_config_file(tmp_path):
    cfg = load_config(tmp_path)
    assert cfg.model.provider == "ollama"
    assert cfg.agent.max_iterations == 25
    assert cfg.state_path == (tmp_path / ".nomad").resolve()


def test_load_from_toml(tmp_path):
    (tmp_path / "nomad.toml").write_text(
        """
[model]
provider = "mock"
name = "test-model"
num_ctx = 4096

[agent]
max_iterations = 5

[paths]
state_dir = ".state"
"""
    )
    cfg = load_config(tmp_path)
    assert cfg.model.provider == "mock"
    assert cfg.model.name == "test-model"
    assert cfg.model.num_ctx == 4096
    assert cfg.agent.max_iterations == 5
    assert cfg.state_dir == Path(".state")


def test_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("NOMAD_MODEL_NAME", "llama3")
    monkeypatch.setenv("NOMAD_AGENT_MAX-ITERATIONS", "ignored")  # malformed, skipped
    monkeypatch.setenv("NOMAD_MODEL_TEMPERATURE", "0.7")
    cfg = load_config(tmp_path)
    assert cfg.model.name == "llama3"
    assert cfg.model.temperature == 0.7


def test_ensure_state_dirs(tmp_path):
    cfg = Config(project_root=tmp_path)
    cfg.ensure_state_dirs()
    for sub in ("logs", "sessions", "cache", "index"):
        assert (tmp_path / ".nomad" / sub).is_dir()


def test_trace_log_roundtrip(tmp_path):
    trace = TraceLog(tmp_path)
    trace.record("request", {"messages": [{"role": "user", "content": "hi"}]})
    trace.record("response", {"content": "hello"})
    events = trace.read_all()
    assert [e["kind"] for e in events] == ["request", "response"]
    assert events[0]["payload"]["messages"][0]["content"] == "hi"


def test_token_estimate_and_truncation():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd" * 100) == 100
    long = "x" * 10_000
    cut = truncate_to_tokens(long, 100)
    assert len(cut) < len(long)
    assert "output truncated" in cut
    assert truncate_to_tokens("short", 100) == "short"
