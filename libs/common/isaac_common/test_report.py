"""Parse a device/net test report (.md table) into an ordered list of test items.

Used by machine_simulator (stream one item at a time) and capacity (count items /
fails to derive the machine's working time). Device Test Report rows come first,
then Net Test Report rows.

FAIL path parsing (per spec): take the substring after the LAST ':' in TestData,
split by '->' into a list of nodes; the fault hint (Power/GND) comes from
"...with <X> Test Path".
"""

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TestItem:
    __test__ = False   # 別讓 pytest 誤收集這個 dataclass
    board: str
    item: str                 # device (J1/R1…) or net name
    result: str               # "PASS" | "FAIL"
    fault: str | None = None  # "Power" | "GND" | None (FAIL only)
    path: list[str] = field(default_factory=list)       # 扁平路徑（PASS 為空）
    path_tree: list[dict] = field(default_factory=list)  # 階層路徑 [{board, nodes}]（見 path_to_tree）


def path_to_tree(path: list[str]) -> list[dict]:
    """把扁平 path 依序壓成 segment：以 '.' 的第一段（board）為父階層，其餘（component.pin）掛在底下。

    節點格式為 `board.component.pin`，例：`SPB_PCIE_Riser2.J1.A13`
        -> {"board": "SPB_PCIE_Riser2", "nodes": ["J1.A13", …]}

    同一 board **連續**的節點歸一段；board 重複經過（非連續，如走過去又繞回來）會成為
    **獨立的一段**，順序完整保留——不可用 dict grouping，否則往返語意會被合併掉。
    """
    segs: list[dict] = []
    for node in path:
        board, _, rest = node.partition(".")
        rest = rest or node
        if segs and segs[-1]["board"] == board:
            segs[-1]["nodes"].append(rest)
        else:
            segs.append({"board": board, "nodes": [rest]})
    return segs


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _parse_fail(testdata: str) -> tuple[str | None, list[str]]:
    tail = testdata.split(":")[-1]                       # 最後一個冒號之後 = 純路徑
    nodes = [n.strip() for n in tail.split("->") if n.strip()]
    m = re.search(r"with\s+(\w+)\s+Test\s+Path", testdata)   # Power / GND
    return (m.group(1) if m else None), nodes


def parse_report(path: str | Path) -> list[TestItem]:
    items: list[TestItem] = []
    section = None
    for ln in Path(path).read_text(encoding="utf-8").splitlines():
        s = ln.strip()
        if s.startswith("### Device Test Report"):
            section = "device"; continue
        if s.startswith("## Net Test Report"):
            section = "net"; continue
        if not s.startswith("|"):
            continue
        c = _cells(ln)
        if not c or c[0] == "Board" or set(c[0]) <= set("- "):   # header / separator
            continue
        board = c[0]
        if section == "device":
            # | Board | Device | TotalPin | TestedPin | TestResult |  (欄位排版不一致)
            name = c[1] if len(c) > 1 else "?"
            result = "FAIL" if any("FAIL" in x.upper() for x in c) else "PASS"
            items.append(TestItem(board, name, result))
        elif section == "net":
            # | Board | Net | Covered | TestResult | TestData |
            name = c[1] if len(c) > 1 else "?"
            result = (c[3].upper() if len(c) > 3 else "PASS")
            if result == "FAIL":
                fault, nodes = _parse_fail(c[4] if len(c) > 4 else "")
                items.append(TestItem(board, name, "FAIL", fault, nodes, path_to_tree(nodes)))
            else:
                items.append(TestItem(board, name, "PASS"))
    return items


def report_stats(path: str | Path) -> tuple[int, int]:
    """(total_items, fail_count) — used by capacity to derive working time."""
    items = parse_report(path)
    return len(items), sum(1 for it in items if it.result == "FAIL")
