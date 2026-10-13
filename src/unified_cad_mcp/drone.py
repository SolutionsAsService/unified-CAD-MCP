"""In-process, pure-Python first-order drone design estimators (drone_*).

These are deterministic HEURISTIC calculators for early concept sizing. They do
not replace a motor/propeller test stand, a validated CFD/VLM/FEA tool, flight
testing or legal advice. No files, network, subprocesses or native CAD are used.

Units are in each field name (g, mm, in, mAh, W, A, Wh, min, kg*m^2, N). The
mass-budget frame is whatever right-handed body frame the caller chooses; it is
echoed back, never inferred from KiCad/FreeCAD documents.
"""
from __future__ import annotations

import math
from typing import Annotated, Literal

from fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from .fixed_wing import FixedWingInput, analyze as fixed_wing_analyze
from .rf_layout import LayoutInput, antenna_layout_check

G0 = 9.80665  # m/s^2
INCH = 0.0254
CELL_NOMINAL_V = {"lipo": 3.7, "lihv": 3.8, "liion": 3.6, "lifepo4": 3.2}
HEURISTIC = ("HEURISTIC first-order estimate (momentum theory with figure of merit and lumped "
             "efficiencies). Validate with manufacturer thrust tables, bench tests and flight logs.")
READ_ONLY = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}

Finite = Annotated[float, Field(allow_inf_nan=False)]
Vec3 = Annotated[list[Finite], Field(min_length=3, max_length=3)]


class MassItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    mass_g: float = Field(gt=0, le=1_000_000, allow_inf_nan=False)
    position_mm: Vec3 = Field(description="Item CG in the caller's body frame, mm")
    size_mm: Vec3 | None = Field(default=None, description="Optional uniform solid-box extents (x,y,z) mm for self-inertia; omitted = point mass")
    inertia_about_cg_kg_m2: Annotated[list[Vec3], Field(min_length=3, max_length=3)] | None = Field(
        default=None, description="Optional self-inertia tensor about the item's own CG (e.g. from airframe_build_fixed_wing); overrides size_mm")
    category: str = Field(default="other", min_length=1, max_length=60)


def isa_density(altitude_m: float, temperature_offset_c: float = 0.0) -> float:
    """ISA troposphere density (valid -500..11000 m) with optional temperature offset."""
    if not -500 <= altitude_m <= 11000:
        raise ValueError("altitude_m must be within -500..11000 (ISA troposphere)")
    t_isa = 288.15 - 0.0065 * altitude_m
    pressure = 101325.0 * (t_isa / 288.15) ** 5.25588
    return pressure / (287.05287 * (t_isa + temperature_offset_c))


def _sym_eigenvalues(m: list[list[float]]) -> list[float]:
    """Eigenvalues of a real symmetric 3x3 matrix (closed form), ascending."""
    p1 = m[0][1] ** 2 + m[0][2] ** 2 + m[1][2] ** 2
    q = (m[0][0] + m[1][1] + m[2][2]) / 3
    if p1 <= 1e-30:
        return sorted([m[0][0], m[1][1], m[2][2]])
    p = math.sqrt((sum((m[i][i] - q) ** 2 for i in range(3)) + 2 * p1) / 6)
    b = [[(m[i][j] - (q if i == j else 0.0)) / p for j in range(3)] for i in range(3)]
    det = (b[0][0] * (b[1][1] * b[2][2] - b[1][2] * b[2][1])
           - b[0][1] * (b[1][0] * b[2][2] - b[1][2] * b[2][0])
           + b[0][2] * (b[1][0] * b[2][1] - b[1][1] * b[2][0]))
    phi = math.acos(max(-1.0, min(1.0, det / 2))) / 3
    e1 = q + 2 * p * math.cos(phi)
    e3 = q + 2 * p * math.cos(phi + 2 * math.pi / 3)
    return sorted([e1, 3 * q - e1 - e3, e3])


