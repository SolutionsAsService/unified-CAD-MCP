"""CalculiX static FEA of tubular members (spar, joiner, tail boom) meshed with gmsh.

Run with an interpreter that has gmsh (python-gmsh) + numpy, e.g. a conda-forge env
with 'calculix python-gmsh numpy'. Reads one JSON request on stdin, prints one JSON
line {"ok": true, "result": ...} on stdout.

Model per member: straight circular tube along +z, root face z=0 clamped (all DOF),
second-order tetrahedra (C3D10), linear isotropic material, small displacement.
Load cases (limit loads, N / N·mm): tip force, tip moment (as a statically equivalent
nodal force couple on the tip face), tip torque, and a uniform line load applied as a
body force (exact resultant). Stresses are reported from integration points; the
clamped root carries a constraint singularity, so the evaluation stations exclude
the first 'root_exclusion_od' outer diameters and the tip load zone. Each case is
compared with Euler-Bernoulli / Saint-Venant beam theory and a margin of safety
MS = allowable / (FoS * sigma_vm) - 1 is reported for both.
"""
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import traceback


def tube_section(od, idd):
    area = math.pi * (od ** 2 - idd ** 2) / 4
    inertia = math.pi * (od ** 4 - idd ** 4) / 64
    return area, inertia, 2 * inertia


def beam_theory(member, case, z):
    od, idd, length = member["od_mm"], member["id_mm"], member["length_mm"]
    area, inertia, polar = tube_section(od, idd)
    fx, fy, fz = case.get("tip_force_N", [0, 0, 0])
    mx, my, mz = case.get("tip_moment_Nmm", [0, 0, 0])
    qx, qy, qz = case.get("line_load_N_per_mm", [0, 0, 0])
    arm = length - z
    # Moments at station z from loads outboard of it (right-hand rule, beam axis +z).
    bx = mx - fy * arm - qy * arm ** 2 / 2
    by = my + fx * arm + qx * arm ** 2 / 2
    bending = math.hypot(bx, by)
    axial = (fz + qz * arm) / area
    sigma = bending * (od / 2) / inertia + abs(axial)
    tau = abs(mz) * (od / 2) / polar
    e = member["material"]["E_MPa"]
    tip = [fx * length ** 3 / (3 * e * inertia) + qx * length ** 4 / (8 * e * inertia) + my * length ** 2 / (2 * e * inertia),
           fy * length ** 3 / (3 * e * inertia) + qy * length ** 4 / (8 * e * inertia) - mx * length ** 2 / (2 * e * inertia)]
    return {"station_z_mm": z, "bending_moment_Nmm": bending, "sigma_bending_plus_axial_MPa": sigma,
            "tau_torsion_MPa": tau, "von_mises_MPa": math.sqrt(sigma ** 2 + 3 * tau ** 2),
            "tip_deflection_mm": math.hypot(*tip)}


def mesh_tube(gmsh, member, size):
    od, idd, length = member["od_mm"], member["id_mm"], member["length_mm"]
    gmsh.model.add(member["name"])
    occ = gmsh.model.occ
    outer = occ.addCylinder(0, 0, 0, 0, 0, length, od / 2)
    if idd > 0:
        inner = occ.addCylinder(0, 0, -1, 0, 0, length + 2, idd / 2)
        occ.cut([(3, outer)], [(3, inner)])
    occ.synchronize()
    gmsh.option.setNumber("Mesh.MeshSizeMax", size)
    gmsh.option.setNumber("Mesh.MeshSizeMin", size * 0.5)
    gmsh.option.setNumber("Mesh.ElementOrder", 2)
    gmsh.option.setNumber("Mesh.SecondOrderLinear", 1)  # straight-sided curved edges are fine at this size
    gmsh.option.setNumber("Mesh.Optimize", 1)
    gmsh.model.mesh.generate(3)
    tags, coords, _ = gmsh.model.mesh.getNodes()
    nodes = {int(t): (coords[3 * i], coords[3 * i + 1], coords[3 * i + 2]) for i, t in enumerate(tags)}
    types, etags, enodes = gmsh.model.mesh.getElements(3)
    elements = []
    for etype, et, en in zip(types, etags, enodes):
        if etype != 11:  # 10-node tetrahedron
            continue
        for i, tag in enumerate(et):
            elements.append((int(tag), [int(n) for n in en[10 * i:10 * i + 10]]))
    gmsh.model.remove()
    return nodes, elements


