"""历史补数接口：维护补数任务与扫描批次，范围结论与成本账单共用一套口径。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.backfill import BackfillService

router = APIRouter(prefix="/api/backfill", tags=["历史补数"])

service = BackfillService()

STATUSES = ["草稿", "补数审批中", "执行中", "已完成", "已终止"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按任务编号或目标表名检索"),
    status: str | None = Query(default=None, description="草稿、补数审批中、执行中、已完成、已终止"),
    table: str | None = Query(default=None, description="目标表名"),
    month: str | None = Query(default=None, description="统计月份 YYYY-MM"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """补数列表内嵌与账单一致的范围结论：待处理区间、重试扫描与覆盖天数同口径。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(
        keyword=keyword, status=status, table=table, month=month, page=page, size=size
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/filters", response_model=dict)
def filters() -> dict[str, Any]:
    """补数页筛选选项，同时供账单页跨入口联动使用。"""
    return service.filter_options()


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出历史补数清单：返回当前过滤条件下的全量数据（含范围结论）。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "backfill", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条补数任务明细、范围结论与扫描批次；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"补数任务 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条补数任务，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少或非法字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="补数任务已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条补数任务执行提交补数审批、启动补数、终止补数；终止会使未执行范围失效。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
