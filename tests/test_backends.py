"""Real pinned source servers. Native KiCad and FreeCAD execution is not asserted."""
import json
import os
from pathlib import Path
import sys

from fastmcp import Client
from fastmcp.client.transports import StdioTransport
import pytest

from unified_cad_mcp.config import load_config

pytestmark = pytest.mark.backends


async def test_pinned_servers_through_real_unified_stdio(tmp_path):
    selected = os.environ.get("UNIFIED_CAD_BACKEND_CONFIG")
    if not selected:
        pytest.skip("Set UNIFIED_CAD_BACKEND_CONFIG after installing pinned backends")
    config = load_config(Path(selected))
    config.backends["cad"].env["DESIGN_MCP_ASSET_ROOT"] = str(tmp_path)
    config.backends["cad"].env.pop("DESIGN_MCP_REVISION_ROOT", None)
    config.backends["cad"].env.pop("DESIGN_MCP_PREVIEW_ROOT", None)
    (tmp_path / "example.txt").write_text("integration artifact\n")
    path = tmp_path / "config.json"
    path.write_text(config.model_dump_json())
    transport = StdioTransport(sys.executable, ["-m", "unified_cad_mcp.server", "--config", str(path)], keep_alive=False)
    async with Client(transport, mode="legacy", timeout=60, init_timeout=60) as client:
        tools = await client.list_tools()
        names = [tool.name for tool in tools]
        assert len(names) == len(set(names)) == (87 if "pcb" in config.backends and config.backends["pcb"].env.get("KICAD_MCP_ENABLE_SCHEMATIC") == "1" else 71 if "pcb" in config.backends else 52)
        assert sum(n.startswith("kicad_") for n in names) == 18
        assert sum(n.startswith("cad_") for n in names) == 33
        assert "kicad_design" in names and "cad_create_box_revision" in names
        converted = await client.call_tool("cad_convert_quantity", {"value": 25.4, "from_unit": "mm", "to_unit": "m"})
        assert not converted.is_error
        assert json.loads(converted.content[0].text)["value"] == 0.0254
        guidance = await client.call_tool("kicad_get_usage_guidance", {"operation": "tactics"})
        assert not guidance.is_error and len(guidance.content[0].text) > 30
        scope = await client.call_tool("cad_get_scope")
        assert "READ_ONLY_ASSET_CATALOG" in scope.content[0].text
        inspected = await client.call_tool("cad_inspect_asset", {"relative_path": "example.txt"})
        assert not inspected.is_error and "sha256" in inspected.content[0].text
        rejected = await client.call_tool("cad_inspect_asset", {"relative_path": "../outside.txt"}, raise_on_error=False)
        assert rejected.is_error
        resources = await client.list_resources()
        assert len(resources) == 8
        assert "design://cad/project/assets" in {str(r.uri) for r in resources}
        revisions = await client.read_resource("design://cad/project/revisions")
        assert json.loads(revisions[0].text)["status"] == "CONFIGURATION_REQUIRED"
