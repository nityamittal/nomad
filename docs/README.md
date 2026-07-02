# Learning Nomad — the complete guide

This documentation is written so you can understand, run, and extend this
coding agent **without referring to anything else**. It assumes you know
basic Python (functions, classes, dicts) and how to use a terminal — nothing
about AI or LLMs is assumed.

Read in order the first time; each chapter builds on the previous one.

| Chapter | What you'll learn |
| --- | --- |
| [1. Getting started](01-getting-started.md) | Install everything, run your first agent session, fix common problems |
| [2. How a coding agent works](02-how-a-coding-agent-works.md) | The core ideas: LLMs, messages, tool calling, the agent loop — from zero |
| [3. Architecture](03-architecture.md) | How Nomad's pieces fit together; the life of one request, end to end |
| [4. Code tour](04-code-tour.md) | Every module explained: what it does, why it exists, where the tricky bits are |
| [5. Glossary](05-glossary.md) | Every term used anywhere in this project, defined in plain language |
| [6. Extending Nomad](06-extending.md) | Add your own tool, model backend, agent role, or benchmark task — step by step |
| [7. Exercises](07-exercises.md) | Hands-on exercises (with hints and solutions) to cement each concept |

Two other documents worth knowing about:

- [`coding_agent_project_plan.md`](../coding_agent_project_plan.md) — the
  original 10-phase plan this project was built from. Each phase became one
  subsystem and one git commit, so `git log` reads as a build diary.
- `NOMAD.md` *(created at runtime, so you won't see it in a fresh clone)* —
  the agent's own memory file for whatever project it works on. See
  chapter 4, "memory".

## The one-paragraph version of this whole project

A coding agent is a loop: send the conversation to a language model, and if
the model answers with *"call this tool with these arguments"* instead of
prose, run that tool (read a file, execute a command...), append the result
to the conversation, and go around again — until the model answers in plain
text. Everything else in this repository exists to make that loop safe
(permissions, sandboxing), smart (retrieval, planning, memory), honest
(verification, benchmarks), and portable across models (the `ModelClient`
interface). If you understand that paragraph, every file in `src/` is just
one clause of it, made concrete.
