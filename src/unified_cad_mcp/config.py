"""Explicit operator-owned subprocess configuration; no implicit project discovery."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator


class BackendConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool = True
    command: str = Field(min_length=1)
    args: list[str] = Field(default_factory=list)
    cwd: str | None = None
    env: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: float = Field(default=120, gt=0, le=3600, allow_inf_nan=False)
    init_timeout_seconds: float = Field(default=20, gt=0, le=60, allow_inf_nan=False)

    @field_validator("command")
    @classmethod
    def command_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("command cannot be blank")
        return value


class Configuration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    project_root: str
    # In-process, pure-Python heuristic drone estimators (drone_*). Off by default so
    # existing catalogs/counts are unchanged; no subprocess, file or network access.
    drone_tools: StrictBool = False
    backends: dict[Literal["kicad", "cad", "pcb", "freecad"], BackendConfig]


def load_config(path: Path) -> Configuration:
    path = path.resolve(strict=True)
    config = Configuration.model_validate(json.loads(path.read_text(encoding="utf-8")))
    project = Path(config.project_root)
    if not project.is_absolute():
        project = path.parent / project
    project = project.resolve(strict=True)
    if not project.is_dir():
        raise ValueError("project_root must be an existing directory")
    config.project_root = str(project)
    for backend in config.backends.values():
        cwd = Path(backend.cwd) if backend.cwd else project
        if not cwd.is_absolute():
            cwd = path.parent / cwd
        cwd = cwd.resolve(strict=True)
        if not cwd.is_dir():
            raise ValueError("Backend cwd must be an existing directory")
        backend.cwd = str(cwd)
    return config
