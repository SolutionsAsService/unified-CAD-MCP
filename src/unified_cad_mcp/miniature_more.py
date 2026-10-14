"""More miniature tools, driven by building the fantasy-miniatures line (tower, well, scatter, figure).

Registered into the "miniature" namespace by create_miniature_server (so they appear as miniature_<name>):
- terrain_grid_check: snap a terrain footprint to a 1 in / 25 mm / 2 in grid and check magnet or OpenLOCK-style clip connectors.
- detail_advice: scale-aware detail exaggeration: smallest feature that reads at table distance and prints, per feature class.
- hollow_plan: resin hollowing advice for one STL: hollow or not, shell, resin saved, drain/vent hole positions.
- split_plan: how to cut an oversize part for a named printer, and the peg / key / clearance for each joint.
- base_catalog: extra bases (hex, square, rectangular, scenic) and which base fits a footprint.
- floor_clearance_check: storey, door and drop-in-floor clearances for buildings with removable floors and roofs.
- plate_pack: how many parts fit on one build plate of a named printer, and how many plates a batch needs.

All HEURISTIC and pure Python. Sources:
- Process limits and fit clearances: PROCESSES in miniature.py (Formlabs Design Guide for resin; 0.2 mm FDM heuristic).
- Visual acuity ~1 arc-minute (Snellen 20/20 definition): smallest readable detail = distance x tan(1/60 deg), about
  0.17 mm at 60 cm; painted detail needs roughly 2-3x that to hold a wash or a highlight (a hobby rule of thumb).
- Heroic-scale exaggeration multipliers (heads, hands, weapons): common sculpting rules of thumb, not a standard.
- Printer build volumes: manufacturer spec sheets (Elegoo, Anycubic, Prusa, Bambu Lab); check your model's sheet.
- Hollowing: Prusa Knowledge Base "Hollowing" (SLA: hollow, then add drain holes) and the Formlabs 3.5 mm drain hole.
- OpenLOCK is an open terrain clip system (Printable Scenery, 1 in grid, clip slots in a 6 mm base); exact clip slot
  dimensions vary by release, so this tool checks the base height and grid only and points at the published clip.
"""
from __future__ import annotations

import math
from typing import Annotated, Literal

from fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from .flotation import Tri, _signed_volume
from .miniature import MAGNETS_MM, OVAL_BASES_MM, PROCESSES, READ_ONLY, ROUND_BASES_MM, Process, rotate, scale_ratio

Pos = Annotated[float, Field(gt=0, allow_inf_nan=False)]
GRIDS_MM = {"1in": 25.4, "25mm": 25.0, "2in": 50.8, "50mm": 50.0}
PRINTERS = {  # usable build volume x, y, z in mm (manufacturer spec sheets)
    "elegoo_mars_5_ultra": {"process": "msla_resin", "mm": [153.36, 77.76, 165]},
    "elegoo_saturn_4_ultra": {"process": "msla_resin", "mm": [218.88, 122.88, 220]},
    "anycubic_photon_mono_m7_pro": {"process": "msla_resin", "mm": [223.78, 126.38, 230]},
    "prusa_sl1s": {"process": "msla_resin", "mm": [127, 80, 150]},
    "prusa_mk4": {"process": "fdm", "mm": [250, 210, 220]},
    "prusa_mini": {"process": "fdm", "mm": [180, 180, 180]},
    "bambu_a1": {"process": "fdm", "mm": [256, 256, 256]},
    "bambu_x1c": {"process": "fdm", "mm": [256, 256, 256]},
}
Printer = Literal[tuple(PRINTERS)]  # type: ignore[valid-type]
HEX_BASES_MM = [25, 32, 40, 50, 60, 76.2, 100]  # across flats
SQUARE_BASES_MM = [20, 25, 40, 50]
RECT_BASES_MM = [[25, 50], [50, 50], [50, 75], [50, 100], [75, 100], [100, 150]]
# detail classes: (heroic multiplier on true size, process limit key, note)
DETAIL = {
    "head": (1.15, None, "heroic heads run 10-25 % large so faces read"),
    "hand": (1.4, "min_wall_unsupported_mm", "hands and fingers 1.3-1.5x so they survive and read"),
    "weapon_haft": (2.0, "wire", "hafts, staffs and spears 1.5-2.5x true thickness; brace them twice"),
    "blade": (1.8, "min_wall_unsupported_mm", "blade thickness, not length, is exaggerated"),
    "eye_socket": (1.5, "min_engraved_mm", "deepen eye sockets so a wash sits in them"),
    "panel_line": (1.0, "min_engraved_mm", "engraved lines: at least the engraving limit and readable width"),
    "rivet": (1.5, "min_embossed_mm", "rivets/studs: proud enough to catch a drybrush"),
    "rope": (2.0, "wire", "ropes, chains and straps 2x+; give chains solid backing"),
    "plank_joint": (1.0, "min_engraved_mm", "plank/stone joints: readable width, 45 deg flanks for FDM"),
    "wall": (1.0, "min_wall_unsupported_mm", "free-standing walls: never below the unsupported wall limit"),
}


