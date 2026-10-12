# Drone testing with unified-CAD-MCP: flaws, fixes and roadmap (2026-10-12)

Context: the Widewing V-tail V0.3 fixed-wing concept (SolutionsAsService/military-display,
assets/downloads/widewing-vtail-v03) was designed **through this server over stdio**
(source/build_via_mcp.py logs every call to reports/mcp-call-log.json). This document
records what blocked or misled that work, what was fixed in this change, and what remains.
Severity is judged by impact on *testing a drone*, not on generic CAD use.

## Flaws found (with repro) and status

| # | Flaw | Severity | Repro / evidence | Status |
|---|---|---|---|---|
| F1 | **OCCT GProp mass properties are wrong on BSpline solids** in the installed FreeCAD 26.3 (conda-forge, OCCT nightly): Shape.Volume/CenterOfMass/MatrixOfInertia under-read lofted wings by 7-20 %; booleans that cut pockets appeared to *add* volume. | Critical (silently wrong mass/CG) | Extrude a NACA 0012 BSpline face (area 7374 mm²) by 500 mm: Shape.Volume = 3.218e6 mm³, tessellated volume = 3.695e6 (expected 0.68505·t·c²·b = 3.699e6). Polygonal sections are exact. Same error on the exported V0.3 wing STEP re-read by **cad_inspect_freecad_model** (enclosed volume -8.3 %). | **Fixed for airframe_***: mass properties are exact polyhedral integrals over a 0.05 mm closed tessellation; GProp is reported next to it (gprop_volume_error_fraction). **Remains** in the upstream cad_inspect_freecad_model (pinned design-mcp-server): its volume/mass on curved solids should not be trusted; report upstream. |
| F2 | Bounding boxes are loose (OCCT BoundBox includes BSpline poles): the V0.3 port wing reported x 179.8-1216.6 mm instead of 544.7-881.6 mm. | High (fit checks/envelopes wrong) | cad_inspect_freecad_model on step/main-wing-port.step | **Fixed for airframe_*** (optimalBoundingBox). Remains upstream. |
| F3 | No fixed-wing capability at all: the 3 drone tools were multirotor-only (hover thrust) plus a point-mass budget; no aero, stability, tail sizing, endurance or structure. | Blocker | tool list at 8fdd850 | **Fixed**: drone_fixed_wing_analysis (planform/MAC, Purser-Campbell V-tail volumes, neutral point incl. Munk fuselage term, static margin and CG band, Raymer drag build-up, polar, stall/min-power/min-drag speeds, electric endurance/range, climb, static T:W, spar root stress, pass/fail checks, sources). |
| F4 | No way to *generate* an airframe: CAD tools could only make boxes (cad_create_box_revision) or inspect existing files. | Blocker | — | **Fixed**: airframe_build_fixed_wing (headless FreeCAD, NACA lofts, superellipse fuselage, hinge-line control-surface split, spar tubes, equipment envelopes, foam pockets, STEP/STL/FCStd/viewer JSON). |
| F5 | Mass budget not linked to CAD; drone_mass_cg_budget could not take CAD inertia and only had a multirotor horizontal-offset CG check. | High | — | **Fixed**: CAD mass/CG/inertia per part from the build; inertia_about_cg_kg_m2 and cg_x_range_mm added to drone_mass_cg_budget; build compares budget vs CAD with tolerances. |
| F6 | No deterministic geometry validation (closed solids, containment, clearance, interference, symmetry). | High | — | **Fixed** in airframe_build_fixed_wing checks + airframe_inspect_step. |
| F7 | No visual verification path (cad_preview_revision is a box SVG; KiCad renders need KiCad). | Medium | — | **Fixed**: airframe_render_preview (matplotlib Agg, orthographic, CG/NP markers, exploded views). First renders had near-white parts on a white background (aft fuselage looked missing) — fixed with a tinted background, clamped shading and edge lines. Occlusion is painter's-algorithm approximate. |
| F8 | No independent aero check. | High | — | **Fixed (wrapped OSS)**: airframe_aero_crosscheck runs AeroSandbox 4.2.10 (MIT) VLM + AeroBuildup/NeuralFoil in its own venv. Finding: AeroBuildup's neutral point was ~47 mm aft of VLM/closed form for V0.3 (it appears to under-represent downwash at the tail); VLM agrees with the closed form within ~6 mm. |
| F9 | unified_get_context hid backend start-up errors (only "RuntimeError"); KiCad failed to start once under load (20 s init timeout) and the tool count silently dropped from 79 to 56. | Medium | first stdio session of the day | **Fixed**: context now includes the error text and init_timeout_seconds. The timeout itself is operator-configurable; not changed. |
| F10 | Proxied cad_* tools return JSON only as text content (structured_content is None), so clients must parse text. | Medium | cad_inspect_freecad_model via fastmcp Client | **Fixed** (unified middleware, src/unified_cad_mcp/structured.py): cad_* tools without an upstream outputSchema get an object schema and structured_content parsed from the text (non-object JSON wrapped as {"result"}, non-JSON as {"text"}); text content preserved; upstream-declared schemas untouched. tests/test_structured.py. Upstream still emits text only. |
| F11 | cad_inspect_stl_geometry reports units "UNKNOWN" for every STL (format has no units), and counts internal cavities as extra shells without saying so. | Low | V0.3 wing STL (servo cavity → 2 shells) | **Fixed**: cad_inspect_stl_geometry gains units= (mm/cm/m/um/in/ft); otherwise a <file>.stl.meta.json sidecar (now written by airframe_build_fixed_wing for every STL) is used, otherwise mm is ASSUMED and flagged (units.assumed, units.source, warning). metrics_mm converts bbox/area/volume/centroid. shell_analysis classifies each edge-connected shell (outer_shell / internal_cavity / nested body / inverted normals, ray-parity nesting) and reports internal_cavity_count and net signed volume. Upstream coordinate_units is kept verbatim. Design fix kept: servo pockets open through the skin. |
| F12 | BOPCheck flags GeomAbs_C0 edges on lofted/cut parts. | Low | check(True) on wing/tail | Reported as bop_advisories (not failures); smooth before CAM. |
| F13 | Concurrent builds into the same directory race (one run deleted the other's output). | Medium | two workers writing /tmp/airframe-mini | **Fixed**: per-output lock, marker-file guard, replace=true required. |
| F14 | Error leakage: a traversal probe on airframe_inspect_step surfaced a raw FileNotFoundError with the resolved path. | Low | path "../../etc/passwd" | **Fixed** (ToolError, same message for missing/outside). |
| F15 | KiCad not installed (needs sudo) → kicad_*/pcb_* native operations unverified; no avionics PCB/harness work possible. | Medium for electronics | backends table | **Detection fixed; native verification pending operator install**: startup probe (src/unified_cad_mcp/kicad_native.py) reports native_kicad {status available/partial/missing, kicad_cli + version, pcbnew python + version} in unified_get_context with the exact install hint and a startup warning; tested with a fake kicad-cli. The user is installing KiCad (sudo, operator action). Once 'which kicad-cli' succeeds, run the kicad_*/pcb_* verification in docs/RESUME-CHECKLIST.md. |
| F16 | Upstream FreeCAD import clobbers json/math in the worker (audit finding). | Medium | audit-2026-10-12.md | Worked around in airframe workers (FreeCAD imported first); upstream unchanged. |
| F17 | No regression test that builds a drone end to end. | High | — | **Fixed**: tests/test_airframe.py reference glider (analytic NACA volume within 0.5 %, pocket volume, totals = Σparts, STEP round trip, previews, replace guard, optional AeroSandbox trim) + CI job "airframe" (micromamba FreeCAD + AeroSandbox venv). |
| F18 | **Coarse BRepMesh triangulations silently drop faces**: the V0.3 fuselage STL (0.2 mm) and viewer mesh (0.6 mm) kept the vertices of the mid-fuselage faces but had no triangles there (x ≈ 400-1100 mm), so the published mesh was not watertight and the fuselage looked truncated in previews. Found only by *looking at the render*. | High (published geometry wrong) | triangle-centroid histogram of fuselage-shell.stl: 2 triangles between x = 500 and 1100 mm | **Fixed**: every STL/viewer mesh is re-triangulated from a cleaned copy and accepted only if watertight and within 1 % of the fine mass-mesh area (retry with halved deflection); one check per part per format in the build report. |
| F19 | Shape.optimalBoundingBox(useTriangulation=True) reuses that cached broken triangulation: fuselage dimensions fed to the aero analysis came out 1967 × 372 × 356 mm instead of 1720 × 125 × 109 mm, corrupting fineness ratio, form factor and lift-curve slope in one run. | High | build report aero_inputs_from_cad, run 3 | **Fixed**: geometry-based optimalBoundingBox(False, False); verify_v03.py checks the viewer span against the spec. |
| F20 | A worker that crashed mid-build left an output directory without its ownership marker, so replace=true then refused to rebuild. | Low | airframe_build_fixed_wing after a KeyError in the worker | **Fixed**: the server deletes/creates the directory and writes the marker before spawning the worker; a missing interpreter now gives a clear error. |

## What was added (summary)

- drone_fixed_wing_analysis — src/unified_cad_mcp/fixed_wing.py (pure Python, tested against textbook identities: trapezoid MAC, Helmbold limits, Lamb apparent-mass table, power/endurance/thrust recomputation).
- airframe_build_fixed_wing / airframe_inspect_step / airframe_render_preview / airframe_aero_crosscheck — src/unified_cad_mcp/airframe.py + workers/. Enabled by an optional "airframe" config section (freecad_python, aerosandbox_python, output_root, read_roots, timeout_seconds). Writes only below output_root; no shell, no network; minimal worker environment.
- drone_mass_cg_budget: inertia tensors and longitudinal CG band.
- unified_get_context: backend error text.

## Open-source decision record

| Need | Options checked | Decision |
|---|---|---|
| Parametric B-rep CAD | FreeCAD (installed), CadQuery, build123d | **FreeCAD** headless worker: already installed and backing cad_*; CadQuery/build123d would add a second OCCT stack with the same GProp risk. Revisit build123d if FreeCAD packaging stays unstable. |
| Aero (VLM, buildup, airfoils) | AeroSandbox + NeuralFoil, OpenVSP (VSPAERO), XFLR5 | **AeroSandbox** wrapped as a worker (MIT, pip-installable, scriptable). OpenVSP/VSPAERO next for panel-method + parasite drag (heavier install); XFLR5 is GUI-first. |
| Closed-form sizing | ecalc (closed, web), Raymer/Hepperle formulas | Implemented formulas directly (deterministic, testable, sources cited); use bench data instead of ecalc. |
| Previews | FreeCAD GUI offscreen, three.js/Playwright, matplotlib | **matplotlib Agg** in the FreeCAD env (no display needed). Playwright Chromium renders of the real viewer remain a roadmap item. |
| FEA | FreeCAD FEM + gmsh + CalculiX, OpenRadioss | gmsh is present in the FreeCAD env; CalculiX not installed. Roadmap. |

## Remaining gaps (prioritised for proving the drone)

1. Upstream cad_inspect_freecad_model mass/volume/bbox on curved solids (F1/F2) — report to design-mcp-server; until fixed, use airframe_inspect_step.
2. Control-surface sizing/derivatives (Cl_δa, Cm_δe, ruddervator mixing), trim and dynamic modes (short period, phugoid, Dutch roll, spiral) — export AeroSandbox stability derivatives into a 6-DoF model.
3. Propeller/motor matching from data (APC/UIUC prop tables, motor Kv/Rm/I0) instead of momentum theory; battery sag model.
4. Structural FEA of the wing/joiner/tail cone (FreeCAD FEM + CalculiX, or OpenRadioss for crash/landing).
5. Conventional tails, flaps, twin booms and multirotor/VTOL in the builder; airfoils beyond NACA 4-digit (Selig .dat import).
6. Output schemas for proxied tools (F10); KiCad install to verify avionics PCB/harness tools (F15).

## Roadmap to a proven aircraft

| Stage | Evidence produced | Tools |
|---|---|---|
| 1. Analysis closure | VLM/panel stability derivatives, trim at loiter/cruise, control power, gust loads | AeroSandbox (now), OpenVSP/VSPAERO |
| 2. Simulation | SITL with the CAD mass/inertia and derived aero (ArduPilot JSBSim or PX4 Gazebo), mission endurance with wind | ArduPilot/PX4 SITL, JSBSim aircraft XML generated from the reports |
| 3. CFD | fuselage/wing-root drag and tail downwash check at Re ≈ 2e5 | OpenFOAM or SU2 on the exported STEP |
| 4. FEA | joiner/spar/skin margins at 4 g × 1.5, tail-cone torsion, landing skid loads | FreeCAD FEM + CalculiX |
| 5. Bench | thrust stand (static/dynamic thrust, current), battery discharge at loiter current, servo torque, CG measurement on the built airframe, wing load test to limit load | thrust stand, scales, sandbag load test |
| 6. Flight | range-limited test plan: taxi/hand-launch glide at the forward CG, then CG sweeps aft in 5 mm steps, autotune, endurance build-up to the 20 % reserve, log-based CD0/e/efficiency fitting fed back into drone_fixed_wing_analysis | autopilot logs, airspeed calibration |
