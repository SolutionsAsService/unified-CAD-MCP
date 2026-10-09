# Source-derived capability coverage

Audit date: 2026-10-09. Sources are the exact three commits in sources.lock.json.
All three source trees were fetched and their actual registrations inspected;
README lists were not used as the inventory. The packaged coverage.json includes
complete live tool schemas/descriptions/annotations and source hashes/locations.
Read it through unified://capabilities/inventory; use tools/list for what is
currently enabled and initialized. A pinned inventory is not a health probe.

## Inventory and semantic deduplication

- SolutionsAsService/kicad-mcp: 18 registered tools, 117 string selector values
  (operation/query/verbosity), plus 67 native subprocess boundary functions.
- SolutionsAsService/design-mcp-server: all 33 tools, six resources and all six
  FreeCAD worker commands. No prompts or templates registered.
- NiRuLabs/kicad-mcp-server: all 35 Tool definitions and matching 35 dispatch
  branches. Nineteen are on by default; sixteen schematic tools are opt-in.
  All 54 public IPC client methods are accounted for, including properties and
  lifecycle methods; 44 are reachable from tool dispatch and ten are not.
- All three upstream servers currently register zero prompts and zero templates.
  Generic proxy support is retained and exercised with synthetic peers.
- Unified: 71 tools normally, 87 with schematic opt-in, eight resources.
  No redundant per-operation wrappers inflate the advertised catalog.

Two exact semantic aliases exist inside the existing KiCad routers:

1. kicad_pcb(operation=get_constraints) aliases
   kicad_audit(operation=constraints), both calling _op_constraints(pcb_path).
2. kicad_pcb(operation=check_silkscreen_overlaps) aliases the same operation in
   kicad_audit, both calling _op_check_silkscreen_overlaps(pcb_path).

The canonical inventory points to audit and explains the shared implementation.
Both old operation forms remain callable for compatibility; no extra tool is
advertised for an alias. Thus 117 selector entries represent 115 canonical
selector capabilities. Shared implementation is not a reason to discard entire
routers that also contain unique actions.

No cross-backend tool was removed based on a similar name. Saved-file pcbnew
editing, live GUI IPC editing, and CAD provenance work have different state,
locking, units and side effects. For example place_track is not a substitute for
pcb(add_trace), and live symbol libraries are not the saved-file library index.
The policy explicitly retains those alternatives.

## What the native coverage means

The 67 KiCad boundary rows identify the actual owning function, callsite and
subprocess arguments (including CLI subcommands where literal). They are not 67
extra callable tools. The native runtime dependencies are not installed by this
project. Parameterized generated scripts are not falsely described as a finite
native command protocol. Consult the pinned function identified in each row.

NiRuLabs uses generated protobuf/NNG IPC. Public methods that are reachable from
tools map transitively to the canonical tools rather than being advertised twice.
The ten not reachable from MCP are is_connected, disconnect, get_active_layer,
set_active_layer, delete_items, fix_route_segment, undo_route_segment,
toggle_route_via, switch_route_layer and get_route_status. The property/lifecycle
entries are not engineering capabilities. The remaining unregistered methods
require a separately reviewed interface and compatible native implementation;
this integration does not invent their schemas or claim stock support.

Importantly, start_route, set_route_end, finish_route and abort_route ARE reachable
through route_pads. Its bridge uses StartRoute/SetRouteEnd/FinishRoute extensions,
not merely the A* find_path algorithm. Their presence in bundled protobuf code
is not proof a running KiCad supports them. Upstream route_between_pads commits
even when preview reports violations. This is preserved and prominently warned
about, not silently converted into a manufacturing-safety guarantee.

The separate find_path/suggest_route tools calculate upstream approximate paths;
place_track/place_via issue create-item operations. Native runtime testing is
required for geometry, clearance, routing and undo behavior. Schematic tools are
explicitly marked experimental upstream and remain gated.

## Reproduction

Run from the checkout after installing the pinned environments:

~~~sh
.venv/bin/python scripts/fetch_audit_sources.py
.venv/bin/python scripts/audit_sources.py --config unified-cad.local.json --check
UNIFIED_CAD_BACKEND_CONFIG="$PWD/unified-cad.local.json" .venv/bin/python -m pytest -q
~~~

The fetch script only retrieves the three selected pins, never runs source setup
scripts, and refuses to change a dirty or differently pinned existing audit tree.
The audit discovers actual wire tools with schematic opt-in, parses registrations,
dispatch branches, guarded selectors (including constant-backed fallback verbs),
resources and native boundaries, applies the reviewed semantic policy, and fails
on drift. CI runs the same source regeneration comparison. It does not index or
upload design projects. To update pins, explicitly regenerate and review the
inventory and lock constraints; do not bypass the drift check.

## State and result boundaries

Each backend has its own long-lived subprocess and environment. No live board,
unsaved GUI state, file lock, commit token, undo history, transaction or working
units are shared. Do not write the same board concurrently through two backends.
A session restart invalidates state and is never automatic after a mutation.

Proxy schemas, annotations, images, structured data, resource/prompt payloads
and error flags are preserved. Upstream descriptions are unchanged, so unified
instructions and this inventory supply necessary native caveats. NiRuLabs often
returns application errors inside JSON text without setting isError; the proxy
preserves this, and real-source tests assert that convention. Inspect both.

License and installation boundaries remain in provenance.md. In particular no
unlicensed design implementation is embedded or relicensed. No native GUI,
plugin, protobuf rebuild, paid service or supplier database is automatically
installed or invoked.

The inventory additionally expands all 54 NiRuLabs schema enum choices into
source-pinned schema_choice rows, including commit/drop and annotation-order
values; these are option coverage, not 54 additional engineering tools.