def mass_cg_budget(items: list[MassItem], reference_point_mm: list[float] | None = None,
                   max_takeoff_mass_g: float | None = None, cg_tolerance_mm: float | None = None,
                   cg_x_range_mm: list[float] | None = None) -> dict:
    if not items:
        raise ValueError("At least one mass item is required")
    names = [i.name for i in items]
    if len(set(names)) != len(names):
        raise ValueError("Mass item names must be unique")
    ref = list(reference_point_mm or [0.0, 0.0, 0.0])
    total_g = sum(i.mass_g for i in items)
    cg = [sum(i.mass_g * i.position_mm[k] for i in items) / total_g for k in range(3)]
    inertia = [[0.0] * 3 for _ in range(3)]
    for item in items:
        m = item.mass_g / 1000
        d = [(item.position_mm[k] - cg[k]) / 1000 for k in range(3)]
        dd = sum(v * v for v in d)
        for a in range(3):
            for b in range(3):
                inertia[a][b] += m * ((dd if a == b else 0.0) - d[a] * d[b])
        if item.inertia_about_cg_kg_m2:
            tensor = item.inertia_about_cg_kg_m2
            if any(abs(tensor[a][b] - tensor[b][a]) > 1e-9 + 1e-6 * abs(tensor[a][b]) for a in range(3) for b in range(3)):
                raise ValueError(f"inertia_about_cg_kg_m2 must be symmetric: {item.name}")
            for a in range(3):
                for b in range(3):
                    inertia[a][b] += tensor[a][b]
        elif item.size_mm:
            if min(item.size_mm) < 0:
                raise ValueError(f"size_mm must be non-negative: {item.name}")
            lx, ly, lz = (v / 1000 for v in item.size_mm)
            inertia[0][0] += m * (ly ** 2 + lz ** 2) / 12
            inertia[1][1] += m * (lx ** 2 + lz ** 2) / 12
            inertia[2][2] += m * (lx ** 2 + ly ** 2) / 12
    by_category: dict[str, float] = {}
    for item in items:
        by_category[item.category] = by_category.get(item.category, 0.0) + item.mass_g
    offset = [cg[k] - ref[k] for k in range(3)]
    horizontal = math.hypot(offset[0], offset[1])
    result = {
        "total_mass_g": round(total_g, 6),
        "cg_mm": [round(v, 4) for v in cg],
        "reference_point_mm": ref,
        "cg_offset_from_reference_mm": [round(v, 4) for v in offset],
        "horizontal_cg_offset_mm": round(horizontal, 4),
        "inertia_about_cg_kg_m2": [[round(v, 9) for v in row] for row in inertia],
        "principal_moments_kg_m2": [round(v, 9) for v in _sym_eigenvalues(inertia)],
        "mass_by_category_g": {k: round(v, 6) for k, v in sorted(by_category.items())},
        "mass_fraction_by_category": {k: round(v / total_g, 6) for k, v in sorted(by_category.items())},
        "frame": "caller-defined right-handed body frame, mm; horizontal offset uses x/y, so put z along the thrust axis",
        "model": "point masses plus optional axis-aligned uniform solid boxes or caller-supplied self-inertia tensors; list wiring, fasteners and adhesive explicitly",
        "warnings": [],
    }
    if max_takeoff_mass_g is not None:
        result["mass_margin_g"] = round(max_takeoff_mass_g - total_g, 6)
        if total_g > max_takeoff_mass_g:
            result["warnings"].append("Total mass exceeds max_takeoff_mass_g")
    if cg_tolerance_mm is not None:
        result["cg_within_tolerance"] = horizontal <= cg_tolerance_mm
        if horizontal > cg_tolerance_mm:
            result["warnings"].append("Horizontal CG offset exceeds cg_tolerance_mm; motors will be unevenly loaded at hover")
    if cg_x_range_mm is not None:
        lo, hi = sorted(cg_x_range_mm)
        result["cg_x_within_range"] = lo <= cg[0] <= hi
        result["cg_x_range_mm"] = [lo, hi]
        if not result["cg_x_within_range"]:
            result["warnings"].append("Longitudinal CG is outside cg_x_range_mm (fixed-wing static-margin band)")
    return result


