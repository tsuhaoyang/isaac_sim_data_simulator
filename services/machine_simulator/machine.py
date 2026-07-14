"""Per-machine state machine — 測試機版 (SPEC §4).

一顆板子在機台上要跑一連串測項（來自測試報告）。working 階段逐筆串流測項：
每筆間隔 row_interval_s；遇 FAIL 該筆照發（含解析後 path），然後**進 error 狀態**停
fail_recovery_s，復原後**回 working 繼續**測下一筆（不中止整板，verdict 記為 FAIL）。
全部測完 → check_out → done。

State flow:  empty -> check_in(收合) -> working(逐筆測項) -> check_out(吐出) -> done -> empty
             working --FAIL--> error --(fail_recovery_s)--> working   (回原處續測，_recovering=True)
  scheduler 把工作中的 error 當「還在忙、復原中」不報廢（policy._observe 不 scrap）。
  真機台若回報 error（_recovering=False 語意）則走 error->empty；此模擬不產生真 error。

Transport-agnostic、deterministic：tick(now) 由注入的 clock 值驅動，可單測。
"""

from dataclasses import dataclass, field

from isaac_common.schemas import MachineState, MachineStateEvent, MachineTestEvent
from isaac_common.test_report import TestItem


@dataclass
class MachineConfig:
    test_rows: list[TestItem]          # 這台機台的測項清單（來自報告檔）
    row_interval_s: float = 2.0        # 每筆測項間隔
    fail_recovery_s: float = 10.0      # 遇 FAIL 的復原時間
    check_in_time_s: float = 0.0       # Tray 收合
    check_out_time_s: float = 0.0      # Tray 吐出
    telemetry_interval_s: float = 1.0
    autonomous: bool = True
    idle_before_load_s: float = 1.0    # autonomous: empty -> next load
    done_hold_s: float = 1.0           # autonomous: done -> auto unload


@dataclass
class TickOutput:
    transitions: list[MachineStateEvent] = field(default_factory=list)  # -> plant/.../state (retained)
    telemetry: MachineStateEvent | None = None                          # -> plant/.../telemetry
    test_items: list[MachineTestEvent] = field(default_factory=list)    # -> plant/.../test


