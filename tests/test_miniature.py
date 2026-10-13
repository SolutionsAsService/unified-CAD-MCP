"""miniature_* tools on synthetic meshes (pure Python, no FreeCAD)."""
import struct
import time

from fastmcp import Client
import pytest

from unified_cad_mcp.config import AirframeConfig, Configuration
from unified_cad_mcp.miniature import Feature, base_and_peg, printability_check, scale_convert, support_plan
from unified_cad_mcp.server import create_server


def box(x0, y0, z0, sx, sy, sz, inward=False):
    """Closed, outward-oriented box as 12 triangles (inward=True flips it, for cavities)."""
    v = [(x0 + i * sx, y0 + j * sy, z0 + k * sz) for i in (0, 1) for j in (0, 1) for k in (0, 1)]
    idx = lambda i, j, k: i * 4 + j * 2 + k
    faces = [((0, 0, 0), (0, 1, 0), (0, 1, 1), (0, 0, 1)), ((1, 0, 0), (1, 0, 1), (1, 1, 1), (1, 1, 0)),
             ((0, 0, 0), (0, 0, 1), (1, 0, 1), (1, 0, 0)), ((0, 1, 0), (1, 1, 0), (1, 1, 1), (0, 1, 1)),
             ((0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)), ((0, 0, 1), (0, 1, 1), (1, 1, 1), (1, 0, 1))]
    tris = []
    for f in faces:
        a, b, c, d = (v[idx(*p)] for p in f)
        tris += [(a, b, c), (a, c, d)] if inward else [(a, c, b), (a, d, c)]
    return tris


def cup():
    """10 x 10 x 5 block with a 6 x 6 x 3 blind pocket open at the top (+z), built from touching boxes (not manifold)."""
    return (box(0, 0, 0, 10, 10, 2) + box(0, 0, 2, 2, 10, 3) + box(8, 0, 2, 2, 10, 3)
            + box(2, 0, 2, 6, 2, 3) + box(2, 8, 2, 6, 2, 3))


def fails(r):
    return {c["name"] for c in r["checks"] if not c["pass"]}


def test_cube_passes_and_reports_volume_and_mass():
    r = printability_check(box(0, 0, 0, 10, 10, 10))
    assert r["all_checks_pass"], fails(r)
    assert r["volume_mm3"] == pytest.approx(1000)
    assert r["mass_g"] == pytest.approx(1.1)
    assert r["topology"]["open_edges"] == 0 and r["islands"]["count"] == 0
    assert r["suction_cups"] == [] and r["enclosed_void_mm3"] == []
    assert r["overhang_area_mm2_by_angle_from_level"]["0-10 deg"] == pytest.approx(100)


def test_open_mesh_two_bodies_and_sealed_void_fail():
    assert "closed (watertight)" in fails(printability_check(box(0, 0, 0, 10, 10, 10)[:-2]))
    two = box(0, 0, 0, 5, 5, 5) + box(10, 0, 0, 5, 5, 5)
    assert "separate bodies" in fails(printability_check(two))
    assert printability_check(two, expected_bodies=2)["all_checks_pass"]
    hollow = printability_check(box(0, 0, 0, 10, 10, 10) + box(2, 2, 2, 6, 6, 6, inward=True))
    assert "no enclosed voids" in fails(hollow)
    assert hollow["enclosed_void_mm3"][0] == pytest.approx(216, rel=0.05)


def test_thin_walls():
    thin = printability_check(box(0, 0, 0, 10, 10, 0.3))
    assert "minimum wall (supported)" in fails(thin) and thin["thickness"]["min_mm"] == pytest.approx(0.3)
    mid = printability_check(box(0, 0, 0, 10, 10, 0.5))
    assert mid["all_checks_pass"] and "minimum wall (unsupported)" in mid["warnings"]
    assert printability_check(box(0, 0, 0, 10, 10, 0.5), process="fdm_0.2_nozzle")["all_checks_pass"]


def test_suction_cup_depends_on_orientation_and_plate():
    up = printability_check(cup())
    assert "suction cups" in fails(up) and up["suction_cups"][0]["max_area_mm2"] == pytest.approx(36, rel=0.1)
    down = printability_check(cup(), rotate_deg=[180, 0, 0])
    assert "suction cups" not in fails(down) and not down["suction_cups"]  # opening toward the plate, on supports: vented from below
    assert "suction cups" in fails(printability_check(cup(), rotate_deg=[180, 0, 0], on_build_plate=True))


