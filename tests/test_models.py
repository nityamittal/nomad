import json

import pytest

from nomad_agent.config import Config, ModelConfig
from nomad_agent.logging_setup import TraceLog
from nomad_agent.models import MockClient, ModelError, create_client
from nomad_agent.models.base import ModelResponse, ToolCall
from nomad_agent.models.ollama import OllamaClient


def _chunks(*items):
    yield from items


def test_ollama_consume_streams_content_and_usage(tmp_path):
    client = OllamaClient(ModelConfig(), TraceLog(tmp_path))
    tokens = []
    response = client._consume(
        _chunks(
            {"message": {"content": "Hel"}},
            {"message": {"content": "lo"}},
            {"done": True, "prompt_eval_count": 10, "eval_count": 2},
        ),
        on_token=tokens.append,
    )
    assert response.content == "Hello"
    assert tokens == ["Hel", "lo"]
    assert response.prompt_tokens == 10
    assert response.completion_tokens == 2


def test_ollama_consume_parses_tool_calls():
    client = OllamaClient(ModelConfig())
    response = client._consume(
        _chunks(
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": "read_file", "arguments": {"path": "a.py"}}},
                        {"function": {"name": "grep", "arguments": '{"pattern": "x"}'}},
                    ],
                }
            },
            {"done": True},
        ),
        on_token=None,
    )
    assert [t.name for t in response.tool_calls] == ["read_file", "grep"]
    assert response.tool_calls[1].arguments == {"pattern": "x"}


def test_ollama_retries_then_fails(monkeypatch):
    config = ModelConfig(max_retries=2)
    client = OllamaClient(config)
    attempts = []

    def failing_stream(payload):
        attempts.append(1)
        raise ConnectionError("refused")
        yield  # pragma: no cover

    monkeypatch.setattr(client, "_post_stream", failing_stream)
    monkeypatch.setattr("nomad_agent.models.ollama.time.sleep", lambda s: None)
    with pytest.raises(ModelError):
        client.send([{"role": "user", "content": "hi"}])
    assert len(attempts) == 3  # initial + 2 retries


def test_ollama_retry_succeeds_second_attempt(monkeypatch):
    config = ModelConfig(max_retries=2)
    client = OllamaClient(config)
    calls = {"n": 0}

    def flaky_stream(payload):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("slow")
        return _chunks({"message": {"content": "ok"}}, {"done": True})

    monkeypatch.setattr(client, "_post_stream", flaky_stream)
    monkeypatch.setattr("nomad_agent.models.ollama.time.sleep", lambda s: None)
    response = client.send([{"role": "user", "content": "hi"}])
    assert response.content == "ok"


def test_ollama_sends_num_ctx_and_traces(tmp_path, monkeypatch):
    trace = TraceLog(tmp_path)
    config = ModelConfig(num_ctx=8192, name="m")
    client = OllamaClient(config, trace)
    captured = {}

    def fake_stream(payload):
        captured.update(payload)
        return _chunks({"message": {"content": "hi"}}, {"done": True})

    monkeypatch.setattr(client, "_post_stream", fake_stream)
    client.send([{"role": "user", "content": "x"}])
    assert captured["options"]["num_ctx"] == 8192
    kinds = [e["kind"] for e in trace.read_all()]
    assert kinds == ["model_request", "model_response"]


def test_mock_client_scripted():
    client = MockClient(["one", ModelResponse(content="two", tool_calls=[ToolCall("t", {})])])
    r1 = client.send([{"role": "user", "content": "a"}])
    r2 = client.send([{"role": "user", "content": "b"}])
    assert r1.content == "one"
    assert r2.tool_calls[0].name == "t"
    assert len(client.requests) == 2
    with pytest.raises(ModelError):
        client.send([])


def test_create_client_factory():
    cfg = Config()
    cfg.model.provider = "mock"
    assert isinstance(create_client(cfg), MockClient)
    cfg.model.provider = "nope"
    with pytest.raises(ValueError):
        create_client(cfg)
