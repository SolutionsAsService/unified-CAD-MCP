# NiRuLink FreeCAD integration

## Architecture and provenance

Audited default main on 2026-10-09: NiRuLabs/NiRuLinkFreeCAD commit
`b60aad723d4607dd1789ef3b1b287571d237afe8`.
Upstream is BOTH a standalone stdlib MCP 2024-11-05 stdio server and a Qt TCP
addon inside the FreeCAD GUI. It is not a pip package. The existing maintained
FastMCP composition mounts an isolated upstream process as `freecad_*`.
The local adapter imports the separately fetched, digest-verified server files;
no source vendoring or protocol reimplementation in the unified server.
MIT, Copyright (c) 2026 Arnold Vosloo / NiRuLabs; original LICENSE stays in checkout.
Existing three source pins/licenses are unchanged, including design's absent license.

Upstream manual says FreeCAD 0.21+ and Python 3.9+. However current package.xml
sets FreeCAD minimum 1.0; server bridge uses evaluated builtin generic annotations
and is stdlib-only. Use **FreeCAD GUI 1.0+** with its bundled Python and PySide2 or
PySide6, and **Python 3.11+** for this project (3.12 verified). No Qt/FreeCAD packages
are installed into the MCP interpreter. GUI availability is separate from MCP
initialization. No native FreeCAD binary/module was available in the WSL test host.

## Install explicitly

1. Install FreeCAD GUI yourself through your trusted platform distribution. No
   upstream installer/setup script, system package manager, client-registration
   command, or global GUI installation is run by this project.
2. Bootstrap the stdlib-only isolated server and exact source checkout:

   ```bash
   .venv/bin/python scripts/bootstrap_backends.py --backend freecad \
     --project-root /absolute/project --asset-root /absolute/project/assets \
     --output unified-cad.freecad.local.json
   ```

   Default `--backend all` also installs this runtime but, since 2026-10-12, writes the
   backend as `"enabled": false` unless `--enable-freecad` is passed (`--backend freecad`
   enables it), so unrestricted execute_python is never exposed by accident.
   Existing output files are never overwritten. For an existing setup, merge only the generated `freecad`
   backend entry into the trusted config you launch; keep other backends unchanged.
3. With FreeCAD closed, place a copy of the **whole pinned checkout**
   `.backends/freecad/source` (excluding .git) in the FreeCAD user addon directory
   as `Mod/NiRuLinkFreeCAD`. Preserve LICENSE, package.xml, resources, and
   `nirulink_setup.py` as well as the four main addon Python files; the older
   README's four-file shortcut omits current toolbar dependencies. Find the actual
   directory via `App.getUserAppDataDir()` in FreeCAD's Python console, then use
   its `Mod` subdirectory (platform installs vary). Do not overwrite another addon.
4. Restart FreeCAD; report view must show localhost:9876 listening. Do **not** click
   Configure MCP clients: that upstream action edits external client settings and
   is unnecessary with unified MCP. Enabling the addon starts its listener at GUI
   startup, even without activating its workbench. It has **no authentication**.
5. Enable the optional backend (example and default bootstrap configs deliberately have `enabled:false`),
   launch unified MCP with your config, then call `freecad_connect_to_freecad`.
   It checks `FreeCAD.Version()`. Missing GUI/listener produces an actual error.
   A stdio handshake alone is not proof of native support.

MCP and FreeCAD must share the loopback network namespace. Windows GUI plus a
WSL server is not automatically supported: run the adapter Python on the GUI host
with locally valid paths rather than exposing the listener publicly. No WSL/Windows
port proxy, firewall change, tokenless remote connection, or tunnel is configured.

## Callable surface and semantics

Two tools by default: `freecad_connect_to_freecad` and
`freecad_execute_python`. Upstream calls execute_python its commissioned path;
this project has NOT native-verified it. Fourteen additional helpers are available
only with `UNIFIED_FREECAD_EXPERIMENTAL=1` (or bootstrap flag
`--enable-freecad-experimental`): new_document, create_body, create_sketch,
add_sketch_rectangle, add_sketch_circle, close_sketch, pad, pocket, fillet,
chamfer, export_stl, export_step, get_objects, get_shape_info. These are upstream's
registered operations, not inferred APIs. **16 total**, not 16 plus an invented
native-command catalog. No upstream resources, templates, prompts or image returns.

