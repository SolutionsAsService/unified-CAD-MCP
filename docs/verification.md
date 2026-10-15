# Verification evidence

Executed 2026-10-09 on Linux/WSL, Python 3.12.3. This report distinguishes protocol/logic regression checks from native engineering integration.

## Passed locally

- Core editable package installed and imports successfully with FastMCP 4.1.0 / MCP 2.3.0.
- Pinned KiCad source installed in its own environment; pinned design source installed with MCP 1.30.0 in a different environment. Installation repeated using all three recorded dependency constraints. Existing generated configuration was preserved; the repeat used a separate ignored output file.
- CLI config check returned success without starting native CAD applications.
- Full acceptance command:

~~~sh
UNIFIED_CAD_BACKEND_CONFIG="$PWD/unified-cad.local.json" .venv/bin/python -m pytest -q
~~~

**27 tests passed in 50.97 seconds** in the bounded acceptance review (180-second command timeout; exit 0). Includes config validation and bootstrap no-overwrite/dry-run checks; actual subprocess lifecycle and end-to-end stdio tests using synthetic protocol peers; and one explicit real-source integration test.

Synthetic peer tests pin behavior for identical local tool names in different namespaces, integer input schema, read-only annotation/custom metadata, structured/image/text/error-result preservation, resource/template/prompt namespace round trips, persistent child PID and working directory, invalid arguments, absent/disabled backends, call timeout, bounded initialization timeout, dead-backend isolation without restart, and process cleanup. Additional acceptance coverage asserts exact output-schema and image MIME preservation, explicit backend environment forwarding without leaking arbitrary ambient credentials, config-relative explicit cwd, and ignored local secrets/index/state paths. The timeout regression now verifies discovery succeeded and a side effect occurred exactly once before timeout, followed by a successful call to the same backend. The previous 0.2-second setting could fail during startup and falsely pass without invoking the operation; it now uses a 2-second request deadline and a 10-second operation. These test the integration, not CAD calculations.

## Real source servers, real unified stdio entrypoint

The opt-in test starts the installed unified CLI as an MCP subprocess, which starts the two pinned source servers as its children. It exercises MCP handshake, tools/list, tools/call, resources/list and resources/read through the single unified connection:

- **52 unique tools:** 18 kicad tools, 33 cad tools, one unified context tool.
- **7 resources:** the six original design resources namespaced under design://cad/... plus unified://project/context.
- cad_convert_quantity: 25.4 mm → **0.0254 m**, actual upstream unit conversion.
- kicad_get_usage_guidance with operation=tactics: actual nonempty upstream guidance.
- cad_get_scope: reports the source's read-only/opt-in authoring boundary.
- cad_inspect_asset: hashes an actual temporary file; an attempted parent-path escape returns an error.
- design://cad/project/revisions reports **CONFIGURATION_REQUIRED** with no revision root enabled.

This proves source-server composition and these read-only operations, not every operation behind either catalog.

## Not performed / not claimed

- Native KiCad GUI/pcbnew/kicad-cli PCB generation, DRC, routing or STEP export: native applications were absent.
- FreeCAD BREP inspection, shape distance, box creation or previews: a FreeCAD runtime was not installed/configured.
- Cross-backend geometric alignment, manufacturing/fit validation, hardware tests, or every upstream unit test: outside this integration's claimed checks.
- Windows execution, Mermaid rendering, or public HTTP/multi-tenant service testing.
- Hosted CI success, deployment, Gateway MCP registration or installation: not performed by the integration build. A CI workflow is provided; local tests are not a remote CI receipt.

The server only reports handshake availability, not native application health. Source tool names, source versions and constraints are recorded in sources.lock.json and locks/. Full verification requires repeating a representative PCB export → explicit CAD asset handoff → FreeCAD inspection with the native tools on the intended host.

## Publication

