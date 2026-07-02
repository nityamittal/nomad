"""Planning layer: decompose a request into steps, execute, re-plan on failure.

The planner is just another use of the ModelClient — it asks for a JSON array
of steps. Parsing is defensive (local models emit JSON loosely); if no plan
can be parsed, the request runs as a single step, which is exactly what the
agent would have done without a planner.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable

from .conversation import Session
from .logging_setup import TraceLog
from .loop import AgentLoop
from .models.base import ModelClient

PLAN_PROMPT = """You are a planning assistant. Decompose the user's request into a short ordered list of concrete, executable steps (1-6 steps). Reply with ONLY a JSON array:
[{{"title": "short name", "instruction": "what to do, specific enough to execute"}}]

User request:
{request}
"""

REVISE_PROMPT = """A plan step failed. Revise the REMAINING plan.

Original request:
{request}

Steps completed:
{completed}

Failed step: {failed_title}
Failure detail:
{failure}

Reply with ONLY a JSON array of the new remaining steps (same format as before). Return [] if the request cannot be completed.
"""

FAILURE_MARKERS = ("STEP FAILED", "Stopped:")


@dataclass
class PlanStep:
    title: str
    instruction: str
    status: str = "pending"  # pending | done | failed
    result: str = ""


def parse_plan(text: str) -> list[PlanStep]:
    """Extract the first JSON array of {title, instruction} objects from text."""
    start = text.find("[")
    while start != -1:
        try:
            parsed, _ = json.JSONDecoder().raw_decode(text[start:])
        except json.JSONDecodeError:
            start = text.find("[", start + 1)
            continue
        if isinstance(parsed, list):
            steps = []
            for item in parsed:
                if isinstance(item, dict) and item.get("instruction"):
                    steps.append(
                        PlanStep(
                            title=str(item.get("title", item["instruction"][:40])),
                            instruction=str(item["instruction"]),
                        )
                    )
            if steps:
                return steps
            if parsed == []:  # an explicit empty plan means "cannot be done"
                return []
        start = text.find("[", start + 1)
    return []


@dataclass
class PlanResult:
    steps: list[PlanStep] = field(default_factory=list)
    completed: bool = False
    summary: str = ""


class PlanExecutor:
    def __init__(
        self,
        client: ModelClient,
        loop: AgentLoop,
        trace: TraceLog | None = None,
        max_replans: int = 2,
    ):
        self.client = client
        self.loop = loop
        self.trace = trace
        self.max_replans = max_replans

    def _ask_for_plan(self, prompt: str) -> list[PlanStep]:
        response = self.client.send([{"role": "user", "content": prompt}])
        steps = parse_plan(response.content)
        if self.trace:
            self.trace.record(
                "plan", {"steps": [{"title": s.title, "instruction": s.instruction} for s in steps]}
            )
        return steps

    def execute(
        self,
        session: Session,
        request: str,
        on_token: Callable[[str], None] | None = None,
        on_step: Callable[[PlanStep, int, int], None] | None = None,
    ) -> PlanResult:
        steps = self._ask_for_plan(PLAN_PROMPT.format(request=request))
        if not steps:
            steps = [PlanStep(title="Do the request", instruction=request)]

        done: list[PlanStep] = []
        replans = 0
        while steps:
            step = steps.pop(0)
            step.status = "running"
            if on_step:
                on_step(step, len(done) + 1, len(done) + 1 + len(steps))
            instruction = self._step_message(request, step, done, remaining=len(steps))
            result = self.loop.run(session, instruction, on_token=on_token)
            step.result = result
            if any(marker in result for marker in FAILURE_MARKERS):
                step.status = "failed"
                done.append(step)
                replans += 1
                if replans > self.max_replans:
                    return PlanResult(
                        steps=done,
                        completed=False,
                        summary=f"Gave up after {self.max_replans} re-plans. "
                        f"Last failure in step '{step.title}': {result[:300]}",
                    )
                steps = self._ask_for_plan(
                    REVISE_PROMPT.format(
                        request=request,
                        completed=self._completed_digest(done),
                        failed_title=step.title,
                        failure=result[:1000],
                    )
                )
                if not steps:
                    return PlanResult(
                        steps=done,
                        completed=False,
                        summary=f"Plan abandoned after step '{step.title}' failed: {result[:300]}",
                    )
                continue
            step.status = "done"
            done.append(step)
        return PlanResult(steps=done, completed=True, summary=done[-1].result if done else "")

    def _step_message(
        self, request: str, step: PlanStep, done: list[PlanStep], remaining: int
    ) -> str:
        parts = [
            f"[Plan step {len(done) + 1}] {step.title}",
            f"Overall goal: {request}",
            f"Current step: {step.instruction}",
            "If this step cannot be completed, reply starting with 'STEP FAILED:' and the reason.",
        ]
        if done:
            parts.insert(2, "Results of previous steps:\n" + self._completed_digest(done))
        if remaining:
            parts.append(f"({remaining} more step(s) follow; do only this one.)")
        return "\n\n".join(parts)

    @staticmethod
    def _completed_digest(done: list[PlanStep]) -> str:
        return "\n".join(
            f"- {s.title} [{s.status}]: {s.result[:300]}" for s in done
        )
