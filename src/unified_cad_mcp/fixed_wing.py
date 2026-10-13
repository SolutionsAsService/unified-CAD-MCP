"""Pure-Python first-order fixed-wing UAV analysis (drone_fixed_wing_analysis).

Deterministic closed-form conceptual-design methods: planform geometry, tail volume
coefficients (V-tail via Purser & Campbell), neutral point and static margin,
component parasite-drag build-up, drag polar, stall / best-L/D / minimum-power
speeds, electric endurance and range, climb, static thrust and a wing-spar root
bending check. Every result carries its formulas, assumptions and sources. These are
HEURISTIC concept estimates: validate with VLM/CFD, wind-tunnel or flight data.

Frame: x aft from a caller-chosen datum (usually the nose), y spanwise, z up, mm.
"""
from __future__ import annotations

import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

G0 = 9.80665  # m/s^2, standard gravity (ISO 80000-3 / CGPM 1901)
R_AIR = 287.05287  # J/(kg K), ISA specific gas constant (ICAO Doc 7488)
GAMMA_AIR = 1.4
MM = 1e-3

Finite = Annotated[float, Field(allow_inf_nan=False)]
Positive = Annotated[float, Field(gt=0, allow_inf_nan=False)]
Fraction = Annotated[float, Field(gt=0, le=1, allow_inf_nan=False)]

ESTIMATE_KIND = ("HEURISTIC conceptual-design estimate (closed-form textbook methods). "
                 "Validate with VLM/CFD (e.g. airframe_aero_crosscheck), bench thrust data and flight logs.")

SOURCES = {
    "raymer": "D. P. Raymer, Aircraft Design: A Conceptual Approach, 6th ed., AIAA, 2018 - ch. 6 (tail volume coefficients), ch. 12 (lift-curve slope, component drag build-up, skin friction, form factors, Oswald efficiency), ch. 16 (neutral point / static margin)",
    "purser_campbell": "P. E. Purser and J. P. Campbell, Experimental verification of a simplified vee-tail theory and analysis of available data on complete models with vee tails, NACA Report 823, 1945",
    "munk": "M. M. Munk, The aerodynamic forces on airship hulls, NACA Report 184, 1924 (slender-body destabilizing moment with apparent-mass factor k2-k1)",
    "lamb": "H. Lamb, Hydrodynamics, 6th ed., 1932, sec. 373 (prolate-spheroid apparent-mass coefficients)",
    "isa": "ICAO Doc 7488/3, Manual of the ICAO Standard Atmosphere (troposphere); Sutherland viscosity law (mu0 = 1.458e-6, S = 110.4 K)",
    "hepperle": "M. Hepperle, Electric Flight - Potential and Limitations, NATO STO-MP-AVT-209, 2012 (battery-electric endurance/range = usable energy / electrical power)",
    "momentum": "Actuator-disk momentum theory, e.g. J. G. Leishman, Principles of Helicopter Aerodynamics, 2nd ed., 2006, ch. 2 (static thrust from shaft power with a figure of merit)",
    "fs": "Factor of safety 1.5 on limit loads: 14 CFR 23 (former sec. 23.303) / CS-23 convention, adopted here for a small UAS",
}


class Station(BaseModel):
    model_config = ConfigDict(extra="forbid")
    s_mm: Finite = Field(ge=0, description="Spanwise station along the surface span from its root (wing: y from centreline)")
    x_le_mm: Finite
    chord_mm: Positive


class Surface(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stations: list[Station] = Field(min_length=2, max_length=20)
    thickness_ratio: Annotated[float, Field(gt=0.02, lt=0.3, allow_inf_nan=False)] = 0.12
    max_thickness_x_c: Annotated[float, Field(gt=0.1, lt=0.6, allow_inf_nan=False)] = 0.3
    airfoil_cl_alpha_per_rad: Annotated[float, Field(gt=3, lt=7.5, allow_inf_nan=False)] = 6.0

    @model_validator(mode="after")
    def ascending(self):
        s = [st.s_mm for st in self.stations]
        if s[0] != 0 or any(b <= a for a, b in zip(s, s[1:])):
            raise ValueError("stations must start at s_mm=0 and strictly increase")
        return self


class Wing(Surface):
    cl_max: Annotated[float, Field(gt=0.5, lt=2.5, allow_inf_nan=False)] = 1.2


class TailSurface(Surface):
    role: Literal["v_tail", "horizontal", "vertical"]
    dihedral_deg: Finite = Field(default=0, ge=-90, le=90, description="V-tail panel angle from horizontal; negative = inverted V (Λ). Purser-Campbell areas use cos²/sin², so the sign only changes roll-yaw coupling, which is not modelled")
    efficiency: Annotated[float, Field(gt=0.3, le=1.0, allow_inf_nan=False)] = 0.9


class Fuselage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    length_mm: Positive
    max_width_mm: Positive
    max_height_mm: Positive
    wetted_area_mm2: Positive | None = Field(default=None, description="From CAD if available")
    volume_mm3: Positive | None = Field(default=None, description="From CAD if available")
    laminar_fraction: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)] = 0.0


