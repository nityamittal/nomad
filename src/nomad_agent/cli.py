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
    client = create_client(cfg, trace)

    session = Session.latest(cfg.state_path) if args.resume else None
    if session is None:
        session = Session(cfg.state_path)
    if not session.messages:
        session.append({"role": "system", "content": load_prompt("system")})

    runner = _build_runner(cfg, client, trace, no_tools=args.no_tools)

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


def _build_runner(cfg, client, trace, no_tools: bool):
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

    def agent_turn(session: Session, text: str) -> None:
        agent.run(session, text, on_token=_print_token)
        print()

    return agent_turn


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
