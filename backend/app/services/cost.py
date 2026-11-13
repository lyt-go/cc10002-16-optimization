"""存储成本业务规则：账单版本、扫描量口径与明细筛选都收在这里。

账单与补数共用 ``scan_range`` 的范围结论：
- 同一月份同一目标表只允许一个当前版本，重复生成只产生一份新版本并使旧版本失效；
- 水位推进后未执行部分立即失效（状态=已失效），历史已执行量随旧版本保留可追溯；
- 明细按统计月份、目标表、覆盖策略筛选并分页，可直接定位待处理日期与重试扫描。
"""
from __future__ import annotations

from typing import Any

from app.services import scan_range
from app.services.scan_range import LINE_DONE, LINE_PENDING, LINE_INVALID, next_id
from app.services.trail import record_trail
from app.store import store

MODULE = "cost"
LINE_MODULE = "cost_bill_line"

REQUIRED_FIELDS = ["统计月份", "数据表名"]
STATUS_ORDER = ["草稿", "已生成", "已核对", "已归档"]
ACTION_RULES = {"生成账单": "已生成", "核对账单": "已核对", "归档账单": "已归档"}

# 计价口径（演示用单价，元/GB）。
SCAN_PRICE = 0.35
STORAGE_GB_PER_DAY = 200.0
STORAGE_PRICE = 0.12


