"""AeroSandbox worker for airframe_aero_crosscheck (runs in a separate venv with aerosandbox).

Protocol: JSON request on stdin, JSON response on stdout (last line). Builds an
asb.Airplane from the same fixed-wing airframe spec used by the FreeCAD builder,
trims CL for level flight at the requested speed, and reports AeroBuildup
(NeuralFoil-based, includes fuselage) and VLM stability derivatives.
AeroSandbox: MIT licence, https://github.com/peterdsharpe/AeroSandbox
"""
import json
import math
import sys
import traceback


def main():
    raw = sys.stdin.read()
    try:
        import aerosandbox as asb
        import aerosandbox.numpy as np

        req = json.loads(raw)
        spec = req["spec"]
        m = 0.001
        wg = spec["wing"]
        dih = math.tan(math.radians(wg.get("dihedral_deg", 0.0)))
        z0 = wg.get("z_root_mm", 0.0)

        def foil(code):
            return asb.Airfoil("naca" + str(code).lower().replace("naca", ""))

        def control(name, s0, s1, frac, symmetric):
            return name, s0, s1, frac, symmetric

        wing_x = [asb.WingXSec(xyz_le=[st["x_le_mm"] * m, st["s_mm"] * m, (z0 + st["s_mm"] * dih) * m],
                               chord=st["chord_mm"] * m, twist=st.get("twist_deg", 0.0),
                               airfoil=foil(st.get("airfoil", wg.get("airfoil", "2412"))))
                  for st in wg["stations"]]
        wing = asb.Wing(name="wing", symmetric=True, xsecs=wing_x)
        tl = spec["tail"]
        g = math.radians(tl["dihedral_deg"])
        root = tl.get("root_mm", [0, 0, 0])
        tail_x = [asb.WingXSec(xyz_le=[st["x_le_mm"] * m, (root[1] + st["s_mm"] * math.cos(g)) * m, (root[2] + st["s_mm"] * math.sin(g)) * m],
                               chord=st["chord_mm"] * m, twist=st.get("twist_deg", 0.0), airfoil=foil(st.get("airfoil", tl.get("airfoil", "0009"))))
                  for st in tl["stations"]]
        tail = asb.Wing(name="v_tail", symmetric=True, xsecs=tail_x)
        fz = spec["fuselage"]
        fus = asb.Fuselage(name="fuselage", xsecs=[
            asb.FuselageXSec(xyz_c=[st["x_mm"] * m, 0, st.get("z_mm", 0.0) * m], width=st["width_mm"] * m,
                             height=st["height_mm"] * m, shape=fz.get("exponent", 2.0)) for st in fz["stations"]])
        cg = [req["cg_mm"][0] * m, req["cg_mm"][1] * m, req["cg_mm"][2] * m]
        plane = asb.Airplane(name=spec.get("id", "airframe"), xyz_ref=cg, wings=[wing, tail], fuselages=[fus])
        atmo = asb.Atmosphere(altitude=req.get("altitude_m", 0.0))
        v = req["velocity_m_s"]
        weight = req["mass_g"] / 1000 * 9.80665
        q = 0.5 * atmo.density() * v ** 2
        cl_target = weight / (q * plane.s_ref)

        def run(alpha):
            op = asb.OperatingPoint(atmosphere=atmo, velocity=v, alpha=alpha)
            return asb.AeroBuildup(airplane=plane, op_point=op).run()

        def f(x):
            return float(np.asarray(x).reshape(-1)[0])

        a0, a1 = 0.0, 4.0
        c0, c1 = f(run(a0)["CL"]), f(run(a1)["CL"])
        alpha = a1
        for _ in range(8):
            alpha = a1 + (cl_target - c1) * (a1 - a0) / (c1 - c0)
            a0, c0 = a1, c1
            a1, c1 = alpha, f(run(alpha)["CL"])
            if abs(c1 - cl_target) < 1e-4:
                break
        op = asb.OperatingPoint(atmosphere=atmo, velocity=v, alpha=alpha)
        ab = asb.AeroBuildup(airplane=plane, op_point=op).run_with_stability_derivatives(alpha=True, beta=True, p=True, q=True, r=True)
        vlm = asb.VortexLatticeMethod(airplane=plane, op_point=op, spanwise_resolution=req.get("vlm_spanwise", 10),
                                      chordwise_resolution=req.get("vlm_chordwise", 8)).run_with_stability_derivatives(alpha=True, beta=True, p=False, q=False, r=False)
        mac = wing.mean_aerodynamic_chord()
        keys = ["CL", "CD", "Cm", "x_np", "CLa", "Cma", "Cnb", "Clb", "CYb", "Clp", "Cmq", "Cnr"]
        abr = {k: round(f(ab[k]), 6) for k in keys if k in ab}
        vr = {k: round(f(vlm[k]), 6) for k in ["CL", "CD", "Cm", "x_np", "CLa", "Cma", "Cnb", "Clb"] if k in vlm}
        re = atmo.density() * v * mac / atmo.dynamic_viscosity()
        root_af = foil(wg["stations"][0].get("airfoil", wg.get("airfoil", "2412")))
        polar = root_af.get_aero_from_neuralfoil(alpha=np.array([-2.0, 0.0, 2.0, 4.0, 6.0, 8.0, 10.0, 12.0, 14.0]), Re=float(re), mach=0.0)
        result = {
            "method": "AeroSandbox %s: AeroBuildup (NeuralFoil section data + fuselage buildup) and VortexLatticeMethod" % asb.__version__,
            "velocity_m_s": v, "altitude_m": req.get("altitude_m", 0.0), "density_kg_m3": round(float(atmo.density()), 5),
            "reference": {"s_ref_m2": round(float(plane.s_ref), 5), "c_ref_m": round(float(plane.c_ref), 5), "b_ref_m": round(float(plane.b_ref), 5),
                          "wing_mac_m": round(float(mac), 5), "xyz_ref_m": cg},
            "trim_target_cl": round(cl_target, 5), "alpha_deg": round(float(alpha), 4),
            "aerobuildup": abr, "vlm": vr,
            "lift_to_drag_aerobuildup": round(abr["CL"] / abr["CD"], 3),
            "static_margin_aerobuildup": round((abr["x_np"] - cg[0]) / float(mac), 4),
            "static_margin_vlm": round((vr["x_np"] - cg[0]) / float(mac), 4),
            "reynolds_mac": round(float(re)),
            "root_airfoil_neuralfoil": {"alpha_deg": [-2, 0, 2, 4, 6, 8, 10, 12, 14],
                                        "cl": [round(float(x), 4) for x in polar["CL"]], "cd": [round(float(x), 5) for x in polar["CD"]],
                                        "confidence": [round(float(x), 3) for x in polar.get("analysis_confidence", [])]},
            "notes": ["Control surfaces, propeller slipstream, gaps and protuberances are not modelled.",
                      "VLM is inviscid and omits the fuselage; AeroBuildup is a semi-empirical buildup. Treat both as conceptual cross-checks."],
        }
        out = {"ok": True, "result": result}
    except Exception as error:
        out = {"ok": False, "error_type": type(error).__name__, "error": str(error)[:2000], "traceback": traceback.format_exc()[-4000:]}
    sys.stdout.write("\n" + json.dumps(out) + "\n")
    sys.stdout.flush()


main()
