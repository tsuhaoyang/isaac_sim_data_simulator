from pathlib import Path

from isaac_common.test_report import parse_report, path_to_tree, report_stats

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


def test_path_tree_splits_board_at_first_dot():
    t = path_to_tree(["SPB_PCIE_Riser2.J1.A13", "SPB_PCIE_Riser2.R5.1", "BMC_Interposer.J7.A33"])
    assert t == [
        {"board": "SPB_PCIE_Riser2", "nodes": ["J1.A13", "R5.1"]},   # 同板連續 -> 一段
        {"board": "BMC_Interposer", "nodes": ["J7.A33"]},
    ]


def test_path_tree_keeps_revisited_board_as_separate_segments():
    # GND 那條會「走過去又繞回 Riser Card」——必須是頭尾兩段獨立，不可合併
    g = next(it for it in parse_report(DATA / "demo.md.err.txt") if it.item == "I2C_3V3_5_SCL")
    assert [s["board"] for s in g.path_tree] == [
        "Riser Card", "SPB_PCIE_Riser", "BMC_Interposer", "SPB_PCIE_Riser2", "Riser Card",
    ]
    assert g.path_tree[0]["nodes"] == ["J1003.A55", "J21.A17"]
    assert g.path_tree[-1]["nodes"] == ["J22.B17", "J1004.B66"]
    assert sum(len(s["nodes"]) for s in g.path_tree) == len(g.path) == 18   # 節點數守恆