On 2026-10-09, GitHub CLI device authorization completed successfully. Both `gh auth status` and the authenticated `user` API verified SolutionsAsService (account ID 152567394); the repository API confirmed push permission on SolutionsAsService/unified-CAD-MCP. Publication uses that account's official noreply identity in repository-local Git configuration, preserves the initial commit and upstream credit, and targets main without force. Only integration source, documentation, scripts, tests, lock snapshots, example configuration and CI are included. Generated .venv, .backends, .cache, dist and local configuration remain ignored. The repository's commit history and Actions runs provide remote publication and CI receipts; the local test results above are not themselves a hosted CI receipt.

## Bounded acceptance review

Reviewed source/configuration, bootstrap and version isolation, CI, protocol/regression coverage, and documentation. No server implementation defect required a change. Expanded `.gitignore` to cover environment-file variants, local credential directories, alternate local operator configs, and runtime index/state directories. Clarified that the MCP SDK inherits only a minimal environment; custom backend variables must be explicitly configured, values are literal, and only project/cwd paths are config-relative. The workflow has read-only repository permissions and no publish/deploy step. Source composition remains separately installed and no design-source license was invented.

`git diff --check` passed after review edits; untracked task-owned text files were also checked for trailing whitespace/conflict markers. The earlier authentication blocker was resolved by successful device authorization and authenticated account/repository permission checks.

## Three-backend extension acceptance (2026-10-09)

The original two-backend evidence above is historical. All original 27 tests
were retained. Added exhaustive source/wire inventory, semantic alias and
no-name-only-dedup tests; PCB absent-runtime isolation; both schematic gate
modes; actual disconnected NiRuLabs JSON error preservation; actual missing
FreeCAD configuration; token/socket separation; and bounded NNG send regression.
Token/deadline boundary tests are mocked unit tests, not native connection proof.

The initial full extended suite passed 35 tests in 61.66 seconds. Subsequent
focused acceptance adds two tests and rechecks the changed adapter/inventory.
Live tests initialize all three exact upstream sources and compare every one of
86 upstream tool schemas, descriptions, output schemas and annotations through
the real unified stdio entrypoint (87 tools including context). Default PCB mode
separately verifies 19 native-dependent tools plus context; experimental mode
adds all sixteen gated tools. Actual CAD conversion, scope, file hashing/path
rejection and KiCad usage guidance continue to run without native mocks.

A real missing-socket test exposed an upstream unbounded NNG send. The launch
adapter now rejects absent explicit Unix sockets before connecting and adds a
30-second NNG send deadline alongside upstream's receive deadline, without
retrying mutations. Upstream native error/result formats remain unchanged.

On this host, command discovery found no kicad-cli, pcbnew GUI, FreeCAD,
FreeCADCmd, freecad, java or kikit. System Python could import neither pcbnew,
FreeCAD nor Part. Therefore no native board mutation, DRC, autorouting, FreeCAD
BREP, or enclosure fit test is claimed. Discovery and disconnected calls are
real MCP tests, not fabricated native success. Native GUI/IPC and Windows tests
remain blocked on operator-supplied compatible runtimes.

CLI configuration validation, wheel build, installed PCB pip check, source
regeneration/drift comparison and whitespace checks are part of acceptance.
CI has read-only contents permission and only installs pinned source packages,
fetches the three audit trees, checks coverage, builds a local wheel and tests;
there is no deploy, release, project upload or external telemetry action.

Final focused acceptance: 11 tests passed in 20.19 seconds, including the two
additional regressions and real native-precondition failure paths. Combined with
the unchanged baseline coverage this provides 37 tests. The regenerated inventory
also records all 54 NiRuLabs schema-enumerated options (layers, labels, commit
actions and annotation ordering); these are not counted as extra MCP tools.

## Four-backend NiRuLink acceptance (2026-10-09)

Earlier sections are historical. Full four-backend suite: **58 passed in 65.02s**,
including all prior 37 tests. Source audit: 18/33/35/16 tools; FreeCAD 54 source
evidence entries. Actual upstream stdio initialize/list/call and all 102 upstream
schemas through unified stdio were checked (103 including context), plus default
gates (73 total) and unchanged eight resources. New FreeCAD backend has no
resources/templates/prompts/images. Real absent-listener/disconnected errors were
checked without touching any real GUI. The raw upstream dispatches were exercised
against a rejecting fake native boundary so no operation can hide ok:false errors.
Fragmented TCP, framing bounds, total deadline, disconnect/no-replay, source digest,
local-only endpoint, schema/path checks and isolation are mocked/unit evidence,
not native geometry proof. Successful connection protocol used explicitly fake
version data; no native FreeCAD installation, GUI shape creation or export is claimed.

