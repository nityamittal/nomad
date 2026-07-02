import pytest

from nomad_agent.config import AgentConfig, Config
from nomad_agent.context import (
    ContextAssembler,
    FileIndex,
    SemanticIndex,
    VectorStore,
    chunk_text,
    cosine,
    keyword_search,
)
from nomad_agent.conversation import Session
from nomad_agent.logging_setup import TraceLog
from nomad_agent.loop import AgentLoop
from nomad_agent.models import MockClient
from nomad_agent.tools import Workspace, default_registry


@pytest.fixture
def project(tmp_path):
    (tmp_path / "auth.py").write_text(
        "def login(user, password):\n    return check_password(user, password)\n"
    )
    (tmp_path / "billing.py").write_text("def charge(amount):\n    return amount * 100\n")
    (tmp_path / "README.md").write_text("A demo project about authentication.\n")
    (tmp_path / ".gitignore").write_text("secret.txt\n*.log\n")
    (tmp_path / "secret.txt").write_text("password hunter2")
    (tmp_path / "app.log").write_text("login login login login")
    sub = tmp_path / "node_modules" / "pkg"
    sub.mkdir(parents=True)
    (sub / "index.js").write_text("login stuff")
    (tmp_path / "image.png").write_bytes(b"\x89PNG\x00\x00")
    return tmp_path


def test_index_respects_ignores(project):
    files = {str(f) for f in FileIndex(project).files()}
    assert "auth.py" in files
    assert "secret.txt" not in files  # .gitignore
    assert "app.log" not in files  # .gitignore glob
    assert not any("node_modules" in f for f in files)  # default ignore
    assert "image.png" not in files  # binary/extension


def test_index_skips_huge_files(project):
    (project / "huge.txt").write_text("x" * 300_000)
    assert "huge.txt" not in {str(f) for f in FileIndex(project).files()}


def test_keyword_search_ranks_relevant_file_first(project):
    hits = keyword_search(FileIndex(project), "fix the login password check")
    assert str(hits[0].path) == "auth.py"
    paths = [str(h.path) for h in hits]
    assert "billing.py" not in paths


def test_keyword_search_filename_bonus(project):
    hits = keyword_search(FileIndex(project), "billing")
    assert str(hits[0].path) == "billing.py"


def test_chunking_and_cosine():
    text = "\n".join(f"line {i}" for i in range(100))
    chunks = chunk_text(text, lines_per_chunk=40, overlap=5)
    assert len(chunks) == 3
    assert chunks[0].startswith("line 0")
    assert cosine([1, 0], [1, 0]) == pytest.approx(1.0)
    assert cosine([1, 0], [0, 1]) == pytest.approx(0.0)
    assert cosine([0, 0], [1, 1]) == 0.0


class FakeEmbedder:
    """Deterministic 'embeddings': direction encodes presence of keywords."""

    def embed(self, texts):
        return [
            [
                float(text.count("login") + text.count("password")),
                float(text.count("billing") + text.count("charge")),
                1.0,
            ]
            for text in texts
        ]


def test_vector_store_and_semantic_search(project, tmp_path):
    store = VectorStore(tmp_path / "v.db")
    semantic = SemanticIndex(store, FakeEmbedder())
    chunks = semantic.build(FileIndex(project))
    assert chunks == store.count() > 0
    results = semantic.search("login password", k=2)
    assert results[0][0] == "auth.py"


def test_assembler_includes_relevant_file_and_traces(project, tmp_path):
    trace = TraceLog(tmp_path)
    assembler = ContextAssembler(FileIndex(project), budget_tokens=2000, trace=trace)
    block = assembler.build("why does login fail?", [])
    assert block is not None
    assert "--- auth.py ---" in block
    events = [e for e in trace.read_all() if e["kind"] == "context_assembly"]
    assert events[0]["payload"]["included"] == ["auth.py"]
    assert events[0]["payload"]["used_tokens"] > 0


def test_assembler_respects_budget(project):
    assembler = ContextAssembler(FileIndex(project), budget_tokens=25)
    block = assembler.build("login authentication readme project demo", [])
    # budget of ~25 tokens fits at most one tiny section
    if block is not None:
        assert block.count("---") <= 2


def test_assembler_returns_none_when_nothing_matches(project):
    assembler = ContextAssembler(FileIndex(project), budget_tokens=2000)
    assert assembler.build("zzz qqq xyzzy", []) is None


def test_loop_injects_context(project, tmp_path):
    workspace = Workspace(project)
    assembler = ContextAssembler(FileIndex(project), budget_tokens=2000)
    client = MockClient(["answered"])
    loop = AgentLoop(
        client,
        default_registry(workspace),
        AgentConfig(),
        context_provider=assembler.build,
    )
    session = Session(tmp_path / "state")
    loop.run(session, "explain the login flow")
    sent = client.requests[0]["messages"]
    context_messages = [m for m in sent if "auto-retrieved" in m.get("content", "")]
    assert len(context_messages) == 1
    assert "auth.py" in context_messages[0]["content"]


def test_from_config_smoke_with_context(project):
    cfg = Config(project_root=project)
    cfg.permissions.mode = "auto"
    cfg.ensure_state_dirs()
    loop = AgentLoop.from_config(cfg, MockClient(["ok"]))
    assert loop.context_provider is not None
    assert loop.run(Session(cfg.state_path), "login") == "ok"
