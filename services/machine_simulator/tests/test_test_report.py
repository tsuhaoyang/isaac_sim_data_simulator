from pathlib import Path

from isaac_common.test_report import parse_report, report_stats

DATA = Path(__file__).resolve().parents[1] / "data"


def test_parse_err_report_counts_and_fails():
    items = parse_report(DATA / "demo.md.err.txt")
    assert len(items) == 265                      # 117 device + 148 net
    fails = [it for it in items if it.result == "FAIL"]
    assert len(fails) == 3
    assert report_stats(DATA / "demo.md.err.txt") == (265, 3)


def test_fail_path_parsed_after_colon_by_arrow():
    items = parse_report(DATA / "demo.md.err.txt")
    f = next(it for it in items if it.item == "BMC_DP_AUX_DN")
    assert f.result == "FAIL" and f.fault == "Power"
    assert f.path[0] == "Riser Card.J1004.B32"
    assert f.path[-1] == "J1_Riser.PCIE1.A78"
    assert len(f.path) == 12
    # 一個 GND 案例 + 較長路徑
    g = next(it for it in items if it.item == "I2C_3V3_5_SCL")
    assert g.fault == "GND" and len(g.path) == 18


def test_pass_copy_has_no_fails():
    assert report_stats(DATA / "demo.md.pass.txt") == (265, 0)
