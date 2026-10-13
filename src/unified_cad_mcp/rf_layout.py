"""Heuristic antenna-layout check for small enclosures (trackers, gateways, nodes). Geometry rules only:
no field solver, no matching, no certification. Every rule cites its basis in the result."""
from __future__ import annotations

import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

C_MM_PER_US = 299_792.458  # mm per microsecond -> lambda_mm = C / f_MHz
Finite = Annotated[float, Field(allow_inf_nan=False)]
Vec3 = Annotated[list[Finite], Field(min_length=3, max_length=3)]


class Antenna(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, Field(min_length=1, max_length=80)]
    role: Literal["cellular_main", "cellular_diversity", "gnss", "ble_wifi", "lora", "other"]
    kind: Literal["monopole", "patch", "chip"] = "monopole"
    feed_mm: Vec3 = Field(description="Feed point (monopole base, patch centre)")
    axis: Vec3 = Field(description="Monopole element direction or patch boresight (normal)")
    length_mm: Annotated[float, Field(ge=0, le=2000, allow_inf_nan=False)] = 0
    low_mhz: Annotated[float, Field(gt=0, le=100_000, allow_inf_nan=False)]
    high_mhz: Annotated[float, Field(gt=0, le=100_000, allow_inf_nan=False)]


class LayoutInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    antennas: Annotated[list[Antenna], Field(min_length=1, max_length=16)]
    up: Vec3 = Field(default=[0, 0, 1], description="Sky / away-from-vehicle direction in the model frame")
    mount_plane_z_mm: Finite = Field(default=0, description="Height of the vehicle mounting face along 'up' (metal)")
    min_monopole_fraction_of_quarter_wave: Annotated[float, Field(gt=0, le=1.5, allow_inf_nan=False)] = 0.5
    max_polarization_tilt_deg: Annotated[float, Field(gt=0, le=90, allow_inf_nan=False)] = 15
    min_diversity_spacing_wavelengths: Annotated[float, Field(gt=0, le=2, allow_inf_nan=False)] = 0.25
    max_gnss_boresight_tilt_deg: Annotated[float, Field(gt=0, le=90, allow_inf_nan=False)] = 10
    min_gnss_to_cellular_mm: Annotated[float, Field(ge=0, le=2000, allow_inf_nan=False)] = 40


def _unit(v):
    n = math.sqrt(sum(c * c for c in v))
    if n == 0:
        raise ValueError("zero-length direction vector")
    return [c / n for c in v]


def _angle_deg(a, b):
    a, b = _unit(a), _unit(b)
    return math.degrees(math.acos(max(-1.0, min(1.0, sum(x * y for x, y in zip(a, b))))))


def antenna_layout_check(data: LayoutInput) -> dict:
    up = _unit(data.up)
    rows, checks = [], []

    def check(name, value, ok, limit, basis):
        checks.append({"name": name, "value": round(value, 3) if isinstance(value, float) else value, "pass": bool(ok), "limit": limit, "basis": basis})

    for a in data.antennas:
        lam_low = C_MM_PER_US / a.low_mhz
        feed_h = sum((p) * u for p, u in zip(a.feed_mm, up)) - data.mount_plane_z_mm
        row = {"name": a.name, "role": a.role, "kind": a.kind, "lambda_low_mm": round(lam_low, 1),
               "quarter_wave_low_mm": round(lam_low / 4, 1), "feed_height_above_mount_mm": round(feed_h, 1)}
        if a.kind == "monopole":
            tilt = _angle_deg(a.axis, up)
            frac = a.length_mm / (lam_low / 4)
            row.update({"tilt_from_up_deg": round(tilt, 1), "fraction_of_quarter_wave": round(frac, 3)})
            check(f"vertical polarization: {a.name}", tilt, tilt <= data.max_polarization_tilt_deg, data.max_polarization_tilt_deg,
                  "Base-station and vehicle antennas are vertically polarized; a monopole lying along the roof couples poorly and loses its ground plane.")
            check(f"element length: {a.name}", frac, frac >= data.min_monopole_fraction_of_quarter_wave, data.min_monopole_fraction_of_quarter_wave,
                  "Fraction of a quarter wave at the lowest band; below ~0.5 a loaded whip loses bandwidth and efficiency fast (Chu limit).")
        if a.kind == "patch":
            tilt = _angle_deg(a.axis, up)
            row["boresight_tilt_deg"] = round(tilt, 1)
            if a.role == "gnss":
                check(f"GNSS sky view: {a.name}", tilt, tilt <= data.max_gnss_boresight_tilt_deg, data.max_gnss_boresight_tilt_deg,
                      "GNSS patches are RHCP with a zenith-peaked pattern; the boresight must point at the sky, away from the vehicle.")
        rows.append(row)

    cell = [a for a in data.antennas if a.role.startswith("cellular")]
    mains = [a for a in cell if a.role == "cellular_main"]
    divs = [a for a in cell if a.role == "cellular_diversity"]
    for m in mains:
        for d in divs:
            dist = math.dist(m.feed_mm, d.feed_mm)
            lam = C_MM_PER_US / min(m.low_mhz, d.low_mhz)
            check(f"diversity spacing: {m.name} / {d.name}", dist / lam, dist / lam >= data.min_diversity_spacing_wavelengths,
                  data.min_diversity_spacing_wavelengths,
                  f"Spacing {dist:.0f} mm in wavelengths at the lowest band; below ~0.25 lambda the envelope correlation of two co-polarized monopoles rises sharply.")
    for g in [a for a in data.antennas if a.role == "gnss"]:
        for c in cell:
            dist = math.dist(g.feed_mm, c.feed_mm)
            check(f"GNSS clearance: {g.name} / {c.name}", dist, dist >= data.min_gnss_to_cellular_mm, data.min_gnss_to_cellular_mm,
                  "Keep the GNSS patch away from transmitting elements to limit desense and pattern blockage.")
    return {"antennas": rows, "checks": checks, "all_checks_pass": all(c["pass"] for c in checks),
            "method": "geometry heuristics only (orientation, electrical length, spacing); not a field solve, matching, efficiency or regulatory result"}
