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


class AirframeConfig(BaseModel):
    """Operator-selected interpreters and the single writable root for airframe_* tools."""
    model_config = ConfigDict(extra="forbid")
    freecad_python: str = Field(min_length=1, description="Python with FreeCAD Part/MeshPart, numpy and matplotlib")
    aerosandbox_python: str | None = Field(default=None, description="Optional Python with aerosandbox + neuralfoil")
    output_root: str = Field(min_length=1, description="Existing dedicated directory; builds go to <output_root>/<name>/")
    read_roots: list[str] = Field(default_factory=list, description="Extra existing directories airframe_inspect_step may read")
    timeout_seconds: float = Field(default=900, gt=0, le=3600, allow_inf_nan=False)
    openvsp_python: str | None = Field(default=None, description="Optional Python matching the OpenVSP python API build (3.12 for OpenVSP 3.4x/3.5x Ubuntu 24.04)")
    openvsp_root: str | None = Field(default=None, description="OpenVSP install dir containing vspaero and python/openvsp")
    openvsp_library_path: str | None = Field(default=None, description="Optional LD_LIBRARY_PATH for the OpenVSP worker (libgomp/libcminpack)")


class Configuration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    project_root: str
    # In-process, pure-Python heuristic drone estimators (drone_*). Off by default so
    # existing catalogs/counts are unchanged; no subprocess, file or network access.
    drone_tools: StrictBool = False
    # Optional headless airframe CAD/aero tools (airframe_*): separate worker interpreters.
    airframe: AirframeConfig | None = None
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
    if config.airframe is not None:
        def directory(value: str) -> str:
            candidate = Path(value)
            if not candidate.is_absolute():
                candidate = path.parent / candidate
            candidate = candidate.resolve(strict=True)
            if not candidate.is_dir():
                raise ValueError(f"airframe directory must exist: {candidate}")
            return str(candidate)
        config.airframe.output_root = directory(config.airframe.output_root)
        config.airframe.read_roots = [directory(v) for v in config.airframe.read_roots]
    return config
