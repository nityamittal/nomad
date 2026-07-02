# 6. Extending Nomad — recipes

Each recipe is complete: code, wiring, and the test to prove it works.
Run `python3 -m pytest` after each one.

## 6.1 Add a new tool

Let's add `grep` — search file contents by regex. Read-only, so ungated.

**1. Write the tool** — `src/nomad_agent/tools/grep.py`:

```python
import re

from .base import Tool, ToolResult
from .workspace import Workspace


class GrepTool(Tool):
    name = "grep"
    description = "Search files in the workspace for a regex. Returns path:line matches."
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Python regex"},
            "glob": {"type": "string", "description": "filename filter, e.g. *.py (default: all)"},
        },
        "required": ["pattern"],
    }

    def __init__(self, workspace: Workspace):
        self.workspace = workspace

    def execute(self, args: dict) -> ToolResult:
        try:
            regex = re.compile(args["pattern"])
        except re.error as exc:
            return ToolResult(f"Bad regex: {exc}", error=True)  # model-readable
        matches = []
        for path in self.workspace.root.rglob(args.get("glob", "*")):
            if not path.is_file() or ".git" in path.parts or ".nomad" in path.parts:
                continue
            try:
                for number, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
                    if regex.search(line):
                        rel = path.relative_to(self.workspace.root)
                        matches.append(f"{rel}:{number}: {line.strip()[:120]}")
            except OSError:
                continue
            if len(matches) >= 100:
                matches.append("[... stopped at 100 matches — narrow the pattern ...]")
                break
        return ToolResult("\n".join(matches) or "No matches.")
```

Notice the house style: error strings are written **for the model to read
and correct itself**, and big outputs self-limit with an explicit marker.

**2. Register it** — in `tools/__init__.py`, add `GrepTool(workspace)` to
the tuple in `default_registry()` (and the import).

**3. Test it** — `tests/test_grep.py`:

```python
from nomad_agent.tools.grep import GrepTool
from nomad_agent.tools import Workspace


def test_grep_finds_matches(tmp_path):
    (tmp_path / "a.py").write_text("def login():\n    pass\n")
    result = GrepTool(Workspace(tmp_path)).execute({"pattern": r"def \w+"})
    assert "a.py:1" in result.output


def test_grep_bad_regex_is_model_readable(tmp_path):
    result = GrepTool(Workspace(tmp_path)).execute({"pattern": "("})
    assert result.error and "Bad regex" in result.output
```

That's the whole lifecycle: the registry automatically exposes your schema
to the model, validates calls against it, and the loop handles errors,
truncation, and auditing. If your tool **mutates** anything, also set
`gated = True` and implement `preview()` to return something a human can
meaningfully approve (a diff beats a JSON blob).

## 6.2 Add a model backend

Suppose a new local server "FastLLM" appears with its own protocol.

1. Create `src/nomad_agent/models/fastllm.py`:
   - subclass `ModelClient`; implement
     `send(messages, tools=None, on_token=None) -> ModelResponse`.
   - keep HTTP in an isolated `_post_stream(payload)` method (copy the shape
     of `ollama.py`) so tests can monkeypatch it with scripted chunks.
   - honor the contract: stream via `on_token`, return accumulated
     `content` + `tool_calls`, raise `ModelError` after retries, convert
     `KeyboardInterrupt` to `GenerationCancelled`.
2. Register it in `create_client()` (`models/__init__.py`) behind
   `provider = "fastllm"`. **Do not** import it anywhere else — the
   conformance test `test_no_subsystem_imports_a_concrete_adapter` will
   fail your build if you do, and it's right.
3. Copy `test_openai_compat_streams_content` in
   `tests/test_model_agnosticism.py` as a template for parsing tests, and
   add your class to `test_all_adapters_conform_to_modelclient`.
4. Prove it earns its keep: `nomad --compare-models "ollama:qwen2.5-coder,fastllm:whatever"`.

## 6.3 Add an agent role

Roles are data. In `orchestrator.py`, add to `BUILTIN_ROLES`:

```python
AgentRole(
    "doc-writer",
    "You are a documentation specialist. Read the relevant code and write "
    "or update documentation for it. Never change code files.",
    allowed_tools=["read_file", "list_dir", "write_file", "edit_file"],
),
```

The `delegate` tool picks up the new role automatically (its schema enum is
built from the role table). Test it the way `test_subagent_runs_in_fresh_
context_with_role_prompt` does: script a `MockClient`, call
`factory.run("doc-writer", ...)`, and assert on the system prompt and the
tool restrictions.

Things to decide for any new role: what can it *not* do (allowlist), and
what should its final summary contain (put that in the prompt — the summary
is all the caller ever sees).

## 6.4 Add benchmark tasks

Benchmarks are JSON — no code needed. Create `mybench.json`:

```json
[
  {
    "name": "rename-function",
    "prompt": "Rename the function calc to calculate everywhere in app.py.",
    "files": {
      "app.py": "def calc(x):\n    return x * 2\n\nprint(calc(3))\n"
    },
    "check": {
      "type": "command_succeeds",
      "command": "python3 -c \"import app; assert app.calculate(3) == 6\" && ! grep -q 'def calc(' app.py"
    }
  }
]
```

Run with `nomad --benchmark mybench.json`. Rules for good tasks:

- the check must be **mechanical** — a command's exit code or a file's
  content, never "does the answer look right";
- keep tasks independent — each gets a fresh scratch directory and client;
- keep the benchmark **separate from your dev prompts**, or you'll tune the
  harness to your own demos (the overfitting warning from the project plan).

## 6.5 Tune behavior without code

Everything in `nomad.toml` (or `NOMAD_SECTION_KEY` env vars):

| Knob | Effect |
| --- | --- |
| `model.num_ctx` | context window; raise for big tasks, costs RAM |
| `model.temperature` | 0 = deterministic, higher = more varied; keep low for coding |
| `model.cache_responses` | disk-cache identical requests (great for benchmarks) |
| `agent.max_iterations` | tool round-trips per request |
| `agent.loop_detection_threshold` | identical calls tolerated before abort |
| `agent.tool_output_token_cap` | max tokens per tool result |
| `context.retrieval_token_budget` | tokens for auto-retrieved files |
| `context.history_token_budget` | history size before compression kicks in |
| `permissions.mode` | `prompt` / `auto` / `deny` |

## 6.6 Ideas from the plan's stretch goals

- **MCP client support** — speak the Model Context Protocol so third-party
  tool servers plug in without writing adapters. Natural home: a
  `tools/mcp.py` that connects to an MCP server and registers each remote
  tool as a `Tool` whose `execute()` forwards the call.
- **Richer TUI** — markdown rendering and syntax-highlighted diffs
  (`rich` is one pip install away; the seam is `_print_token` and
  `preview()` output in `cli.py`).
- **Self-hosting test** — point Nomad at its own repository
  (`nomad --verify --plan`) and have it fix a real issue end to end. The
  ultimate integration test.

Next: [chapter 7](07-exercises.md) — exercises that walk you through the
codebase hands-on.
