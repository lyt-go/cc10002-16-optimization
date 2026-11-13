/** 补数与账单共用的扫描范围结论类型，保证两个页面消费同一份后端口径。 */

export interface RangeConclusion {
  目标表名: string
  统计月份: string | null
  计划天数: number
  已完成天数: number
  待处理天数: number
  失效天数: number
  plan_scan_gb: number
  actual_scan_gb: number
  retry_scan_gb: number
  covered_date_count: number
  完成水位: string | null
  待处理区间: [string, string][]
  失败区间: [string, string][]
  lines: BillLine[]
}

export interface BillLine {
  id: number
  账单编号?: string
  日期: string
  目标表名: string
  统计月份: string
  行状态: '已完成' | '待处理' | '已失效'
  覆盖策略: string | null
  是否重试扫描: boolean
  重试次数: number
  尝试次数: number
  扫描量GB: number
  计划扫描量GB: number
  来源任务编号: string | null
  来源批次: string | null
  采用区间: string | null
  失效原因: string | null
  批次明细: Array<{
    exec_id: string
    批次编号: string
    任务编号: string
    覆盖策略: string
    结果: string
    重试次数: number
    当日扫描量GB: number
    adopted: boolean
  }>
}

export interface BillVersion {
  id: number
  账单编号: string
  版本号: number
  is_current: boolean
  status: string
  数据表名: string
  统计月份: string
  触发方式: string
  关联exec_id: string | null
  plan_scan_gb: number
  actual_scan_gb: number
  retry_scan_gb: number
  covered_date_count: number
  已完成扫描量GB: number
  失效计划量GB: number
  存储大小GB: number
  存储成本: number
  计算成本: number
  watermark_before: string | null
  watermark_after: string | null
  计划天数: number
  已完成天数: number
  待处理天数: number
  失效天数: number
  生成原因: string
  明细?: BillLine[]
}

export interface ScanBatch {
  id: number
  批次编号: string
  exec_id: string
  任务编号: string
  目标表名: string
  覆盖策略: string
  开始日期: string
  结束日期: string
  结果: '成功' | '失败'
  重试次数: number
  扫描量GB: number
}

export interface BackfillRow {
  id: number
  status: string
  任务编号: string
  目标表名: string
  开始日期: string
  结束日期: string
  并行度: number
  覆盖策略: string
  完成水位: string | null
  失败区间: string | null
  每日扫描量GB: number
  scope_closed: boolean
  范围结论?: {
    统计月份: string[]
    待处理区间: [string, string][][]
    失败区间汇总: [string, string][][]
    plan_scan_gb: number
    actual_scan_gb: number
    retry_scan_gb: number
    covered_date_count: number
  }
  扫描批次?: ScanBatch[]
}

export interface PageResult<T> {
  items: T[]
  total: number
  page: number
  size: number
}

export function formatRanges(ranges?: [string, string][] | null): string {
  if (!ranges || ranges.length === 0) return '—'
  return ranges.map(([start, end]) => (start === end ? start : `${start}~${end}`)).join('、')
}
