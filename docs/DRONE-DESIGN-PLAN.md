# Drone / UAV design: gap analysis and integration plan

Status date: 2026-10-12. Scope: what the unified MCP (pinned kicad_*, cad_*, pcb_*,
freecad_* backends plus the new opt-in drone_* estimators) can and cannot do across
a complete drone design workflow, and a phased plan to close the gaps.

Legend: **covered** = a tool performs the step (native runtime may still be
required); **partial** = useful pieces exist but the step needs manual work or
lacks a key capability; **missing** = no tool in this server performs it.
"Native" means the step only works with KiCad (kicad-cli/pcbnew, KiCad GUI IPC)
or FreeCAD installed on the host; discovery alone never proves that.

Licenses below are as published by each project at the time of writing. They are
planning notes, not legal clearance: re-check the LICENSE file at the exact pin
before integrating. GPL/AGPL tools are proposed only as **separately installed
executables in isolated backends** (the same subprocess pattern used today), not
as linked/vendored code.

## 1. Gap table

| # | Workflow step | Status | Existing tools | What is missing | Candidate open-source pieces (license) |
|---|---|---|---|---|---|
| 1 | Requirements and mission profile | partial | cad_add_project_requirement, cad_add_project_parameter, cad_create_project_record (hash-linked scalar records; need DESIGN_MCP_REVISION_ROOT) | Mission model (segments: takeoff, climb, cruise/hover, loiter, reserve), payload, wind, endurance/range targets, derived requirements | New pure drone_mission_profile (this repo); AeroSandbox mission/energy helpers (MIT) |
| 2 | Airframe CAD: frame, arms, motor mounts, canopy; parametric | partial (native) | freecad_execute_python (unrestricted, live GUI), opt-in freecad_* sketch/pad/pocket/fillet helpers (provisional), cad_create_box_revision (boxes only) | Headless parametric generator for X/H/deadcat frames, arm profiles, motor bolt circles, FC stack holes, canopy shells; no live GUI should be required | build123d (Apache-2.0) or CadQuery (Apache-2.0) on OCP/OCCT (LGPL-2.1 with exception) in an isolated backend; FreeCAD PartDesign via FreeCADCmd (LGPL-2.1+) |
| 3 | Mass / CG / inertia budget | partial → **slice implemented** | **drone_mass_cg_budget** (point masses + solid boxes, CG offset, inertia tensor, principal moments; heuristic budget) | CAD mass properties (volume × material density) feeding the budget automatically; material library | build123d/CadQuery or FreeCAD Shape.Volume / MatrixOfInertia with explicit densities |
| 4 | Propulsion sizing: motor, prop, ESC, battery, TWR, flight time | partial → **slice implemented** | **drone_propulsion_estimate** (momentum theory + figure of merit, hover power/current/time, TWR from datasheet thrust, ESC and C-rate) | Prop performance tables (CT/CP vs RPM), motor model (Kv, Rm, I0), battery sag/Peukert, forward-flight and mixed-mission endurance, temperature | AeroSandbox propulsion and battery models (MIT); user-supplied manufacturer thrust tables (CSV); UIUC propeller database (academic data, check terms). eCalc itself is proprietary and is not a candidate |
| 5 | Aerodynamics (fixed-wing airfoil, VLM, stability) | missing | none | Airfoil polars, wing/tail sizing, VLM/AVL lift and stability derivatives, drag build-up | AeroSandbox (MIT) with NeuralFoil (MIT); XFOIL (GPL-2.0) and AVL (GPL-2.0) as subprocesses; OpenVSP (NASA Open Source Agreement 1.3) for geometry + VSPAERO |
| 6 | Structural FEA (arms, plates, mounts) | missing | none | Mesh, loads (thrust, crash/drop, vibration), static stress/deflection, modal frequencies vs motor RPM bands | FreeCAD FEM (LGPL-2.1+) driving CalculiX (GPL-2.0) headless via FreeCADCmd; Gmsh (GPL-2.0+) |
| 7 | Flight-controller / PDB PCB: schematic, layout, DRC, BOM, fab outputs | covered (native) / partial | kicad_schematic, kicad_pcb (outline, footprints, nets, zones), kicad_drc, kicad_audit, kicad_autoroute (FreeRouting, needs Java), kicad_panelize_pcb (KiKit), kicad_export (gerbers, bom_csv, thumbnail), kicad_lcsc, kicad_design (firmware → intent → partial schematic), pcb_* live IPC routing | Native kicad-cli/pcbnew absent on this host, so DRC/export were not exercised. No PCB STEP export, no explicit drill/pick-and-place export, no high-current trace sizing (IPC-2152) for PDB, no FC reference templates (MCU + IMU + baro + UART/ESC connectors) | kicad-cli pcb export step/drill/pos (KiCad, GPL-3.0+) wrapped in a new tool; IPC-2152 approximation in pure Python; KiKit (MIT) already used |
| 8 | Harness / wiring | missing | none | Connector pinouts, wire gauge vs current, lengths, harness drawing/BOM | WireViz (GPL-3.0) as subprocess; pure-Python gauge/voltage-drop check |
| 9 | Firmware configuration (PX4 / ArduPilot params) | missing | kicad_design captures firmware *electrical* intent only | Frame/motor geometry, battery, failsafe, ESC protocol, sensor orientation params generated from the design and validated against upstream parameter metadata | PX4 parameter metadata (PX4, BSD-3-Clause); ArduPilot parameter definitions (GPL-3.0) consumed as data; MAVSDK (BSD-3-Clause) / pymavlink (LGPL-3.0) for upload, opt-in only |
| 10 | Simulation (Gazebo / SITL / jMAVSim) | missing | none | SITL launch with the generated params and model mass/inertia/thrust, scripted hover/step tests, log extraction | PX4 SITL + Gazebo (Apache-2.0) / jMAVSim (BSD-3-Clause); ArduPilot SITL (GPL-3.0); run in a container with bounded runtime and no host network exposure |
| 11 | Mechanical-electrical integration (board outline ↔ frame holes, STEP export/import) | partial | kicad_pcb set_outline / get_footprint_dimensions, cad_inspect_freecad_model, cad_inspect_cad_envelopes (AABB, UNKNOWN on overlap), cad_register_component_candidate, cad_create_assembly_manifest (records, no fit proof) | Mounting-pattern check (30.5×30.5 M3, 20×20 M2, 25.5×25.5 whoop, 16×16 M2), board STEP export, automatic transform alignment, clearance to props/battery | Pure drone_mounting_pattern_check (this repo); kicad-cli pcb export step; build123d assembly + OCCT distance queries |
| 12 | Manufacturing: STL/3MF printing, DXF for CNC/carbon plates, slicing | partial (native) | freecad_export_stl / freecad_export_step (provisional, opt-in, live GUI), freecad_execute_python, cad_inspect_stl_geometry (units UNKNOWN) | Headless STL/3MF/DXF export, plate nesting, slicer integration (time/filament estimate, printability), CNC toolpath hand-off | build123d/CadQuery exporters (Apache-2.0); ezdxf (MIT); lib3mf (BSD-2-Clause); PrusaSlicer CLI (AGPL-3.0) or OrcaSlicer (AGPL-3.0) as subprocess |
| 13 | Compliance (weight classes, Remote ID) | partial → **slice implemented** | **drone_regulatory_weight_class** (coarse FAA/EASA weight bands and Remote ID notes, not legal advice) | Live rule sources, Remote ID module selection, RF (FCC/CE) and battery transport (UN 38.3) checklists, operations-over-people categories | OpenDroneID core (Apache-2.0) for Remote ID message formats; official FAA/EASA pages as sources |

