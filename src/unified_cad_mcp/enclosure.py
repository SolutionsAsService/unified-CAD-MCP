"""Internals packaging for handheld / printed enclosures built around off-the-shelf modules.

Tools (namespace "enclosure", mounted with drone_tools):
- module_catalog: dimension records for off-the-shelf modules with their source and confidence.
- fit_check: axis-aligned packaging check of modules inside an enclosure cavity — per-side clearance, pairwise
  separation, keep-outs (e.g. no metal over an NFC antenna), battery swelling allowance and print rules.
Boxes only: it does not replace a CAD interference check on the real solids, and it never certifies fit
from catalog dimensions; every module carries the confidence of its source.
"""
from __future__ import annotations

from typing import Annotated, Literal

from fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

Finite = Annotated[float, Field(allow_inf_nan=False)]
Pos = Annotated[float, Field(gt=0, allow_inf_nan=False)]
Vec3 = Annotated[list[Finite], Field(min_length=3, max_length=3)]
Size3 = Annotated[list[Pos], Field(min_length=3, max_length=3)]
READ_ONLY = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}

# Published envelopes (mm). Where sources conflict the larger one is kept and the conflict is recorded.
CATALOG = {
    "waveshare-esp32-s3-touch-lcd-3.5": {"size_mm": [63.67, 95.11, 14.10], "kind": "host",
        "source": "Waveshare assembled-dimension graphic (larger than the 61 x 92.44 x 11.5 fit box in the same wiki)",
        "confidence": "vendor drawing, conflicting sources; measure the purchased board", "mass_g": 45},
    "m5stack-unit-nfc-u216": {"size_mm": [48.0, 24.0, 8.0], "kind": "nfc",
        "source": "M5Stack U216 drawing (vendor STL parses to 88.5 x 48 x 8 and is not used)",
        "confidence": "vendor drawing; antenna face and hole axis to verify", "mass_g": 9,
        "keepout": {"no_metal_mm": 5, "face": "antenna side"}},
    "pkcell-lp503035": {"size_mm": [30.5, 37.5, 5.3], "kind": "battery",
        "source": "PKCELL LP503035 datasheet maximum body (1S 500 mAh, protected, JST-PHR-2P leads 50 mm)",
        "confidence": "datasheet maximum", "mass_g": 10.5, "swelling_fraction": 0.08},
    "usb-c-panel-extension-short": {"size_mm": [12.0, 30.0, 7.5], "kind": "connector",
        "source": "generic USB-C male-to-female panel-mount extension, flange body (catalog class, not a selected MPN)",
        "confidence": "class envelope; select a part", "mass_g": 6},
    "waveshare-2.4in-lcd-module": {"size_mm": [70.5, 43.3, 7.5], "kind": "host",
        "source": "Waveshare 2.4inch LCD Module (ILI9341, SPI, 240x320) wiki outline 70.50 x 43.30 mm; thickness incl. header estimated",
        "confidence": "vendor outline; thickness, holes and active-area offset to measure", "mass_g": 18,
        "active_area_mm": [48.96, 36.72]},
    "seeed-xiao-esp32s3": {"size_mm": [21.0, 17.8, 3.5], "kind": "host",
        "source": "Seeed Studio XIAO ESP32S3 wiki: 21 x 17.8 mm, USB-C on the short edge (height incl. USB-C ~3.5 mm)",
        "confidence": "vendor dimensions", "mass_g": 3},
    "tactile-switch-6x6": {"size_mm": [6.0, 6.0, 5.0], "kind": "control",
        "source": "6 x 6 mm through-hole tactile switch, 5 mm overall height, ~0.25 mm travel (common C&K PTS645-class)",
        "confidence": "class envelope; travel per selected part", "mass_g": 0.3, "travel_mm": 0.25},
    "slide-switch-ss12d00": {"size_mm": [8.6, 3.6, 3.5], "kind": "control",
        "source": "SS12D00 SPDT slide switch body 8.6 x 3.6 x 3.5 mm, actuator 1.5 mm proud, 2 mm throw",
        "confidence": "class envelope", "mass_g": 0.5},
    "tactile-switch-12mm-module": {"size_mm": [12.0, 12.0, 7.3], "kind": "control",
        "source": "12 x 12 mm tactile switch (body + cap) on a small breakout",
        "confidence": "class envelope", "mass_g": 2},
}


