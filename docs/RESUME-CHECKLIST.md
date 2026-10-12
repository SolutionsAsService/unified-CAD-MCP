# Resume checklist (successor agents start here)

Task (2026-10-12, subagent "CAD MCP fixes + Widewing V0.4 propeller"). Update this file in **every** commit.
Commit as SolutionsAsService <152567394+SolutionsAsService@users.noreply.github.com>; push small increments to main
after `.venv/bin/python -m pytest -q -m 'not backends'` passes (CI: `~/.local/bin/gh run list -L 3`).

Environments (no sudo): FreeCAD `~/opt/freecad-env/bin/python`, AeroSandbox `~/opt/aero-venv/bin/python`,
micromamba `~/opt/bin/micromamba` (MAMBA_ROOT_PREFIX=~/opt/mamba). New envs being created by /tmp/inst.sh:
`~/opt/fea-env` (calculix+gmsh), `~/opt/vsp-env` (conda-forge openvsp), `~/opt/cfd-env` (conda-forge su2).

## A. Server fixes
- [x] F10 structured_content for proxied cad_* tools (middleware src/unified_cad_mcp/structured.py) + tests/test_structured.py
- [x] F11 STL units parameter/sidecar + shell/cavity report (stl_report.py) + worker writes <stl>.meta.json + tests
- [x] F15 KiCad detection at startup (kicad_native.py) + install hint
- [ ] F15 verify kicad_*/pcb_* natively once `which kicad-cli` works (was missing at 17:40): start server with
      unified-cad.openclaw.local.json, check unified_get_context native_kicad=available, then run kicad_drc / kicad_export
      (gerbers) on a fixture board and `.venv/bin/python -m pytest -m backends`.

## B. New capabilities (worker + MCP tools + tests + docs)
- [ ] OpenVSP / VSPAERO (vsp-env)
- [ ] FEA: gmsh + CalculiX spar/joiner/boom load cases (fea-env)
- [ ] CFD: SU2 coarse external flow (cfd-env), honest caveats
- [ ] docs/drone-testing-flaws.md F10/F11/F15 status; CI jobs where feasible

## C. Widewing V0.4 (military-display/assets/downloads/widewing-vtail-v04)
- [ ] derive driver from V0.3 build_via_mcp.py; real propeller (catalogued, cited) + realism details
- [ ] full MCP analysis, prop thrust/efficiency estimate, renders checked with view_image
- [ ] register in catalog + compare vs V0.3; npm test passes

## Log
- 17:2x started; no prior uncommitted work found in either repo.
- 17:45 A (F10/F11/F15 detection) committed; 83 non-backend tests pass. Envs: fea-env (ccx, gmsh+python-gmsh, meshio) OK,
  cfd-env (SU2 8.x + python-gmsh) OK, OpenVSP NOT on conda-forge/PyPI -> official Ubuntu 24.04 .deb 3.55.0 extracted with
  dpkg-deb -x into ~/opt/openvsp/opt/OpenVSP (needs python 3.12 + libgomp + cminpack from ~/opt/vsp-env, LD_LIBRARY_PATH).
- Exec quirk: setsid/nohup jobs get killed when the exec cell ends; use exec(background:true) WITHOUT '&'.
