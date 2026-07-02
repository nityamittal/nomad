# 1. Getting started

By the end of this chapter you'll have Nomad running on your machine and
will have watched it edit a file for you.

## What you need

- **Python 3.11 or newer** (`python3 --version` to check).
- **8 GB+ of RAM** for a small local model (more is better; nothing here
  needs a GPU, it's just faster with one).
- That's it. No API keys, no accounts, no cloud services.

## Step 1 — install Nomad

```bash
git clone https://github.com/nityamittal/nomad
cd nomad
pip install -e .          # -e = editable: your code edits apply immediately
python3 -m pytest         # optional but recommended: 109 tests, all offline
```

`pip install -e .` registers the `nomad` command (defined in
`pyproject.toml` under `[project.scripts]`). The test suite needs no model
running — chapter 3 explains how that works.

## Step 2 — install a local model

Nomad talks to models through [Ollama](https://ollama.com), a free program
that downloads and runs open-source models on your own machine.

```bash
# install Ollama (see ollama.com for Windows/macOS installers)
curl -fsSL https://ollama.com/install.sh | sh

# pull a coding model (~4.7 GB). Smaller option: qwen2.5-coder:1.5b (~1 GB)
ollama pull qwen2.5-coder

# optional, for semantic search (chapter 4, "context engine"):
ollama pull nomic-embed-text
```

Check it works: `ollama run qwen2.5-coder "say hi"` should print a greeting.
Ollama keeps serving on `http://localhost:11434` in the background — that's
the address in `nomad.toml`.

## Step 3 — first session

Run Nomad **inside the project you want it to work on**. To try it on Nomad
itself:

```bash
nomad
```

You'll see something like:

```
nomad — model=ollama:qwen2.5-coder session=1735820000-a1b2c3d4
Ctrl+C cancels a generation; 'exit' or Ctrl+D quits.

>
```

Try a question first: `what files are in this project?` — the model should
call the `list_dir` tool and answer from its output.

Then try a change: `add a comment at the top of nomad.toml explaining what
this file is`. Because editing files is a **gated** action, Nomad shows you
a diff and asks:

```
--- approval required: edit_file ---
edit_file nomad.toml:
--- a/nomad.toml
+++ b/nomad.toml
...

Allow? [y]es / [n]o / [a]lways for this tool:
```

`y` runs it once, `n` refuses, `a` approves this tool forever (stored in
`.nomad/permissions.json` — delete that file to reset).

## Step 4 — the flags you'll actually use

```bash
nomad --resume                     # continue your last session
nomad --once "run the tests"       # one request, then exit (good for scripts)
nomad --plan                       # break big requests into steps (chapter 4)
nomad --verify                     # don't accept "done" until tests pass
nomad --index                      # build the semantic search index
nomad --benchmark                  # score your model on the built-in tasks
nomad --model qwen2.5-coder:1.5b   # try a different model just this run
```

## Where Nomad keeps its state

Everything lives in a `.nomad/` directory inside the project (git-ignored):

```
.nomad/
  logs/nomad.log        human-readable log
  logs/trace.jsonl      EVERY raw model request/response and tool call
  sessions/*.json       full conversation history, resumable
  audit.jsonl           every tool call: what ran, what happened
  permissions.json      your "always allow" choices
  cache/                cached model responses (if enabled in nomad.toml)
  index/vectors.db      semantic search index (after --index)
```

**`trace.jsonl` is your best friend.** When the agent does something odd,
the answer to "why?" is always in there — it records exactly what the model
was sent and exactly what it replied. Debugging agents is 90% reading this
file.

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `Ollama request failed after retries: <urlopen error ...>` | Ollama isn't running. Start it (`ollama serve` or the desktop app), confirm with `curl localhost:11434`. |
| Model gives empty/garbled answers or ignores tools | Model too small or not tool-trained. Use `qwen2.5-coder` (7b) or larger; avoid non-coding models. |
| Agent "forgets" earlier parts of long conversations | Context window too small. Raise `num_ctx` in `nomad.toml` (costs RAM). This is the #1 local-model gotcha — see chapter 5, "context window". |
| Every generation is very slow | Normal on CPU for 7b+ models. Try `qwen2.5-coder:1.5b`, or enable `cache_responses = true` for repeated work. |
| `Stopped: tool 'X' called 3 times in a row with identical arguments` | Working as intended — the loop detector caught the model spinning. Rephrase the request or check `trace.jsonl` for what confused it. |
| Tool calls appear as raw JSON text in answers | The model lacks native tool-calling; Nomad's fallback parser handles the fenced-JSON form automatically (chapter 4), but a tool-trained model works much better. |

Next: [chapter 2](02-how-a-coding-agent-works.md) explains what's actually
happening when you press Enter.
