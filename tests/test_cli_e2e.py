"""End-to-end: the real CLI against a live (stub) OpenAI-compatible server.

This exercises the full stack over an actual socket — argument parsing,
config, client creation, SSE streaming, tool-call parsing, the approval
gate in auto mode, tool execution, and session persistence.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from nomad_agent.cli import main


class StubModelHandler(BaseHTTPRequestHandler):
    """Scripted 'model': first call emits a write_file tool call, second call
    (which must contain the tool result) answers in plain text."""

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append(body)
        has_tool_result = any(m["role"] == "tool" for m in body["messages"])
        if not has_tool_result:
            events = [
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "function": {
                                            "name": "write_file",
                                            "arguments": json.dumps(
                                                {"path": "hello.txt", "content": "hello from the stub\n"}
                                            ),
                                        },
                                    }
                                ]
                            }
                        }
                    ]
                }
            ]
        else:
            events = [
                {"choices": [{"delta": {"content": "Created "}}]},
                {"choices": [{"delta": {"content": "hello.txt"}}]},
            ]
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for event in events:
            self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")

    def log_message(self, *args):
        pass


@pytest.fixture
def stub_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), StubModelHandler)
    server.requests = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()


def test_cli_once_full_stack(stub_server, tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("NOMAD_MODEL_BASE-URL", "ignored")  # malformed, must be skipped
    port = stub_server.server_address[1]
    (tmp_path / "nomad.toml").write_text(
        f"""
[model]
provider = "openai-compat"
name = "stub-model"
base_url = "http://127.0.0.1:{port}"
max_retries = 0

[permissions]
mode = "auto"
"""
    )
    exit_code = main(["--project", str(tmp_path), "--once", "create hello.txt"])
    assert exit_code == 0
    # the tool actually ran against the workspace
    assert (tmp_path / "hello.txt").read_text() == "hello from the stub\n"
    # streamed answer reached stdout
    assert "Created hello.txt" in capsys.readouterr().out
    # second model request carried the tool result back
    followup = stub_server.requests[1]["messages"]
    assert any(m["role"] == "tool" and "hello.txt" in m["content"] for m in followup)
    # the session was persisted with the full exchange
    sessions = list((tmp_path / ".nomad" / "sessions").glob("*.json"))
    assert len(sessions) == 1
    saved = json.loads(sessions[0].read_text())["messages"]
    assert saved[0]["role"] == "system"
    assert saved[-1]["content"] == "Created hello.txt"


def test_cli_resume_continues_latest_session(stub_server, tmp_path, capsys):
    port = stub_server.server_address[1]
    (tmp_path / "nomad.toml").write_text(
        f"""
[model]
provider = "openai-compat"
name = "stub-model"
base_url = "http://127.0.0.1:{port}"
max_retries = 0

[permissions]
mode = "auto"
"""
    )
    assert main(["--project", str(tmp_path), "--once", "make the file"]) == 0
    assert main(["--project", str(tmp_path), "--resume", "--once", "make it again"]) == 0
    sessions = list((tmp_path / ".nomad" / "sessions").glob("*.json"))
    assert len(sessions) == 1  # resumed, not forked
    saved = json.loads(sessions[0].read_text())["messages"]
    user_turns = [m for m in saved if m["role"] == "user"]
    assert [m["content"] for m in user_turns[:1]] == ["make the file"]
    assert any("make it again" in m["content"] for m in user_turns)
