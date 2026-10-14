"""Mesh budget and shell audit for sculpted (SDF / voxel) miniatures, from the fantasy-miniatures dwarf blacksmith and
dire wolf builds.

Registered into the "miniature" namespace (miniature_<name>):
- mesh_budget: the SDF grid pitch that lands a sculpt under a triangle budget (the pure-Python checks refuse > 300k),
  and whether that pitch still resolves the smallest feature. Lesson behind it: QEM-decimating a marched SDF mesh
  (FreeCAD Mesh.decimate) folds and pinches it (non-manifold edges, self-intersections, then fake 0 mm walls in
  miniature_printability_check). Re-mesh the field coarser instead; triangle count scales with 1 / pitch^2.
- shell_audit: connected shells of one STL with signed volume. Marching a smooth-union SDF leaves speck shells and
  inside-out bubbles just under the skin; the printability check reads them as enclosed voids and 0 mm walls. Lists
  them so the build can drop shells under min_mm3 and every negative-volume shell before export.

HEURISTIC, pure Python (stdlib only).
"""
from __future__ import annotations

import math
from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

READ_ONLY = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
CHECKER_LIMIT = 300_000


def mesh_budget(current_triangles: int, current_pitch_mm: float, target_triangles: int = 250_000,
                min_feature_mm: float | None = None, voxels_per_feature: float = 3.0) -> dict:
    """Pitch for a target triangle count (n ~ 1/pitch^2) and the resulting voxels across the smallest feature."""
    ratio = current_triangles / target_triangles
    pitch = current_pitch_mm * math.sqrt(ratio) if ratio > 1 else current_pitch_mm
    pitch = math.ceil(pitch * 100) / 100  # round up to 0.01 mm so the estimate stays under budget
    predicted = int(current_triangles * (current_pitch_mm / pitch) ** 2)
    out = {"recommended_pitch_mm": pitch, "predicted_triangles": predicted, "checker_limit": CHECKER_LIMIT,
           "under_checker_limit": predicted <= CHECKER_LIMIT, "warnings": [],
           "advice": ["Re-mesh the SDF at recommended_pitch_mm instead of decimating: QEM on marched meshes breaks manifold "
                      "and creates fake 0 mm walls.",
                      "Before export drop shells under 0.05 mm3 and every inside-out (negative-volume) shell "
                      "(miniature_shell_audit lists them).",
                      "Thicken sub-0.4 mm sheets in the field (SDF offset of about 0.1-0.2 mm) rather than after meshing."]}
    if min_feature_mm:
        vox = min_feature_mm / pitch
        out["voxels_across_min_feature"] = round(vox, 2)
        if vox < voxels_per_feature:
            out["warnings"].append(f"smallest feature {min_feature_mm} mm spans {vox:.1f} voxels at {pitch} mm (< "
                                   f"{voxels_per_feature}): it will soften or break; thicken it, or mesh that part on its own grid")
    if ratio <= 1:
        out["warnings"].append("already under target: keep the current pitch")
    return out


def shell_audit(tris, min_mm3: float = 0.05) -> dict:
    """Edge-connected shells (vertices welded to 0.1 um) with signed volume; flags specks and inside-out shells."""
    key = lambda p: (round(p[0], 4), round(p[1], 4), round(p[2], 4))
    idx: dict = {}
    faces = [[idx.setdefault(key(p), len(idx)) for p in t] for t in tris]
    parent = list(range(len(idx)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for a, b, c in faces:
        ra = find(a)
        for v in (b, c):
            rv = find(v)
            if rv != ra:
                parent[rv] = ra
    vol: dict = {}
    count: dict = {}
    for t, f in zip(tris, faces):
        r = find(f[0])
        (ax, ay, az), (bx, by, bz), (cx, cy, cz) = t
        vol[r] = vol.get(r, 0.0) + (ax * (by * cz - bz * cy) - ay * (bx * cz - bz * cx) + az * (bx * cy - by * cx)) / 6.0
        count[r] = count.get(r, 0) + 1
    shells = sorted(({"triangles": count[r], "volume_mm3": round(v, 4)} for r, v in vol.items()), key=lambda s: -abs(s["volume_mm3"]))
    specks = [s for s in shells if abs(s["volume_mm3"]) < min_mm3]
    inside_out = [s for s in shells if s["volume_mm3"] < 0 and abs(s["volume_mm3"]) >= min_mm3]
    return {"shell_count": len(shells), "shells": shells[:20], "speck_shells": len(specks), "inside_out_shells": len(inside_out),
            "clean": not specks and not inside_out,
            "advice": "drop speck and inside-out shells before export (fantasy-miniatures tools/sculptkit.drop_specks)" if specks or inside_out else "",
            "estimate_kind": "HEURISTIC: edge-connected components after welding, signed volume by divergence theorem"}


def register(mcp: FastMCP, load) -> None:
    import asyncio

    @mcp.tool(name="mesh_budget", annotations=READ_ONLY)
    async def mesh_budget_tool(
        current_triangles: Annotated[int, Field(gt=0)],
        current_pitch_mm: Annotated[float, Field(gt=0, le=5)],
        target_triangles: Annotated[int, Field(gt=1000, le=CHECKER_LIMIT)] = 250_000,
        min_feature_mm: Annotated[float | None, Field(gt=0, le=50)] = None,
    ) -> dict:
        """SDF grid pitch that lands a sculpt under a triangle budget without decimation, and whether it still resolves the
        smallest feature. Use before re-meshing an over-budget sculpt for miniature_printability_check."""
        return mesh_budget(current_triangles, current_pitch_mm, target_triangles, min_feature_mm)

    @mcp.tool(name="shell_audit", annotations=READ_ONLY)
    async def shell_audit_tool(
        stl_path: Annotated[str, Field(description="part STL in mm")],
        min_mm3: Annotated[float, Field(gt=0, le=10)] = 0.05,
    ) -> dict:
        """Connected shells with signed volume: speck shells and inside-out bubbles left by marching an SDF (they read as
        enclosed voids and 0 mm walls in miniature_printability_check)."""
        return await asyncio.to_thread(shell_audit, load(stl_path), min_mm3)
