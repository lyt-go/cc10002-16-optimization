"""历史补数业务规则：状态流转、范围结论与扫描批次口径都收在这里。

列表与成本账单共用 ``scan_range`` 计算的范围结论：
待处理范围基于成功区间、失败区间与覆盖策略计算，已成功补数不会再次估算为待处理。
"""
from __future__ import annotations

from typing import Any

from app.services import scan_range
from app.services.scan_batch import ScanBatchService
from app.services.trail import record_trail
from app.store import store

MODULE = "backfill"
REQUIRED_FIELDS = ["任务编号", "目标表名", "开始日期", "结束日期", "覆盖策略"]
STATUS_ORDER = ["草稿", "补数审批中", "执行中", "已完成", "已终止"]
ACTION_RULES = {"提交补数审批": "补数审批中", "启动补数": "执行中", "终止补数": "已终止"}
NEGATIVE_ACTIONS = ["终止补数"]
POLICIES = ["增量覆盖", "分区覆盖", "全量覆盖"]

scan_service = ScanBatchService()


class BackfillService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        table: str | None = None,
        month: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = list(store.rows(MODULE))
        if keyword:
            rows = [
                row for row in rows
                if keyword in str(row.get("任务编号", "")) or keyword in str(row.get("目标表名", ""))
            ]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if table:
            rows = [row for row in rows if row.get("目标表名") == table]
        if month:
            start, end = scan_range.month_bounds(month)
            rows = [
                row for row in rows
                if not (str(row.get("结束日期") or "") < start or str(row.get("开始日期") or "") > end)
            ]
        rows.sort(key=lambda r: int(r.get("id", 0)))
        total = len(rows)
        start_idx = max(page - 1, 0) * size
        page_rows = rows[start_idx:start_idx + size]
        # 补数列表内嵌与账单完全一致的范围结论，两个入口不再各看各的字段。
        enriched = [self._with_conclusion(row) for row in page_rows]
        return enriched, total

    def _with_conclusion(self, row: dict[str, Any]) -> dict[str, Any]:
        entry = dict(row)
        months = scan_range.all_months_for_table(store.rows(MODULE), str(row.get("目标表名")))
        conclusions = [
            scan_service.table_conclusion(str(row.get("目标表名")), month) for month in months
        ]
        entry["范围结论"] = {
            "统计月份": months,
            "待处理区间": [c["待处理区间"] for c in conclusions],
            "失败区间汇总": [c["失败区间"] for c in conclusions],
            "plan_scan_gb": round(sum(c["plan_scan_gb"] for c in conclusions), 4),
            "actual_scan_gb": round(sum(c["actual_scan_gb"] for c in conclusions), 4),
            "retry_scan_gb": round(sum(c["retry_scan_gb"] for c in conclusions), 4),
            "covered_date_count": sum(c["covered_date_count"] for c in conclusions),
        }
        return entry

    def filter_options(self) -> dict[str, list[str]]:
        rows = store.rows(MODULE)
        tables = sorted({str(row.get("目标表名") or "") for row in rows})
        months = sorted({
            day[:7]
            for row in rows
            for day in scan_range.daterange(str(row.get("开始日期") or ""), str(row.get("结束日期") or ""))
        })
        return {"目标表名": tables, "统计月份": months, "覆盖策略": POLICIES}

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        row = store.find(MODULE, entry_id)
        if row is None:
            return None
        entry = self._with_conclusion(row)
        batches = [b for b in store.rows("scan_batch") if b.get("任务编号") == row.get("任务编号")]
        entry["扫描批次"] = sorted(batches, key=lambda b: int(b.get("id", 0)))
        return entry

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        if str(values.get("覆盖策略") or "").strip() not in POLICIES:
            return None, ["覆盖策略"]
        if not scan_range.daterange(str(values["开始日期"]), str(values["结束日期"])):
            return None, ["开始日期", "结束日期"]
        rows = store.rows(MODULE)
        entry = {"id": scan_range.next_id(rows)}
        entry.update({
            "任务编号": str(values["任务编号"]).strip(),
            "目标表名": str(values["目标表名"]).strip(),
            "开始日期": str(values["开始日期"]).strip()[:10],
            "结束日期": str(values["结束日期"]).strip()[:10],
            "并行度": int(values.get("并行度") or 1),
            "覆盖策略": str(values["覆盖策略"]).strip(),
            "完成水位": None,
            "失败区间": None,
            "每日扫描量GB": round(float(values.get("每日扫描量GB") or 0.0), 4),
        })
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        entry["scope_closed"] = False
        rows.append(entry)
        record_trail(
            change_type="补数任务登记",
            table_name=entry["目标表名"],
            after={"任务编号": entry["任务编号"], "区间": f"{entry['开始日期']}~{entry['结束日期']}"},
            reason="登记补数任务",
        )
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"补数任务 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于历史补数可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        before = entry.get("status")
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        if action == "终止补数":
            # 终止即收口：未完成计划不再估算为待处理，账单未执行部分立即失效。
            scan_service.close_task(entry, reason=f"{entry['任务编号']} 终止，未执行范围失效")
        record_trail(
            change_type="补数状态流转",
            table_name=str(entry.get("目标表名")),
            before={"status": before},
            after={"status": target, "任务编号": entry.get("任务编号")},
            reason=action,
        )
        return entry, f"补数任务已{action}"