def _limit(process: str, key: str | None, length_mm: float = 10.0) -> float:
    p = PROCESSES[process]
    if key is None:
        return 0.0
    if key == "wire":  # diameter that can stand 'length_mm' unsupported
        (d0, l0), (d1, l1) = p["wire_mm"][0], p["wire_mm"][-1]
        if length_mm <= l0:
            return d0
        if length_mm <= l1:
            return d0 + (length_mm - l0) * (d1 - d0) / (l1 - l0)
        return d1 * length_mm / l1
    return p[key]


def terrain_grid_check(footprint_mm: list[float], grid: str = "1in", connector: str = "magnet", magnet: str | None = "5x2",
                       base_height_mm: float = 6.0, process: str = "fdm_0.2_nozzle", edge_wall_mm: float = 1.2) -> dict:
    g = GRIDS_MM[grid]
    cells = [max(1, round(v / g)) for v in footprint_mm]
    snapped = [round(c * g, 2) for c in cells]
    off = [round(s - v, 2) for s, v in zip(snapped, footprint_mm)]
    p = PROCESSES[process]
    checks = [{"name": "footprint within 2 mm of the grid", "value": max(abs(o) for o in off), "limit": 2.0, "pass": max(abs(o) for o in off) <= 2.0}]
    conns = []
    if connector == "magnet":
        md, mh = MAGNETS_MM[magnet or "5x2"]
        clr = p["fit_clearance_per_side_mm"]["press"]
        pocket = [round(md + 2 * clr, 2), round(mh + clr, 2)]
        for axis, n in (("x", cells[0]), ("y", cells[1])):
            for i in range(n):
                for side in (-1, 1):
                    pos = (i + 0.5) * g - snapped[0 if axis == "x" else 1] / 2
                    conns.append({"edge": f"{'-+'[side > 0]}{'y' if axis == 'x' else 'x'}", "at_mm": round(pos, 2)})
        checks += [
            {"name": "pocket floor under magnet", "value": round(base_height_mm - pocket[1], 2), "limit": p["min_wall_supported_mm"],
             "pass": base_height_mm - pocket[1] >= p["min_wall_supported_mm"]},
            {"name": "wall between pocket and tile edge", "value": edge_wall_mm, "limit": p["min_wall_unsupported_mm"],
             "pass": edge_wall_mm >= p["min_wall_unsupported_mm"]},
        ]
        detail = {"magnet": magnet, "pocket_mm": pocket, "pocket_centre_from_edge_mm": round(edge_wall_mm + pocket[0] / 2, 2),
                  "polarity": "alternate N/S along each edge (or mark one face) so any two tiles attract"}
    elif connector == "openlock":
        checks.append({"name": "base height for OpenLOCK clip slots", "value": base_height_mm, "limit": 6.0, "pass": base_height_mm >= 6.0})
        for axis, n in (("x", cells[0]), ("y", cells[1])):
            for i in range(n):
                conns += [{"edge": f"{s}{'y' if axis == 'x' else 'x'}", "at_mm": round((i + 0.5) * g - snapped[0 if axis == 'x' else 1] / 2, 2)} for s in "-+"]
        detail = {"note": "one clip slot per 1 in of edge, centred on each grid cell; take the slot from Printable Scenery's "
                          "published OpenLOCK clip/socket files rather than redrawing it", "grid_required": "1in"}
        checks.append({"name": "OpenLOCK runs on the 1 in grid", "value": grid, "limit": "1in", "pass": grid == "1in"})
    else:
        detail = {}
    return {"grid": grid, "grid_mm": g, "cells": cells, "snapped_footprint_mm": snapped, "offset_mm": off, "connector": connector,
            "connector_detail": detail, "connectors": conns, "checks": checks, "all_checks_pass": all(c["pass"] for c in checks),
            "basis": "HEURISTIC; grid sizes exact, magnet press fit from PROCESSES, OpenLOCK base height 6 mm."}