class ExtraDrag(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    drag_area_mm2: Positive = Field(description="CD*S of the item, mm^2")


class Battery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cells_series: int = Field(ge=1, le=24)
    cells_parallel: int = Field(ge=1, le=20)
    cell_capacity_mah: Positive
    cell_nominal_v: Annotated[float, Field(gt=1, lt=5, allow_inf_nan=False)] = 3.6
    usable_fraction: Fraction = 0.8
    reserve_fraction: Annotated[float, Field(ge=0, lt=1, allow_inf_nan=False)] = 0.2


class Propulsion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prop_diameter_in: Annotated[float, Field(gt=2, le=40, allow_inf_nan=False)]
    prop_efficiency_cruise: Annotated[float, Field(gt=0.2, le=0.9, allow_inf_nan=False)] = 0.65
    prop_efficiency_climb: Annotated[float, Field(gt=0.2, le=0.9, allow_inf_nan=False)] = 0.55
    motor_efficiency: Annotated[float, Field(gt=0.3, le=0.98, allow_inf_nan=False)] = 0.85
    esc_efficiency: Annotated[float, Field(gt=0.5, le=0.995, allow_inf_nan=False)] = 0.95
    max_shaft_power_w: Positive | None = None
    static_figure_of_merit: Annotated[float, Field(gt=0.2, le=0.85, allow_inf_nan=False)] = 0.5
    datasheet_static_thrust_g: Positive | None = None


class Spar(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outer_diameter_mm: Positive
    inner_diameter_mm: Annotated[float, Field(ge=0, allow_inf_nan=False)] = 0
    allowable_stress_mpa: Positive = 400
    modulus_gpa: Positive = 100


class Requirements(BaseModel):
    model_config = ConfigDict(extra="forbid")
    static_margin_min: Finite = 0.05
    static_margin_max: Finite = 0.15
    max_stall_speed_m_s: Positive | None = None
    min_endurance_min: Positive | None = None
    min_thrust_to_weight: Positive | None = None
    tail_volume_h_range: tuple[Finite, Finite] = (0.35, 0.70)
    tail_volume_v_range: tuple[Finite, Finite] = (0.02, 0.05)


class LaunchMount(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, Field(min_length=1, max_length=120)]
    mass_g: Positive
    allowable_n: Positive


class Launch(BaseModel):
    """Catapult / rail launch case: axial acceleration on the airframe and on every listed mount."""
    model_config = ConfigDict(extra="forbid")
    accel_g: Annotated[float, Field(gt=0, le=60, allow_inf_nan=False)]
    end_speed_m_s: Positive
    min_speed_over_stall: Annotated[float, Field(ge=1.0, le=2.0, allow_inf_nan=False)] = 1.2
    max_stroke_m: Positive | None = None
    mounts: list[LaunchMount] = Field(default_factory=list, max_length=50)


class NeutralPointCrosscheck(BaseModel):
    """An independent neutral point (e.g. AeroSandbox VLM) to confirm the closed form, which matters for swept wings."""
    model_config = ConfigDict(extra="forbid")
    neutral_point_x_mm: Finite
    source: Annotated[str, Field(min_length=1, max_length=120)]
    tolerance_mac: Annotated[float, Field(gt=0, le=0.5, allow_inf_nan=False)] = 0.05
    includes_fuselage: bool = Field(default=False, description="VLM / lifting-surface methods usually omit the fuselage; then the "
                                    "comparison uses the closed-form wing+tail neutral point and the closed-form fuselage shift is "
                                    "added to the reference before computing its static margin")


class FixedWingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mass_g: Positive
    cg_x_mm: Finite
    wing: Wing
    tails: list[TailSurface] = Field(min_length=1, max_length=4)
    fuselage: Fuselage
    battery: Battery
    propulsion: Propulsion
    avionics_power_w: Annotated[float, Field(ge=0, le=5000, allow_inf_nan=False)] = 6.0
    payload_power_w: Annotated[float, Field(ge=0, le=5000, allow_inf_nan=False)] = 0.0
    cruise_speed_m_s: Positive | None = None
    altitude_m: Annotated[float, Field(ge=-500, le=11000, allow_inf_nan=False)] = 0.0
    temperature_offset_c: Annotated[float, Field(ge=-60, le=60, allow_inf_nan=False)] = 0.0
    misc_drag_fraction: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)] = 0.10
    wing_laminar_fraction: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)] = 0.0
    extra_drag: list[ExtraDrag] = Field(default_factory=list, max_length=50)
    limit_load_factor: Annotated[float, Field(ge=1, le=15, allow_inf_nan=False)] = 4.0
    safety_factor: Annotated[float, Field(ge=1, le=3, allow_inf_nan=False)] = 1.5
    spar: Spar | None = None
    launch: Launch | None = None
    neutral_point_crosscheck: NeutralPointCrosscheck | None = None
    requirements: Requirements = Field(default_factory=Requirements)


