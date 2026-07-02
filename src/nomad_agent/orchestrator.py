"""Multi-agent delegation: specialized roles over the same agent loop.

A role is just configuration — a system prompt, a tool allowlist, an
iteration budget. Every sub-agent gets a FRESH context (that isolation is
the point, not parallelism) and returns only its final summary to the
caller. Parallelism is opt-in and defaults to sequential: several local
model calls at once are RAM/GPU-bound.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

from .config import AgentConfig, Config
from .conversation import Session
from .logging_setup import TraceLog
from .loop import AgentLoop, Approver
from .models.base import ModelClient
from .tools import ToolRegistry
from .tools.base import Tool, ToolResult

READ_ONLY_TOOLS = ["read_file", "list_dir", "git"]


@dataclass
class AgentRole:
    name: str
    system_prompt: str
    allowed_tools: list[str] | None = None  # None = every tool except `delegate`
    max_iterations: int | None = None


BUILTIN_ROLES: dict[str, AgentRole] = {
    role.name: role
    for role in [
        AgentRole(
            "planner",
            "You are a planning specialist. Analyze the task and produce a concrete, "
            "ordered plan. You may read files but must not change anything.",
            allowed_tools=READ_ONLY_TOOLS,
        ),
        AgentRole(
            "coder",
            "You are a coding specialist. Implement exactly the requested change with "
            "minimal, clean edits. Read before you write. Finish with a one-paragraph "
            "summary of what you changed.",
        ),
        AgentRole(
            "reviewer",
            "You are a code reviewer. Read the relevant code and critique it: bugs, "
            "edge cases, style. You must not modify anything. Finish with a verdict "
            "line: APPROVE or REQUEST_CHANGES, plus your findings.",
            allowed_tools=READ_ONLY_TOOLS,
        ),
        AgentRole(
            "tester",
            "You are a testing specialist. Write and/or run tests for the change in "
            "question and report exactly what passes and fails.",
        ),
        AgentRole(
            "debugger",
            "You are a debugging specialist. Reproduce the failure, locate the root "
            "cause, and fix it with the smallest change that makes checks pass.",
        ),
    ]
}


def filtered_registry(base: ToolRegistry, allowed: list[str] | None) -> ToolRegistry:
    registry = ToolRegistry()
    for name in base.names():
        if name == "delegate":
            continue  # sub-agents never delegate: no recursive fan-out
        if allowed is None or name in allowed:
            tool = base.get(name)
            assert tool is not None
            registry.register(tool)
    return registry


class SubAgentFactory:
    def __init__(
        self,
        cfg: Config,
        client: ModelClient,
        base_registry: ToolRegistry,
        trace: TraceLog | None = None,
        approver: Approver | None = None,
        audit: Callable[[dict], None] | None = None,
        roles: dict[str, AgentRole] | None = None,
    ):
        self.cfg = cfg
        self.client = client
        self.base_registry = base_registry
        self.trace = trace
        self.approver = approver
        self.audit = audit
        self.roles = roles or BUILTIN_ROLES

    def run(self, role_name: str, task: str) -> str:
        """Run one sub-agent in a fresh context and return its summary."""
        role = self.roles.get(role_name)
        if role is None:
            return f"Unknown role '{role_name}'. Available: {', '.join(sorted(self.roles))}"
        agent_cfg = AgentConfig(
            max_iterations=role.max_iterations or self.cfg.agent.max_iterations,
            loop_detection_threshold=self.cfg.agent.loop_detection_threshold,
            tool_output_token_cap=self.cfg.agent.tool_output_token_cap,
        )
        loop = AgentLoop(
            self.client,
            filtered_registry(self.base_registry, role.allowed_tools),
            agent_cfg,
            trace=self.trace,
            approver=self.approver,
            audit=self.audit,
        )
        session = Session(self.cfg.state_path, session_id=None)
        session.append({"role": "system", "content": role.system_prompt})
        if self.trace:
            self.trace.record("subagent_start", {"role": role_name, "task": task})
        summary = loop.run(session, task)
        if self.trace:
            self.trace.record("subagent_end", {"role": role_name, "summary": summary[:500]})
        return summary


class Orchestrator:
    def __init__(self, factory: SubAgentFactory, max_workers: int = 1):
        self.factory = factory
        self.max_workers = max_workers

    def delegate(self, role_name: str, task: str) -> str:
        return self.factory.run(role_name, task)

    def run_jobs(self, jobs: list[tuple[str, str]]) -> list[str]:
        """Run (role, task) jobs; order of results matches input order.
        max_workers=1 (default) keeps local inference sequential."""
        if self.max_workers <= 1:
            return [self.delegate(role, task) for role, task in jobs]
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = [pool.submit(self.delegate, role, task) for role, task in jobs]
            return [f.result() for f in futures]

    def code_and_review(self, task: str) -> dict:
        """The Phase 8 'done when': a reviewer critiques the coder's output."""
        implementation = self.delegate("coder", task)
        critique = self.delegate(
            "reviewer",
            f"Review the change just made for this task:\n{task}\n\n"
            f"Coder's summary:\n{implementation}",
        )
        return {"implementation": implementation, "review": critique}


class DelegateTool(Tool):
    name = "delegate"
    description = (
        "Delegate a self-contained sub-task to a specialist agent with a fresh "
        "context. Returns the specialist's summary."
    )

    def __init__(self, orchestrator: Orchestrator, roles: dict[str, AgentRole] | None = None):
        self.orchestrator = orchestrator
        role_names = sorted(roles or BUILTIN_ROLES)
        self.parameters = {
            "type": "object",
            "properties": {
                "role": {"type": "string", "enum": role_names},
                "task": {
                    "type": "string",
                    "description": "complete, self-contained instructions for the specialist",
                },
            },
            "required": ["role", "task"],
        }

    def execute(self, args: dict) -> ToolResult:
        summary = self.orchestrator.delegate(args["role"], args["task"])
        return ToolResult(f"[{args['role']} agent] {summary}")
