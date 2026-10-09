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
