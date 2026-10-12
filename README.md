# Unified CAD MCP

One stdio MCP connection for **KiCad electronics** and **CAD inspection / opt-in FreeCAD box revisions**, and **live KiCad IPC routing**, composed from the two SolutionsAsService repositories and NiRuLabs/kicad-mcp-server plus **NiRuLink live FreeCAD GUI control**. This repository is the integration entry point, not a claim to implement all CAD engineering.

**Why combine them?** The agent gets one catalog, explicit tool ownership, a shared project reference, and one documented PCB-to-mechanical handoff. Distinct namespaces avoid confusing KiCad's firmware design router with mechanical CAD. Combining servers does **not** automatically align coordinates, synchronize documents, validate clearances, or make incomplete engineering evidence true.

Integration by [SolutionsAsService](https://github.com/SolutionsAsService). Upstream authorship and licensing remain separate; see [source provenance](docs/provenance.md).

## What is available

| Namespace | Source | Actual scope |
|---|---|---|
| kicad_* | [kicad-mcp](https://github.com/SolutionsAsService/kicad-mcp) | 18 tools: schematic/PCB domain routers, libraries, routing, export, firmware intent and guidance |
| cad_* | [design-mcp-server](https://github.com/SolutionsAsService/design-mcp-server) | 33 tools: asset/STL inspection, optional FreeCAD geometry, box revisions, provenance, quantities and guarded candidate/assembly records |
| pcb_* | [NiRuLabs/kicad-mcp-server](https://github.com/NiRuLabs/kicad-mcp-server) | 19 default tools, plus 16 opt-in experimental schematic tools; live IPC inspection, pathfinding, track/via editing and routing |
| freecad_* | [NiRuLabs/NiRuLinkFreeCAD](https://github.com/NiRuLabs/NiRuLinkFreeCAD) | 2 default tools (connect/Python); 14 opt-in provisional modeling/export/inspection helpers; separate live GUI |
| drone_* | This integration (opt-in, in-process) | 3 pure-Python HEURISTIC drone sizing calculators: mass/CG/inertia budget, hover propulsion estimate, FAA/EASA weight-class notes. Off unless the config sets "drone_tools": true |
| unified_get_context | This integration | Routing, startup status, project reference, units and limitations |

At the pinned revisions: **73 default tools, or 103 with both experimental sets enabled; 8 resources** (18 KiCad + 33 CAD + 19/35 PCB + 2/16 FreeCAD + context; six design resources + context + exhaustive capability inventory). Re-verified on 2026-10-12 through the real stdio entrypoint. Enabling drone_tools adds 3 tools (76 / 106). A freecad backend with "enabled": false (the bootstrap default since 2026-10-12) contributes 0, giving 71 / 87. Capability discovery is not proof that native KiCad or FreeCAD is installed. Resource templates and prompts are also proxied if a backend supplies them. Names and schemas are discovered dynamically, not reimplemented in a stale hand-maintained catalog.

### Architecture

~~~mermaid
flowchart LR
  Agent[MCP client] --> Unified[Unified CAD MCP: stdio]
  Unified --> Context[unified_get_context: project / units / startup status]
  Unified -->|kicad_*| K[Persistent KiCad subprocess]
  Unified -->|cad_*| C[Persistent design subprocess]
  Unified -->|pcb_*| P[Persistent NiRuLabs MCP1 subprocess]
  P --> G[Compatible running KiCad GUI IPC]
  Unified -->|freecad_*| N[NiRuLink stdlib MCP plus safety adapter]
  N --> Q[User-installed FreeCAD GUI addon: loopback TCP]
  K --> E[KiCad / pcbnew / kicad-cli: optional native install]
  C --> F[FreeCAD bundled Python: optional native install]
  K -. explicit export and coordinate / unit handoff .-> C
  C -. source hashes / UNKNOWN evidence .-> Context
~~~

[FastMCP 4.1 composition and proxies](https://gofastmcp.com/servers/composition) own protocol routing and content serialization. Backends run in **separate Python environments**: current KiCad uses FastMCP 4 / MCP 2, while design and NiRuLabs use isolated MCP 1 runtimes. The two KiCad distributions both import as kicad_mcp and MUST NOT share a Python environment. Installing both into one Python environment would create a dependency conflict. The original three backends use MCP 2025-11-25; NiRuLink uses MCP 2024-11-05 in its isolated stdlib server. FastMCP handles both.

## Quick start

Requires Python 3.11+, Git, and pip 23+ (pip must support --python). The tested lock snapshots were produced with Python 3.12 on Linux. Python's venv must be available; OS package installation is not automated here. Only the unified .venv needs pip: backend venvs are created without pip and populated with pip --python. If ensurepip is missing (Debian/Ubuntu without python3-venv, no sudo), create the venv with python3 -m venv --without-pip .venv and bootstrap pip with the official https://bootstrap.pypa.io/get-pip.py into .venv/bin/python.

~~~sh
git clone https://github.com/SolutionsAsService/unified-CAD-MCP.git
cd unified-CAD-MCP
python3 -m venv .venv
.venv/bin/python -m pip install -c locks/unified-py312.txt -e '.[dev]'
# Choose EXISTING directories you intend the backends to work with.
.venv/bin/python scripts/bootstrap_backends.py \
  --project-root /absolute/path/to/project \
  --asset-root /absolute/path/to/project/assets \
  --enable-drone-tools   # optional: heuristic drone_* calculators
.venv/bin/unified-cad-mcp --config unified-cad.local.json --check-config
.venv/bin/unified-cad-mcp --config unified-cad.local.json
~~~

On Windows use .venv\Scripts\python.exe and .venv\Scripts\unified-cad-mcp.exe; the bootstrap chooses the correct backend interpreter paths. Windows execution is not claimed tested.

The bootstrap installs **exact Git commits** from [sources.lock.json](sources.lock.json), with [tested dependency constraints](locks/). It creates .backends/kicad, .backends/cad and .backends/pcb plus .backends/freecad, writes the freecad backend as "enabled": false unless --enable-freecad (or --backend freecad) is given, because its default execute_python tool is unrestricted code execution in a live GUI, installs no GUI/native CAD applications, never changes host MCP settings, and refuses to overwrite an existing config. Use --dry-run to inspect commands without writes, or --backend cad / --backend kicad / --backend pcb / --backend freecad for one domain. Re-running against existing managed venvs may update packages to the selected locks; inspect before doing so. Network access is needed during installation. Source pins and dependency versions are recorded; this is not a hash-locked supply-chain attestation.

**Licensing:** this integration repository does not yet contain a LICENSE file; until the owner adds one, no reuse license is granted beyond GitHub's terms. Backend source is installed separately, not copied/relicensed into this repository. KiCad has an MIT license. No license was declared in the inspected design source; see [provenance](docs/provenance.md) before redistributing its code.

### Live FreeCAD setup

See [exact native prerequisites, installation, security and provisional-tool limits](docs/freecad.md). Bootstrap installs only the pinned stdlib server, **not the FreeCAD GUI/addon**. Existing configs remain unchanged; merge the optional backend explicitly. Example config disables it. Default calls are freecad_connect_to_freecad and freecad_execute_python; the latter is unrestricted code under the GUI user, not sandboxed. No unauthenticated remote endpoint is allowed by the adapter.

### MCP host configuration

Add one stdio server entry to your host's supported configuration. Example (adapt paths; not automatically installed):

~~~json
{
  "mcpServers": {
    "unified-cad": {
      "command": "/absolute/path/to/unified-CAD-MCP/.venv/bin/unified-cad-mcp",
      "args": ["--config", "/absolute/path/to/unified-CAD-MCP/unified-cad.local.json"]
    }
  }
}
~~~

OpenClaw uses mcp.servers instead of mcpServers; use the same command/args through its supported MCP settings. Avoid enabling both standalone source servers in the same client unless duplicate catalogs are intentional. No host configuration or Gateway restart is performed by this project.

## Configuration and boundaries

[Example JSON](unified-cad.example.json). Configuration is **trusted executable configuration**, not an uploaded design document. It selects executable commands without a shell. Project/cwd paths resolve relative to the config file, not the launch directory. Backend env entries override the MCP SDK's minimal inherited environment (for example HOME and PATH), not a copy of the parent environment. Arbitrary parent credentials and DESIGN_MCP_/KICAD_ variables are **not inherited**: explicitly configure required backend variables in the ignored local JSON. Values are passed literally (no shell or ${VAR} expansion). Use absolute executable paths; a bare executable is searched on PATH, and command/argument/env paths are not rebased to the config directory. Never commit secrets in JSON. Unknown namespaces/fields and invalid timeouts are rejected.

- Project root is shared context, **not a sandbox**. KiCad permits broader filesystem access and may invoke native programs or network tools under current-user permissions.
- Design assets must reside beneath DESIGN_MCP_ASSET_ROOT. That backend owns path validation; unified does not weaken or replace it.
- CAD writes require DESIGN_MCP_REVISION_ROOT: an existing dedicated output directory distinct from the asset root. Previews also require DESIGN_MCP_PREVIEW_ROOT, distinct from both. These are **not enabled by default**. Use non-overlapping roots; never place untrusted manifests in an output root.
- Optional **cad_** FreeCAD operations require DESIGN_MCP_FREECAD_PYTHON pointing to a compatible interpreter with FreeCAD and Part modules. KiCad has separate native setup requirements; consult its pinned source documentation.
- Opening untrusted native CAD documents invokes complex parsers. Process separation is not OS sandboxing.
- One long-lived process per backend preserves KiCad's **in-process** PCB write locks. Separate unified/standalone processes are not cross-process locked. Do not run multiple writers on a board. One trusted operator session per stdio server is intended; no public HTTP service or multi-tenant isolation is provided.
- Backend initialization has a finite deadline. One missing backend does not remove the other. unified_get_context reports startup failure; fix it then restart. No automatic reconnect or mutation replay is implemented. A timeout may leave a native subprocess operation running: inspect effects before retrying.
- timeout_seconds defaults to 120 (maximum 3600); init_timeout_seconds defaults to 20 (maximum 60). Long autorouting needs a deliberate larger backend and host request timeout. The per-request timeout_seconds also bounds the MCP initialize request, so the effective startup deadline is the smaller of the two; keep timeout_seconds above backend interpreter startup (several seconds on slow/WSL hosts).
- Tool parameters, schemas, annotations, content, images, structured results and error flags are relayed by FastMCP. Names gain a namespace. Backend descriptions/prose may still use local tool names; apply the namespace. Upstream server instructions are not silently promoted to unified instructions.
- FastMCP banner/update checks are disabled for child processes by default. This does not disable legitimate upstream network features or provide an air gap.

## PCB to enclosure workflow

1. Read unified_get_context, then kicad_get_usage_guidance for PCB operations.
2. Use kicad_* for board work. kicad_design means firmware-driven electrical intent, **not** generic 3D modeling.
3. The pinned kicad_export router offers gerbers, bom_csv and thumbnail, NOT STEP export. Obtain STEP/FCStd with an explicitly selected external native workflow, then place it in the CAD asset root. Do not invent an export operation. No automatic copy/import occurs.
4. Inspect the STEP/FCStd with cad_* if FreeCAD is available. Record source hash, units, origin/axes, transforms and selected objects. STL units remain **UNKNOWN** until externally established; FreeCAD lengths are mm.
5. Preserve backend UNKNOWN fit results. AABB overlap is not a collision proof; user-attested records are not independent verification. Box-only authoring is not arbitrary enclosure/assembly design.
6. Recheck hashes and affected geometry after changes. There is no cross-backend transaction, live synchronization, manufacturing certification, or automatic engineering approval.

Resource example: design://project/assets becomes **design://cad/project/assets**. Consult resources/list for exact names.

## Drone design

The repository can host early drone/UAV concept work, but most of the workflow is
not yet covered. See the [drone design gap analysis and roadmap](docs/DRONE-DESIGN-PLAN.md):
electronics (KiCad, native) is strongest; airframe CAD is GUI-bound or box-only;
aerodynamics, FEA, harness, PX4/ArduPilot parameters and SITL simulation have no tools.
The opt-in drone_* calculators (drone_mass_cg_budget, drone_propulsion_estimate,
drone_regulatory_weight_class) are deterministic HEURISTIC estimates (momentum theory,
lumped efficiencies, coarse regulatory bands) with their assumptions returned in every
result. They are not bench data, FEA, flight tests or legal advice.

## Development / evidence

~~~sh
.venv/bin/python -m pytest -q
# After bootstrap: actual pinned upstream server processes, no native CAD required
UNIFIED_CAD_BACKEND_CONFIG="$PWD/unified-cad.local.json" .venv/bin/python -m pytest -q -m backends
~~~

Default protocol tests launch **synthetic MCP peers**, including through the real unified stdio entrypoint. They check routing, schemas, metadata/content/errors, resources/templates/prompts, process reuse/cleanup, missing backends and deadlines. Opt-in tests exercise the pinned upstream servers. Neither substitutes for native PCB export or FreeCAD BREP/revision tests. See [verification evidence](docs/verification.md) and the [2026-10-12 install/audit report](docs/audit-2026-10-12.md). CI runs protocol/config tests and pinned backend integration, not GUI/hardware tests or publication.

## Live PCB backend and exhaustive coverage

Read [coverage and native limits](docs/coverage.md). The full pinned, source-derived
inventory is packaged as src/unified_cad_mcp/coverage.json and available through
unified://capabilities/inventory. It is an audit snapshot, not runtime health.

The PCB bootstrap launches a stdlib adapter inside its separate environment.
It fixes the upstream api_token/socket_path positional mismatch without changing
any tool schema or result. Optional backend env UNIFIED_PCB_SOCKET selects the
actual socket; pass api_token only as the tool argument. Tokens are not logged.
An absent explicit Unix socket fails before NNG can wait indefinitely. Automatic
upstream discovery may choose the first socket/document: explicit selection is
recommended when multiple KiCad instances are open. Do not infer that the live
GUI board matches a file being edited by kicad_*.

Use --enable-pcb-schematic during bootstrap, or explicitly set the PCB backend
KICAD_MCP_ENABLE_SCHEMATIC=1, to advertise all 35 upstream tools. Default is 19.
The extra 16 require experimental native schematic IPC support. Discovery alone
never certifies those native commands. pcb_route_pads uses upstream push-and-shove
extensions; stock KiCad support is NOT established. Its upstream implementation
can commit despite reported violations. Never treat its success as DRC approval.
No fake native success or no-op substitute is supplied.

KiCad telemetry writes are disabled by default with KICAD_MCP_NO_TELEMETRY=1.
No project indexing, supplier download, GUI installation, or host registration
occurs at startup. Invoking the corresponding upstream tools remains an explicit
operator action. Error payloads are preserved: some upstream failures are JSON
text with an error field while isError remains false; inspect both.
