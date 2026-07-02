# Nomad — a local-first coding agent

A coding agent built from scratch on a **free, self-hostable stack**: local
models via [Ollama](https://ollama.com) (or any OpenAI-compatible server like
llama.cpp / vLLM / LM Studio), stdlib-only Python, SQLite for vectors, and
DuckDuckGo for search. No API keys, no hosted services.

Built by following [`coding_agent_project_plan.md`](coding_agent_project_plan.md)
phase by phase — each phase of that plan maps to a subsystem here.

## Quick start

```bash
pip install -e .

# Requires Ollama running locally with a coding model pulled:
#   ollama pull qwen2.5-coder
nomad                       # interactive REPL in the current project
nomad --resume              # continue the last session
nomad --once "read main.py and add a docstring"
```

Useful flags:

| Flag | What it does |
| --- | --- |
| `--plan` | decompose each request into steps, execute sequentially, re-plan on failure |
| `--verify` | gate completion on the project's tests/build passing (auto-detected) |
| `--auto` | auto-approve gated tools (benchmarks/CI only) |
| `--index` | build the semantic file index (needs `ollama pull nomic-embed-text`) |
| `--benchmark` | run the built-in benchmark suite against the configured model |
| `--compare-models "ollama:qwen2.5-coder,ollama:deepseek-coder"` | same suite across models, side-by-side table |
| `--provider` / `--model` | override the configured backend for this run |
| `--no-tools` | plain chat, no tool use |

## Configuration

Everything lives in [`nomad.toml`](nomad.toml) (all values have code defaults;
env overrides via `NOMAD_<SECTION>_<KEY>`). Swapping models is one config
value: `provider = "ollama" | "openai-compat" | "mock"` plus `name`.

Note `num_ctx`: Ollama's own default context window is tiny; Nomad always
sends this value explicitly so truncation is deliberate, never silent.

## How it's put together

```
src/nomad_agent/
  models/         ModelClient seam: ollama, openai-compat, mock adapters   (phases 1, 10)
  loop.py         model<->tool loop: truncation, loop detection, hooks     (phase 2)
  tools/          registry + read/write/edit/shell/search/git tools       (phase 2)
  permissions.py  approval gate (y/n/always) + audit log                  (phase 3)
  sandbox.py      workdir-pinned, env-scrubbed, timeout-killed exec       (phase 3)
  context/        file index, keyword + semantic retrieval, assembler     (phase 4)
  planner.py      plan -> execute -> re-plan                              (phase 5)
  memory.py       NOMAD.md project memory + remember tool                 (phase 6)
  evals/          verifier (gates "done" on green), benchmark suite       (phase 7)
  orchestrator.py roles, fresh-context sub-agents, delegate tool          (phase 8)
  compression.py  stable-prefix history compaction, response cache        (phase 9)
```

Design rules the code holds itself to:

- **Everything behind the `ModelClient` interface.** A conformance test fails
  if any subsystem names a concrete adapter.
- **No gated action without control.** Mutating tools show a diff/command
  preview at the approval gate; every call lands in `.nomad/audit.jsonl`.
- **Tool output is data, not instructions.** File and web content is marked
  untrusted before the model sees it.
- **Done means verified.** With `--verify`, the agent cannot claim success
  while the project's tests fail — failures are fed back for fix rounds.
- **Logs first.** Raw model traffic and every tool call are traced to
  `.nomad/logs/trace.jsonl`.

## Tests

```bash
python3 -m pytest
```

The suite (109 tests) runs fully offline: model behavior is scripted through
the mock adapter, and the end-to-end tests drive the real CLI against a live
stub server speaking the OpenAI SSE protocol.
