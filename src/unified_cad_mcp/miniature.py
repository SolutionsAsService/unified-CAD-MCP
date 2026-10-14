"""Miniature tools: tabletop / display figures, terrain and scale models printed in MSLA resin or fine FDM.

Tools (namespace "miniature", mounted with drone_tools; the STL tools read inside airframe.output_root/read_roots):
- scale_convert: real size <-> model size for named scales, and which features fall below the process limits.
- printability_check: one STL part: closed/manifold, bodies, enclosed voids, thin walls, thin spikes, overhang area,
  islands, suction cups (cavities sealed by the vat film), volume / resin mass and build-volume fit.
- base_and_peg: standard base sizes, magnet pockets and keyed peg/socket fits for multi-part kits.
- support_plan: samples print orientations and ranks them by largest layer area (peel force), support area and
  support-start points.

Everything is HEURISTIC and pure Python (stdlib only, like flotation.py). Sources for the numbers:
- Formlabs Design Guide (Clear resin, 100 um, Form 1+/Form 2): supported wall 0.4 mm, unsupported wall 0.6 mm,
  unsupported overhang <= 1 mm and >= 19 deg from level, embossed detail 0.1 mm, engraved detail 0.4 mm, hole 0.5 mm,
  clearance 0.5 mm, drain hole 3.5 mm, wires 0.3 mm up to 7 mm tall and 1.5 mm up to 30 mm tall ("a wire is a feature
  whose length is greater than two times its width").
  https://formlabs-media.formlabs.com/filer_public/17/a1/17a190d9-9a76-4cf5-af12-3d0dda4121dc/formlabs-design-guide.pdf
- Prusa Knowledge Base: "Overhang threshold" is the most horizontal slope, measured from the horizontal plane, that
  prints without support (https://help.prusa3d.com/article/support-material_1698); SLA hollowing adds drainage holes
  (https://help.prusa3d.com/article/hollowing_117285).
- CHITUBOX / Lychee island and suction-cup detection: an island is a layer region with nothing under it; a suction cup
  is a cavity the vat film seals off while it peels. Same definitions here, on a ray-cast voxel model.
- Wikipedia "Miniature wargame": figure "scale" in mm is measured inconsistently (to the eyes or to the crown);
  28 mm ~ 1:64 for a 1.78 m man measured to the crown. Eye height ~0.936 x stature (Drillis & Contini 1966 segment
  proportions, as tabulated in Winter, Biomechanics and Motor Control of Human Movement).
- trimesh (is_watertight, split, ray thickness) is the usual Python library for this; it is not a dependency here.
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from typing import Annotated, Literal

from fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from .flotation import Tri, _signed_volume
from .stl_report import analyze_shells

READ_ONLY = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
Pos = Annotated[float, Field(gt=0, allow_inf_nan=False)]

PROCESSES = {
    "msla_resin": {
        "min_wall_supported_mm": 0.4, "min_wall_unsupported_mm": 0.6, "min_hole_mm": 0.5, "min_embossed_mm": 0.1,
        "min_engraved_mm": 0.4, "min_clearance_mm": 0.5, "min_drain_hole_mm": 3.5, "self_supporting_deg": 19.0,
        "wire_mm": [[0.3, 7.0], [1.5, 30.0]], "layer_mm": 0.05,
        "fit_clearance_per_side_mm": {"press": 0.05, "glue": 0.1, "slip": 0.15},
        "basis": "Formlabs Design Guide (SLA, Clear resin, 100 um layers); hobby MSLA printers at 0.03-0.05 mm often hold "
                 "finer detail, so these limits are conservative. Fit clearances are a hobby convention, not from the "
                 "guide: print a stepped tolerance coupon on your printer and resin before trusting them.",
    },
    "fdm_0.2_nozzle": {
        "min_wall_supported_mm": 0.45, "min_wall_unsupported_mm": 0.8, "min_hole_mm": 0.6, "min_embossed_mm": 0.2,
        "min_engraved_mm": 0.3, "min_clearance_mm": 0.25, "min_drain_hole_mm": 3.5, "self_supporting_deg": 45.0,
        "wire_mm": [[0.6, 6.0], [2.0, 20.0]], "layer_mm": 0.08,
        "fit_clearance_per_side_mm": {"press": 0.1, "glue": 0.15, "slip": 0.25},
        "basis": "HEURISTIC for a 0.2 mm nozzle: a wall needs two ~0.22 mm extrusion lines (PrusaSlicer default width ~1.1 x "
                 "nozzle), embossed detail one line, the usual 45 deg overhang rule (PrusaSlicer 'overhang threshold' is "
                 "measured from the horizontal), pins ~10:1. No single published source: verify with a test coupon.",
    },
}
Process = Literal["msla_resin", "fdm_0.2_nozzle"]

EYE_FRACTION = 0.936  # eye height / stature (Drillis & Contini 1966)
ROUND_BASES_MM = [25, 28.5, 32, 40, 50, 60, 80, 90, 100, 130, 160]
OVAL_BASES_MM = [[60, 35], [75, 42], [90, 52], [105, 70], [120, 92], [170, 105]]
MAGNETS_MM = {"2x1": [2, 1], "3x1": [3, 1], "3x2": [3, 2], "4x2": [4, 2], "5x1": [5, 1], "5x2": [5, 2], "6x2": [6, 2],
              "6x3": [6, 3], "8x2": [8, 2], "10x2": [10, 2]}


def scale_ratio(scale: str, convention: Literal["eye", "crown"] = "eye", stature_mm: float = 1780.0) -> dict:
    """1:N ratio for '1:48' style scales or for heroic figure scales such as '28mm' / '32mm'."""
    s = scale.strip().lower().replace(" ", "")
    if m := re.fullmatch(r"1:(\d+(?:\.\d+)?)", s):
        return {"scale": scale, "ratio": float(m.group(1)), "kind": "ratio"}
    if m := re.fullmatch(r"(\d+(?:\.\d+)?)mm", s):
        fig = float(m.group(1))
        ref = stature_mm * (EYE_FRACTION if convention == "eye" else 1.0)
        return {"scale": scale, "ratio": round(ref / fig, 2), "kind": "figure", "convention": convention, "stature_mm": stature_mm,
                "note": f"A {fig:g} mm figure measured feet-to-{'eyes' if convention == 'eye' else 'crown'} of a {stature_mm:g} mm "
                        "person. Makers disagree on the convention (Wikipedia 'Miniature wargame'); heroic figures also "
                        "exaggerate heads, hands and weapons, so use the ratio for vehicles and terrain, not anatomy."}
    raise ValueError("scale must look like '1:48' or '28mm'")


def wire_max_length(d: float, table: list[list[float]]) -> float:
    """Longest printable free-standing wire of diameter d, interpolated in the process table (0 below its first row)."""
    (d0, l0), (d1, l1) = table[0], table[-1]
    if d < d0:
        return 0.0
    if d >= d1:
        return d * l1 / d1
    return l0 + (d - d0) * (l1 - l0) / (d1 - d0)


class Feature(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, Field(min_length=1, max_length=80)]
    kind: Literal["wall", "wire", "hole", "embossed", "engraved", "gap", "length"] = "length"
    real_mm: Pos | None = Field(default=None, description="Real (full-size) dimension")
    model_mm: Pos | None = Field(default=None, description="Model dimension (alternative to real_mm)")
    wire_length_real_mm: Pos | None = Field(default=None, description="For kind=wire: free length, full size")


LIMIT_KEY = {"wall": "min_wall_unsupported_mm", "hole": "min_hole_mm", "embossed": "min_embossed_mm",
             "engraved": "min_engraved_mm", "gap": "min_clearance_mm"}


def scale_convert(scale: str, features: list[Feature], process: str = "msla_resin",
                  convention: Literal["eye", "crown"] = "eye", stature_mm: float = 1780.0) -> dict:
    sc = scale_ratio(scale, convention, stature_mm)
    r = sc["ratio"]
    p = PROCESSES[process]
    rows = []
    for f in features:
        if (f.real_mm is None) == (f.model_mm is None):
            raise ValueError(f"{f.name}: give exactly one of real_mm or model_mm")
        model = f.model_mm if f.model_mm is not None else f.real_mm / r
        row = {"name": f.name, "kind": f.kind, "real_mm": round(model * r, 3), "model_mm": round(model, 4)}
        limit, ok = None, True
        if f.kind == "wire":
            limit = p["wire_mm"][0][0]
            longest = wire_max_length(model, p["wire_mm"])
            length = (f.wire_length_real_mm or 0) / r
            ok = model >= limit and length <= longest
            row.update(limit_mm=limit, max_free_length_mm=round(longest, 3), wire_length_model_mm=round(length, 3) if length else None)
        elif f.kind in LIMIT_KEY:
            limit = p[LIMIT_KEY[f.kind]]
            ok = model >= limit
            row["limit_mm"] = limit
        row["printable"] = ok
        if not ok and model < limit:
            row["exaggerate_to_mm"] = limit
            row["exaggeration_factor"] = round(limit / model, 2)
        rows.append(row)
    return {**sc, "process": process, "features": rows, "below_limit": [r["name"] for r in rows if not r["printable"]],
            "limits": {k: v for k, v in p.items() if k != "fit_clearance_per_side_mm"},
            "estimate_kind": "HEURISTIC: process limits from the cited design guides; thicken (heroic exaggeration) or "
                             "drop features below them. Walls use the unsupported-wall limit."}


# ---------- mesh helpers ----------

def _mat(axis: int, deg: float) -> list[list[float]]:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return [[[1, 0, 0], [0, c, -s], [0, s, c]], [[c, 0, s], [0, 1, 0], [-s, 0, c]], [[c, -s, 0], [s, c, 0], [0, 0, 1]]][axis]


def _mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def rotate(tris: list[Tri], rx: float = 0.0, ry: float = 0.0, rz: float = 0.0) -> list[Tri]:
    """Rotate about x, then y, then z (degrees) and drop the mesh so its lowest point is at z = 0."""
    m = _mul(_mat(2, rz), _mul(_mat(1, ry), _mat(0, rx)))
    (a, b, c), (d, e, f), (g, h, i) = m
    out = [tuple((a * x + b * y + c * z, d * x + e * y + f * z, g * x + h * y + i * z) for x, y, z in t) for t in tris]
    z0 = min(q[2] for t in out for q in t)
    return [tuple((q[0], q[1], q[2] - z0) for q in t) for t in out]


def _normal(t: Tri) -> tuple[tuple[float, float, float], float]:
    (ax, ay, az), (bx, by, bz), (cx, cy, cz) = t
    ux, uy, uz, vx, vy, vz = bx - ax, by - ay, bz - az, cx - ax, cy - ay, cz - az
    nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    n = math.sqrt(nx * nx + ny * ny + nz * nz)
    return ((nx / n, ny / n, nz / n) if n else (0.0, 0.0, 0.0)), n / 2


def _edges(tris: list[Tri]) -> dict:
    """Open, non-manifold and inconsistently wound edges after welding vertices to 0.1 um."""
    key = lambda p: (round(p[0], 4), round(p[1], 4), round(p[2], 4))
    idx: dict = {}
    directed: dict = defaultdict(int)
    degenerate = 0
    for t in tris:
        f = [idx.setdefault(key(p), len(idx)) for p in t]
        if len(set(f)) < 3:
            degenerate += 1
            continue
        for a, b in ((f[0], f[1]), (f[1], f[2]), (f[2], f[0])):
            directed[(a, b)] += 1
    undirected: dict = defaultdict(int)
    for (a, b), n in directed.items():
        undirected[(min(a, b), max(a, b))] += n
    return {"open_edges": sum(n == 1 for n in undirected.values()),
            "non_manifold_edges": sum(n > 2 for n in undirected.values()),
            "inconsistent_winding_edges": sum(n > 1 for n in directed.values()),
            "degenerate_triangles": degenerate, "vertices": len(idx)}


def _thickness(tris: list[Tri], probe: float, max_samples: int = 30000) -> list[tuple[float, float, tuple]]:
    """Local wall thickness at triangle centroids: distance along the inward normal to the next surface (as in
    trimesh/Meshmixer ray thickness). Returns (thickness, or inf beyond probe; area weight; point)."""
    h = max(probe, 0.5)
    grid: dict = defaultdict(list)
    for i, t in enumerate(tris):
        lo = [math.floor(min(p[k] for p in t) / h) for k in range(3)]
        hi = [math.floor(max(p[k] for p in t) / h) for k in range(3)]
        for a in range(lo[0], hi[0] + 1):
            for b in range(lo[1], hi[1] + 1):
                for c in range(lo[2], hi[2] + 1):
                    grid[(a, b, c)].append(i)
    step = max(1, math.ceil(len(tris) / max_samples))
    out = []
    for i in range(0, len(tris), step):
        t = tris[i]
        (nx, ny, nz), area = _normal(t)
        if not area:
            continue
        ox, oy, oz = ((t[0][k] + t[1][k] + t[2][k]) / 3 for k in range(3))
        dx, dy, dz = -nx, -ny, -nz
        ends = ((ox, oy, oz), (ox + dx * probe, oy + dy * probe, oz + dz * probe))
        lo = [math.floor(min(e[k] for e in ends) / h) for k in range(3)]
        hi = [math.floor(max(e[k] for e in ends) / h) for k in range(3)]
        cand = {j for a in range(lo[0], hi[0] + 1) for b in range(lo[1], hi[1] + 1) for c in range(lo[2], hi[2] + 1)
                for j in grid.get((a, b, c), ())}
        best = math.inf
        for j in cand:
            if j == i:
                continue
            (ax, ay, az), (bx, by, bz), (cx, cy, cz) = tris[j]
            e1x, e1y, e1z, e2x, e2y, e2z = bx - ax, by - ay, bz - az, cx - ax, cy - ay, cz - az
            px, py, pz = dy * e2z - dz * e2y, dz * e2x - dx * e2z, dx * e2y - dy * e2x
            det = e1x * px + e1y * py + e1z * pz
            if abs(det) < 1e-12:
                continue
            inv = 1.0 / det
            tx, ty, tz = ox - ax, oy - ay, oz - az
            u = (tx * px + ty * py + tz * pz) * inv
            if u < 0 or u > 1:
                continue
            qx, qy, qz = ty * e1z - tz * e1y, tz * e1x - tx * e1z, tx * e1y - ty * e1x
            v = (dx * qx + dy * qy + dz * qz) * inv
            if v < 0 or u + v > 1:
                continue
            d = (e2x * qx + e2y * qy + e2z * qz) * inv
            if 1e-6 < d < best:
                best = d
        out.append((best if best <= probe else math.inf, area * step, (ox, oy, oz)))
    return out


def _rows(tris: list[Tri], pitch: float):
    """Solid x-intervals of rays along +x on a (z, y) grid of row centres. Winding rule, so overlapping shells union."""
    xs = [p[0] for t in tris for p in t]
    ys = [p[1] for t in tris for p in t]
    zs = [p[2] for t in tris for p in t]
    y0, z0 = min(ys), min(zs)
    ny = max(1, math.ceil((max(ys) - y0) / pitch))
    nz = max(1, math.ceil((max(zs) - z0) / pitch))
    jit = 0.0137 * pitch  # keeps row centres off shared mesh edges
    cross: dict = defaultdict(list)
    for t in tris:
        n, _ = _normal(t)
        (ax, ay, az), (bx, by, bz), (cx, cy, cz) = t
        det = (by - ay) * (cz - az) - (cy - ay) * (bz - az)
        if abs(n[0]) < 1e-12 or abs(det) < 1e-15:
            continue
        j0 = max(0, math.ceil((min(ay, by, cy) - y0 - jit) / pitch - 0.5))
        j1 = min(ny - 1, math.floor((max(ay, by, cy) - y0 - jit) / pitch - 0.5))
        k0 = max(0, math.ceil((min(az, bz, cz) - z0 - jit) / pitch - 0.5))
        k1 = min(nz - 1, math.floor((max(az, bz, cz) - z0 - jit) / pitch - 0.5))
        w = 1 if n[0] < 0 else -1  # a +x ray enters through faces looking at -x
        for k in range(k0, k1 + 1):
            z = z0 + (k + 0.5) * pitch + jit
            for j in range(j0, j1 + 1):
                y = y0 + (j + 0.5) * pitch + jit
                u = ((y - ay) * (cz - az) - (cy - ay) * (z - az)) / det
                v = ((by - ay) * (z - az) - (y - ay) * (bz - az)) / det
                if u < 0 or v < 0 or u + v > 1:
                    continue
                cross[(k, j)].append((ax + u * (bx - ax) + v * (cx - ax), w))
    rows: dict = {}
    for key, c in cross.items():
        c.sort()
        ivs, wind, start = [], 0, 0.0
        for x, w in c:
            if wind <= 0 < wind + w:
                start = x
            elif wind > 0 >= wind + w and x > start:
                ivs.append((start, x))
            wind += w
        if ivs:
            rows[key] = ivs
    return rows, ny, nz, (min(xs) - pitch, max(xs) + pitch, y0, z0)


class _DSU:
    def __init__(self):
        self.p: list[int] = [0]  # node 0 = outside / sentinel

    def add(self) -> int:
        self.p.append(len(self.p))
        return len(self.p) - 1

    def find(self, a: int) -> int:
        p = self.p
        while p[a] != a:
            p[a] = p[p[a]]
            a = p[a]
        return a

    def union(self, a: int, b: int) -> None:
        a, b = self.find(a), self.find(b)
        if a != b:
            if b == 0:
                a, b = b, a
            self.p[b] = a  # node 0 always stays a root


def _overlaps(a: list, b: list):
    """Index pairs of overlapping intervals in two sorted interval lists."""
    i = j = 0
    while i < len(a) and j < len(b):
        if a[i][0] < b[j][1] and b[j][0] < a[i][1]:
            yield i, j
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1


def _voxel_analysis(tris: list[Tri], pitch: float, on_plate: bool, wire_table: list[list[float]]) -> dict:
    rows, ny, nz, (xlo, xhi, y0, z0) = _rows(tris, pitch)
    zc = lambda k: z0 + (k + 0.5) * pitch
    yc = lambda j: y0 + (j + 0.5) * pitch
    empty: dict = {}
    for k in range(nz):
        for j in range(ny):
            gaps, x = [], xlo
            for a, b in rows.get((k, j), []):
                if a > x:
                    gaps.append((x, a))
                x = max(x, b)
            gaps.append((x, xhi))
            empty[(k, j)] = gaps
    # Empty space, layer by layer from the plate: a region not connected to the vat through layers <= k is sealed by
    # the film at layer k (suction cup). After printing, below and above are open too (enclosed voids).
    dsu, eid, cups = _DSU(), {}, {}
    for k in range(nz):
        for j in range(ny):
            gaps = empty[(k, j)]
            ids = [dsu.add() for _ in gaps]
            eid[(k, j)] = ids
            for n in range(len(gaps)):
                if n in (0, len(gaps) - 1) or j in (0, ny - 1) or (k == 0 and not on_plate):
                    dsu.union(0, ids[n])
            if j:
                for p, q in _overlaps(gaps, empty[(k, j - 1)]):
                    dsu.union(ids[p], eid[(k, j - 1)][q])
            if k:
                for p, q in _overlaps(gaps, empty[(k - 1, j)]):
                    dsu.union(ids[p], eid[(k - 1, j)][q])
        sealed: dict = {}
        for j in range(ny):
            for n, (a, b) in enumerate(empty[(k, j)]):
                r = dsu.find(eid[(k, j)][n])
                if r:
                    s = sealed.setdefault(r, [0.0, a, b, yc(j)])
                    s[0] += (b - a) * pitch
        for r, (area, a, b, y) in sealed.items():
            c = cups.setdefault(r, {"layers": 0, "max_area_mm2": 0.0, "z_mm": [zc(k), zc(k)], "xy_mm": None})
            c["layers"] += 1
            c["z_mm"][1] = zc(k)
            if area > c["max_area_mm2"]:
                c["max_area_mm2"], c["xy_mm"] = area, [round((a + b) / 2, 2), round(y, 2)]
    groups: dict = {}
    for r, c in cups.items():  # cup records whose regions joined later are one cavity
        g = groups.setdefault(dsu.find(r), {"layers": 0, "max_area_mm2": 0.0, "z_mm": [math.inf, -math.inf], "xy_mm": None})
        g["layers"] += c["layers"]
        g["z_mm"] = [min(g["z_mm"][0], c["z_mm"][0]), max(g["z_mm"][1], c["z_mm"][1])]
        if c["max_area_mm2"] > g["max_area_mm2"]:
            g["max_area_mm2"], g["xy_mm"] = c["max_area_mm2"], c["xy_mm"]
    for j in range(ny):
        for k in (0, nz - 1):
            for e in eid[(k, j)]:
                dsu.union(0, e)
    voids: dict = defaultdict(float)
    for key, gaps in empty.items():
        for n, (a, b) in enumerate(gaps):
            r = dsu.find(eid[key][n])
            if r:
                voids[r] += (b - a) * pitch * pitch
    # Solid per layer: area, islands (nothing below) and thin vertical wires (compact small sections stacked up).
    areas, islands, wires = [], [], []
    prev_comp: dict = {}   # (j, interval index) in the previous layer -> component root
    prev_info: dict = {}   # previous layer component root -> wire chain or None
    for k in range(nz):
        sd = _DSU()
        sid = {}
        for j in range(ny):
            ivs = rows.get((k, j), [])
            sid[j] = [sd.add() for _ in ivs]
            if j:
                for p, q in _overlaps(ivs, rows.get((k, j - 1), [])):
                    sd.union(sid[j][p], sid[j - 1][q])
        comps: dict = {}
        for j in range(ny):
            for n, (a, b) in enumerate(rows.get((k, j), [])):
                c = comps.setdefault(sd.find(sid[j][n]), {"area": 0.0, "x": [a, b], "y": [yc(j), yc(j)], "below": set()})
                c["area"] += (b - a) * pitch
                c["x"] = [min(c["x"][0], a), max(c["x"][1], b)]
                c["y"] = [min(c["y"][0], yc(j)), max(c["y"][1], yc(j))]
                for _, q in _overlaps([(a, b)], rows.get((k - 1, j), [])):
                    c["below"].add(prev_comp[(j, q)])
        areas.append(sum(c["area"] for c in comps.values()))
        info, fed = {}, set()
        for r, c in comps.items():
            xy = [round((c["x"][0] + c["x"][1]) / 2, 2), round((c["y"][0] + c["y"][1]) / 2, 2)]
            if k and not c["below"]:
                islands.append({"z_mm": round(zc(k), 3), "xy_mm": xy, "area_mm2": round(c["area"], 3)})
            d = 2 * math.sqrt(c["area"] / math.pi)
            extent = max(c["x"][1] - c["x"][0], c["y"][1] - c["y"][0] + pitch)
            info[r] = None
            if d < wire_table[-1][0] and extent <= 1.5 * max(d, pitch):  # round-ish section, not the edge of a tilted plate
                below = [prev_info[b] for b in c["below"] if prev_info.get(b)]
                fed |= {b for b in c["below"] if prev_info.get(b)}
                longest = max(below, key=lambda b: b["len"], default={"len": 0.0, "dsum": 0.0, "dmin": d})
                info[r] = {"len": pitch + longest["len"], "dsum": d * pitch + longest["dsum"], "dmin": min(d, longest["dmin"]),
                           "xy": xy, "z_top": zc(k)}
        wires += [w for b, w in prev_info.items() if w and b not in fed]
        prev_info = info
        prev_comp = {(j, n): sd.find(sid[j][n]) for j in range(ny) for n in range(len(rows.get((k, j), [])))}
    wires += [w for w in prev_info.values() if w]
    spikes = []
    for w in wires:
        d = w["dsum"] / w["len"]  # mean diameter: a taper is judged by its body, not its tip
        limit = wire_max_length(d, wire_table)
        if w["len"] >= 3 * pitch and w["len"] > 2 * d and w["len"] > limit:  # 1-2 layer slivers are raster noise
            spikes.append({"xy_mm": w["xy"], "top_z_mm": round(w["z_top"], 3), "length_mm": round(w["len"], 3),
                           "mean_diameter_mm": round(d, 3), "min_diameter_mm": round(w["dmin"], 3), "max_free_length_mm": round(limit, 3)})
    return {"pitch_mm": pitch, "layers": nz, "layer_area_mm2": areas, "islands": islands,
            "cups": [{**c, "max_area_mm2": round(c["max_area_mm2"], 3), "z_mm": [round(v, 3) for v in c["z_mm"]]} for c in groups.values()],
            "voids_mm3": sorted((round(v, 3) for v in voids.values()), reverse=True), "spikes": spikes}


def printability_check(tris: list[Tri], *, process: str = "msla_resin", rotate_deg: list[float] | None = None,
                       on_build_plate: bool = False, supports_planned: bool = True, expected_bodies: int = 1,
                       voxel_mm: float | None = None, density_g_cm3: float = 1.1, build_volume_mm: list[float] | None = None,
                       thin_area_tolerance_mm2: float = 0.5, max_cup_area_mm2: float = 10.0, max_rows: int = 120_000) -> dict:
    p = PROCESSES[process]
    tris = rotate(tris, *(rotate_deg or [0, 0, 0]))
    checks: list[dict] = []

    def check(name, value, ok, limit, basis, severity="fail"):
        checks.append({"name": name, "value": round(value, 4) if isinstance(value, float) else value, "pass": bool(ok),
                       "limit": limit, "severity": severity, "basis": basis})

    lo = [min(q[k] for t in tris for q in t) for k in range(3)]
    hi = [max(q[k] for t in tris for q in t) for k in range(3)]
    size = [hi[k] - lo[k] for k in range(3)]
    topo = _edges(tris)
    shells = analyze_shells(tris)
    volume = _signed_volume(tris)
    check("closed (watertight)", topo["open_edges"], topo["open_edges"] == 0, 0,
          "Every edge shared by exactly two triangles (trimesh is_watertight); slicers guess at open meshes.")
    check("manifold", topo["non_manifold_edges"], topo["non_manifold_edges"] == 0, 0,
          "No edge shared by more than two triangles (touching bodies, tangent booleans).")
    check("consistent winding", topo["inconsistent_winding_edges"], topo["inconsistent_winding_edges"] == 0, 0,
          "Neighbouring triangles must wind the same way so inside and outside are defined.", "warn")
    check("separate bodies", shells["outer_shell_count"], shells["outer_shell_count"] == expected_bodies, expected_bodies,
          "Disconnected shells print as separate pieces or loose islands; split them into parts or join them.")
    pitch = voxel_mm or max(p["layer_mm"], math.sqrt((size[1] + 1) * (size[2] + 1) / max_rows))
    vox = _voxel_analysis(tris, pitch, on_build_plate, p["wire_mm"])
    void_mm3 = sum(v for v in vox["voids_mm3"] if v > 2 * pitch ** 3)
    check("no enclosed voids", round(void_mm3, 3), shells["internal_cavity_count"] == 0 and void_mm3 == 0, 0,
          f"Sealed cavities trap uncured resin that later cracks the part: add a drain hole (>= {p['min_drain_hole_mm']} mm, "
          "Formlabs) and a vent, as PrusaSlicer SLA hollowing does.")
    thick = _thickness(tris, probe=p["min_wall_unsupported_mm"] * 1.5)
    thin_s = sum(a for t, a, _ in thick if t < p["min_wall_supported_mm"])
    thin_u = sum(a for t, a, _ in thick if t < p["min_wall_unsupported_mm"])
    spots: dict = {}
    for t, a, q in thick:
        if t < p["min_wall_unsupported_mm"]:
            s = spots.setdefault(tuple(round(v) for v in q), {"at_mm": [round(v, 2) for v in q], "min_mm": t, "area_mm2": 0.0})
            s["area_mm2"] += a
            if t < s["min_mm"]:
                s["min_mm"], s["at_mm"] = t, [round(v, 2) for v in q]
    finite = [t for t, _, _ in thick if t < math.inf]
    check("minimum wall (supported)", thin_s, thin_s <= thin_area_tolerance_mm2,
          f"<= {thin_area_tolerance_mm2} mm2 thinner than {p['min_wall_supported_mm']} mm",
          "Formlabs: walls connected on two or more sides >= 0.4 mm; thinner walls warp on peel. Small areas at tips and edges are tolerated.")
    check("minimum wall (unsupported)", thin_u, thin_u <= thin_area_tolerance_mm2,
          f"<= {thin_area_tolerance_mm2} mm2 thinner than {p['min_wall_unsupported_mm']} mm",
          "Formlabs: walls connected on fewer than two sides >= 0.6 mm. Thin spots listed here are fine only if braced on two sides.", "warn")
    check("thin spikes / wires", len(vox["spikes"]), not vox["spikes"], "none longer than the process wire table allows",
          "Formlabs: a wire (length > 2 x width) holds 0.3 mm up to 7 mm tall and 1.5 mm up to 30 mm; longer ones wave or snap. "
          "Detected along the print z axis only; horizontal pins show up as thin walls.")
    max_cup = max((c["max_area_mm2"] for c in vox["cups"]), default=0.0)
    check("suction cups", round(max_cup, 3), max_cup <= max_cup_area_mm2, f"<= {max_cup_area_mm2} mm2 sealed layer area",
          "A cavity the vat film seals while it peels (Lychee/CHITUBOX 'suction cup') pulls the part off its supports or "
          "deforms it. Vent it sideways or toward the plate, or re-orient. The default limit is a heuristic; 0 flags every cup.")
    overhang: dict = defaultdict(float)
    support_area = 0.0
    for t in tris:
        (_, _, nz), a = _normal(t)
        if nz >= 0 or not a or (on_build_plate and max(q[2] for q in t) < 1e-3):
            continue  # upward, degenerate, or resting on the plate
        ang = math.degrees(math.acos(min(1.0, -nz)))
        if ang < 60:
            overhang[int(ang // 10) * 10] += a
        if ang < p["self_supporting_deg"]:
            support_area += a
    check("overhangs needing support", round(support_area, 2), supports_planned or support_area <= 1.0,
          f"downward faces flatter than {p['self_supporting_deg']} deg from level",
          "Formlabs: unsupported overhangs >= 19 deg from level (resin); the Prusa KB 'overhang threshold' is measured the same way "
          "(45 deg is the usual FDM rule).", "warn" if supports_planned else "fail")
    check("islands", len(vox["islands"]), supports_planned or not vox["islands"], 0,
          "A layer region with nothing under it (CHITUBOX/Lychee island detection) needs a support there.",
          "warn" if supports_planned else "fail")
    if build_volume_mm:
        check("fits build volume", [round(s, 2) for s in size], all(size[k] <= build_volume_mm[k] for k in range(3)), build_volume_mm,
              "Bounding box in print orientation vs the printer build volume.")
    areas = vox["layer_area_mm2"]
    kmax = max(range(len(areas)), key=areas.__getitem__)
    return {
        "process": process, "basis": p["basis"], "rotate_deg": rotate_deg or [0, 0, 0], "on_build_plate": on_build_plate,
        "bounding_box_mm": {"min": [round(v, 3) for v in lo], "max": [round(v, 3) for v in hi], "size": [round(v, 3) for v in size]},
        "volume_mm3": round(volume, 2), "resin_ml": round(volume / 1000, 3), "mass_g": round(volume / 1000 * density_g_cm3, 3),
        "density_g_cm3": density_g_cm3, "topology": topo,
        "shells": {k: shells[k] for k in ("shell_count", "outer_shell_count", "internal_cavity_count", "inverted_shell_count")},
        "thickness": {"min_mm": round(min(finite), 3) if finite else f"> {round(p['min_wall_unsupported_mm'] * 1.5, 3)}",
                      "area_below_supported_mm2": round(thin_s, 3), "area_below_unsupported_mm2": round(thin_u, 3),
                      "thin_spots": sorted(({**s, "min_mm": round(s["min_mm"], 3), "area_mm2": round(s["area_mm2"], 3)} for s in spots.values()),
                                           key=lambda s: s["min_mm"])[:12], "samples": len(thick)},
        "overhang_area_mm2_by_angle_from_level": {f"{k}-{k + 10} deg": round(v, 2) for k, v in sorted(overhang.items())},
        "support_area_mm2": round(support_area, 2),
        "islands": {"count": len(vox["islands"]), "first": vox["islands"][:20]},
        "suction_cups": sorted(vox["cups"], key=lambda c: -c["max_area_mm2"])[:12],
        "enclosed_void_mm3": vox["voids_mm3"][:12], "spikes": vox["spikes"][:12],
        "largest_layer": {"area_mm2": round(areas[kmax], 2), "z_mm": round((kmax + 0.5) * pitch, 3)},
        "voxel_mm": round(pitch, 4), "checks": checks,
        "all_checks_pass": all(c["pass"] for c in checks if c["severity"] == "fail"),
        "warnings": [c["name"] for c in checks if not c["pass"] and c["severity"] == "warn"],
        "method": "Edge topology after welding to 0.1 um; shells and cavities by edge-connected groups with ray parity; wall "
                  "thickness by inward-normal ray casting from triangle centroids; islands, suction cups, voids and wires on a "
                  "ray-cast voxel model (rows along x, exact in x, voxel_mm in y and z). z up = away from the build plate.",
        "estimate_kind": "HEURISTIC printability screen, not a slicer: confirm supports and suction in your slicer "
                         "(Lychee, CHITUBOX, PrusaSlicer) and print a test piece.",
    }


# ---------- orientation ----------

def _layer_areas(tris: list[Tri], heights: list[float]) -> list[float]:
    """Cross-section area of a closed outward mesh at evenly spaced heights (shoelace over the cut segments)."""
    areas = [0.0] * len(heights)
    h0, dh = heights[0], (heights[1] - heights[0]) if len(heights) > 1 else 1.0
    for t in tris:
        zs = (t[0][2], t[1][2], t[2][2])
        k0 = max(0, math.ceil((min(zs) - h0) / dh))
        k1 = min(len(heights) - 1, math.floor((max(zs) - h0) / dh))
        if k1 < k0:
            continue
        (nx, ny, _), _ = _normal(t)
        for k in range(k0, k1 + 1):
            h = heights[k]
            pts = []
            for i in range(3):
                p, q = t[i], t[(i + 1) % 3]
                if (p[2] - h) * (q[2] - h) < 0:
                    s = (h - p[2]) / (q[2] - p[2])
                    pts.append((p[0] + s * (q[0] - p[0]), p[1] + s * (q[1] - p[1])))
            if len(pts) != 2:
                continue
            (ax, ay), (bx, by) = pts
            if (bx - ax) * -ny + (by - ay) * nx < 0:  # run along ez x n so the section winds counter-clockwise
                ax, ay, bx, by = bx, by, ax, ay
            areas[k] += (ax * by - ay * bx) / 2
    return areas


def support_plan(tris: list[Tri], *, process: str = "msla_resin", tilt_step_deg: float = 15.0, tilt_max_deg: float = 45.0,
                 slices: int = 80) -> dict:
    p = PROCESSES[process]
    key = lambda q: (round(q[0], 4), round(q[1], 4), round(q[2], 4))
    idx: dict = {}
    faces = [[idx.setdefault(key(q), len(idx)) for q in t] for t in tris]
    verts: list = [None] * len(idx)
    nbr: dict = defaultdict(set)
    for t, f in zip(tris, faces):
        for q, i in zip(t, f):
            verts[i] = q
        a, b, c = f
        nbr[a] |= {b, c}
        nbr[b] |= {a, c}
        nbr[c] |= {a, b}
    n = int(tilt_max_deg // tilt_step_deg)
    angles = [i * tilt_step_deg for i in range(-n, n + 1)]
    rows = []
    for rx in angles:
        for ry in angles:
            r = rotate(tris, rx, ry)
            top = max(q[2] for t in r for q in t)
            areas = _layer_areas(r, [top * (i + 0.5) / slices for i in range(slices)])
            support = 0.0
            for t in r:
                (_, _, nz), a = _normal(t)
                if nz < 0 and math.degrees(math.acos(min(1.0, -nz))) < p["self_supporting_deg"]:
                    support += a
            m = _mul(_mat(1, ry), _mat(0, rx))[2]
            vz = [m[0] * q[0] + m[1] * q[1] + m[2] * q[2] for q in verts]
            minima = sum(1 for i, z in enumerate(vz) if nbr[i] and all(vz[j] > z + 1e-9 for j in nbr[i]))
            rows.append({"rotate_deg": [rx, ry, 0.0], "height_mm": round(top, 2), "max_layer_area_mm2": round(max(areas), 2),
                         "support_area_mm2": round(support, 2), "support_start_points": minima})
    base = next(r for r in rows if r["rotate_deg"] == [0.0, 0.0, 0.0])
    ref = lambda k: base[k] or 1.0
    for row in rows:
        row["score"] = round(row["max_layer_area_mm2"] / ref("max_layer_area_mm2") + 0.5 * row["support_area_mm2"] / ref("support_area_mm2")
                             + 0.25 * row["support_start_points"] / ref("support_start_points"), 4)
    ranked = sorted(rows, key=lambda r: r["score"])
    return {"process": process, "as_modelled": base, "best": ranked[:5], "orientations_sampled": len(rows),
            "score": "max layer area / as-modelled + 0.5 x support area / as-modelled + 0.25 x support starts / as-modelled (lower is better)",
            "notes": ["The largest layer area drives MSLA peel force; tilting usually trades it for more supports and height.",
                      "Pass the chosen rotate_deg to miniature_printability_check to see islands and suction cups in that pose.",
                      "Keep painted show faces and fine detail away from supports; supports scar the surface they touch."],
            "estimate_kind": "HEURISTIC orientation screen; it does not place supports or simulate peel forces."}


# ---------- bases, magnets, pegs ----------

def base_and_peg(*, base: str = "40", magnet: str | None = "5x2", process: str = "msla_resin", base_height_mm: float = 4.0,
                 magnet_fit: str = "glue", peg_diameter_mm: float | None = None, peg_fit: str = "glue",
                 host_section_mm: float | None = None) -> dict:
    p = PROCESSES[process]
    checks = []

    def check(name, value, ok, limit, basis):
        checks.append({"name": name, "value": round(value, 3), "pass": bool(ok), "limit": limit, "basis": basis})

    b = base.lower().replace(" ", "").replace("mm", "")
    if "x" in b:
        major, minor = (float(v) for v in b.split("x"))
        footprint = {"shape": "oval", "major_mm": major, "minor_mm": minor, "standard": [major, minor] in OVAL_BASES_MM}
        smallest = minor
    else:
        smallest = float(b)
        footprint = {"shape": "round", "diameter_mm": smallest, "standard": smallest in ROUND_BASES_MM}
    out = {"process": process, "base": {**footprint, "height_mm": base_height_mm,
           "note": "Standard sizes follow the Games Workshop base range (round 25-160 mm, oval 60x35-170x105) that most "
                   "tabletop games use; ~3-4.5 mm tall with a bevelled rim. Measure a commercial base if the game cares."}}
    if magnet:
        md, mh = MAGNETS_MM[magnet] if magnet in MAGNETS_MM else [float(v) for v in magnet.lower().split("x")]
        c = p["fit_clearance_per_side_mm"][magnet_fit]
        pocket = {"magnet_mm": [md, mh], "pocket_diameter_mm": round(md + 2 * c, 3), "pocket_depth_mm": round(mh + c, 3),
                  "fit": magnet_fit, "opens": "underside", "entry_chamfer_mm": 0.2,
                  "note": "Neodymium discs are typically +/-0.05-0.1 mm; resin holes print slightly small. Glue fit takes a drop "
                          "of CA; a press fit can crack thin resin. Mark polarity before gluing a set."}
        floor = base_height_mm - pocket["pocket_depth_mm"]
        pocket["floor_above_pocket_mm"] = round(floor, 3)
        check("magnet pocket floor", floor, floor >= p["min_wall_supported_mm"], p["min_wall_supported_mm"],
              "Material above the pocket; a thinner floor holds harder to steel but must still print (supported wall).")
        rim = (smallest - pocket["pocket_diameter_mm"]) / 2
        check("rim around the magnet pocket", rim, rim >= 2, 2, "Leave >= 2 mm of base around the pocket.")
        out["magnet_pocket"] = pocket
    if peg_diameter_mm:
        c = p["fit_clearance_per_side_mm"][peg_fit]
        length = max(2.0, 1.25 * peg_diameter_mm)
        socket = peg_diameter_mm + 2 * c
        out["peg"] = {"diameter_mm": peg_diameter_mm, "length_mm": round(length, 2), "tip_chamfer_mm": round(min(0.3, 0.1 * peg_diameter_mm + 0.05), 2),
                      "key": {"type": "D-flat", "flat_at_mm_from_axis": round(0.3 * peg_diameter_mm, 3),
                              "why": "a flat (or a second peg) lets the joint go together one way only"},
                      "socket_diameter_mm": round(socket, 3), "socket_flat_at_mm_from_axis": round(0.3 * peg_diameter_mm + c, 3),
                      "socket_depth_mm": round(length + 0.2, 2), "fit": peg_fit, "clearance_per_side_mm": c}
        limit = wire_max_length(peg_diameter_mm, p["wire_mm"])
        check("peg prints as a free-standing pin", length, length <= limit, round(limit, 2),
              "Formlabs wire table (0.3 mm/7 mm to 1.5 mm/30 mm): stubby pegs survive printing, washing and assembly.")
        if host_section_mm:
            wall = (host_section_mm - socket) / 2
            check("wall around the socket", wall, wall >= p["min_wall_unsupported_mm"], p["min_wall_unsupported_mm"],
                  "Material left around the socket in the receiving part (unsupported-wall limit).")
    out.update(checks=checks, all_checks_pass=all(c["pass"] for c in checks), clearances_basis=p["basis"],
               catalog={"round_mm": ROUND_BASES_MM, "oval_mm": OVAL_BASES_MM, "magnets_mm": MAGNETS_MM},
               estimate_kind="HEURISTIC fits: print a stepped tolerance coupon (pockets and sockets in 0.05 mm steps) first.")
    return out


def create_miniature_server(readable=None) -> FastMCP:
    """readable(path) -> Path confines STL reads (airframe output_root/read_roots); None disables STL file access."""
    import asyncio
    from fastmcp.exceptions import ToolError
    from .flotation import read_stl

    mcp = FastMCP("miniature")

    def load(path: str) -> list[Tri]:
        if readable is None:
            raise ToolError("STL tools need the airframe section (output_root/read_roots) in the operator config")
        p = readable(path)
        if p.suffix.lower() != ".stl":
            raise ToolError("expects an .stl mesh in mm")
        try:
            tris = read_stl(p)
        except ValueError as error:
            raise ToolError(str(error)) from None
        if not tris or len(tris) > 300_000:
            raise ToolError("Mesh empty or too large for the pure-Python checker (> 300k triangles); export a coarser STL")
        return tris

    @mcp.tool(name="scale_convert", annotations=READ_ONLY)
    def scale_convert_tool(
        scale: Annotated[str, Field(description="'1:35', '1:48', '1:72', '1:100', '1:144', '1:285' (any 1:N) or a figure scale like '28mm' / '32mm'")],
        features: Annotated[list[Feature], Field(min_length=1, max_length=200)],
        process: Process = "msla_resin",
        convention: Annotated[Literal["eye", "crown"], Field(description="Figure scales: height measured to the eyes or to the crown")] = "eye",
        stature_mm: Annotated[float, Field(ge=500, le=3000, allow_inf_nan=False)] = 1780.0,
    ) -> dict:
        """Convert real sizes to model sizes (or back) for a named scale and flag features below the process limits (walls,
        wires, holes, embossed/engraved detail, gaps) with the exaggeration needed to print them. Figure scales (28/32 mm
        heroic) become 1:N by eye or crown height. HEURISTIC; resin limits from the Formlabs Design Guide."""
        try:
            return scale_convert(scale, features, process, convention, stature_mm)
        except ValueError as error:
            raise ToolError(str(error)) from None

    @mcp.tool(name="printability_check", annotations=READ_ONLY)
    async def printability_check_tool(
        stl_path: Annotated[str, Field(description="One part as a closed STL in mm, inside output_root/read_roots")],
        process: Process = "msla_resin",
        rotate_deg: Annotated[list[Annotated[float, Field(ge=-360, le=360, allow_inf_nan=False)]] | None,
                              Field(min_length=3, max_length=3, description="Print orientation: rotate about x, then y, then z (deg); z up = away from the plate")] = None,
        on_build_plate: Annotated[bool, Field(description="True if the part sits directly on the plate (no supports or raft)")] = False,
        supports_planned: bool = True,
        expected_bodies: Annotated[int, Field(ge=1, le=100)] = 1,
        voxel_mm: Annotated[float | None, Field(ge=0.02, le=5, allow_inf_nan=False)] = None,
        density_g_cm3: Annotated[float, Field(gt=0.5, le=3, allow_inf_nan=False)] = 1.1,
        build_volume_mm: Annotated[list[Pos] | None, Field(min_length=3, max_length=3)] = None,
        thin_area_tolerance_mm2: Annotated[float, Field(ge=0, le=1000, allow_inf_nan=False)] = 0.5,
        max_cup_area_mm2: Annotated[float, Field(ge=0, le=100000, allow_inf_nan=False)] = 10.0,
    ) -> dict:
        """HEURISTIC printability screen of one miniature part for MSLA resin or 0.2 mm-nozzle FDM: watertight/manifold,
        separate bodies, enclosed voids (need drain + vent), wall thickness by ray casting (resin: supported 0.4 /
        unsupported 0.6 mm), thin spikes vs the Formlabs wire table, overhang area by angle, islands, suction cups sealed
        by the vat film, volume and resin mass, build-volume fit. Each check carries its basis; confirm in a slicer."""
        tris = load(stl_path)
        return await asyncio.to_thread(printability_check, tris, process=process, rotate_deg=rotate_deg, on_build_plate=on_build_plate,
                                       supports_planned=supports_planned, expected_bodies=expected_bodies, voxel_mm=voxel_mm,
                                       density_g_cm3=density_g_cm3, build_volume_mm=build_volume_mm,
                                       thin_area_tolerance_mm2=thin_area_tolerance_mm2, max_cup_area_mm2=max_cup_area_mm2)

    @mcp.tool(name="base_and_peg", annotations=READ_ONLY)
    def base_and_peg_tool(
        base: Annotated[str, Field(description="Round diameter '25'/'32'/'40'/'50'/'60' or oval '60x35'/'75x42'/'90x52'/'105x70' (mm)")] = "40",
        magnet: Annotated[str | None, Field(description="Disc magnet 'DxH' in mm, e.g. '3x1', '5x2', '6x2'; null for none")] = "5x2",
        process: Process = "msla_resin",
        base_height_mm: Annotated[float, Field(gt=1, le=20, allow_inf_nan=False)] = 4.0,
        magnet_fit: Literal["press", "glue", "slip"] = "glue",
        peg_diameter_mm: Annotated[float | None, Field(gt=0.3, le=20, allow_inf_nan=False)] = None,
        peg_fit: Literal["press", "glue", "slip"] = "glue",
        host_section_mm: Annotated[float | None, Field(gt=0, le=500, allow_inf_nan=False)] = None,
    ) -> dict:
        """Base footprint (standard round/oval sizes), magnet pocket size and floor, and a keyed (D-flat) peg and socket for
        multi-part kits with per-side clearance for the process and fit (press/glue/slip). HEURISTIC clearances: print a
        tolerance coupon first."""
        try:
            return base_and_peg(base=base, magnet=magnet, process=process, base_height_mm=base_height_mm, magnet_fit=magnet_fit,
                                peg_diameter_mm=peg_diameter_mm, peg_fit=peg_fit, host_section_mm=host_section_mm)
        except (ValueError, KeyError) as error:
            raise ToolError(f"bad base or magnet size: {error}") from None

    @mcp.tool(name="support_plan", annotations=READ_ONLY)
    async def support_plan_tool(
        stl_path: Annotated[str, Field(description="One part as a closed STL in mm, inside output_root/read_roots")],
        process: Process = "msla_resin",
        tilt_step_deg: Annotated[float, Field(ge=5, le=45, allow_inf_nan=False)] = 15.0,
        tilt_max_deg: Annotated[float, Field(ge=0, le=90, allow_inf_nan=False)] = 45.0,
    ) -> dict:
        """HEURISTIC print-orientation screen: samples rotations about x and y and ranks them by the largest layer area
        (MSLA peel and suction force), support area (faces flatter than the self-supporting angle) and support-start
        points (local minima). Returns rotate_deg for miniature_printability_check. Does not place supports."""
        tris = load(stl_path)
        return await asyncio.to_thread(support_plan, tris, process=process, tilt_step_deg=tilt_step_deg, tilt_max_deg=tilt_max_deg)

    from .miniature_more import register  # terrain, detail, hollowing, splitting, bases, buildings, plate packing
    register(mcp, load)
    from .miniature_gaps import register as register_gaps  # narrow air gaps between facing surfaces, through-loop count
    register_gaps(mcp, load)
    from .miniature_figure import register as register_figure  # per-feature thickness of blades, hafts, staffs, bows
    register_figure(mcp, load)
    from .miniature_scene import register as register_scene  # scene bases: several parts, sockets and a magnet on one base
    register_scene(mcp, load)
    return mcp