class Module(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, Field(min_length=1, max_length=80)]
    kind: Literal["host", "nfc", "battery", "connector", "control", "secure_element", "other"] = "other"
    min_mm: Vec3
    size_mm: Size3
    clearance_mm: Annotated[float, Field(ge=0, le=10, allow_inf_nan=False)] = 0.4
    swelling_fraction: Annotated[float, Field(ge=0, le=0.5, allow_inf_nan=False)] = 0.0
    swelling_axis: Literal[0, 1, 2] = 2
    may_breach_cavity: bool = Field(default=False, description="Parts meant to pass through the wall (connectors, buttons)")


class Keepout(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, Field(min_length=1, max_length=80)]
    min_mm: Vec3
    size_mm: Size3
    forbid_kinds: list[str] = Field(default_factory=lambda: ["metal"])


class Metal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, Field(min_length=1, max_length=80)]
    min_mm: Vec3
    size_mm: Size3


class FitInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cavity_min_mm: Vec3
    cavity_size_mm: Size3
    modules: Annotated[list[Module], Field(min_length=1, max_length=40)]
    keepouts: list[Keepout] = Field(default_factory=list, max_length=20)
    metal: list[Metal] = Field(default_factory=list, max_length=60, description="Screws, inserts, magnets, shields")
    min_wall_mm: Pos = 1.6
    wall_mm: Pos = 2.0


def _box(m):
    return [m.min_mm[i] for i in range(3)], [m.min_mm[i] + m.size_mm[i] for i in range(3)]


def _gap(a, b):
    """Signed separation of two boxes: > 0 = gap along the separating axis, < 0 = overlap depth (smallest axis)."""
    (a0, a1), (b0, b1) = a, b
    seps = [max(b0[i] - a1[i], a0[i] - b1[i]) for i in range(3)]
    return max(seps)


def fit_check(data: FitInput) -> dict:
    checks, rows = [], []
    def check(name, value, ok, limit, basis):
        checks.append({"name": name, "value": round(value, 3), "pass": bool(ok), "limit": limit, "basis": basis})

    c0 = data.cavity_min_mm
    c1 = [c0[i] + data.cavity_size_mm[i] for i in range(3)]
    check("wall thickness", data.wall_mm, data.wall_mm >= data.min_wall_mm, data.min_wall_mm,
          "FDM walls below ~1.6 mm (four 0.4 mm perimeters) are fragile at screw bosses and seams.")
    boxes = {}
    for m in data.modules:
        lo, hi = _box(m)
        if m.swelling_fraction:
            hi[m.swelling_axis] += m.size_mm[m.swelling_axis] * m.swelling_fraction
        boxes[m.name] = (lo, hi)
        side = min(min(lo[i] - c0[i], c1[i] - hi[i]) for i in range(3))
        rows.append({"name": m.name, "kind": m.kind, "min_side_clearance_mm": round(side, 3),
                     "envelope_mm": [round(hi[i] - lo[i], 3) for i in range(3)]})
        if not m.may_breach_cavity:
            check(f"inside cavity with clearance: {m.name}", side, side >= m.clearance_mm - 1e-6, m.clearance_mm,
                  "Per-side clearance around a rigid module for FDM (0.4–0.5 mm starting point; verify with a stepped coupon).")
        if m.kind == "battery":
            check(f"battery swelling allowance: {m.name}", m.swelling_fraction, m.swelling_fraction >= 0.05, 0.05,
                  "Pouch cells grow in thickness over life; leave ≥ 5–10 % unless the supplier states otherwise, and never compress the pack.")
    names = list(boxes)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = data.modules[i], data.modules[j]
            g = _gap(boxes[a.name], boxes[b.name])
            need = max(a.clearance_mm, b.clearance_mm) if "battery" not in (a.kind, b.kind) else max(1.0, a.clearance_mm, b.clearance_mm)
            check(f"separation: {a.name} / {b.name}", g, g >= need - 1e-6, need,
                  "Separation between module envelopes (battery ≥ 1 mm from boards: insulation and abrasion).")
    for k in data.keepouts:
        kb = _box(k)
        for mt in data.metal:
            g = _gap(kb, _box(mt))
            check(f"keep-out {k.name}: {mt.name}", g, g > 0, 0, "No metal (screws, inserts, magnets) inside an antenna keep-out; it detunes the loop and shields the field.")
    return {"modules": rows, "checks": checks, "all_checks_pass": all(c["pass"] for c in checks),
            "method": "axis-aligned envelopes; complements, not replaces, a solid interference check"}


