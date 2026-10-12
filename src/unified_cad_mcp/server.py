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
from .airframe import create_airframe_server
from .drone import create_drone_server

LOG = logging.getLogger(__name__)
INSTRUCTIONS = """Unified CAD MCP: one connection, four optional isolated engineering backends.
Start with unified_get_context. Use kicad_* for electronics/schematic/PCB workflows;
pcb_* for NiRuLabs live IPC PCB routing (separate unsaved GUI state);
cad_* for asset inspection, STL/FreeCAD geometry, provenance and opt-in box revisions.
freecad_* controls a separate live FreeCAD GUI through NiRuLink; connect explicitly.
execute_python has unrestricted current-user code/file/network access, NOT a sandbox.
Provisional helper tools require experimental opt-in and may be incorrect.
Never infer document ownership from cad_*; select/inspect documents explicitly.
NiRuLink returns text/repr, not images or structured geometry.
KiCad's kicad_design is a firmware/design-intent router, NOT mechanical CAD.
Names in upstream guidance are backend-local: use the matching kicad_, cad_, pcb_ or freecad_ tool
from tools/list; operation parameters are unchanged. Use exact resource URIs from
resources/list (namespaced by FastMCP), not guessed unprefixed upstream URIs.
STL units are UNKNOWN. FreeCAD length is mm. No automatic coordinate alignment,
board/enclosure synchronization, material inference, clearance/fit approval or
cross-backend transaction exists. Preserve UNKNOWN and user-attested evidence.
Project root is a coordination reference, NOT a filesystem sandbox; KiCad may
access current-user paths and invoke native programs/network. CAD root checks are
backend-specific. Never infer native KiCad/FreeCAD availability from tool discovery.
pcb_route_pads requires compatible native push-and-shove extensions; stock KiCad
support is not established and upstream may commit despite reported violations.
Inspect JSON error fields as well as isError; upstream error conventions vary.
Do not replay a timed-out mutation: inspect its effects before retrying.
drone_* (only when enabled) are pure in-process HEURISTIC sizing calculators
(mass/CG/inertia budget, hover propulsion, fixed-wing aero/stability/performance,
weight-class notes); they read no CAD documents and their outputs are estimates,
not test data or legal advice.
airframe_* (only when configured) build parametric fixed-wing CAD headlessly in a
separate FreeCAD interpreter, validate it (closed solids, containment, interference,
mass budget), render PNG previews and cross-check aerodynamics with AeroSandbox.
Mass properties come from exact mesh integrals because OCCT GProp volumes were
measured wrong on BSpline solids in the tested FreeCAD build.
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
                                    "error": (str(error) or repr(error))[:500],
                                    "init_timeout_seconds": config.backends[name].init_timeout_seconds,
                                    "action": "Check backend install/configuration (and init_timeout_seconds on slow hosts) and restart the unified server."}
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
    for name in ("kicad", "cad", "pcb", "freecad"):
        backend = config.backends.get(name)
        if backend is None or not backend.enabled:
            states[name] = {"state": "disabled"}
            continue
        client = Client(
            StdioTransport(backend.command, backend.args, env={"FASTMCP_CHECK_FOR_UPDATES": "off", "FASTMCP_SHOW_SERVER_BANNER": "false", "KICAD_MCP_NO_TELEMETRY": "1", **backend.env},
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

    if config.drone_tools:
        server.mount(create_drone_server(), namespace="drone")
        states["drone"] = {"state": "builtin", "native_cad_runtime": "not_required",
                           "kind": "heuristic estimators"}
    else:
        states["drone"] = {"state": "disabled"}

    if config.airframe is not None:
        server.mount(create_airframe_server(config.airframe), namespace="airframe")
        states["airframe"] = {"state": "builtin", "kind": "headless worker subprocesses",
                              "freecad_python": config.airframe.freecad_python,
                              "aerosandbox_python": config.airframe.aerosandbox_python or "not configured",
                              "output_root": config.airframe.output_root,
                              "native_cad_runtime": "checked per call (worker reports FreeCAD/AeroSandbox version)"}
    else:
        states["airframe"] = {"state": "disabled"}

    @server.tool(annotations={"readOnlyHint": True, "destructiveHint": False,
                              "idempotentHint": True, "openWorldHint": False})
    async def unified_get_context() -> dict:
        """Report routing, startup availability and explicit project/units boundaries; not design approval."""
        return {
            "project_root": config.project_root,
            "backends": {name: dict(state) for name, state in states.items()},
            "availability_scope": "startup handshake only; native applications and later liveness are not implied",
            "routing": {"kicad_*": "electronics, schematic, PCB, firmware intent",
                        "cad_*": "asset/geometry inspection, provenance, opt-in box revisions",
                        "pcb_*": "live KiCad IPC routing; separate session, board and transactions",
                        "freecad_*": "NiRuLink live GUI; arbitrary Python, independent active document/session",
                        "drone_*": "opt-in pure-Python heuristic sizing: mass/CG budget, multirotor hover propulsion, fixed-wing aero/stability/performance, weight class; no documents",
                        "airframe_*": "opt-in headless FreeCAD airframe build/validation/STEP inspection/PNG previews and AeroSandbox aero cross-check; writes only under airframe.output_root"},
            "units": {"stl": "UNKNOWN", "freecad_length": "mm",
                      "kicad": "read each tool schema; do not forward internal coordinates without conversion"},
            "coordinate_alignment": "explicit user-provided transform required",
            "shared_document_transaction": False,
            "inventory_resource": "unified://capabilities/inventory",
            "pcb_preconditions": "Running compatible KiCad GUI with IPC enabled, explicit document selection. Experimental schematic tools require opt-in. route_pads reaches push-and-shove bridge methods, but no stock native support is implied. Other unregistered bridge methods are listed in the inventory.",
            "pcb_connection_adapter": "Launch adapter corrects upstream api_token/socket_path mismatch; optional UNIFIED_PCB_SOCKET selects the socket explicitly. No credentials are logged.",
            "freecad_preconditions": "User-installed FreeCAD GUI 1.0+ with pinned NiRuLink addon on localhost; explicit connect. Only connect/execute_python default. No native runtime implied by initialization, rollback, retries or shared document ownership.",
            "filesystem_sandbox": False,
            "handoff": "Pinned kicad_export supports gerbers/bom_csv/thumbnail, not STEP. Obtain STEP/FCStd using an explicitly chosen native workflow (freecad_execute_python can export from its independent GUI document), select it in the CAD asset root, then inspect with cad_*. No automatic copying/import/fit certification.",
        }

    @server.resource("unified://project/context")
    async def project_context() -> str:
        return json.dumps(await unified_get_context(), sort_keys=True)

    @server.resource("unified://capabilities/inventory")
    async def capability_inventory() -> str:
        return (Path(__file__).parent / "coverage.json").read_text(encoding="utf-8")

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
