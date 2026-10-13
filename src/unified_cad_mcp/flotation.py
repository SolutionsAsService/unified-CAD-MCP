"""Static hydrostatics for floating / amphibious vehicles from closed STL meshes (pure Python).

Used by airframe_flotation_check. Given the sealed (buoyant) volumes as closed, outward-oriented meshes, the vehicle
mass and CG, it finds the upright waterline, reserve buoyancy, centre of buoyancy, freeboard of named points (prop
discs, vents, seams, camera window) and righting-arm (GZ) curves in roll and pitch, from which it reads the initial
metacentric height (GM), the angle of maximum GZ and the angle of vanishing stability.

Method: submerged volume and centroid of a mesh below a horizontal plane by clipping each triangle against the plane
and summing signed tetrahedra with their apex ON the plane, so the (unbuilt) cap polygon contributes nothing. The
waterline is found by bisection. Heeled cases rotate the meshes and the CG about the body x (roll) or y (pitch)
axis and re-balance the waterline (free sinkage, trim held fixed: a standard fixed-trim GZ curve).
HEURISTIC static hydrostatics only: no waves, wind, rotor downwash, spray, dynamic heel, free-surface effect of
water inside the hull, flooding or structural loads. Validate with a tank test.
"""
from __future__ import annotations

import math
import struct
from pathlib import Path

Tri = tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]


def read_stl(path: Path) -> list[Tri]:
    data = path.read_bytes()
    if len(data) >= 84:
        n = struct.unpack_from("<I", data, 80)[0]
        if 84 + 50 * n == len(data):
            tris = []
            for i in range(n):
                v = struct.unpack_from("<12f", data, 84 + 50 * i)
                tris.append(((v[3], v[4], v[5]), (v[6], v[7], v[8]), (v[9], v[10], v[11])))
            return tris
    pts = [tuple(float(x) for x in line.split()[1:4]) for line in data.decode("ascii", "replace").splitlines()
           if line.strip().startswith("vertex")]
    if not pts or len(pts) % 3:
        raise ValueError(f"{path.name}: not a readable binary or ASCII STL")
    return [(pts[i], pts[i + 1], pts[i + 2]) for i in range(0, len(pts), 3)]


def _rot(p, axis: str, a: float):
    c, s = math.cos(a), math.sin(a)
    x, y, z = p
    if axis == "roll":   # about +x: +y side goes up for a > 0
        return (x, y * c - z * s, y * s + z * c)
    return (x * c + z * s, y, -x * s + z * c)  # pitch about +y: +x (nose) goes down for a > 0


def submerged(tris: list[Tri], h: float) -> tuple[float, list[float]]:
    """Signed volume (mm^3) and centroid of the part of a closed mesh below z = h."""
    vol = 0.0
    m = [0.0, 0.0, 0.0]
    for t in tris:
        d0, d1, d2 = t[0][2] - h, t[1][2] - h, t[2][2] - h
        if d0 >= 0 and d1 >= 0 and d2 >= 0:
            continue
        if d0 < 0 and d1 < 0 and d2 < 0:
            poly = t
        else:
            d = (d0, d1, d2)
            poly = []
            for i in range(3):
                p, q, dp, dq = t[i], t[(i + 1) % 3], d[i], d[(i + 1) % 3]
                if dp < 0:
                    poly.append(p)
                if (dp < 0) != (dq < 0):
                    k = dp / (dp - dq)
                    poly.append((p[0] + k * (q[0] - p[0]), p[1] + k * (q[1] - p[1]), h))
        a = poly[0]
        ax, ay, az = a[0], a[1], a[2] - h
        for i in range(1, len(poly) - 1):
            b, c = poly[i], poly[i + 1]
            # tetrahedron with apex O = (0, 0, h); coordinates relative to O
            bx, by, bz = b[0], b[1], b[2] - h
            cx, cy, cz = c[0], c[1], c[2] - h
            v = (ax * (by * cz - bz * cy) - ay * (bx * cz - bz * cx) + az * (bx * cy - by * cx)) / 6.0
            vol += v
            m[0] += v * (ax + bx + cx) / 4
            m[1] += v * (ay + by + cy) / 4
            m[2] += v * (az + bz + cz) / 4
    if vol <= 0:
        return 0.0, [0.0, 0.0, h]
    return vol, [m[0] / vol, m[1] / vol, m[2] / vol + h]


def _signed_volume(tris: list[Tri]) -> float:
    return sum((a[0] * (b[1] * c[2] - b[2] * c[1]) - a[1] * (b[0] * c[2] - b[2] * c[0]) + a[2] * (b[0] * c[1] - b[1] * c[0])) / 6.0
               for a, b, c in tris)


def oriented(tris: list[Tri]) -> list[Tri]:
    """Flip an inward-wound closed mesh so its signed volume is positive."""
    return [(a, c, b) for a, b, c in tris] if _signed_volume(tris) < 0 else tris


def _balance(tris, target_mm3, iters=40):
    lo = min(p[2] for t in tris for p in t)
    hi = max(p[2] for t in tris for p in t)
    total, _ = submerged(tris, hi + 1)
    if total < target_mm3:
        return None, total
    for _ in range(iters):
        mid = (lo + hi) / 2
        v, _ = submerged(tris, mid)
        lo, hi = (mid, hi) if v < target_mm3 else (lo, mid)
    return (lo + hi) / 2, total