# ---------------------------------------------------------------- atmosphere

def atmosphere(altitude_m: float, temperature_offset_c: float = 0.0) -> dict:
    if not -500 <= altitude_m <= 11000:
        raise ValueError("altitude_m must be within -500..11000 (ISA troposphere)")
    t_isa = 288.15 - 0.0065 * altitude_m
    pressure = 101325.0 * (t_isa / 288.15) ** 5.25588
    t = t_isa + temperature_offset_c
    rho = pressure / (R_AIR * t)
    mu = 1.458e-6 * t ** 1.5 / (t + 110.4)
    return {"temperature_k": t, "pressure_pa": pressure, "density_kg_m3": rho,
            "dynamic_viscosity_pa_s": mu, "speed_of_sound_m_s": math.sqrt(GAMMA_AIR * R_AIR * t)}


# ---------------------------------------------------------------- planform

def planform(stations: list[Station], mirrored: bool = True) -> dict:
    """Exact integrals for a piecewise-linear (trapezoidal-panel) planform, all in mm."""
    half_area = c2 = xc = yc = sweep_w = 0.0
    for a, b in zip(stations, stations[1:]):
        dy = b.s_mm - a.s_mm
        area = dy * (a.chord_mm + b.chord_mm) / 2
        half_area += area
        c2 += dy * (a.chord_mm ** 2 + a.chord_mm * b.chord_mm + b.chord_mm ** 2) / 3
        xc += dy * (2 * a.x_le_mm * a.chord_mm + a.x_le_mm * b.chord_mm + b.x_le_mm * a.chord_mm + 2 * b.x_le_mm * b.chord_mm) / 6
        yc += dy * (a.s_mm * (2 * a.chord_mm + b.chord_mm) + b.s_mm * (a.chord_mm + 2 * b.chord_mm)) / 6
        mid_a, mid_b = a.x_le_mm + a.chord_mm / 2, b.x_le_mm + b.chord_mm / 2
        sweep_w += area * math.atan2(mid_b - mid_a, dy)
    semi = stations[-1].s_mm
    span = 2 * semi if mirrored else semi
    area = 2 * half_area if mirrored else half_area
    mac = c2 / half_area
    return {
        "area_mm2": area, "span_mm": span, "aspect_ratio": span ** 2 / area, "mac_mm": mac,
        "mac_x_le_mm": xc / half_area, "mac_s_mm": yc / half_area,
        "sweep_half_chord_deg": math.degrees(sweep_w / half_area),
        "taper_ratio": stations[-1].chord_mm / stations[0].chord_mm,
        "root_chord_mm": stations[0].chord_mm, "tip_chord_mm": stations[-1].chord_mm,
    }


def chord_at(stations: list[Station], s_mm: float) -> float:
    for a, b in zip(stations, stations[1:]):
        if a.s_mm <= s_mm <= b.s_mm:
            t = (s_mm - a.s_mm) / (b.s_mm - a.s_mm)
            return a.chord_mm + t * (b.chord_mm - a.chord_mm)
    raise ValueError("station outside surface span")


def lift_curve_slope(aspect_ratio: float, airfoil_cl_alpha: float, sweep_half_chord_deg: float,
                     mach: float = 0.0, area_factor: float = 1.0) -> float:
    """Helmbold / DATCOM finite-wing lift-curve slope per rad (Raymer ch. 12)."""
    beta2 = 1 - mach ** 2
    eta = airfoil_cl_alpha / (2 * math.pi / math.sqrt(beta2))
    tan2 = math.tan(math.radians(sweep_half_chord_deg)) ** 2
    a = aspect_ratio
    return 2 * math.pi * a / (2 + math.sqrt(4 + (a ** 2 * beta2 / eta ** 2) * (1 + tan2 / beta2))) * area_factor


def spheroid_apparent_mass(fineness: float) -> tuple[float, float]:
    """Lamb's axial (k1) and transverse (k2) apparent-mass coefficients of a prolate spheroid."""
    if fineness <= 1.0001:
        return 0.5, 0.5
    e = math.sqrt(1 - 1 / fineness ** 2)
    log = math.log((1 + e) / (1 - e))
    alpha0 = 2 * (1 - e ** 2) / e ** 3 * (0.5 * log - e)
    beta0 = 1 / e ** 2 - (1 - e ** 2) / (2 * e ** 3) * log
    return alpha0 / (2 - alpha0), beta0 / (2 - beta0)


