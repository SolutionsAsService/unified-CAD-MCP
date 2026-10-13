from unified_cad_mcp.rf_layout import LayoutInput, antenna_layout_check


def layout(**over):
    base = dict(antennas=[
        dict(name="cell main", role="cellular_main", feed_mm=[-72, 0, 48], axis=[0, 0, 1], length_mm=90, low_mhz=698, high_mhz=2690),
        dict(name="cell div", role="cellular_diversity", feed_mm=[72, 0, 48], axis=[0, 0, 1], length_mm=90, low_mhz=698, high_mhz=2690),
        dict(name="gnss", role="gnss", kind="patch", feed_mm=[0, 10, 42], axis=[0, 0, 1], low_mhz=1559, high_mhz=1610)])
    base.update(over)
    return antenna_layout_check(LayoutInput.model_validate(base))


def test_vertical_spaced_layout_passes():
    r = layout()
    assert r["all_checks_pass"], [c for c in r["checks"] if not c["pass"]]


def test_horizontal_close_whips_fail():
    r = layout(antennas=[
        dict(name="a", role="cellular_main", feed_mm=[30, 40, 28], axis=[0, 1, 0], length_mm=27, low_mhz=698, high_mhz=2690),
        dict(name="b", role="cellular_diversity", feed_mm=[42, 40, 28], axis=[0, 1, 0], length_mm=27, low_mhz=698, high_mhz=2690),
        dict(name="g", role="gnss", kind="patch", feed_mm=[53, 40, 28], axis=[0, 1, 0], low_mhz=1559, high_mhz=1610)])
    failed = {c["name"].split(":")[0] for c in r["checks"] if not c["pass"]}
    assert {"vertical polarization", "element length", "diversity spacing", "GNSS sky view", "GNSS clearance"} <= failed
