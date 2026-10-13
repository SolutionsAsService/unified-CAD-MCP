"""airframe_* tools: config/path guards (always) and an end-to-end reference-drone build (opt-in).

The end-to-end tests need a FreeCAD Python (UNIFIED_AIRFRAME_FREECAD_PYTHON) and,
for the aero cross-check, an AeroSandbox Python (UNIFIED_AIRFRAME_AEROSANDBOX_PYTHON).
CI provides both in the 'airframe' job; locally they are skipped when unset.
"""
import json
import math
import os
from pathlib import Path

from fastmcp import Client
import pytest

from unified_cad_mcp.airframe import _spec_errors
from unified_cad_mcp.config import AirframeConfig, Configuration, load_config
from unified_cad_mcp.server import create_server

FIXTURE = Path(__file__).parent / "fixtures" / "reference-glider.airframe.json"
FREECAD = os.environ.get("UNIFIED_AIRFRAME_FREECAD_PYTHON")
ASB = os.environ.get("UNIFIED_AIRFRAME_AEROSANDBOX_PYTHON")
VSP_PY = os.environ.get("UNIFIED_AIRFRAME_OPENVSP_PYTHON")
VSP_ROOT = os.environ.get("UNIFIED_AIRFRAME_OPENVSP_ROOT")
VSP_LIB = os.environ.get("UNIFIED_AIRFRAME_OPENVSP_LIBRARY_PATH")


def spec():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def server(tmp_path, freecad="/nonexistent/python", asb=None):
    out = tmp_path / "airframe"
    out.mkdir(exist_ok=True)
    cfg = Configuration(project_root=str(tmp_path), drone_tools=True, backends={},
                        airframe=AirframeConfig(freecad_python=freecad, aerosandbox_python=asb, output_root=str(out)))
    return create_server(cfg), out


def test_spec_validation_messages_are_actionable():
    good = spec()
    assert _spec_errors(good) == []
    bad = spec()
    bad["materials"] = {}
    bad["tail"]["type"] = "t"
    bad["components"][0]["mass_g"] = 0
    errors = "\n".join(_spec_errors(bad))
    assert "not defined in materials" in errors and "only 'v'" in errors and "mass_g > 0" in errors
    assert "missing top-level key 'wing'" in _spec_errors({"id": "x"})


def test_airframe_config_requires_existing_dirs(tmp_path):
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"schema_version": 1, "project_root": str(tmp_path), "backends": {},
                                "airframe": {"freecad_python": "python3", "output_root": "missing"}}))
    with pytest.raises(Exception):
        load_config(path)
    (tmp_path / "out").mkdir()
    path.write_text(json.dumps({"schema_version": 1, "project_root": str(tmp_path), "backends": {},
                                "airframe": {"freecad_python": "python3", "output_root": "out"}}))
    assert load_config(path).airframe.output_root == str((tmp_path / "out").resolve())