Summary: electronics (step 7) is the strongest area, but only with native KiCad.
Mechanical design is GUI-bound (FreeCAD + NiRuLink) or box-only. Aerodynamics, FEA,
harness, firmware parameters and simulation have **no tools**. Steps 3, 4 and 13 now
have first-order heuristic calculators.

## 2. First slice implemented (Phase 0)

drone_* is an **in-process FastMCP sub-server** (src/unified_cad_mcp/drone.py), mounted
only when the trusted config sets "drone_tools": true (bootstrap flag
--enable-drone-tools). It is pure Python: no files, subprocesses, network or native
CAD. All three tools are annotated read-only/idempotent/closed-world.

- drone_mass_cg_budget(items[{name, mass_g, position_mm[3], size_mm?[3], category?}],
  reference_point_mm?, max_takeoff_mass_g?, cg_tolerance_mm?) → total mass, CG, CG offset,
  inertia tensor about the CG (kg·m²), principal moments, category breakdown, margins.
- drone_propulsion_estimate(all_up_mass_g, rotor_count, prop_diameter_in, battery_cells,
  battery_capacity_mah, chemistry, efficiencies, figure_of_merit, altitude, coaxial,
  max_thrust_per_motor_g?, motor_max_current_a?, battery_c_rating?) → hover thrust per
  rotor, disk loading, ideal/shaft/electrical hover power, hover current, hover time,
  TWR (only if datasheet thrust supplied), ESC rating, required C rating, warnings.
