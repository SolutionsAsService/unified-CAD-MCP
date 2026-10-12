"""Independent STL shell/cavity analysis and explicit unit resolution (flaw F11).

STL carries no units. Instead of a silent "UNKNOWN", every report says which unit was
used and *why*: an explicit units argument, a sidecar file written by our exporters
(<file>.stl.meta.json with {"units": "mm"}), or the documented default assumption
(millimetres, FreeCAD's internal length unit). Shells are classified so an internal
cavity (e.g. a closed servo pocket) is reported as such instead of only inflating an
edge-connected shell count.
"""
from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any

UNIT_TO_MM = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "um": 0.001, "in": 25.4, "ft": 304.8}
DEFAULT_UNITS = "mm"
SIDECAR_SUFFIX = ".meta.json"
MAX_BYTES = 64 * 1024 * 1024
MAX_NESTING_SHELLS = 64


def sidecar_path(stl: Path) -> Path:
    return stl.with_name(stl.name + SIDECAR_SUFFIX)


def write_sidecar(stl: Path, units: str = DEFAULT_UNITS, **extra: Any) -> Path:
    """Exporter helper: record the units an STL was written in."""
    if units not in UNIT_TO_MM:
        raise ValueError(f"units must be one of {sorted(UNIT_TO_MM)}")
    target = sidecar_path(stl)
    target.write_text(json.dumps({"units": units, "format": "unified-cad-mcp/stl-meta@1", **extra},
                                 indent=1, sort_keys=True), encoding="utf-8")
    return target


def parse_stl(data: bytes) -> list[tuple[tuple[float, float, float], ...]]:
    if len(data) >= 84:
        count = struct.unpack_from("<I", data, 80)[0]
        if 84 + 50 * count == len(data):
            triangles = []
            for index in range(count):
                values = struct.unpack_from("<12f", data, 84 + 50 * index)
                triangles.append((values[3:6], values[6:9], values[9:12]))
            return triangles
    text = data.decode("ascii", errors="strict")
    vertices: list[tuple[float, ...]] = []
    for line in text.splitlines():
        parts = line.split()
        if parts and parts[0] == "vertex" and len(parts) == 4:
            vertices.append(tuple(float(v) for v in parts[1:]))
    if not vertices or len(vertices) % 3:
        raise ValueError("Not a valid binary or ASCII STL")
    return [tuple(vertices[i:i + 3]) for i in range(0, len(vertices), 3)]


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _ray_hits(origin, direction, triangle) -> bool:
    first, second, third = triangle
    edge1, edge2 = _sub(second, first), _sub(third, first)
    pvec = _cross(direction, edge2)
    det = _dot(edge1, pvec)
    if abs(det) < 1e-14:
        return False
    inv = 1.0 / det
    tvec = _sub(origin, first)
    u = _dot(tvec, pvec) * inv
    if u < 0 or u > 1:
        return False
    qvec = _cross(tvec, edge1)
    v = _dot(direction, qvec) * inv
    if v < 0 or u + v > 1:
        return False
    return _dot(edge2, qvec) * inv > 1e-9


def _inside(point, triangles) -> bool:
    # Slightly skewed ray so it does not pass exactly through mesh edges/vertices.
    direction = (1.0, 0.000123457, 0.000234567)
    return sum(_ray_hits(point, direction, t) for t in triangles) % 2 == 1