class CostService:
    # ---------- 内部口径 ----------
    @staticmethod
    def _versions() -> list[dict[str, Any]]:
        return store.rows(MODULE)

    @staticmethod
    def _lines() -> list[dict[str, Any]]:
        return store.rows(LINE_MODULE)

    def _build_snapshot(
        self,
        table: str,
        month: str,
        *,
        trigger: str,
        before_watermark: str | None,
        after_watermark: str | None,
        exec_id: str | None,
        reason: str,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        conclusion = scan_range.build_table_conclusion(
            store.rows("backfill"), store.rows("scan_batch"), table, month
        )
        versions = self._versions()
        seq = sum(1 for v in versions if v.get("数据表名") == table and v.get("统计月份") == month) + 1
        bill_no = f"COST-{table}-{month}-V{seq}"

        done_gb = round(sum(
            line["扫描量GB"] for line in conclusion["lines"] if line["行状态"] == LINE_DONE
        ), 4)
        invalid_gb = round(sum(
            line["计划扫描量GB"] for line in conclusion["lines"] if line["行状态"] == LINE_INVALID
        ), 4)
        storage_gb = round(STORAGE_GB_PER_DAY * conclusion["计划天数"], 4)
        storage_cost = round(storage_gb * STORAGE_PRICE, 2)
        compute_cost = round(conclusion["actual_scan_gb"] * SCAN_PRICE, 2)

        version = {
            "id": next_id(versions),
            "账单编号": bill_no,
            "版本号": seq,
            "is_current": True,
            "status": "已生成",
            "pending": False,
            "abnormal": False,
            "数据表名": table,
            "统计月份": month,
            "触发方式": trigger,
            "关联exec_id": exec_id,
            "plan_scan_gb": conclusion["plan_scan_gb"],
            "actual_scan_gb": conclusion["actual_scan_gb"],
            "retry_scan_gb": conclusion["retry_scan_gb"],
            "covered_date_count": conclusion["covered_date_count"],
            "已完成扫描量GB": done_gb,
            "失效计划量GB": invalid_gb,
            "存储大小GB": storage_gb,
            "存储成本": storage_cost,
            "计算成本": compute_cost,
            "watermark_before": before_watermark,
            "watermark_after": after_watermark,
            "计划天数": conclusion["计划天数"],
            "已完成天数": conclusion["已完成天数"],
            "待处理天数": conclusion["待处理天数"],
            "失效天数": conclusion["失效天数"],
            "生成原因": reason,
        }

        lines: list[dict[str, Any]] = []
        for line in conclusion["lines"]:
            row = dict(line)
            row["id"] = next_id(self._lines())
            row["账单编号"] = bill_no
            row["数据表名"] = table
            lines.append(row)
        return version, lines

    def _publish_version(
        self,
        table: str,
        month: str,
        *,
        trigger: str,
        exec_id: str | None = None,
        reason: str = "",
        before_watermark: str | None = None,
    ) -> dict[str, Any]:
        """发布新版本：旧当前版本立刻失效但保留可追溯，明细按版本隔离。"""
        versions = self._versions()
        old_current = next(
            (v for v in versions if v.get("数据表名") == table
             and v.get("统计月份") == month and v.get("is_current")),
            None,
        )
        if old_current is None:
            before_wm = before_watermark
        else:
            before_wm = before_watermark if before_watermark is not None else old_current.get("watermark_after")
            old_current["is_current"] = False
            old_current["status"] = "已归档"
            old_current["pending"] = False

        conclusion = scan_range.build_table_conclusion(
            store.rows("backfill"), store.rows("scan_batch"), table, month
        )
        after_wm = conclusion["完成水位"]
        version, lines = self._build_snapshot(
            table,
            month,
            trigger=trigger,
            before_watermark=before_wm,
            after_watermark=after_wm,
            exec_id=exec_id,
            reason=reason,
        )
        versions.append(version)
        self._lines().extend(lines)

        record_trail(
            change_type="账单版本切换",
            table_name=table,
            before={"账单编号": old_current.get("账单编号"), "watermark_after": old_current.get("watermark_after")}
            if old_current else None,
            after={"账单编号": version["账单编号"], "watermark_before": before_wm, "watermark_after": after_wm},
            reason=reason or trigger,
        )
        return version

    def bootstrap(self) -> None:
        """服务启动后按现有补数任务与扫描批次生成首份当前账单。"""
        backfill_rows = store.rows("backfill")
        tables = sorted({str(row.get("目标表名") or "") for row in backfill_rows})
        for table in tables:
            for month in scan_range.all_months_for_table(backfill_rows, table):
                self._publish_version(
                    table, month, trigger="初始化", reason="按补数记录与扫描批次初始化当前账单版本"
                )

    def refresh_for_batch(self, table: str, month: str, exec_id: str | None, reason: str) -> dict[str, Any] | None:
        """扫描批次登记或任务终止后，已有账单的月份立刻刷新；没有账单则等手工生成。"""
        exists = any(
            v.get("数据表名") == table and v.get("统计月份") == month
            for v in self._versions()
        )
        if not exists:
            return None
        return self._publish_version(
            table, month, trigger="水位推进", exec_id=exec_id, reason=reason
        )

    # ---------- 查询 ----------
    def list_versions(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        month: str | None = None,
        table: str | None = None,
        current_only: bool = False,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = list(self._versions())
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("账单编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if month:
            rows = [row for row in rows if row.get("统计月份") == month]
        if table:
            rows = [row for row in rows if row.get("数据表名") == table]
        if current_only:
            rows = [row for row in rows if row.get("is_current")]
        rows.sort(key=lambda r: (str(r.get("统计月份")), str(r.get("数据表名")), int(r.get("版本号", 0))), reverse=True)
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def version_history(self, table: str, month: str) -> list[dict[str, Any]]:
        rows = [
            row for row in self._versions()
            if row.get("数据表名") == table and row.get("统计月份") == month
        ]
        return sorted(rows, key=lambda r: int(r.get("版本号", 0)), reverse=True)

    def list_lines(
        self,
        *,
        month: str | None = None,
        table: str | None = None,
        policy: str | None = None,
        line_status: str | None = None,
        retry_only: bool = False,
        version_id: int | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        # 明细默认挂当前版本；指定版本时读取历史版本，保证旧版本可追溯。
        versions = self._versions()
        if version_id is not None:
            target = next((v for v in versions if int(v.get("id", 0)) == version_id), None)
            if target is None:
                return [], 0
            bill_nos = [target["账单编号"]]
        else:
            bill_nos = [
                v["账单编号"] for v in versions
                if v.get("is_current")
                and (month is None or v.get("统计月份") == month)
                and (table is None or v.get("数据表名") == table)
            ]
        rows = [row for row in self._lines() if row.get("账单编号") in bill_nos]
        if month:
            rows = [row for row in rows if row.get("统计月份") == month]
        if table:
            rows = [row for row in rows if row.get("数据表名") == table]
        if policy:
            rows = [row for row in rows if row.get("覆盖策略") == policy]
        if line_status:
            rows = [row for row in rows if row.get("行状态") == line_status]
        if retry_only:
            rows = [row for row in rows if row.get("是否重试扫描")]
        rows.sort(key=lambda r: (str(r.get("目标表名")), str(r.get("日期"))))
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_version_detail(self, version_id: int) -> dict[str, Any] | None:
        version = store.find(MODULE, version_id)
        if version is None:
            return None
        lines = [row for row in self._lines() if row.get("账单编号") == version.get("账单编号")]
        detail = dict(version)
        detail["明细"] = sorted(lines, key=lambda r: str(r.get("日期")))
        return detail

    # ---------- 写入与状态流转 ----------
    def generate_bill(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        month = str(values["统计月份"]).strip()[:7]
        table = str(values["数据表名"]).strip()
        try:
            scan_range.month_bounds(month)
        except (ValueError, TypeError):
            return None, ["统计月份"]
        # 同一月份同一表重复生成：不新增第二份当前账单，而是产生一份新版本并作废旧当前版本。
        version = self._publish_version(
            table,
            month,
            trigger="手工生成",
            reason=str(values.get("remark") or "手工生成账单"),
        )
        return version, []

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        return self.generate_bill(values)

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"成本账单 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于存储成本可执行范围"
        if not entry.get("is_current"):
            return None, "历史版本只读，请对当前版本执行操作"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        before = entry.get("status")
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        record_trail(
            change_type="账单状态流转",
            table_name=str(entry.get("数据表名")),
            before={"status": before},
            after={"status": target, "账单编号": entry.get("账单编号")},
            reason=action,
        )
        return entry, f"成本账单已{action}"
