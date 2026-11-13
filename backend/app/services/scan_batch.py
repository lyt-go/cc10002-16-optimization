"""扫描批次服务：登记补数任务的每一次执行（含重试），并把结果贯通到水位与账单。

- 实际扫描量按批次累计：重试产生新批次，计划量不受影响；
- 成功批次按覆盖策略严格度决定同一天的最终采用区间；
- 登记成功后推进任务水位、刷新失败区间，并联动成本账单生成新版本。
"""
from __future__ import annotations

from typing import Any

from app.services import scan_range
from app.services.scan_range import (
    BATCH_FAILED,
    BATCH_SUCCESS,
    batch_days,
    daterange,
    format_ranges,
    merge_dates,
    next_id,
    parse_date,
)
from app.services.trail import record_trail
from app.store import store

MODULE = "scan_batch"
REQUIRED_FIELDS = ["任务编号", "开始日期", "结束日期", "覆盖策略", "结果"]


class ScanBatchService:
    def _batches(self) -> list[dict[str, Any]]:
        return store.rows(MODULE)

    def list_batches(
        self,
        *,
        task_no: str | None = None,
        table: str | None = None,
        result: str | None = None,
        retry_only: bool = False,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = list(self._batches())
        if task_no:
            rows = [row for row in rows if row.get("任务编号") == task_no]
        if table:
            rows = [row for row in rows if row.get("目标表名") == table]
        if result:
            rows = [row for row in rows if row.get("结果") == result]
        if retry_only:
            rows = [row for row in rows if int(row.get("重试次数") or 0) >= 1]
        rows.sort(key=lambda r: int(r.get("id", 0)))
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_batch(self, batch_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, batch_id)

    def register_batch(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        task = next(
            (row for row in store.rows("backfill") if row.get("任务编号") == values["任务编号"]),
            None,
        )
        if task is None:
            return None, ["任务编号"]
        result = str(values["结果"]).strip()
        if result not in (BATCH_SUCCESS, BATCH_FAILED):
            return None, ["结果"]
        days = daterange(str(values["开始日期"]), str(values["结束日期"]))
        if not days:
            return None, ["开始日期", "结束日期"]

        rows = self._batches()
        retry_no = int(values.get("重试次数") or 0)
        if retry_no <= 0:
            # 未显式传重试次数时，按同一日期区间已有失败次数自动推断。
            existing = [
                row for row in rows
                if row.get("任务编号") == task["任务编号"]
                and row.get("开始日期") == values["开始日期"]
                and row.get("结束日期") == values["结束日期"]
            ]
            retry_no = sum(1 for row in existing if row.get("结果") == BATCH_FAILED)

        batch = {
            "id": next_id(rows),
            "批次编号": f"SCAN-{len(rows) + 1:04d}",
            "exec_id": str(values.get("exec_id") or f"EXEC-{len(rows) + 1:04d}"),
            "任务编号": task["任务编号"],
            "目标表名": task.get("目标表名"),
            "覆盖策略": str(values["覆盖策略"]).strip(),
            "开始日期": days[0],
            "结束日期": days[-1],
            "结果": result,
            "重试次数": retry_no,
            "扫描量GB": round(float(values.get("扫描量GB") or 0.0), 4),
        }
        rows.append(batch)
        record_trail(
            change_type="扫描批次登记",
            table_name=str(task.get("目标表名")),
            before=None,
            after={"exec_id": batch["exec_id"], "结果": result, "重试次数": retry_no},
            reason=f"{task['任务编号']} 登记扫描批次 {batch['批次编号']}",
        )
        self._recompute_task(task)
        self._refresh_bills(task, batch)
        return batch, []

    def _recompute_task(self, task: dict[str, Any]) -> None:
        """按该任务自身的批次重算完成水位与失败区间（不看并发任务的区间）。"""
        task_batches = [b for b in self._batches() if b.get("任务编号") == task["任务编号"]]
        success_days = {
            day for b in task_batches if b.get("结果") == BATCH_SUCCESS for day in batch_days(b)
        }
        failed_days = sorted({
            day for b in task_batches if b.get("结果") == BATCH_FAILED for day in batch_days(b)
            if day not in success_days
        })
        planned = daterange(str(task.get("开始日期") or ""), str(task.get("结束日期") or ""))
        # 水位：从计划起点开始连续成功到的最后一天。
        watermark = None
        for day in planned:
            if day in success_days:
                watermark = day
            else:
                break
        before_wm = task.get("完成水位")
        task["完成水位"] = watermark
        task["失败区间"] = format_ranges(merge_dates(failed_days)) or None
        if planned and watermark == planned[-1]:
            task["status"] = "已完成"
            task["pending"] = False
            task["abnormal"] = False
        elif failed_days and task.get("status") not in ("草稿", "补数审批中"):
            task["abnormal"] = True
        if before_wm != watermark:
            record_trail(
                change_type="完成水位推进",
                table_name=str(task.get("目标表名")),
                before={"完成水位": before_wm},
                after={"完成水位": watermark, "任务编号": task["任务编号"]},
                reason=f"{task['任务编号']} 扫描结果回写",
            )

    def _refresh_bills(self, task: dict[str, Any], batch: dict[str, Any]) -> None:
        # 延迟导入避免 cost <-> scan_batch 循环引用。
        from app.services.cost import CostService

        months = sorted({day[:7] for day in batch_days(batch)})
        cost_service = CostService()
        for month in months:
            cost_service.refresh_for_batch(
                str(task.get("目标表名")),
                month,
                str(batch.get("exec_id")),
                reason=f"扫描批次 {batch['批次编号']} 回写后刷新范围结论",
            )

    def close_task(self, task: dict[str, Any], reason: str) -> None:
        """终止补数：未完成计划立即从待处理范围剔除，账单未执行部分随之失效。"""
        task["scope_closed"] = True
        from app.services.cost import CostService

        months = sorted({day[:7] for day in daterange(
            str(task.get("开始日期") or ""), str(task.get("结束日期") or "")
        )})
        cost_service = CostService()
        for month in months:
            cost_service.refresh_for_batch(
                str(task.get("目标表名")),
                month,
                None,
                reason=reason,
            )

    # ---------- 范围结论（补数页与账单页共用） ----------
    def table_conclusion(self, table: str, month: str | None = None) -> dict[str, Any]:
        return scan_range.build_table_conclusion(
            store.rows("backfill"), store.rows(MODULE), table, month
        )
