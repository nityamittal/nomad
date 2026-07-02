"""Command sandbox: workdir-pinned, timeout-enforced, env-scrubbed execution.

Local mode runs the command in its own process group (so a timeout kills the
whole tree) with a minimal environment (no inherited secrets). Docker mode
(optional, free) adds filesystem/network isolation:
    docker run --rm --network none -v <root>:/work -w /work <image> sh -c <cmd>
"""

from __future__ import annotations

import os
import signal
import subprocess
from dataclasses import dataclass

from .tools.workspace import Workspace

SAFE_ENV_KEYS = ("PATH", "HOME", "LANG", "LC_ALL", "TERM", "TMPDIR")


@dataclass
class SandboxResult:
    stdout: str
    stderr: str
    returncode: int
    timed_out: bool = False


class CommandSandbox:
    def __init__(
        self,
        workspace: Workspace,
        use_docker: bool = False,
        docker_image: str = "python:3.11-slim",
    ):
        self.workspace = workspace
        self.use_docker = use_docker
        self.docker_image = docker_image

    def _env(self) -> dict[str, str]:
        return {k: os.environ[k] for k in SAFE_ENV_KEYS if k in os.environ}

    def run(self, command: str, timeout_s: int = 60) -> SandboxResult:
        if self.use_docker:
            argv = [
                "docker", "run", "--rm", "--network", "none",
                "-v", f"{self.workspace.root}:/work", "-w", "/work",
                self.docker_image, "sh", "-c", command,
            ]
            proc = subprocess.Popen(
                argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
        else:
            proc = subprocess.Popen(
                command,
                shell=True,
                cwd=self.workspace.root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=self._env(),
                start_new_session=True,
            )
        try:
            stdout, stderr = proc.communicate(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
            proc.wait()
            return SandboxResult("", f"timed out after {timeout_s}s", -9, timed_out=True)
        return SandboxResult(stdout or "", stderr or "", proc.returncode)
