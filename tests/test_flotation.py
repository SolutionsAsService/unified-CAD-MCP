import math
import struct

import pytest

from unified_cad_mcp import flotation
from unified_cad_mcp.enclosure import OringGland, oring_gland


def box(x0, y0, z0, sx, sy, sz):
    """Closed, outward-oriented box as 12 triangles."""
    v = [(x0 + i * sx, y0 + j * sy, z0 + k * sz) for i in (0, 1) for j in (0, 1) for k in (0, 1)]
    idx = lambda i, j, k: i * 4 + j * 2 + k
    faces = [((0, 0, 0), (0, 1, 0), (0, 1, 1), (0, 0, 1)), ((1, 0, 0), (1, 0, 1), (1, 1, 1), (1, 1, 0)),
             ((0, 0, 0), (0, 0, 1), (1, 0, 1), (1, 0, 0)), ((0, 1, 0), (1, 1, 0), (1, 1, 1), (0, 1, 1)),
             ((0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)), ((0, 0, 1), (0, 1, 1), (1, 1, 1), (1, 0, 1))]
    tris = []
    for f in faces:
        a, b, c, d = (v[idx(*p)] for p in f)
        tris += [(a, c, b), (a, d, c)]  # outward
    return tris


def test_box_volume_and_centroid_below_plane():
    t = box(-50, -50, 0, 100, 100, 100)
    vol, c = flotation.submerged(t, 40)
    assert vol == pytest.approx(100 * 100 * 40)
    assert c == pytest.approx([0, 0, 20], abs=1e-6)


def test_box_hydrostatics_match_closed_form():
    # 100 mm cube, 500 g, fresh water: draft 50, KB 25, BM = B^2/(12 T) = 16.67
    r = flotation.analyze({"cube": box(-50, -50, 0, 100, 100, 100)}, mass_g=500, cg_mm=[0, 0, 20],
                          points=[{"name": "deck", "position_mm": [0, 0, 100], "min_freeboard_mm": 30}], heel_step_deg=2, heel_max_deg=40)
    assert r["waterline_z_mm"] == pytest.approx(50, abs=1e-3)
    assert r["reserve_buoyancy_fraction"] == pytest.approx(1.0, abs=1e-6)
    assert r["stability"]["roll"]["gm_initial_mm"] == pytest.approx(25 + 10000 / 600 - 20, rel=0.02)
    assert r["freeboard"][0]["freeboard_mm"] == pytest.approx(50, abs=1e-3)


def test_top_heavy_box_fails_and_sinker_is_reported():
    r = flotation.analyze({"cube": box(-50, -50, 0, 100, 100, 100)}, mass_g=500, cg_mm=[0, 0, 60], heel_max_deg=30)
    gm = next(c for c in r["checks"] if c["name"] == "initial stability GM (roll)")
    assert gm["value"] < 0 and not r["all_checks_pass"]
    s = flotation.analyze({"cube": box(0, 0, 0, 10, 10, 10)}, mass_g=5000, cg_mm=[5, 5, 5])
    assert s["all_checks_pass"] is False and "Sinks" in s["note"]


def test_outboard_floats_beat_a_single_hull():
    hull = box(-100, -50, 0, 200, 100, 90)  # 1.8 L, floats alone
    floats = [box(x - 30, y - 30, -20, 60, 60, 80) for x in (-120, 120) for y in (-140, 140)]
    alone = flotation.analyze({"hull": hull}, mass_g=1500, cg_mm=[0, 0, 45], heel_max_deg=30)
    both = flotation.analyze({"hull": hull, **{f"f{i}": f for i, f in enumerate(floats)}}, mass_g=1500, cg_mm=[0, 0, 45], heel_max_deg=30)
    assert both["stability"]["roll"]["gm_initial_mm"] > 5 * alone["stability"]["roll"]["gm_initial_mm"]


def test_inward_mesh_is_reoriented():
    inward = [(a, c, b) for a, b, c in box(-50, -50, 0, 100, 100, 100)]
    r = flotation.analyze({"cube": inward}, mass_g=500, cg_mm=[0, 0, 20], heel_max_deg=20)
    assert r["waterline_z_mm"] == pytest.approx(50, abs=1e-3)


def test_binary_stl_round_trip(tmp_path):
    t = box(0, 0, 0, 10, 20, 30)
    p = tmp_path / "b.stl"
    p.write_bytes(b"\0" * 80 + struct.pack("<I", len(t)) + b"".join(struct.pack("<12fH", 0, 0, 0, *a, *b, *c, 0) for a, b, c in t))
    vol, _ = flotation.submerged(flotation.read_stl(p), 1000)
    assert vol == pytest.approx(6000, rel=1e-6)


def test_oring_gland_good_and_bad():
    ok = oring_gland(OringGland(name="lid", cord_diameter_mm=2.0, groove_depth_mm=1.5, groove_width_mm=2.6,
                                fastener_pitch_mm=40, flange_thickness_mm=5))
    assert ok["all_checks_pass"], ok["checks"]
    assert ok["squeeze"] == pytest.approx(0.25)
    assert ok["gland_fill"] == pytest.approx(math.pi / (1.5 * 2.6), rel=1e-3)
    bad = oring_gland(OringGland(name="tight", cord_diameter_mm=2.0, groove_depth_mm=1.2, groove_width_mm=2.0,
                                 groove_centerline_length_mm=200, oring_inner_diameter_mm=55, fastener_pitch_mm=90, flange_thickness_mm=3))
    failed = {c["name"] for c in bad["checks"] if not c["pass"]}
    assert {"squeeze", "gland fill", "installed stretch", "clamp-screw pitch / flange thickness"} <= failed