def analyze_shells(triangles) -> dict[str, Any]:
    index: dict[tuple, int] = {}
    faces = [tuple(index.setdefault(tuple(v), len(index)) for v in t) for t in triangles]
    parent = list(range(len(faces)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    owner: dict[tuple[int, int], int] = {}
    for face_index, face in enumerate(faces):
        for a, b in ((face[0], face[1]), (face[1], face[2]), (face[2], face[0])):
            key = (a, b) if a < b else (b, a)
            other = owner.setdefault(key, face_index)
            if other != face_index:
                ra, rb = root(other), root(face_index)
                if ra != rb:
                    parent[rb] = ra
    groups: dict[int, list[int]] = {}
    for face_index in range(len(faces)):
        groups.setdefault(root(face_index), []).append(face_index)

    shells = []
    for members in groups.values():
        tris = [triangles[i] for i in members]
        signed = sum(_dot(a, _cross(b, c)) for a, b, c in tris) / 6.0
        points = [v for t in tris for v in t]
        lo = [min(p[k] for p in points) for k in range(3)]
        hi = [max(p[k] for p in points) for k in range(3)]
        shells.append({"triangles": tris, "signed_volume": signed, "min": lo, "max": hi, "probe": tris[0][0]})
    shells.sort(key=lambda s: -abs(s["signed_volume"]))

    def contains_box(outer, inner):
        return all(outer["min"][k] <= inner["min"][k] and inner["max"][k] <= outer["max"][k] for k in range(3))

    nesting_checked = len(shells) <= MAX_NESTING_SHELLS
    report = []
    for i, shell in enumerate(shells):
        depth, container = 0, None
        if nesting_checked:
            for j, other in enumerate(shells):
                if j != i and abs(other["signed_volume"]) > abs(shell["signed_volume"]) and contains_box(other, shell) \
                        and _inside(shell["probe"], other["triangles"]):
                    depth += 1
                    if container is None or abs(shells[container]["signed_volume"]) > abs(other["signed_volume"]):
                        container = j
        if not nesting_checked:
            role = "unclassified_too_many_shells"
        elif depth % 2 == 1:
            role = "internal_cavity" if shell["signed_volume"] < 0 else "internal_cavity_inverted_normals"
        elif depth > 0:
            role = "nested_body_inside_cavity"
        else:
            role = "outer_shell" if shell["signed_volume"] > 0 else "outer_shell_inverted_normals"
        report.append({"index": i, "role": role, "triangle_count": len(shell["triangles"]),
                       "signed_volume_model_units3": shell["signed_volume"],
                       "bounding_box_model_units": {"minimum": shell["min"], "maximum": shell["max"]},
                       "contained_in_shell": container})
    return {
        "shell_count": len(report),
        "outer_shell_count": sum(s["role"].startswith("outer_shell") for s in report),
        "internal_cavity_count": sum(s["role"].startswith("internal_cavity") for s in report),
        "nested_body_count": sum(s["role"] == "nested_body_inside_cavity" for s in report),
        "inverted_shell_count": sum(s["role"].endswith("inverted_normals") for s in report),
        "net_signed_volume_model_units3": sum(s["signed_volume_model_units3"] for s in report),
        "nesting_checked": nesting_checked,
        "shells": report[:MAX_NESTING_SHELLS],
        "note": ("Shells are edge-connected triangle groups. An internal cavity is a shell enclosed by "
                 "another (ray-parity test); its negative signed volume is already subtracted in the net "
                 "volume. Closed internal pockets usually come from a boolean cut that did not break the skin."),
    }


def resolve_units(stl: Path | None, argument: str | None) -> dict[str, Any]:
    sidecar_units = sidecar_error = None
    if stl is not None:
        side = sidecar_path(stl)
        if side.is_file():
            try:
                value = json.loads(side.read_text(encoding="utf-8")).get("units")
                if value in UNIT_TO_MM:
                    sidecar_units = value
                else:
                    sidecar_error = f"sidecar units {value!r} not in {sorted(UNIT_TO_MM)}; ignored"
            except (OSError, ValueError, AttributeError) as error:
                sidecar_error = f"unreadable sidecar ignored: {type(error).__name__}"
    warnings = []
    if argument is not None:
        if argument not in UNIT_TO_MM:
            raise ValueError(f"units must be one of {sorted(UNIT_TO_MM)}")
        units, source = argument, "argument"
        if sidecar_units and sidecar_units != argument:
            warnings.append(f"argument units {argument!r} override sidecar units {sidecar_units!r}")
    elif sidecar_units:
        units, source = sidecar_units, f"sidecar:{sidecar_path(stl).name}"
    else:
        units, source = DEFAULT_UNITS, "default_assumption"
        warnings.append("STL encodes no units and no units argument/sidecar was found; ASSUMED millimetres "
                        "(unified-CAD-MCP default). Pass units=... or add <file>.stl.meta.json if wrong.")
    if sidecar_error:
        warnings.append(sidecar_error)
    return {"units": units, "source": source, "assumed": source == "default_assumption",
            "to_mm": UNIT_TO_MM[units], "warnings": warnings}


def scaled_metrics(upstream: dict[str, Any], to_mm: float) -> dict[str, Any]:
    out: dict[str, Any] = {}
    box = upstream.get("bounding_box_model_units")
    if isinstance(box, dict):
        out["bounding_box_mm"] = {k: [v * to_mm for v in box[k]] for k in ("minimum", "maximum", "extent") if k in box}
    if isinstance(upstream.get("surface_area_model_units2"), (int, float)):
        out["surface_area_mm2"] = upstream["surface_area_model_units2"] * to_mm ** 2
    if isinstance(upstream.get("enclosed_volume_model_units3"), (int, float)):
        out["enclosed_volume_mm3"] = upstream["enclosed_volume_model_units3"] * to_mm ** 3
    if isinstance(upstream.get("volume_centroid_model_units"), list):
        out["volume_centroid_mm"] = [v * to_mm for v in upstream["volume_centroid_model_units"]]
    return out


def stl_report(path: Path, units: str | None) -> dict[str, Any]:
    """Units + shell report for a file the caller already confined to an allowed root."""
    resolution = resolve_units(path, units)
    result: dict[str, Any] = {"units": resolution}
    try:
        if path.stat().st_size > MAX_BYTES:
            result["shell_analysis"] = {"status": "skipped_size_limit"}
            return result
        shells = analyze_shells(parse_stl(path.read_bytes()))
        shells["net_signed_volume_mm3"] = shells["net_signed_volume_model_units3"] * resolution["to_mm"] ** 3
        result["shell_analysis"] = {"status": "ok", **shells}
    except (OSError, ValueError, UnicodeDecodeError, struct.error) as error:
        result["shell_analysis"] = {"status": "failed", "error": type(error).__name__}
    return result
