from nomad_agent.compression import (
    SUMMARY_MARKER,
    CachingClient,
    HistoryCompressor,
    hierarchical_summary,
)
from nomad_agent.config import Config
from nomad_agent.models import MockClient
from nomad_agent.models.base import ModelResponse, ToolCall


def _history(n_turns: int, chars_per_message: int = 400) -> list[dict]:
    messages = [{"role": "system", "content": "You are Nomad."}]
    for i in range(n_turns):
        messages.append({"role": "user", "content": f"question {i} " + "x" * chars_per_message})
        messages.append({"role": "assistant", "content": f"answer {i} " + "y" * chars_per_message})
    return messages


def test_compressor_noop_under_budget():
    compressor = HistoryCompressor(MockClient([]), budget_tokens=100_000)
    messages = _history(3)
    assert compressor(messages) is messages


def test_compressor_summarizes_once_and_reuses():
    client = MockClient(["- did stuff; touched a.py"])  # exactly ONE summary allowed
    compressor = HistoryCompressor(client, budget_tokens=1000, keep_recent=4)
    messages = _history(20)

    view = compressor(messages)
    assert view[0]["role"] == "system"  # main system prompt survives
    assert SUMMARY_MARKER in view[1]["content"]
    assert view[-1] == messages[-1]  # tail verbatim
    assert len(view) == 2 + 4

    # conversation continues: same compaction is reused, no new summary call
    grown = messages + [{"role": "user", "content": "next question"}]
    view2 = compressor(grown)
    assert SUMMARY_MARKER in view2[1]["content"]
    assert view2[-1]["content"] == "next question"
    assert len(client.requests) == 1  # stable prefix: summarized exactly once


def test_compressor_summary_prompt_contains_history():
    client = MockClient(["summary"])
    compressor = HistoryCompressor(client, budget_tokens=500, keep_recent=2)
    compressor(_history(10))
    sent = client.requests[0]["messages"][0]["content"]
    assert "question 0" in sent
    assert "question 9" not in sent  # recent tail is not summarized


def test_compressor_drops_stale_compaction_for_new_session():
    client = MockClient(["summary A", "summary B"])
    compressor = HistoryCompressor(client, budget_tokens=1000, keep_recent=2)
    compressor(_history(10))
    different = _history(12, chars_per_message=500)
    view = compressor(different)
    assert len(client.requests) == 2  # re-summarized for the diverged history
    assert SUMMARY_MARKER in view[1]["content"]


def test_caching_client_hits_disk(tmp_path):
    inner = MockClient(
        [ModelResponse(content="expensive", tool_calls=[ToolCall("t", {"a": 1})])]
    )
    client = CachingClient(inner, tmp_path)
    messages = [{"role": "user", "content": "q"}]

    first = client.send(messages)
    assert first.content == "expensive"
    assert (client.hits, client.misses) == (0, 1)

    tokens = []
    second = client.send(messages, on_token=tokens.append)
    assert second.content == "expensive"
    assert second.tool_calls[0].name == "t"
    assert tokens == ["expensive"]  # replayed for streaming consumers
    assert (client.hits, client.misses) == (1, 1)
    assert len(inner.requests) == 1  # inner model called exactly once

    # different conversation -> miss (script exhausted -> error proves it tried)
    import pytest
    from nomad_agent.models import ModelError

    with pytest.raises(ModelError):
        client.send([{"role": "user", "content": "different"}])


def test_create_client_wraps_with_cache(tmp_path):
    cfg = Config(project_root=tmp_path)
    cfg.model.provider = "mock"
    cfg.model.cache_responses = True
    from nomad_agent.models import create_client

    client = create_client(cfg)
    assert isinstance(client, CachingClient)
    assert (cfg.state_path / "cache" / "responses").is_dir()


def test_hierarchical_summary_small_text_single_call():
    client = MockClient(["short summary"])
    assert hierarchical_summary(client, "tiny text") == "short summary"
    assert len(client.requests) == 1


def test_hierarchical_summary_large_text_chunks_then_combines():
    text = "z" * 40_000  # ~10k tokens -> chunks at 1500 tokens each
    script = [f"part {i}" for i in range(7)] + ["combined summary"]
    client = MockClient(script)
    result = hierarchical_summary(client, text, chunk_tokens=1500)
    assert result == "combined summary"
    assert len(client.requests) == 8
    assert client.requests[-1]["messages"][0]["content"].startswith("Combine these")
