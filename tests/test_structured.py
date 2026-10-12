"""F10 structured results for proxied cad_* tools, F11 STL units/shells, F15 KiCad probe."""
import json
import struct

from fastmcp import Client
import pytest

from unified_cad_mcp.config import BackendConfig
from unified_cad_mcp.kicad_native import detect_kicad
from unified_cad_mcp.server import create_server
from unified_cad_mcp.stl_report import analyze_shells, parse_stl, resolve_units, write_sidecar


def cube(lo, hi, inward=False):
    x0, y0, z0 = lo
    x1, y1, z1 = hi
    v = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0), (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    quads = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    tris = []
    for a, b, c, d in quads:
        for t in ((a, b, c), (a, c, d)):
            tris.append(tuple(v[i] for i in (t[::-1] if inward else t)))
    return tris


def binary_stl(triangles):
    data = bytearray(80) + struct.pack("<I", len(triangles))
    for t in triangles:
        data += struct.pack("<12fH", 0, 0, 0, *t[0], *t[1], *t[2], 0)
    return bytes(data)


def test_shell_analysis_reports_internal_cavity():
    outer = cube((0, 0, 0), (10, 10, 10))
    cavity = cube((3, 3, 3), (5, 5, 5), inward=True)
    separate = cube((20, 0, 0), (21, 1, 1))
    report = analyze_shells(parse_stl(binary_stl(outer + cavity + separate)))
    assert report["shell_count"] == 3
    assert report["outer_shell_count"] == 2
    assert report["internal_cavity_count"] == 1
    assert report["net_signed_volume_model_units3"] == pytest.approx(1000 - 8 + 1)
    roles = {s["role"] for s in report["shells"]}
    assert roles == {"outer_shell", "internal_cavity"}


def test_inverted_and_ascii(tmp_path):
    tris = cube((0, 0, 0), (1, 1, 1), inward=True)
    text = "solid t\n" + "".join("facet normal 0 0 0\nouter loop\n" + "".join(f"vertex {a} {b} {c}\n" for a, b, c in t)
                                 + "endloop\nendfacet\n" for t in tris) + "endsolid t\n"
    report = analyze_shells(parse_stl(text.encode()))
    assert report["inverted_shell_count"] == 1 and report["outer_shell_count"] == 1


def test_units_resolution_order(tmp_path):
    stl = tmp_path / "part.stl"
    stl.write_bytes(binary_stl(cube((0, 0, 0), (1, 1, 1))))
    default = resolve_units(stl, None)
    assert default["units"] == "mm" and default["assumed"] and default["source"] == "default_assumption"
    assert "ASSUMED" in default["warnings"][0]
    write_sidecar(stl, "m")
    side = resolve_units(stl, None)
    assert side["units"] == "m" and side["source"] == "sidecar:part.stl.meta.json" and not side["assumed"]
    arg = resolve_units(stl, "in")
    assert arg["units"] == "in" and arg["to_mm"] == 25.4 and "override" in arg["warnings"][0]
    with pytest.raises(ValueError):
        resolve_units(stl, "furlong")


async def test_proxied_cad_tools_return_structured_content(config):
    async with Client(create_server(config)) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
        assert tools["cad_text_json"].output_schema["type"] == "object"
        assert tools["kicad_text_json"].output_schema is None  # only cad_* is covered
        # Upstream-declared schemas are untouched.
        assert tools["cad_rich_result"].output_schema["required"] == ["domain"]
        result = await client.call_tool("cad_text_json", {"kind": "object"})
        assert result.structured_content == {"domain": "cad", "n": 1}
        assert json.loads(result.content[0].text) == {"domain": "cad", "n": 1}  # text kept
        listed = await client.call_tool("cad_text_json", {"kind": "list"})
        assert listed.structured_content == {"result": [1, 2]}
        plain = await client.call_tool("cad_text_json", {"kind": "plain"})
        assert plain.structured_content == {"text": "not json at all"}
        raw = await client.call_tool("kicad_text_json", {"kind": "object"})
        assert raw.structured_content is None


async def test_stl_tool_units_argument_sidecar_and_shells(config, tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "wing.stl").write_bytes(binary_stl(cube((0, 0, 0), (2, 2, 2)) + cube((0.5, 0.5, 0.5), (1, 1, 1), inward=True)))
    config.backends["cad"].env["DESIGN_MCP_ASSET_ROOT"] = str(assets)
    async with Client(create_server(config)) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
        assert "units" in tools["cad_inspect_stl_geometry"].input_schema["properties"]
        default = (await client.call_tool("cad_inspect_stl_geometry", {"relative_path": "wing.stl"})).structured_content
        assert default["coordinate_units"].startswith("UNKNOWN")  # upstream verbatim
        assert default["units"]["source"] == "default_assumption" and default["units"]["assumed"] is True
        assert default["coordinate_units_resolved"] == "mm"
        assert default["shell_analysis"]["internal_cavity_count"] == 1
        assert default["shell_analysis"]["shell_count"] == 2
        metres = (await client.call_tool("cad_inspect_stl_geometry", {"relative_path": "wing.stl", "units": "m"})).structured_content
        assert metres["units"]["source"] == "argument"
        assert metres["metrics_mm"]["bounding_box_mm"]["extent"] == [2000, 2000, 2000]
        assert metres["metrics_mm"]["enclosed_volume_mm3"] == pytest.approx(7e9)
        write_sidecar(assets / "wing.stl", "cm")
        side = (await client.call_tool("cad_inspect_stl_geometry", {"relative_path": "wing.stl"})).structured_content
        assert side["units"]["source"] == "sidecar:wing.stl.meta.json" and side["metrics_mm"]["surface_area_mm2"] == 2400
        bad = await client.call_tool("cad_inspect_stl_geometry", {"relative_path": "wing.stl", "units": "furlong"},
                                     raise_on_error=False)
        assert bad.is_error
        outside = (await client.call_tool("cad_inspect_stl_geometry", {"relative_path": "../x.stl"})).structured_content
        assert outside["shell_analysis"]["status"] == "unavailable"


def test_kicad_probe_missing_gives_actionable_hint(tmp_path, monkeypatch):
    monkeypatch.delenv("UNIFIED_KICAD_CLI", raising=False)
    report = detect_kicad(path=str(tmp_path), python=None)
    assert report["status"] == "missing" and report["kicad_cli"] is None
    assert "sudo apt install" in report["action"] and "which kicad-cli" in report["action"]


def test_kicad_probe_detects_fake_cli(tmp_path, monkeypatch):
    monkeypatch.delenv("UNIFIED_KICAD_CLI", raising=False)
    cli = tmp_path / "kicad-cli"
    cli.write_text("#!/bin/sh\necho 9.0.4\n")
    cli.chmod(0o755)
    py = tmp_path / "python3"
    py.write_text("#!/bin/sh\necho 9.0.4\n")
    py.chmod(0o755)
    report = detect_kicad(path=str(tmp_path))
    assert report["status"] == "available" and report["kicad_cli_version"] == "9.0.4"


async def test_context_reports_native_kicad(config):
    async with Client(create_server(config)) as client:
        context = (await client.call_tool("unified_get_context")).data
        assert context["backends"]["kicad"]["native_kicad"]["status"] in {"available", "partial", "missing"}
        assert "native_kicad" not in context["backends"]["cad"]
