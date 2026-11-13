import type { ReactNode } from 'react'

export function Pager(props: { page: number; size: number; total: number; onPage: (page: number) => void }) {
  const pages = Math.max(1, Math.ceil(props.total / props.size))
  return (
    <div className="pager">
      <span>共 {props.total} 条 · 第 {props.page}/{pages} 页</span>
      <button
        className="btn small"
        type="button"
        disabled={props.page <= 1}
        onClick={() => props.onPage(props.page - 1)}
      >
        上一页
      </button>
      <button
        className="btn small"
        type="button"
        disabled={props.page >= pages}
        onClick={() => props.onPage(props.page + 1)}
      >
        下一页
      </button>
    </div>
  )
}

export function Modal(props: { title: string; onClose: () => void; children: ReactNode }) {
  return (
    <div className="modal-mask" onClick={props.onClose}>
      <div className="modal" onClick={(event) => event.stopPropagation()}>
        <div className="modal-head">
          <h3>{props.title}</h3>
          <button className="btn ghost small" type="button" onClick={props.onClose}>关闭</button>
        </div>
        {props.children}
      </div>
    </div>
  )
}

export function StatusBadge(props: { status: string }) {
  const map: Record<string, string> = {
    已完成: 'badge badge-done',
    待处理: 'badge badge-pending',
    已失效: 'badge badge-invalid',
    失败: 'badge badge-failed',
    成功: 'badge badge-done',
  }
  return <span className={map[props.status] ?? 'badge'}>{props.status}</span>
}
