"""OpenVSP worker: rebuild the fixed-wing spec in OpenVSP, run VSPAERO (VLM or panel) and Parasite Drag.

Run with a Python matching the OpenVSP python build (3.12 for the official 3.55
Ubuntu 24.04 .deb) and LD_LIBRARY_PATH providing libgomp/libcminpack. Request on
stdin, last stdout line = JSON {"ok", "result"}. OpenVSP/VSPAERO: NASA Open Source
Agreement 1.3, https://openvsp.org.

Geometry mapping (metres): wing and V-tail are OpenVSP WING components with one
segment per spec station pair (span, root/tip chord, LE sweep, dihedral, twist, NACA
4-digit section); the fuselage is a FUSELAGE with super-ellipse sections at the spec
stations. Control surfaces, gaps, propeller and protuberances are not modelled.
"""
import json
import math
import os
import sys
import traceback


def setup_path(root):
    for sub in ("openvsp", "degen_geom", "openvsp_config", "utilities"):
        p = os.path.join(root, "python", sub)
        if os.path.isdir(p):
            sys.path.insert(0, p)


def naca(code):
    code = str(code).lower().replace("naca", "")
    if len(code) != 4 or not code.isdigit():
        raise ValueError(f"only NACA 4-digit sections are mapped, got {code!r}")
    return int(code[0]) / 100, int(code[1]) / 10, int(code[2:]) / 100


def build_lifting(vsp, name, stations, root_xyz, dihedral_deg, default_af):
    m = 0.001
    geom = vsp.AddGeom("WING")
    vsp.SetGeomName(geom, name)
    surf = vsp.GetXSecSurf(geom, 0)
    for _ in range(len(stations) - 2):
        vsp.InsertXSec(geom, 1, vsp.XS_FOUR_SERIES)
    vsp.Update()
    vsp.SetParmVal(geom, "X_Rel_Location", "XForm", root_xyz[0] * m)
    vsp.SetParmVal(geom, "Y_Rel_Location", "XForm", root_xyz[1] * m)
    vsp.SetParmVal(geom, "Z_Rel_Location", "XForm", root_xyz[2] * m)
    for j in range(1, len(stations)):
        a, b = stations[j - 1], stations[j]
        span = (b["s_mm"] - a["s_mm"]) * m
        group = f"XSec_{j}"
        vsp.SetParmVal(geom, "Span", group, span)
        vsp.SetParmVal(geom, "Root_Chord", group, a["chord_mm"] * m)
        vsp.SetParmVal(geom, "Tip_Chord", group, b["chord_mm"] * m)
        vsp.SetParmVal(geom, "Sweep", group, math.degrees(math.atan2((b["x_le_mm"] - a["x_le_mm"]) * m, span)))
        vsp.SetParmVal(geom, "Sweep_Location", group, 0.0)
        vsp.SetParmVal(geom, "Dihedral", group, dihedral_deg)
        vsp.SetParmVal(geom, "Twist", group, b.get("twist_deg", 0.0))  # outboard-section twist of this segment
        vsp.Update()
    for j, st in enumerate(stations):
        vsp.ChangeXSecShape(surf, j, vsp.XS_FOUR_SERIES)
        xs = vsp.GetXSec(surf, j)
        camber, loc, thick = naca(st.get("airfoil", default_af))
        vsp.SetParmVal(vsp.GetXSecParm(xs, "Camber"), camber)
        vsp.SetParmVal(vsp.GetXSecParm(xs, "CamberLoc"), loc if camber > 0 else 0.4)
        vsp.SetParmVal(vsp.GetXSecParm(xs, "ThickChord"), thick)
    if stations[0].get("twist_deg"):
        vsp.SetParmVal(geom, "Twist", "XSec_0", stations[0]["twist_deg"])
    vsp.Update()
    return geom