- drone_regulatory_weight_class(takeoff_mass_g, jurisdiction US|EU, use) → weight band,
  registration / Remote ID notes, official source links, disclaimer.

What is heuristic: momentum theory with a constant figure of merit (default 0.6)
and lumped motor (0.8) and ESC (0.95) efficiencies; nominal cell voltage; constant
usable-capacity fraction; a crude 1.25 coaxial penalty; static hover only. Expect
±20–30 % versus bench data. Thrust-to-weight is never invented: it is UNKNOWN unless
the caller supplies a datasheet max thrust. The weight-class tool is a simplified
summary and rules change; always verify with FAA/EASA. Tests: tests/test_drone.py
(independent momentum-theory recomputation, trend checks, inertia tensor and
trace invariants, weight-band edges, opt-in exposure and schema validation).

## 3. Phased roadmap

| Phase | Goal | Deliverables | Isolation / license notes |
|---|---|---|---|
| 0 (done) | First-order sizing | drone_mass_cg_budget, drone_propulsion_estimate, drone_regulatory_weight_class | In-process, pure Python, opt-in |
| 1 | Headless parametric airframe | airframe_* backend on build123d: frame plates, arms, motor mounts, stack holes, canopy shell; STEP/STL/3MF/DXF export into a dedicated output root; mass properties → drone_mass_cg_budget; drone_mounting_pattern_check | Separate venv (OCP wheels are large); Apache-2.0; output root distinct from inputs like cad_ revision roots |
| 2 | Propulsion and aerodynamics | Prop/motor table import for drone_propulsion_estimate (CT/CP or thrust tables, Kv/Rm/I0), forward-flight endurance; aero_* backend on AeroSandbox/NeuralFoil (airfoil polars, VLM wing, fixed-wing cruise endurance) | AeroSandbox MIT in its own venv; XFOIL/AVL (GPL) only as optional external executables |
| 3 | Electronics completion | kicad STEP/drill/position export wrapper (kicad-cli), IPC-2152 trace width/temperature calculator for PDB, FC/PDB reference intent templates for kicad_design, harness_* via WireViz | kicad-cli native, GPL-3.0+ executable; WireViz GPL-3.0 subprocess |
| 4 | Firmware and simulation | fc_params_* generating PX4/ArduPilot parameter files from the design and validating against pinned parameter metadata; sim_* running PX4 or ArduPilot SITL in a container for a bounded hover/step test, returning log metrics compared to the Phase 0 estimate | Container required; no MAVLink upload to real hardware without explicit opt-in flag and confirmation |
| 5 | Structures and manufacturing | fea_* using FreeCAD FEM + CalculiX headless (static arm bending, plate deflection, modal); slicer_* using PrusaSlicer CLI (print time, filament, G-code into output root); DXF nesting for CNC plates | GPL/AGPL executables isolated; outputs only inside a dedicated root |
| 6 | Compliance and release | Release checklist tool (mass class, Remote ID module present, RF modules, battery transport, labeling), BOM cost roll-up from kicad_export bom_csv + mechanical BOM | Advisory only; sources linked, no legal claims |

## 4. Proposed tools (schema sketches)

All new tools must follow existing conventions: explicit units in field names,
UNKNOWN instead of invented values, read-only annotations where true, outputs only
in dedicated output roots, no network by default.

~~~text
drone_mission_profile(
  segments: [{kind: "takeoff"|"climb"|"hover"|"cruise"|"loiter"|"descent"|"reserve",
              duration_s?: number, distance_m?: number, speed_m_s?: number, climb_m?: number}],
  all_up_mass_g, propulsion: <drone_propulsion_estimate args>, wind_m_s?: number
) -> {energy_wh_by_segment, total_energy_wh, reserve_ok: bool, warnings, estimate_kind}

drone_mounting_pattern_check(
  board: {holes_mm: [[x,y]...], hole_diameter_mm, outline_mm?: [[x,y]...]},
  frame: {pattern: "30.5x30.5"|"20x20"|"25.5x25.5"|"16x16"|"custom", holes_mm?: [[x,y]...], screw: "M2"|"M3"},
  tolerance_mm = 0.2
) -> {matched: bool, max_hole_error_mm, rotation_deg, keepout_violations, warnings}

