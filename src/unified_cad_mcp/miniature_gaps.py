"""Gap and loop audit for miniatures, driven by building the fantasy-miniatures "Bestiary" (goblin archer, orc brute,
skeleton warrior, dire wolf).

Registered into the "miniature" namespace by create_miniature_server (so it appears as miniature_gap_check):
- gap_check: narrow air gaps between surfaces of one STL that face each other (a bowstring passing a knee, an arm
  hugging a torso, a tail grazing a rock, a shield rim against a thigh). From triangle centroids it casts a ray along
  the OUTWARD normal and records the distance to the next surface that faces back at it. Gaps under the process
  clearance fill with partly cured resin, print as a thin web or a sealed slit that never washes out: "grazing
  intersections" in this line's failure list. It also counts handles (through-loops) per shell from the Euler
  characteristic, so a sculpt that meant to fuse an arm to a hip, or a ribcage meant to be solid, shows an unexpected loop.

Why a separate tool: miniature_printability_check measures material (inward rays, walls, wires, voids); a narrow
slot of *air* between two thick parts passes every one of those checks.

HEURISTIC and pure Python (stdlib only). Sources:
- Formlabs Design Guide (resin): clearance between parts >= 0.5 mm (min_clearance_mm in PROCESSES); FDM value from the
  same table. Narrow gaps also trap resin that wicks into slits during washing (Formlabs, "Designing for resin").
- Ray thickness / ray clearance at centroids as in Meshmixer and trimesh (ray casting, Moller-Trumbore 1997).
- Euler characteristic of a closed orientable surface: V - E + F = 2 - 2g, g = number of handles (Euler, Poincare).
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

from .flotation import Tri
from .miniature import PROCESSES, READ_ONLY, Process, _normal, rotate


def _ray_hit(o, d, tri):
    (ax, ay, az), (bx, by, bz), (cx, cy, cz) = tri
    e1x, e1y, e1z, e2x, e2y, e2z = bx - ax, by - ay, bz - az, cx - ax, cy - ay, cz - az
    px, py, pz = d[1] * e2z - d[2] * e2y, d[2] * e2x - d[0] * e2z, d[0] * e2y - d[1] * e2x
    det = e1x * px + e1y * py + e1z * pz
    if abs(det) < 1e-12:
        return None
    inv = 1.0 / det
    tx, ty, tz = o[0] - ax, o[1] - ay, o[2] - az
    u = (tx * px + ty * py + tz * pz) * inv
    if u < 0 or u > 1:
        return None
    qx, qy, qz = ty * e1z - tz * e1y, tz * e1x - tx * e1z, tx * e1y - ty * e1x
    v = (d[0] * qx + d[1] * qy + d[2] * qz) * inv
    if v < 0 or u + v > 1:
        return None
    return (e2x * qx + e2y * qy + e2z * qz) * inv


def _gaps(tris: list[Tri], probe: float, max_samples: int):
    """(gap mm or inf, area weight, point) per sampled centroid: outward ray to the next surface facing back at it."""
    h = max(probe, 0.5)
    grid: dict = defaultdict(list)
    for i, t in enumerate(tris):
        lo = [math.floor(min(p[k] for p in t) / h) for k in range(3)]
        hi = [math.floor(max(p[k] for p in t) / h) for k in range(3)]
        for a in range(lo[0], hi[0] + 1):
            for b in range(lo[1], hi[1] + 1):
                for c in range(lo[2], hi[2] + 1):
                    grid[(a, b, c)].append(i)
    normals = [None] * len(tris)
    step = max(1, math.ceil(len(tris) / max_samples))
    out = []
    for i in range(0, len(tris), step):
        t = tris[i]
        n, area = _normal(t)
        if not area:
            continue
        o = tuple((t[0][k] + t[1][k] + t[2][k]) / 3 for k in range(3))
        end = tuple(o[k] + n[k] * probe for k in range(3))
        lo = [math.floor(min(o[k], end[k]) / h) for k in range(3)]
        hi = [math.floor(max(o[k], end[k]) / h) for k in range(3)]
        cand = {j for a in range(lo[0], hi[0] + 1) for b in range(lo[1], hi[1] + 1) for c in range(lo[2], hi[2] + 1)
                for j in grid.get((a, b, c), ())}
        best = math.inf
        for j in cand:
            if j == i:
                continue
            d = _ray_hit(o, n, tris[j])
            if d is None or not (1e-6 < d < best):
                continue
            if normals[j] is None:
                normals[j] = _normal(tris[j])[0]
            nj = normals[j]
            if nj[0] * n[0] + nj[1] * n[1] + nj[2] * n[2] < -0.2:  # the far surface faces back: an air gap, not a crease
                best = d
        out.append((best if best <= probe else math.inf, area * step, o))
    return out


def _handles(tris: list[Tri]) -> list[dict]:
    """Genus per edge-connected shell from V - E + F (vertices welded to 1e-6 mm)."""
    key = {}
    faces = []
    for t in tris:
        faces.append(tuple(key.setdefault(tuple(round(c, 6) for c in p), len(key)) for p in t))
    parent = list(range(len(faces)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    edge_face: dict = {}
    for fi, (a, b, c) in enumerate(faces):
        for e in ((a, b), (b, c), (c, a)):
            e = (min(e), max(e))
            if e in edge_face:
                ra, rb = find(fi), find(edge_face[e])
                if ra != rb:
                    parent[ra] = rb
            else:
                edge_face[e] = fi
    shells: dict = defaultdict(lambda: {"F": 0, "E": set(), "V": set()})
    for fi, (a, b, c) in enumerate(faces):
        s = shells[find(fi)]
        s["F"] += 1
        s["V"].update((a, b, c))
        s["E"].update((min(e), max(e)) for e in ((a, b), (b, c), (c, a)))
    out = []
    for s in shells.values():
        chi = len(s["V"]) - len(s["E"]) + s["F"]
        out.append({"triangles": s["F"], "euler_characteristic": chi, "handles": (2 - chi) // 2 if chi <= 2 else 0})
    return sorted(out, key=lambda r: -r["triangles"])


def gap_check(tris: list[Tri], process: str = "msla_resin", rotate_deg: list[float] | None = None, min_gap_mm: float | None = None,
              gap_area_tolerance_mm2: float = 1.0, expected_handles: int | None = None, max_samples: int = 30000) -> dict:
    p = PROCESSES[process]
    tris = rotate(tris, *(rotate_deg or [0, 0, 0]))
    limit = min_gap_mm or p["min_clearance_mm"]
    rows = _gaps(tris, probe=limit * 2, max_samples=max_samples)
    narrow = [(g, a, q) for g, a, q in rows if g < limit]
    area = sum(a for _, a, _ in narrow)
    spots = []
    for g, a, q in sorted(narrow):
        if all(math.dist(q, s["at"]) > 1.0 for s in spots):
            spots.append({"at": [round(c, 2) for c in q], "gap_mm": round(g, 3)})
        if len(spots) >= 12:
            break
    shells = _handles(tris)
    checks = [{"name": "narrow gaps", "value": round(area, 3), "pass": area <= gap_area_tolerance_mm2,
               "limit": f"<= {gap_area_tolerance_mm2} mm2 of surface facing another within {limit} mm", "severity": "fail",
               "basis": "Formlabs: clearance between surfaces >= 0.5 mm in resin; narrower slits print as webs or trap resin."}]
    if expected_handles is not None:
        got = sum(s["handles"] for s in shells)
        checks.append({"name": "through-loops (handles)", "value": got, "pass": got == expected_handles, "limit": expected_handles,
                       "severity": "warn", "basis": "Euler characteristic V - E + F = 2 - 2g per closed shell; an unexpected loop is "
                                                    "an unintended strut or a fusion that did not happen."})
    finite = [g for g, _, _ in rows if g < math.inf]
    return {"process": process, "min_gap_mm": limit, "narrow_gap_area_mm2": round(area, 3),
            "smallest_gap_mm": round(min(finite), 3) if finite else f"> {round(limit * 2, 3)}", "narrow_spots": spots,
            "shells": shells, "handles_total": sum(s["handles"] for s in shells), "samples": len(rows), "checks": checks,
            "all_checks_pass": all(c["pass"] for c in checks if c["severity"] == "fail"),
            "estimate_kind": "HEURISTIC: outward-normal ray clearance at triangle centroids (gaps between surfaces that face each "
                             "other) and Euler-characteristic handle count; not a slicer."}


def register(mcp: FastMCP, load) -> None:
    import asyncio

    @mcp.tool(name="gap_check", annotations=READ_ONLY)
    async def gap_check_tool(
        stl_path: Annotated[str, Field(description="part STL in mm (any pose; give rotate_deg to match the print pose)")],
        process: Process = "msla_resin",
        min_gap_mm: Annotated[float | None, Field(gt=0, le=5)] = None,
        gap_area_tolerance_mm2: Annotated[float, Field(ge=0, le=100)] = 1.0,
        expected_handles: Annotated[int | None, Field(ge=0, le=1000)] = None,
        rotate_deg: Annotated[list[float] | None, Field(min_length=3, max_length=3)] = None,
    ) -> dict:
        """Narrow air gaps between facing surfaces (outward-normal ray clearance; resin slits, webs, grazing contacts) and
        through-loop count per shell (Euler characteristic). Complements miniature_printability_check, which measures material."""
        return await asyncio.to_thread(gap_check, load(stl_path), process, rotate_deg, min_gap_mm, gap_area_tolerance_mm2, expected_handles)
