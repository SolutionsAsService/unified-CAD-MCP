"""drone_fixed_wing_analysis: closed-form checks against independent textbook values."""
import math

from fastmcp import Client
import pytest

from unified_cad_mcp.config import Configuration
from unified_cad_mcp.fixed_wing import (FixedWingInput, Station, analyze, atmosphere, lift_curve_slope, planform,
                                        spheroid_apparent_mass)
from unified_cad_mcp.server import create_server


def st(s, x, c):
    return Station(s_mm=s, x_le_mm=x, chord_mm=c)


def test_rectangular_and_tapered_planform_integrals():
    rect = planform([st(0, 0, 200), st(1000, 0, 200)])
    assert rect["area_mm2"] == pytest.approx(400_000)
    assert rect["aspect_ratio"] == pytest.approx(10)
    assert rect["mac_mm"] == pytest.approx(200)
    assert rect["mac_s_mm"] == pytest.approx(500)
    cr, lam, semi = 300.0, 0.5, 1200.0
    taper = planform([st(0, 0, cr), st(semi, 100, cr * lam)])
    assert taper["mac_mm"] == pytest.approx(2 / 3 * cr * (1 + lam + lam ** 2) / (1 + lam))
    assert taper["mac_s_mm"] == pytest.approx(semi / 3 * (1 + 2 * lam) / (1 + lam))
    assert taper["mac_x_le_mm"] == pytest.approx(100 * taper["mac_s_mm"] / semi)
    # a two-panel planform equals the sum of its panels
    two = planform([st(0, 0, 300), st(600, 30, 240), st(1200, 60, 150)])
    assert two["area_mm2"] == pytest.approx(2 * (600 * 270 + 600 * 195))


def test_lift_curve_slope_limits_and_lamb_coefficients():
    assert lift_curve_slope(1e6, 2 * math.pi, 0) == pytest.approx(2 * math.pi, rel=1e-3)
    # Helmbold low-aspect-ratio limit -> pi A / 2
    assert lift_curve_slope(0.01, 2 * math.pi, 0) == pytest.approx(math.pi * 0.01 / 2, rel=1e-3)
    assert 4.0 < lift_curve_slope(6, 2 * math.pi, 0) < 4.7
    # Lamb, Hydrodynamics sec. 373 table (prolate spheroid): l/d = 4 -> k1 .082, k2 .860; 10 -> .021, .960
    for ratio, k1, k2 in [(4, 0.082, 0.860), (10, 0.021, 0.960), (2, 0.209, 0.702)]:
        a, b = spheroid_apparent_mass(ratio)
        assert a == pytest.approx(k1, abs=3e-3) and b == pytest.approx(k2, abs=3e-3)
    assert atmosphere(0)["density_kg_m3"] == pytest.approx(1.225, abs=1e-3)
    assert atmosphere(0)["dynamic_viscosity_pa_s"] == pytest.approx(1.789e-5, rel=2e-3)


def glider(**overrides):
    data = dict(
        mass_g=3500, cg_x_mm=620,
        wing=dict(stations=[dict(s_mm=0, x_le_mm=550, chord_mm=300), dict(s_mm=1500, x_le_mm=600, chord_mm=200)],
                  thickness_ratio=0.12, cl_max=1.2),
        tails=[dict(role="v_tail", dihedral_deg=40, stations=[dict(s_mm=0, x_le_mm=1350, chord_mm=250), dict(s_mm=450, x_le_mm=1430, chord_mm=170)],
                    thickness_ratio=0.09)],
        fuselage=dict(length_mm=1700, max_width_mm=120, max_height_mm=105),
        battery=dict(cells_series=6, cells_parallel=2, cell_capacity_mah=4500),
        propulsion=dict(prop_diameter_in=13, max_shaft_power_w=450),
        spar=dict(outer_diameter_mm=17, inner_diameter_mm=12))
    data.update(overrides)
    return analyze(FixedWingInput.model_validate(data))


def test_static_margin_moves_with_cg_and_tail():
    base = glider()
    s = base["stability"]
    mac = base["geometry"]["mac_mm"]
    assert s["static_margin_mac"] == pytest.approx((s["neutral_point_x_mm"] - 620) / mac, abs=1e-4)
    aft = glider(cg_x_mm=660)["stability"]
    assert aft["neutral_point_x_mm"] == pytest.approx(s["neutral_point_x_mm"])
    assert aft["static_margin_mac"] < s["static_margin_mac"]
    # neutral point lies between the wing AC and the tail AC, and the fuselage moves it forward
    assert base["geometry"]["wing_ac_x_mm"] < s["neutral_point_x_mm"] < base["geometry"]["tails"][0]["x_ac_mm"]
    assert s["neutral_point_x_mm"] < s["neutral_point_without_fuselage_x_mm"]
    flat = glider(tails=[dict(role="v_tail", dihedral_deg=60, stations=[dict(s_mm=0, x_le_mm=1350, chord_mm=250), dict(s_mm=450, x_le_mm=1430, chord_mm=170)])])
    assert flat["geometry"]["horizontal_tail_volume"] < base["geometry"]["horizontal_tail_volume"]
    assert flat["geometry"]["vertical_tail_volume"] > base["geometry"]["vertical_tail_volume"]
    # Purser-Campbell projections: cos^2 + sin^2 = full area
    t = base["geometry"]["tails"][0]
    assert t["effective_horizontal_area_mm2"] + t["effective_vertical_area_mm2"] == pytest.approx(t["area_mm2"], abs=0.2)