# gmsh 10-node tet ordering -> CalculiX C3D10 ordering (edge nodes 9 and 10 swapped).
GMSH_TO_CCX = [0, 1, 2, 3, 4, 5, 6, 7, 9, 8]


def write_inp(path, member, case, nodes, elements):
    length = member["length_mm"]
    mat = member["material"]
    area, _, _ = tube_section(member["od_mm"], member["id_mm"])
    tol = 1e-6 * max(1.0, length)
    root = [n for n, c in nodes.items() if abs(c[2]) < tol]
    tip = [n for n, c in nodes.items() if abs(c[2] - length) < tol]
    lines = ["*HEADING", f"unified-cad-mcp tube {member['name']} case {case['name']}", "*NODE"]
    lines += [f"{n},{c[0]:.9g},{c[1]:.9g},{c[2]:.9g}" for n, c in nodes.items()]
    lines.append("*ELEMENT,TYPE=C3D10,ELSET=EALL")
    lines += [f"{tag}," + ",".join(str(conn[k]) for k in GMSH_TO_CCX) for tag, conn in elements]
    lines.append("*NSET,NSET=ROOT")
    lines += [",".join(str(n) for n in root[i:i + 12]) for i in range(0, len(root), 12)]
    lines += ["*MATERIAL,NAME=M", "*ELASTIC", f"{mat['E_MPa']:.9g},{mat.get('nu', 0.3):.6g}"]
    q = case.get("line_load_N_per_mm", [0, 0, 0])
    qmag = math.sqrt(sum(v * v for v in q))
    if qmag > 0:
        # Body force per volume = q / A, realised as GRAV with a unit pseudo-density.
        lines += ["*DENSITY", "1."]
    lines += ["*SOLID SECTION,ELSET=EALL,MATERIAL=M", "*STEP", "*STATIC", "*BOUNDARY", "ROOT,1,3,0."]
    cload = {}

    def add(n, dof, value):
        cload[(n, dof)] = cload.get((n, dof), 0.0) + value

    force = case.get("tip_force_N", [0, 0, 0])
    for dof in range(3):
        if force[dof]:
            for n in tip:
                add(n, dof + 1, force[dof] / len(tip))
    mx, my, mz = case.get("tip_moment_Nmm", [0, 0, 0])
    # Couples about the tip-face node centroid; the mean is removed so each couple has zero net
    # force (the unstructured tip mesh is not exactly symmetric) and still the exact moment.
    cx = sum(nodes[n][0] for n in tip) / len(tip)
    cy = sum(nodes[n][1] for n in tip) / len(tip)
    rel = {n: (nodes[n][0] - cx, nodes[n][1] - cy) for n in tip}
    sxx = sum(x * x for x, _ in rel.values())
    syy = sum(y * y for _, y in rel.values())
    for n, (x, y) in rel.items():
        if mx:
            add(n, 3, mx * y / syy)  # sum(F_z * y) = Mx
        if my:
            add(n, 3, -my * x / sxx)  # sum(-F_z * x) = My
    if mz:
        fx = {n: -mz * y / (sxx + syy) for n, (x, y) in rel.items()}
        fy = {n: mz * x / (sxx + syy) for n, (x, y) in rel.items()}
        mean_x, mean_y = sum(fx.values()) / len(tip), sum(fy.values()) / len(tip)
        applied = sum(-y * (fx[n] - mean_x) + x * (fy[n] - mean_y) for n, (x, y) in rel.items())
        for n in tip:
            add(n, 1, (fx[n] - mean_x) * mz / applied)
            add(n, 2, (fy[n] - mean_y) * mz / applied)
    if cload:
        lines.append("*CLOAD")
        lines += [f"{n},{dof},{v:.9g}" for (n, dof), v in sorted(cload.items())]
    if qmag > 0:
        lines += ["*DLOAD", f"EALL,GRAV,{qmag / area:.9g},{q[0] / qmag:.9g},{q[1] / qmag:.9g},{q[2] / qmag:.9g}"]
    lines += ["*NODE PRINT,NSET=NALL", "U", "*EL PRINT,ELSET=EALL", "S", "*END STEP"]
    allnodes = sorted(nodes)
    at = lines.index("*NSET,NSET=ROOT")
    lines[at:at] = ["*NSET,NSET=NALL"] + [",".join(str(n) for n in allnodes[i:i + 12]) for i in range(0, len(allnodes), 12)]
    with open(path, "w") as handle:
        handle.write("\n".join(lines) + "\n")
    return len(root), len(tip)


