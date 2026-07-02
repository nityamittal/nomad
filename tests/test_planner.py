import pytest

from nomad_agent.config import AgentConfig
from nomad_agent.conversation import Session
from nomad_agent.loop import AgentLoop
from nomad_agent.models import MockClient
from nomad_agent.planner import PlanExecutor, PlanStep, parse_plan
from nomad_agent.tools import Workspace, default_registry


PLAN_JSON = (
    'Here is the plan:\n[{"title": "Read", "instruction": "Read main.py"},'
    ' {"title": "Edit", "instruction": "Add a docstring"}]'
)


def test_parse_plan_from_noisy_text():
    steps = parse_plan(PLAN_JSON)
    assert [s.title for s in steps] == ["Read", "Edit"]
    assert steps[0].instruction == "Read main.py"


def test_parse_plan_skips_invalid_entries_and_garbage():
    text = 'x [1, 2] then [{"instruction": "do it"}, {"nope": 1}]'
    steps = parse_plan(text)
    assert len(steps) == 1
    assert steps[0].instruction == "do it"
    assert parse_plan("no json at all") == []


@pytest.fixture
def env(tmp_path):
    (tmp_path / "proj").mkdir()
    workspace = Workspace(tmp_path / "proj")
    session = Session(tmp_path / "state")
    return workspace, session


def _executor(workspace, script, max_replans=2):
    client = MockClient(script)
    loop = AgentLoop(client, default_registry(workspace), AgentConfig())
    return PlanExecutor(client, loop, max_replans=max_replans), client


def test_executes_steps_and_feeds_results_forward(env):
    workspace, session = env
    script = [
        PLAN_JSON,          # planning call
        "read it: found main()",   # step 1 execution
        "docstring added",  # step 2 execution
    ]
    executor, client = _executor(workspace, script)
    result = executor.execute(session, "add a docstring to main.py")
    assert result.completed
    assert [s.status for s in result.steps] == ["done", "done"]
    assert result.summary == "docstring added"
    # step 2's request must carry step 1's result forward
    step2_messages = client.requests[2]["messages"]
    user_texts = [m["content"] for m in step2_messages if m["role"] == "user"]
    assert any("found main()" in t for t in user_texts)
    assert any("Overall goal: add a docstring" in t for t in user_texts)


def test_unparseable_plan_falls_back_to_single_step(env):
    workspace, session = env
    executor, client = _executor(workspace, ["I refuse to emit JSON", "did the thing"])
    result = executor.execute(session, "just do it")
    assert result.completed
    assert len(result.steps) == 1
    assert result.steps[0].instruction == "just do it"


def test_replans_on_failure_then_succeeds(env):
    workspace, session = env
    script = [
        PLAN_JSON,                       # initial plan
        "STEP FAILED: main.py does not exist",   # step 1 fails
        '[{"title": "Create", "instruction": "Create main.py first"}]',  # revised plan
        "created main.py",               # revised step runs
    ]
    executor, client = _executor(workspace, script)
    result = executor.execute(session, "add a docstring")
    assert result.completed
    statuses = [(s.title, s.status) for s in result.steps]
    assert ("Read", "failed") in statuses
    assert ("Create", "done") in statuses
    # the revise prompt included the failure detail
    revise_request = client.requests[2]["messages"][0]["content"]
    assert "main.py does not exist" in revise_request


def test_gives_up_after_max_replans(env):
    workspace, session = env
    script = [
        '[{"title": "A", "instruction": "do A"}]',
        "STEP FAILED: no",
        '[{"title": "B", "instruction": "do B"}]',
        "STEP FAILED: still no",
    ]
    executor, _ = _executor(workspace, script, max_replans=1)
    result = executor.execute(session, "impossible thing")
    assert not result.completed
    assert "Gave up" in result.summary


def test_abandons_when_revision_is_empty(env):
    workspace, session = env
    script = [
        '[{"title": "A", "instruction": "do A"}]',
        "STEP FAILED: impossible",
        "[]",  # planner concedes
    ]
    executor, _ = _executor(workspace, script)
    result = executor.execute(session, "impossible")
    assert not result.completed
    assert "abandoned" in result.summary


def test_loop_abort_counts_as_failure(env):
    workspace, session = env
    script = [
        '[{"title": "A", "instruction": "do A"}]',
        "Stopped: reached the limit of 25 tool iterations for this request.",
        "[]",
    ]
    executor, _ = _executor(workspace, script)
    result = executor.execute(session, "spin forever")
    assert not result.completed
    assert result.steps[0].status == "failed"
