# Fixed-wing airframe spec (schema unified-cad-mcp/fixed-wing-airframe/1)

Input to airframe_build_fixed_wing and airframe_aero_crosscheck. Frame: x aft from a
nose datum, y to port (starboard = -y), z up; millimetres, grams, degrees. Example:
tests/fixtures/reference-glider.airframe.json; full example: Widewing V0.3 spec in
SolutionsAsService/military-display.

| Key | Content |
|---|---|
| id | lowercase slug; names the assembly STEP / FCStd / viewer JSON |
| materials | name → {density_g_cm3} (solids, foam cores, tubes) or {areal_g_m2} (skins/laminates), plus note |
| fuselage | name, exponent (superellipse; 2 = ellipse), stations [{x_mm, width_mm, height_mm, z_mm}], construction |
| wing | name, airfoil (NACA 4-digit default), dihedral_deg, z_root_mm, stations [{s_mm (from centreline), x_le_mm, chord_mm, twist_deg, airfoil}], construction, aileron {s_start_mm, s_end_mm, chord_fraction}; port side is built and mirrored; the root is trimmed by the fuselage |
| tail | type "v" (only V-tail today), dihedral_deg, root_mm [x,y,z] translation, stations in the panel frame (s along the panel), airfoil, construction, ruddervator {s_start_mm, s_end_mm, chord_fraction} |
| spars | [{name, mirror, from_mm, to_mm, od_mm, id_mm, material, inside [...], pocket_in [...]}] |
| components | [{name, shape box/cylinder/cone/cell_pack, size/diameter/length, axis, center_mm, mass_g (per side if mirror), inside [...], pocket_in [...], pocket_open_down_mm (box pockets), category, group, color, note}] |
| envelopes | zero-mass keep-outs (e.g. propeller disc), same shape keys |
| allowances | [{name, mass_g, position_mm}] masses without geometry (wiring, adhesive) |
| budget | items_g {base name: g}, item_tolerance_fraction, total_g, total_tolerance_fraction, history |
| validation | min_wall_clearance_mm, allowed_overlaps [[a, b]] |
| export | step, stl, fcstd, stl_linear_deflection_mm, viewer_linear_deflection_mm |

construction.type: solid {material}, shell {skin} (mass = outer area × areal density),
core_skin {core, skin}. Mirrored parts are named "<name> · port" / "<name> · starboard";
inside/pocket_in references use those names and are mirrored automatically.

Mass properties are exact integrals over a 0.05 mm closed tessellation (see
docs/drone-testing-flaws.md F1). Extra keys (e.g. performance_inputs) are ignored by
the builder and can carry analysis inputs so the spec stays the single source of truth.