def propulsion_estimate(*, all_up_mass_g: float, rotor_count: int, prop_diameter_in: float,
                        battery_cells: int, battery_capacity_mah: float, battery_chemistry: str = "lipo",
                        usable_capacity_fraction: float = 0.8, figure_of_merit: float = 0.6,
                        motor_efficiency: float = 0.8, esc_efficiency: float = 0.95,
                        avionics_power_w: float = 5.0, altitude_m: float = 0.0,
                        temperature_offset_c: float = 0.0, coaxial: bool = False,
                        max_thrust_per_motor_g: float | None = None,
                        motor_max_current_a: float | None = None,
                        battery_c_rating: float | None = None) -> dict:
    if battery_chemistry not in CELL_NOMINAL_V:
        raise ValueError("Unknown battery_chemistry")
    rho = isa_density(altitude_m, temperature_offset_c)
    thrust_per_rotor_n = all_up_mass_g / 1000 * G0 / rotor_count
    area = math.pi * (prop_diameter_in * INCH / 2) ** 2
    induced_ideal_w = thrust_per_rotor_n ** 1.5 / math.sqrt(2 * rho * area)
    coaxial_factor = 1.25 if coaxial else 1.0  # crude 20-30 % coaxial interference loss
    shaft_w = induced_ideal_w / figure_of_merit * coaxial_factor
    hover_power_w = shaft_w / (motor_efficiency * esc_efficiency) * rotor_count + avionics_power_w
    v_nom = CELL_NOMINAL_V[battery_chemistry] * battery_cells
    capacity_ah = battery_capacity_mah / 1000
    usable_wh = v_nom * capacity_ah * usable_capacity_fraction
    out = {
        "air_density_kg_m3": round(rho, 5),
        "hover_thrust_per_rotor_g": round(thrust_per_rotor_n / G0 * 1000, 3),
        "disk_loading_n_m2": round(thrust_per_rotor_n / area, 3),
        "ideal_induced_power_per_rotor_w": round(induced_ideal_w, 3),
        "hover_shaft_power_per_rotor_w": round(shaft_w, 3),
        "hover_electrical_power_w": round(hover_power_w, 3),
        "hover_efficiency_g_per_w": round(all_up_mass_g / hover_power_w, 4),
        "battery_nominal_voltage_v": round(v_nom, 3),
        "usable_energy_wh": round(usable_wh, 3),
        "hover_current_a": round(hover_power_w / v_nom, 3),
        "hover_flight_time_min": round(usable_wh / hover_power_w * 60, 2),
        "estimate_kind": HEURISTIC,
        "assumptions": {
            "figure_of_merit": figure_of_merit, "motor_efficiency": motor_efficiency,
            "esc_efficiency": esc_efficiency, "avionics_power_w": avionics_power_w,
            "usable_capacity_fraction": usable_capacity_fraction, "coaxial_penalty_factor": coaxial_factor,
            "cell_nominal_v": CELL_NOMINAL_V[battery_chemistry],
            "scope": "Static hover only: no forward flight, wind, climb, battery sag, temperature or ageing effects",
        },
        "warnings": [],
    }
    warnings = out["warnings"]
    if max_thrust_per_motor_g is not None:
        twr = max_thrust_per_motor_g * rotor_count / all_up_mass_g
        out["thrust_to_weight"] = round(twr, 3)
        out["hover_thrust_fraction"] = round(1 / twr, 4)
        out["twr_guidance"] = ("rule of thumb: <2 marginal; 2-3 camera/survey; 3-5 general/freestyle; >5 racing")
        if twr < 2:
            warnings.append("Thrust-to-weight below 2: poor control authority and wind margin")
        if 1 / twr > 0.6:
            warnings.append("Hover needs >60% of max thrust; expect sluggish control and overheating")
    else:
        out["thrust_to_weight"] = None
        warnings.append("max_thrust_per_motor_g not supplied: thrust-to-weight UNKNOWN (use the motor's published thrust table for this prop and voltage)")
    if motor_max_current_a is not None:
        peak_a = motor_max_current_a * rotor_count + avionics_power_w / v_nom
        out["recommended_esc_continuous_a"] = round(motor_max_current_a * 1.25, 1)
        out["peak_battery_current_a"] = round(peak_a, 2)
        out["required_battery_c_rating"] = round(peak_a / capacity_ah, 2)
        if battery_c_rating is not None and battery_c_rating < peak_a / capacity_ah:
            warnings.append("Battery continuous C rating is below the estimated full-throttle draw")
    if not 2 <= prop_diameter_in <= 40:
        warnings.append("Propeller diameter outside 2-40 in; default figure of merit is unreliable")
    if usable_wh / hover_power_w * 60 < 3:
        warnings.append("Estimated hover endurance under 3 minutes")
    return out


