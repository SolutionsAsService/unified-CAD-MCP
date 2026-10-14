"""Tests for miniature_base_layout_check (miniature_scene.py)."""
import asyncio

from fastmcp import Client

from unified_cad_mcp.miniature import create_miniature_server
from unified_cad_mcp.miniature_scene import base_layout_check

FIG = {"name": "figure", "xy": [-0.5, 3.2], "r": [5.9, 4.9], "socket": {"xy": [-0.5, 2.7], "d": 2.6, "depth": 3.2}, "touch_ok": ["anvil"]}
ANV = {"name": "anvil", "xy": [0.5, -6.8], "r": [3.3, 3.3], "socket": {"xy": [0.5, -6.8], "d": 2.6, "depth": 3.2}}


def test_blacksmith_layout_passes():
    r = base_layout_check(32, 3.6, [FIG, ANV], {"xy": [-6.5, -1.0], "d": 5.2, "h": 2.1}, 0.8)
    assert r["all_checks_pass"], [c for c in r["checks"] if not c["pass"]]


def test_socket_over_magnet_and_overlap_fail():
    bad_anvil = {**ANV, "xy": [0.5, -1.0], "socket": {"xy": [0.5, -1.0], "d": 2.6, "depth": 3.2}}
    r = base_layout_check(32, 3.6, [{**FIG, "touch_ok": []}, bad_anvil], {"xy": [0.0, -1.0], "d": 5.2, "h": 2.1})
    failed = {c["name"] for c in r["checks"] if not c["pass"]}
    assert "anvil: wall socket to magnet pocket" in failed and "gap figure / anvil" in failed


def test_footprint_off_the_base_fails():
    r = base_layout_check(25, 3.2, [{"name": "big", "xy": [6, 0], "r": [8, 8]}])
    assert not r["all_checks_pass"]


def test_mcp_surface():
    async def go():
        async with Client(create_miniature_server()) as c:
            return (await c.call_tool("base_layout_check", {"base_d_mm": 32, "base_height_mm": 3.6, "parts": [FIG, ANV],
                                                            "magnet": {"xy": [-6.5, -1.0], "d": 5.2, "h": 2.1}, "top_inset_mm": 0.8})).data
    assert asyncio.run(go())["all_checks_pass"]