def test_thin_spike_against_wire_table():
    pin = box(0, 0, 0, 10, 10, 2) + box(4.85, 4.85, 2, 0.3, 0.3, 10)
    r = printability_check(pin, voxel_mm=0.05, expected_bodies=2)  # the pin is its own shell
    assert "thin spikes / wires" in fails(r)
    assert r["spikes"][0]["length_mm"] == pytest.approx(10, abs=0.2)
    short = box(0, 0, 0, 10, 10, 2) + box(4.5, 4.5, 2, 1.0, 1.0, 5)
    assert not printability_check(short, voxel_mm=0.05, expected_bodies=2)["spikes"]


def test_islands_need_supports_unless_planned():
    bridge = box(0, 0, 0, 2, 2, 6) + box(-3, 0, 6, 8, 2, 1)  # T: arms overhang, no islands
    assert printability_check(bridge, expected_bodies=2)["islands"]["count"] == 0
    floating = box(0, 0, 0, 2, 2, 2) + box(5, 0, 3, 2, 2, 2)
    r = printability_check(floating, expected_bodies=2, supports_planned=False)
    assert r["islands"]["count"] == 1 and "islands" in fails(r)


def test_support_plan_tilts_a_flat_plate():
    r = support_plan(box(0, 0, 0, 20, 20, 1))
    assert r["as_modelled"]["max_layer_area_mm2"] == pytest.approx(400, rel=0.02)
    best = r["best"][0]
    assert best["rotate_deg"] != [0.0, 0.0, 0.0] and best["max_layer_area_mm2"] < 100
    assert r["orientations_sampled"] == 49


def test_base_and_peg_numbers():
    r = base_and_peg(base="40", magnet="5x2", peg_diameter_mm=2.0, host_section_mm=4.0)
    assert r["base"]["standard"] and r["magnet_pocket"]["pocket_diameter_mm"] == 5.2
    assert r["magnet_pocket"]["pocket_depth_mm"] == 2.1 and r["peg"]["socket_diameter_mm"] == 2.2
    assert r["all_checks_pass"]
    oval = base_and_peg(base="60x35", magnet="6x3", base_height_mm=3.0)
    assert oval["base"]["shape"] == "oval" and "magnet pocket floor" in fails(oval)


def test_scale_convert_ratios_and_limits():
    r = scale_convert("1:48", [Feature(name="span", real_mm=3600), Feature(name="boom", kind="wall", real_mm=22),
                               Feature(name="pitot", kind="wire", real_mm=10, wire_length_real_mm=200)])
    rows = {f["name"]: f for f in r["features"]}
    assert rows["span"]["model_mm"] == 75 and rows["boom"]["exaggerate_to_mm"] == 0.6
    assert set(r["below_limit"]) == {"boom", "pitot"}
    assert scale_convert("28mm", [Feature(name="x", real_mm=1)], convention="crown")["ratio"] == pytest.approx(63.6, abs=0.1)
    assert scale_convert("28mm", [Feature(name="x", real_mm=1)])["ratio"] == pytest.approx(59.5, abs=0.1)
    with pytest.raises(ValueError):
        scale_convert("huge", [Feature(name="x", real_mm=1)])


def write_stl(path, tris):
    data = bytearray(80) + struct.pack("<I", len(tris))
    for t in tris:
        data += struct.pack("<12fH", 0, 0, 0, *t[0], *t[1], *t[2], 0)
    path.write_bytes(bytes(data))


async def test_mcp_namespace_paths_and_timing(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    write_stl(out / "cube.stl", box(0, 0, 0, 10, 10, 10))
    (tmp_path / "secret.stl").write_bytes((out / "cube.stl").read_bytes())
    cfg = Configuration(project_root=str(tmp_path), drone_tools=True, backends={},
                        airframe=AirframeConfig(freecad_python="/nonexistent/python", output_root=str(out)))
    async with Client(create_server(cfg)) as c:
        names = {t.name for t in await c.list_tools()}
        assert {"miniature_scale_convert", "miniature_printability_check", "miniature_base_and_peg", "miniature_support_plan"} <= names
        start = time.monotonic()
        r = await c.call_tool("miniature_printability_check", {"stl_path": "cube.stl"})
        assert r.data["all_checks_pass"] and time.monotonic() - start < 30
        bad = await c.call_tool("miniature_printability_check", {"stl_path": "../secret.stl"}, raise_on_error=False)
        assert bad.is_error and "outside" in bad.content[0].text
        ctx = (await c.call_tool("unified_get_context")).data
        assert ctx["backends"]["miniature"]["state"] == "builtin" and "miniature_*" in ctx["routing"]
    async with Client(create_server(Configuration(project_root=str(tmp_path), drone_tools=True, backends={}))) as c:
        r = await c.call_tool("miniature_support_plan", {"stl_path": str(out / "cube.stl")}, raise_on_error=False)
        assert r.is_error and "airframe section" in r.content[0].text
