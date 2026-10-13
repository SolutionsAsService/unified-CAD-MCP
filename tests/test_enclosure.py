from unified_cad_mcp.enclosure import CATALOG, ButtonStack, FitInput, button_stack, fit_check


def base(**over):
    d = dict(cavity_min_mm=[0, 0, 0], cavity_size_mm=[66, 100, 26],
             modules=[dict(name="host", kind="host", min_mm=[1, 2, 10], size_mm=CATALOG["waveshare-esp32-s3-touch-lcd-3.5"]["size_mm"]),
                      dict(name="nfc", kind="nfc", min_mm=[9, 60, 0.6], size_mm=[48, 24, 8]),
                      dict(name="cell", kind="battery", min_mm=[17, 10, 0.6], size_mm=[30.5, 37.5, 5.3], swelling_fraction=0.08)],
             keepouts=[dict(name="nfc antenna", min_mm=[9, 60, -3], size_mm=[48, 24, 3])],
             metal=[dict(name="screw", min_mm=[2, 2, -3], size_mm=[3, 3, 6])])
    d.update(over)
    return fit_check(FitInput.model_validate(d))


def test_good_layout_passes():
    r = base()
    assert r["all_checks_pass"], [c for c in r["checks"] if not c["pass"]]


def test_overlap_tight_side_and_metal_in_keepout_fail():
    r = base(metal=[dict(name="insert over loop", min_mm=[30, 70, -2], size_mm=[4, 4, 4])],
             modules=[dict(name="host", kind="host", min_mm=[0.1, 2, 6], size_mm=[63.67, 95.11, 14.1]),
                      dict(name="nfc", kind="nfc", min_mm=[9, 60, 0.6], size_mm=[48, 24, 8])])
    failed = {c["name"].split(":")[0] for c in r["checks"] if not c["pass"]}
    assert {"inside cavity with clearance", "separation", "keep-out nfc antenna"} <= failed


def test_button_stack_sizes_cap_and_catches_bad_stack():
    ok = button_stack(ButtonStack(name="A", carrier_top_z_mm=17.6, switch_height_mm=5.0, switch_travel_mm=0.25, shell_outer_z_mm=26, wall_mm=1.8))
    assert ok["all_checks_pass"], ok["checks"]
    assert ok["cap_length_mm"] == 26 + 1.5 - (17.6 + 5.0 + 0.15)
    bad = button_stack(ButtonStack(name="low", carrier_top_z_mm=22.5, switch_height_mm=2.5, switch_travel_mm=0.25, shell_outer_z_mm=26, wall_mm=1.8))
    assert not bad["all_checks_pass"]


def test_catalog_has_game_boy_wallet_parts():
    for k in ("waveshare-2.4in-lcd-module", "seeed-xiao-esp32s3", "tactile-switch-6x6", "slide-switch-ss12d00"):
        assert k in CATALOG and CATALOG[k]["source"]
