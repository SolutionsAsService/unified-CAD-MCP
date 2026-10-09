# Unified CAD MCP

One stdio MCP connection for **KiCad electronics** and **CAD inspection / opt-in FreeCAD box revisions**, composed from the requested SolutionsAsService repositories. This repository is the integration entry point, not a claim to implement all CAD engineering.

**Why combine them?** The agent gets one catalog, explicit tool ownership, a shared project reference, and one documented PCB-to-mechanical handoff. Distinct namespaces avoid confusing KiCad's firmware design router with mechanical CAD. Combining servers does **not** automatically align coordinates, synchronize documents, validate clearances, or make incomplete engineering evidence true.

Integration by [SolutionsAsService](https://github.com/SolutionsAsService). Upstream authorship and licensing remain separate; see [source provenance](docs/provenance.md).

## What is available

| Namespace | Source | Actual scope |
|---|---|---|
| kicad_* | [kicad-mcp](https://github.com/SolutionsAsService/kicad-mcp) | 18 tools: schematic/PCB domain routers, libraries, routing, export, firmware intent and guidance |
| cad_* | [design-mcp-server](https://github.com/SolutionsAsService/design-mcp-server) | 33 tools: asset/STL inspection, optional FreeCAD geometry, box revisions, provenance, quantities and guarded candidate/assembly records |
| unified_get_context | This integration | Routing, startup status, project reference, units and limitations |

At the pinned revisions: **52 tools and 7 resources total** (18 + 33 + 1 tools; six design resources + one integration resource). Capability discovery is not proof that native KiCad or FreeCAD is installed. Resource templates and prompts are also proxied if a backend supplies them. Names and schemas are discovered dynamically, not reimplemented in a stale hand-maintained catalog.

### Architecture

~~~mermaid
flowchart LR
  Agent[MCP client] --> Unified[Unified CAD MCP: stdio]
  Unified --> Context[unified_get_context: project / units / startup status]
  Unified -->|kicad_*| K[Persistent KiCad subprocess]
  Unified -->|cad_*| C[Persistent design subprocess]
  K --> E[KiCad / pcbnew / kicad-cli: optional native install]
  C --> F[FreeCAD bundled Python: optional native install]
  K -. explicit export and coordinate / unit handoff .-> C
  C -. source hashes / UNKNOWN evidence .-> Context
~~~

[FastMCP 4.1 composition and proxies](https://gofastmcp.com/servers/composition) own protocol routing and content serialization. Backends run in **separate Python environments**: current KiCad uses FastMCP 4 / MCP 2, while design requires MCP 1 (mcp<2). Installing both into one Python environment would create a dependency conflict. Both backend connections use the interoperable MCP 2025-11-25 handshake.

## Quick start

Requires Python 3.11+, Git, and pip 23+ (pip must support --python). The tested lock snapshots were produced with Python 3.12 on Linux. Python's venv must be available; OS package installation is not automated here.

~~~sh
git clone https://github.com/SolutionsAsService/unified-CAD-MCP.git
cd unified-CAD-MCP
python3 -m venv .venv
.venv/bin/python -m pip install -c locks/unified-py312.txt -e '.[dev]'
# Choose EXISTING directories you intend the backends to work with.
.venv/bin/python scripts/bootstrap_backends.py \
  --project-root /absolute/path/to/project \
  --asset-root /absolute/path/to/project/assets
.venv/bin/unified-cad-mcp --config unified-cad.local.json --check-config
.venv/bin/unified-cad-mcp --config unified-cad.local.json
~~~

On Windows use .venv\Scripts\python.exe and .venv\Scripts\unified-cad-mcp.exe; the bootstrap chooses the correct backend interpreter paths. Windows execution is not claimed tested.

The bootstrap installs **exact Git commits** from [sources.lock.json](sources.lock.json), with [tested dependency constraints](locks/). It creates .backends/kicad and .backends/cad, installs no GUI/native CAD applications, never changes host MCP settings, and refuses to overwrite an existing config. Use --dry-run to inspect commands without writes, or --backend cad / --backend kicad for one domain. Re-running against existing managed venvs may update packages to the selected locks; inspect before doing so. Network access is needed during installation. Source pins and dependency versions are recorded; this is not a hash-locked supply-chain attestation.

**Licensing:** backend source is installed separately, not copied/relicensed into this repository. KiCad has an MIT license. No license was declared in the inspected design source; see [provenance](docs/provenance.md) before redistributing its code.

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
- Optional FreeCAD operations require DESIGN_MCP_FREECAD_PYTHON pointing to a compatible interpreter with FreeCAD and Part modules. KiCad has separate native setup requirements; consult its pinned source documentation.
- Opening untrusted native CAD documents invokes complex parsers. Process separation is not OS sandboxing.
- One long-lived process per backend preserves KiCad's **in-process** PCB write locks. Separate unified/standalone processes are not cross-process locked. Do not run multiple writers on a board. One trusted operator session per stdio server is intended; no public HTTP service or multi-tenant isolation is provided.
- Backend initialization has a finite deadline. One missing backend does not remove the other. unified_get_context reports startup failure; fix it then restart. No automatic reconnect or mutation replay is implemented. A timeout may leave a native subprocess operation running: inspect effects before retrying.
- timeout_seconds defaults to 120 (maximum 3600); init_timeout_seconds defaults to 20 (maximum 60). Long autorouting needs a deliberate larger backend and host request timeout.
- Tool parameters, schemas, annotations, content, images, structured results and error flags are relayed by FastMCP. Names gain a namespace. Backend descriptions/prose may still use local tool names; apply the namespace. Upstream server instructions are not silently promoted to unified instructions.
- FastMCP banner/update checks are disabled for child processes by default. This does not disable legitimate upstream network features or provide an air gap.

## PCB to enclosure workflow

1. Read unified_get_context, then kicad_get_usage_guidance for PCB operations.
2. Use kicad_* for board work. kicad_design means firmware-driven electrical intent, **not** generic 3D modeling.
3. Use the actual kicad_export schema to export a board model. Explicitly select an output path in the CAD asset root, or move it there yourself. No automatic copy/import occurs.
4. Inspect the STEP/FCStd with cad_* if FreeCAD is available. Record source hash, units, origin/axes, transforms and selected objects. STL units remain **UNKNOWN** until externally established; FreeCAD lengths are mm.
5. Preserve backend UNKNOWN fit results. AABB overlap is not a collision proof; user-attested records are not independent verification. Box-only authoring is not arbitrary enclosure/assembly design.
6. Recheck hashes and affected geometry after changes. There is no cross-backend transaction, live synchronization, manufacturing certification, or automatic engineering approval.

Resource example: design://project/assets becomes **design://cad/project/assets**. Consult resources/list for exact names.

## Development / evidence

~~~sh
.venv/bin/python -m pytest -q
# After bootstrap: actual pinned upstream server processes, no native CAD required
UNIFIED_CAD_BACKEND_CONFIG="$PWD/unified-cad.local.json" .venv/bin/python -m pytest -q -m backends
~~~

Default protocol tests launch **synthetic MCP peers**, including through the real unified stdio entrypoint. They check routing, schemas, metadata/content/errors, resources/templates/prompts, process reuse/cleanup, missing backends and deadlines. Opt-in tests exercise the pinned upstream servers. Neither substitutes for native PCB export or FreeCAD BREP/revision tests. See [verification evidence](docs/verification.md). CI runs protocol/config tests and pinned backend integration, not GUI/hardware tests or publication.
