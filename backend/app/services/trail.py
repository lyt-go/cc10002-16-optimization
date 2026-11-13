"""补数到账单链路上的历史审计记录。

扫描批次登记、水位推进、账单版本切换都在这里留痕，旧版本账单本身也保留，
两处共同构成可追溯链路。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.services.scan_range import next_id

TRAIL_MODULE = "audit_trail"


def record_trail(
    *,
    change_type: str,
    table_name: str,
    before: Any = None,
    after: Any = None,
    reason: str = "",
    operator: str = "系统",
) -> dict[str, Any]:
    # 延迟导入：Store 初始化时会反向调用成本服务，模块级 import 会形成循环。
    from app.store import store

    rows = store.rows(TRAIL_MODULE)
    entry = {
        "id": next_id(rows),
        "记录编号": f"TRAIL-{len(rows) + 1:04d}",
        "操作表名": table_name,
        "变更类型": change_type,
        "变更前值": before,
        "变更后值": after,
        "操作人": operator,
        "操作时间": datetime.now().isoformat(timespec="seconds"),
        "变更原因": reason,
        "status": "已记录",
        "pending": False,
        "abnormal": False,
    }
    rows.append(entry)
    return entry
