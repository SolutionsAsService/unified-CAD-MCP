"""Scene-base layout check for miniatures, driven by the fantasy-miniatures dwarf-blacksmith (figure + anvil on one 32 mm base).

Registered into the "miniature" namespace by create_miniature_server (appears as miniature_base_layout_check):
- base_layout_check: several printed parts plugged into one base. From plan-view footprints (ellipses), D-flat peg
  sockets and the magnet pocket it checks that every footprint sits on the base top, parts keep a gap (or touch on
  purpose), every socket lies inside its part's footprint, the material between socket and rim and between socket and
  magnet pocket is at least the supported wall, and the floor under each socket and above the pocket holds.

Why a separate tool: miniature_base_and_peg sizes ONE peg and ONE pocket in isolation; a scene base has several sockets
and a pocket competing for the same few millimetres, and the thin wall between two holes is invisible to the outer-skin
checks until the STL is built. This answers it from numbers, before modelling.

All HEURISTIC and pure Python. Sources: Formlabs Design Guide (resin supported wall 0.4 mm, clearance 0.5 mm); the
FDM limits come from PROCESSES in miniature.py. Ellipse gaps are sampled over 360 directions (error < 0.01 x radius).
"""
from __future__ import annotations

import math
from typing import Annotated

from fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from .miniature import PROCESSES, READ_ONLY, Process


def _ell_gap(a: dict, b: dict) -> float:
    """Approximate clear gap between two axis-aligned ellipses (negative = overlap), by sampling a's boundary."""
    best = math.inf
    for i in range(360):
        t = math.radians(i)
        x, y = a["xy"][0] + a["r"][0] * math.cos(t), a["xy"][1] + a["r"][1] * math.sin(t)
        k = math.hypot((x - b["xy"][0]) / b["r"][0], (y - b["xy"][1]) / b["r"][1])
        best = min(best, (k - 1) * min(b["r"]))
    return best


def base_layout_check(base_d_mm: float, base_height_mm: float, parts: list[dict], magnet: dict | None = None,
                      top_inset_mm: float = 0.0, process: str = "msla_resin") -> dict:
    """parts: [{name, xy, r: [rx, ry] footprint semi-axes, socket: {xy, d, depth}?, touch_ok: [names]?}];
    magnet: {xy, d, h} pocket in the underside; sockets are blind holes from the base top (z = base_height_mm)."""
    p = PROCESSES[process]
    wall, clear = p["min_wall_supported_mm"] - 1e-9, p["min_clearance_mm"] - 1e-9  # a wall exactly at the limit passes
    R_top = base_d_mm / 2 - top_inset_mm
    checks = []

    def check(name, value, ok, limit):
        checks.append({"name": name, "value": round(value, 3), "pass": bool(ok), "limit": limit})

    for q in parts:
        reach = max(math.hypot(q["xy"][0] + q["r"][0] * math.cos(t), q["xy"][1] + q["r"][1] * math.sin(t))
                    for t in (math.radians(i) for i in range(360)))
        check(f"{q['name']}: footprint inside the base top", R_top - reach, reach <= R_top, ">= 0 mm to the top edge")
        s = q.get("socket")
        if s:
            k = math.hypot((s["xy"][0] - q["xy"][0]) / q["r"][0], (s["xy"][1] - q["xy"][1]) / q["r"][1])
            margin = (1 - k) * min(q["r"]) - s["d"] / 2
            check(f"{q['name']}: socket under the footprint", margin, margin >= wall, f">= {wall:.2f} mm inside the footprint edge")
            rim = R_top - math.hypot(*s["xy"]) - s["d"] / 2
            check(f"{q['name']}: wall socket to rim", rim, rim >= wall, f">= {wall:.2f} mm")
            floor = base_height_mm - s["depth"]
            check(f"{q['name']}: floor under socket", floor, floor >= wall, f">= {wall:.2f} mm")
            if magnet:
                plan = math.dist(s["xy"], magnet["xy"]) - (s["d"] + magnet["d"]) / 2
                vert = (base_height_mm - s["depth"]) - magnet["h"]
                sep = max(plan, vert)
                check(f"{q['name']}: wall socket to magnet pocket", sep, sep >= wall, f">= {wall:.2f} mm in plan or in height")
    for i, a in enumerate(parts):
        for b in parts[i + 1:]:
            g = min(_ell_gap(a, b), _ell_gap(b, a))
            touch = b["name"] in a.get("touch_ok", []) or a["name"] in b.get("touch_ok", [])
            ok = touch or g >= clear
            check(f"gap {a['name']} / {b['name']}", g, ok, "touch allowed" if touch else f">= {clear:.2f} mm (or list in touch_ok)")
    if magnet:
        cover = base_height_mm - magnet["h"]
        check("floor above magnet pocket", cover, cover >= wall, f">= {wall:.2f} mm")
        rim = base_d_mm / 2 - math.hypot(*magnet["xy"]) - magnet["d"] / 2
        check("wall magnet pocket to rim", rim, rim >= wall, f">= {wall:.2f} mm")
    return {"process": process, "base_d_mm": base_d_mm, "checks": checks, "all_checks_pass": all(c["pass"] for c in checks),
            "method": "plan-view ellipse footprints and cylindrical sockets/pocket; gaps sampled over 360 directions",
            "estimate_kind": "HEURISTIC layout screen: confirm on the built STL with miniature_printability_check"}


class SocketIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    xy: Annotated[list[float], Field(min_length=2, max_length=2)]
    d: Annotated[float, Field(gt=0, le=20)]
    depth: Annotated[float, Field(gt=0, le=20)]


class PartIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    xy: Annotated[list[float], Field(min_length=2, max_length=2)]
    r: Annotated[list[Annotated[float, Field(gt=0)]], Field(min_length=2, max_length=2, description="footprint semi-axes [rx, ry] mm")]
    socket: SocketIn | None = None
    touch_ok: list[str] = []


class MagnetIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    xy: Annotated[list[float], Field(min_length=2, max_length=2)]
    d: Annotated[float, Field(gt=0, le=30)]
    h: Annotated[float, Field(gt=0, le=10)]


def register(mcp: FastMCP, load) -> None:
    @mcp.tool(name="base_layout_check", annotations=READ_ONLY)
    def base_layout_check_tool(
        base_d_mm: Annotated[float, Field(gt=5, le=200)],
        base_height_mm: Annotated[float, Field(gt=0.5, le=30)],
        parts: Annotated[list[PartIn], Field(min_length=1, max_length=20)],
        magnet: MagnetIn | None = None,
        top_inset_mm: Annotated[float, Field(ge=0, le=10)] = 0.0,
        process: Process = "msla_resin",
    ) -> dict:
        """HEURISTIC layout screen for a scene base carrying several parts: footprints on the base top, gaps between
        parts, socket inside its footprint, socket-to-rim, socket-to-magnet-pocket and floor walls vs the process."""
        return base_layout_check(base_d_mm, base_height_mm, [q.model_dump() for q in parts],
                                 magnet.model_dump() if magnet else None, top_inset_mm, process)
