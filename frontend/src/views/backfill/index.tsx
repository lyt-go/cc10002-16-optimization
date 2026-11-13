import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import { request } from '@/api/client'
import type { BackfillRow, PageResult, RangeConclusion, ScanBatch } from '@/api/scan'
import { formatRanges } from '@/api/scan'
import { Modal, Pager, StatusBadge } from '@/components/widgets'

const ENDPOINT = '/api/backfill'
const actions = ['提交补数审批', '启动补数', '终止补数']
const policies = ['增量覆盖', '分区覆盖', '全量覆盖']

type Filters = { keyword?: string; status?: string; table?: string; month?: string }

export default function BackfillView() {
  const [rows, setRows] = useState<BackfillRow[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [size] = useState(10)
  const [errorMessage, setErrorMessage] = useState('')
  const [filters, setFilters] = useState<Filters>({})
  const [options, setOptions] = useState<{ 目标表名: string[]; 统计月份: string[] }>({ 目标表名: [], 统计月份: [] })
  const [range, setRange] = useState<RangeConclusion | null>(null)
  const [detail, setDetail] = useState<BackfillRow | null>(null)
  const [batchTask, setBatchTask] = useState<BackfillRow | null>(null)
  const [batchForm, setBatchForm] = useState<Record<string, string>>({})

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams()
    Object.entries(filters).forEach(([key, value]) => value && query.set(key, value))
    query.set('page', String(page))
    query.set('size', String(size))
    try {
      const response = await request(`${ENDPOINT}?${query}`)
      if (!response.ok) throw new Error('补数任务列表读取失败')
      const payload = (await response.json()) as PageResult<BackfillRow>
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '历史补数列表读取失败')
    }
  }, [filters, page, size])

  const loadOptions = useCallback(async () => {
    const response = await request(`${ENDPOINT}/filters`)
    if (response.ok) setOptions(await response.json())
  }, [])

  const loadRange = useCallback(async (table: string, month?: string) => {
    const query = new URLSearchParams({ table })
    if (month) query.set('month', month)
    const response = await request(`/api/scan/range?${query}`)
    if (response.ok) setRange((await response.json()) as RangeConclusion)
  }, [])

  useEffect(() => { void reload() }, [reload])
  useEffect(() => { void loadOptions() }, [loadOptions])
  useEffect(() => {
    const table = filters.table ?? options.目标表名[0]
    if (table) void loadRange(table, filters.month)
  }, [filters.table, filters.month, options, loadRange])

  const stats = useMemo(() => {
    const running = rows.filter((row) => row.status === '执行中').length
    const done = rows.filter((row) => row.status === '已完成').length
    return [
      { label: '补数任务总数', value: total },
      { label: '执行中任务', value: running },
      { label: '已完成任务', value: done },
      { label: '范围待处理天数', value: range?.待处理天数 ?? 0 },
    ]
  }, [rows, total, range])

  async function runAction(action: string, row: BackfillRow) {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ values: { action } }),
      })
      const payload = await response.json()
      if (!response.ok || !payload.ok) throw new Error(payload.message ?? '历史补数动作未生效')
      await reload()
      if (range) await loadRange(range.目标表名, range.统计月份 ?? undefined)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '历史补数操作失败')
    }
  }

  async function openDetail(row: BackfillRow) {
    const response = await request(`${ENDPOINT}/${row.id}`)
    if (response.ok) setDetail((await response.json()) as BackfillRow)
  }

  function openBatchForm(row: BackfillRow) {
    setBatchTask(row)
    setBatchForm({
      开始日期: row.完成水位 ?? row.开始日期,
      结束日期: row.完成水位 ?? row.开始日期,
      覆盖策略: row.覆盖策略,
      结果: '成功',
      扫描量GB: String(row.每日扫描量GB ?? 0),
    })
  }

  async function submitBatch() {
    if (!batchTask) return
    setErrorMessage('')
    try {
      const response = await request('/api/scan/batches', {
        method: 'POST',
        body: JSON.stringify({ values: { 任务编号: batchTask.任务编号, ...batchForm } }),
      })
      const payload = await response.json()
      if (!response.ok || !payload.ok) throw new Error(payload.message ?? '扫描批次登记失败')
      setBatchTask(null)
      await reload()
      await loadRange(batchTask.目标表名, filters.month)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '扫描批次登记失败')
    }
  }

  return (
    <section className="page" data-module="backfill">
      <header className="page-head">
        <div>
          <h2>历史补数管理</h2>
          <p className="page-desc">
            基于成功区间、失败区间与覆盖策略计算仍需扫描范围；计划量固定、实际量按重试累计，与成本账单共用同一份范围结论。
          </p>
        </div>
        <div className="page-actions">
          <Link className="btn" to="/cost">查看联动成本账单 →</Link>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出历史补数清单
          </button>
        </div>
      </header>

      <div className="stat-row">
        {stats.map((item) => (
          <article key={item.label} className="stat-card">
            <span className="stat-label">{item.label}</span>
            <strong className="stat-value">{item.value}</strong>
          </article>
        ))}
      </div>

      {range && (
        <div className="metric-grid">
          <div className="metric-card"><span className="metric-label">目标表 / 月份</span><span className="metric-value">{range.目标表名} {range.统计月份 ?? '全部'}</span></div>
          <div className="metric-card"><span className="metric-label">已完成 / 计划天数</span><span className="metric-value">{range.已完成天数} / {range.计划天数}</span></div>
          <div className="metric-card"><span className="metric-label">待处理区间</span><span className="metric-value" style={{ fontSize: 13 }}>{formatRanges(range.待处理区间)}</span></div>
          <div className="metric-card"><span className="metric-label">失败区间</span><span className="metric-value" style={{ fontSize: 13 }}>{formatRanges(range.失败区间) || '—'}</span></div>
          <div className="metric-card"><span className="metric-label">计划扫描量 GB（固定）</span><span className="metric-value">{range.plan_scan_gb}</span></div>
          <div className="metric-card"><span className="metric-label">实际扫描量 GB（累计）</span><span className="metric-value">{range.actual_scan_gb}</span></div>
          <div className="metric-card"><span className="metric-label">重试扫描量 GB</span><span className="metric-value">{range.retry_scan_gb}</span></div>
          <div className="metric-card"><span className="metric-label">已覆盖天数（去重）</span><span className="metric-value">{range.covered_date_count}</span></div>
        </div>
      )}

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); setPage(1); void reload() }}>
        <label className="filter-item">
          <span>任务编号 / 目标表</span>
          <input placeholder="关键字" value={filters.keyword ?? ''} onChange={(e) => setFilters((p) => ({ ...p, keyword: e.target.value }))} />
        </label>
        <label className="filter-item">
          <span>状态</span>
          <select value={filters.status ?? ''} onChange={(e) => setFilters((p) => ({ ...p, status: e.target.value }))}>
            <option value="">全部</option>
            {['草稿', '补数审批中', '执行中', '已完成', '已终止'].map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </label>
        <label className="filter-item">
          <span>目标表名</span>
          <select value={filters.table ?? ''} onChange={(e) => setFilters((p) => ({ ...p, table: e.target.value }))}>
            <option value="">全部</option>
            {options.目标表名.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </label>
        <label className="filter-item">
          <span>统计月份</span>
          <select value={filters.month ?? ''} onChange={(e) => setFilters((p) => ({ ...p, month: e.target.value }))}>
            <option value="">全部</option>
            {options.统计月份.map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        </label>
        <button className="btn" type="submit">查询</button>
        <button className="btn ghost" type="button" onClick={() => { setFilters({}); setPage(1) }}>重置条件</button>
      </form>

      <table className="data-table">
        <thead>
          <tr>
            <th>任务编号</th><th>目标表名</th><th>计划区间</th><th>覆盖策略</th><th>状态</th>
            <th>完成水位</th><th>失败区间</th><th>计划/实际/重试扫描 GB</th><th>待处理区间</th><th>覆盖天数</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={String(row.id)}>
              <td><button className="link" type="button" onClick={() => void openDetail(row)}>{row.任务编号}</button></td>
              <td>{row.目标表名}</td>
              <td>{row.开始日期}~{row.结束日期}</td>
              <td>{row.覆盖策略}</td>
              <td><StatusBadge status={row.status === '已终止' ? '已失效' : row.status === '已完成' ? '已完成' : '待处理'} /></td>
              <td>{row.完成水位 ?? '—'}</td>
              <td>{row.失败区间 ?? '—'}</td>
              <td>{row.范围结论 ? `${row.范围结论.plan_scan_gb} / ${row.范围结论.actual_scan_gb} / ${row.范围结论.retry_scan_gb}` : '—'}</td>
              <td style={{ fontSize: 12 }}>{row.范围结论 ? formatRanges(row.范围结论.待处理区间[0]) : '—'}</td>
              <td>{row.范围结论?.covered_date_count ?? '—'}</td>
              <td className="row-actions">
                <button className="link" type="button" onClick={() => openBatchForm(row)}>登记扫描批次</button>
                <Link className="nav-link" to={`/cost?table=${encodeURIComponent(row.目标表名)}&month=${row.开始日期.slice(0, 7)}`}>账单</Link>
                {actions.map((action) => (
                  <button key={action} className="link" type="button" onClick={() => void runAction(action, row)}>{action}</button>
                ))}
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr><td className="empty-state" colSpan={11}>暂无历史补数数据</td></tr>
          )}
        </tbody>
      </table>
      <Pager page={page} size={size} total={total} onPage={setPage} />

      <footer className="page-foot">
        <span>补数列表与成本详情读取同一范围结果</span>
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>

      {detail && (
        <Modal title={`补数任务 ${detail.任务编号} · 扫描批次与范围审计`} onClose={() => setDetail(null)}>
          <p className="page-desc">
            目标表 {detail.目标表名}，计划 {detail.开始日期}~{detail.结束日期}，覆盖策略 {detail.覆盖策略}，
            当前水位 {detail.完成水位 ?? '—'}，scope_closed={String(detail.scope_closed)}
          </p>
          <table className="data-table subtable">
            <thead><tr><th>exec_id</th><th>批次</th><th>区间</th><th>策略</th><th>结果</th><th>重试次数</th><th>扫描量GB</th></tr></thead>
            <tbody>
              {(detail.扫描批次 ?? []).map((b: ScanBatch) => (
                <tr key={b.id}>
                  <td>{b.exec_id}</td><td>{b.批次编号}</td><td>{b.开始日期}~{b.结束日期}</td>
                  <td>{b.覆盖策略}</td><td><StatusBadge status={b.结果} /></td><td>{b.重试次数}</td><td>{b.扫描量GB}</td>
                </tr>
              ))}
              {(detail.扫描批次 ?? []).length === 0 && <tr><td className="empty-state" colSpan={7}>暂无扫描批次</td></tr>}
            </tbody>
          </table>
        </Modal>
      )}

      {batchTask && (
        <Modal title={`登记扫描批次 · ${batchTask.任务编号}`} onClose={() => setBatchTask(null)}>
          <div className="form-grid">
            <label>开始日期<input type="date" value={batchForm.开始日期 ?? ''} onChange={(e) => setBatchForm((p) => ({ ...p, 开始日期: e.target.value }))} /></label>
            <label>结束日期<input type="date" value={batchForm.结束日期 ?? ''} onChange={(e) => setBatchForm((p) => ({ ...p, 结束日期: e.target.value }))} /></label>
            <label>覆盖策略
              <select value={batchForm.覆盖策略 ?? ''} onChange={(e) => setBatchForm((p) => ({ ...p, 覆盖策略: e.target.value }))}>
                {policies.map((p) => <option key={p} value={p}>{p}</option>)}
              </select>
            </label>
            <label>结果
              <select value={batchForm.结果 ?? '成功'} onChange={(e) => setBatchForm((p) => ({ ...p, 结果: e.target.value }))}>
                <option value="成功">成功</option>
                <option value="失败">失败</option>
              </select>
            </label>
            <label>扫描量GB（实际，重试累计）<input type="number" value={batchForm.扫描量GB ?? ''} onChange={(e) => setBatchForm((p) => ({ ...p, 扫描量GB: e.target.value }))} /></label>
            <label>重试次数（留空自动推断）<input type="number" min="0" value={batchForm.重试次数 ?? ''} onChange={(e) => setBatchForm((p) => ({ ...p, 重试次数: e.target.value }))} /></label>
          </div>
          <p className="page-desc">登记后自动推进完成水位、重算待处理范围，并联动生成新的账单当前版本。</p>
          <div className="modal-foot">
            <button className="btn ghost" type="button" onClick={() => setBatchTask(null)}>取消</button>
            <button className="btn primary" type="button" onClick={() => void submitBatch()}>提交批次</button>
          </div>
        </Modal>
      )}
    </section>
  )
}
