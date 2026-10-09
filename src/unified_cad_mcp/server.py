"""FastMCP-owned protocol proxy with explicit domains and isolated backend runtimes."""
from __future__ import annotations

import argparse
from contextlib import AsyncExitStack, asynccontextmanager
import json
import logging
from pathlib import Path
from typing import Any

from fastmcp import Client, FastMCP
from fastmcp.client.transports import StdioTransport
from fastmcp.server.providers.proxy import FastMCPProxy

from .config import Configuration, load_config

LOG = logging.getLogger(__name__)
INSTRUCTIONS = """Unified CAD MCP: one connection, two distinct engineering domains.
Start with unified_get_context. Use kicad_* for electronics/schematic/PCB workflows;
cad_* for asset inspection, STL/FreeCAD geometry, provenance and opt-in box revisions.
KiCad's kicad_design is a firmware/design-intent router, NOT mechanical CAD.
Names in upstream guidance are backend-local: use the matching kicad_ or cad_ tool
from tools/list; operation parameters are unchanged. Use exact resource URIs from
resources/list (namespaced by FastMCP), not guessed unprefixed upstream URIs.
STL units are UNKNOWN. FreeCAD length is mm. No automatic coordinate alignment,
board/enclosure synchronization, material inference, clearance/fit approval or
cross-backend transaction exists. Preserve UNKNOWN and user-attested evidence.
Project root is a coordination reference, NOT a filesystem sandbox; KiCad may
access current-user paths and invoke native programs/network. CAD root checks are
backend-specific. Never infer native KiCad/FreeCAD availability from tool discovery.
Do not replay a timed-out mutation: inspect its effects before retrying.
"""


def create_server(config: Configuration) -> FastMCP:
    clients: dict[str, Client] = {}
    states: dict[str, dict[str, Any]] = {}
    active: set[str] = set()

    @asynccontextmanager
    async def lifespan(server: FastMCP):
        async with AsyncExitStack() as stack:
            for name, client in clients.items():
                try:
                    await stack.enter_async_context(client)
                except Exception as error:
                    states[name] = {"state": "unavailable", "error_type": type(error).__name__,
                                    "action": "Check backend install/configuration and restart the unified server."}
                    LOG.warning("Backend %s failed to initialize (%s)", name, type(error).__name__)
                else:
                    active.add(name)
                    states[name] = {"state": "initialized",
                                    "native_cad_runtime": "not_checked"}
            try:
                yield
            finally:
                active.clear()

    server = FastMCP("unified-CAD-MCP", instructions=INSTRUCTIONS, lifespan=lifespan)
    for name in ("kicad", "cad"):
        backend = config.backends.get(name)
        if backend is None or not backend.enabled:
            states[name] = {"state": "disabled"}
            continue
        client = Client(
            StdioTransport(backend.command, backend.args, env={"FASTMCP_CHECK_FOR_UPDATES": "off", "FASTMCP_SHOW_SERVER_BANNER": "false", **backend.env},
                           cwd=backend.cwd or config.project_root, keep_alive=False),
            name=f"unified-{name}", timeout=backend.timeout_seconds,
            init_timeout=backend.init_timeout_seconds, mode="legacy",
        )
        clients[name] = client
        states[name] = {"state": "not_started"}

        def connected_client(domain=name, instance=client):
            if domain not in active or not instance.is_connected():
                raise RuntimeError(f"{domain} backend unavailable; call unified_get_context")
            return instance

        proxy = FastMCPProxy(name=f"{name}-backend", client_factory=connected_client)
        server.mount(proxy, namespace=name)

    @server.tool(annotations={"readOnlyHint": True, "destructiveHint": False,
                              "idempotentHint": True, "openWorldHint": False})
    async def unified_get_context() -> dict:
        """Report routing, startup availability and explicit project/units boundaries; not design approval."""
        return {
            "project_root": config.project_root,
            "backends": {name: dict(state) for name, state in states.items()},
            "availability_scope": "startup handshake only; native applications and later liveness are not implied",
            "routing": {"kicad_*": "electronics, schematic, PCB, firmware intent",
                        "cad_*": "asset/geometry inspection, provenance, opt-in box revisions"},
            "units": {"stl": "UNKNOWN", "freecad_length": "mm",
                      "kicad": "read each tool schema; do not forward internal coordinates without conversion"},
            "coordinate_alignment": "explicit user-provided transform required",
            "shared_document_transaction": False,
            "filesystem_sandbox": False,
            "handoff": "Export a board using kicad_export; explicitly select its output in the CAD asset root, then inspect it with cad_* tools. No automatic copying/import/fit certification.",
        }

    @server.resource("unified://project/context")
    async def project_context() -> str:
        return json.dumps(await unified_get_context(), sort_keys=True)

    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Unified KiCad + CAD MCP (stdio)")
    parser.add_argument("--config", type=Path, required=True, help="Trusted operator configuration JSON")
    parser.add_argument("--check-config", action="store_true", help="Validate paths/schema without starting backends")
    args = parser.parse_args()
    try:
        config = load_config(args.config)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    if args.check_config:
        print("Configuration valid; no backend or native runtime was tested.")
        return
    logging.basicConfig(level=logging.WARNING)
    create_server(config).run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()
