"""The nomad CLI: a REPL over the agent.

Phase 1 gives plain conversation; later phases plug tools, context, planning
and memory into the same loop. Ctrl+C during a generation cancels that
generation only; Ctrl+C (or Ctrl+D / `exit`) at the prompt leaves the REPL.
"""

from __future__ import annotations

import argparse
import sys

from .config import load_config
from .conversation import Session
from .logging_setup import TraceLog, setup_logging
from .models import GenerationCancelled, ModelError, create_client
from .prompts import load_prompt


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nomad", description="Local-first coding agent")
    parser.add_argument("--project", default=".", help="project root to work in")
    parser.add_argument("--config", default=None, help="path to nomad.toml")
    parser.add_argument("--resume", action="store_true", help="continue the most recent session")
    parser.add_argument("--once", metavar="PROMPT", help="run a single prompt and exit")
    parser.add_argument("--provider", default=None, help="override model provider")
    parser.add_argument("--model", default=None, help="override model name")
    parser.add_argument("--auto", action="store_true", help="auto-approve gated tools (benchmarks/CI)")
    parser.add_argument("--no-tools", action="store_true", help="plain chat, no tool use")
    parser.add_argument(
        "--plan", action="store_true", help="decompose each request into steps before executing"
    )
    parser.add_argument(
        "--index", action="store_true", help="build/refresh the semantic file index and exit"
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="gate completion on the project's tests/build passing",
    )
    parser.add_argument(
        "--benchmark",
        nargs="?",
        const="builtin",
        metavar="TASKS.json",
        help="run the benchmark suite (builtin tasks by default) and exit",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config(args.project, args.config)
    if args.provider:
        cfg.model.provider = args.provider
    if args.model:
        cfg.model.name = args.model
    if args.auto:
        cfg.permissions.mode = "auto"
    cfg.ensure_state_dirs()
    setup_logging(cfg.state_path)
    trace = TraceLog(cfg.state_path)

    if args.index:
        return _build_index(cfg)

    if args.benchmark:
        return _run_benchmark(cfg, trace, args.benchmark)

    client = create_client(cfg, trace)

    session = Session.latest(cfg.state_path) if args.resume else None
    if session is None:
        session = Session(cfg.state_path)
    if not session.messages:
        session.append({"role": "system", "content": load_prompt("system")})

    runner = _build_runner(
        cfg, client, trace, no_tools=args.no_tools, plan=args.plan, verify=args.verify
    )

    if args.once:
        return _run_turn(runner, session, args.once)

    print(f"nomad — model={cfg.model.provider}:{cfg.model.name} session={session.id}")
    print("Ctrl+C cancels a generation; 'exit' or Ctrl+D quits.")
    while True:
        try:
            user_input = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if user_input in ("exit", "quit"):
            return 0
        if not user_input:
            continue
        _run_turn(runner, session, user_input)


def _build_runner(cfg, client, trace, no_tools: bool, plan: bool = False, verify: bool = False):
    """Return a callable(session, text) -> None that runs one turn."""
    if no_tools:

        def chat_turn(session: Session, text: str) -> None:
            session.append({"role": "user", "content": text})
            response = client.send(session.messages, on_token=_print_token)
            print()
            session.append({"role": "assistant", "content": response.content})

        return chat_turn

    from .loop import AgentLoop

    agent = AgentLoop.from_config(cfg, client, trace)

    if verify:
        from .evals import VerifiedLoop, Verifier
        from .sandbox import CommandSandbox
        from .tools import Workspace

        verifier = Verifier(CommandSandbox(Workspace(cfg.project_root)))
        agent = VerifiedLoop(agent, verifier)

    if plan:
        from .planner import PlanExecutor

        executor = PlanExecutor(client, agent, trace)

        def plan_turn(session: Session, text: str) -> None:
            def show_step(step, number, total):
                print(f"\n=== step {number}/{total}: {step.title} ===")

            result = executor.execute(session, text, on_token=_print_token, on_step=show_step)
            print()
            if not result.completed:
                print(f"[plan incomplete] {result.summary}")

        return plan_turn

    def agent_turn(session: Session, text: str) -> None:
        agent.run(session, text, on_token=_print_token)
        print()

    return agent_turn


def _build_index(cfg) -> int:
    from .context import FileIndex, OllamaEmbeddings, SemanticIndex, VectorStore

    db_path = cfg.state_path / "index" / "vectors.db"
    store = VectorStore(db_path)
    embedder = OllamaEmbeddings(cfg.context.embedding_model, cfg.model.base_url)
    index = FileIndex(cfg.project_root)
    try:
        chunks = SemanticIndex(store, embedder).build(index)
    except OSError as exc:
        print(
            f"Could not reach the embedding model ({exc}). "
            f"Is Ollama running with '{cfg.context.embedding_model}' pulled?",
            file=sys.stderr,
        )
        return 1
    print(f"Indexed {chunks} chunks from {len(index.files())} files into {db_path}")
    return 0


def _run_benchmark(cfg, trace, suite: str) -> int:
    from pathlib import Path

    from .evals import BenchmarkTask, run_benchmark

    tasks_path = (
        Path(__file__).parent / "evals" / "tasks.json" if suite == "builtin" else Path(suite)
    )
    tasks = BenchmarkTask.load_suite(tasks_path)
    report = run_benchmark(
        tasks,
        client_factory=lambda: create_client(cfg, trace),
        model_label=f"{cfg.model.provider}:{cfg.model.name}",
    )
    print(report.render())
    return 0 if report.passed == report.total else 1


def _print_token(token: str) -> None:
    sys.stdout.write(token)
    sys.stdout.flush()


def _run_turn(runner, session: Session, text: str) -> int:
    try:
        runner(session, text)
        return 0
    except GenerationCancelled:
        print("\n[generation cancelled]")
        session.append({"role": "assistant", "content": "[cancelled by user]"})
        return 1
    except ModelError as exc:
        print(f"\n[model error] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
