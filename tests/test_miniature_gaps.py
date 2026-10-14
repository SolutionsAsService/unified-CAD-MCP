"""Tests for miniature_gap_check (miniature_gaps.py): narrow air gaps and through-loop counts."""
import asyncio
import math
import struct

from fastmcp import Client

from unified_cad_mcp.miniature import create_miniature_server
from unified_cad_mcp.miniature_gaps import gap_check


def box_tris(x0, sx, sy, sz):
    v = [(x0 + x, y, z) for z in (0, sz) for y in (0, sy) for x in (0, sx)]
    f = [(0, 2, 1), (1, 2, 3), (4, 5, 6), (5, 7, 6), (0, 1, 4), (1, 5, 4), (2, 6, 3), (3, 6, 7), (0, 4, 2), (2, 4, 6), (1, 3, 5), (3, 7, 5)]
    return [tuple(v[i] for i in t) for t in f]


def refine(tris, n=3):
    """Split every triangle into 4, n times, so centroids sample the faces (the checker samples centroids)."""
    for _ in range(n):
        out = []
        for a, b, c in tris:
            ab, bc, ca = (tuple((p + q) / 2 for p, q in zip(u, w)) for u, w in ((a, b), (b, c), (c, a)))
            out += [(a, ab, ca), (ab, b, bc), (ca, bc, c), (ab, bc, ca)]
        tris = out
    return tris


def torus_tris(R=5.0, r=1.5, n=48, m=24):
    pt = [[((R + r * math.cos(v)) * math.cos(u), (R + r * math.cos(v)) * math.sin(u), r * math.sin(v))
           for v in (2 * math.pi * j / m for j in range(m))] for u in (2 * math.pi * i / n for i in range(n))]
    tris = []
    for i in range(n):
        for j in range(m):
            a, b, c, d = pt[i][j], pt[(i + 1) % n][j], pt[(i + 1) % n][(j + 1) % m], pt[i][(j + 1) % m]
            tris += [(a, b, c), (a, c, d)]
    return tris


def test_narrow_slot_between_two_blocks_fails_and_a_wide_one_passes():
    near = refine(box_tris(0, 3, 3, 3) + box_tris(3.3, 3, 3, 3))  # 0.3 mm slot
    r = gap_check(near)
    assert not r["all_checks_pass"] and r["narrow_gap_area_mm2"] > 1.0
    assert abs(r["smallest_gap_mm"] - 0.3) < 1e-6 and r["narrow_spots"]
    far = refine(box_tris(0, 3, 3, 3) + box_tris(5.0, 3, 3, 3))  # 2 mm apart
    assert gap_check(far)["all_checks_pass"]


def test_a_single_solid_has_no_gaps_and_no_handles():
    r = gap_check(refine(box_tris(0, 4, 2, 1)), expected_handles=0)
    assert r["all_checks_pass"] and r["narrow_gap_area_mm2"] == 0 and r["handles_total"] == 0
    assert all(c["pass"] for c in r["checks"])


def test_torus_counts_one_through_loop():
    r = gap_check(torus_tris(), expected_handles=0)
    assert r["handles_total"] == 1 and r["shells"][0]["euler_characteristic"] == 0
    loop = next(c for c in r["checks"] if c["name"].startswith("through-loops"))
    assert not loop["pass"] and loop["severity"] == "warn"
    assert r["all_checks_pass"]  # a loop alone is advisory


def test_gap_check_over_mcp(tmp_path):
    from unified_cad_mcp.config import AirframeConfig, Configuration
    from unified_cad_mcp.server import create_server
    tris = refine(box_tris(0, 3, 3, 3) + box_tris(3.3, 3, 3, 3), 2)
    p = tmp_path / "slot.stl"
    with open(p, "wb") as f:
        f.write(b"slot".ljust(80, b" "))
        f.write(struct.pack("<I", len(tris)))
        for a, b, c in tris:
            f.write(struct.pack("<12fH", 0, 0, 0, *a, *b, *c, 0))
    cfg = Configuration(project_root=str(tmp_path), drone_tools=True, backends={},
                        airframe=AirframeConfig(freecad_python="unused", output_root=str(tmp_path), read_roots=[str(tmp_path)]))

    async def go():
        async with Client(create_server(cfg)) as c:
            names = {t.name for t in await c.list_tools()}
            assert "miniature_gap_check" in names
            return (await c.call_tool("miniature_gap_check", {"stl_path": str(p)})).data
    r = asyncio.run(go())
    assert r["all_checks_pass"] is False and r["min_gap_mm"] == 0.5