def build_fuselage(vsp, fus):
    m = 0.001
    st = fus["stations"]
    geom = vsp.AddGeom("FUSELAGE")
    vsp.SetGeomName(geom, "fuselage")
    surf = vsp.GetXSecSurf(geom, 0)
    while vsp.GetNumXSec(surf) < len(st):
        vsp.InsertXSec(geom, 1, vsp.XS_SUPER_ELLIPSE)
    while vsp.GetNumXSec(surf) > len(st):
        vsp.CutXSec(geom, 1)
    vsp.Update()
    x0, x1 = st[0]["x_mm"], st[-1]["x_mm"]
    length = (x1 - x0) * m
    vsp.SetParmVal(geom, "Length", "Design", length)
    vsp.SetParmVal(geom, "X_Rel_Location", "XForm", x0 * m)
    # OrderPolicy 0 = MONOTONIC (1 = LOOP would tie the last section to the first). Stations are
    # set front to back so each XLocPercent stays within its neighbours' limits.
    vsp.SetParmVal(geom, "OrderPolicy", "Design", 0.0)
    vsp.Update()
    for j, s in enumerate(st):
        w, h = s["width_mm"], s["height_mm"]
        point = w <= 0.5 or h <= 0.5
        vsp.ChangeXSecShape(surf, j, vsp.XS_POINT if point else vsp.XS_SUPER_ELLIPSE)
        xs = vsp.GetXSec(surf, j)
        vsp.SetParmVal(vsp.GetXSecParm(xs, "XLocPercent"), (s["x_mm"] - x0) / (x1 - x0))
        vsp.SetParmVal(vsp.GetXSecParm(xs, "ZLocPercent"), s.get("z_mm", 0.0) * m / length)
        if not point:
            vsp.SetParmVal(vsp.GetXSecParm(xs, "Super_Width"), w * m)
            vsp.SetParmVal(vsp.GetXSecParm(xs, "Super_Height"), h * m)
            n = fus.get("exponent", 2.0)
            vsp.SetParmVal(vsp.GetXSecParm(xs, "Super_M"), n)
            vsp.SetParmVal(vsp.GetXSecParm(xs, "Super_N"), n)
    vsp.Update()
    return geom


def results(vsp, name):
    rid = vsp.FindLatestResultsID(name)
    if not rid:
        return None, {}
    data = {}
    for key in vsp.GetAllDataNames(rid):
        kind = vsp.GetResultsType(rid, key)
        if kind == vsp.DOUBLE_DATA:
            data[key] = list(vsp.GetDoubleResults(rid, key, 0))
        elif kind == vsp.INT_DATA:
            data[key] = list(vsp.GetIntResults(rid, key, 0))
        elif kind == vsp.STRING_DATA:
            data[key] = list(vsp.GetStringResults(rid, key, 0))
    return rid, data


