"""Pure-Python drone_* estimators: physics sanity, validation and MCP exposure."""
import math

from fastmcp import Client
import pytest

from unified_cad_mcp.config import Configuration
from unified_cad_mcp.drone import MassItem, isa_density, mass_cg_budget, propulsion_estimate, regulatory_weight_class
from unified_cad_mcp.server import create_server


def quad(**overrides):
    args = dict(all_up_mass_g=1000, rotor_count=4, prop_diameter_in=10, battery_cells=4,
                battery_capacity_mah=5000, figure_of_merit=0.6, motor_efficiency=0.8,
                esc_efficiency=0.95, avionics_power_w=5, usable_capacity_fraction=0.8)
    args.update(overrides)
    return propulsion_estimate(**args)


def test_hover_power_matches_independent_momentum_theory():
    result = quad()
    thrust = 1.0 * 9.80665 / 4
    area = math.pi * (10 * 0.0254 / 2) ** 2
    ideal = thrust ** 1.5 / math.sqrt(2 * 1.225 * area)
    assert result["ideal_induced_power_per_rotor_w"] == pytest.approx(ideal, rel=1e-3)
    electrical = ideal / 0.6 / (0.8 * 0.95) * 4 + 5
    assert result["hover_electrical_power_w"] == pytest.approx(electrical, rel=1e-3)
    wh = 4 * 3.7 * 5.0 * 0.8
    assert result["hover_flight_time_min"] == pytest.approx(wh / electrical * 60, rel=1e-3)
    assert result["hover_current_a"] == pytest.approx(electrical / (4 * 3.7), rel=1e-3)
    assert result["air_density_kg_m3"] == pytest.approx(1.225, abs=1e-3)
    assert "HEURISTIC" in result["estimate_kind"]
    assert result["thrust_to_weight"] is None
    assert any("UNKNOWN" in w for w in result["warnings"])


def test_trends_and_margins():
    base = quad()
    assert quad(altitude_m=3000)["hover_electrical_power_w"] > base["hover_electrical_power_w"]
    assert quad(prop_diameter_in=12)["hover_electrical_power_w"] < base["hover_electrical_power_w"]
    assert quad(coaxial=True)["hover_electrical_power_w"] > base["hover_electrical_power_w"]
    assert isa_density(0) == pytest.approx(1.225, abs=1e-3)
    assert isa_density(5000) == pytest.approx(0.7364, abs=2e-3)
    strong = quad(max_thrust_per_motor_g=1000, motor_max_current_a=30, battery_c_rating=20)
    assert strong["thrust_to_weight"] == 4.0 and strong["hover_thrust_fraction"] == 0.25
    assert strong["recommended_esc_continuous_a"] == 37.5
    assert strong["required_battery_c_rating"] == pytest.approx((120 + 5 / 14.8) / 5, rel=1e-3)
    assert any("C rating" in w for w in strong["warnings"])
    weak = quad(max_thrust_per_motor_g=400)
    assert any("below 2" in w for w in weak["warnings"]) and any(">60%" in w for w in weak["warnings"])


def test_mass_cg_inertia_budget():
    items = [MassItem(name=f"motor{i}", mass_g=50, position_mm=[x, y, 0], category="propulsion")
             for i, (x, y) in enumerate([(100, 100), (-100, 100), (-100, -100), (100, -100)])]
    items.append(MassItem(name="battery", mass_g=200, position_mm=[0, 0, 0], size_mm=[100, 40, 30], category="power"))
    result = mass_cg_budget(items, max_takeoff_mass_g=390, cg_tolerance_mm=2)
    assert result["total_mass_g"] == 400 and result["cg_mm"] == [0, 0, 0]
    box_xx = 0.2 * (0.04 ** 2 + 0.03 ** 2) / 12
    tensor = result["inertia_about_cg_kg_m2"]
    assert tensor[0][0] == pytest.approx(4 * 0.05 * 0.01 + box_xx, rel=1e-6)
    assert tensor[2][2] == pytest.approx(4 * 0.05 * 0.02 + 0.2 * (0.1 ** 2 + 0.04 ** 2) / 12, rel=1e-6)
    assert tensor[0][1] == pytest.approx(0, abs=1e-12)
    assert result["principal_moments_kg_m2"] == sorted([tensor[0][0], tensor[1][1], tensor[2][2]])
    assert result["mass_margin_g"] == -10 and result["cg_within_tolerance"] is True
    assert result["mass_by_category_g"] == {"power": 200, "propulsion": 200}
    shifted = mass_cg_budget(items + [MassItem(name="camera", mass_g=100, position_mm=[50, 0, -20])], cg_tolerance_mm=2)
    assert shifted["cg_mm"][0] == pytest.approx(10.0) and shifted["cg_within_tolerance"] is False
    # Off-diagonal products of inertia appear and principal moments stay consistent (trace invariant).
    t = shifted["inertia_about_cg_kg_m2"]
    assert t[0][2] != 0 and sum(shifted["principal_moments_kg_m2"]) == pytest.approx(t[0][0] + t[1][1] + t[2][2], rel=1e-6)
    with pytest.raises(ValueError):
        mass_cg_budget(items + [MassItem(name="battery", mass_g=1, position_mm=[0, 0, 0])])


