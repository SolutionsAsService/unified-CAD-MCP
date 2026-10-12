import asyncio
import json
import os
from pathlib import Path
import sys

from fastmcp import Client
from fastmcp.client.transports import StdioTransport
import pytest

from unified_cad_mcp.config import BackendConfig
from unified_cad_mcp.server import create_server


async def test_namespaces_schemas_annotations_and_persistent_processes(config):
    async with Client(create_server(config)) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
        assert set(tools) == {"unified_get_context", "kicad_identify", "cad_identify",
                              "kicad_rich_result", "cad_rich_result", "kicad_wait", "cad_wait"}
        assert tools["cad_identify"].input_schema["properties"]["value"]["type"] == "integer"
        assert tools["cad_rich_result"].output_schema == {
            "type": "object", "properties": {"domain": {"type": "string"}},
            "required": ["domain"],
        }
        assert tools["cad_identify"].annotations.read_only_hint is True
        assert tools["cad_identify"].meta["fixture"] == "preserve-me"
        first = (await client.call_tool("cad_identify", {"value": 42})).data
        again = (await client.call_tool("cad_identify")).data
        other = (await client.call_tool("kicad_identify")).data
        assert first["domain"] == "cad" and first["value"] == 42
        assert first["pid"] == again["pid"] != other["pid"]
        assert first["cwd"] == config.project_root
        assert other["domain"] == "kicad"
        with pytest.raises(Exception):
            await client.call_tool("identify")
        invalid = await client.call_tool("cad_identify", {"value": "not-int"}, raise_on_error=False)
        assert invalid.is_error


@pytest.mark.parametrize("fail", [False, True])
async def test_content_structured_image_metadata_and_error_preserved(config, fail):
    async with Client(create_server(config)) as client:
        await client.list_tools()
        result = await client.call_tool("cad_rich_result", {"fail": fail}, raise_on_error=False)
        assert result.is_error is fail
        assert result.structured_content == {"domain": "cad"}
        assert result.content[0].text == "backend text"
        assert result.content[1].type == "image"
        assert result.content[1].data == "aGVsbG8="
        assert result.content[1].mime_type == "image/png"
        assert result.meta["fixture_result"] == "unchanged"


async def test_resources_templates_prompts_and_context(config):
    async with Client(create_server(config)) as client:
        resources = await client.list_resources()
        uris = {str(resource.uri) for resource in resources}
        assert {"fixture://cad/status", "fixture://kicad/status", "unified://project/context", "unified://capabilities/inventory"} == uris
        value = await client.read_resource("fixture://cad/status")
        assert json.loads(value[0].text) == {"domain": "cad"}
        templates = await client.list_resource_templates()
        assert {str(t.uri_template) for t in templates} == {"fixture://cad/items/{item}", "fixture://kicad/items/{item}"}
        value = await client.read_resource("fixture://kicad/items/board")
        assert value[0].text == "kicad:board"
        assert {p.name for p in await client.list_prompts()} == {"cad_explain", "kicad_explain"}
        prompt = await client.get_prompt("cad_explain", {"subject": "box"})
        assert prompt.messages[0].content.text == "cad explanation for box"
        context = await client.read_resource("unified://project/context")
        assert json.loads(context[0].text)["units"]["stl"] == "UNKNOWN"


async def test_unavailable_backend_does_not_break_other_domain(config):
    config.backends["kicad"] = BackendConfig(command="unified-cad-nonexistent-executable")
    async with Client(create_server(config)) as client:
        names = {t.name for t in await client.list_tools()}
        assert "cad_identify" in names
        assert not any(name.startswith("kicad_") for name in names)
        context = (await client.call_tool("unified_get_context")).data
        assert context["backends"]["kicad"]["state"] == "unavailable"
        assert (await client.call_tool("cad_identify")).data["domain"] == "cad"


async def test_disabled_backend_never_started(config):
    config.backends["kicad"].enabled = False
    async with Client(create_server(config)) as client:
        await client.list_tools()
        assert not (Path(config.project_root) / "kicad.pid").exists()


async def test_backend_timeout_returns_error_without_replay(config):
    # The per-request timeout also bounds the MCP initialize request, so it must
    # exceed a slow host's interpreter/FastMCP startup (about 4 s observed on WSL)
    # while staying well below the operation's duration.
    config.backends["cad"].timeout_seconds = 8
    async with Client(create_server(config)) as client:
        assert "cad_wait" in {tool.name for tool in await client.list_tools()}
        result = await client.call_tool("cad_wait", {"seconds": 30}, raise_on_error=False)
        assert result.is_error
        assert (Path(config.project_root) / "cad.wait-calls").read_text() == "started\n"
        assert (await client.call_tool("cad_identify")).data["domain"] == "cad"
        assert (await client.call_tool("kicad_identify")).data["domain"] == "kicad"


async def test_real_stdio_entrypoint_and_shutdown(config, tmp_path):
    path = tmp_path / "config.json"
    path.write_text(config.model_dump_json())
    transport = StdioTransport(sys.executable, ["-m", "unified_cad_mcp.server", "--config", str(path)], keep_alive=False)
    async with Client(transport, timeout=30) as client:
        await client.list_tools()
        assert (await client.call_tool("cad_identify")).data["domain"] == "cad"
    if os.name == "posix":
        for name in ["cad", "kicad"]:
            pid = int((tmp_path / f"{name}.pid").read_text())
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)


async def test_hung_backend_initialization_is_bounded_and_cleaned(config):
    import time
    pid_path = Path(config.project_root) / "hung.pid"
    code = "import os,time;from pathlib import Path;Path(%r).write_text(str(os.getpid()));time.sleep(30)" % str(pid_path)
    config.backends["kicad"] = BackendConfig(command=sys.executable, args=["-c", code], init_timeout_seconds=0.2)
    started = time.monotonic()
    async with Client(create_server(config)) as client:
        names = {tool.name for tool in await client.list_tools()}
        assert "cad_identify" in names and "kicad_identify" not in names
        assert time.monotonic() - started < 15
    if os.name == "posix":
        with pytest.raises(ProcessLookupError):
            os.kill(int(pid_path.read_text()), 0)


@pytest.mark.skipif(os.name != "posix", reason="POSIX test-owned process termination")
async def test_dead_backend_does_not_restart_or_break_other_domain(config):
    import signal
    async with Client(create_server(config)) as client:
        await client.list_tools()
        original = (await client.call_tool("cad_identify")).data
        os.kill(original["pid"], signal.SIGTERM)
        await asyncio.sleep(0.1)
        failed = await client.call_tool("cad_identify", raise_on_error=False)
        assert failed.is_error
        assert int((Path(config.project_root) / "cad.pid").read_text()) == original["pid"]
        assert (await client.call_tool("kicad_identify")).data["domain"] == "kicad"


async def test_explicit_env_forwarded_without_ambient_credentials(config, monkeypatch):
    monkeypatch.setenv("UNIFIED_TEST_AMBIENT_SECRET", "synthetic-not-a-secret")
    monkeypatch.setenv("FIXTURE_CONFIGURED_VALUE", "ambient-value")
    config.backends["cad"].env["FIXTURE_CONFIGURED_VALUE"] = "explicit-value"
    async with Client(create_server(config)) as client:
        await client.list_tools()
        cad = (await client.call_tool("cad_identify")).data
        kicad = (await client.call_tool("kicad_identify")).data
        assert cad["configured_env"] == "explicit-value"
        assert kicad["configured_env"] is None
        assert not cad["ambient_secret_present"]
        assert not kicad["ambient_secret_present"]
