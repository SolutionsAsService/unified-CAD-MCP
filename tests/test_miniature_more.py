"""Tests for the extra miniature tools (miniature_more.py): pure functions plus the MCP surface."""
import asyncio
import struct

import pytest
from fastmcp import Client

from unified_cad_mcp.miniature import create_miniature_server
from unified_cad_mcp.miniature_more import (base_catalog, detail_advice, floor_clearance_check, hollow_plan, plate_pack, split_plan,
                                            terrain_grid_check)


def box_tris(sx, sy, sz):
    v = [(x, y, z) for z in (0, sz) for y in (0, sy) for x in (0, sx)]
    f = [(0, 2, 1), (1, 2, 3), (4, 5, 6), (5, 7, 6), (0, 1, 4), (1, 5, 4), (2, 6, 3), (3, 6, 7), (0, 4, 2), (2, 4, 6), (1, 3, 5), (3, 7, 5)]
    return [tuple(v[i] for i in t) for t in f]


def test_terrain_grid_snaps_and_lays_out_magnets():
    r = terrain_grid_check([76.0, 51.5], "1in", "magnet", "5x2", 4.0, "fdm_0.2_nozzle")
    assert r["cells"] == [3, 2] and r["snapped_footprint_mm"] == [76.2, 50.8]
    assert len(r["connectors"]) == 2 * 3 + 2 * 2  # both edges of every cell column/row
    assert r["all_checks_pass"]
    thin = terrain_grid_check([50, 50], "25mm", "magnet", "5x2", 2.2, "msla_resin")
    assert not thin["all_checks_pass"]  # 2.2 mm base - 2.05 mm pocket leaves 0.15 mm floor


def test_terrain_grid_openlock_needs_6mm_base_and_inch_grid():
    assert terrain_grid_check([50.8, 50.8], "1in", "openlock", None, 6.0)["all_checks_pass"]
    bad = terrain_grid_check([50, 50], "25mm", "openlock", None, 4.0)
    assert {c["name"] for c in bad["checks"] if not c["pass"]} == {"base height for OpenLOCK clip slots", "OpenLOCK runs on the 1 in grid"}


def test_detail_advice_exaggerates_hafts_and_floors_at_process_limits():
    r = detail_advice("28mm", "msla_resin", [{"kind": "weapon_haft", "real_mm": 40, "length_mm": 25},
                                             {"kind": "panel_line", "real_mm": 5}])
    haft, line = r["features"]
    assert haft["true_model_mm"] < 1.0 and haft["recommended_mm"] >= 1.2  # 25 mm free wire needs >1.2 mm in resin
    assert line["recommended_mm"] >= max(0.4, r["readable_painted_detail_mm"])
    assert 0.15 < r["acuity_limit_mm"] < 0.2  # 1 arc-minute at 60 cm


def test_hollow_plan_hollows_big_blocks_not_small_ones():
    big = hollow_plan(box_tris(40, 40, 30))
    assert big["recommend_hollow"] and big["resin_saved_ml"] > 20
    assert [h for h in big["holes"] if h["role"].startswith("drain")] and big["holes"][-1]["role"].startswith("vent")
    assert all(h["diameter_mm"] >= 2.5 for h in big["holes"])
    small = hollow_plan(box_tris(8, 8, 8))
    assert not small["recommend_hollow"] and small["holes"] == []
    assert not hollow_plan(box_tris(40, 40, 30), process="fdm_0.2_nozzle")["recommend_hollow"]


def test_split_plan_cuts_a_tall_tower_for_a_small_resin_printer():
    r = split_plan([75, 75, 200], "elegoo_mars_5_ultra", "msla_resin")
    assert r["pieces"] > 1 and r["fits_after_split"]
    j = r["joints"][0]
    assert 1.5 <= j["peg_diameter_mm"] <= 6 and j["socket_diameter_mm"] > j["peg_diameter_mm"]
    assert split_plan([20, 20, 30], "prusa_mk4", "fdm_0.2_nozzle")["pieces"] == 1


def test_base_catalog_finds_smallest_fit():
    r = base_catalog([30, 20])
    assert r["smallest_fits"][0]["area_mm2"] <= r["smallest_fits"][-1]["area_mm2"]
    assert all(f["shape"] in r["bases_mm"] for f in r["smallest_fits"])
    assert r["bases_mm"]["hex"][0] == [25, 28.87]


def test_floor_clearance_flags_low_storeys_and_tight_floors():
    ok = floor_clearance_check("28mm", 40.0, [20, 34], floor_fit_gap_mm=0.25, lip_engagement_mm=3.0, hatch_mm=[12, 12], opening_mm=63)
    assert ok["all_checks_pass"], ok["checks"]
    bad = floor_clearance_check("28mm", 30.0, [12, 25], floor_fit_gap_mm=0.1, lip_engagement_mm=1.0, hatch_mm=[6, 6])
    assert sum(not c["pass"] for c in bad["checks"]) == 6


def test_plate_pack_counts_plates():
    r = plate_pack([{"name": "barrel", "footprint_mm": [9, 9], "qty": 40}, {"name": "tower", "footprint_mm": [300, 300]}],
                   "elegoo_mars_5_ultra")
    assert r["too_big"] == ["tower"] and r["plates"] >= 1 and sum(sum(p.values()) for p in r["per_plate"]) == 40
    many = plate_pack([{"name": "tile", "footprint_mm": [60, 60], "qty": 7}], "elegoo_mars_5_ultra")
    assert many["plates"] == 4  # 143 x 68 mm usable: two 60 mm tiles across, one row -> 2 per plate
    assert all(sum(p.values()) <= 2 for p in many["per_plate"])


def test_tools_are_exposed_over_mcp(tmp_path):
    stl = tmp_path / "b.stl"
    tris = box_tris(40, 40, 30)
    with open(stl, "wb") as f:
        f.write(b"x" * 80 + struct.pack("<I", len(tris)))
        for t in tris:
            f.write(struct.pack("<12fH", 0, 0, 0, *t[0], *t[1], *t[2], 0))

    async def run():
        async with Client(create_miniature_server(lambda p: tmp_path / p)) as c:
            names = {t.name for t in await c.list_tools()}
            assert {"terrain_grid_check", "detail_advice", "hollow_plan", "split_plan", "base_catalog", "floor_clearance_check",
                    "plate_pack"} <= names
            r = (await c.call_tool("hollow_plan", {"stl_path": "b.stl"})).data
            assert r["recommend_hollow"]
            r = (await c.call_tool("plate_pack", {"parts": [{"name": "a", "footprint_mm": [10, 10], "qty": 3}]})).data
            assert r["plates"] == 1
            with pytest.raises(Exception):
                await c.call_tool("detail_advice", {"scale": "28mm", "features": [{"kind": "nope", "real_mm": 1}]})
    asyncio.run(run())
