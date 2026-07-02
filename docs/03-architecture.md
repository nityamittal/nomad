# 3. Architecture — how the pieces fit

Chapter 2 gave you the concepts. This chapter maps them onto Nomad's actual
modules and then traces one request end to end.

## 3.1 The big picture

```
                            ┌──────────────────────────────┐
 you ──► cli.py (REPL) ──►  │        loop.py AgentLoop     │ ◄── the heart
                            └──────────────────────────────┘
                                 │                  ▲
             what the model      │                  │  ModelResponse
             should see          ▼                  │  (text or tool calls)
        ┌─────────────────────────────┐   ┌─────────────────────────┐
        │ context/   (what to include)│   │ models/  ModelClient    │──► Ollama /
        │ memory.py  (NOMAD.md)       │   │  ollama | openai-compat │    llama.cpp /
        │ compression.py (fit budget) │   │  | mock                 │    vLLM ...
        └─────────────────────────────┘   └─────────────────────────┘
                                 │
                tool calls       ▼
        ┌────────────────────────────────────────────┐
        │ tools/    registry + validation            │
        │   files, shell, search, git, remember,     │
        │   delegate                                 │
        │ permissions.py  approval gate + audit      │
        │ sandbox.py      confined execution         │
        └────────────────────────────────────────────┘

  wrappers around the loop:            observability everywhere:
    planner.py   (steps + re-planning)   logging_setup.py TraceLog
    evals/       (verify, benchmark)     .nomad/logs/trace.jsonl
    orchestrator.py (sub-agents)         .nomad/audit.jsonl
```

Three design rules keep this untangled:

1. **One seam for models.** Everything depends on the abstract `ModelClient`
   (`models/base.py`); only the factory `create_client()` knows concrete
   adapters. A test (`test_model_agnosticism.py`) *fails the suite* if any
   other module names an adapter.
2. **The loop takes hooks, not dependencies.** `AgentLoop` accepts optional
   callables — `approver`, `audit`, `context_provider`, `compressor`. Each
   later phase of the project plugged in a hook without touching loop logic.
   You can build a bare loop in tests with none of them.
3. **Messages are plain dicts.** `{"role": ..., "content": ...}` end to
   end — trivially JSON-serializable to session files, the trace log, and
   every provider's wire format.

## 3.2 The life of one request

Say an indexed project and a running session, and you type:

```
> fix the failing test in test_math.py
```

**1. CLI → loop.** `cli.py` reads the line and calls
`AgentLoop.run(session, text)` (or wrapped variants for `--plan`/`--verify`).

**2. Context assembly** (`context_provider` hook). Before the request is
even added, the loop asks the provider for relevant context:
- `memory.py` contributes the `NOMAD.md` block — once per session (it
  checks whether it's already in the history, so the prefix stays stable).
- `context/assembler.py` runs keyword retrieval (`retrieval.py`) over the
  file index (`indexer.py`), plus semantic search (`embeddings.py`) if a
  vector index exists. It packs the winners under
  `retrieval_token_budget`, and records included/excluded files to the
  trace log.
The result is appended as a `system` message, then your request as `user`.

**3. Compression** (`compressor` hook). `compression.py` checks the total
history against `history_token_budget`. Over budget? The old middle is
summarized (one extra model call), cached, and reused verbatim on every
subsequent turn — the session file on disk always keeps the *full* history;
only the view sent to the model shrinks.

**4. Model call.** The loop calls `client.send(messages, tools=schemas)`.
The Ollama adapter posts to `/api/chat` with `num_ctx` set explicitly,
streams tokens back (printing them via `on_token`), retries on network
errors with exponential backoff, and logs the raw request and response to
`trace.jsonl`.

**5. Parse.** The response either has native `tool_calls`, or
`toolcalls.py` fishes fenced-JSON calls out of the text (the fallback for
models without native support). No calls → the text is the answer; done.

**6. Execute each call.** For, say,
`run_command {"command": "python3 -m pytest -q"}`:
- **Validate** (`tools/base.py`): unknown tool? missing/mistyped argument?
  The error string is crafted to be *fed back to the model* as a tool
  result, so it can retry correctly.
- **Gate** (`permissions.py`): `run_command` is gated, so the tool's
  `preview()` (the command line; for edits, a unified diff) is shown and
  you answer y/n/a. Deny, and the model is told "Denied: the user did not
  approve" — it can propose something else.
- **Sandbox** (`sandbox.py`): approved commands run pinned to the project
  root, with a scrubbed environment (your API keys aren't inherited), in
  their own process group so a timeout kills the whole tree.
- **Record**: the call and outcome go to `audit.jsonl` and `trace.jsonl`.

**7. Truncate & mark.** The output is capped at `tool_output_token_cap`
(with an explicit `[... output truncated: N more characters ...]` marker so
the model knows to narrow its request) and prefixed with the untrusted-data
notice from chapter 2.6, then appended as a `tool` message.

**8. Around again.** Steps 3–7 repeat — model sees the pytest failure,
calls `read_file`, then `edit_file` (another diff, another approval), then
`run_command` again — until the model answers in plain text, or a guard
fires: `max_iterations` reached, or the loop detector sees the same
(tool, arguments) `loop_detection_threshold` times in a row.

**9. Persistence.** Every appended message hit `.nomad/sessions/<id>.json`
immediately (that's why `--resume` always works, even after a crash).

## 3.3 The wrappers around the loop

These compose *around* `AgentLoop` without changing it:

- **`--plan` → `planner.py`.** First asks the model for a JSON array of
  steps, then runs each step through the loop, feeding earlier results
  forward. A step that reports failure triggers *re-planning*: the model is
  shown what succeeded, what failed, and asked for a new remaining plan
  (bounded by `max_replans`).
- **`--verify` → `evals/verifier.py`.** Wraps the loop: after the model
  claims completion, run the project's real check (auto-detected: pytest /
  npm test / make test / cargo / go). Red? Feed the output back as a fix
  request. Still red after the fix budget? The final answer *starts with*
  `[NOT verified]` — the agent structurally cannot claim success.
- **`delegate` tool → `orchestrator.py`.** The main agent can hand a
  self-contained sub-task to a specialist role (planner / coder / reviewer /
  tester / debugger). Each sub-agent is a *fresh* `AgentLoop` — new
  conversation, role-specific system prompt, role-filtered tool registry
  (the reviewer literally has no `write_file`), and it returns only a
  summary. Context isolation is the point: the sub-agent's exploration
  doesn't pollute the main conversation. Sub-agents never get `delegate`
  (no recursive fan-out), and parallelism defaults to sequential because
  local models are RAM-bound.
- **`--benchmark` / `--compare-models` → `evals/benchmark.py`.** Each task
  gets a scratch directory, a fresh client, auto-approved permissions, and
  a *mechanical* check (file contains X / command exits 0) — the model is
  never asked to grade itself.

## 3.4 How this is all testable without a model

Two substitution points make the 109-test suite run offline and fast:

- **`MockClient`** (`models/mock.py`): a scripted `ModelClient` — you queue
  up responses ("first call: this tool call, second call: this text") and
  it records every request it receives, so tests assert both directions.
- **`_post_stream` isolation**: the real adapters keep HTTP in one small
  method that tests monkeypatch with scripted chunks, so streaming/parsing/
  retry logic is tested without sockets.

Plus one honest end-to-end layer: `tests/test_cli_e2e.py` boots a real HTTP
server speaking the OpenAI SSE protocol and drives the actual `nomad` CLI
against it — real socket, real streaming, real file writes, real session
files.

Next: [chapter 4](04-code-tour.md) walks through every module in detail.
