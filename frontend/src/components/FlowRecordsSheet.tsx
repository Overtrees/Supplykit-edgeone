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
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [skuQ, setSkuQ] = useState('')

  const PS = 50
  const WT_LABEL: Record<string, string> = { own: '自有仓', platform: '平台仓', platform_b: 'B仓' }

  const load = async (t: 'inbound' | 'outbound', p: number, append = false) => {
    setLoading(true)
    setErr('')
    try {
      const r = await api.get(
        `/api/flow-records/list?target=${t}&page=${p}&page_size=${PS}&channel=${channel}` +
          (dateFrom ? `&date_from=${dateFrom}` : '') +
          (dateTo ? `&date_to=${dateTo}` : '') +
          (skuQ ? `&sku=${encodeURIComponent(skuQ)}` : '') +
          `&t=${Date.now()}`,
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
  }, [open, tab, channel, dateFrom, dateTo, skuQ])

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
            maxHeight: '88dvh',
            display: 'flex',
            flexDirection: 'column',
            pointerEvents: 'auto',
          }}
        >
          {/* 标题 + 两态 tab */}
          <div className="flow-head">
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

          {/* 筛选栏: 日期范围 + SKU 模糊(后端已支持, 触发重载) */}
          {/* 筛选栏(2026-10-29): 日期一行 + 搜索独立一行, 样式走全局类 flow-filter-* */}
          <div className="flow-filter-row">
            <input
              type="date"
              value={dateFrom}
              onChange={e => setDateFrom(e.target.value)}
              className="tpl-field flow-filter-field"
            />
            <span className="flow-filter-tilde">~</span>
            <input
              type="date"
              value={dateTo}
              onChange={e => setDateTo(e.target.value)}
              className="tpl-field flow-filter-field"
            />
          </div>
          <div className="flow-filter-row">
            <input
              placeholder="搜索 SKU"
              value={skuQ}
              onChange={e => setSkuQ(e.target.value)}
              className="tpl-field flow-filter-field-full"
            />
          </div>

          {err && (
            <div style={{ fontSize: 'var(--font-xs)', color: 'var(--danger)', marginBottom: 6 }}>
              {err}
            </div>
          )}

          {/* 表格 */}
          <div
            className="flow-table-wrap"
            onScroll={e => {
              const el = e.currentTarget
              // 滚动到底 80px 内 → 自动续页(替换原"加载更多"按钮)
              if (!loading && items.length < total && el.scrollHeight - el.scrollTop - el.clientHeight < 80) {
                setPage(pg => pg + 1)
              }
            }}
          >
            <table className="flow-table">
              <thead>
                <tr>
                  {[
                    '日期',
                    'SKU',
                    '仓库',
                    '生产日期',
                    '截止日期',
                    '数量',
                    extraLabel,
                    '备注',
                    '',
                  ].map((h, i) => (
                    <th key={i}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {items.map(it => (
                  <tr key={it.id}>
                    <td>
                      {String(it.date || '').slice(0, 10)}
                      <span className="muted2" style={{ marginLeft: 3, fontSize: 'var(--font-9)' }}>
                        {String(it.date || '').slice(11, 16)}
                      </span>
                    </td>
                    <td>{it.sku}</td>
                    <td>{it.warehouse || WT_LABEL[it.warehouse_type] || '—'}</td>
                    <td>{it.prod_date || '—'}</td>
                    <td>{it.exp_date || '—'}</td>
                    <td>{it.quantity}</td>
                    <td className="ellip">{it.extra || '—'}</td>
                    <td className="ellip">{it.remark || '—'}</td>
                    <td>
                      <button
                        className="tpl-btn-ghost clickable flow-del-btn"
                        onClick={() => doDelete(it.id)}
                        title="删除"
                      >
                        <IconTrash size={13} />
                      </button>
                    </td>
                  </tr>
                ))}
                {!items.length && !loading && (
                  <tr>
                    <td colSpan={9} className="flow-empty">
                      {dateFrom || dateTo || skuQ
                        ? '当前筛选无匹配记录，可调整日期或清空搜索'
                        : '暂无记录'}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          {/* 底部状态: 滚动到底自动加载(原"加载更多"按钮占 60px 已省出给表格) */}
          {loading && (
            <div className="flow-foot">加载中…</div>
          )}
          {!loading && items.length >= total && items.length > 0 && (
            <div className="flow-foot">已全部加载（{total} 条）</div>
          )}
        </div>
      </div>
    </>,
    document.body,
  )
}