def test_performance_matches_independent_recomputation():
    r = glider()
    perf, aero, geo = r["performance"], r["aerodynamics"], r["geometry"]
    rho = r["atmosphere"]["density_kg_m3"]
    W = 3.5 * 9.80665
    S = geo["wing_area_m2"]
    assert perf["stall_speed_m_s"] == pytest.approx(math.sqrt(2 * W / (rho * S * 1.2)), rel=1e-4)
    K = 1 / (math.pi * geo["aspect_ratio"] * aero["oswald_efficiency"])
    assert aero["max_lift_to_drag"] == pytest.approx(0.5 / math.sqrt(K * aero["cd0"]), rel=1e-3)
    v = perf["loiter"]["speed_m_s"]
    p_aero = 0.5 * rho * v ** 3 * S * aero["cd0"] + 2 * K * W ** 2 / (rho * v * S)
    assert perf["loiter"]["aero_power_w"] == pytest.approx(p_aero, rel=1e-3)
    p_elec = p_aero / (0.65 * 0.85 * 0.95) + 6.0
    wh = 6 * 3.6 * 2 * 4.5 * 0.8 * 0.8
    assert perf["loiter"]["endurance_min_to_reserve"] == pytest.approx(wh / p_elec * 60, abs=0.06)
    disk = math.pi * (13 * 0.0254 / 2) ** 2
    thrust = (2 * rho * disk) ** (1 / 3) * (0.5 * 450) ** (2 / 3)
    assert perf["static_thrust_to_weight"] == pytest.approx(thrust / W, abs=1e-3)
    m_root = 6 * W / 2 * 4 * 1.5 / (3 * math.pi)
    sigma = m_root * 0.0085 / (math.pi * (0.017 ** 4 - 0.012 ** 4) / 64) / 1e6
    assert r["structure"]["spar_root_stress_ultimate_mpa"] == pytest.approx(sigma, rel=1e-3)
    assert 0.015 < aero["cd0"] < 0.05 and 0.6 < aero["oswald_efficiency"] < 0.9
    assert all({"name", "value", "limit", "pass"} <= set(c) for c in r["checks"])
    assert "raymer" in r["sources"] and "HEURISTIC" in r["estimate_kind"]


def test_invalid_inputs_are_rejected_clearly():
    with pytest.raises(ValueError, match="start at s_mm=0"):
        glider(wing=dict(stations=[dict(s_mm=10, x_le_mm=0, chord_mm=300), dict(s_mm=1500, x_le_mm=0, chord_mm=200)]))
    with pytest.raises(ValueError, match="inner_diameter"):
        glider(spar=dict(outer_diameter_mm=10, inner_diameter_mm=12))


@pytest.mark.asyncio
async def test_fixed_wing_tool_is_exposed_over_mcp(tmp_path):
    config = Configuration(project_root=str(tmp_path), drone_tools=True, backends={})
    async with Client(create_server(config)) as client:
        names = {t.name for t in await client.list_tools()}
        assert "drone_fixed_wing_analysis" in names
        payload = {"mass_g": 3500, "cg_x_mm": 620,
                   "wing": {"stations": [{"s_mm": 0, "x_le_mm": 550, "chord_mm": 300}, {"s_mm": 1500, "x_le_mm": 600, "chord_mm": 200}]},
                   "tails": [{"role": "v_tail", "dihedral_deg": 40, "stations": [{"s_mm": 0, "x_le_mm": 1350, "chord_mm": 250}, {"s_mm": 450, "x_le_mm": 1430, "chord_mm": 170}]}],
                   "fuselage": {"length_mm": 1700, "max_width_mm": 120, "max_height_mm": 105},
                   "battery": {"cells_series": 6, "cells_parallel": 2, "cell_capacity_mah": 4500},
                   "propulsion": {"prop_diameter_in": 13}}
        result = await client.call_tool("drone_fixed_wing_analysis", {"aircraft": payload})
        assert result.structured_content["geometry"]["wing_span_m"] == 3.0
        bad = await client.call_tool("drone_fixed_wing_analysis", {"aircraft": {**payload, "mass_g": -1}}, raise_on_error=False)
        assert bad.is_error


def test_inverted_v_tail_is_accepted_and_matches_upright_areas():
    st = [dict(s_mm=0, x_le_mm=1350, chord_mm=250), dict(s_mm=450, x_le_mm=1430, chord_mm=170)]
    up = glider(tails=[dict(role="v_tail", dihedral_deg=40, stations=st)])
    inv = glider(tails=[dict(role="v_tail", dihedral_deg=-40, stations=st)])
    assert inv["stability"]["neutral_point_x_mm"] == pytest.approx(up["stability"]["neutral_point_x_mm"])
    assert inv["geometry"]["vertical_tail_volume"] == pytest.approx(up["geometry"]["vertical_tail_volume"])
    assert inv["geometry"]["tails"][0]["inverted_v"] is True and up["geometry"]["tails"][0]["inverted_v"] is False
