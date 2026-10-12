"""airframe_*: headless parametric airframe CAD, validation, previews and aero cross-checks.

Each tool runs a short-lived worker in a SEPARATE interpreter selected by the
trusted operator config ("airframe" section):

- freecad_python: a Python with FreeCAD (Part, MeshPart) + numpy + matplotlib.
  Used for airframe_build_fixed_wing, airframe_inspect_step, airframe_render_preview.
- aerosandbox_python (optional): a Python with AeroSandbox (MIT) + NeuralFoil.
  Used for airframe_aero_crosscheck.

Writes go only to <output_root>/<output_name>/ (output_name is a slug). Reads are
limited to output_root plus optional read_roots. No shell, no network. One build at
a time per output directory (in-process lock). Workers get a minimal environment.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import BaseModel, ConfigDict, Field

from .config import AirframeConfig

WORKERS = Path(__file__).parent / "workers"
NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
MARKER = ".airframe-build.json"
WRITE = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False}
READ = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
Finite = Annotated[float, Field(allow_inf_nan=False)]
Vec3 = Annotated[list[Finite], Field(min_length=3, max_length=3)]


class Marker(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=40)
    position_mm: Vec3
    color: str = Field(default="#d62728", pattern=r"^#[0-9a-fA-F]{6}$")


SPEC_KEYS_REQUIRED = ("id", "materials", "fuselage", "wing", "tail")


def _spec_errors(spec: dict) -> list[str]:
    """Cheap structural validation with actionable messages before spawning FreeCAD."""
    errors = [f"missing top-level key '{k}'" for k in SPEC_KEYS_REQUIRED if k not in spec]
    if errors:
        return errors
    if not isinstance(spec["id"], str) or not NAME.match(spec["id"]):
        errors.append("spec.id must be a lowercase slug (a-z, 0-9, . _ -), max 64 chars")
    mats = spec.get("materials", {})
    for path, con in [("wing", spec["wing"].get("construction")), ("tail", spec["tail"].get("construction")),
                      ("fuselage", spec["fuselage"].get("construction"))]:
        if not isinstance(con, dict) or con.get("type") not in {"solid", "shell", "core_skin"}:
            errors.append(f"{path}.construction.type must be solid | shell | core_skin")
            continue
        for key in ("material", "core", "skin"):
            if key in con and con[key] not in mats:
                errors.append(f"{path}.construction.{key} '{con[key]}' is not defined in materials")
    for i, st in enumerate(spec["wing"].get("stations", [])):
        if st.get("chord_mm", 0) <= 0:
            errors.append(f"wing.stations[{i}].chord_mm must be > 0")
    if len(spec["wing"].get("stations", [])) < 2:
        errors.append("wing.stations needs at least 2 stations")
    if len(spec["fuselage"].get("stations", [])) < 3:
        errors.append("fuselage.stations needs at least 3 stations")
    if spec["tail"].get("type") != "v":
        errors.append("tail.type: only 'v' (V-tail) is implemented by this builder; conventional tails are on the roadmap")
    names = [c.get("name") for c in spec.get("components", []) + spec.get("envelopes", [])]
    dup = sorted({n for n in names if names.count(n) > 1})
    if dup:
        errors.append(f"duplicate component names: {dup}")
    for c in spec.get("components", []):
        if c.get("mass_g", 0) <= 0:
            errors.append(f"component '{c.get('name')}' needs mass_g > 0 (use envelopes for zero-mass keep-outs)")
        if c.get("shape") not in {"box", "cylinder", "cone", "cell_pack"}:
            errors.append(f"component '{c.get('name')}' shape must be box | cylinder | cone | cell_pack")
    return errors


def create_airframe_server(cfg: AirframeConfig) -> FastMCP:
    mcp = FastMCP("airframe")
    root = Path(cfg.output_root)
    read_roots = [root, *(Path(p) for p in cfg.read_roots)]
    locks: dict[str, asyncio.Lock] = {}

    def readable(path: str) -> Path:
        p = Path(path)
        if not p.is_absolute():
            p = root / p
        try:
            p = p.resolve(strict=True)
        except (FileNotFoundError, OSError):
            raise ToolError(f"File not found (or outside output_root/read_roots): {path}") from None
        if not any(p == r or r in p.parents for r in read_roots):
            raise ToolError(f"Path is outside output_root/read_roots: {p}")
        if not p.is_file():
            raise ToolError(f"Not a file: {p}")
        return p

    def build_dir(output_name: str) -> Path:
        if not NAME.match(output_name):
            raise ToolError("output_name must match ^[a-z0-9][a-z0-9._-]{0,63}$ (no path separators)")
        return root / output_name

    async def run(python: str | None, worker: str, request: dict, what: str) -> dict:
        if not python:
            raise ToolError(f"{what} needs an interpreter that is not configured in the 'airframe' config section")
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", "/tmp"),
               "LANG": "C.UTF-8", "MPLBACKEND": "Agg", "MPLCONFIGDIR": tempfile.gettempdir() + "/unified-airframe-mpl",
               "PYTHONDONTWRITEBYTECODE": "1"}
        try:
            proc = await asyncio.create_subprocess_exec(python, str(WORKERS / worker), stdin=asyncio.subprocess.PIPE,
                                                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env)
        except OSError as error:
            raise ToolError(f"{what}: worker interpreter not found or not executable: {python} ({error.strerror})") from None
        try:
            out, err = await asyncio.wait_for(proc.communicate(json.dumps(request).encode()), cfg.timeout_seconds)
        except (asyncio.TimeoutError, TimeoutError):
            proc.kill()
            await proc.wait()
            raise ToolError(f"{what} timed out after {cfg.timeout_seconds:g} s; partial files may exist, inspect before retrying")
        lines = out.decode(errors="replace").strip().splitlines()
        try:
            response = json.loads(lines[-1]) if lines else None
        except json.JSONDecodeError:
            response = None
        if not isinstance(response, dict):
            raise ToolError(f"{what} worker exited {proc.returncode} without a JSON result. stderr tail: "
                            f"{err.decode(errors='replace')[-1500:]}")
        if not response.get("ok"):
            raise ToolError(f"{what} failed: {response.get('error_type')}: {response.get('error')}\n"
                            f"worker traceback tail:\n{response.get('traceback', '')[-1500:]}")
        return response["result"]

    @mcp.tool(name="build_fixed_wing", annotations=WRITE)
    async def build_fixed_wing(
        spec: Annotated[dict[str, Any], Field(description="Fixed-wing airframe spec (schema unified-cad-mcp/fixed-wing-airframe/1); see docs/airframe-spec.md")],
        output_name: Annotated[str, Field(description="Slug for <output_root>/<output_name>/")],
        replace: Annotated[bool, Field(description="Replace a previous build directory created by this tool (marker file required)")] = False,
    ) -> dict:
        """Build a parametric fixed-wing airframe headlessly in FreeCAD and validate it.

        Lofts NACA 4-digit wing/V-tail panels, a superellipse fuselage, splits control
        surfaces at hinge lines, adds spar tubes and equipment envelopes, cuts pockets,
        then reports per-part and total mass, CG and inertia (exact integrals over a
        fine tessellation; OCCT GProp shown only as a cross-check), closed-solid
        validity, mirror symmetry, containment/wall clearance, interference and
        budget-vs-CAD checks. Exports STEP (per part + named assembly), STL, FCStd
        and a viewer parts JSON. Geometry is a conceptual OML/packaging model, not a
        released manufacturing definition.
        """
        errors = _spec_errors(spec)
        if errors:
            raise ToolError("Invalid airframe spec:\n- " + "\n- ".join(errors))
        target = build_dir(output_name)
        lock = locks.setdefault(output_name, asyncio.Lock())
        if lock.locked():
            raise ToolError(f"A build into '{output_name}' is already running")
        async with lock:
            if target.exists():
                if not replace:
                    raise ToolError(f"{target} exists; pass replace=true to rebuild it")
                if not (target / MARKER).is_file():
                    raise ToolError(f"{target} exists but was not created by airframe_build_fixed_wing (no {MARKER}); refusing to replace")
                shutil.rmtree(target)
            # The marker is written BEFORE the worker runs so a crashed/timed-out build can still be replaced.
            target.mkdir(parents=True)
            (target / MARKER).write_text(json.dumps({"tool": "airframe_build_fixed_wing", "spec_id": spec["id"]}), encoding="utf-8")
            result = await run(cfg.freecad_python, "freecad_airframe.py",
                               {"op": "build", "spec": spec, "output_dir": str(target), "replace": False},
                               "airframe_build_fixed_wing")
        result["output_dir"] = str(target)
        return result

    @mcp.tool(name="inspect_step", annotations=READ)
    async def inspect_step(
        path: Annotated[str, Field(description="STEP file inside output_root or a configured read_root (relative paths resolve against output_root)")],
        density_g_cm3: Annotated[float | None, Field(gt=0, le=25, allow_inf_nan=False)] = None,
    ) -> dict:
        """Deterministic STEP validation: per-solid validity, closed shells, volume (mesh-integrated AND OCCT GProp), area, centroid, bounding box; optional mass/inertia for a uniform density."""
        p = readable(path)
        if p.suffix.lower() not in {".step", ".stp"}:
            raise ToolError("inspect_step accepts .step/.stp files only")
        return await run(cfg.freecad_python, "freecad_airframe.py", {"op": "inspect_step", "path": str(p), "density_g_cm3": density_g_cm3},
                         "airframe_inspect_step")

    @mcp.tool(name="render_preview", annotations=WRITE)
    async def render_preview(
        output_name: str,
        views: list[Literal["iso", "top", "side", "front", "rear"]] = ["iso", "top", "side", "front"],
        hidden_parts: Annotated[list[str], Field(max_length=200)] = [],
        explode: Annotated[float, Field(ge=0, le=2, allow_inf_nan=False)] = 0.0,
        markers: Annotated[list[Marker], Field(max_length=20)] = [],
        prefix: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,40}$")] = "preview",
        width_px: Annotated[int, Field(ge=320, le=4000)] = 1600,
        height_px: Annotated[int, Field(ge=240, le=4000)] = 1000,
    ) -> dict:
        """Headless PNG previews (matplotlib Agg, orthographic, flat shading) of a build's viewer JSON, written to <build>/previews/.

        Painter's-algorithm occlusion is approximate. Markers (e.g. CG and neutral
        point) are drawn on top. Images still need a human or model to look at them.
        """
        target = build_dir(output_name)
        viewer = sorted(target.glob("*.viewer.json"))
        if not viewer:
            raise ToolError(f"No *.viewer.json in {target}; run airframe_build_fixed_wing first")
        return await run(cfg.freecad_python, "freecad_airframe.py", {
            "op": "render", "viewer_json": str(viewer[0]), "output_dir": str(target / "previews"), "views": views,
            "hidden_parts": hidden_parts, "explode": explode, "markers": [m.model_dump() for m in markers], "prefix": prefix,
            "title": viewer[0].name.replace(".viewer.json", ""), "width_px": width_px, "height_px": height_px},
            "airframe_render_preview")

    @mcp.tool(name="aero_crosscheck", annotations=READ)
    async def aero_crosscheck(
        spec: Annotated[dict[str, Any], Field(description="Same fixed-wing airframe spec used for the build")],
        mass_g: Annotated[float, Field(gt=0, le=150_000, allow_inf_nan=False)],
        cg_mm: Vec3,
        velocity_m_s: Annotated[float, Field(gt=1, le=100, allow_inf_nan=False)],
        altitude_m: Annotated[float, Field(ge=-500, le=11000, allow_inf_nan=False)] = 0.0,
    ) -> dict:
        """Independent aerodynamic cross-check with AeroSandbox (MIT): level-flight trim, AeroBuildup (NeuralFoil + fuselage) and VLM neutral point, static margin, CL/CD, Cn_beta, Cl_beta.

        Compare with drone_fixed_wing_analysis; disagreement means the closed-form
        estimate should not be trusted without wind-tunnel/CFD/flight evidence.
        """
        errors = _spec_errors(spec)
        if errors:
            raise ToolError("Invalid airframe spec:\n- " + "\n- ".join(errors))
        return await run(cfg.aerosandbox_python, "aerosandbox_aero.py",
                         {"spec": spec, "mass_g": mass_g, "cg_mm": cg_mm, "velocity_m_s": velocity_m_s, "altitude_m": altitude_m},
                         "airframe_aero_crosscheck")

    return mcp
