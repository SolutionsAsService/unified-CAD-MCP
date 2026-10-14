"""Figure tools for miniatures, driven by building the fantasy-miniatures "Heroes" party (knight, rogue, cleric, ranger).

Registered into the "miniature" namespace by create_miniature_server (so they appear as miniature_<name>):
- slender_feature_check: cross-section thickness along named slender features of one STL (sword blades, mace hafts,
  dagger blades, bow limbs and strings, staffs): slices the mesh perpendicular to each feature axis at evenly spaced
  stations, takes the section loop that holds the axis, and reports its minimum caliper width (the thinnest way
  through) and equivalent diameter against a per-feature minimum (default 1.0 mm, the line's weapon rule).

Why a separate tool: miniature_printability_check measures wall thickness by inward-normal rays and fails only when the
*area* under the process wall passes a tolerance, and it detects wires along the print z axis only. A 0.8 mm blade or a
tilted 0.9 mm haft passes both (above the 0.4 mm supported wall, not vertical), yet breaks in the wash. A per-feature
section answers the sculptor's question directly ("is this haft at least 1 mm everywhere?") and names the feature.

All HEURISTIC and pure Python (stdlib only). Sources:
- Formlabs Design Guide (resin): unsupported wall 0.6 mm, supported 0.4 mm; wires 0.3 mm up to 7 mm, 1.5 mm up to 30 mm.
- Minimum width of a point set by rotating calipers (Shamos 1978; Toussaint 1983); sampled here over 90 directions,
  which overestimates the true width by at most w x (1/cos 1 deg - 1), about 0.015 %.
- Hobby practice for 28-32 mm resin figures: weapons, hafts and bow limbs at 1.0 mm or more survive washing, curing and
  handling (a studio rule of thumb, not a standard).
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Annotated

from fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from .flotation import Tri
from .miniature import PROCESSES, READ_ONLY, Process, wire_max_length

Vec3 = Annotated[list[float], Field(min_length=3, max_length=3)]


class SlenderFeature(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, Field(min_length=1, max_length=80)]
    start_mm: Vec3 = Field(description="one end of the feature axis (e.g. the sword guard), in the STL frame")
    end_mm: Vec3 = Field(description="the other end (e.g. the sword point)")
    min_mm: Annotated[float, Field(gt=0, le=10)] = 1.0
    stations: Annotated[int, Field(ge=2, le=200)] = 12
    search_radius_mm: Annotated[float, Field(gt=0, le=50)] = 4.0
    end_margin: Annotated[float, Field(ge=0, lt=0.5, description="fraction of the length skipped at each end (rounded tips)")] = 0.06


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _unit(a):
    n = math.sqrt(_dot(a, a))
    return (a[0] / n, a[1] / n, a[2] / n)


def _basis(axis):
    a = _unit(axis)
    h = (1.0, 0.0, 0.0) if abs(a[0]) < 0.9 else (0.0, 1.0, 0.0)
    e1 = _unit(_cross(a, h))
    return a, e1, _cross(a, e1)


def section(tris: list[Tri], point, axis, radius: float):
    """2D segments of the mesh section by the plane through point with normal axis (plane basis e1, e2), keeping
    segments with an end within radius of point."""
    a, e1, e2 = _basis(axis)
    segs = []
    r2 = radius * radius
    for t in tris:
        d = [_dot(_sub(q, point), a) for q in t]
        if min(d) > 0 or max(d) < 0:
            continue
        pts = []
        for i in range(3):
            p, q, dp, dq = t[i], t[(i + 1) % 3], d[i], d[(i + 1) % 3]
            if (dp < 0 <= dq) or (dq < 0 <= dp):
                s = dp / (dp - dq)
                x = (p[0] + s * (q[0] - p[0]), p[1] + s * (q[1] - p[1]), p[2] + s * (q[2] - p[2]))
                rel = _sub(x, point)
                pts.append((_dot(rel, e1), _dot(rel, e2)))
        if len(pts) != 2:
            continue
        (ux, uy), (vx, vy) = pts
        if ux * ux + uy * uy > r2 and vx * vx + vy * vy > r2:
            continue
        segs.append(((ux, uy), (vx, vy)))
    return segs


def _components(segs):
    key = lambda p: (round(p[0], 4), round(p[1], 4))
    parent = list(range(len(segs)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    by_pt = defaultdict(list)
    for i, (p, q) in enumerate(segs):
        by_pt[key(p)].append(i)
        by_pt[key(q)].append(i)
    for ids in by_pt.values():
        for j in ids[1:]:
            a, b = find(ids[0]), find(j)
            if a != b:
                parent[a] = b
    comps = defaultdict(list)
    for i in range(len(segs)):
        comps[find(i)].append(segs[i])
    return list(comps.values())


def _contains_origin(segs) -> bool:
    inside = False
    for (ax, ay), (bx, by) in segs:  # ray along +x from the origin, parity of crossings
        if (ay > 0) != (by > 0):
            x = ax + (0 - ay) * (bx - ax) / (by - ay)
            if x > 0:
                inside = not inside
    return inside


def _area(segs):
    """Unsigned area of a closed loop from unordered, unoriented segments (fan from the origin with segment orientation
    fixed so that the origin-side cross products agree with the loop's parity is not needed: use the shoelace on the
    chained loop)."""
    adj = defaultdict(list)
    key = lambda p: (round(p[0], 4), round(p[1], 4))
    for p, q in segs:
        adj[key(p)].append(key(q))
        adj[key(q)].append(key(p))
    seen, total = set(), 0.0
    for start in list(adj):
        if start in seen:
            continue
        loop, prev, cur = [start], None, start
        seen.add(start)
        while True:
            nxt = next((n for n in adj[cur] if n != prev and n not in seen), None)
            if nxt is None:
                break
            loop.append(nxt)
            seen.add(nxt)
            prev, cur = cur, nxt
        total += abs(sum(loop[i][0] * loop[(i + 1) % len(loop)][1] - loop[(i + 1) % len(loop)][0] * loop[i][1]
                         for i in range(len(loop))) / 2)
    return total


def _width(points, directions=90):
    best, best_dir = math.inf, 0.0
    for k in range(directions):
        th = math.pi * k / directions
        c, s = math.cos(th), math.sin(th)
        proj = [x * c + y * s for x, y in points]
        w = max(proj) - min(proj)
        if w < best:
            best, best_dir = w, math.degrees(th)
    return best, best_dir


def slender_feature_check(tris: list[Tri], features: list[dict], process: str = "msla_resin") -> dict:
    p = PROCESSES[process]
    out = []
    for f in features:
        s, e = tuple(f["start_mm"]), tuple(f["end_mm"])
        axis = _sub(e, s)
        length = math.sqrt(_dot(axis, axis))
        if length <= 0:
            raise ValueError(f"{f['name']}: start and end coincide")
        n, m = f.get("stations", 12), f.get("end_margin", 0.06)
        rows = []
        for i in range(n):
            t = m + (1 - 2 * m) * i / (n - 1)
            c = (s[0] + t * axis[0], s[1] + t * axis[1], s[2] + t * axis[2])
            comps = [k for k in _components(section(tris, c, axis, f.get("search_radius_mm", 4.0))) if _contains_origin(k)]
            if not comps:
                rows.append({"t": round(t, 3), "at_mm": [round(v, 3) for v in c], "found": False})
                continue
            loop = min(comps, key=_area)  # innermost loop round the axis: a blade inside a fist is measured as the blade
            area = _area(loop)
            w, th = _width([q for sg in loop for q in sg])
            rows.append({"t": round(t, 3), "at_mm": [round(v, 3) for v in c], "found": True, "min_width_mm": round(w, 3),
                         "width_direction_deg": round(th, 1), "area_mm2": round(area, 3),
                         "equivalent_diameter_mm": round(2 * math.sqrt(area / math.pi), 3)})
        found = [r for r in rows if r["found"]]
        thinnest = min(found, key=lambda r: r["min_width_mm"]) if found else None
        limit = f.get("min_mm", 1.0)
        ok = bool(found) and len(found) == len(rows) and thinnest["min_width_mm"] >= limit
        d_mean = sum(r["equivalent_diameter_mm"] for r in found) / len(found) if found else 0.0
        out.append({"name": f["name"], "length_mm": round(length, 3), "min_mm": limit, "pass": ok,
                    "min_width_mm": thinnest["min_width_mm"] if thinnest else None,
                    "thinnest_at_mm": thinnest["at_mm"] if thinnest else None,
                    "stations_missing": [r["t"] for r in rows if not r["found"]],
                    "free_wire_length_limit_mm": round(wire_max_length(d_mean, p["wire_mm"]), 2) if found else None,
                    "stations": rows})
    return {"process": process, "features": out, "all_checks_pass": all(f["pass"] for f in out),
            "checks": [{"name": f"{f['name']}: min section width", "value": f["min_width_mm"], "limit": f["min_mm"], "pass": f["pass"]}
                       for f in out],
            "method": "Plane sections perpendicular to each feature axis; the smallest section loop that holds the axis point; "
                      "minimum caliper width over 90 directions; equivalent diameter from the loop area. A station whose "
                      "section misses the axis is reported missing and fails, so a wrong axis cannot pass silently.",
            "estimate_kind": "HEURISTIC section gauge on the mesh as given (use the print STL); not a strength analysis."}


def register(mcp: FastMCP, load) -> None:
    import asyncio

    @mcp.tool(name="slender_feature_check", annotations=READ_ONLY)
    async def slender_feature_check_tool(
        stl_path: Annotated[str, Field(description="part STL in mm (any pose; feature axes in the same frame)")],
        features: Annotated[list[SlenderFeature], Field(min_length=1, max_length=50)],
        process: Process = "msla_resin",
    ) -> dict:
        """Minimum cross-section width along named slender features (blades, hafts, bow limbs, strings) vs a per-feature minimum."""
        return await asyncio.to_thread(slender_feature_check, load(stl_path), [f.model_dump() for f in features], process)