With all four configured: **73 default tools**, **103 with both experimental
sets enabled**, **8 resources**, **0 prompts/templates**. Disabled backend adds no
tools. Existing kicad_/pcb_/cad_ names and defaults remain compatible. Inventory
contains all 16 schemas and dispatches, plane enum choices, and internal listener/
client functions explicitly classified as implementation, never extra tools.

FreeCAD GUI objects/documents are not the cad_ asset catalog or worker documents.
get_shape_info overlaps the subject of cad_inspect_freecad_model but operates on
unsaved active GUI state, unlike file inspection with provenance. new_document/
pad/etc. are not cad_create_box_revision's revision-root workflow. Thus no truly
interchangeable duplicate is advertised or old API removed. Length mm, area mm²,
volume mm³; active document/body/sketch references are upstream-local. There is
no automatic coordinate transform, document selection/ownership, recompute success
certification, export/import synchronization, transaction or rollback.

For safe commissioning, manually choose a disposable GUI document. Explicitly
inspect `App.listDocuments()` and active document before mutation; do not let an
assistant assume it owns your current document. A one-off new temporary document
can be created and closed by explicit name with execute_python; nothing is created
or closed automatically during discovery. Expressions return repr strings;
statements return captured stdout and generally None. Use explicit print for
multi-statement inspection. No eval of returned repr occurs in the adapter.

## Known upstream helper limitations (not silently repaired)

* Listener eval falls back to exec; final expression of multiline statements is
  not returned. get_objects/get_shape_info may therefore report None.
* Several helpers claim success even with absent objects or incomplete geometry.
  Adapter turns actual `ok:false` into MCP isError, but cannot certify valid CAD
  when upstream returns `ok:true`. Inspect geometry after any provisional call.
* create_sketch interpolates omitted body as "None"; rectangle constraints assume
  geometry indices 0–3; pad uses Symmetric; fillet/chamfer read body Tip after adding
  a feature; STL export feeds tessellation vertices to addFacets. Native behavior
  is unverified and these are concrete reasons to prefer execute_python.
* No screenshots/images are registered; execute_python cannot manufacture MCP
  ImageContent. Text output remains text. Existing other backends' images are
  unchanged by the proxy.

## Security and adapter boundaries

The addon executes eval/exec with full builtins, imports and current-user filesystem,
process and network permissions on the GUI thread. There is no per-call native
confirmation, auth token, cancellation or sandbox. We do not suppress native UI
confirmations, add public listeners, or claim a private multi-user security boundary.
Loopback still permits other local processes/users. Only use on a trusted machine;
disable/remove the addon or stop its server in the GUI when not needed.

Adapter only permits literal localhost/127.0.0.1 (connects numerically to avoid DNS),
a valid operator-selected `UNIFIED_FREECAD_PORT` (9876 default), and bounded
`UNIFIED_FREECAD_TIMEOUT` (>0, <=60s, default 30). If selecting another port,
configure the addon explicitly too and pass the same port to connect. The unified
outer timeout should exceed this bound (default 120s). Frames <=16 MiB, exact-read
fragment handling and a total response deadline prevent unbounded buffering/waits.
Timeout/disconnect invalidates the socket and reports outcome UNKNOWN. No automatic
reconnect/retry occurs; a GUI-side mutation may continue after the timeout. Inspect
its effects before reconnecting; never replay blindly.

Schemas are faithfully retained; runtime rejects unknown/mistyped/nonfinite args,
nonlocal endpoints, unsafe helper interpolation strings and nonabsolute export
paths. Use forward slashes for Windows helper paths. Export paths refer to the GUI
host and can overwrite user files. These guards do not constrain explicit Python:
execute_python is deliberately full-access. Project root is not a sandbox.

Upstream logs the first 500 characters of every request: adapter disables that
logger to avoid copying CAD code/project contents into stderr. No project upload,
telemetry, indexing or external client registration is introduced. Python code
chosen by a caller can itself access network/files; that is not prevented or hidden.