def main():
    req = json.loads(sys.stdin.read())
    root = req["openvsp_root"]
    setup_path(root)
    import openvsp as vsp
    vsp.VSPCheckSetup()
    vsp.SetVSPAEROPath(root)
    vsp.ClearVSPModel()
    spec = req["spec"]
    wg, tl = spec["wing"], spec["tail"]
    wing = build_lifting(vsp, "wing", wg["stations"], [wg["stations"][0]["x_le_mm"], 0.0, wg.get("z_root_mm", 0.0)],
                         wg.get("dihedral_deg", 0.0), wg.get("airfoil", "2412"))
    troot = tl.get("root_mm", [0, 0, 0])
    tail = build_lifting(vsp, "v_tail", tl["stations"], [tl["stations"][0]["x_le_mm"], troot[1], troot[2]],
                         tl["dihedral_deg"], tl.get("airfoil", "0009"))
    geoms = {"wing": wing, "v_tail": tail}
    if req.get("include_fuselage", True):
        geoms["fuselage"] = build_fuselage(vsp, spec["fuselage"])
    vsp.Update()
    work = req["work_dir"]
    os.makedirs(work, exist_ok=True)
    vsp3 = os.path.join(work, spec.get("id", "airframe") + ".vsp3")
    vsp.WriteVSPFile(vsp3)
    out = {"openvsp_version": vsp.GetVSPVersion(), "vsp3_file": vsp3, "geometry": {}}
    for label, g in geoms.items():
        out["geometry"][label] = {"bbox_m": [[vsp.GetParmVal(g, "X_Min", "BBox"), vsp.GetParmVal(g, "Y_Min", "BBox"), vsp.GetParmVal(g, "Z_Min", "BBox")],
                                             [vsp.GetParmVal(g, "X_Len", "BBox"), vsp.GetParmVal(g, "Y_Len", "BBox"), vsp.GetParmVal(g, "Z_Len", "BBox")]],
                                  "bbox_note": "[min xyz], [extent xyz] in metres"}
    v = req["velocity_m_s"]
    rho = req.get("density_kg_m3", 1.225)
    mu = req.get("dynamic_viscosity_pa_s", 1.789e-5)
    cg = [c * 0.001 for c in req["cg_mm"]]
    method = req.get("method", "vlm")
    # Reference quantities from the main wing (OpenVSP computes Sref/bref/cref).
    vsp.SetAnalysisInputDefaults("VSPAEROComputeGeometry")
    if method == "panel":
        vsp.SetIntAnalysisInput("VSPAEROComputeGeometry", "GeomSet", [vsp.SET_ALL])
        vsp.SetIntAnalysisInput("VSPAEROComputeGeometry", "ThinGeomSet", [vsp.SET_NONE])
    else:
        vsp.SetIntAnalysisInput("VSPAEROComputeGeometry", "GeomSet", [vsp.SET_NONE])
        vsp.SetIntAnalysisInput("VSPAEROComputeGeometry", "ThinGeomSet", [vsp.SET_ALL])
    vsp.SetIntAnalysisInput("VSPAEROComputeGeometry", "Symmetry", [1])
    vsp.ExecAnalysis("VSPAEROComputeGeometry")
    a = "VSPAEROSweep"
    vsp.SetAnalysisInputDefaults(a)
    if method == "panel":
        vsp.SetIntAnalysisInput(a, "GeomSet", [vsp.SET_ALL])
        vsp.SetIntAnalysisInput(a, "ThinGeomSet", [vsp.SET_NONE])
    else:
        vsp.SetIntAnalysisInput(a, "GeomSet", [vsp.SET_NONE])
        vsp.SetIntAnalysisInput(a, "ThinGeomSet", [vsp.SET_ALL])
    vsp.SetIntAnalysisInput(a, "RefFlag", [vsp.COMPONENT_REF])
    vsp.SetStringAnalysisInput(a, "WingID", [wing])
    vsp.SetDoubleAnalysisInput(a, "Xcg", [cg[0]])
    vsp.SetDoubleAnalysisInput(a, "Ycg", [cg[1]])
    vsp.SetDoubleAnalysisInput(a, "Zcg", [cg[2]])
    alphas = req.get("alpha_deg") or [-4.0, -2.0, 0.0, 2.0, 4.0, 6.0, 8.0, 10.0]
    vsp.SetDoubleAnalysisInput(a, "AlphaStart", [float(alphas[0])])
    vsp.SetDoubleAnalysisInput(a, "AlphaEnd", [float(alphas[-1])])
    vsp.SetIntAnalysisInput(a, "AlphaNpts", [len(alphas)])
    vsp.SetDoubleAnalysisInput(a, "BetaStart", [0.0])
    vsp.SetIntAnalysisInput(a, "BetaNpts", [1])
    mach = v / 340.3
    vsp.SetDoubleAnalysisInput(a, "MachStart", [mach])
    vsp.SetIntAnalysisInput(a, "MachNpts", [1])
    vsp.SetDoubleAnalysisInput(a, "Vinf", [v])
    vsp.SetDoubleAnalysisInput(a, "Vref", [v])
    vsp.SetDoubleAnalysisInput(a, "Rho", [rho])
    vsp.SetIntAnalysisInput(a, "Symmetry", [1])
    vsp.SetIntAnalysisInput(a, "WakeNumIter", [req.get("wake_iterations", 3)])
    vsp.SetIntAnalysisInput(a, "NCPU", [int(req.get("threads", 2))])
    vsp.Update()
    # cref for Reynolds number
    vsp.ExecAnalysis(a)
    _, polar = results(vsp, "VSPAERO_Polar")
    if not polar:
        raise RuntimeError("VSPAERO produced no VSPAERO_Polar results (check vspaero binary and LD_LIBRARY_PATH)")
    def col(*names):
        for n in names:
            if n in polar:
                return polar[n]
        return None
    alpha = col("Alpha")
    cl = col("CLtot", "CL")
    cdi = col("CDi", "CDitot")
    cdtot = col("CDtot", "CD")
    cm = col("CMytot", "CMy", "CMytot_total")
    # Neutral point from dCm/dCL about the CG (both from the same sweep).
    n = len(cl)
    mean = lambda xs: sum(xs) / len(xs)
    def slope(x, y):
        mx, my = mean(x), mean(y)
        return sum((a - mx) * (b - my) for a, b in zip(x, y)) / sum((a - mx) ** 2 for a in x)
    dcm_dcl = slope(cl, cm) if cm else None
    cla = slope([math.radians(x) for x in alpha], cl)
    # MAC of the spec wing (trapezoid panels), metres
    st = wg["stations"]
    sref_half = mac_num = 0.0
    for i in range(1, len(st)):
        c0, c1 = st[i - 1]["chord_mm"] * 0.001, st[i]["chord_mm"] * 0.001
        span = (st[i]["s_mm"] - st[i - 1]["s_mm"]) * 0.001
        sref_half += (c0 + c1) / 2 * span
        mac_num += (c0 ** 2 + c0 * c1 + c1 ** 2) / 3 * span
    cref = req.get("cref_m") or mac_num / sref_half
    x_np = cg[0] - dcm_dcl * cref if dcm_dcl is not None else None
    out["vspaero"] = {
        "method": "VLM (thin surfaces)" if method != "panel" else "panel (thick surfaces)",
        "mach": mach, "velocity_m_s": v, "density_kg_m3": rho,
        "alpha_deg": alpha, "CL": cl, "CDi": cdi, "CDtot": cdtot, "CMy": cm,
        "CL_alpha_per_rad": cla, "dCm_dCL": dcm_dcl, "cref_m": cref,
        "x_np_m": x_np, "static_margin": (x_np - cg[0]) / cref if x_np is not None else None,
        "polar_columns": sorted(polar)[:80],
    }
    # Trim-point estimate: interpolate alpha for the requested CL.
    if req.get("mass_g"):
        s_ref = 2 * sref_half if not req.get("sref_m2") else req["sref_m2"]
        cl_req = req["mass_g"] / 1000 * 9.80665 / (0.5 * rho * v ** 2 * s_ref)
        pts = sorted(zip(cl, alpha, cdi or [None] * n, cm or [None] * n))
        trim = None
        for (c0, a0, d0, m0), (c1, a1, d1, m1) in zip(pts, pts[1:]):
            if c0 <= cl_req <= c1:
                f = (cl_req - c0) / (c1 - c0)
                trim = {"CL": cl_req, "alpha_deg": a0 + f * (a1 - a0),
                        "CDi": d0 + f * (d1 - d0) if d0 is not None else None,
                        "CMy_about_cg": m0 + f * (m1 - m0) if m0 is not None else None}
        out["vspaero"]["trim"] = trim or {"CL": cl_req, "note": "CL outside the alpha sweep"}
        out["vspaero"]["sref_m2"] = s_ref
    # Parasite drag build-up (OpenVSP ParasiteDrag tool: Swet, form factors, Cf per component).
    p = "ParasiteDrag"
    vsp.SetAnalysisInputDefaults(p)
    vsp.SetIntAnalysisInput(p, "LengthUnit", [vsp.LEN_M])
    vsp.SetIntAnalysisInput(p, "VelocityUnit", [vsp.V_UNIT_M_S])
    vsp.SetDoubleAnalysisInput(p, "Vinf", [v])
    vsp.SetDoubleAnalysisInput(p, "Altitude", [req.get("altitude_m", 0.0)])
    vsp.SetIntAnalysisInput(p, "RefFlag", [vsp.COMPONENT_REF])
    vsp.SetStringAnalysisInput(p, "WingID", [wing])
    vsp.SetStringAnalysisInput(p, "FileName", [os.path.join(work, "parasite_drag.csv")])
    vsp.SetIntAnalysisInput(p, "GeomSet", [vsp.SET_ALL])
    try:
        vsp.ExecAnalysis(p)
        _, pd = results(vsp, "Parasite_Drag")
    except Exception as error:  # noqa: BLE001
        pd = {"error": str(error)}
    keep = {k: pd[k] for k in pd if k in ("Comp_Label", "Comp_Swet", "Comp_FF", "Comp_Cf", "Comp_CD", "Comp_Re", "Comp_Q",
                                          "Total_CD_Total", "FC_Sref", "Geom_Sref", "Total_Swet", "Total_f_Total", "Sref", "error")}
    out["parasite_drag"] = keep or {"available_keys": sorted(pd)[:60]}
    cd0 = keep.get("Total_CD_Total", [None])[0] if isinstance(keep.get("Total_CD_Total"), list) else None
    out["parasite_drag"]["CD0"] = cd0
    if cd0 and out["vspaero"].get("trim") and out["vspaero"]["trim"].get("CDi") is not None:
        t = out["vspaero"]["trim"]
        t["CD_total_estimate"] = cd0 + t["CDi"]
        t["L_over_D_estimate"] = t["CL"] / t["CD_total_estimate"]
    out["notes"] = ["VSPAERO VLM is inviscid; CDi is induced drag only. CD0 comes from OpenVSP's component build-up "
                    "(flat-plate Cf x form factor x Swet), not from the panel solution.",
                    "Fuselage in VLM mode is omitted unless method='panel' (thick-surface panel method).",
                    "Control surfaces, propeller, gaps and protuberances are not modelled."]
    return out


if __name__ == "__main__":
    # VSPAERO/OpenVSP print progress from C code; send fd 1 to stderr so the only stdout
    # line is the JSON response.
    sys.stdout.flush()
    reply = os.fdopen(os.dup(1), "w")
    os.dup2(2, 1)
    try:
        response = {"ok": True, "result": main()}
    except Exception as error:  # noqa: BLE001
        response = {"ok": False, "error_type": type(error).__name__, "error": str(error)[:2000],
                    "traceback": traceback.format_exc()[-4000:]}
    sys.stdout.flush()
    reply.write(json.dumps(response) + "\n")
    reply.flush()
