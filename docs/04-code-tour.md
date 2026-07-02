# 4. Code tour — every module, and why it looks the way it does

Read this with the code open. Paths are relative to `src/nomad_agent/`.
Each section ends with the tests that pin the behavior down — reading a
module's tests is often the fastest way to understand it.

## `config.py` — configuration

Plain dataclasses (`ModelConfig`, `AgentConfig`, `ContextConfig`,
`PermissionsConfig`) loaded from `nomad.toml`, with `NOMAD_<SECTION>_<KEY>`
environment overrides (types coerced from the default's type). Two rules:

- Every value has a code default — `nomad.toml` is optional.
- Nothing else reads the TOML file; everything receives a `Config` object.
  That's why tests can build any configuration in memory.

`Config.state_path` points at `.nomad/` inside the target project;
`ensure_state_dirs()` creates `logs/`, `sessions/`, `cache/`, `index/`.

*Tests: `tests/test_config.py`*

## `logging_setup.py` — the trace log

`TraceLog.record(kind, payload)` appends one JSON line to
`.nomad/logs/trace.jsonl`. Model requests, model responses, tool calls,
context-assembly decisions, plans, compressions — everything flows through
here. It's thread-safe (sub-agents may share it) and deliberately dumb:
append-only JSONL survives crashes and greps beautifully.

Why this exists at all (and was built in Phase 0, before the agent could do
anything): agent bugs look like "the model got weird", and the only way to
tell *model* problems from *harness* problems is to see exactly what the
model was sent.

## `tokens.py` — token budgeting

`estimate_tokens(text)` ≈ `len(text) // 4`. Crude, dependency-free, and
consistently conservative — fine, because every consumer treats budgets as
estimates. `truncate_to_tokens()` cuts text and appends
`[... output truncated: N more characters ...]` — the marker is part of the
contract with the model: it tells it to ask for a narrower slice instead of
retrying the same call.

## `models/` — the ModelClient seam

`base.py` defines the whole interface:

```python
class ModelClient(ABC):
    def send(self, messages, tools=None, on_token=None) -> ModelResponse: ...
```

`ModelResponse` carries `content`, `tool_calls` (list of
`ToolCall(name, arguments)`), and token counts. `on_token` is called with
each streamed chunk — that's how the CLI prints as the model generates.

- **`ollama.py`** posts to `/api/chat`, streaming NDJSON. Details that
  matter: `num_ctx` is *always* sent (Ollama's default context window is
  tiny and silently truncates — the #1 local-agent gotcha); localhost
  requests bypass any HTTP proxy from the environment; network errors retry
  with exponential backoff (1s, 2s, 4s...); `KeyboardInterrupt` mid-stream
  becomes `GenerationCancelled` so Ctrl+C kills the generation, not the
  REPL. HTTP lives in `_post_stream()` alone, so tests fake it.
- **`openai_compat.py`** speaks `/v1/chat/completions` SSE — one adapter
  covers llama.cpp server, vLLM, LM Studio, LocalAI. The fiddly part is
  streamed tool calls: arguments arrive as *fragments of a JSON string*
  keyed by index, assembled and parsed only at the end (unparseable ones
  are preserved as `{"_raw": ...}` rather than dropped).
- **`mock.py`** is the test workhorse: construct with a script of responses,
  and it records every request. Most of the suite is "script the model,
  assert what the harness did about it".
- **`__init__.py`** holds `create_client(config)` — the only place concrete
  adapters are constructed, optionally wrapped in the response cache.

*Tests: `test_models.py`, `test_model_agnosticism.py`*

## `conversation.py` — sessions

`Session` is a list of message dicts that writes itself to
`.nomad/sessions/<id>.json` on every `append()` (crash-safe by
construction). `Session.latest()` powers `--resume`. No cleverness — the
interesting decision is what it *doesn't* do: it never edits old messages,
which keeps the prefix stable for KV caching (chapter 2.5).

## `prompts/system.md` — the system prompt

Versioned as a file, not a string in code, because you will iterate on it
constantly and want diffs. Read it — it encodes the project's values:
verify before claiming done, treat read content as data not instructions,
don't repeat failing calls, and (for weak models) the fenced-JSON fallback
format for tool calls.

## `toolcalls.py` — fallback tool-call parsing

For models without native tool calling: extracts
` ```json {"tool": ..., "arguments": {...}} ``` ` blocks (or a bare JSON
object) from prose. Accepts `name`/`args` as synonyms, ignores malformed
JSON silently (the loop's validation layer produces the model-facing
errors). Native calls always win when present.

## `tools/` — the hands

`base.py`: a `Tool` is `name` + `description` + JSON-schema `parameters` +
`execute(args) -> ToolResult`. Two hooks matter:

- `is_gated(args)` — per-*call* gating. `git status` runs free;
  `git commit` needs approval. Default: the class-level `gated` flag.
- `preview(args)` — what the human sees at the approval gate. File tools
  return a unified diff; shell returns the command line. A good preview is
  what makes approval *meaningful* rather than a reflexive "y".

`ToolRegistry.validate()` checks required keys, types, and enums — and
returns error strings written for the *model* to read and fix ("missing
required argument 'path'"), because with small local models malformed calls
are the normal case, not the exception.

The tools:

| Tool | File | Gated? | Notes |
| --- | --- | --- | --- |
| `read_file` | `files.py` | no | numbered lines; `start_line`/`end_line` for slices of big files |
| `write_file` | `files.py` | yes | preview = diff vs current content |
| `edit_file` | `files.py` | yes | exact-string replace; `old_string` must match exactly once — forces the model to read before editing |
| `list_dir` | `files.py` | no | |
| `run_command` | `shell.py` | yes | runs via the sandbox; timeout capped at 300s |
| `web_search` | `search.py` | no | DuckDuckGo's free HTML endpoint, regex-parsed; unwraps DDG's redirect URLs |
| `git` | `gitops.py` | per-call | whitelisted subcommands only; read-only ones ungated |
| `remember` | `memory.py` | no | appends to NOMAD.md only |
| `delegate` | `orchestrator.py` | no | spawns a sub-agent (which has its own gates) |

`workspace.py` is the security floor under all of it: `Workspace.resolve()`
is the single choke point that turns a model-supplied path into a real one,
refusing anything that escapes the project root (`../`, absolute paths,
symlinks out).

*Tests: `test_tools.py`*

## `loop.py` — the heart

Read `AgentLoop.run()` — it's ~40 lines and it *is* the diagram from
chapter 2.4. Guard rails: `max_iterations`, consecutive-identical-call
detection (compared by `name + sorted-JSON(arguments)`), output truncation,
and the untrusted-data prefix on every tool result. Tool crashes are caught
and returned *to the model* as errors — a buggy tool must never kill the
conversation.

The constructor takes the four hooks (`approver`, `audit`,
`context_provider`, `compressor`); `from_config()` is the production wiring
that builds and connects everything — read it to see the whole object graph
in one screen.

*Tests: `test_loop.py` — including the plan's Phase 2 acceptance test
("read main.py and add a docstring") as an actual test.*

## `permissions.py` + `sandbox.py` — the brakes

`ApprovalGate` has three modes: `prompt` (interactive y/n/a; "a" persists
to `.nomad/permissions.json`), `auto` (benchmarks/CI), `deny` (everything
gated refused). It's injected as the loop's `approver` hook and is fully
testable by injecting `input_fn`/`print_fn`.

`CommandSandbox.run()`:
- `cwd` pinned to the project root,
- environment reduced to `PATH/HOME/LANG/...` — the model can't `echo
  $OPENAI_API_KEY`,
- `start_new_session=True` + `os.killpg` on timeout, so `sleep 900 &`
  can't outlive its budget,
- optional Docker mode (`--network none`, project mounted at `/work`) for
  real isolation.

`AuditLog` is the flight recorder: every tool call — approved, denied,
crashed — as one JSONL line in `.nomad/audit.jsonl`.

*Tests: `test_permissions.py`*

## `context/` — what the model gets to see

- `indexer.py`: walks the project, honoring defaults (`.git`,
  `node_modules`, binaries...) plus `.gitignore` patterns; skips files over
  200 KB and anything with NUL bytes. Everything downstream consumes this
  list, so ignore rules live in exactly one place.
- `retrieval.py`: keyword scoring — term frequency capped per term (so one
  spammy file can't dominate) plus a strong bonus for filename matches.
  Cheap, no index needed, surprisingly effective on code because
  identifiers are distinctive.
- `embeddings.py`: the semantic tier. Files → 40-line overlapping chunks →
  vectors via Ollama's `/api/embed` → rows in SQLite (`index/vectors.db`).
  Search = embed the query, cosine-compare against every chunk in Python.
  O(n) linear scan — honest and fine up to tens of thousands of chunks;
  swap in FAISS the day it isn't.
- `assembler.py`: merges both tiers under `retrieval_token_budget`
  (keyword hits first — more precise on code), caps each file at 1500
  tokens, and traces every include/exclude decision, because "why didn't
  the agent see that file?" is a daily debugging question.

*Tests: `test_context.py`*

## `memory.py` — remembering across sessions

`NOMAD.md` at the project root: human-readable, human-editable, owned
jointly by you and the agent. `ProjectMemory.append_note()` files notes
under `##` sections; the `remember` tool exposes it to the model; the
context provider injects the file once per session (it checks the history
for the marker first — re-injecting every turn would both waste tokens and
destabilize the prefix).

Design choice worth noticing: memory is *just a markdown file*, not a
database. You can read it, edit it, delete a wrong memory with your text
editor, and it diffs in git.

*Tests: `test_memory.py`*

## `planner.py` — thinking in steps

`parse_plan()` hunts for the first JSON array of
`{"title", "instruction"}` objects anywhere in the model's reply, skipping
over decorative arrays like `[1, 2]` (defensive parsing is a theme: assume
the model formats loosely). `PlanExecutor.execute()`:

1. ask for a plan (unparseable → single-step fallback = the raw request),
2. run each step through the loop, prefixing each with the goal, previous
   step results (digested to 300 chars each), and the instruction to reply
   `STEP FAILED: <reason>` if stuck,
3. on failure (that marker, or a loop-abort message) → ask for a revised
   *remaining* plan, bounded by `max_replans`; an empty revision `[]` means
   the model concedes the task is impossible.

*Tests: `test_planner.py`*

## `evals/` — keeping everyone honest

`verifier.py`: `detect_verify_command()` sniffs the project (package.json
scripts.test → `npm test`; pyproject/tests/ → pytest; Makefile with a
`test:` target; Cargo.toml; go.mod). `VerifiedLoop` wraps the agent loop
with the run→check→feed-back-failures cycle; its refusal string
(`[NOT verified] ... Do not treat this task as done`) is the Phase 7
guarantee, enforced structurally rather than by trusting the model.

`benchmark.py`: tasks are data (`evals/tasks.json`), checks are mechanical
(`file_contains` / `command_succeeds`), each task gets a scratch dir and a
fresh client, crashes count as failures. `render_comparison()` prints the
cross-model table for `--compare-models`.

*Tests: `test_evals.py`*

## `orchestrator.py` — many agents, one loop

`AgentRole` = name + system prompt + tool allowlist + iteration budget:
a role is *configuration*, not a subclass. `SubAgentFactory.run()` builds a
fresh session (role prompt + task only — no parent history), a filtered
registry (never including `delegate` — no recursion), runs the loop, and
returns the summary. `Orchestrator.code_and_review()` is the built-in
pipeline: coder implements, reviewer (read-only tools) critiques with an
APPROVE / REQUEST_CHANGES verdict. `Orchestrator(factory, max_workers=N)`
enables parallel `run_jobs()`, but the default is 1 — see the RAM note in
chapter 3.3.

*Tests: `test_orchestrator.py`*

## `compression.py` — fitting in the window

`HistoryCompressor` fires only when the history exceeds
`history_token_budget`. It summarizes the *old middle* (system prompt kept,
last `keep_recent` messages kept verbatim) with one model call, then caches
the result keyed by a hash of the exact messages it covered. Subsequent
turns reuse the identical summary block — the prefix sent to the model
stays byte-stable, which is what keeps the server's KV cache warm
(chapter 2.5). The session file always retains full history; compression
only changes the *view*.

`CachingClient` wraps any `ModelClient` with a content-addressed disk cache
(SHA-256 of model + messages + tools). Identical request → identical
response with zero inference; `on_token` is replayed so streaming consumers
don't notice. Enable with `cache_responses = true`.

`hierarchical_summary()` handles text bigger than the window: chunk →
summarize each → recursively summarize the summaries.

*Tests: `test_compression.py`*

## `cli.py` — the front door

Argument parsing, config/override resolution, and mode dispatch (`--index`,
`--benchmark`, `--compare-models`, or the REPL). `_build_runner()` shows how
the wrappers stack: base loop → `VerifiedLoop` (if `--verify`) →
`PlanExecutor` (if `--plan`). Ctrl+C semantics: mid-generation it cancels
that generation (`GenerationCancelled`); at the prompt it exits.

*Tests: `test_cli_e2e.py` — the full-stack stub-server tests.*

Next: [chapter 5](05-glossary.md) if any term is fuzzy, or jump to
[chapter 6](06-extending.md) to start building on this.
