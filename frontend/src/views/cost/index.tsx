import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import { request } from '@/api/client'
import type { BillLine, BillVersion, PageResult } from '@/api/scan'
import { Modal, Pager, StatusBadge } from '@/components/widgets'

const ENDPOINT = '/api/cost'
const policies = ['增量覆盖', '分区覆盖', '全量覆盖']
const lineStatuses = ['已完成', '待处理', '已失效']
const versionActions = ['核对账单', '归档账单']

type LineFilters = {
  month?: string
  table?: string
  policy?: string
  line_status?: string
  retry_only?: boolean
  version_id?: number
}

export default function CostView() {
  const [searchParams] = useSearchParams()
  const [tab, setTab] = useState<'versions' | 'lines'>('versions')
  const [versions, setVersions] = useState<BillVersion[]>([])
  const [versionTotal, setVersionTotal] = useState(0)
  const [versionPage, setVersionPage] = useState(1)
  const [versionFilters, setVersionFilters] = useState<Record<string, string>>({
    month: searchParams.get('month') ?? '',
    table: searchParams.get('table') ?? '',
  })
  const [lines, setLines] = useState<BillLine[]>([])
  const [lineTotal, setLineTotal] = useState(0)
  const [linePage, setLinePage] = useState(1)
  const [lineFilters, setLineFilters] = useState<LineFilters>({
    month: searchParams.get('month') ?? '',
    table: searchParams.get('table') ?? '',
  })
  const [options, setOptions] = useState<{ 统计月份: string[]; 目标表名: string[] }>({ 统计月份: [], 目标表名: [] })
  const [history, setHistory] = useState<BillVersion[] | null>(null)
  const [detail, setDetail] = useState<BillVersion | null>(null)
  const [generateForm, setGenerateForm] = useState<Record<string, string>>({
    统计月份: searchParams.get('month') ?? '2026-09',
    数据表名: searchParams.get('table') ?? '',
  })
  const [showGenerate, setShowGenerate] = useState(false)
  const [errorMessage, setErrorMessage] = useState('')
  const size = 10

  const loadVersions = useCallback(async () => {
    const query = new URLSearchParams()
    Object.entries(versionFilters).forEach(([key, value]) => value && query.set(key, value))
    query.set('page', String(versionPage))
    query.set('size', String(size))
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) throw new Error('账单版本读取失败')
    const payload = (await response.json()) as PageResult<BillVersion>
    setVersions(payload.items)
    setVersionTotal(payload.total)
  }, [versionFilters, versionPage])

  const loadLines = useCallback(async () => {
    const query = new URLSearchParams()
    Object.entries(lineFilters).forEach(([key, value]) => {
      if (value === true || (typeof value === 'string' && value)) query.set(key, String(value))
    })
    query.set('page', String(linePage))
    query.set('size', String(size))
    const response = await request(`${ENDPOINT}/lines?${query}`)
    if (!response.ok) throw new Error('账单明细读取失败')
    const payload = (await response.json()) as PageResult<BillLine>
    setLines(payload.items)
    setLineTotal(payload.total)
  }, [lineFilters, linePage])

  const loadOptions = useCallback(async () => {
    const response = await request(`${ENDPOINT}/filters`)
    if (response.ok) setOptions(await response.json())
  }, [])

  useEffect(() => { void loadOptions() }, [loadOptions])
  useEffect(() => { loadVersions().catch((e) => setErrorMessage(e.message)) }, [loadVersions])
  useEffect(() => { loadLines().catch((e) => setErrorMessage(e.message)) }, [loadLines])

  const stats = useMemo(() => {
    const current = versions.filter((v) => v.is_current)
    return [
      { label: '当前账单版本数', value: current.length },
      { label: '当前版本计划扫描 GB', value: current.reduce((s, v) => s + v.plan_scan_gb, 0) },
      { label: '实际扫描 GB（历史保留）', value: current.reduce((s, v) => s + v.actual_scan_gb, 0) },
      { label: '重试扫描 GB', value: current.reduce((s, v) => s + v.retry_scan_gb, 0) },
    ]
  }, [versions])

  async function openHistory(table: string, month: string) {
    const response = await request(`${ENDPOINT}/history?table=${encodeURIComponent(table)}&month=${encodeURIComponent(month)}`)
    if (response.ok) {
      const payload = await response.json()
      setHistory(payload.items as BillVersion[])
    }
  }

  async function openDetail(version: BillVersion) {
    const response = await request(`${ENDPOINT}/${version.id}`)
    if (response.ok) setDetail((await response.json()) as BillVersion)
  }

  function viewVersionLines(version: BillVersion) {
    setHistory(null)
    setLineFilters({ version_id: version.id, month: version.统计月份, table: version.数据表名 })
    setLinePage(1)
    setTab('lines')
  }

  async function runAction(action: string, version: BillVersion) {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/${version.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ values: { action } }),
      })
      const payload = await response.json()
      if (!response.ok || !payload.ok) throw new Error(payload.message ?? '账单动作未生效')
      await loadVersions()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '账单操作失败')
    }
  }

  async function generate() {
    setErrorMessage('')
    try {
      const response = await request(ENDPOINT, { method: 'POST', body: JSON.stringify({ values: generateForm }) })
      const payload = await response.json()
      if (!response.ok || !payload.ok) throw new Error(payload.message ?? '账单生成失败')
      setShowGenerate(false)
      setVersionPage(1)
      await loadOptions()
      await loadVersions()
      await loadLines()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '账单生成失败')
    }
  }

  return (
    <section className="page" data-module="cost">
      <header className="page-head">
        <div>
          <h2>存储成本管理</h2>
          <p className="page-desc">
            账单与补数共用扫描范围结论；同一月份同一目标表仅一个当前版本，重复生成产生新版本，旧版本可追溯。
          </p>
        </div>
        <div className="page-actions">
          <Link className="btn" to="/backfill">← 查看历史补数</Link>
          <button className="btn primary" type="button" onClick={() => setShowGenerate(true)}>生成 / 重新生成账单</button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>导出账单版本</button>
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

      <div className="tabs">
        <button className={`tab ${tab === 'versions' ? 'active' : ''}`} type="button" onClick={() => setTab('versions')}>账单版本</button>
        <button className={`tab ${tab === 'lines' ? 'active' : ''}`} type="button" onClick={() => setTab('lines')}>账单明细（按日期定位待处理与重试）</button>
      </div>

      {tab === 'versions' && (
        <>
          <form className="filter-bar" onSubmit={(e) => { e.preventDefault(); setVersionPage(1); void loadVersions() }}>
            <label className="filter-item"><span>账单编号</span>
              <input placeholder="按账单编号" value={versionFilters.keyword ?? ''} onChange={(e) => setVersionFilters((p) => ({ ...p, keyword: e.target.value }))} />
            </label>
            <label className="filter-item"><span>统计月份</span>
              <select value={versionFilters.month ?? ''} onChange={(e) => setVersionFilters((p) => ({ ...p, month: e.target.value }))}>
                <option value="">全部</option>{options.统计月份.map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
            </label>
            <label className="filter-item"><span>目标表</span>
              <select value={versionFilters.table ?? ''} onChange={(e) => setVersionFilters((p) => ({ ...p, table: e.target.value }))}>
                <option value="">全部</option>{options.目标表名.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
            </label>
            <label className="filter-item"><span>状态</span>
              <select value={versionFilters.status ?? ''} onChange={(e) => setVersionFilters((p) => ({ ...p, status: e.target.value }))}>
                <option value="">全部</option>{['已生成', '已核对', '已归档'].map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
            </label>
            <button className="btn" type="submit">查询</button>
            <button className="btn ghost" type="button" onClick={() => { setVersionFilters({}); setVersionPage(1) }}>重置</button>
          </form>
          <table className="data-table">
            <thead>
              <tr>
                <th>账单编号 / 版本</th><th>月份</th><th>目标表</th><th>版本</th><th>exec_id</th>
                <th>计划/实际/重试 GB</th><th>覆盖天数</th><th>水位变化</th><th>失效计划 GB</th><th>状态</th><th>操作</th>
              </tr>
            </thead>
            <tbody>
              {versions.map((v) => (
                <tr key={v.id}>
                  <td><button className="link" type="button" onClick={() => void openDetail(v)}>{v.账单编号}</button></td>
                  <td>{v.统计月份}</td>
                  <td>{v.数据表名}</td>
                  <td><span className={v.is_current ? 'badge badge-current' : 'badge badge-history'}>{v.is_current ? '当前 V' : '历史 V'}{v.版本号}</span></td>
                  <td>{v.关联exec_id ?? '—'}</td>
                  <td>{v.plan_scan_gb} / {v.actual_scan_gb} / {v.retry_scan_gb}</td>
                  <td>{v.covered_date_count}</td>
                  <td style={{ fontSize: 12 }}>{v.watermark_before ?? '—'} → {v.watermark_after ?? '—'}</td>
                  <td>{v.失效计划量GB}</td>
                  <td>{v.status}</td>
                  <td className="row-actions">
                    <button className="link" type="button" onClick={() => void openHistory(v.数据表名, v.统计月份)}>版本历史</button>
                    <button className="link" type="button" onClick={() => viewVersionLines(v)}>查看明细</button>
                    {v.is_current && versionActions.map((action) => (
                      <button key={action} className="link" type="button" onClick={() => void runAction(action, v)}>{action}</button>
                    ))}
                  </td>
                </tr>
              ))}
              {versions.length === 0 && <tr><td className="empty-state" colSpan={11}>暂无账单版本，可先在补数页登记扫描批次或手工生成账单</td></tr>}
            </tbody>
          </table>
          <Pager page={versionPage} size={size} total={versionTotal} onPage={setVersionPage} />
        </>
      )}

      {tab === 'lines' && (
        <>
          <form className="filter-bar" onSubmit={(e) => { e.preventDefault(); setLinePage(1); void loadLines() }}>
            <label className="filter-item"><span>统计月份</span>
              <select value={lineFilters.month ?? ''} onChange={(e) => setLineFilters((p) => ({ ...p, month: e.target.value, version_id: undefined }))}>
                <option value="">全部</option>{options.统计月份.map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
            </label>
            <label className="filter-item"><span>目标表</span>
              <select value={lineFilters.table ?? ''} onChange={(e) => setLineFilters((p) => ({ ...p, table: e.target.value, version_id: undefined }))}>
                <option value="">全部</option>{options.目标表名.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
            </label>
            <label className="filter-item"><span>覆盖策略</span>
              <select value={lineFilters.policy ?? ''} onChange={(e) => setLineFilters((p) => ({ ...p, policy: e.target.value }))}>
                <option value="">全部</option>{policies.map((p) => <option key={p} value={p}>{p}</option>)}
              </select>
            </label>
            <label className="filter-item"><span>行状态</span>
              <select value={lineFilters.line_status ?? ''} onChange={(e) => setLineFilters((p) => ({ ...p, line_status: e.target.value }))}>
                <option value="">全部</option>{lineStatuses.map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
            </label>
            <label className="filter-item"><span>仅重试扫描</span>
              <input type="checkbox" checked={!!lineFilters.retry_only} onChange={(e) => setLineFilters((p) => ({ ...p, retry_only: e.target.checked }))} />
            </label>
            <button className="btn" type="submit">查询</button>
            <button className="btn ghost" type="button" onClick={() => { setLineFilters({}); setLinePage(1) }}>重置（回到当前版本）</button>
          </form>
          <table className="data-table">
            <thead>
              <tr>
                <th>日期</th><th>目标表</th><th>行状态</th><th>覆盖策略</th><th>来源任务</th><th>来源批次 exec_id</th>
                <th>采用区间</th><th>重试</th><th>当日扫描 GB</th><th>计划扫描 GB</th><th>说明</th>
              </tr>
            </thead>
            <tbody>
              {lines.map((line) => (
                <tr key={line.id}>
                  <td>{line.日期}</td>
                  <td>{line.目标表名}</td>
                  <td><StatusBadge status={line.行状态} /></td>
                  <td>{line.覆盖策略 ?? '—'}</td>
                  <td>{line.来源任务编号 ?? '—'}</td>
                  <td style={{ fontSize: 12 }}>{line.来源批次 ?? '—'}</td>
                  <td style={{ fontSize: 12 }}>{line.采用区间 ?? '—'}</td>
                  <td>{line.是否重试扫描 ? <span className="badge badge-failed">重试×{line.重试次数}</span> : '—'}</td>
                  <td>{line.扫描量GB}</td>
                  <td>{line.计划扫描量GB}</td>
                  <td style={{ fontSize: 12 }}>{line.失效原因 ?? (line.行状态 === '待处理' ? '等待补数扫描' : '')}</td>
                </tr>
              ))}
              {lines.length === 0 && <tr><td className="empty-state" colSpan={11}>没有符合条件的账单明细</td></tr>}
            </tbody>
          </table>
          <Pager page={linePage} size={size} total={lineTotal} onPage={setLinePage} />
        </>
      )}

      <footer className="page-foot">
        <span>同一月份仅一个当前账单版本生效；未执行计划随水位推进立即失效，历史已执行量随旧版本保留</span>
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>

      {history && (
        <Modal title={`版本历史 · ${history[0]?.数据表名 ?? ''} ${history[0]?.统计月份 ?? ''}`} onClose={() => setHistory(null)}>
          <table className="data-table subtable">
            <thead><tr><th>版本</th><th>账单编号</th><th>exec_id</th><th>计划/实际/重试</th><th>覆盖天数</th><th>水位 before→after</th><th>状态</th></tr></thead>
            <tbody>
              {history.map((v) => (
                <tr key={v.id}>
                  <td>V{v.版本号} {v.is_current ? '（当前）' : ''}</td>
                  <td>{v.账单编号}</td>
                  <td>{v.关联exec_id ?? '—'}</td>
                  <td>{v.plan_scan_gb}/{v.actual_scan_gb}/{v.retry_scan_gb}</td>
                  <td>{v.covered_date_count}</td>
                  <td>{v.watermark_before ?? '—'}→{v.watermark_after ?? '—'}</td>
                  <td>{v.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="modal-foot">
            <button className="btn" type="button" onClick={() => history[0] && viewVersionLines(history[0])}>查看当前版本明细</button>
          </div>
        </Modal>
      )}

      {detail && (
        <Modal title={`账单版本详情 · ${detail.账单编号}`} onClose={() => setDetail(null)}>
          <div className="metric-grid">
            <div className="metric-card"><span className="metric-label">exec_id</span><span className="metric-value" style={{ fontSize: 14 }}>{detail.关联exec_id ?? '—'}</span></div>
            <div className="metric-card"><span className="metric-label">plan_scan_gb</span><span className="metric-value">{detail.plan_scan_gb}</span></div>
            <div className="metric-card"><span className="metric-label">actual_scan_gb</span><span className="metric-value">{detail.actual_scan_gb}</span></div>
            <div className="metric-card"><span className="metric-label">retry_scan_gb</span><span className="metric-value">{detail.retry_scan_gb}</span></div>
            <div className="metric-card"><span className="metric-label">covered_date_count</span><span className="metric-value">{detail.covered_date_count}</span></div>
            <div className="metric-card"><span className="metric-label">水位变化</span><span className="metric-value" style={{ fontSize: 13 }}>{detail.watermark_before ?? '—'} → {detail.watermark_after ?? '—'}</span></div>
          </div>
          <p className="page-desc">生成原因：{detail.生成原因}；失效计划量 {detail.失效计划量GB} GB；存储成本 {detail.存储成本} 元，计算成本 {detail.计算成本} 元。</p>
          <table className="data-table subtable">
            <thead><tr><th>日期</th><th>状态</th><th>策略</th><th>来源任务</th><th>采用区间</th><th>重试</th><th>扫描GB</th></tr></thead>
            <tbody>
              {(detail.明细 ?? []).map((line) => (
                <tr key={line.id}>
                  <td>{line.日期}</td><td><StatusBadge status={line.行状态} /></td><td>{line.覆盖策略 ?? '—'}</td>
                  <td>{line.来源任务编号 ?? '—'}</td><td>{line.采用区间 ?? '—'}</td>
                  <td>{line.是否重试扫描 ? line.重试次数 : '—'}</td><td>{line.扫描量GB}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Modal>
      )}

      {showGenerate && (
        <Modal title="生成 / 重新生成账单（同月同表只保留一个当前版本）" onClose={() => setShowGenerate(false)}>
          <div className="form-grid">
            <label>统计月份<input type="month" value={generateForm.统计月份} onChange={(e) => setGenerateForm((p) => ({ ...p, 统计月份: e.target.value }))} /></label>
            <label>目标表名
              <select value={generateForm.数据表名} onChange={(e) => setGenerateForm((p) => ({ ...p, 数据表名: e.target.value }))}>
                <option value="">请选择</option>{options.目标表名.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
            </label>
          </div>
          <p className="page-desc">重复生成不会产生第二份生效账单：旧当前版本转为历史归档，新版本记录最新 exec_id 与水位前后值。</p>
          <div className="modal-foot">
            <button className="btn ghost" type="button" onClick={() => setShowGenerate(false)}>取消</button>
            <button className="btn primary" type="button" onClick={() => void generate()}>生成当前版本</button>
          </div>
        </Modal>
      )}
    </section>
  )
}