def detail_advice(scale: str, process: str, features: list[dict], viewing_distance_mm: float = 600.0) -> dict:
    ratio = scale_ratio(scale)["ratio"]
    acuity = viewing_distance_mm * math.tan(math.radians(1 / 60))
    readable = round(2.5 * acuity, 3)
    rows = []
    for f in features:
        mult, key, note = DETAIL[f["kind"]]
        true_model = f["real_mm"] / ratio
        limit = _limit(process, key, f.get("length_mm", 10.0))
        floor = max(limit, readable if f["kind"] in ("panel_line", "plank_joint", "rivet", "eye_socket") else 0.0)
        rec = max(true_model * mult, floor)
        rows.append({"name": f.get("name", f["kind"]), "kind": f["kind"], "true_model_mm": round(true_model, 3),
                     "heroic_multiplier": mult, "process_limit_mm": round(limit, 3), "recommended_mm": round(rec, 3),
                     "exaggeration_vs_true": round(rec / true_model, 2) if true_model else None, "note": note})
    return {"scale": scale, "ratio": ratio, "process": process, "viewing_distance_mm": viewing_distance_mm,
            "acuity_limit_mm": round(acuity, 3), "readable_painted_detail_mm": readable, "features": rows,
            "basis": "HEURISTIC: 1 arc-minute acuity, x2.5 to hold paint; heroic multipliers are sculpting rules of thumb."}


def _bbox(tris: list[Tri]):
    lo = [min(q[k] for t in tris for q in t) for k in range(3)]
    hi = [max(q[k] for t in tris for q in t) for k in range(3)]
    return lo, hi


