from nomad_agent.conversation import Session


def test_session_persists_and_reloads(tmp_path):
    session = Session(tmp_path)
    session.append({"role": "user", "content": "hello"})
    session.append({"role": "assistant", "content": "hi"})

    reloaded = Session(tmp_path, session_id=session.id)
    assert [m["content"] for m in reloaded.messages] == ["hello", "hi"]


def test_latest_returns_most_recent(tmp_path):
    a = Session(tmp_path, session_id="1000-aaaa")
    a.append({"role": "user", "content": "first"})
    b = Session(tmp_path, session_id="2000-bbbb")
    b.append({"role": "user", "content": "second"})

    latest = Session.latest(tmp_path)
    assert latest is not None
    assert latest.id == "2000-bbbb"
    assert latest.messages[0]["content"] == "second"


def test_latest_none_when_empty(tmp_path):
    assert Session.latest(tmp_path) is None