def parse_dat(path):
    displacement, stresses, mode = {}, [], None
    with open(path) as handle:
        for line in handle:
            low = line.strip().lower()
            if low.startswith("displacements"):
                mode = "u"
                continue
            if low.startswith("stresses"):
                mode = "s"
                continue
            parts = line.split()
            if not parts:
                continue
            try:
                values = [float(v) for v in parts]
            except ValueError:
                mode = None if not low.startswith(("displacements", "stresses")) else mode
                continue
            if mode == "u" and len(values) == 4:
                displacement[int(values[0])] = values[1:]
            elif mode == "s" and len(values) == 8:
                stresses.append((int(values[0]), int(values[1]), values[2:]))
    return displacement, stresses


def von_mises(s):
    sxx, syy, szz, sxy, sxz, syz = s
    return math.sqrt(0.5 * ((sxx - syy) ** 2 + (syy - szz) ** 2 + (szz - sxx) ** 2) + 3 * (sxy ** 2 + sxz ** 2 + syz ** 2))


def run_member(gmsh, ccx, member, workdir, threads):
    od, idd, length = member["od_mm"], member["id_mm"], member["length_mm"]
    wall = (od - idd) / 2 if idd > 0 else od / 2
    size = member.get("mesh_size_mm") or max(min(wall * 1.5, od / 3), 0.3)
    nodes, elements = mesh_tube(gmsh, member, size)
    centroid = {}
    for tag, conn in elements:
        corner = [nodes[n] for n in conn[:4]]
        centroid[tag] = tuple(sum(c[k] for c in corner) / 4 for k in range(3))
    excl = member.get("root_exclusion_od", 1.5) * od
    tip_excl = member.get("tip_exclusion_od", 1.5) * od
    mat = member["material"]
    fos = member.get("factor_of_safety", 1.5)
    out = {"name": member["name"], "od_mm": od, "id_mm": idd, "length_mm": length,
           "mesh": {"element_type": "C3D10", "elements": len(elements), "nodes": len(nodes), "size_mm": size},
           "material": mat, "factor_of_safety": fos, "cases": []}
    for case in member["load_cases"]:
        job = os.path.join(workdir, f"{member['name']}-{case['name']}".replace(" ", "_"))
        nroot, ntip = write_inp(job + ".inp", member, case, nodes, elements)
        env = dict(os.environ, OMP_NUM_THREADS=str(threads), CCX_NPROC_EQUATION_SOLVER=str(threads))
        proc = subprocess.run([ccx, "-i", os.path.basename(job)], cwd=workdir, capture_output=True, text=True,
                              timeout=member.get("timeout_seconds", 600), env=env)
        if proc.returncode != 0 or not os.path.exists(job + ".dat"):
            raise RuntimeError(f"ccx failed for {member['name']}/{case['name']}: {proc.stdout[-800:]} {proc.stderr[-400:]}")
        displacement, stresses = parse_dat(job + ".dat")
        if not stresses or not displacement:
            raise RuntimeError(f"no results parsed from {job}.dat")
        tip_nodes = [n for n, c in nodes.items() if abs(c[2] - length) < 1e-6 * max(1, length)]
        tip_u = [sum(displacement[n][k] for n in tip_nodes) / len(tip_nodes) for k in range(3)]
        max_u = max(math.sqrt(sum(v * v for v in u)) for u in displacement.values())
        overall = max(von_mises(s) for _, _, s in stresses)
        window = [(e, von_mises(s)) for e, _, s in stresses if excl <= centroid[e][2] <= length - tip_excl]
        if window:
            peak_e, peak = max(window, key=lambda item: item[1])
            station = centroid[peak_e][2]
            # Compare at the same station with beam theory (outer-fibre value).
            band = [v for e, v in window if abs(centroid[e][2] - station) <= od]
        else:
            peak, station, band = overall, 0.0, []
        theory = beam_theory(member, case, station)
        theory_root = beam_theory(member, case, 0.0)
        allowable = mat["allowable_MPa"]
        result = {
            "name": case["name"], "loads": {k: case[k] for k in case if k != "name"},
            "nodes_root_clamped": nroot, "nodes_tip_loaded": ntip,
            "fea_von_mises_max_overall_MPa": overall,
            "fea_von_mises_peak_in_window_MPa": peak, "peak_station_z_mm": station,
            "evaluation_window_z_mm": [excl, length - tip_excl],
            "beam_theory_at_peak_station": theory,
            "beam_theory_at_root": theory_root,
            "fea_vs_beam_ratio": peak / theory["von_mises_MPa"] if theory["von_mises_MPa"] > 0 else None,
            "fea_tip_deflection_mm": math.sqrt(sum(v * v for v in tip_u)),
            "fea_tip_displacement_mm": tip_u, "fea_max_displacement_mm": max_u,
            "beam_tip_deflection_mm": theory["tip_deflection_mm"],
            # Root moment governs a cantilever: extrapolate the FEA/beam ratio to the root moment.
            "design_von_mises_root_MPa": theory_root["von_mises_MPa"] * max(1.0, peak / theory["von_mises_MPa"]) if theory["von_mises_MPa"] > 0 else overall,
        }
        result["margin_of_safety"] = allowable / (fos * result["design_von_mises_root_MPa"]) - 1 if result["design_von_mises_root_MPa"] > 0 else None
        result["margin_of_safety_beam_theory"] = allowable / (fos * theory_root["von_mises_MPa"]) - 1 if theory_root["von_mises_MPa"] > 0 else None
        result["pass"] = result["margin_of_safety"] is not None and result["margin_of_safety"] >= 0
        out["cases"].append(result)
    return out