class ButtonStack(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, Field(min_length=1, max_length=80)]
    carrier_top_z_mm: Finite = Field(description="Top face of the board / carrier the switch sits on")
    switch_height_mm: Pos = Field(description="Switch height from board to plunger top, unpressed")
    switch_travel_mm: Pos
    shell_outer_z_mm: Finite = Field(description="Outer face of the wall the cap passes through")
    wall_mm: Pos
    protrusion_mm: Pos = Field(default=1.5, description="Cap face above the outer surface")
    flange_mm: Pos = Field(default=1.0, description="Retaining flange under the wall")
    preload_gap_mm: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)] = 0.15
    cap_travel_limit_mm: Pos = Field(default=0.6, description="Cap stroke before the flange would hit the board (overtravel stop)")


def button_stack(b: ButtonStack) -> dict:
    """Cap length for a printed button over a tactile switch, and whether the stack works."""
    plunger = b.carrier_top_z_mm + b.switch_height_mm
    face = b.shell_outer_z_mm + b.protrusion_mm
    cap_len = face - (plunger + b.preload_gap_mm)
    flange_bottom = b.shell_outer_z_mm - b.wall_mm - b.flange_mm
    stroke_to_click = b.preload_gap_mm + b.switch_travel_mm
    checks = []
    def check(name, value, ok, limit, basis):
        checks.append({"name": name, "value": round(value, 3), "pass": bool(ok), "limit": limit, "basis": basis})
    check("cap reaches the switch with preload gap", cap_len, cap_len > b.wall_mm + b.flange_mm, f"> wall + flange ({b.wall_mm + b.flange_mm})",
          "The cap must pass through the wall and keep its flange below it.")
    check("flange clears the board at full press", flange_bottom - stroke_to_click - b.carrier_top_z_mm, flange_bottom - stroke_to_click - b.carrier_top_z_mm >= 0.3, 0.3,
          "At full stroke the flange must not land on the board or components.")
    check("click before the cap bottoms", stroke_to_click, stroke_to_click <= b.protrusion_mm and stroke_to_click <= b.cap_travel_limit_mm, min(b.protrusion_mm, b.cap_travel_limit_mm),
          "The switch must click before the cap face is flush with the shell (and within the stroke limit).")
    return {"name": b.name, "cap_length_mm": round(cap_len, 3), "plunger_top_z_mm": round(plunger, 3), "cap_face_z_mm": round(face, 3),
            "flange_bottom_z_mm": round(flange_bottom, 3), "stroke_to_click_mm": round(stroke_to_click, 3), "checks": checks,
            "all_checks_pass": all(c["pass"] for c in checks)}


def create_enclosure_server() -> FastMCP:
    mcp = FastMCP("enclosure-internals")

    @mcp.tool(name="module_catalog", annotations=READ_ONLY)
    def module_catalog_tool() -> dict:
        """Off-the-shelf module envelopes (mm) with source and confidence. Catalog values are planning data; measure parts before cutting tools."""
        return {"modules": CATALOG}

    @mcp.tool(name="fit_check", annotations=READ_ONLY)
    def fit_check_tool(fit: FitInput) -> dict:
        """Packaging check for modules in an enclosure cavity: per-side clearance, pairwise separation, battery swelling
        allowance, antenna keep-outs vs metal parts, and wall thickness. Axis-aligned boxes; run a solid interference check too."""
        return fit_check(fit)

    @mcp.tool(name="button_stack", annotations=READ_ONLY)
    def button_stack_tool(stack: ButtonStack) -> dict:
        """Size a printed button cap over a tactile switch (D-pad, A/B, start/select): cap length from board, switch height,
        wall and protrusion, and check preload, flange clearance at full press and click-before-bottom-out."""
        return button_stack(stack)

    return mcp
