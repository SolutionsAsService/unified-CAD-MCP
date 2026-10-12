"""Headless FreeCAD worker for airframe_* tools (runs in FreeCAD's own Python).

Protocol: one JSON request on stdin, one JSON response on stdout (last line).
Operations: build (parametric fixed-wing airframe from a spec), inspect_step,
render (matplotlib Agg previews of a viewer-parts JSON). FreeCAD is imported FIRST:
with conda-forge FreeCAD 26.x, importing FreeCAD after json/math removes those
names from __main__ (see docs/audit-2026-10-12.md finding 3).

conda-forge FreeCAD installs FreeCAD.so into <env>/lib, which is not on sys.path of a
stock env. If the first import fails, add that directory and retry, without binding any
other names in __main__ (so the import-order constraint above still holds). This makes
a plain `micromamba create -n freecad freecad` interpreter work without a host .pth.
"""
try:
    import FreeCAD  # noqa: E402  (must be first)
except ImportError:
    __import__("sys").path.append(__import__("os").path.join(__import__("sys").prefix, "lib"))
    import FreeCAD  # noqa: E402
import Part  # noqa: E402


def _main():
    import hashlib
    import json
    import math
    import os
    import re
    import shutil
    import sys
    import traceback

    V = FreeCAD.Vector
    SLUG = re.compile(r"[^a-z0-9]+")

    def slug(name):
        return SLUG.sub("-", name.lower().replace("·", " ")).strip("-")

    def sha256(path):
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()

    # ------------------------------------------------------------ geometry helpers
    def naca4(code, n=48):
        code = str(code).lower().replace("naca", "").strip()
        if not re.fullmatch(r"\d{4}", code):
            raise ValueError("Only NACA 4-digit airfoils are supported, got %r" % code)
        m, p, t = int(code[0]) / 100, int(code[1]) / 10, int(code[2:]) / 100
        xs = [(1 - math.cos(math.pi * i / n)) / 2 for i in range(n + 1)]
        up, lo = [], []
        for x in xs:
            yt = 5 * t * (0.2969 * math.sqrt(x) - 0.1260 * x - 0.3516 * x ** 2 + 0.2843 * x ** 3 - 0.1015 * x ** 4)
            if m and p:
                if x < p:
                    yc, dy = m / p ** 2 * (2 * p * x - x ** 2), 2 * m / p ** 2 * (p - x)
                else:
                    yc, dy = m / (1 - p) ** 2 * ((1 - 2 * p) + 2 * p * x - x ** 2), 2 * m / (1 - p) ** 2 * (p - x)
            else:
                yc, dy = 0.0, 0.0
            th = math.atan(dy)
            up.append((x - yt * math.sin(th), yc + yt * math.cos(th)))
            lo.append((x + yt * math.sin(th), yc - yt * math.cos(th)))
        return list(reversed(up)) + lo[1:]  # TE upper -> LE -> TE lower

    def airfoil_wire(code, chord, x_le, twist_deg, to_world):
        pts = []
        th = math.radians(twist_deg)
        for xc, zc in naca4(code):
            dx, dz = (xc - 0.25) * chord, zc * chord
            xr = dx * math.cos(th) + dz * math.sin(th)
            zr = dz * math.cos(th) - dx * math.sin(th)
            pts.append(to_world(x_le + 0.25 * chord + xr, zr))
        curve = Part.BSplineCurve()
        curve.interpolate(pts)
        edges = [curve.toShape()]
        if (pts[0] - pts[-1]).Length > 1e-6:
            edges.append(Part.makeLine(pts[-1], pts[0]))
        return Part.Wire(edges)

    def superellipse_wire(x, width, height, zc, exponent, n=40):
        pts = []
        for i in range(n):
            a = 2 * math.pi * i / n
            c, s = math.cos(a), math.sin(a)
            y = width / 2 * math.copysign(abs(c) ** (2 / exponent), c)
            z = zc + height / 2 * math.copysign(abs(s) ** (2 / exponent), s)
            pts.append(V(x, y, z))
        curve = Part.BSplineCurve()
        curve.interpolate(pts, PeriodicFlag=True)
        return Part.Wire([curve.toShape()])

    def prism_xy(poly, zmin=-2000, zmax=2000):
        pts = [V(x, y, zmin) for x, y in poly] + [V(poly[0][0], poly[0][1], zmin)]
        face = Part.Face(Part.makePolygon(pts))
        return face.extrude(V(0, 0, zmax - zmin))

    def lerp(stations, s, key):
        for a, b in zip(stations, stations[1:]):
            if a["s_mm"] <= s <= b["s_mm"]:
                t = (s - a["s_mm"]) / (b["s_mm"] - a["s_mm"])
                return a[key] + t * (b[key] - a[key])
        raise ValueError("station %s outside span" % s)

    def surface_solid(stations, default_airfoil, to_world_factory):
        wires = []
        for st in stations:
            wires.append(airfoil_wire(st.get("airfoil", default_airfoil), st["chord_mm"], st["x_le_mm"],
                                      st.get("twist_deg", 0.0), to_world_factory(st["s_mm"])))
        solid = Part.makeLoft(wires, True, True)  # solid, ruled = exact trapezoidal panels
        if solid.Volume < 0:
            solid.reverse()
        return solid

    def control_prism(stations, s0, s1, fraction):
        h0 = lerp(stations, s0, "x_le_mm") + (1 - fraction) * lerp(stations, s0, "chord_mm")
        h1 = lerp(stations, s1, "x_le_mm") + (1 - fraction) * lerp(stations, s1, "chord_mm")
        return prism_xy([(h0, s0), (h1, s1), (h1 + 5000, s1), (h0 + 5000, s0)])

    def tube(p0, p1, od, idd):
        """Returns (tube solid, outer envelope used to pocket the foam core)."""
        a, b = V(*p0), V(*p1)
        d = b - a
        length = d.Length
        d.normalize()
        outer = Part.makeCylinder(od / 2, length, a, d)
        return (outer.cut(Part.makeCylinder(idd / 2, length, a, d)) if idd > 0 else outer), outer

    def primitive(item):
        shape = item["shape"]
        c = V(*item["center_mm"])
        axis = {"x": V(1, 0, 0), "y": V(0, 1, 0), "z": V(0, 0, 1)}[item.get("axis", "x")]
        if shape == "box":
            lx, ly, lz = item["size_mm"]
            return Part.makeBox(lx, ly, lz, c - V(lx / 2, ly / 2, lz / 2))
        if shape == "cylinder":
            L = item["length_mm"]
            return Part.makeCylinder(item["diameter_mm"] / 2, L, c - axis * (L / 2), axis)
        if shape == "cone":
            L = item["length_mm"]
            # start_diameter at the -axis end, end_diameter at the +axis end
            return Part.makeCone(item["start_diameter_mm"] / 2, item.get("end_diameter_mm", 0) / 2, L, c - axis * (L / 2), axis)
        if shape == "cell_pack":
            ny, nz = item["grid"]
            d, L, gap = item["cell_diameter_mm"], item["cell_length_mm"], item.get("gap_mm", 0.6)
            pitch = d + gap
            cells = []
            for iy in range(ny):
                for iz in range(nz):
                    y = c.y + (iy - (ny - 1) / 2) * pitch
                    z = c.z + (iz - (nz - 1) / 2) * pitch
                    cells.append(Part.makeCylinder(d / 2, L, V(c.x - L / 2, y, z), V(1, 0, 0)))
            return Part.makeCompound(cells)
        raise ValueError("Unknown primitive shape %r for %s" % (shape, item.get("name")))

    def mirror_y(shape):
        return shape.mirror(V(0, 0, 0), V(0, 1, 0))

    # ------------------------------------------------------------ mass properties
    # OCCT GProp (Shape.Volume / CenterOfMass / MatrixOfInertia) was measured 13-20 % low on
    # BSpline-surfaced solids in FreeCAD 26.3 (conda-forge, OCCT nightly) and reported volume
    # GAINS after pocket cuts. Mass properties are therefore integrated exactly over a fine
    # closed tessellation (divergence theorem / tetrahedral covariance); GProp is reported
    # only as a cross-check.
    import MeshPart
    import numpy as np

    MESH_DEFLECTION = 0.05  # mm, chordal deviation of the mass-property mesh

    def mesh_arrays(shape, deflection=None):
        mesh = MeshPart.meshFromShape(Shape=shape, LinearDeflection=deflection or MESH_DEFLECTION,
                                      AngularDeflection=0.2, Relative=False)
        pts, facets = mesh.Topology
        P = np.array([[p.x, p.y, p.z] for p in pts], dtype=float)
        F = np.array(facets, dtype=np.int64)
        return P, F, mesh

    def solid_integrals(shape):
        """Exact volume, first and second moments of the closed tessellation (mm units)."""
        if not shape.Solids:
            return 0.0, np.zeros(3), np.zeros((3, 3)), {"closed": False, "triangles": 0}
        P, F, mesh = mesh_arrays(shape)
        a, b, c = P[F[:, 0]], P[F[:, 1]], P[F[:, 2]]
        det = np.einsum("ij,ij->i", a, np.cross(b, c))
        vol = det.sum() / 6.0
        s = a + b + c
        first = (det[:, None] * s).sum(axis=0) / 24.0
        outer = (np.einsum("ni,nj->nij", a, a) + np.einsum("ni,nj->nij", b, b) + np.einsum("ni,nj->nij", c, c)
                 + np.einsum("ni,nj->nij", s, s))
        second = (det[:, None, None] * outer).sum(axis=0) / 120.0
        if vol < 0:  # inward-wound tessellation
            vol, first, second = -vol, -first, -second
        return vol, first, second, {"closed": bool(mesh.isSolid()), "triangles": int(len(F))}

    def surface_integrals(shape):
        P, F, _ = mesh_arrays(shape)
        a, b, c = P[F[:, 0]], P[F[:, 1]], P[F[:, 2]]
        area = 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)
        s = a + b + c
        first = (area[:, None] * s).sum(axis=0) / 3.0
        outer = (np.einsum("ni,nj->nij", a, a) + np.einsum("ni,nj->nij", b, b) + np.einsum("ni,nj->nij", c, c)
                 + np.einsum("ni,nj->nij", s, s))
        second = (area[:, None, None] * outer).sum(axis=0) / 12.0
        return area.sum(), first, second

    def to_item(mass, measure, first, second):
        """Mass item (g, CG mm, inertia about CG g*mm^2) from integrals of a uniform density."""
        if measure <= 0 or mass <= 0:
            return None
        rho = mass / measure
        com = first / measure
        cov = rho * (second - measure * np.outer(com, com))
        inertia = np.trace(cov) * np.eye(3) - cov
        return (float(mass), com.tolist(), inertia.tolist())

    def tight_bbox(shape):
        try:
            # geometry-based (useTriangulation=False): a cached coarse triangulation can be incomplete
            bb = shape.optimalBoundingBox(False, False)
        except Exception:  # older FreeCAD
            bb = shape.BoundBox
        return [[round(bb.XMin, 3), round(bb.YMin, 3), round(bb.ZMin, 3)], [round(bb.XMax, 3), round(bb.YMax, 3), round(bb.ZMax, 3)]]

    def export_mesh(shape, deflection, expect_area):
        """Fresh triangulation for STL/viewer export, validated against the fine mass mesh.

        Coarse BRepMesh runs were seen to silently drop whole faces (mid-fuselage) while
        leaving their vertices; retry with halved deflection until the mesh area matches
        the mass-mesh area within 1 % and solids stay watertight.
        """
        d = deflection
        for _ in range(5):
            s = shape.copy()
            try:
                s.clean()  # drop cached triangulations
            except Exception:
                pass
            mesh = MeshPart.meshFromShape(Shape=s, LinearDeflection=d, AngularDeflection=0.35, Relative=False)
            ok_area = expect_area <= 0 or abs(mesh.Area - expect_area) <= 0.01 * expect_area
            ok_closed = (not shape.Solids) or mesh.isSolid()
            if ok_area and ok_closed:
                return mesh, d, True
            d = max(d / 2, MESH_DEFLECTION)
        return mesh, d, False

    def mesh_volume(shape):
        return solid_integrals(shape)[0] if shape.Solids else 0.0

    def combine(items):
        """items: (mass_g, com[3] mm, inertia_about_com g*mm^2 3x3). Parallel-axis sum."""
        total = sum(i[0] for i in items)
        if total <= 0:
            return 0.0, [0.0, 0.0, 0.0], [[0.0] * 3 for _ in range(3)]
        com = [sum(i[0] * i[1][k] for i in items) / total for k in range(3)]
        inertia = [[0.0] * 3 for _ in range(3)]
        for m, c, I in items:
            d = [c[k] - com[k] for k in range(3)]
            dd = sum(x * x for x in d)
            for a in range(3):
                for b in range(3):
                    inertia[a][b] += I[a][b] + m * ((dd if a == b else 0.0) - d[a] * d[b])
        return total, com, inertia

    def solid_props(shape, density_g_mm3):
        vol, first, second, _ = solid_integrals(shape)
        item = to_item(vol * density_g_mm3, vol, first, second)
        return [item] if item else []

    def surface_props(shape, areal_g_mm2):
        area, first, second = surface_integrals(shape)
        item = to_item(area * areal_g_mm2, area, first, second)
        return [item] if item else []

    def fixed_mass_props(shape, mass_g):
        vol, first, second, _ = solid_integrals(shape)
        item = to_item(mass_g, vol, first, second)
        return [item] if item else []

    # ------------------------------------------------------------ build
    def build(req):
        spec = req["spec"]
        out = req["output_dir"]
        mats = spec["materials"]
        parts = []  # dicts: name, base, shape, kind, construction, color, group, note, explode, inside

        def add(name, base, shape, construction, color, group, note, explode=(0, 0, 0), inside=None, category="structure",
                role="structure", pocket_in=None, pocket_shape=None):
            parts.append({"name": name, "base": base, "shape": shape, "construction": construction, "color": color,
                          "group": group, "note": note, "explode": list(explode), "inside": inside or [],
                          "category": category, "role": role, "pocket_in": pocket_in or [], "pocket_shape": pocket_shape})

        def starboard(names):
            return [n.replace("· port", "· starboard") for n in (names or [])]

        # fuselage
        fz = spec["fuselage"]
        wires = [superellipse_wire(st["x_mm"], st["width_mm"], st["height_mm"], st.get("z_mm", 0.0), fz.get("exponent", 2.0))
                 for st in fz["stations"]]
        fuselage = Part.makeLoft(wires, True, False)
        if fuselage.Volume < 0:
            fuselage.reverse()
        fus_name = fz.get("name", "Fuselage shell")

        # wing (port side built, starboard mirrored)
        wg = spec["wing"]
        dih = math.tan(math.radians(wg.get("dihedral_deg", 0.0)))
        z0 = wg.get("z_root_mm", 0.0)
        wing = surface_solid(wg["stations"], wg.get("airfoil", "2412"),
                             lambda s: (lambda x, z: V(x, s, z0 + s * dih + z)))
        wing = wing.cut(fuselage)
        ail = wg.get("aileron")
        aileron = None
        if ail:
            prism = control_prism(wg["stations"], ail["s_start_mm"], ail["s_end_mm"], ail["chord_fraction"])
            aileron = wing.common(prism)
            wing = wing.cut(prism)
        wn = wg.get("name", "Main wing")
        add(wn + " · port", wn, wing, wg["construction"], wg.get("color", "#d5dade"), "wing",
            wg.get("note", "Lofted NACA wing panel"), (0, 260, 0))
        add(wn + " · starboard", wn, mirror_y(wing), wg["construction"], wg.get("color", "#d5dade"), "wing",
            wg.get("note", "Lofted NACA wing panel"), (0, -260, 0))
        if aileron is not None:
            an = ail.get("name", "Aileron")
            add(an + " · port", an, aileron, wg["construction"], ail.get("color", "#9fb3bf"), "control surface",
                ail.get("note", "Aileron split from the wing loft at the hinge line"), (60, 300, -20))
            add(an + " · starboard", an, mirror_y(aileron), wg["construction"], ail.get("color", "#9fb3bf"), "control surface",
                ail.get("note", "Aileron split from the wing loft at the hinge line"), (60, -300, -20))

        # V-tail (local panel frame, then rotated by dihedral about +x)
        tl = spec["tail"]
        gamma = tl["dihedral_deg"]
        local = surface_solid(tl["stations"], tl.get("airfoil", "0009"), lambda s: (lambda x, z: V(x, s, z)))
        rv = tl.get("ruddervator")
        rud = None
        if rv:
            prism = control_prism(tl["stations"], rv["s_start_mm"], rv["s_end_mm"], rv["chord_fraction"])
            rud = local.common(prism)
            local = local.cut(prism)
        root = V(*tl["root_mm"])

        def place(shape):
            s = shape.copy()
            s.rotate(V(0, 0, 0), V(1, 0, 0), gamma)
            s.translate(root)
            return s

        panel = place(local).cut(fuselage)
        tn = tl.get("name", "V-tail panel")
        add(tn + " · port", tn, panel, tl["construction"], tl.get("color", "#c3ccd2"), "tail",
            tl.get("note", "Lofted symmetric-section V-tail panel"), (80, 120, 120))
        add(tn + " · starboard", tn, mirror_y(panel), tl["construction"], tl.get("color", "#c3ccd2"), "tail",
            tl.get("note", "Lofted symmetric-section V-tail panel"), (80, -120, 120))
        if rud is not None:
            rn = rv.get("name", "Ruddervator")
            rp = place(rud)
            add(rn + " · port", rn, rp, tl["construction"], rv.get("color", "#8fa6b4"), "control surface",
                rv.get("note", "Ruddervator split at the hinge line"), (140, 150, 150))
            add(rn + " · starboard", rn, mirror_y(rp), tl["construction"], rv.get("color", "#8fa6b4"), "control surface",
                rv.get("note", "Ruddervator split at the hinge line"), (140, -150, 150))

        add(fus_name, fus_name, fuselage, fz["construction"], fz.get("color", "#e3e7ea"), "fuselage",
            fz.get("note", "Lofted fuselage outer mould line; mass from wetted area x laminate areal density"), (0, 0, 0))

        for sp in spec.get("spars", []):
            shape, outer = tube(sp["from_mm"], sp["to_mm"], sp["od_mm"], sp.get("id_mm", 0))
            con = {"type": "solid", "material": sp["material"]}
            ex = list(sp.get("explode", (0, 200, 40)))
            if sp.get("mirror"):
                add(sp["name"] + " · port", sp["name"], shape, con, sp.get("color", "#2f3438"), "structure",
                    sp.get("note", ""), ex, sp.get("inside"), "structure", "structure", sp.get("pocket_in"), outer)
                add(sp["name"] + " · starboard", sp["name"], mirror_y(shape), con, sp.get("color", "#2f3438"), "structure",
                    sp.get("note", ""), [ex[0], -ex[1], ex[2]], starboard(sp.get("inside")), "structure", "structure",
                    starboard(sp.get("pocket_in")), mirror_y(outer))
            else:
                add(sp["name"], sp["name"], shape, con, sp.get("color", "#2f3438"), "structure", sp.get("note", ""),
                    ex, sp.get("inside"), "structure", "structure", sp.get("pocket_in"), outer)

        for item in spec.get("components", []) + spec.get("envelopes", []):
            role = "envelope" if item in spec.get("envelopes", []) else "component"
            shape = primitive(item)
            if item.get("pocket_open_down_mm") and item["shape"] == "box":
                # open the foam pocket through the lower skin (servo/hatch access) instead of a sealed cavity
                lx, ly, lz = item["size_mm"]
                drop = float(item["pocket_open_down_mm"])
                c0 = V(*item["center_mm"])
                pocket = Part.makeBox(lx, ly, lz + drop, c0 - V(lx / 2, ly / 2, lz / 2 + drop))
            else:
                pocket = None
            con = {"type": "envelope"} if role == "envelope" else {"type": "fixed_mass", "mass_g": item["mass_g"]}
            if item.get("mirror"):  # mass_g is per side
                add(item["name"] + " · port", item["name"], shape, dict(con), item.get("color", "#6b7c88"), item.get("group", role),
                    item.get("note", ""), item.get("explode", (0, 0, -120)), item.get("inside"), item.get("category", "other"), role,
                    item.get("pocket_in"), pocket)
                ex = list(item.get("explode", (0, 0, -120)))
                ex[1] = -ex[1]
                add(item["name"] + " · starboard", item["name"], mirror_y(shape), dict(con), item.get("color", "#6b7c88"),
                    item.get("group", role), item.get("note", ""), ex, starboard(item.get("inside")),
                    item.get("category", "other"), role, starboard(item.get("pocket_in")), mirror_y(pocket) if pocket is not None else None)
            else:
                add(item["name"], item["name"], shape, con, item.get("color", "#6b7c88"), item.get("group", role),
                    item.get("note", ""), item.get("explode", (0, 0, -120)), item.get("inside"), item.get("category", "other"), role,
                    item.get("pocket_in"), pocket)

        names = [p["name"] for p in parts]
        if len(set(names)) != len(names):
            raise ValueError("Duplicate part names: %s" % sorted({n for n in names if names.count(n) > 1}))
        by_name = {p["name"]: p for p in parts}
        for p in parts:
            for n in p["inside"] + p["pocket_in"]:
                if n not in by_name:
                    raise ValueError("%s references unknown part %r" % (p["name"], n))
        # containment is judged against the outer mould lines BEFORE pockets are cut
        oml = {p["name"]: p["shape"].copy() for p in parts}
        pocket_log = []
        for p in parts:
            for n in p["pocket_in"]:
                host = by_name[n]
                if host["construction"]["type"] == "shell":
                    raise ValueError("Cannot pocket %s into shell part %s" % (p["name"], n))
                before = mesh_volume(host["shape"])
                host["shape"] = host["shape"].cut(p["pocket_shape"] or p["shape"])
                removed = before - mesh_volume(host["shape"])
                pocket_log.append({"part": p["name"], "host": n, "removed_mm3": round(removed, 2)})
                if removed <= 0:
                    raise ValueError("Pocket boolean for %s in %s did not remove material (%.1f mm3)" % (p["name"], n, removed))

        # mass properties
        rows = []
        mass_items = []
        for p in parts:
            con = p["construction"]
            shape = p["shape"]
            kind = con["type"]
            if kind == "envelope":
                items = []
            elif kind == "solid":
                items = solid_props(shape, mats[con["material"]]["density_g_cm3"] / 1000)
            elif kind == "shell":
                items = surface_props(shape, mats[con["skin"]]["areal_g_m2"] / 1e6)
            elif kind == "core_skin":
                items = solid_props(shape, mats[con["core"]]["density_g_cm3"] / 1000) + \
                    surface_props(shape, mats[con["skin"]]["areal_g_m2"] / 1e6)
            elif kind == "fixed_mass":
                items = fixed_mass_props(shape, con["mass_g"])
            else:
                raise ValueError("Unknown construction type %r" % kind)
            m, com, I = combine(items)
            mvol, _, _, minfo = solid_integrals(shape)
            gvol = sum(s.Volume for s in shape.Solids)
            row = {"name": p["name"], "base": p["base"], "role": p["role"], "category": p["category"],
                   "construction": con, "mass_g": round(m, 3), "cg_mm": [round(v, 3) for v in com],
                   "inertia_about_cg_kg_m2": [[round(v * 1e-9, 9) for v in r] for r in I],
                   "volume_mm3": round(mvol, 2), "kernel_gprop_volume_mm3": round(gvol, 2),
                   "gprop_volume_error_fraction": round((gvol - mvol) / mvol, 5) if mvol else None,
                   "mass_mesh": minfo, "area_mm2": round(surface_integrals(shape)[0], 2),
                   "bbox_mm": tight_bbox(shape)}
            rows.append(row)
            if m > 0:
                mass_items.append((m, com, I))
        for al in spec.get("allowances", []):
            rows.append({"name": al["name"], "base": al["name"], "role": "allowance", "category": al.get("category", "other"),
                         "construction": {"type": "allowance"}, "mass_g": al["mass_g"], "cg_mm": al["position_mm"],
                         "note": al.get("note", "")})
            mass_items.append((al["mass_g"], al["position_mm"], [[0.0] * 3 for _ in range(3)]))
        total, cg, inertia = combine(mass_items)

        # validation
        checks = []
        bop_advisories = []

        def check(name, ok, detail=None):
            checks.append({"name": name, "pass": bool(ok), **({"detail": detail} if detail is not None else {})})

        for p in parts:
            s = p["shape"]
            problems = []
            if not s.isValid():
                problems.append("shape invalid")
            try:
                s.check(True)
            except Exception as error:  # BOPCheck failure text
                problems.append("check(): " + str(error)[:200])
            advisories = [x for x in problems if "GeomAbs_C0" in x]
            problems = [x for x in problems if "GeomAbs_C0" not in x]
            if not s.Solids:
                problems.append("no solids")
            for i, so in enumerate(s.Solids):
                if not all(sh.isClosed() for sh in so.Shells):
                    problems.append("solid %d has an open shell" % i)
                vol, _, _, info = solid_integrals(so)
                marea = surface_integrals(so)[0]
                if so.Area > 0 and abs(marea - so.Area) > 0.01 * so.Area:
                    problems.append("solid %d mass-mesh area %.0f differs from kernel area %.0f by > 1 %% (incomplete triangulation?)" % (i, marea, so.Area))
                if vol <= 0:
                    problems.append("solid %d non-positive volume" % i)
                if not info["closed"]:
                    problems.append("solid %d tessellation is not watertight" % i)
            check("valid closed solid: " + p["name"], not problems, problems or None)
            if advisories:
                bop_advisories.append({"part": p["name"], "advisory": "BOPCheck reports C0-continuous edge geometry (booleans still succeeded; smooth before CAM)"})
        for p in parts:
            if p["name"].endswith(" · port"):
                other = p["name"][: -len(" · port")] + " · starboard"
                a = next(r for r in rows if r["name"] == p["name"])
                b = next(r for r in rows if r["name"] == other)
                ok = abs(a["mass_g"] - b["mass_g"]) <= 1e-6 * max(1, a["mass_g"]) and \
                    abs(a["cg_mm"][0] - b["cg_mm"][0]) < 1e-3 and abs(a["cg_mm"][1] + b["cg_mm"][1]) < 1e-3 and \
                    abs(a["cg_mm"][2] - b["cg_mm"][2]) < 1e-3
                check("mirror symmetry: " + p["base"], ok)
        clearance_min = spec.get("validation", {}).get("min_wall_clearance_mm", 0.0)
        for p in parts:
            if not p["inside"]:
                continue
            containers = [oml[n] for n in p["inside"]]
            container = containers[0] if len(containers) == 1 else containers[0].fuse(containers[1:])
            vol = mesh_volume(p["shape"])
            inside = mesh_volume(p["shape"].common(container)) / vol
            detail = {"inside_fraction": round(inside, 5), "containers": p["inside"]}
            ok = inside >= 0.999
            if clearance_min and p["role"] == "component" and fus_name in p["inside"]:
                dist = by_name[fus_name]["shape"].Shells[0].distToShape(p["shape"])[0]
                detail["wall_clearance_mm"] = round(dist, 3)
                ok = ok and dist >= clearance_min
            check("contained: " + p["name"], ok, detail)
        allowed = {frozenset(pair) for pair in spec.get("validation", {}).get("allowed_overlaps", [])}
        interference = []
        solid_parts = [p for p in parts if p["role"] != "envelope"]
        for i, a in enumerate(solid_parts):
            for b in solid_parts[i + 1:]:
                if frozenset((a["base"], b["base"])) in allowed or frozenset((a["name"], b["name"])) in allowed:
                    continue
                if not a["shape"].BoundBox.intersect(b["shape"].BoundBox):
                    continue
                if a["name"] == fus_name or b["name"] == fus_name:
                    continue  # fuselage OML is a container, not a solid occupant
                common_shape = a["shape"].common(b["shape"])
                common = mesh_volume(common_shape) if common_shape.Solids else 0.0
                if common > 1.0:
                    interference.append({"a": a["name"], "b": b["name"], "common_mm3": round(common, 2)})
        check("no unexpected interference between parts", not interference, interference or None)

        # budget comparison
        budget = spec.get("budget", {})
        by_base = {}
        for r in rows:
            by_base[r["base"]] = by_base.get(r["base"], 0.0) + r["mass_g"]
        budget_rows = []
        tol = budget.get("item_tolerance_fraction", 0.15)
        for base, target in budget.get("items_g", {}).items():
            actual = by_base.get(base)
            ok = actual is not None and abs(actual - target) <= tol * target
            budget_rows.append({"item": base, "budget_g": target, "cad_g": None if actual is None else round(actual, 2),
                                "delta_fraction": None if actual is None else round((actual - target) / target, 4), "pass": ok})
        if budget.get("total_g"):
            ttol = budget.get("total_tolerance_fraction", 0.03)
            check("total mass within budget tolerance", abs(total - budget["total_g"]) <= ttol * budget["total_g"],
                  {"cad_total_g": round(total, 2), "budget_total_g": budget["total_g"], "tolerance_fraction": ttol})
        for br in budget_rows:
            check("budget item: " + br["item"], br["pass"], {k: br[k] for k in ("budget_g", "cad_g", "delta_fraction")})

        # exports
        os.makedirs(out, exist_ok=True)
        files = {}
        exp = spec.get("export", {})
        if exp.get("step", True) or exp.get("fcstd", True):
            doc = FreeCAD.newDocument("airframe")
            objs = []
            for p in parts:
                o = doc.addObject("Part::Feature", "P_" + slug(p["name"]).replace("-", "_"))
                o.Label = p["name"]
                o.Shape = p["shape"]
                objs.append(o)
            doc.recompute()
        if exp.get("step", True):
            os.makedirs(os.path.join(out, "step"), exist_ok=True)
            import Import
            asm = os.path.join(out, spec["id"] + "-assembly.step")
            Import.export(objs, asm)
            files["assembly_step"] = asm
            for p in parts:
                path = os.path.join(out, "step", slug(p["name"]) + ".step")
                p["shape"].exportStep(path)
                files["step:" + p["name"]] = path
        if exp.get("fcstd", True):
            path = os.path.join(out, spec["id"] + ".FCStd")
            doc.saveAs(path)
            files["fcstd"] = path
        if exp.get("step", True) or exp.get("fcstd", True):
            FreeCAD.closeDocument(doc.Name)
        area_of = {r["name"]: r.get("area_mm2", 0.0) for r in rows}
        export_log = []
        if exp.get("stl", True):
            os.makedirs(os.path.join(out, "stl"), exist_ok=True)
            for p in parts:
                mesh, used, ok = export_mesh(p["shape"], exp.get("stl_linear_deflection_mm", 0.2), area_of[p["name"]])
                export_log.append({"part": p["name"], "format": "stl", "deflection_mm": used, "complete": ok, "triangles": mesh.CountFacets})
                check("export mesh complete (STL): " + p["name"], ok, None if ok else {"area_mm2": round(mesh.Area, 1), "expected_mm2": area_of[p["name"]]})
                path = os.path.join(out, "stl", slug(p["name"]) + ".stl")
                mesh.write(path)
                # F11: STL has no units; record them next to the file (read by cad_inspect_stl_geometry).
                with open(path + ".meta.json", "w") as handle:
                    json.dump({"units": "mm", "format": "unified-cad-mcp/stl-meta@1", "part": p["name"],
                               "generator": "unified-cad-mcp airframe worker (FreeCAD MeshPart)",
                               "deflection_mm": used, "triangles": mesh.CountFacets}, handle, indent=1, sort_keys=True)
                files["stl:" + p["name"]] = path
        viewer = []
        defl = exp.get("viewer_linear_deflection_mm", 1.0)
        for p in parts:
            mesh, used, ok = export_mesh(p["shape"], defl, area_of[p["name"]])
            export_log.append({"part": p["name"], "format": "viewer", "deflection_mm": used, "complete": ok, "triangles": mesh.CountFacets})
            check("export mesh complete (viewer): " + p["name"], ok, None if ok else {"area_mm2": round(mesh.Area, 1), "expected_mm2": area_of[p["name"]]})
            pts, faces = mesh.Topology
            verts = [[round(v.x, 2), round(v.y, 2), round(v.z, 2)] for v in pts]
            tris = [list(f) for f in faces]
            row = next(r for r in rows if r["name"] == p["name"])
            viewer.append({"name": p["name"], "color": p["color"], "group": p["group"], "explode": p["explode"],
                           "note": p["note"] + (" · CAD mass %.1f g, CG (%.0f, %.0f, %.0f) mm" % (row["mass_g"], *row["cg_mm"]) if row["mass_g"] else " · zero-mass keep-out envelope"),
                           "vertices": verts, "triangles": tris})
        vpath = os.path.join(out, spec["id"] + ".viewer.json")
        with open(vpath, "w", encoding="utf-8") as fh:
            json.dump(viewer, fh, ensure_ascii=False, separators=(",", ":"))
            fh.write("\n")
        files["viewer_json"] = vpath

        fus_row = next(r for r in rows if r["name"] == fus_name)
        try:
            fbb = fuselage.optimalBoundingBox(False, False)
        except Exception:
            fbb = fuselage.BoundBox
        exposed = {r["base"]: 0.0 for r in rows}
        for r in rows:
            exposed[r["base"]] += r.get("area_mm2", 0.0)
        report = {
            "id": spec["id"], "freecad_version": ".".join(FreeCAD.Version()[:3]),
            "frame": spec.get("frame", "x aft from nose datum, y to port, z up; mm"),
            "bbox_method": "tight (Shape.optimalBoundingBox); OCCT's default BoundBox includes BSpline poles and was up to 365 mm loose on the trimmed wing",
            "part_count": len(parts), "parts": rows,
            "totals": {"mass_g": round(total, 3), "cg_mm": [round(v, 3) for v in cg],
                       "inertia_about_cg_kg_m2": [[round(v * 1e-9, 9) for v in r] for r in inertia]},
            "aero_inputs_from_cad": {
                "fuselage_wetted_area_mm2": round(fus_row["area_mm2"], 1), "fuselage_volume_mm3": round(fus_row["volume_mm3"], 1),
                "fuselage_length_mm": round(fbb.XLength, 3), "fuselage_max_width_mm": round(fbb.YLength, 3),
                "fuselage_max_height_mm": round(fbb.ZLength, 3),
                "surface_area_by_part_family_mm2": {k: round(v, 1) for k, v in exposed.items()},
            },
            "pockets": pocket_log, "bop_advisories": bop_advisories, "export_meshes": export_log,
            "budget": budget_rows, "checks": checks, "check_count": len(checks),
            "mass_property_method": "exact polyhedral integrals over a closed tessellation (chordal deflection %.3f mm); OCCT GProp reported per part as kernel_gprop_volume_mm3 for comparison" % MESH_DEFLECTION, "all_checks_pass": all(c["pass"] for c in checks),
            "files": {k: {"path": os.path.relpath(v, out), "sha256": sha256(v), "bytes": os.path.getsize(v)} for k, v in files.items()},
            "notes": [
                "STEP files embed export timestamps, so their bytes (and SHA-256) change between identical builds; compare mass properties instead.",
                "Fuselage mass uses outer-mould-line area x laminate areal density; no inner shell solid is modelled.",
                "Control surfaces are split at the hinge line with zero gap; hinges, horns and linkages are not modelled.",
            ],
        }
        with open(os.path.join(out, "report.json"), "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=1, ensure_ascii=False)
        return report

    # ------------------------------------------------------------ inspect STEP
    def inspect_step(req):
        shape = Part.read(req["path"])
        density = req.get("density_g_cm3")
        solids = []
        total_v = 0.0
        moment = np.zeros(3)
        for i, s in enumerate(shape.Solids):
            vol, first, second, info = solid_integrals(s)
            gvol = s.Volume
            com = first / vol if vol else np.zeros(3)
            row = {"index": i, "valid": s.isValid(), "closed": all(sh.isClosed() for sh in s.Shells) and info["closed"],
                   "volume_mm3": round(vol, 3), "kernel_gprop_volume_mm3": round(gvol, 3),
                   "gprop_volume_error_fraction": round((gvol - vol) / vol, 5) if vol else None,
                   "area_mm2": round(surface_integrals(s)[0], 3),
                   "centroid_mm": [round(float(v), 4) for v in com], "bbox_mm": tight_bbox(s)}
            if density:
                item = to_item(vol * density / 1000, vol, first, second)
                row["mass_g"] = round(item[0], 4)
                row["inertia_about_centroid_kg_m2"] = [[round(v * 1e-9, 10) for v in r] for r in item[2]]
            solids.append(row)
            total_v += vol
            moment += first
        com = (moment / total_v).tolist() if total_v else [None, None, None]
        return {"path": req["path"], "sha256": sha256(req["path"]), "solid_count": len(shape.Solids),
                "shell_count": len(shape.Shells), "face_count": len(shape.Faces), "all_valid": shape.isValid() and all(r["valid"] for r in solids),
                "all_closed": all(r["closed"] for r in solids), "total_volume_mm3": round(total_v, 3),
                "volume_centroid_mm": [None if v is None else round(v, 4) for v in com],
                "bbox_mm": tight_bbox(shape), "method": "mesh-integrated volumes/centroids (deflection %.3f mm) with OCCT GProp for comparison; tight bbox" % MESH_DEFLECTION,
                "units": "mm (STEP length unit converted by OCCT)", "solids": solids[:200]}

    # ------------------------------------------------------------ render
    def render(req):
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from mpl_toolkits.mplot3d.art3d import Poly3DCollection
        import numpy as np

        with open(req["viewer_json"], encoding="utf-8") as fh:
            parts = json.load(fh)
        hidden = set(req.get("hidden_parts", []))
        explode = float(req.get("explode", 0.0))
        views = {"iso": (24, -128), "top": (90, -90), "side": (0, -90), "front": (0, 180), "rear": (8, 0)}
        light = np.array([0.35, -0.45, 0.82])
        light /= np.linalg.norm(light)
        polys, colors = [], []
        allpts = []
        for p in parts:
            if p["name"] in hidden:
                continue
            v = np.asarray(p["vertices"], dtype=float) + explode * np.asarray(p.get("explode", [0, 0, 0]), dtype=float)
            t = np.asarray(p["triangles"], dtype=int)
            if not len(t):
                continue
            tri = v[t]
            n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
            norm = np.linalg.norm(n, axis=1)
            n = n / np.where(norm == 0, 1, norm)[:, None]
            shade = 0.34 + 0.56 * np.abs(n @ light)  # never pure white: light parts must contrast with the background
            base = np.array(matplotlib.colors.to_rgb(p.get("color", "#bbbbbb")))
            polys.append(tri)
            colors.append(np.clip(base[None, :] * shade[:, None], 0, 1))
            allpts.append(v)
        tris = np.concatenate(polys)
        cols = np.concatenate(colors)
        pts = np.concatenate(allpts)
        lo, hi = pts.min(axis=0), pts.max(axis=0)
        outputs = []
        os.makedirs(req["output_dir"], exist_ok=True)
        for view in req.get("views", ["iso", "top", "side", "front"]):
            elev, azim = views[view]
            bg = req.get("background", "#cfd8df")
            fig = plt.figure(figsize=(req.get("width_px", 1600) / 100, req.get("height_px", 1000) / 100), dpi=100, facecolor=bg)
            ax = fig.add_subplot(111, projection="3d", facecolor=bg)
            ax.add_collection3d(Poly3DCollection(tris, facecolors=cols, edgecolors=cols * 0.8, linewidths=0.15))
            for mk in req.get("markers", []):
                x, y, z = mk["position_mm"]
                ax.scatter([x], [y], [z + (hi[2] - lo[2]) * 0.02], s=70, c=mk.get("color", "#d62728"), depthshade=False, zorder=10)
                ax.text(x, y, z + (hi[2] - lo[2]) * 0.06, mk["label"], fontsize=9, color=mk.get("color", "#d62728"), zorder=11)
            ax.set_xlim(lo[0], hi[0]); ax.set_ylim(lo[1], hi[1]); ax.set_zlim(lo[2], hi[2])
            ax.set_box_aspect(tuple(np.maximum(hi - lo, 1e-6)))
            ax.set_proj_type("ortho")
            ax.view_init(elev=elev, azim=azim)
            ax.set_axis_off()
            ax.set_title("%s - %s view (mm)" % (req.get("title", ""), view), fontsize=11)
            fig.subplots_adjust(0, 0, 1, 0.95)
            path = os.path.join(req["output_dir"], "%s-%s.png" % (req.get("prefix", "preview"), view))
            fig.savefig(path, facecolor=bg)
            plt.close(fig)
            outputs.append({"view": view, "path": path, "sha256": sha256(path), "bytes": os.path.getsize(path)})
        return {"renderer": "matplotlib %s Agg (painter's algorithm; occlusion approximate)" % matplotlib.__version__,
                "triangles": int(len(tris)), "images": outputs, "bounds_mm": [lo.round(3).tolist(), hi.round(3).tolist()]}

    raw = sys.stdin.read()
    try:
        req = json.loads(raw)
        op = req.get("op")
        if op == "build":
            if req.get("replace") and os.path.isdir(req["output_dir"]):
                shutil.rmtree(req["output_dir"])
            result = build(req)
        elif op == "inspect_step":
            result = inspect_step(req)
        elif op == "render":
            result = render(req)
        else:
            raise ValueError("unknown op %r" % op)
        out = {"ok": True, "result": result}
    except Exception as error:
        out = {"ok": False, "error_type": type(error).__name__, "error": str(error)[:2000],
               "traceback": traceback.format_exc()[-4000:]}
    sys.stdout.write("\n" + json.dumps(out) + "\n")
    sys.stdout.flush()


_main()
