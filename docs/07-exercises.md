# 7. Exercises — learn by poking it

Ordered from observation to construction. Each has a goal, steps, and a
hint or solution sketch. Do them in a scratch copy of any small project (or
this repo itself). Exercises that need a live model are marked 🦙; the rest
work with tests/mocks only.

---

## Exercise 1 — Read a conversation's skeleton 🦙

**Goal:** internalize that "chat" is a resent list of messages.

1. Run `nomad`, ask two questions, exit.
2. Open `.nomad/sessions/<id>.json`. Identify the system prompt, your
   turns, the assistant turns, and any auto-injected `system` context
   blocks.
3. Now open `.nomad/logs/trace.jsonl` and find the *second*
   `model_request`. Confirm it contains the *entire* first exchange.

**Check yourself:** why must the whole history be resent? (Chapter 2.1:
the model is stateless.)

---

## Exercise 2 — Watch a tool call happen 🦙

**Goal:** see the loop's machinery in the raw.

1. `nomad --once "how many lines is the longest file here?"`
2. In `trace.jsonl`, trace the chain: `model_request` (with the tool
   catalog) → `model_response` (with `tool_calls`) → `tool_call` records →
   next `model_request` containing `role: "tool"` messages.
3. Find the untrusted-data prefix on the tool result, and (if any output
   was large) the truncation marker.

---

## Exercise 3 — Break the loop detector (safely)

**Goal:** understand the guard rails without a model.

Write a test: script a `MockClient` that returns the *same* `read_file`
call five times. Assert the run's result mentions "identical arguments".
Then lower `loop_detection_threshold` to 2 and assert it triggers earlier.

<details><summary>Solution sketch</summary>

`tests/test_loop.py::test_loop_detection_aborts` already does exactly
this — write yours first, then compare.
</details>

---

## Exercise 4 — Fool the validator, watch it recover

**Goal:** see why validation errors are written for the model.

Script a `MockClient` whose first response calls
`edit_file {"path": "a.py"}` (missing both string arguments) and whose
second response is `"recovered"`. Run the loop and print every message in
the session afterwards. Find the tool message explaining exactly which
argument was missing — that message is the *only* reason a real model's
second attempt succeeds.

---

## Exercise 5 — Path-escape red team

**Goal:** trust `Workspace` because you attacked it, not because the docs
said so.

In a test, try to make any file tool touch something outside the project:
`../../etc/hosts`, an absolute path, a symlink inside the workspace
pointing outside (create it with `os.symlink`). Assert every attempt
returns an error and nothing outside was read or written.

<details><summary>Hint</summary>

`Workspace.resolve()` calls `Path.resolve()`, which follows symlinks
*before* the containment check — that's what defeats the symlink trick.
</details>

---

## Exercise 6 — Feel the context budget

**Goal:** understand retrieval as a budget problem.

Using `test_context.py`'s fixtures as a template, build a fake project of
five files, then construct `ContextAssembler` with `budget_tokens=100`.
Which files made it in? Read the `context_assembly` trace event's
`included` / `excluded_over_budget` lists. Now double the budget and watch
the lists change.

---

## Exercise 7 — Give the agent a memory, then interrogate it 🦙

1. Tell the agent: *"remember that this project uses tabs, not spaces"*.
2. Verify `NOMAD.md` appeared and contains the note.
3. Quit, start a **new** session, ask *"what indentation does this project
   use?"* — it should answer from memory without reading any code.
4. Look in the session file: find the injected memory block, and confirm
   it appears exactly once even after several turns.

---

## Exercise 8 — Make verification catch a lie

**Goal:** prove the agent can't claim success with red tests.

Write a test using `VerifiedLoop` with `Verifier(sandbox, command="exit 1")`
and a `MockClient` scripted to cheerfully answer "done!" three times.
Assert the final answer starts with `[NOT verified]`. Then change the
command to `exit 0` and assert it ends with a `[verified: ...]` suffix.

---

## Exercise 9 — Write your first tool

Do recipe 6.1 (the `grep` tool) end to end, including tests. Then go one
step further: script a `MockClient` that *uses* your tool
(`ToolCall("grep", {...})`) and assert the loop feeds the matches back to
the model. You've now touched every layer: tool → registry → loop → model.

---

## Exercise 10 — Benchmark two models honestly 🦙

1. Pull a second model: `ollama pull qwen2.5-coder:1.5b`.
2. `nomad --compare-models "ollama:qwen2.5-coder,ollama:qwen2.5-coder:1.5b"`
3. Read the table. Add one task of your own (recipe 6.4) that you predict
   the small model will fail, and re-run. Were you right?

---

## Exercise 11 — Trace a plan's failure and recovery

Read `test_planner.py::test_replans_on_failure_then_succeeds` and, without
running it, write down the exact sequence of `client.send()` calls it
expects (who asks what, in which order). Then run it with
`pytest -q tests/test_planner.py -k replans` and check yourself against
the `MockClient.requests` list in a debugger or with a print.

---

## Exercise 12 — Capstone: self-hosting 🦙

Point Nomad at its own repository and give it a real task with the full
stack on:

```bash
nomad --plan --verify
> add a --version flag to the CLI that prints the package version
```

Approve its edits, watch verification run the test suite, and review the
diff it produced with `git diff`. If it succeeds, you've watched a coding
agent you fully understand modify itself with the tests as a safety net —
which is the entire idea of this project, demonstrated.

---

## Where to go next

- Reread the [project plan](../coding_agent_project_plan.md) — it will read
  completely differently now.
- Build one stretch goal (chapter 6.6). MCP support is the most valuable;
  the TUI is the most fun.
- Compare Nomad's choices against a production agent you use. For every
  difference, ask: what constraint (context, safety, weak local models)
  drove their choice — and does it apply to yours?
