"""补数范围结论与成本账单联动的回归测试，仅依赖标准库：

    cd backend && .venv/bin/python -m unittest discover -s tests -v
"""
from __future__ import annotations

import unittest

from app.services.backfill import BackfillService
from app.services.cost import CostService
from app.services.scan_batch import ScanBatchService
from app.store import store


class BackfillCostFlowTest(unittest.TestCase):
    def setUp(self) -> None:
        store.reset()
        self.backfill = BackfillService()
        self.cost = CostService()
        self.scan = ScanBatchService()

    def test_back_pay_range_split_matches_example(self) -> None:
        """BACK_PAY 9/1-9/30：1-18 已完成、19-30 待处理，重试扫描单列。"""
        d = self.scan.table_conclusion("BACK_PAY", "2026-09")
        self.assertEqual(d["计划天数"], 30)
        self.assertEqual(d["已完成天数"], 18)
        self.assertEqual(d["待处理天数"], 12)
        self.assertEqual(d["待处理区间"], [["2026-09-19", "2026-09-30"]])
        self.assertEqual(d["失败区间"], [["2026-09-19", "2026-09-19"]])
        line19 = next(line for line in d["lines"] if line["日期"] == "2026-09-19")
        self.assertEqual(line19["行状态"], "待处理")
        self.assertTrue(line19["是否重试扫描"])
        self.assertEqual(line19["重试次数"], 1)

    def test_plan_scan_fixed_actual_and_retry_accumulate(self) -> None:
        before = self.scan.table_conclusion("BACK_PAY", "2026-09")
        self.assertEqual(before["plan_scan_gb"], 3600.0)
        actual_before = before["actual_scan_gb"]
        retry_before = before["retry_scan_gb"]

        batch, missing = self.scan.register_batch({
            "任务编号": "BACK-0001", "开始日期": "2026-09-20", "结束日期": "2026-09-20",
            "覆盖策略": "全量覆盖", "结果": "成功", "扫描量GB": 120,
        })
        self.assertEqual(missing, [])
        after = self.scan.table_conclusion("BACK_PAY", "2026-09")
        # 计划量保持不变；实际量与累计执行批次同口径增长。
        self.assertEqual(after["plan_scan_gb"], 3600.0)
        self.assertEqual(after["actual_scan_gb"], actual_before + 120)
        self.assertEqual(after["retry_scan_gb"], retry_before)
        self.assertIsNotNone(batch["exec_id"])

    def test_concurrent_same_day_uses_stricter_policy_once(self) -> None:
        """并发任务扫描同一天：严格策略的成功区间胜出，覆盖天数按天去重。"""
        d = self.scan.table_conclusion("BACK_PAY", "2026-09")
        line10 = next(line for line in d["lines"] if line["日期"] == "2026-09-10")
        self.assertEqual(line10["覆盖策略"], "全量覆盖")
        self.assertEqual(line10["来源任务编号"], "BACK-0001")
        self.assertEqual(line10["采用区间"], "2026-09-01~2026-09-18")
        # 两个来源任务都保留在批次明细里，但只有严格策略的批次被采用。
        source_tasks = {b["任务编号"] for b in line10["批次明细"]}
        self.assertEqual(source_tasks, {"BACK-0001", "BACK-0002"})
        adopted = [b for b in line10["批次明细"] if b["adopted"]]
        self.assertEqual(len(adopted), 1)
        self.assertEqual(adopted[0]["覆盖策略"], "全量覆盖")
        # 并集 30 天，已成功 18 天，不允许重复计入。
        self.assertEqual(d["covered_date_count"], d["已完成天数"])
        self.assertEqual(len({line["日期"] for line in d["lines"]}), 30)

    def test_successful_backfill_not_re_estimated_pending(self) -> None:
        """补 19 号成功后，该日期立即离开待处理，水位推进且账单记录水位前后值。"""
        self.scan.register_batch({
            "任务编号": "BACK-0001", "开始日期": "2026-09-19", "结束日期": "2026-09-19",
            "覆盖策略": "全量覆盖", "结果": "成功", "扫描量GB": 120,
        })
        d = self.scan.table_conclusion("BACK_PAY", "2026-09")
        self.assertEqual(d["已完成天数"], 19)
        self.assertEqual(d["待处理区间"], [["2026-09-20", "2026-09-30"]])
        current = self.cost.version_history("BACK_PAY", "2026-09")[0]
        self.assertEqual(current["watermark_before"], "2026-09-18")
        self.assertEqual(current["watermark_after"], "2026-09-19")
        self.assertEqual(current["covered_date_count"], 19)
        self.assertIsNotNone(current["关联exec_id"])

    def test_terminate_invalidates_unexecuted_but_keeps_history(self) -> None:
        """终止任务：未执行计划立即失效，历史已执行量与旧版本保留。"""
        self.backfill.run_action(1, "终止补数")
        d = self.scan.table_conclusion("BACK_PAY", "2026-09")
        self.assertEqual(d["失效天数"], 10)
        self.assertEqual(d["待处理区间"], [["2026-09-19", "2026-09-20"]])
        current = self.cost.version_history("BACK_PAY", "2026-09")[0]
        self.assertGreater(current["失效计划量GB"], 0)
        # 历史已执行量（含重试）仍保留在新版本汇总里。
        self.assertEqual(current["actual_scan_gb"], 2756.0)
        self.assertEqual(current["retry_scan_gb"], 116.0)
        invalid_lines, total = self.cost.list_lines(
            month="2026-09", table="BACK_PAY", line_status="已失效"
        )
        self.assertEqual(total, 10)
        self.assertTrue(all(line["失效原因"] for line in invalid_lines))

    def test_bill_single_current_version_and_history_traceable(self) -> None:
        """同月同表重复生成：只有一个当前版本，旧版本归档且只读、明细可追溯。"""
        v1, missing = self.cost.generate_bill({"统计月份": "2026-09", "数据表名": "BACK_PAY"})
        self.assertEqual(missing, [])
        v2, _ = self.cost.generate_bill({"统计月份": "2026-09", "数据表名": "BACK_PAY"})
        history = self.cost.version_history("BACK_PAY", "2026-09")
        currents = [v for v in history if v["is_current"]]
        self.assertEqual(len(currents), 1)
        self.assertEqual(currents[0]["账单编号"], v2["账单编号"])
        self.assertFalse(v1["is_current"])
        # 历史版本只读。
        blocked, message = self.cost.run_action(v1["id"], "核对账单")
        self.assertIsNone(blocked)
        self.assertIn("历史版本只读", message)
        # 历史版本明细仍可按 version_id 追溯。
        old_lines, total = self.cost.list_lines(version_id=v1["id"])
        self.assertEqual(total, 30)
        self.assertTrue(all(line["账单编号"] == v1["账单编号"] for line in old_lines))

    def test_bill_lines_filter_and_pagination(self) -> None:
        """账单明细按月份、目标表、覆盖策略筛选，分页可定位待处理与重试。"""
        pending, total = self.cost.list_lines(
            month="2026-09", table="BACK_PAY", line_status="待处理", page=1, size=5
        )
        self.assertEqual(total, 12)
        self.assertEqual(len(pending), 5)
        self.assertEqual([line["日期"] for line in pending][0], "2026-09-19")

        retry, retry_total = self.cost.list_lines(
            month="2026-09", table="BACK_PAY", retry_only=True
        )
        self.assertEqual(retry_total, 1)
        self.assertEqual(retry[0]["日期"], "2026-09-19")

        full, full_total = self.cost.list_lines(
            month="2026-09", table="BACK_PAY", policy="全量覆盖"
        )
        self.assertEqual(full_total, 30)
        self.assertTrue(all(line["覆盖策略"] == "全量覆盖" for line in full))

    def test_bill_version_records_required_audit_fields(self) -> None:
        """账单版本必须记录 exec_id、三类扫描量、覆盖天数与水位前后值。"""
        self.scan.register_batch({
            "任务编号": "BACK-0001", "开始日期": "2026-09-19", "结束日期": "2026-09-19",
            "覆盖策略": "全量覆盖", "结果": "成功", "扫描量GB": 120,
        })
        version = self.cost.version_history("BACK_PAY", "2026-09")[0]
        for field in (
            "关联exec_id", "plan_scan_gb", "actual_scan_gb", "retry_scan_gb",
            "covered_date_count", "watermark_before", "watermark_after",
        ):
            self.assertIn(field, version)
        self.assertEqual(version["watermark_before"], "2026-09-18")
        self.assertEqual(version["watermark_after"], "2026-09-19")

    def test_backfill_list_and_bill_share_same_conclusion(self) -> None:
        """补数列表内嵌范围结论与账单页读取的口径完全一致。"""
        items, _ = self.backfill.list_entries(table="BACK_PAY", page=1, size=10)
        conclusion = items[0]["范围结论"]
        range_view = self.scan.table_conclusion("BACK_PAY", "2026-09")
        self.assertEqual(conclusion["plan_scan_gb"], range_view["plan_scan_gb"])
        self.assertEqual(conclusion["actual_scan_gb"], range_view["actual_scan_gb"])
        self.assertEqual(conclusion["retry_scan_gb"], range_view["retry_scan_gb"])
        self.assertEqual(conclusion["covered_date_count"], range_view["covered_date_count"])

    def test_retry_number_inferred_and_retry_scan_accumulates(self) -> None:
        """同一区间失败后再登记成功批次，重试次数自动推断且重试扫描量累计。"""
        fail, _ = self.scan.register_batch({
            "任务编号": "BACK-0003", "开始日期": "2026-09-16", "结束日期": "2026-09-16",
            "覆盖策略": "分区覆盖", "结果": "失败", "扫描量GB": 80,
        })
        success, _ = self.scan.register_batch({
            "任务编号": "BACK-0003", "开始日期": "2026-09-16", "结束日期": "2026-09-16",
            "覆盖策略": "分区覆盖", "结果": "成功", "扫描量GB": 80,
        })
        self.assertEqual(fail["重试次数"], 0)
        self.assertEqual(success["重试次数"], 1)
        d = self.scan.table_conclusion("ORDER_DWD", "2026-09")
        self.assertEqual(d["retry_scan_gb"], 160.0)


if __name__ == "__main__":
    unittest.main()
