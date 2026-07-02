"""Configuration loading: nomad.toml -> dataclasses, with env overrides.

Nothing in the agent reads nomad.toml directly; everything goes through
`Config` so tests can construct one in memory.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ModelConfig:
    provider: str = "ollama"
    name: str = "qwen2.5-coder"
    base_url: str = "http://localhost:11434"
    num_ctx: int = 16384
    temperature: float = 0.2
    request_timeout_s: int = 300
    max_retries: int = 3


@dataclass
class AgentConfig:
    max_iterations: int = 25
    loop_detection_threshold: int = 3
    tool_output_token_cap: int = 2000


@dataclass
class ContextConfig:
    retrieval_token_budget: int = 6000
    embedding_model: str = "nomic-embed-text"


@dataclass
class PermissionsConfig:
    mode: str = "prompt"  # prompt | auto | deny


@dataclass
class Config:
    model: ModelConfig = field(default_factory=ModelConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    context: ContextConfig = field(default_factory=ContextConfig)
    permissions: PermissionsConfig = field(default_factory=PermissionsConfig)
    state_dir: Path = Path(".nomad")
    project_root: Path = Path(".")

    @property
    def state_path(self) -> Path:
        root = self.project_root.resolve()
        state = (root / self.state_dir).resolve()
        return state

    def ensure_state_dirs(self) -> None:
        for sub in ("logs", "sessions", "cache", "index"):
            (self.state_path / sub).mkdir(parents=True, exist_ok=True)


def _apply_section(obj: object, section: dict) -> None:
    for key, value in section.items():
        if hasattr(obj, key):
            setattr(obj, key, value)


def load_config(project_root: str | Path = ".", config_file: str | Path | None = None) -> Config:
    """Load nomad.toml from the project root (if present) and apply env overrides.

    Env overrides use NOMAD_<SECTION>_<KEY>, e.g. NOMAD_MODEL_NAME=llama3.
    """
    root = Path(project_root)
    cfg = Config(project_root=root)
    path = Path(config_file) if config_file else root / "nomad.toml"
    if path.is_file():
        data = tomllib.loads(path.read_text())
        _apply_section(cfg.model, data.get("model", {}))
        _apply_section(cfg.agent, data.get("agent", {}))
        _apply_section(cfg.context, data.get("context", {}))
        _apply_section(cfg.permissions, data.get("permissions", {}))
        if "state_dir" in data.get("paths", {}):
            cfg.state_dir = Path(data["paths"]["state_dir"])

    sections = {
        "MODEL": cfg.model,
        "AGENT": cfg.agent,
        "CONTEXT": cfg.context,
        "PERMISSIONS": cfg.permissions,
    }
    for env_key, raw in os.environ.items():
        if not env_key.startswith("NOMAD_"):
            continue
        parts = env_key.split("_", 2)
        if len(parts) != 3:
            continue
        _, section_name, key = parts
        section = sections.get(section_name)
        if section is None:
            continue
        key = key.lower()
        if not hasattr(section, key):
            continue
        current = getattr(section, key)
        if isinstance(current, bool):
            value: object = raw.lower() in ("1", "true", "yes")
        elif isinstance(current, int):
            value = int(raw)
        elif isinstance(current, float):
            value = float(raw)
        else:
            value = raw
        setattr(section, key, value)
    return cfg