@pytest.mark.parametrize("mass,jurisdiction,band", [
    (249, "US", "under 250 g"), (250, "US", "250 g up to 55 lb"), (30000, "US", "55 lb"),
    (249, "EU", "C0"), (899, "EU", "C1"), (3999, "EU", "C2"), (24999, "EU", "C3/C4"), (25000, "EU", "25 kg or more")])
def test_weight_bands(mass, jurisdiction, band):
    result = regulatory_weight_class(mass, jurisdiction)
    assert band in result["weight_band"] and "NOT legal advice" in result["disclaimer"]


async def test_drone_namespace_is_opt_in_and_validated(tmp_path):
    off = Configuration(project_root=str(tmp_path), backends={})
    async with Client(create_server(off)) as client:
        assert {t.name for t in await client.list_tools()} == {"unified_get_context"}
        assert (await client.call_tool("unified_get_context")).data["backends"]["drone"] == {"state": "disabled"}
    on = Configuration(project_root=str(tmp_path), backends={}, drone_tools=True)
    async with Client(create_server(on)) as client:
        tools = {t.name: t for t in await client.list_tools()}
        assert set(tools) == {"unified_get_context", "drone_mass_cg_budget", "drone_propulsion_estimate", "drone_regulatory_weight_class",
                              "drone_fixed_wing_analysis", "drone_rf_antenna_layout_check",
                              "enclosure_module_catalog", "enclosure_fit_check", "enclosure_button_stack", "enclosure_oring_gland",
                              "miniature_scale_convert", "miniature_printability_check", "miniature_base_and_peg", "miniature_support_plan",
                              "miniature_terrain_grid_check", "miniature_detail_advice", "miniature_hollow_plan", "miniature_split_plan",
                              "miniature_base_catalog", "miniature_floor_clearance_check", "miniature_plate_pack"}
        assert all(tools[n].annotations.read_only_hint for n in tools if n.startswith(("drone_", "enclosure_", "miniature_")))
        result = await client.call_tool("drone_propulsion_estimate", {"all_up_mass_g": 1000, "rotor_count": 4, "prop_diameter_in": 10, "battery_cells": 4, "battery_capacity_mah": 5000})
        assert result.data["hover_flight_time_min"] > 0
        budget = await client.call_tool("drone_mass_cg_budget", {"items": [{"name": "frame", "mass_g": 150, "position_mm": [0, 0, 0]}]})
        assert budget.data["total_mass_g"] == 150
        for bad in [{"all_up_mass_g": -1, "rotor_count": 4, "prop_diameter_in": 10, "battery_cells": 4, "battery_capacity_mah": 5000},
                    {"all_up_mass_g": 1000, "rotor_count": 4, "prop_diameter_in": 10, "battery_cells": 4, "battery_capacity_mah": 5000, "figure_of_merit": 1.5}]:
            assert (await client.call_tool("drone_propulsion_estimate", bad, raise_on_error=False)).is_error
        unknown = await client.call_tool("drone_mass_cg_budget", {"items": [{"name": "x", "mass_g": 1, "position_mm": [0, 0, 0], "extra": 1}]}, raise_on_error=False)
        assert unknown.is_error
        context = (await client.call_tool("unified_get_context")).data
        assert context["backends"]["drone"]["state"] == "builtin" and "drone_*" in context["routing"]


def test_mass_budget_accepts_cad_inertia_and_longitudinal_cg_band():
    from unified_cad_mcp.drone import MassItem, mass_cg_budget
    tensor = [[0.01, 0.0, 0.001], [0.0, 0.02, 0.0], [0.001, 0.0, 0.03]]
    r = mass_cg_budget([MassItem(name="wing", mass_g=1000, position_mm=[100, 0, 0], inertia_about_cg_kg_m2=tensor),
                        MassItem(name="nose", mass_g=1000, position_mm=[-100, 0, 0])], cg_x_range_mm=[-10, 10])
    # own tensor + parallel axis: each mass 1 kg at 0.1 m from the CG -> 2 * 0.01 kg m^2 about y and z
    assert r["inertia_about_cg_kg_m2"][0][0] == pytest.approx(0.01)
    assert r["inertia_about_cg_kg_m2"][1][1] == pytest.approx(0.02 + 0.02)
    assert r["inertia_about_cg_kg_m2"][0][2] == pytest.approx(0.001)
    assert r["cg_x_within_range"] is True
    out = mass_cg_budget([MassItem(name="a", mass_g=10, position_mm=[50, 0, 0])], cg_x_range_mm=[0, 10])
    assert out["cg_x_within_range"] is False and any("cg_x_range_mm" in w for w in out["warnings"])
    with pytest.raises(ValueError, match="symmetric"):
        mass_cg_budget([MassItem(name="bad", mass_g=1, position_mm=[0, 0, 0], inertia_about_cg_kg_m2=[[1, 2, 0], [0, 1, 0], [0, 0, 1]])])