class Machine:
    def __init__(self, machine_id: str, cfg: MachineConfig, rng, now: float, next_product_id):
        self.id = machine_id
        self.cfg = cfg
        self.rng = rng
        self._next_product_id = next_product_id
        self.state = MachineState.EMPTY
        self.product_id: str | None = None
        self._entered = now
        self._deadline = now + cfg.idle_before_load_s
        self._last_tele = now
        self._pending_load: str | None = None
        self._pending_unload = False
        # test streaming
        self._row_idx = 0
        self._next_row_at = 0.0
        self._fails = 0
        self._recovering = False   # True = 目前的 error 是「測試 FAIL 復原」，復原後回 working

    def reset(self, now: float) -> None:
        self.state = MachineState.EMPTY
        self.product_id = None
        self._entered = now
        self._deadline = now + self.cfg.idle_before_load_s
        self._last_tele = now
        self._pending_load = None
        self._pending_unload = False
        self._row_idx = 0
        self._fails = 0
        self._recovering = False

    # ---- external triggers (driven mode) ----
    def request_load(self, product_id: str) -> None:
        self._pending_load = product_id

    def request_unload(self) -> None:
        self._pending_unload = True

    # ---- helpers ----
    def _enter(self, state: MachineState, now: float) -> MachineStateEvent:
        self.state = state
        self._entered = now
        self._last_tele = now
        return self._event(now)

    def _remaining(self, now: float) -> float:
        """working 剩餘時間估計：剩餘測項 × interval + 剩餘 FAIL × recovery。"""
        left = self.cfg.test_rows[self._row_idx:]
        fails = sum(1 for r in left if r.result == "FAIL")
        wait = max(self._next_row_at - now, 0.0)
        return wait + max(len(left) - 1, 0) * self.cfg.row_interval_s + fails * self.cfg.fail_recovery_s

    def _event(self, now: float) -> MachineStateEvent:
        if self.state == MachineState.WORKING:
            remaining = self._remaining(now)
        elif self.state == MachineState.ERROR:
            remaining = max(self._deadline - now, 0.0)   # 復原剩餘時間
        else:
            remaining = 0.0
        return MachineStateEvent(
            machine_id=self.id, state=self.state, product_id=self.product_id,
            elapsed_s=round(now - self._entered, 3), remaining_s=round(remaining, 3),
        )

    def _test_event(self, row: TestItem, index: int) -> MachineTestEvent:
        return MachineTestEvent(
            machine_id=self.id, product_id=self.product_id,
            index=index, total=len(self.cfg.test_rows),
            board=row.board, item=row.item, result=row.result,
            fault=row.fault, path=list(row.path),
        )

    def _begin_job(self, product_id: str, now: float, out: TickOutput) -> None:
        self.product_id = product_id
        out.transitions.append(self._enter(MachineState.CHECK_IN, now))     # Tray 收合
        self._deadline = now + self.cfg.check_in_time_s

    def _begin_working(self, now: float, out: TickOutput) -> None:
        self._row_idx = 0
        self._fails = 0
        self._next_row_at = now + self.cfg.row_interval_s
        out.transitions.append(self._enter(MachineState.WORKING, now))

    # ---- main tick ----
    def tick(self, now: float) -> TickOutput:
        out = TickOutput()
        st = self.state

        if st == MachineState.EMPTY:
            if self._pending_load is not None:
                self._begin_job(self._pending_load, now, out)
                self._pending_load = None
            elif self.cfg.autonomous and now >= self._deadline:
                self._begin_job(self._next_product_id(), now, out)

        elif st == MachineState.CHECK_IN:                       # 收合完成 -> 開始測試
            if now >= self._deadline:
                self._begin_working(now, out)

        elif st == MachineState.WORKING:
            rows = self.cfg.test_rows
            if self._row_idx < len(rows) and now >= self._next_row_at:
                row = rows[self._row_idx]
                self._row_idx += 1
                out.test_items.append(self._test_event(row, self._row_idx))
                if row.result == "FAIL":
                    self._fails += 1
                    out.transitions.append(self._enter(MachineState.ERROR, now))  # FAIL -> 發 error
                    self._recovering = True
                    self._deadline = now + self.cfg.fail_recovery_s
                else:
                    self._next_row_at = now + self.cfg.row_interval_s
            elif self._row_idx >= len(rows) and now >= self._next_row_at:
                out.transitions.append(self._enter(MachineState.CHECK_OUT, now))  # 全測完 -> Tray 吐出
                self._deadline = now + self.cfg.check_out_time_s
            elif now - self._last_tele >= self.cfg.telemetry_interval_s:
                self._last_tele = now
                out.telemetry = self._event(now)

        elif st == MachineState.CHECK_OUT:                      # 吐出完成 -> 待取料
            if now >= self._deadline:
                out.transitions.append(self._enter(MachineState.DONE, now))
                if self.cfg.autonomous:
                    self._deadline = now + self.cfg.done_hold_s

        elif st == MachineState.DONE:
            if self._pending_unload or (self.cfg.autonomous and now >= self._deadline):
                self._pending_unload = False
                self.product_id = None
                out.transitions.append(self._enter(MachineState.EMPTY, now))
                self._deadline = now + self.cfg.idle_before_load_s

        elif st == MachineState.ERROR:
            if now >= self._deadline:
                if self._recovering:                           # 測試 FAIL 復原完 -> 回 working 續測
                    self._recovering = False
                    out.transitions.append(self._enter(MachineState.WORKING, now))
                    self._next_row_at = now + self.cfg.row_interval_s
                else:                                          # 真機台故障（模擬不會走到）-> empty
                    self.product_id = None
                    out.transitions.append(self._enter(MachineState.EMPTY, now))
                    self._deadline = now + self.cfg.idle_before_load_s

        return out
