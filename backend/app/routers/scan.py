"""扫描批次接口：登记补数执行批次，并对外提供补数与账单共用的范围结论。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.scan_batch import ScanBatchService

router = APIRouter(prefix="/api/scan", tags=["扫描批次"])

service = ScanBatchService()


@router.get("/range", response_model=dict)
def table_range(
    table: str = Query(..., description="目标表名"),
    month: str | None = Query(default=None, description="统计月份 YYYY-MM，缺省返回全部月份"),
) -> dict:
    """补数页与账单页共用的范围结论入口。"""
    return service.table_conclusion(table, month)


@router.get("/batches", response_model=PageResult[dict])
def list_batches(
    task_no: str | None = Query(default=None, description="按补数任务编号过滤"),
    table: str | None = Query(default=None, description="按目标表名过滤"),
    result: str | None = Query(default=None, description="成功/失败"),
    retry_only: bool = Query(default=False, description="只看重试扫描"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_batches(
        task_no=task_no, table=table, result=result, retry_only=retry_only, page=page, size=size
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/batches/{batch_id}", response_model=dict)
def get_batch(batch_id: int) -> dict:
    batch = service.get_batch(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail=f"扫描批次 {batch_id} 不存在")
    return batch


@router.post("/batches", response_model=ActionResult)
def register_batch(payload: EntryPayload) -> ActionResult:
    """登记一次扫描执行（重试同样走这里，实际扫描量按批次累计）。"""
    batch, missing = service.register_batch(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少或非法字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="扫描批次已登记，水位与账单范围已联动刷新", entry=batch)