Bootstrap installed an isolated stdlib FreeCAD server from its exact Git commit;
no installer, native GUI, host config or external registration ran. Existing local
config preserved; full acceptance uses ignored unified-cad.all.local.json.
CI remains read-only tests/build only and bootstraps all four backends.

Final focused run: all **23 FreeCAD tests passed in 6.08s**, including two
additional port/type/gate/privacy regressions (60 tests total with prior suite).
Wheel build passed and packaged the adapter/inventory. Source drift comparison,
CLI config validation and git diff whitespace check are required before commit.

## Install, audit and drone slice (2026-10-12)

Earlier sections are historical. Fresh install on Linux/WSL2, Python 3.12.3, no sudo
and no ensurepip: python3 -m venv --without-pip .venv, pip via get-pip.py, then the
README quick start. Full report and findings: [audit-2026-10-12.md](audit-2026-10-12.md).

- Real unified stdio handshake (MCP 2025-11-25) and tools/list against the bootstrapped
  config: **73 tools default** (unified 1, kicad 18, cad 33, pcb 19, freecad 2) and
  **103 with both experimental gates** (pcb 35, freecad 16); **8 resources**.
- With the operator config (freecad disabled, drone_tools true): **74 tools**
  (unified 1, kicad 18, cad 33, pcb 19, drone 3).
- Full suite with UNIFIED_CAD_BACKEND_CONFIG and fetched audit sources:
  **72 passed, 0 skipped in 75.9 s** (includes tests/test_drone.py and the example-config
  schema test). Source drift check: no drift.
- Before the timeout-test fix, the default run had 1 reproducible failure
  (test_backend_timeout_returns_error_without_replay; the 2 s request timeout also
  bounded initialize on this slow host).

Native FreeCAD (first native evidence for cad_*): FreeCAD 26.3 from conda-forge via
micromamba in a user prefix (no sudo). Through the real unified stdio entrypoint:
cad_inspect_freecad_model on a STEP (valid closed solid, 7 faces, mm units),
cad_get_freecad_entities (faces page), cad_create_box_revision (FCStd saved and
reopened, REOPENED_GEOMETRY_ONLY) and cad_preview_cad_revision (isometric SVG) all
succeeded. This required two host-side steps that are not part of this repository:

1. a .pth file in the conda env's site-packages containing the env's lib/ path so
   import FreeCAD works under python -I;
2. a DESIGN_MCP_FREECAD_PYTHON shim (an executable script named python) that runs
   the real interpreter with -c 'import FreeCAD, Part, runpy, sys; sys.argv = sys.argv[1:];
   runpy.run_path(sys.argv[0], run_name="__main__")', because import FreeCAD in this
   build deletes json/math from __main__ and the pinned upstream worker imports them
   first (NameError otherwise). Upstream fix requested; see audit finding 3.

Still not performed: any native KiCad operation (kicad-cli/pcbnew not installable here
without sudo; not on conda-forge), live KiCad IPC, FreeCAD GUI / NiRuLink addon, Windows.
drone_* outputs are heuristic estimates verified only against independent formulae.

## Miniature check throughput and memory (field notes, 2026-10-14)

- miniature_printability_check / gap / shell tools take ~2 min per part at ~160k triangles and ~5 min at ~245k triangles. Budget triangles before calling them (~160k for figures and bits, ~250k for bases).
- Cache results on the caller side keyed by tool + args + STL sha256, so a killed batch resumes where it stopped instead of starting over.
- On an 8 GB host, run at most one heavy check per build at a time, under a memory cap (systemd-run --user --scope -p MemoryMax=1500M). Keep two builds or fewer in parallel, because the agent transcripts live in the gateway's memory.