def regulatory_weight_class(takeoff_mass_g: float, jurisdiction: str, use: str = "recreational") -> dict:
    """Coarse weight-threshold notes. NOT legal advice; rules change frequently."""
    notes: list[str] = []
    if jurisdiction == "US":
        if takeoff_mass_g < 250:
            band = "US: under 250 g (0.55 lb)"
            if use == "recreational":
                notes.append("Recreational flights under the limited recreational exception generally need no FAA registration; the TRUST certificate is still required.")
                notes.append("Remote ID is generally not required for unregistered sub-250 g recreational drones.")
            else:
                notes.append("Part 107 (non-recreational) operations require registration and a remote pilot certificate regardless of weight; Remote ID applies to registered drones.")
        elif takeoff_mass_g < 25000:
            band = "US: 250 g up to 55 lb (25 kg)"
            notes.append("FAA registration required; Standard Remote ID or a Remote ID broadcast module required unless flying within an FAA-recognized identification area (FRIA).")
            notes.append("Part 107 applies to non-recreational use (under 55 lb including payload).")
        else:
            band = "US: 55 lb (25 kg) or more"
            notes.append("Outside the Part 107 small-UAS limit: needs other FAA authorization (for example a 49 U.S.C. 44807 exemption).")
        sources = ["https://www.faa.gov/uas", "https://www.faa.gov/uas/getting_started/remote_id"]
    elif jurisdiction == "EU":
        if takeoff_mass_g < 250:
            band = "EU: under 250 g (C0 / privately built under 250 g)"
            notes.append("Open category A1 possible. Operator registration is still required if it carries a camera or sensor able to capture personal data (unless a toy).")
        elif takeoff_mass_g < 900:
            band = "EU: 250-900 g (C1 mass band)"
            notes.append("Operator registration required. Class-marked C1 drones need Direct Remote ID; privately built drones in this band fly in A3 or under the specific category.")
        elif takeoff_mass_g < 4000:
            band = "EU: 0.9-4 kg (C2 mass band)"
            notes.append("Registration required; class-marked C2 needs Direct Remote ID and an A2 certificate to fly near people. Privately built drones: A3.")
        elif takeoff_mass_g < 25000:
            band = "EU: 4-25 kg (C3/C4 mass band)"
            notes.append("Registration required; open category A3 only (far from people). C3 requires Direct Remote ID.")
        else:
            band = "EU: 25 kg or more"
            notes.append("Outside the open category: specific category (operational authorisation, e.g. SORA) or certified category.")
        sources = ["https://www.easa.europa.eu/en/domains/civil-drones"]
    else:
        raise ValueError("jurisdiction must be US or EU")
    notes.append("Class marks come from a manufacturer conformity assessment; a home-built aircraft does not acquire one by meeting a mass threshold.")
    return {"takeoff_mass_g": takeoff_mass_g, "jurisdiction": jurisdiction, "use": use, "weight_band": band,
            "notes": notes, "sources": sources,
            "disclaimer": "Simplified weight-threshold summary, NOT legal advice. Verify current national and local rules, airspace, operations over people and insurance before flight."}


