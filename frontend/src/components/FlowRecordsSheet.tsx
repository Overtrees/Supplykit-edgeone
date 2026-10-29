import React, { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { api } from '../api/client'
import { IconTrash } from './Icons'

/**
 * 出入库明细弹窗(2026-10-29)
 * 复用底部 sheet 标准: 遮罩 fixed inset:0 zIndex 9998 / 面板 material-regular zIndex 9999
 * 标题两态 pill 切换 入库/出库; 行内删除走 /api/flow-records/batch(流水表无软删)
 */
export default function FlowRecordsSheet({
  open,
  onClose,
  channel = 'jd',
}: {
  open: boolean
  onClose: () => void
  channel?: string
}) {
  const [tab, setTab] = useState<'inbound' | 'outbound'>('inbound')
  const [items, setItems] = useState<any[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')
  const [extraLabel, setExtraLabel] = useState('供应商')
  const [delId, setDelId] = useState<number | null>(null)

  const PS = 50

  const load = async (t: 'inbound' | 'outbound', p: number, append = false) => {
    setLoading(true)
    setErr('')
    try {
      const r = await api.get(
        `/api/flow-records/list?target=${t}&page=${p}&page_size=${PS}&channel=${channel}&t=${Date.now()}`,
        { timeout: 60000 },
      )
      const d = r.data || {}
      setItems(prev => (append ? [...prev, ...(d.items || [])] : d.items || []))
      setTotal(d.total || 0)
      setExtraLabel(d.extra_label || (t === 'inbound' ? '供应商' : '目标仓'))
      setPage(p)
    } catch (e: any) {
      setErr(String(e?.message || e).slice(0, 80))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (open) {
      setItems([])
      setTotal(0)
      load(tab, 1)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, tab, channel])

  const doDelete = async (id: number) => {
    setDelId(id)
    try {
      const r: any = await api.post('/api/flow-records/batch', {
        target: tab,
        ids: [id],
        reason: '出入库明细弹窗手动删除',
      })
      if (r?.data?.ok === false) throw new Error(r?.data?.error || '删除失败')
      setItems(prev => prev.filter(x => x.id !== id))
      setTotal(t => Math.max(0, t - 1))
    } catch (e: any) {
      setErr(String(e?.message || e).slice(0, 80))
    } finally {
      setDelId(null)
    }
  }

  if (!open) return null

  return createPortal(
    <>
      <div
        style={{ position: 'fixed', inset: 0, zIndex: 9998, background: 'transparent' }}
        onClick={onClose}
      />
      <div
        style={{
          position: 'fixed',
          left: 0,
          right: 0,
          bottom: 'calc(env(safe-area-inset-bottom) + 14px)',
          zIndex: 9999,
          display: 'flex',
          justifyContent: 'center',
          padding: '0 14px',
          pointerEvents: 'none',
        }}
      >
        <div
          className="material-regular"
          style={{
            width: '100%',
            maxWidth: 680,
            borderRadius: 'var(--radius-lg)',
            padding: 16,
            maxHeight: '70vh',
            display: 'flex',
            flexDirection: 'column',
            pointerEvents: 'auto',
          }}
        >
          {/* 标题 + 两态 tab */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
            <div style={{ fontSize: 16, fontWeight: 700 }}>出入库明细</div>
            <div className="hammer-segmented" style={{ marginLeft: 4 }}>
              {(
                [
                  ['inbound', '入库'],
                  ['outbound', '出库'],
                ] as const
              ).map(([k, l]) => (
                <span
                  key={k}
                  onClick={() => setTab(k as any)}
                  className={'hammer-segment' + (tab === k ? ' active' : '')}
                  style={{ fontSize: 'var(--font-xs)', cursor: 'pointer' }}
                >
                  {l}
                </span>
              ))}
            </div>
            <span
              className="muted2"
              style={{ fontSize: 'var(--font-xs)', marginLeft: 'auto', whiteSpace: 'nowrap' }}
            >
              共 {total} 条
            </span>
          </div>

          {err && (
            <div style={{ fontSize: 'var(--font-xs)', color: 'var(--danger)', marginBottom: 6 }}>
              {err}
            </div>
          )}

          {/* 表格 */}
          <div style={{ overflow: 'auto', flex: 1, minHeight: 200 }}>
            <table
              style={{ width: '100%', borderCollapse: 'collapse', fontSize: 'var(--font-xs)' }}
            >
              <thead>
                <tr style={{ position: 'sticky', top: 0, background: 'var(--card)', zIndex: 1 }}>
                  {['日期', 'SKU', '生产日期', '截止日期', '数量', extraLabel, '备注', ''].map(
                    (h, i) => (
                      <th
                        key={i}
                        style={{
                          textAlign: i === 4 || i === 7 ? 'right' : 'left',
                          padding: '6px 6px',
                          fontWeight: 600,
                          color: 'var(--muted)',
                          whiteSpace: 'nowrap',
                          borderBottom: '1px solid var(--border)',
                        }}
                      >
                        {h}
                      </th>
                    ),
                  )}
                </tr>
              </thead>
              <tbody>
                {items.map(it => (
                  <tr key={it.id} style={{ borderBottom: '1px solid var(--border)' }}>
                    <td style={{ padding: '6px 6px', whiteSpace: 'nowrap' }}>
                      {String(it.date || '').slice(0, 10)}
                      <span className="muted2" style={{ marginLeft: 3, fontSize: 9 }}>
                        {String(it.date || '').slice(11, 16)}
                      </span>
                    </td>
                    <td style={{ padding: '6px 6px', whiteSpace: 'nowrap' }}>{it.sku}</td>
                    <td style={{ padding: '6px 6px', whiteSpace: 'nowrap' }}>
                      {it.prod_date || '—'}
                    </td>
                    <td style={{ padding: '6px 6px', whiteSpace: 'nowrap' }}>
                      {it.exp_date || '—'}
                    </td>
                    <td
                      style={{
                        padding: '6px 6px',
                        textAlign: 'right',
                        fontVariantNumeric: 'tabular-nums',
                      }}
                    >
                      {it.quantity}
                    </td>
                    <td
                      style={{
                        padding: '6px 6px',
                        maxWidth: 120,
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}
                    >
                      {it.extra || '—'}
                    </td>
                    <td
                      style={{
                        padding: '6px 6px',
                        maxWidth: 120,
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                        color: 'var(--muted2)',
                      }}
                    >
                      {it.remark || '—'}
                    </td>
                    <td style={{ padding: '2px 4px', textAlign: 'right' }}>
                      <button
                        className="tpl-btn-ghost clickable"
                        style={{ width: 28, height: 28, padding: 0 }}
                        disabled={delId === it.id}
                        onClick={() => doDelete(it.id)}
                        title="删除该记录"
                      >
                        <IconTrash size={12} />
                      </button>
                    </td>
                  </tr>
                ))}
                {!items.length && !loading && (
                  <tr>
                    <td
                      colSpan={7}
                      style={{ padding: 20, textAlign: 'center', color: 'var(--muted2)' }}
                    >
                      暂无记录
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          {/* 加载更多 + 关闭 */}
          <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
            {items.length < total && (
              <button
                className="tpl-btn clickable"
                style={{ flex: 1 }}
                disabled={loading}
                onClick={() => load(tab, page + 1, true)}
              >
                {loading ? '加载中…' : `加载更多（${items.length}/${total}）`}
              </button>
            )}
            <button className="tpl-btn clickable" style={{ flex: 1 }} onClick={onClose}>
              关闭
            </button>
          </div>
        </div>
      </div>
    </>,
    document.body,
  )
}