def skin_friction(reynolds: float, laminar_fraction: float, mach: float) -> float:
    """Raymer ch. 12 flat-plate skin friction: Blasius laminar blended with turbulent (Schlichting fit)."""
    reynolds = max(reynolds, 1e4)
    lam = 1.328 / math.sqrt(reynolds)
    turb = 0.455 / (math.log10(reynolds) ** 2.58 * (1 + 0.144 * mach ** 2) ** 0.65)
    return laminar_fraction * lam + (1 - laminar_fraction) * turb


def lifting_form_factor(t_c: float, x_t: float, sweep_deg: float, mach: float) -> float:
    """Raymer ch. 12 wing/tail form factor; the Mach term is applied only for M >= 0.2 (it vanishes as M -> 0)."""
    ff = 1 + 0.6 / x_t * t_c + 100 * t_c ** 4
    if mach >= 0.2:
        ff *= 1.34 * mach ** 0.18 * math.cos(math.radians(sweep_deg)) ** 0.28
    return ff


# ---------------------------------------------------------------- analysis

def analyze(data: FixedWingInput) -> dict:
    atm = atmosphere(data.altitude_m, data.temperature_offset_c)
    rho, mu, a_snd = atm["density_kg_m3"], atm["dynamic_viscosity_pa_s"], atm["speed_of_sound_m_s"]
    warnings: list[str] = []
    w = data.wing
    wp = planform(w.stations, mirrored=True)
    S = wp["area_mm2"] * MM ** 2
    b = wp["span_mm"] * MM
    c = wp["mac_mm"] * MM
    AR = wp["aspect_ratio"]
    x_ac_w = wp["mac_x_le_mm"] + 0.25 * wp["mac_mm"]
    fus = data.fuselage
    d_fus = math.sqrt(fus.max_width_mm * fus.max_height_mm)  # equivalent diameter, mm

    root_cover = 2 * (fus.max_width_mm / 2) * chord_at(w.stations, min(fus.max_width_mm / 2, w.stations[-1].s_mm))
    s_exposed = wp["area_mm2"] - root_cover
    f_factor = 1.07 * (1 + d_fus / wp["span_mm"]) ** 2
    area_factor = min(0.98, s_exposed / wp["area_mm2"] * f_factor)
    cla_w = lift_curve_slope(AR, w.airfoil_cl_alpha_per_rad, wp["sweep_half_chord_deg"], 0.0, area_factor)
    downwash = 2 * cla_w / (math.pi * AR)

    tail_rows = []
    pitch_num = pitch_den = 0.0
    s_h_eff = s_v_eff = 0.0
    x_ac_h_weighted = x_ac_v_weighted = 0.0
    tail_wetted = []
    for t in data.tails:
        mirrored = t.role != "vertical"
        tp = planform(t.stations, mirrored=mirrored)
        area = tp["area_mm2"]
        x_ac = tp["mac_x_le_mm"] + 0.25 * tp["mac_mm"]
        ar_unfolded = (2 * t.stations[-1].s_mm) ** 2 / area if mirrored else 2 * t.stations[-1].s_mm ** 2 / area
        cla_t = lift_curve_slope(ar_unfolded, t.airfoil_cl_alpha_per_rad, tp["sweep_half_chord_deg"])
        if t.role == "v_tail":
            gamma = math.radians(t.dihedral_deg)
            h_factor, v_factor = math.cos(gamma) ** 2, math.sin(gamma) ** 2
        elif t.role == "horizontal":
            h_factor, v_factor = 1.0, 0.0
        else:
            h_factor, v_factor = 0.0, 1.0
        s_h, s_v = area * h_factor, area * v_factor
        s_h_eff += s_h
        s_v_eff += s_v
        x_ac_h_weighted += s_h * x_ac
        x_ac_v_weighted += s_v * x_ac
        term = t.efficiency * (s_h / wp["area_mm2"]) * cla_t * (1 - downwash)
        pitch_num += term * x_ac
        pitch_den += term
        tail_rows.append({"role": t.role, "area_mm2": round(area, 1), "span_mm": round(tp["span_mm"], 2),
                          "mac_mm": round(tp["mac_mm"], 2), "x_ac_mm": round(x_ac, 2),
                          "aspect_ratio_unfolded": round(ar_unfolded, 3), "cl_alpha_per_rad": round(cla_t, 4),
                          "effective_horizontal_area_mm2": round(s_h, 1), "effective_vertical_area_mm2": round(s_v, 1),
                          "dihedral_deg": t.dihedral_deg, "inverted_v": t.role == "v_tail" and t.dihedral_deg < 0})
        tail_wetted.append((t, tp, area * (1.977 + 0.52 * t.thickness_ratio)))
    x_ac_h = x_ac_h_weighted / s_h_eff if s_h_eff else None
    x_ac_v = x_ac_v_weighted / s_v_eff if s_v_eff else None
    v_h = s_h_eff * (x_ac_h - x_ac_w) / (wp["area_mm2"] * wp["mac_mm"]) if s_h_eff else 0.0
    v_v = s_v_eff * (x_ac_v - x_ac_w) / (wp["area_mm2"] * wp["span_mm"]) if s_v_eff else 0.0

    fineness = fus.length_mm / d_fus
    k1, k2 = spheroid_apparent_mass(fineness)
    vol_fus = fus.volume_mm3 or math.pi / 6 * fus.length_mm * fus.max_width_mm * fus.max_height_mm
    cma_fus = 2 * (k2 - k1) * vol_fus / (wp["area_mm2"] * wp["mac_mm"])  # per rad, positive = destabilizing
    x_np = (cla_w * x_ac_w - cma_fus * wp["mac_mm"] + pitch_num) / (cla_w + pitch_den)
    static_margin = (x_np - data.cg_x_mm) / wp["mac_mm"]
    x_np_no_fus = (cla_w * x_ac_w + pitch_num) / (cla_w + pitch_den)
    cla_total = cla_w + pitch_den

    W = data.mass_g / 1000 * G0
    v_ref = data.cruise_speed_m_s or math.sqrt(2 * W / (rho * S * 0.6))
    mach = v_ref / a_snd
    rows = []

    def add(name, cf, ff, q, swet_mm2):
        cd = cf * ff * q * swet_mm2 / wp["area_mm2"]
        rows.append({"component": name, "cf": round(cf, 6), "form_factor": round(ff, 4),
                     "interference_q": q, "wetted_area_mm2": round(swet_mm2, 1), "cd0_contribution": round(cd, 6)})
        return cd

    swet_w = s_exposed * (1.977 + 0.52 * w.thickness_ratio)
    re_w = rho * v_ref * c / mu
    cd0 = add("wing", skin_friction(re_w, data.wing_laminar_fraction, mach),
              lifting_form_factor(w.thickness_ratio, w.max_thickness_x_c, wp["sweep_half_chord_deg"], mach), 1.0, swet_w)
    for t, tp, swet in tail_wetted:
        re_t = rho * v_ref * tp["mac_mm"] * MM / mu
        cd0 += add(f"tail ({t.role})", skin_friction(re_t, data.wing_laminar_fraction, mach),
                   lifting_form_factor(t.thickness_ratio, t.max_thickness_x_c, tp["sweep_half_chord_deg"], mach),
                   1.03 if t.role == "v_tail" else 1.04, swet)
    swet_f = fus.wetted_area_mm2 or math.pi * d_fus * fus.length_mm * 0.75
    re_f = rho * v_ref * fus.length_mm * MM / mu
    cd0 += add("fuselage", skin_friction(re_f, fus.laminar_fraction, mach), 1 + 60 / fineness ** 3 + fineness / 400, 1.0, swet_f)
    extra_cd = sum(e.drag_area_mm2 for e in data.extra_drag) / wp["area_mm2"]
    for e in data.extra_drag:
        rows.append({"component": e.name, "drag_area_mm2": e.drag_area_mm2, "cd0_contribution": round(e.drag_area_mm2 / wp["area_mm2"], 6)})
    cd0_total = (cd0 + extra_cd) * (1 + data.misc_drag_fraction)
    oswald = 1.78 * (1 - 0.045 * AR ** 0.68) - 0.64
    if not 0.5 <= oswald <= 0.95:
        warnings.append(f"Oswald estimate {oswald:.3f} outside 0.5-0.95; Raymer straight-wing fit is outside its range")
        oswald = min(0.95, max(0.5, oswald))
    K = 1 / (math.pi * AR * oswald)

    q_factor = 2 * W / (rho * S)
    v_stall = math.sqrt(q_factor / w.cl_max)
    v_min_drag = math.sqrt(q_factor) * (K / cd0_total) ** 0.25
    v_min_power = v_min_drag / 3 ** 0.25
    v_floor = 1.3 * v_stall
    ld_max = 0.5 * math.sqrt(1 / (K * cd0_total))
    prop = data.propulsion
    eta_chain = prop.prop_efficiency_cruise * prop.motor_efficiency * prop.esc_efficiency
    hotel = data.avionics_power_w + data.payload_power_w

    def p_req(v):
        return 0.5 * rho * v ** 3 * S * cd0_total + 2 * K * W ** 2 / (rho * v * S)

    def p_elec(v):
        return p_req(v) / eta_chain + hotel

    v_loiter = max(v_min_power, v_floor)
    v_cruise = data.cruise_speed_m_s or max(v_min_drag, v_floor)
    if data.cruise_speed_m_s and data.cruise_speed_m_s < v_floor:
        warnings.append("cruise_speed_m_s is below 1.3 x stall speed")
    bat = data.battery
    pack_v = bat.cells_series * bat.cell_nominal_v
    pack_wh = pack_v * bat.cells_parallel * bat.cell_capacity_mah / 1000
    usable_wh = pack_wh * bat.usable_fraction
    mission_wh = usable_wh * (1 - bat.reserve_fraction)

    def point(v, label):
        cl = q_factor / v ** 2
        pe = p_elec(v)
        cd = cd0_total + K * cl ** 2
        return {"label": label, "speed_m_s": round(v, 3), "cl": round(cl, 4), "cd": round(cd, 5),
                "lift_to_drag": round(cl / cd, 3), "aero_power_w": round(p_req(v), 2),
                "electrical_power_w": round(pe, 2), "battery_current_a": round(pe / pack_v, 3),
                "endurance_min_to_reserve": round(mission_wh / pe * 60, 1),
                "range_km_to_reserve": round(v * mission_wh / pe * 3.6, 2),
                "reynolds_mac": round(rho * v * c / mu)}

    curve = [point(v_floor * f, "sweep") for f in (1.0, 1.15, 1.3, 1.5, 1.75, 2.0, 2.4)]
    loiter = point(v_loiter, "loiter (max endurance, >= 1.3 Vs)")
    cruise = point(v_cruise, "cruise (requested)" if data.cruise_speed_m_s else "cruise (best range at constant efficiency, >= 1.3 Vs)")

    disk = math.pi * (prop.prop_diameter_in * 0.0254 / 2) ** 2
    perf_extra = {}
    thrust_n = None
    if prop.datasheet_static_thrust_g:
        thrust_n = prop.datasheet_static_thrust_g / 1000 * G0
        perf_extra["static_thrust_source"] = "datasheet (caller supplied)"
    elif prop.max_shaft_power_w:
        thrust_n = (2 * rho * disk) ** (1 / 3) * (prop.static_figure_of_merit * prop.max_shaft_power_w) ** (2 / 3)
        perf_extra["static_thrust_source"] = "momentum theory with static figure of merit (HEURISTIC)"
    if thrust_n is not None:
        perf_extra["static_thrust_g"] = round(thrust_n / G0 * 1000, 1)
        perf_extra["static_thrust_to_weight"] = round(thrust_n / W, 3)
    if prop.max_shaft_power_w:
        roc = (prop.prop_efficiency_climb * prop.max_shaft_power_w - p_req(v_loiter)) / W
        perf_extra["max_climb_rate_m_s"] = round(roc, 3)
        perf_extra["climb_speed_m_s"] = round(v_loiter, 3)
        max_e = prop.max_shaft_power_w / (prop.motor_efficiency * prop.esc_efficiency) + hotel
        perf_extra["max_electrical_power_w"] = round(max_e, 1)
        perf_extra["max_battery_current_a"] = round(max_e / pack_v, 2)
        perf_extra["required_continuous_c_rating"] = round(max_e / pack_v / (bat.cells_parallel * bat.cell_capacity_mah / 1000), 2)
        perf_extra["power_loading_w_per_kg"] = round(prop.max_shaft_power_w / (data.mass_g / 1000), 1)

    structure = {"limit_load_factor": data.limit_load_factor, "safety_factor": data.safety_factor}
    n_ult = data.limit_load_factor * data.safety_factor
    semi = wp["span_mm"] / 2 * MM
    m_root = n_ult * W / 2 * (4 * semi / (3 * math.pi))
    structure["root_bending_moment_ultimate_n_m"] = round(m_root, 2)
    if data.spar:
        od, idd = data.spar.outer_diameter_mm * MM, data.spar.inner_diameter_mm * MM
        if idd >= od:
            raise ValueError("spar inner_diameter_mm must be smaller than outer_diameter_mm")
        inertia = math.pi * (od ** 4 - idd ** 4) / 64
        sigma = m_root * (od / 2) / inertia / 1e6
        structure.update({
            "spar_second_moment_mm4": round(inertia / MM ** 4, 1),
            "spar_root_stress_ultimate_mpa": round(sigma, 1),
            "spar_allowable_stress_mpa": data.spar.allowable_stress_mpa,
            "spar_margin_of_safety": round(data.spar.allowable_stress_mpa / sigma - 1, 3),
            "spar_tip_deflection_limit_load_mm": round(data.limit_load_factor * W / 2 / semi * semi ** 4 / (8 * data.spar.modulus_gpa * 1e9 * inertia) / MM, 1),
            "spar_model": "single tube carries all bending over the full semispan (conservative; skins and shear webs ignored); tip deflection assumes a uniform tube under uniform load",
        })

    launch = None
    if data.launch:
        L = data.launch
        a = L.accel_g * 9.80665
        launch = {"accel_g": L.accel_g, "end_speed_m_s": L.end_speed_m_s,
                  "stroke_m": round(L.end_speed_m_s ** 2 / (2 * a), 3),
                  "end_speed_over_stall": round(L.end_speed_m_s / v_stall, 3),
                  "airframe_axial_load_ultimate_n": round(data.mass_g / 1000 * a * data.safety_factor, 1),
                  "mounts": [{"name": m.name, "load_ultimate_n": round(m.mass_g / 1000 * a * data.safety_factor, 2),
                              "allowable_n": m.allowable_n,
                              "margin_of_safety": round(m.allowable_n / (m.mass_g / 1000 * a * data.safety_factor) - 1, 3)}
                             for m in L.mounts],
                  "model": "rigid body, constant acceleration over the stroke, ultimate = mass x accel x safety factor"}
    np_check = None
    if data.neutral_point_crosscheck:
        c = data.neutral_point_crosscheck
        closed = x_np if c.includes_fuselage else x_np_no_fus
        fus_shift = 0.0 if c.includes_fuselage else x_np - x_np_no_fus
        diff = (closed - c.neutral_point_x_mm) / wp["mac_mm"]
        ref_full = c.neutral_point_x_mm + fus_shift
        np_check = {"closed_form_x_mm": round(closed, 2), "closed_form_basis": "with fuselage" if c.includes_fuselage else "wing + tail (no fuselage), like the reference",
                    "reference_x_mm": c.neutral_point_x_mm, "source": c.source,
                    "difference_mac": round(diff, 4), "tolerance_mac": c.tolerance_mac,
                    "reference_with_fuselage_shift_x_mm": round(ref_full, 2),
                    "static_margin_with_reference": round((ref_full - data.cg_x_mm) / wp["mac_mm"], 4)}

    checks = []
    req = data.requirements

    def check(name, value, ok, limit):
        checks.append({"name": name, "value": value, "limit": limit, "pass": bool(ok)})

    check("static_margin", round(static_margin, 4), req.static_margin_min <= static_margin <= req.static_margin_max,
          [req.static_margin_min, req.static_margin_max])
    check("horizontal_tail_volume", round(v_h, 4), req.tail_volume_h_range[0] <= v_h <= req.tail_volume_h_range[1], list(req.tail_volume_h_range))
    check("vertical_tail_volume", round(v_v, 5), req.tail_volume_v_range[0] <= v_v <= req.tail_volume_v_range[1], list(req.tail_volume_v_range))
    if req.max_stall_speed_m_s:
        check("stall_speed_m_s", round(v_stall, 3), v_stall <= req.max_stall_speed_m_s, req.max_stall_speed_m_s)
    if req.min_endurance_min:
        check("loiter_endurance_min_to_reserve", loiter["endurance_min_to_reserve"], loiter["endurance_min_to_reserve"] >= req.min_endurance_min, req.min_endurance_min)
    if req.min_thrust_to_weight and "static_thrust_to_weight" in perf_extra:
        check("static_thrust_to_weight", perf_extra["static_thrust_to_weight"], perf_extra["static_thrust_to_weight"] >= req.min_thrust_to_weight, req.min_thrust_to_weight)
    if "spar_margin_of_safety" in structure:
        check("spar_margin_of_safety", structure["spar_margin_of_safety"], structure["spar_margin_of_safety"] >= 0, 0)
    if launch:
        L = data.launch
        check("launch_end_speed_over_stall", launch["end_speed_over_stall"], launch["end_speed_over_stall"] >= L.min_speed_over_stall, L.min_speed_over_stall)
        if L.max_stroke_m:
            check("launch_stroke_m", launch["stroke_m"], launch["stroke_m"] <= L.max_stroke_m, L.max_stroke_m)
        for m in launch["mounts"]:
            check("launch_mount_margin: " + m["name"], m["margin_of_safety"], m["margin_of_safety"] >= 0, 0)
    if np_check:
        check("neutral_point_crosscheck_mac", np_check["difference_mac"], abs(np_check["difference_mac"]) <= np_check["tolerance_mac"], np_check["tolerance_mac"])
        sm_ref = np_check["static_margin_with_reference"]
        check("static_margin_with_reference_np", sm_ref, req.static_margin_min <= sm_ref <= req.static_margin_max,
              [req.static_margin_min, req.static_margin_max])
    for item in checks:
        if not item["pass"]:
            warnings.append(f"Check failed: {item['name']} = {item['value']} (limit {item['limit']})")

    return {
        "estimate_kind": ESTIMATE_KIND,
        "atmosphere": {k: round(v, 6) for k, v in atm.items()},
        "geometry": {
            "wing_area_m2": round(S, 5), "wing_span_m": round(b, 4), "aspect_ratio": round(AR, 4),
            "mac_mm": round(wp["mac_mm"], 3), "mac_x_le_mm": round(wp["mac_x_le_mm"], 3),
            "mac_spanwise_station_mm": round(wp["mac_s_mm"], 2), "taper_ratio": round(wp["taper_ratio"], 4),
            "sweep_half_chord_deg": round(wp["sweep_half_chord_deg"], 3), "wing_ac_x_mm": round(x_ac_w, 3),
            "wing_exposed_area_m2": round(s_exposed * MM ** 2, 5),
            "tails": tail_rows,
            "effective_horizontal_tail_area_m2": round(s_h_eff * MM ** 2, 5),
            "effective_vertical_tail_area_m2": round(s_v_eff * MM ** 2, 5),
            "horizontal_tail_volume": round(v_h, 4), "vertical_tail_volume": round(v_v, 5),
            "fuselage_fineness_ratio": round(fineness, 3),
            "wing_loading_kg_m2": round(data.mass_g / 1000 / S, 3),
            "wing_loading_g_dm2": round(data.mass_g / (S * 100), 2),
        },
        "stability": {
            "wing_cl_alpha_per_rad": round(cla_w, 4), "downwash_gradient": round(downwash, 4),
            "aircraft_cl_alpha_per_rad": round(cla_total, 4),
            "fuselage_cm_alpha_per_rad": round(cma_fus, 5), "fuselage_apparent_mass_k2_minus_k1": round(k2 - k1, 4),
            "neutral_point_x_mm": round(x_np, 2), "neutral_point_without_fuselage_x_mm": round(x_np_no_fus, 2),
            "cg_x_mm": data.cg_x_mm, "static_margin_mac": round(static_margin, 4),
            "cg_range_for_margin_x_mm": [round(x_np - req.static_margin_max * wp["mac_mm"], 2),
                                         round(x_np - req.static_margin_min * wp["mac_mm"], 2)],
        },
        "aerodynamics": {
            "reference_speed_m_s": round(v_ref, 3), "mach": round(mach, 4),
            "cd0_components": rows, "misc_drag_fraction": data.misc_drag_fraction,
            "cd0": round(cd0_total, 6), "oswald_efficiency": round(oswald, 4), "induced_drag_factor_k": round(K, 6),
            "max_lift_to_drag": round(ld_max, 3), "cl_max": w.cl_max,
        },
        "performance": {
            "mass_kg": data.mass_g / 1000, "weight_n": round(W, 3),
            "stall_speed_m_s": round(v_stall, 3), "min_power_speed_m_s": round(v_min_power, 3),
            "min_drag_speed_m_s": round(v_min_drag, 3),
            "battery_pack_nominal_v": round(pack_v, 3), "battery_pack_energy_wh": round(pack_wh, 2),
            "usable_energy_wh": round(usable_wh, 2), "mission_energy_wh_to_reserve": round(mission_wh, 2),
            "powertrain_efficiency_cruise": round(eta_chain, 4), "hotel_power_w": hotel,
            "loiter": loiter, "cruise": cruise, "power_curve": curve, **perf_extra,
        },
        "structure": structure,
        **({"launch": launch} if launch else {}),
        **({"neutral_point_crosscheck": np_check} if np_check else {}),
        "checks": checks,
        "all_checks_pass": all(item["pass"] for item in checks),
        "assumptions": {
            "wing_ac": "25 % MAC (subsonic)",
            "lift_curve_slope": "Helmbold/DATCOM with fuselage lift factor F = 1.07(1+d/b)^2 x S_exposed/S (capped 0.98)",
            "downwash": "d(eps)/d(alpha) = 2 CL_alpha_w / (pi A), tail-height effects ignored",
            "v_tail": "Purser-Campbell: pitch uses S cos^2(Gamma), yaw uses S sin^2(Gamma); lift-curve slope from the unfolded panel aspect ratio",
            "fuselage_pitch": "Munk slender-body moment 2(k2-k1) Vol, Lamb spheroid k2-k1 at the fineness ratio; conservative (moves the neutral point forward)",
            "parasite_drag": "Raymer component build-up; wetted area of lifting surfaces = S_exposed (1.977 + 0.52 t/c); misc/leakage/protuberance as a fraction",
            "induced_drag": "Oswald e = 1.78(1 - 0.045 A^0.68) - 0.64 (Raymer straight-wing fit)",
            "speeds": "loiter = max(min-power speed, 1.3 Vs); cruise = max(min-drag speed, 1.3 Vs) unless requested",
            "energy": "endurance = usable energy x (1 - reserve) / electrical power; constant efficiencies, no battery sag, wind or climb segments",
            "static_thrust": "datasheet value if given, else momentum theory T = (2 rho A)^(1/3) (FM P)^(2/3)",
            "spar": "ultimate root moment n_ult (W/2)(4 s / 3 pi) for elliptic lift with no inertia relief",
        },
        "sources": SOURCES,
        "warnings": warnings,
    }