def analyze(meshes: dict[str, list[Tri]], *, mass_g: float, cg_mm: list[float], water_density_kg_m3: float = 1000.0,
            points: list[dict] | None = None, heel_step_deg: float = 5.0, heel_max_deg: float = 90.0,
            min_reserve_buoyancy: float = 0.5, min_gm_mm: float = 20.0, min_vanishing_angle_deg: float = 45.0,
            max_static_heel_deg: float = 3.0) -> dict:
    meshes = {k: oriented(m) for k, m in meshes.items()}
    tris = [t for m in meshes.values() for t in m]
    rho = water_density_kg_m3 * 1e-6  # g/mm^3
    need = mass_g / rho
    checks: list[dict] = []

    def check(name, value, ok, limit, basis):
        checks.append({"name": name, "value": None if value is None else round(value, 3), "pass": bool(ok), "limit": limit, "basis": basis})

    per_mesh = {k: round(submerged(m, max(p[2] for t in m for p in t) + 1)[0] / 1000, 2) for k, m in meshes.items()}
    h0, total = _balance(tris, need)
    reserve = total / need - 1
    check("floats (sealed volume exceeds displacement)", total * rho, h0 is not None, f">= mass {mass_g} g",
          "Archimedes: the sealed volumes must displace at least the vehicle mass.")
    check("reserve buoyancy", reserve, reserve >= min_reserve_buoyancy, min_reserve_buoyancy,
          "Spare sealed volume as a fraction of displacement; covers waves, spray, payload growth and a partly flooded "
          "compartment (small craft commonly keep >= 50 %).")
    out = {"water_density_kg_m3": water_density_kg_m3, "mass_g": mass_g, "cg_mm": cg_mm,
           "sealed_volume_l": round(total / 1e6, 4), "sealed_volume_by_mesh_cm3": per_mesh,
           "reserve_buoyancy_fraction": round(reserve, 4), "checks": checks}
    if h0 is None:
        out["all_checks_pass"] = False
        out["note"] = "Sinks: sealed volume is below the displacement."
        return out
    _, cb = submerged(tris, h0)
    out["waterline_z_mm"] = round(h0, 3)
    out["draft_mm"] = round(h0 - min(p[2] for t in tris for p in t), 3)
    out["centre_of_buoyancy_mm"] = [round(v, 3) for v in cb]
    out["cg_cb_horizontal_offset_mm"] = round(math.hypot(cb[0] - cg_mm[0], cb[1] - cg_mm[1]), 3)
    fb = []
    for p in points or []:
        f = p["position_mm"][2] - h0
        lim = p.get("min_freeboard_mm", 0)
        fb.append({"name": p["name"], "freeboard_mm": round(f, 2), "min_freeboard_mm": lim})
        check(f"freeboard: {p['name']}", f, f >= lim, lim,
              "Height above the upright waterline (props clear of water and spray; vents, seams and windows above water).")
    out["freeboard"] = fb
    curves = {}
    steps = int(round(heel_max_deg / heel_step_deg))
    for axis in ("roll", "pitch"):
        rows = []
        for i in range(steps + 1):
            a = math.radians(i * heel_step_deg)
            rt = [tuple(_rot(p, axis, a) for p in t) for t in tris]
            g = _rot(cg_mm, axis, a)
            h, _ = _balance(rt, need, iters=30)
            _, b = submerged(rt, h)
            gz = (g[1] - b[1]) if axis == "roll" else (b[0] - g[0])
            rows.append({"angle_deg": round(i * heel_step_deg, 2), "gz_mm": round(gz, 3), "waterline_z_mm": round(h, 2)})
        gm = rows[1]["gz_mm"] / math.sin(math.radians(heel_step_deg))
        vanish = next((r["angle_deg"] for r in rows[1:] if r["gz_mm"] <= 0), None)
        best = max(rows, key=lambda r: r["gz_mm"])
        curves[axis] = {"gz_curve": rows, "gm_initial_mm": round(gm, 2), "max_gz_mm": best["gz_mm"], "angle_of_max_gz_deg": best["angle_deg"],
                        "angle_of_vanishing_stability_deg": vanish if vanish is not None else f"> {heel_max_deg:g}"}
        check(f"initial stability GM ({axis})", gm, gm >= min_gm_mm, min_gm_mm,
              "Initial metacentric height from the GZ slope at the first step; positive = rights itself at small angles. "
              "Keep a margin for wind on cages/antennas and downwash at take-off.")
        v = vanish if vanish is not None else heel_max_deg + heel_step_deg
        check(f"range of positive stability ({axis})", v, v >= min_vanishing_angle_deg, min_vanishing_angle_deg,
              "Angle at which the righting arm vanishes; below it the craft rights itself.")
        if axis == "roll" and gm > 0:
            heel = math.degrees(math.atan2(abs(cb[1] - cg_mm[1]), gm))
            out["static_list_deg"] = round(heel, 2)
            check("static list (lateral CG vs CB)", heel, heel <= max_static_heel_deg, max_static_heel_deg,
                  "Lateral CG offset over GM: a floating multirotor should sit level to take off.")
    out["stability"] = curves
    out["all_checks_pass"] = all(c["pass"] for c in checks)
    out["method"] = ("Clipped-mesh hydrostatics (signed tetrahedra with apex on the waterplane), bisection waterline, fixed-trim GZ "
                     "curves in roll and pitch. Meshes must be closed and outward-oriented; only sealed volumes count.")
    out["estimate_kind"] = ("HEURISTIC static hydrostatics: no waves, wind, downwash, spray, dynamic heel, free-surface or flooding. "
                            "Validate with a tank test (level float, freeboard marks, heel by known weight, self-righting).")
    return out