@pytest.mark.asyncio
async def test_airframe_tools_guard_paths_names_and_missing_runtime(tmp_path):
    mcp, out = server(tmp_path)
    (tmp_path / "secret.step").write_text("x")
    async with Client(mcp) as client:
        names = {t.name for t in await client.list_tools()}
        assert {"airframe_build_fixed_wing", "airframe_inspect_step", "airframe_render_preview", "airframe_aero_crosscheck",
                "airframe_vspaero_analysis"} <= names
        ctx = (await client.call_tool("unified_get_context", {})).structured_content
        assert ctx["backends"]["airframe"]["state"] == "builtin"
        r = await client.call_tool("airframe_inspect_step", {"path": "../secret.step"}, raise_on_error=False)
        assert r.is_error and "outside" in r.content[0].text
        r = await client.call_tool("airframe_build_fixed_wing", {"spec": spec(), "output_name": "../escape"}, raise_on_error=False)
        assert r.is_error and "output_name" in r.content[0].text
        (out / "foreign").mkdir()
        r = await client.call_tool("airframe_build_fixed_wing", {"spec": spec(), "output_name": "foreign", "replace": True}, raise_on_error=False)
        assert r.is_error and "not created by airframe_build_fixed_wing" in r.content[0].text
        r = await client.call_tool("airframe_build_fixed_wing", {"spec": spec(), "output_name": "ok"}, raise_on_error=False)
        assert r.is_error and "interpreter not found" in r.content[0].text
        assert (out / "ok" / ".airframe-build.json").is_file()  # marker exists even though the worker never ran
        r = await client.call_tool("airframe_build_fixed_wing", {"spec": spec(), "output_name": "ok", "replace": True}, raise_on_error=False)
        assert r.is_error and "interpreter not found" in r.content[0].text  # a failed build stays replaceable
        r = await client.call_tool("airframe_aero_crosscheck", {"spec": spec(), "mass_g": 1000, "cg_mm": [0, 0, 0], "velocity_m_s": 12}, raise_on_error=False)
        assert r.is_error and "not configured" in r.content[0].text
        r = await client.call_tool("airframe_vspaero_analysis", {"spec": spec(), "output_name": "vsp", "cg_mm": [60, 0, 0], "velocity_m_s": 12},
                                   raise_on_error=False)
        assert r.is_error and "openvsp_root" in r.content[0].text and "dpkg-deb -x" in r.content[0].text


@pytest.mark.airframe
@pytest.mark.skipif(not (VSP_PY and VSP_ROOT), reason="UNIFIED_AIRFRAME_OPENVSP_PYTHON/_ROOT not set")
@pytest.mark.asyncio
async def test_vspaero_reference_glider(tmp_path):
    out = tmp_path / "airframe"
    out.mkdir()
    cfg = Configuration(project_root=str(tmp_path), backends={}, airframe=AirframeConfig(
        freecad_python="/nonexistent/python", output_root=str(out), openvsp_python=VSP_PY, openvsp_root=VSP_ROOT,
        openvsp_library_path=VSP_LIB))
    async with Client(create_server(cfg)) as client:
        r = await client.call_tool("airframe_vspaero_analysis", {"spec": spec(), "output_name": "ref", "cg_mm": [60, 0, 0],
                                                                "velocity_m_s": 12, "mass_g": 600, "alpha_deg": [-2, 0, 2, 4, 6]})
        res = r.structured_content
        if "result" in res and isinstance(res["result"], dict):
            res = res["result"]
    v = res["vspaero"]
    # Rectangular AR-5 wing: Helmbold 2*pi*A/(2+sqrt(A^2+4)) ~ 4.0 /rad for the wing alone; the V-tail adds a little
    # lift on the wing reference area, so VSPAERO's whole-aircraft slope must land in 3.5..5.0 /rad (measured 4.39).
    assert 3.5 < v["CL_alpha_per_rad"] < 5.0
    assert v["dCm_dCL"] < 0 and 0 < v["static_margin"] < 0.4  # statically stable with the CG at 60 mm
    assert v["trim"]["CL"] > 0 and -2 < v["trim"]["alpha_deg"] < 8
    pd = res["parasite_drag"]
    assert 0.01 < pd["CD0"] < 0.05 and set(pd["Comp_Label"]) == {"wing", "v_tail", "fuselage"}
    assert (out / "ref" / "vspaero").is_dir()