airframe_generate_frame(
  layout: "X"|"H"|"deadcat"|"plus", motor_to_motor_mm, arm_width_mm, arm_thickness_mm,
  plate_thickness_mm, motor_bolt_circle_mm, motor_screw: "M2"|"M3", stack_pattern,
  material: {name, density_g_cm3}, output_name
) -> {revision_id, files: {step, stl, dxf, "3mf"}, sha256, mass_properties: {mass_g, cg_mm, inertia_kg_m2}}

aero_airfoil_polar(airfoil: "naca2412"|<coordinates>, reynolds, mach=0, alpha_deg: [..])
  -> {cl, cd, cm per alpha, method: "NeuralFoil"|"XFOIL", converged flags}

aero_wing_vlm(span_m, root_chord_m, tip_chord_m, sweep_deg, dihedral_deg, airfoil, velocity_m_s, alpha_deg)
  -> {CL, CDi, Cm, neutral_point_m, static_margin?, method}

kicad_export_mechanical(pcb_path, outputs: ["step","drill","pos"], output_dir)   # wraps kicad-cli
pdb_trace_width(current_a, copper_oz, temp_rise_c, layer: "external"|"internal") -> {width_mm, method: "IPC-2152 approximation"}
harness_render(connectors, cables, connections, output_name) -> {svg, bom_tsv, sha256}

fc_params_generate(firmware: "px4"|"ardupilot", frame: {type, motor_count, geometry_mm},
  battery: {cells, capacity_mah}, esc_protocol, failsafes, sensor_orientation)
  -> {param_file_text, validated_against: {metadata_version, sha256}, unknown_params, warnings}

sim_sitl_hover_test(firmware, params_file, model: {mass_kg, inertia_kg_m2, max_thrust_n, arm_length_m},
  duration_s <= 300) -> {hover_throttle, attitude_rms_deg, log_path, sim_versions}

fea_static_arm(step_path, fixed_faces, load_n, load_face, material: {E_gpa, nu, yield_mpa}, mesh_size_mm)
  -> {max_von_mises_mpa, max_displacement_mm, safety_factor, mesh_stats, solver: "CalculiX x.y"}

slicer_slice(stl_or_3mf_path, profile_ini, output_name)
  -> {gcode_path, print_time_s, filament_g, warnings, slicer_version}
~~~

## 5. Acceptance tests (per phase)

- **Phase 0:** independent momentum-theory recomputation within 0.1 %; density at
  5000 m ISA within 0.3 %; symmetric quad CG at origin and inertia matching the
  parallel-axis theorem; trace invariance of principal moments; FAA/EASA band edges
  at 250 g, 900 g, 4 kg, 25 kg; opt-in exposure (absent unless drone_tools=true).
  Implemented in tests/test_drone.py.
- **Phase 1:** generated 5-inch X frame STEP re-imports with the expected bounding box
  (±0.1 mm) and hole positions; mass properties within 1 % of an analytic plate +
  arm calculation; DXF contains closed outer contour; output never written outside
  the output root (path traversal test); identical parameters produce identical hashes.
- **Phase 2:** NACA 2412 polar at Re=1e6 within published-data tolerance bands;
  rectangular wing CL_alpha matches lifting-line theory within 5 %; propulsion with
  a supplied thrust table reproduces the table at table points.
- **Phase 3:** kicad-cli STEP of a fixture board has the board outline dimensions;
  drill file hole count equals footprint pad holes; IPC-2152 calculator matches
  reference chart points within 10 %; WireViz output hash stable.
- **Phase 4:** generated PX4/ArduPilot params contain no names absent from the
  pinned metadata; SITL hover test in a container reaches stable hover and its
  hover throttle is within 25 % of the Phase 0 prediction for the same model; no
  network port bound outside the container.
- **Phase 5:** cantilever arm FEA within 5 % of Euler-Bernoulli beam theory for a
  rectangular section; slicer returns non-zero time/filament for a fixture STL and
  writes G-code only into the output root.
- **Phase 6:** checklist flags a 260 g build as needing registration/Remote ID (US)
  and links official sources.

## 6. Risks and limits

- Native KiCad/FreeCAD were not available on the integration host; electronics and
  GUI CAD steps remain unverified end to end.
- freecad_execute_python is unrestricted code execution in the GUI user session; it
  must not become the default path for automated airframe generation (Phase 1
  replaces it with a headless, parameter-only backend).
- The design-mcp-server source still declares no license; resolve before any
  redistribution or deeper coupling.
- Heuristic tools must keep returning their assumptions and HEURISTIC labels; they
  are not a substitute for thrust-stand, FEA or flight-test evidence.
