"""Tests for miniature_mesh_budget and miniature_shell_audit (miniature_mesh.py)."""
from unified_cad_mcp.miniature_mesh import mesh_budget, shell_audit


def box(x0, s, flip=False):
    v = [(x0 + x, y, z) for z in (0, s) for y in (0, s) for x in (0, s)]
    f = [(0, 2, 1), (1, 2, 3), (4, 5, 6), (5, 7, 6), (0, 1, 4), (1, 5, 4), (2, 6, 3), (3, 6, 7), (0, 4, 2), (2, 4, 6), (1, 3, 5), (3, 7, 5)]
    tris = [tuple(v[i] for i in t) for t in f]
    return [(a, c, b) for a, b, c in tris] if flip else tris


def test_budget_scales_pitch_with_sqrt_of_triangle_ratio():
    r = mesh_budget(1_000_000, 0.1, 250_000)
    assert r["recommended_pitch_mm"] == 0.2
    assert r["predicted_triangles"] <= 250_000 and r["under_checker_limit"]


def test_budget_warns_when_feature_falls_under_three_voxels():
    r = mesh_budget(1_600_000, 0.1, 250_000, min_feature_mm=0.6)
    assert r["voxels_across_min_feature"] < 3 and r["warnings"]


def test_budget_keeps_pitch_when_already_under():
    r = mesh_budget(100_000, 0.15)
    assert r["recommended_pitch_mm"] == 0.15


def test_shell_audit_flags_speck_and_inside_out_shells():
    tris = box(0, 10) + box(20, 0.2) + box(40, 2, flip=True)
    r = shell_audit(tris)
    assert r["shell_count"] == 3 and r["speck_shells"] == 1 and r["inside_out_shells"] == 1 and not r["clean"]
    assert shell_audit(box(0, 10))["clean"]