def hollow_plan(tris: list[Tri], process: str = "msla_resin", shell_mm: float | None = None, rotate_deg: list[float] | None = None) -> dict:
    if rotate_deg:
        tris = rotate(tris, *rotate_deg)
    p = PROCESSES[process]
    vol = abs(_signed_volume(tris)) / 1000.0  # ml
    lo, hi = _bbox(tris)
    size = [hi[k] - lo[k] for k in range(3)]
    area = sum(0.5 * math.dist((0, 0, 0), (
        (b[1] - a[1]) * (c[2] - a[2]) - (b[2] - a[2]) * (c[1] - a[1]),
        (b[2] - a[2]) * (c[0] - a[0]) - (b[0] - a[0]) * (c[2] - a[2]),
        (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]))) for a, b, c in tris)
    shell = shell_mm or (1.5 if min(size) < 25 else 2.0 if min(size) < 60 else 2.5)
    min_core = 2 * shell + p["min_drain_hole_mm"]
    shell_ml = min(vol, area * shell / 1000.0)
    saved = max(0.0, vol - shell_ml)
    hollow = process == "msla_resin" and min(size) >= min_core and saved >= 2.0 and saved / max(vol, 1e-9) >= 0.25
    reasons = []
    if process != "msla_resin":
        reasons.append("FDM: use infill instead of a hollow shell")
    if min(size) < min_core:
        reasons.append(f"thinnest dimension {min(size):.1f} mm < 2 x shell + drain hole ({min_core:.1f} mm): leave solid")
    if saved < 2.0:
        reasons.append(f"saves only {saved:.1f} ml: not worth the drain holes")
    holes = []
    if hollow:
        n = 2 if max(size) < 80 else 3
        verts = sorted({q for t in tris for q in t}, key=lambda q: q[2])
        zs = lo[2] + min(2.0, size[2] * 0.1)
        low = [q for q in verts if q[2] <= zs] or verts[:1]
        cx, cy = (lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2
        low.sort(key=lambda q: -math.hypot(q[0] - cx, q[1] - cy))
        picks = []
        for q in low:
            if all(math.dist(q[:2], s[:2]) > size[0] / 3 for s in picks):
                picks.append(q)
            if len(picks) == n - 1:
                break
        top = verts[-1]
        holes = [{"role": "drain (near the plate, resin runs out as the part lifts)", "at_mm": [round(v, 2) for v in q],
                  "diameter_mm": max(p["min_drain_hole_mm"], 3.5)} for q in picks]
        holes.append({"role": "vent (highest point, lets air in so the shell does not cup)", "at_mm": [round(v, 2) for v in top],
                      "diameter_mm": max(p["min_drain_hole_mm"], 3.5) - 1.0})
    return {"process": process, "volume_ml": round(vol, 2), "surface_mm2": round(area, 1), "size_mm": [round(v, 2) for v in size],
            "recommend_hollow": hollow, "shell_mm": shell, "shell_volume_ml": round(shell_ml, 2), "resin_saved_ml": round(saved if hollow else 0.0, 2),
            "holes": holes, "reasons": reasons or ["hollow: big enough, saves resin, and lowers peel force"],
            "basis": "HEURISTIC: shell volume ~ surface x shell; holes at the lowest outer points (print pose) plus a vent at the top. "
                     "Prusa KB 'Hollowing'; Formlabs drain hole 3.5 mm. Hollow in your slicer; this only plans it."}


def split_plan(size_mm: list[float], printer: str, process: str, fit: str = "glue", margin_mm: float = 5.0) -> dict:
    vol = PRINTERS[printer]["mm"]
    avail = [v - 2 * margin_mm for v in vol]
    order = sorted(range(3), key=lambda k: -size_mm[k])
    usable = sorted(avail, reverse=True)
    pieces = [max(1, math.ceil(size_mm[k] / usable[i])) for i, k in enumerate(order)]
    per_axis = [1, 1, 1]
    for i, k in enumerate(order):
        per_axis[k] = pieces[i]
    cuts, joints = [], []
    clr = PROCESSES[process]["fit_clearance_per_side_mm"][fit]
    for k, n in enumerate(per_axis):
        for j in range(1, n):
            at = round(size_mm[k] * j / n, 2)
            section = sorted(size_mm[i] for i in range(3) if i != k)
            d = round(min(6.0, max(1.5, 0.3 * section[0])), 1)
            npeg = 2 if section[1] >= 20 else 1
            joints.append({"cut_axis": "xyz"[k], "at_mm": at, "section_mm": section, "peg_diameter_mm": d, "pegs": npeg,
                           "keying": "two pegs, unequal diameters" if npeg == 2 else "one D-flat peg",
                           "peg_length_mm": round(max(2.0, 1.25 * d), 2), "socket_diameter_mm": round(d + 2 * clr, 2),
                           "socket_depth_mm": round(max(2.0, 1.25 * d) + 0.2, 2),
                           "hide_seam": "cut along a natural line (storey, roof eave, belt, plank) so the seam reads as detail"})
            cuts.append({"axis": "xyz"[k], "at_mm": at})
    fits = all(math.ceil(size_mm[k] / per_axis[k]) <= max(avail) for k in range(3))
    return {"printer": printer, "build_volume_mm": vol, "margin_mm": margin_mm, "pieces_per_axis": per_axis,
            "pieces": per_axis[0] * per_axis[1] * per_axis[2], "cuts": cuts, "joints": joints, "fit": fit,
            "clearance_per_side_mm": clr, "fits_after_split": fits,
            "basis": "HEURISTIC: axis-aligned cuts by longest side; peg ~0.3 x thinnest section (1.5-6 mm), length 1.25 d; "
                     "clearance from PROCESSES fit_clearance_per_side_mm."}


def base_catalog(footprint_mm: list[float] | None = None, margin_mm: float = 1.5, shapes: list[str] | None = None) -> dict:
    shapes = shapes or ["round", "oval", "hex", "square", "rect"]
    cat = {"round": [[d, d] for d in ROUND_BASES_MM], "oval": OVAL_BASES_MM, "hex": [[d, round(d * 2 / math.sqrt(3), 2)] for d in HEX_BASES_MM],
           "square": [[s, s] for s in SQUARE_BASES_MM], "rect": RECT_BASES_MM}
    out = {k: v for k, v in cat.items() if k in shapes}
    scenic = {"rubble": "scatter stones 0.5-2 mm proud; keep a flat 1-2 mm rim for the peg socket",
              "flagstone": "0.4 mm engraved joints (resin) / 0.3 mm (FDM 0.2), 45 deg flanks",
              "cobble": "sunken hemispheres 1.5-2.5 mm; leave a flat pad under the feet",
              "grass": "leave flat: static grass and tufts glue on after printing",
              "lip": "a 0.8-1.2 mm bevel/rim like commercial bases; magnet pocket vented to the rim when printed flat"}
    best = None
    if footprint_mm:
        fx, fy = sorted(footprint_mm, reverse=True)
        need = [fx + 2 * margin_mm, fy + 2 * margin_mm]
        fits = []
        for shape, sizes in out.items():
            for s in sizes:
                a, b = sorted(s, reverse=True)
                inner = [a, b] if shape not in ("round", "oval", "hex") else [a * 0.95, b * 0.95]  # curved edge eats a little
                if shape == "hex":
                    inner = [s[0], s[0]]  # across flats bounds both axes
                if inner[0] >= need[0] and inner[1] >= need[1]:
                    fits.append({"shape": shape, "size_mm": s, "area_mm2": round(a * b, 1)})
        fits.sort(key=lambda f: f["area_mm2"])
        best = fits[:5]
    return {"bases_mm": out, "hex_note": "hex sizes are across flats; [flats, corners]", "scenic_tops": scenic,
            "smallest_fits": best, "footprint_mm": footprint_mm, "margin_mm": margin_mm}


def floor_clearance_check(scale: str, storey_clear_mm: float, door_mm: list[float] | None = None, base_height_mm: float = 3.2,
                          figure_base_mm: float = 25.0, floor_fit_gap_mm: float | None = None, process: str = "fdm_0.2_nozzle",
                          lip_engagement_mm: float | None = None, hatch_mm: list[float] | None = None, opening_mm: float | None = None) -> dict:
    r = scale_ratio(scale)
    stature = 1780.0 / r["ratio"]  # crown height of a 1.78 m figure at this scale
    fig_h = stature * 1.1 + base_height_mm  # heroic heads/hats/raised weapons +10 %, standing on its base
    p = PROCESSES[process]
    checks = [{"name": "storey clear height vs figure on base", "value": round(storey_clear_mm, 2), "limit": round(fig_h + 3, 2),
               "pass": storey_clear_mm >= fig_h + 3}]
    if opening_mm is not None:
        checks.append({"name": "roof/floor opening fits a base plus fingers", "value": opening_mm, "limit": figure_base_mm + 15,
                       "pass": opening_mm >= figure_base_mm + 15})
    if door_mm:
        w, h = door_mm
        checks += [{"name": "door width vs base", "value": w, "limit": round(figure_base_mm * 0.7, 2), "pass": w >= figure_base_mm * 0.7},
                   {"name": "door height vs figure on base", "value": h, "limit": round(stature + base_height_mm, 2), "pass": h >= stature + base_height_mm}]
    if floor_fit_gap_mm is not None:
        lim = p["fit_clearance_per_side_mm"]["slip"]
        checks.append({"name": "drop-in floor gap per side (slip fit)", "value": floor_fit_gap_mm, "limit": lim, "pass": floor_fit_gap_mm >= lim})
    if lip_engagement_mm is not None:
        checks.append({"name": "storey lip engagement", "value": lip_engagement_mm, "limit": 2.0, "pass": lip_engagement_mm >= 2.0})
    if hatch_mm:
        checks.append({"name": "floor hatch fits a fingertip", "value": min(hatch_mm), "limit": 10.0, "pass": min(hatch_mm) >= 10.0})
    return {"scale": scale, "ratio": r["ratio"], "figure_crown_mm": round(stature, 2), "figure_on_base_heroic_mm": round(fig_h, 2),
            "checks": checks, "all_checks_pass": all(c["pass"] for c in checks),
            "basis": "HEURISTIC: crown height of a 1.78 m figure, +10 % heroic, + base, + 3 mm hand room; door width 0.7 x base; "
                     "slip clearance from PROCESSES; 2 mm lip so storeys do not slide; 10 mm hatch for a fingertip."}


def plate_pack(parts: list[dict], printer: str, gap_mm: float = 3.0, margin_mm: float = 5.0) -> dict:
    W, D, H = PRINTERS[printer]["mm"]
    W, D = W - 2 * margin_mm, D - 2 * margin_mm
    items, too_big = [], []
    for p in parts:
        x, y = sorted(p["footprint_mm"], reverse=True)
        if p.get("height_mm", 0) > H or (x > max(W, D) or y > min(W, D)):
            too_big.append(p["name"])
            continue
        items += [{"name": p["name"], "x": x, "y": y}] * int(p.get("qty", 1))
    items.sort(key=lambda i: -i["y"])
    plates, cur, shelves = [], [], []

    def place(it):
        for sh in shelves:  # shelf packing along the long side
            if it["y"] <= sh["h"] and sh["used"] + it["x"] <= W:
                sh["used"] += it["x"] + gap_mm
                return True
        top = sum(s["h"] + gap_mm for s in shelves)
        if top + it["y"] <= D and it["x"] <= W:
            shelves.append({"h": it["y"], "used": it["x"] + gap_mm})
            return True
        return False

    for it in items:
        if not place(it):
            plates.append(cur)
            cur, shelves[:] = [], []
            place(it)
        cur.append(it["name"])
    if cur:
        plates.append(cur)
    used = sum(i["x"] * i["y"] for i in items)
    counts = [{n: c.count(n) for n in dict.fromkeys(c)} for c in plates]
    return {"printer": printer, "usable_plate_mm": [W, D], "build_height_mm": H, "plates": len(plates), "per_plate": counts,
            "too_big": too_big, "plate_fill": round(used / max(1, len(plates) * W * D), 3),
            "basis": "HEURISTIC shelf packing of print-pose footprints with a gap; resin supports and tilt need extra room, "
                     "so treat this as an upper bound and leave the slicer to arrange."}


class DetailFeature(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal[tuple(DETAIL)]  # type: ignore[valid-type]
    real_mm: Pos
    name: str | None = Field(default=None, max_length=80)
    length_mm: Pos | None = Field(default=None, description="free length for wire-like features (hafts, ropes)")


class PackPart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(max_length=80)
    footprint_mm: Annotated[list[Pos], Field(min_length=2, max_length=2)]
    height_mm: Pos | None = None
    qty: Annotated[int, Field(ge=1, le=500)] = 1


def register(mcp: FastMCP, load) -> None:
    """Add the tools to the miniature server; load(path) -> triangles (already confined to the read roots)."""
    import asyncio

    @mcp.tool(name="terrain_grid_check", annotations=READ_ONLY)
    def terrain_grid_check_tool(
        footprint_mm: Annotated[list[Pos], Field(min_length=2, max_length=2, description="terrain tile/piece footprint [x, y] mm")],
        grid: Literal["1in", "25mm", "2in", "50mm"] = "1in",
        connector: Literal["magnet", "openlock", "none"] = "magnet",
        magnet: Literal[tuple(MAGNETS_MM)] | None = "5x2",  # type: ignore[valid-type]
        base_height_mm: Pos = 6.0,
        process: Process = "fdm_0.2_nozzle",
        edge_wall_mm: Pos = 1.2,
    ) -> dict:
        """Snap a terrain footprint to a gaming grid and lay out edge connectors (magnets or OpenLOCK-style clips) with checks."""
        return terrain_grid_check(footprint_mm, grid, connector, magnet, base_height_mm, process, edge_wall_mm)

    @mcp.tool(name="detail_advice", annotations=READ_ONLY)
    def detail_advice_tool(
        scale: Annotated[str, Field(description="'28mm', '32mm' or '1:N'")],
        features: Annotated[list[DetailFeature], Field(min_length=1, max_length=100)],
        process: Process = "msla_resin",
        viewing_distance_mm: Annotated[float, Field(ge=100, le=3000)] = 600.0,
    ) -> dict:
        """Scale-aware detail exaggeration: true size at scale, heroic multiplier, process limit and the size to model."""
        return detail_advice(scale, process, [f.model_dump(exclude_none=True) for f in features], viewing_distance_mm)

    @mcp.tool(name="hollow_plan", annotations=READ_ONLY)
    async def hollow_plan_tool(
        stl_path: Annotated[str, Field(description="part STL in mm, in its print pose (or give rotate_deg)")],
        process: Process = "msla_resin",
        shell_mm: Annotated[float | None, Field(ge=0.8, le=5)] = None,
        rotate_deg: Annotated[list[float] | None, Field(min_length=3, max_length=3)] = None,
    ) -> dict:
        """Resin hollowing advice: hollow or not, shell thickness, resin saved, and drain + vent hole positions."""
        return await asyncio.to_thread(hollow_plan, load(stl_path), process, shell_mm, rotate_deg)

    @mcp.tool(name="split_plan", annotations=READ_ONLY)
    def split_plan_tool(
        size_mm: Annotated[list[Pos], Field(min_length=3, max_length=3, description="part bounding box [x, y, z] mm")],
        printer: Printer = "elegoo_saturn_4_ultra",
        process: Process = "msla_resin",
        fit: Literal["press", "glue", "slip"] = "glue",
        margin_mm: Annotated[float, Field(ge=0, le=30)] = 5.0,
    ) -> dict:
        """Cut plan for a part too big for a named printer, with peg size, count, keying and clearance per joint."""
        return split_plan(size_mm, printer, process, fit, margin_mm)

    @mcp.tool(name="base_catalog", annotations=READ_ONLY)
    def base_catalog_tool(
        footprint_mm: Annotated[list[Pos] | None, Field(min_length=2, max_length=2)] = None,
        margin_mm: Annotated[float, Field(ge=0, le=20)] = 1.5,
        shapes: list[Literal["round", "oval", "hex", "square", "rect"]] | None = None,
    ) -> dict:
        """Round, oval, hex, square and rectangular bases plus scenic-top advice; smallest bases that fit a footprint."""
        return base_catalog(footprint_mm, margin_mm, shapes)

    @mcp.tool(name="floor_clearance_check", annotations=READ_ONLY)
    def floor_clearance_check_tool(
        scale: Annotated[str, Field(description="'28mm', '32mm' or '1:N'")],
        storey_clear_mm: Annotated[Pos, Field(description="floor top to the underside of the next floor/roof")],
        door_mm: Annotated[list[Pos] | None, Field(min_length=2, max_length=2, description="[width, height]")] = None,
        base_height_mm: Pos = 3.2,
        figure_base_mm: Pos = 25.0,
        floor_fit_gap_mm: Annotated[float | None, Field(ge=0, le=5)] = None,
        process: Process = "fdm_0.2_nozzle",
        lip_engagement_mm: Annotated[float | None, Field(ge=0, le=20)] = None,
        hatch_mm: Annotated[list[Pos] | None, Field(min_length=2, max_length=2)] = None,
        opening_mm: Annotated[Pos | None, Field(description="smallest access opening when a roof/floor is lifted")] = None,
    ) -> dict:
        """Building clearances: storey height and doors vs a figure on its base, drop-in floor fit, lip engagement, hatch."""
        return floor_clearance_check(scale, storey_clear_mm, door_mm, base_height_mm, figure_base_mm, floor_fit_gap_mm, process,
                                     lip_engagement_mm, hatch_mm, opening_mm)

    @mcp.tool(name="plate_pack", annotations=READ_ONLY)
    def plate_pack_tool(
        parts: Annotated[list[PackPart], Field(min_length=1, max_length=200)],
        printer: Printer = "elegoo_saturn_4_ultra",
        gap_mm: Annotated[float, Field(ge=0, le=30)] = 3.0,
        margin_mm: Annotated[float, Field(ge=0, le=30)] = 5.0,
    ) -> dict:
        """Parts per build plate and plates per batch for a named printer (shelf packing of print-pose footprints)."""
        return plate_pack([p.model_dump(exclude_none=True) for p in parts], printer, gap_mm, margin_mm)