def create_drone_server() -> FastMCP:
    """Pure in-process calculators mounted by the unified server as drone_*."""
    mcp = FastMCP("drone-estimators")

    @mcp.tool(name="mass_cg_budget", annotations=READ_ONLY)
    def mass_cg_budget_tool(
        items: Annotated[list[MassItem], Field(min_length=1, max_length=500)],
        reference_point_mm: Vec3 | None = None,
        max_takeoff_mass_g: Annotated[float | None, Field(gt=0, allow_inf_nan=False)] = None,
        cg_tolerance_mm: Annotated[float | None, Field(ge=0, allow_inf_nan=False)] = None,
        cg_x_range_mm: Annotated[list[Finite] | None, Field(min_length=2, max_length=2, description="Fixed-wing: allowed longitudinal CG band [min, max] mm, e.g. drone_fixed_wing_analysis stability.cg_range_for_margin_x_mm")] = None,
    ) -> dict:
        """Mass, CG and inertia budget from a listed bill of masses (point masses / solid boxes / supplied inertia tensors).

        Returns total mass, CG, CG offset from the reference point (usually the
        thrust centre), inertia tensor about the CG and principal moments. This
        is a budget from caller-listed items, NOT CAD mass-property extraction.
        """
        return mass_cg_budget(items, reference_point_mm, max_takeoff_mass_g, cg_tolerance_mm, cg_x_range_mm)

    @mcp.tool(name="propulsion_estimate", annotations=READ_ONLY)
    def propulsion_estimate_tool(
        all_up_mass_g: Annotated[float, Field(gt=0, le=150_000, allow_inf_nan=False)],
        rotor_count: Annotated[int, Field(ge=1, le=16)],
        prop_diameter_in: Annotated[float, Field(gt=0, le=80, allow_inf_nan=False)],
        battery_cells: Annotated[int, Field(ge=1, le=24)],
        battery_capacity_mah: Annotated[float, Field(gt=0, le=200_000, allow_inf_nan=False)],
        battery_chemistry: Literal["lipo", "lihv", "liion", "lifepo4"] = "lipo",
        usable_capacity_fraction: Annotated[float, Field(gt=0, le=1, allow_inf_nan=False)] = 0.8,
        figure_of_merit: Annotated[float, Field(ge=0.3, le=0.85, allow_inf_nan=False)] = 0.6,
        motor_efficiency: Annotated[float, Field(ge=0.3, le=0.98, allow_inf_nan=False)] = 0.8,
        esc_efficiency: Annotated[float, Field(ge=0.5, le=0.995, allow_inf_nan=False)] = 0.95,
        avionics_power_w: Annotated[float, Field(ge=0, le=2000, allow_inf_nan=False)] = 5.0,
        altitude_m: Annotated[float, Field(ge=-500, le=11000, allow_inf_nan=False)] = 0.0,
        temperature_offset_c: Annotated[float, Field(ge=-60, le=60, allow_inf_nan=False)] = 0.0,
        coaxial: bool = False,
        max_thrust_per_motor_g: Annotated[float | None, Field(gt=0, le=1_000_000, allow_inf_nan=False)] = None,
        motor_max_current_a: Annotated[float | None, Field(gt=0, le=1000, allow_inf_nan=False)] = None,
        battery_c_rating: Annotated[float | None, Field(gt=0, le=1000, allow_inf_nan=False)] = None,
    ) -> dict:
        """HEURISTIC multirotor hover sizing: thrust per rotor, hover power/current, flight time, TWR, ESC and C-rate.

        Momentum theory with a figure of merit and lumped motor/ESC efficiency.
        Thrust-to-weight is reported only from a caller-supplied datasheet max
        thrust per motor. Not valid for fixed-wing cruise or forward-flight endurance.
        """
        return propulsion_estimate(
            all_up_mass_g=all_up_mass_g, rotor_count=rotor_count, prop_diameter_in=prop_diameter_in,
            battery_cells=battery_cells, battery_capacity_mah=battery_capacity_mah,
            battery_chemistry=battery_chemistry, usable_capacity_fraction=usable_capacity_fraction,
            figure_of_merit=figure_of_merit, motor_efficiency=motor_efficiency,
            esc_efficiency=esc_efficiency, avionics_power_w=avionics_power_w, altitude_m=altitude_m,
            temperature_offset_c=temperature_offset_c, coaxial=coaxial,
            max_thrust_per_motor_g=max_thrust_per_motor_g, motor_max_current_a=motor_max_current_a,
            battery_c_rating=battery_c_rating)

    @mcp.tool(name="fixed_wing_analysis", annotations=READ_ONLY)
    def fixed_wing_analysis_tool(aircraft: FixedWingInput) -> dict:
        """HEURISTIC fixed-wing UAV analysis: planform, tail volumes (V-tail via Purser-Campbell), neutral point and static margin, parasite-drag build-up, polar, stall/min-power/min-drag speeds, electric endurance/range, climb, static thrust-to-weight and spar root stress, with pass/fail checks.

        Closed-form textbook methods (Raymer; NACA Rep. 823; Munk/Lamb); every
        result lists its assumptions and sources. Cross-check with
        airframe_aero_crosscheck (AeroSandbox) and flight data.
        """
        return fixed_wing_analyze(aircraft)

    @mcp.tool(name="rf_antenna_layout_check", annotations=READ_ONLY)
    def rf_antenna_layout_check_tool(layout: LayoutInput) -> dict:
        """HEURISTIC antenna-layout check for vehicle trackers and small RF nodes: monopole orientation vs 'up',
        electrical length vs a quarter wave at the lowest band, cellular main/diversity spacing in wavelengths,
        GNSS patch sky view and clearance from cellular elements. Geometry rules only, not RF performance."""
        return antenna_layout_check(layout)

    @mcp.tool(name="regulatory_weight_class", annotations=READ_ONLY)
    def regulatory_weight_class_tool(
        takeoff_mass_g: Annotated[float, Field(gt=0, le=1_000_000, allow_inf_nan=False)],
        jurisdiction: Literal["US", "EU"],
        use: Literal["recreational", "commercial"] = "recreational",
    ) -> dict:
        """Coarse FAA/EASA weight-band, registration and Remote ID notes for a takeoff mass. NOT legal advice."""
        return regulatory_weight_class(takeoff_mass_g, jurisdiction, use)

    return mcp