@pytest.mark.airframe
@pytest.mark.skipif(not FREECAD, reason="UNIFIED_AIRFRAME_FREECAD_PYTHON not set")
@pytest.mark.asyncio
async def test_reference_drone_end_to_end(tmp_path):
    mcp, out = server(tmp_path, FREECAD, ASB)
    async with Client(mcp, timeout=900) as client:
        result = await client.call_tool("airframe_build_fixed_wing", {"spec": spec(), "output_name": "ref"})
        rep = result.structured_content
        failed = [c for c in rep["checks"] if not c["pass"]]
        assert rep["all_checks_pass"], failed
        parts = {p["name"]: p for p in rep["parts"]}
        # analytic NACA 0012 volume: 0.68505 t c^2 per unit span, minus aileron and spar pocket
        wing = parts["Main wing · port"]["volume_mm3"] + parts["Aileron · port"]["volume_mm3"]
        pocket = next(p["removed_mm3"] for p in rep["pockets"] if p["host"] == "Main wing · port")
        analytic = 0.68505 * 0.12 * 200 ** 2 * 500
        assert wing + pocket == pytest.approx(analytic, rel=0.005)
        assert pocket == pytest.approx(math.pi * 5 ** 2 * 399, rel=0.01)
        assert parts["Main wing · port"]["mass_mesh"]["closed"]
        # totals equal the sum of parts (independent recomputation)
        total = sum(p["mass_g"] for p in rep["parts"])
        assert rep["totals"]["mass_g"] == pytest.approx(total, abs=0.01)
        cg_x = sum(p["mass_g"] * p["cg_mm"][0] for p in rep["parts"]) / total
        assert rep["totals"]["cg_mm"][0] == pytest.approx(cg_x, abs=0.01)
        assert abs(rep["totals"]["cg_mm"][1]) < 1e-3  # symmetric reference aircraft
        assert parts["Battery"]["mass_g"] == pytest.approx(200)
        assert (out / "ref" / "reference-glider.viewer.json").is_file()
        step = rep["files"]["step:Main wing · port"]["path"]
        insp = (await client.call_tool("airframe_inspect_step", {"path": f"ref/{step}"})).structured_content
        assert insp["all_valid"] and insp["all_closed"] and insp["solid_count"] == 1
        assert insp["total_volume_mm3"] == pytest.approx(parts["Main wing · port"]["volume_mm3"], rel=0.002)
        prev = (await client.call_tool("airframe_render_preview", {"output_name": "ref", "views": ["iso", "top"],
                                                                     "markers": [{"label": "CG", "position_mm": rep["totals"]["cg_mm"]}]})).structured_content
        assert len(prev["images"]) == 2 and all(i["bytes"] > 10_000 for i in prev["images"])
        again = await client.call_tool("airframe_build_fixed_wing", {"spec": spec(), "output_name": "ref"}, raise_on_error=False)
        assert again.is_error and "replace=true" in again.content[0].text
        if ASB:
            aero = (await client.call_tool("airframe_aero_crosscheck", {"spec": spec(), "mass_g": rep["totals"]["mass_g"],
                                                                         "cg_mm": rep["totals"]["cg_mm"], "velocity_m_s": 12})).structured_content
            assert aero["aerobuildup"]["CL"] == pytest.approx(aero["trim_target_cl"], abs=2e-3)
            assert aero["vlm"]["x_np"] > 0


def test_tip_fin_and_root_fairing_are_validated():
    good = spec()
    good["wing"]["tip_fin"] = {"cant_deg": 70, "stations": [{"s_mm": 0, "x_le_mm": 600, "chord_mm": 150}, {"s_mm": 120, "x_le_mm": 680, "chord_mm": 80}]}
    good["wing"]["root_fairing"] = {"stations": [{"s_mm": 0, "x_le_mm": 450, "chord_mm": 420}, {"s_mm": 120, "x_le_mm": 540, "chord_mm": 280}]}
    assert _spec_errors(good) == []
    bad = spec()
    bad["wing"]["tip_fin"] = {"cant_deg": 120, "stations": [{"s_mm": 0, "x_le_mm": 0, "chord_mm": 0}]}
    bad["wing"]["root_fairing"] = {"stations": []}
    errors = "\n".join(_spec_errors(bad))
    assert "tip_fin.stations" in errors and "cant_deg" in errors and "root_fairing.stations" in errors