def main():
    request = json.loads(sys.stdin.read())
    ccx = request.get("ccx") or shutil.which("ccx") or os.path.join(sys.prefix, "bin", "ccx")
    if not os.path.exists(ccx):
        raise RuntimeError("CalculiX 'ccx' not found (conda-forge package 'calculix')")
    import gmsh
    gmsh.initialize(["-noenv"], readConfigFiles=False)
    gmsh.option.setNumber("General.Terminal", 0)
    gmsh.option.setNumber("General.NumThreads", request.get("threads", 2))
    workdir = request.get("work_dir") or tempfile.mkdtemp(prefix="unified-fea-")
    os.makedirs(workdir, exist_ok=True)
    try:
        members = [run_member(gmsh, ccx, m, workdir, request.get("threads", 2)) for m in request["members"]]
    finally:
        gmsh.finalize()
    version = subprocess.run([ccx, "-v"], capture_output=True, text=True).stdout.strip().splitlines()
    return {"solver": {"ccx": ccx, "version": version[-1] if version else "unknown", "gmsh": gmsh.__version__},
            "work_dir": workdir, "members": members,
            "all_pass": all(c["pass"] for m in members for c in m["cases"]),
            "method": __doc__.strip()}


if __name__ == "__main__":
    try:
        print(json.dumps({"ok": True, "result": main()}))
    except Exception as error:  # noqa: BLE001 - reported to the server as JSON
        print(json.dumps({"ok": False, "error_type": type(error).__name__, "error": str(error),
                          "traceback": traceback.format_exc()}))
