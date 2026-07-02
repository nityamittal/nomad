# Project Plan: Build Your Own Coding Agent (Solo, Free Stack)

A learning-oriented roadmap for building an OpenClaw-style coding agent from scratch, using only free/self-hostable components. No paid API keys, no paid hosting. Sequenced so each phase produces something runnable before you add the next layer.

## Free-stack constraint (read first)
- **Model:** Run local open-source models via [Ollama](https://ollama.com) (free, self-hosted). Good coding picks: Qwen2.5-Coder / Qwen3, Devstral (built specifically for coding-agent work), DeepSeek-Coder, or GLM. All run on your own machine. Prefer a model with **native tool-calling support** — it saves you a lot of parsing pain in Phase 2.
- **Hosting:** Everything runs locally on your computer — the agent, the model, and the tools. Nothing needs a server.
- **Language/runtime:** Python or Node — both free.
- **Embeddings (Phase 4):** Use a local embedding model (e.g. `nomic-embed-text` via Ollama) — no paid API.
- **Vector store (Phase 4):** Local + free — SQLite, [Chroma](https://www.trychroma.com/), or FAISS. No hosted DB.
- **Web search (Phase 2):** Free options — DuckDuckGo's HTML endpoint or SearXNG (self-hosted); avoid paid search APIs.
- The only real cost is your own hardware (RAM/GPU). A smaller quantized model runs on a modest laptop.

## Guiding principles
- Ship a thin end-to-end loop early (model → tool call → result), then deepen.
- Keep every subsystem behind an interface so you can swap implementations.
- Write the eval harness *before* you need it — it's how you'll know anything works.
- Log everything from day one — when the agent misbehaves, the raw request/response log is how you'll find out why.

---

## Phase 0 — Scaffolding
**Goal:** A repo you can build on without friction.

- [ ] Pick a language, init the repo, set up dependency management, a linter, and a test runner.
- [ ] Add a config file (model name, Ollama URL, context size, paths) so nothing is hardcoded.
- [ ] Set up debug logging that captures every raw model request and response to disk — you will live in these logs.

**Done when:** `git clone && install && run` works on a clean machine and tests (even one) pass.

---

## Phase 1 — Foundation: model + basic loop
**Goal:** A CLI that sends a prompt to a local model and prints the reply.

- [ ] Install Ollama and pull a coding model (e.g. `ollama pull qwen2.5-coder`).
- [ ] Wrap the local model behind a `ModelClient` interface (`send(messages) -> response`) — this is the seam for Phase 10.
- [ ] Build a minimal message loop: user input → model → printed output.
- [ ] Write a first-pass system prompt (role, environment, expectations) and keep it in a versioned file, not inline in code — you'll iterate on it constantly.
- [ ] Add streaming output and basic error/retry handling.
- [ ] Handle Ctrl+C so it cancels the current generation without killing the whole session.
- [ ] Persist conversation history to disk and add a `--resume` flag to continue the last session.

**Done when:** You can hold a multi-turn text conversation from your terminal, fully offline, and pick it up again after quitting.

> ⚠️ **Ollama gotcha:** Ollama's default context window (`num_ctx`) is small (2k–4k tokens depending on version) regardless of what the model supports. If you don't raise it explicitly, long conversations get silently truncated and the agent "forgets" — it looks like a model bug but isn't. Set it deliberately in Phase 1 and treat it as your token budget everywhere.

---

## Phase 2 — Tool-calling system
**Goal:** The agent can act on your machine, not just talk.

- [ ] Define a `Tool` interface (name, JSON schema, `execute(args)`).
- [ ] Implement core tools: read file, write/edit file, run shell command, web search (DuckDuckGo/SearXNG), git ops.
- [ ] Wire tool-use / function-calling into the model loop (parse tool calls, run them, feed results back). Note: local models vary in native tool-call support — you may need to prompt for a JSON action format and parse it yourself.
- [ ] Add a parse/validation layer so malformed tool calls fail gracefully — and feed the error back to the model so it can retry, rather than crashing the loop.
- [ ] **Cap and truncate tool output.** One `cat` of a big file or a noisy build log will blow a local model's context in a single turn. Truncate with a clear marker ("output truncated, N more lines") so the model knows to narrow its request.
- [ ] Add a max-iterations guard and simple loop detection (same tool + same args N times in a row → stop and surface it) so a confused model can't spin forever.
- [ ] Show file edits as a diff, not a blind overwrite — this becomes your approval UI in Phase 3.

**Done when:** You can say "read main.py and add a docstring" and it happens.

---

## Phase 3 — Permissions & sandboxing
**Goal:** No action runs without appropriate control.

- [ ] Add an approval gate: prompt before write/exec actions (with allow/deny/always-allow).
- [ ] Sandbox command execution (working-dir restriction, timeouts; optionally a free Docker container).
- [ ] Maintain an audit log of every tool call and its result.
- [ ] Treat file contents and web-search results as **untrusted input** (prompt injection): text the agent reads can contain instructions aimed at it. Never auto-approve actions whose arguments came from fetched content, and keep the approval gate between "model wants to run X" and "X runs".

**Done when:** Destructive actions require review and are logged. *(Do this early — before the agent gets autonomous.)*

---

## Phase 4 — Context engine
**Goal:** Feed the model only what it needs.

- [ ] Build file indexing (directory walk + ignore rules).
- [ ] Add retrieval: keyword/grep first, then local semantic search (embeddings via `nomic-embed-text` on Ollama + Chroma/FAISS/SQLite).
- [ ] Assemble context per request (relevant files + docs + recent conversation) under a token budget.
- [ ] Track token usage and log what got included/excluded for debugging.

**Done when:** The agent pulls the right files into context without you naming them all.

---

## Phase 5 — Planning layer
**Goal:** Break big requests into executable steps.

- [ ] Add a planning prompt/step that decomposes a request into an ordered task list.
- [ ] Execute tasks sequentially, feeding results forward.
- [ ] Add re-planning when a step fails or reveals new information.

**Done when:** "Add auth to this app" becomes a sequence of discrete, executed steps.

---

## Phase 6 — Memory
**Goal:** Remember conventions and prior work across sessions.

- [ ] Persist project context (conventions, architecture notes, decisions) to disk (plain files or local SQLite).
- [ ] Support a project config/notes file the agent reads on startup and updates over time.
- [ ] Inject relevant memory into context (ties into Phase 4).

**Done when:** The agent recalls your project's conventions in a fresh session.

---

## Phase 7 — Evaluation harness
**Goal:** Automatically verify the agent actually solved the problem.

- [ ] Auto-run the project's tests / build after a task and parse pass/fail.
- [ ] Build a small benchmark suite of representative tasks with expected outcomes — and keep it separate from the tasks you use for day-to-day development, or you'll overfit to your own demos.
- [ ] Gate task completion on verification — don't mark "done" until checks pass.

**Done when:** The agent won't claim success on a task whose tests fail. *(Start a rough version of this in Phase 2 — it pays off everywhere.)*

---

## Phase 8 — Multi-agent delegation
**Goal:** Specialized sub-agents in parallel.

- [ ] Define agent roles (planner, coder, debugger, tester, reviewer) as configs over the same loop.
- [ ] Add an orchestrator that delegates and collects results.
- [ ] Give each sub-agent its own fresh context and have it return only a summary — context isolation is the actual point of sub-agents, not just parallelism.
- [ ] Run independent sub-tasks in parallel where safe. Note: running several local-model calls at once is RAM/GPU-bound — run sequentially or use a smaller model if hardware is tight.

**Done when:** A review agent can critique a coding agent's output before merge.

---

## Phase 9 — Context compression & caching
**Goal:** Work on large repos without blowing the context window.

- [ ] Summarize/compress older conversation turns.
- [ ] Cache retrieved context and model responses locally to cut redundant calls (matters more with slower local inference).
- [ ] Keep the conversation prefix stable so Ollama's KV cache gets reused — rewriting early messages every turn forces a full re-process and makes local inference feel much slower than it needs to.
- [ ] Add hierarchical summarization for large files/dirs.

**Done when:** The agent stays functional on a repo far bigger than the context window.

---

## Phase 10 — Model-agnosticism (the harness *is* the product)
**Goal:** Swap models with minimal change.

- [ ] Confirm every subsystem depends on the `ModelClient` interface, not a specific model.
- [ ] Add adapters for 2+ local models (and optionally a paid-API adapter later, if you ever want it — not required).
- [ ] Run your Phase 7 benchmark across models to compare.

**Done when:** Changing one config value swaps the underlying model and everything still passes.

---

## Stretch goals (post-Phase 10, pick what's fun)
- **MCP client support:** speak the Model Context Protocol so third-party tool servers plug in without writing adapters — the free ecosystem is large.
- **Nicer terminal UI:** markdown rendering, syntax-highlighted diffs (Python `rich` / Node `ink`).
- **Self-hosting test:** point the agent at its own repo and have it fix one of its own issues end-to-end.

## Common pitfalls (learned the hard way, listed up front)
- **Small local models are much weaker at tool calling than frontier models.** Design for malformed calls as the normal case, not the exception. If tool calls keep failing, try a bigger quant or a model tuned for agentic work before rewriting your parser.
- **The context window is the whole game locally.** Most "the agent got dumb" bugs are actually silent truncation — of the conversation (Ollama `num_ctx`), or of tool output you forgot to cap.
- **Don't skip Phase 3 because it's your own machine.** The first time the agent runs `rm` on the wrong path or follows instructions embedded in a webpage, you'll want the approval gate that was "boring" to build.
- **Iterate on the system prompt like code.** Version it, and re-run the Phase 7 benchmark after prompt changes — prompt regressions are real and invisible without evals.

## Suggested sequencing note
The original roadmap lists Context and Planning before Tools. For a solo build it's usually easier to get **tools + a basic loop working first** (Phases 1–2 here), because they give you immediate feedback, then layer context and planning on top. Adjust to taste.

## Cost summary
$0 in software and hosting. Everything runs locally on hardware you already own. Larger models want more RAM/VRAM; if a model is too slow, drop to a smaller quantized version — the architecture doesn't change.
