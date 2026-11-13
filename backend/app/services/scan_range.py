"""扫描范围结论引擎（纯函数，不依赖 FastAPI 与单例 store）。

补数任务与成本账单共用同一份范围结论，口径集中在本模块：

- 基于补数任务的计划区间、扫描批次的成功/失败结果与覆盖策略，计算仍需扫描的范围；
- 并发任务扫描同一天时，只承认「已成功且覆盖策略更严格」的区间，已覆盖天数按天去重；
- 计划扫描量只由计划区间决定，重试不改变计划量；实际扫描量按批次（含重试）累计；
- 任务终止后，水位之外既未成功也不再计划的日期归为已失效，历史已执行量保留。
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

# 覆盖策略严格度：数值越大越严格。并发任务同一天都成功时，严格策略的区间胜出。
POLICY_RANK = {"增量覆盖": 1, "分区覆盖": 2, "全量覆盖": 3}

LINE_DONE = "已完成"
LINE_PENDING = "待处理"
LINE_INVALID = "已失效"

BATCH_SUCCESS = "成功"
BATCH_FAILED = "失败"


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(str(value)[:10])


def iso(day: date) -> str:
    return day.isoformat()


def daterange(start: str, end: str) -> list[str]:
    """返回闭区间内的全部日期，起止颠倒时自动交换。"""
    first = parse_date(start)
    last = parse_date(end)
    if first is None or last is None:
        return []
    if first > last:
        first, last = last, first
    days = (last - first).days
    return [iso(first + timedelta(days=i)) for i in range(days + 1)]


def month_bounds(month: str) -> tuple[str, str]:
    """把 'YYYY-MM' 转成当月首日与末日。"""
    year, mon = (int(part) for part in month.split("-")[:2])
    start = date(year, mon, 1)
    if mon == 12:
        end = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        end = date(year, mon + 1, 1) - timedelta(days=1)
    return iso(start), iso(end)


def merge_dates(days: list[str]) -> list[list[str]]:
    """把离散日期合并成连续区间 [start, end] 列表。"""
    if not days:
        return []
    ordered = sorted(days)
    ranges: list[list[str]] = []
    start = prev = parse_date(ordered[0])
    for value in ordered[1:]:
        current = parse_date(value)
        if current - prev == timedelta(days=1):
            prev = current
            continue
        ranges.append([iso(start), iso(prev)])
        start = prev = current
    ranges.append([iso(start), iso(prev)])
    return ranges


def format_ranges(ranges: list[list[str]]) -> str:
    if not ranges:
        return ""
    return "、".join(start if start == end else f"{start}~{end}" for start, end in ranges)


def policy_rank(policy: str | None) -> int:
    return POLICY_RANK.get(str(policy or "").strip(), 0)


def batch_days(batch: dict[str, Any]) -> list[str]:
    return daterange(str(batch.get("开始日期") or ""), str(batch.get("结束日期") or ""))


def batch_daily_gb(batch: dict[str, Any]) -> float:
    days = batch_days(batch)
    if not days:
        return 0.0
    return round(float(batch.get("扫描量GB") or 0.0) / len(days), 6)


def _task_plan_gb(task: dict[str, Any]) -> float:
    return float(task.get("每日扫描量GB") or 0.0)


def table_tasks(backfill_rows: list[dict[str, Any]], table: str) -> list[dict[str, Any]]:
    return [row for row in backfill_rows if str(row.get("目标表名") or "") == table]


def build_table_conclusion(
    backfill_rows: list[dict[str, Any]],
    batch_rows: list[dict[str, Any]],
    table: str,
    month: str | None = None,
) -> dict[str, Any]:
    """计算一张目标表（可限定统计月份）的统一范围结论。

    返回结构同时服务补数列表与成本账单，保证两个入口读到的口径完全一致。
    """
    tasks = table_tasks(backfill_rows, table)
    month_start, month_end = month_bounds(month) if month else (None, None)

    def in_month(day: str) -> bool:
        return month_start is None or month_start <= day <= month_end

    # 1) 计划区间：计划量只认任务计划，重试不会改变它。
    plan_owners: dict[str, list[dict[str, Any]]] = {}
    plan_gb: dict[str, float] = {}
    for task in tasks:
        for day in daterange(str(task.get("开始日期") or ""), str(task.get("结束日期") or "")):
            if not in_month(day):
                continue
            plan_owners.setdefault(day, []).append(task)
            plan_gb[day] = max(plan_gb.get(day, 0.0), _task_plan_gb(task))

    batches = [row for row in batch_rows if str(row.get("目标表名") or "") == table]
    touched: dict[str, list[dict[str, Any]]] = {}
    for batch in batches:
        for day in batch_days(batch):
            if in_month(day):
                touched.setdefault(day, []).append(batch)

    # 2) 每天的最终采用区间：只看成功批次，严格策略优先，同策略取最新批次。
    adopted: dict[str, dict[str, Any]] = {}
    for day, day_batches in touched.items():
        winners = [b for b in day_batches if b.get("结果") == BATCH_SUCCESS]
        if winners:
            adopted[day] = max(winners, key=lambda b: (policy_rank(b.get("覆盖策略")), int(b.get("id", 0))))

    completed_days = sorted(day for day in plan_owners if day in adopted)
    pending_days: list[str] = []
    invalid_days: list[str] = []
    failed_days: list[str] = []

    for day in sorted(plan_owners):
        if day in adopted:
            continue
        owners = plan_owners[day]
        # 能进入 plan_owners 说明该任务计划覆盖当天；只要还有未收口任务，日期就仍待处理。
        still_intended = any(not bool(owner.get("scope_closed")) for owner in owners)
        if still_intended:
            pending_days.append(day)
        else:
            invalid_days.append(day)
        if any(b.get("结果") == BATCH_FAILED for b in touched.get(day, [])):
            failed_days.append(day)

    # 3) 扫描量：实际量按所有执行批次累计，重试量只累计 retry_no>=1 的批次。
    actual_scan_gb = round(sum(float(b.get("扫描量GB") or 0.0) for b in batches), 4)
    retry_scan_gb = round(
        sum(float(b.get("扫描量GB") or 0.0) for b in batches if int(b.get("重试次数") or 0) >= 1),
        4,
    )
    plan_scan_gb = round(sum(plan_gb.values()), 4)

    lines: list[dict[str, Any]] = []
    for day in sorted(plan_owners):
        day_batches = sorted(touched.get(day, []), key=lambda b: int(b.get("id", 0)))
        winner = adopted.get(day)
        if winner is not None:
            status = LINE_DONE
            policy = winner.get("覆盖策略")
            source_task = winner.get("任务编号")
            adopted_range = f"{winner.get('开始日期')}~{winner.get('结束日期')}"
        else:
            status = LINE_PENDING if day in pending_days else LINE_INVALID
            strictest = max(plan_owners[day], key=lambda t: policy_rank(t.get("覆盖策略")))
            policy = strictest.get("覆盖策略")
            source_task = strictest.get("任务编号")
            adopted_range = None
        batch_details = [
            {
                "exec_id": b.get("exec_id"),
                "批次编号": b.get("批次编号"),
                "任务编号": b.get("任务编号"),
                "覆盖策略": b.get("覆盖策略"),
                "结果": b.get("结果"),
                "重试次数": int(b.get("重试次数") or 0),
                "当日扫描量GB": batch_daily_gb(b),
                "adopted": winner is not None and b.get("id") == winner.get("id"),
            }
            for b in day_batches
        ]
        retry_count = sum(1 for b in day_batches if int(b.get("重试次数") or 0) >= 1)
        lines.append({
            "日期": day,
            "目标表名": table,
            "统计月份": month or day[:7],
            "行状态": status,
            "覆盖策略": policy,
            "是否重试扫描": retry_count > 0,
            "重试次数": retry_count,
            "尝试次数": len(day_batches),
            "扫描量GB": round(sum(batch_daily_gb(b) for b in day_batches), 4),
            "计划扫描量GB": plan_gb.get(day, 0.0),
            "来源任务编号": source_task,
            "来源批次": "、".join(str(b.get("exec_id")) for b in day_batches) or None,
            "采用区间": adopted_range,
            "批次明细": batch_details,
            "失效原因": None if status != LINE_INVALID else "任务已终止且该日期未成功补数",
        })

    primary_task = None
    if tasks:
        primary_task = min(tasks, key=lambda t: (str(t.get("开始日期")), -policy_rank(t.get("覆盖策略"))))
    watermark = primary_task.get("完成水位") if primary_task else None

    return {
        "目标表名": table,
        "统计月份": month,
        "计划日期": sorted(plan_owners),
        "已完成日期": completed_days,
        "待处理日期": pending_days,
        "已失效日期": invalid_days,
        "失败日期": sorted(set(failed_days)),
        "待处理区间": merge_dates(pending_days),
        "失败区间": merge_dates(sorted(set(failed_days))),
        "plan_scan_gb": plan_scan_gb,
        "actual_scan_gb": actual_scan_gb,
        "retry_scan_gb": retry_scan_gb,
        "covered_date_count": len(set(completed_days)),
        "计划天数": len(plan_owners),
        "已完成天数": len(completed_days),
        "待处理天数": len(pending_days),
        "失效天数": len(invalid_days),
        "完成水位": watermark,
        "lines": lines,
    }


def all_months_for_table(backfill_rows: list[dict[str, Any]], table: str) -> list[str]:
    months: set[str] = set()
    for task in table_tasks(backfill_rows, table):
        for day in daterange(str(task.get("开始日期") or ""), str(task.get("结束日期") or "")):
            months.add(day[:7])
    return sorted(months)


def next_id(rows: list[dict[str, Any]]) -> int:
    return max((int(row.get("id", 0)) for row in rows), default=0) + 1
