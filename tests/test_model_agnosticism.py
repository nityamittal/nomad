"""Phase 10: the harness must not care which model is underneath."""

import inspect

import pytest

from nomad_agent.config import Config, ModelConfig
from nomad_agent.evals import BenchmarkReport, TaskResult, render_comparison
from nomad_agent.models import MockClient, OllamaClient, create_client
from nomad_agent.models.base import ModelClient
from nomad_agent.models.openai_compat import OpenAICompatClient


def _events(*items):
    yield from items


def test_openai_compat_streams_content():
    client = OpenAICompatClient(ModelConfig())
    tokens = []
    response = client._consume(
        _events(
            {"choices": [{"delta": {"content": "He"}}]},
            {"choices": [{"delta": {"content": "y"}}]},
            {"choices": [{"delta": {}}], "usage": {"prompt_tokens": 5, "completion_tokens": 2}},
        ),
        on_token=tokens.append,
    )
    assert response.content == "Hey"
    assert tokens == ["He", "y"]
    assert response.prompt_tokens == 5


def test_openai_compat_assembles_streamed_tool_calls():
    client = OpenAICompatClient(ModelConfig())
    response = client._consume(
        _events(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {"index": 0, "function": {"name": "read_file", "arguments": '{"pa'}}
                            ]
                        }
                    }
                ]
            },
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {"index": 0, "function": {"arguments": 'th": "a.py"}'}}
                            ]
                        }
                    }
                ]
            },
        ),
        on_token=None,
    )
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == "read_file"
    assert response.tool_calls[0].arguments == {"path": "a.py"}


def test_openai_compat_malformed_args_preserved_raw():
    client = OpenAICompatClient(ModelConfig())
    response = client._consume(
        _events(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {"index": 0, "function": {"name": "x", "arguments": "{oops"}}
                            ]
                        }
                    }
                ]
            }
        ),
        on_token=None,
    )
    assert response.tool_calls[0].arguments == {"_raw": "{oops"}


def test_swapping_provider_is_one_config_value(tmp_path):
    cfg = Config(project_root=tmp_path)
    cfg.model.provider = "ollama"
    assert isinstance(create_client(cfg), OllamaClient)
    cfg.model.provider = "openai-compat"
    assert isinstance(create_client(cfg), OpenAICompatClient)
    cfg.model.provider = "mock"
    assert isinstance(create_client(cfg), MockClient)


def test_all_adapters_conform_to_modelclient():
    for adapter in (OllamaClient, OpenAICompatClient, MockClient):
        assert issubclass(adapter, ModelClient)
        signature = inspect.signature(adapter.send)
        assert list(signature.parameters) == ["self", "messages", "tools", "on_token"]


def test_no_subsystem_imports_a_concrete_adapter():
    """The seam check: only the model package and tests may name adapters."""
    import pathlib

    src = pathlib.Path(__file__).parent.parent / "src" / "nomad_agent"
    offenders = []
    for path in src.rglob("*.py"):
        if "models" in path.parts:
            continue
        text = path.read_text()
        for needle in ("OllamaClient", "OpenAICompatClient"):
            if needle in text:
                offenders.append(f"{path.name}: {needle}")
    assert offenders == []


def test_render_comparison_table():
    a = BenchmarkReport(
        model="ollama:qwen",
        results=[TaskResult("t1", True), TaskResult("t2", False)],
    )
    b = BenchmarkReport(
        model="mock:x",
        results=[TaskResult("t1", True), TaskResult("t2", True)],
    )
    table = render_comparison([a, b])
    assert "ollama:qwen" in table and "mock:x" in table
    assert "1/2" in table and "2/2" in table
    lines = table.splitlines()
    t2_line = next(l for l in lines if l.startswith("t2"))
    assert "FAIL" in t2_line and "PASS" in t2_line
