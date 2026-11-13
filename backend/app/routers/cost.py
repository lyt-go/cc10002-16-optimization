"""存储成本接口：账单版本、版本历史与按月份/目标表/覆盖策略分页的账单明细。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.cost import CostService

router = APIRouter(prefix="/api/cost", tags=["存储成本"])

service = CostService()

STATUSES = ["草稿", "已生成", "已核对", "已归档"]
POLICIES = ["增量覆盖", "分区覆盖", "全量覆盖"]
LINE_STATUSES = ["已完成", "待处理", "已失效"]


@router.get("", response_model=PageResult[dict])
def list_versions(
    keyword: str | None = Query(default=None, description="按账单编号检索"),
    status: str | None = Query(default=None, description="草稿、已生成、已核对、已归档"),
    month: str | None = Query(default=None, description="统计月份 YYYY-MM"),
    table: str | None = Query(default=None, description="目标表名"),
    current_only: bool = Query(default=False, description="只看当前生效版本"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """账单版本列表；同一月份同一表只有一个当前版本，旧版本保留可追溯。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_versions(
        keyword=keyword,
        status=status,
        month=month,
        table=table,
        current_only=current_only,
        page=page,
        size=size,
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/filters", response_model=dict)
def filters() -> dict[str, Any]:
    """给账单页提供筛选选项（月份、目标表、覆盖策略、行状态）。"""
    versions, _ = service.list_versions(page=1, size=10000)
    months = sorted({str(v.get("统计月份") or "") for v in versions})
    tables = sorted({str(v.get("数据表名") or "") for v in versions})
    return {"统计月份": months, "目标表名": tables, "覆盖策略": POLICIES, "行状态": LINE_STATUSES}


@router.get("/history", response_model=dict)
def version_history(
    table: str = Query(..., description="目标表名"),
    month: str = Query(..., description="统计月份 YYYY-MM"),
) -> dict[str, Any]:
    """同一月份同一目标表的全部账单版本，最新在前。"""
    return {
        "table": table,
        "month": month,
        "items": service.version_history(table, month),
    }


@router.get("/lines", response_model=PageResult[dict])
def list_lines(
    month: str | None = Query(default=None, description="统计月份 YYYY-MM"),
    table: str | None = Query(default=None, description="目标表名"),
    policy: str | None = Query(default=None, description="覆盖策略：增量覆盖/分区覆盖/全量覆盖"),
    line_status: str | None = Query(default=None, description="已完成/待处理/已失效"),
    retry_only: bool = Query(default=False, description="只看重试扫描日期"),
    version_id: int | None = Query(default=None, description="查看历史版本明细时传版本 id"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """账单明细分页：可定位待处理日期与重试扫描，同一天保留来源任务与最终采用区间。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_lines(
        month=month,
        table=table,
        policy=policy,
        line_status=line_status,
        retry_only=retry_only,
        version_id=version_id,
        page=page,
        size=size,
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出生成过的全部账单版本（含已失效历史版本）。"""
    items, total = service.list_versions(page=1, size=10000)
    return {"module": "cost", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """账单版本详情，附带该版本下的全量明细。"""
    detail = service.get_version_detail(entry_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"成本账单 {entry_id} 不存在或已归档")
    return detail


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """生成（或重复生成）某月份某目标表账单；重复生成只产生一份新当前版本。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少或非法字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="账单当前版本已生成，历史版本保留可追溯", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对当前版本执行生成、核对、归档；历史版本只读。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
