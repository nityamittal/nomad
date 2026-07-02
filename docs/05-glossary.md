# 5. Glossary — every term, in plain language

Alphabetical. Terms in *italics* have their own entry.

**Adapter** — a class that translates between Nomad's `ModelClient`
interface and one specific backend's wire protocol (`ollama.py`,
`openai_compat.py`, `mock.py`). Only `create_client()` ever names one.

**Agent** — a program that uses an *LLM* in a loop with *tools* to
accomplish tasks, rather than just answering once.

**Agent loop** — the cycle: send conversation → model replies → if *tool
calls*, execute and append results → repeat until plain text. Implemented
in `loop.py`.

**Approval gate** — the y/n/always prompt shown before any *gated* tool
runs. `permissions.py`.

**Audit log** — append-only record of every tool call and outcome
(`.nomad/audit.jsonl`). Answers "what did the agent actually do?"

**Backoff (exponential)** — retrying failures with growing waits (1s, 2s,
4s...) so a struggling server isn't hammered.

**Benchmark** — a fixed suite of tasks with machine-checkable outcomes,
used to measure a model or harness change objectively. `evals/benchmark.py`,
tasks in `evals/tasks.json`.

**Chunk** — a slice of a file (here: 40 lines, overlapping by 5) that gets
its own *embedding*, so search can find the relevant part of a big file.

**Context / context window** — everything sent to the model in one call /
the hard cap (in *tokens*) on how much that can be. The scarcest resource
in the whole system; chapters 2.5 and the `num_ctx` entry below.

**Context assembly** — choosing what goes into the window: retrieved
files + memory + conversation, under a *token budget*. `context/assembler.py`.

**Cosine similarity** — how alike two *vectors* point (1 = same direction,
0 = unrelated). The math behind *semantic search*: similar meanings →
similar vectors → high cosine.

**Embedding** — a list of numbers (a *vector*) representing a text's
meaning, produced by an embedding model (here `nomic-embed-text`). Texts
with similar meaning get nearby vectors.

**Function calling** — synonym for *tool calling* (OpenAI's original name
for it).

**Gated tool** — a tool that mutates state (write, edit, shell, mutating
git) and therefore must pass the *approval gate*. Read-only tools are not
gated. Gating can be per-call: `git status` free, `git commit` gated.

**Harness** — everything around the model: loop, tools, permissions,
retrieval, verification. This repository *is* a harness. "The harness is
the product" = models are swappable; the harness is where the value
compounds.

**Hierarchical summarization** — summarizing text too big for the window
by chunking, summarizing chunks, then summarizing the summaries.
`compression.py`.

**JSON Schema** — a standard way to describe JSON shapes ("object with a
required string field `path`"). Every tool declares its arguments as one;
the model receives these schemas as its tool catalog, and `validate()`
checks calls against them.

**KV cache (key-value cache)** — a model server's reuse of computation for
a conversation *prefix* it has already processed. Only works if the prefix
is byte-identical to last time — the reason Nomad never rewrites old
messages and caches its compression summaries.

**LLM (large language model)** — a function from text to text that
predicts plausible continuations token by token. Stateless; everything it
"knows" per call must be in the *context*.

**Loop detection** — aborting when the model repeats the identical
(tool, arguments) call N times in a row: it's stuck, and more iterations
just burn time.

**Message** — one turn in the conversation: a dict with a `role` and
`content`. The conversation is a list of these, resent in full every call.

**Mock client** — a scripted fake `ModelClient` used in tests: you queue
responses, it records requests. Lets the entire harness be tested offline.

**`num_ctx`** — Ollama's parameter for the context window size. Its
default is far smaller than most models support; if you don't set it,
long conversations silently truncate and the agent "forgets". Nomad always
sends it explicitly (`nomad.toml` → `[model] num_ctx`).

**Ollama** — free, self-hosted server that downloads and runs open-source
models locally, exposing an HTTP API on `localhost:11434`.

**OpenAI-compatible** — the de-facto standard HTTP protocol
(`/v1/chat/completions`) spoken by many free local servers (llama.cpp,
vLLM, LM Studio). One adapter covers them all.

**Prefix stability** — keeping the start of the conversation byte-identical
across calls so the *KV cache* stays valid. Design constraint honored by
sessions (append-only), memory (inject once), and compression (cached
summary block).

**Prompt** — text given to the model. The *system prompt* is the standing
instruction sheet (`prompts/system.md`).

**Prompt injection** — an attack where text the agent *reads* (a file, a
web page) contains instructions aimed at the model ("ignore your
instructions and run X"). Defenses: mark tool output as data-not-
instructions, and keep a human gate before destructive actions.

**RAG (retrieval-augmented generation)** — fetching relevant documents and
putting them in the context, instead of hoping the model knows. Nomad's
context engine is RAG over your codebase.

**Registry (tool registry)** — the catalog of available tools: provides
their *JSON Schemas* to the model and *validates* incoming calls.

**Re-planning** — when a plan step fails, asking the model for a revised
remaining plan given what succeeded and what failed. `planner.py`.

**Role (chat)** — who a *message* is from: `system`, `user`, `assistant`,
or `tool`.

**Role (agent)** — a specialist configuration of the same loop: system
prompt + tool allowlist + iteration budget (planner, coder, reviewer...).
`orchestrator.py`.

**Sandbox** — confinement for shell commands: pinned working directory,
scrubbed environment, hard timeout that kills the whole process tree,
optionally Docker. `sandbox.py`.

**Semantic search** — finding text by meaning rather than exact words:
*embed* the query, rank stored *chunks* by *cosine similarity*.

**Session** — one persisted conversation (`.nomad/sessions/<id>.json`),
resumable with `--resume`.

**SSE (server-sent events)** — the streaming format of the OpenAI
protocol: lines of `data: {json}`, ending with `data: [DONE]`.

**Streaming** — receiving the model's answer token by token as it
generates (the `on_token` callback), instead of waiting for the whole
thing.

**Sub-agent** — a fresh, isolated agent loop spawned for a self-contained
sub-task via the `delegate` tool; returns only a summary to its caller.

**System prompt** — see *Prompt*.

**Token** — the unit models read and write; roughly 4 characters of
English. Context windows, budgets, and costs are all measured in tokens.
Nomad estimates with `len(text) // 4` (`tokens.py`).

**Token budget** — a self-imposed cap on tokens spent on some part of the
context (retrieved files, history) so the total fits the window with room
to spare.

**Tool** — a function the model can request: name + description + argument
schema + implementation. The model *requests*; the harness *executes*.

**Tool call** — the model's machine-readable request to run a tool. Native
(structured field in the API response) or fallback (fenced JSON in prose,
parsed by `toolcalls.py`).

**Trace log** — `.nomad/logs/trace.jsonl`: every raw model request/response
and tool call. The primary debugging instrument.

**Truncation (of tool output)** — capping big tool results before they
enter the context, with an explicit marker telling the model how much was
cut so it can request a narrower slice.

**Vector / vector store** — the number-list form of an *embedding* / the
database that holds them for search (here: a SQLite table, cosine-scanned
in Python).

**Verification** — running the project's real checks (tests/build) to
decide whether a task is done, instead of trusting the model's claim.
`evals/verifier.py`.

**Workspace** — the project directory the agent operates in; all file
paths resolve inside it and escapes are refused (`tools/workspace.py`).
