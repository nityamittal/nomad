"""Verification: run the project's tests/build and parse pass/fail.

The agent never gets to declare victory on its own word — VerifiedLoop reruns
verification after every attempt and feeds failures back until they pass or
the fix budget runs out.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..sandbox import CommandSandbox

_SUMMARY_RES = [
    re.compile(r"\d+ (?:passed|failed|errors?)[^\n]*"),  # pytest
    re.compile(r"Tests:\s+[^\n]+"),  # jest
    re.compile(r"(?:OK|FAILED)\s*\([^\n]*\)"),  # unittest
]


@dataclass
class Verification:
    command: str
    passed: bool
    output: str
    summary: str = ""


def detect_verify_command(root: str | Path) -> str | None:
    """Best-effort detection of how this project verifies itself."""
    root = Path(root)
    package_json = root / "package.json"
    if package_json.is_file():
        try:
            scripts = json.loads(package_json.read_text()).get("scripts", {})
            if "test" in scripts:
                return "npm test --silent"
        except json.JSONDecodeError:
            pass
    if (root / "pyproject.toml").is_file() or (root / "tests").is_dir():
        return "python3 -m pytest -q"
    if (root / "Makefile").is_file():
        makefile = (root / "Makefile").read_text()
        if re.search(r"^test:", makefile, re.MULTILINE):
            return "make test"
    if (root / "Cargo.toml").is_file():
        return "cargo test"
    if (root / "go.mod").is_file():
        return "go test ./..."
    return None


class Verifier:
    def __init__(
        self,
        sandbox: CommandSandbox,
        command: str | None = None,
        timeout_s: int = 120,
    ):
        self.sandbox = sandbox
        self.command = command or detect_verify_command(sandbox.workspace.root)
        self.timeout_s = timeout_s

    def run(self) -> Verification | None:
        """Run verification; None when the project has no detectable check."""
        if not self.command:
            return None
        result = self.sandbox.run(self.command, timeout_s=self.timeout_s)
        output = (result.stdout + "\n" + result.stderr).strip()
        summary = ""
        for pattern in _SUMMARY_RES:
            match = pattern.search(output)
            if match:
                summary = match.group(0)
                break
        return Verification(
            command=self.command,
            passed=result.returncode == 0 and not result.timed_out,
            output=output,
            summary=summary or output.splitlines()[-1] if output else "",
        )


class VerifiedLoop:
    """Wrap an AgentLoop so task completion is gated on verification."""

    def __init__(self, loop, verifier: Verifier, max_fix_rounds: int = 2):
        self.loop = loop
        self.verifier = verifier
        self.max_fix_rounds = max_fix_rounds

    def run(self, session, user_text: str, on_token: Callable[[str], None] | None = None) -> str:
        answer = self.loop.run(session, user_text, on_token=on_token)
        for round_number in range(self.max_fix_rounds + 1):
            verification = self.verifier.run()
            if verification is None:
                return answer
            if verification.passed:
                return f"{answer}\n\n[verified: `{verification.command}` passed — {verification.summary}]"
            if round_number == self.max_fix_rounds:
                return (
                    f"[NOT verified] `{verification.command}` still fails after "
                    f"{self.max_fix_rounds} fix round(s). Do not treat this task as done.\n"
                    f"Last output:\n{verification.output[-2000:]}"
                )
            answer = self.loop.run(
                session,
                "Verification failed. Fix the problem, changing only what is needed.\n"
                f"Command: {verification.command}\nOutput:\n{verification.output[-3000:]}",
                on_token=on_token,
            )
        return answer  # unreachable, loop always returns
