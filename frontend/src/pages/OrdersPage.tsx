import React, { useState, useEffect, useRef, useMemo } from 'react'
import { apiJson } from '../api/client'
import { useAppStore } from '../store/useAppStore'
import { clearCache, abortScope } from '../api/client'
import { useFreshness, StaleDot } from '../components/Freshness'
import EmptyState from '../components/EmptyState'
import ErrorRetry from '../components/ErrorRetry'
import { useToast } from '../components/Toast'
import ConfirmDialog from '../components/ConfirmDialog'
import { IconCircle, IconCircleCheck } from '../components/Icons'
import { colLabel } from '../components/colLabels'
import { orderStatusLabel, orderStatusTone } from '../components/statusLabels'
import { codeColWidth } from '../components/hammer/codeCell'
import { t } from '../locale'

// 列只存 id: 表头渲染走 colLabel(col.id), 列选择面板同源(configs.ts 的 ORDER_COLS)
const COLS = [
  { id: 'date' },
  { id: 'brand' },
  { id: 'order_no' },
  { id: 'barcode' },
  { id: 'store' },
  { id: 'warehouse' },
  { id: 'product' },
  { id: 'quantity' },
  { id: 'unit_price' },
  { id: 'amount' },
  { id: 'status' },
  { id: 'paid_at' },
]
const COL_KEY = () => 'c_cols_orders_' + (useAppStore.getState().channel || 'jd')
const getVis = () => {
  try {
    return JSON.parse(localStorage.getItem(COL_KEY()) || 'null')
  } catch {
    return null
  }
}

function OrderSkeleton() {
  return (
    <div>
      {[1, 2, 3, 4, 5].map(i => (
        <div
          key={i}
          style={{
            display: 'flex',
            gap: 8,
            padding: '8px 0',
            borderBottom: '1px solid var(--border)',
          }}
        >
          <div className="skeleton" style={{ width: 80, height: 14 }} />
          <div className="skeleton" style={{ width: 60, height: 14 }} />
          <div className="skeleton" style={{ width: 40, height: 14 }} />
          <div className="skeleton" style={{ flex: 1, height: 14 }} />
          <div className="skeleton" style={{ width: 36, height: 14 }} />
          <div className="skeleton" style={{ width: 36, height: 14 }} />
          <div className="skeleton" style={{ width: 50, height: 14 }} />
        </div>
      ))}
    </div>
  )
}

export default function OrdersPage() {
  const toast = useToast()
  const {
    orders,
    orderPage,
    orderLoading,
    setOrderPage,
    orderStatus,
    dataLoaded,
    channel,
    hammerCols,
    hammerSearch,
    orderTotal,
    setHammerSearch,
    orderLoadErr,
    orderBatch,
    orderSelIds,
    setOrderSelIds,
    setOrderBatch,
  } = useAppStore()
  useEffect(() => {
    const hSelAll = () => {
      // 全选(当前页)——与商品页 prodBatchAllReq 同款
      const all = orders.map(x => x.id)
      setOrderSelIds(orderSelIds.length === all.length && all.length > 0 ? [] : all)
    }
    const hChanged = () => {
      // 批量操作后清 orders 缓存再刷新(30s 内存缓存曾致 total 旧值)
      clearCache('orders')
      useAppStore.getState().loadAll()
    }
    window.addEventListener('orders-toggle-all', hSelAll)
    window.addEventListener('orders-changed', hChanged)
    return () => {
      window.removeEventListener('orders-toggle-all', hSelAll)
      window.removeEventListener('orders-changed', hChanged)
    }
  }, [orders, orderSelIds, setOrderSelIds])
  useEffect(() => {
    clearCache('orders')
    useAppStore.getState().loadAll(1)
    setHammerSearch('')
  }, [channel])
  useEffect(() => {
    // 2026-11-07: 只刷订单(原来 loadAll 会连带 summary/stock-risk/alerts/质检 6~8 个接口)
    const st = useAppStore.getState()
    st.loadOrdersOnly(1, st.hammerSearch || '', st.orderStatus || '')
  }, [hammerSearch, orderStatus])
  const [confirmDel, setConfirmDel] = useState(null)
  useEffect(() => () => abortScope('orders'), [])

  // 删除撤销定时器集合：卸载时统一清理，防止软删订单残留
  const timersRef = useRef([])
  useEffect(
    () => () => {
      timersRef.current.forEach(clearTimeout)
      timersRef.current = []
    },
    [],
  )
  const [visCols, setVisCols] = useState(() => getVis() || COLS.map(c => c.id))
  const fresh = useFreshness(useAppStore.getState().channel) // 2026-10-29 数据新鲜度
  useEffect(() => {
    if (hammerCols?.orders) setVisCols(hammerCols.orders)
  }, [hammerCols])
  const totalPages = Math.max(1, Math.ceil((orderTotal || 0) / 30))
  const s = hammerSearch || ''
  const st = orderStatus || ''

  // 码值列(订单号/条码)宽度按**当前这一页数据里最长的那串**算:
  // 2026-11-04 订单页订单号 164px 撞 .col-sku 的 max-width 140px, 且 td.mono 是
  // nowrap + overflow:visible, 文字直接叠印到右边条码上。现在按内容给宽, 不再钉死。
  const codeW = useMemo(() => {
    const out: Record<string, ReturnType<typeof codeColWidth>> = {}
    for (const id of visCols) {
      if (id !== 'order_no' && id !== 'barcode') continue
      out[id] = codeColWidth(orders.map(x => (id === 'order_no' ? x.order_no : x.barcode)))
    }
    return out
  }, [orders, visCols])

  // 加载平台仓库存（按 SKU+仓库 维度）
  const doBatchOrder = async (action: string, ids: number[]) => {
    try {
      const API = import.meta.env.VITE_API_BASE_URL || ''
      // S3-2 收口: 批量是重操作, 超时 90s
      const d = await apiJson<{ ok?: boolean; error?: string }>('POST', API + '/api/orders/batch', {
        timeout: 90000,
        data: { action, ids },
      })
      if (d.ok) {
        setOrderSelIds([])
        setOrderBatch(false)
        useAppStore.getState().loadAll()
        toast.success(
          action === 'permanent-delete'
            ? t('oms.batch_purged', { n: ids.length })
            : t('oms.batch_deleted', { n: ids.length }),
        )
      } else {
        toast.error(t('oms.action_failed', { msg: d.error || '' }))
      }
    } catch (e) {
      toast.error(t('oms.action_failed', { msg: e.message }))
    }
  }
  const delOrder = async () => {
    if (!confirmDel) return
    const id = confirmDel
    setConfirmDel(null)
    // 合并到批量链路: 软删(deleted_at, 回收站可恢复, 30 天清理 cron 兜底)
    await doBatchOrder('delete', [id])
  }

  return (
    <div>
      {s !== '' && (
        <div
          style={{
            display: 'flex',
            gap: 6,
            alignItems: 'center',
            marginBottom: 8,
            flexWrap: 'wrap',
          }}
        >
          <span className="small muted">{t('oms.search_tag', { q: s })}</span>
          {st && <span className="pill info">{st}</span>}
        </div>
      )}
      <div className="card">
        <div style={{ fontSize: 'var(--font-18)', fontWeight: 'var(--fw-bold)', marginBottom: 8 }}>
          {t('nav.orders')} {/* 2026-10-28 全站统一: 已加载 N/M 条 · 显示 x/y 列(同一行并排) */}
          <span
            className="small muted"
            style={{ fontWeight: 'var(--fw-regular)', fontSize: 'var(--font-xs)' }}
          >
            {t('oms.loaded', {
              a: Math.min(orders.length, orderTotal || orders.length),
              b: orderTotal || orders.length,
              c: visCols.length,
              d: COLS.length,
            })}
            <StaleDot
              level={fresh?.staleLevel || 0}
              days={Math.max(...(fresh?.sources || []).map(s => s.days ?? 0), 0)}
            />
          </span>
          {/* 2026-11-02 S2-4/A: 订单只保留 365 天, 更早的在历史归档 —— 不给入口的话
              用户查一年前订单只会看到"没有数据", 且不知道去哪查 */}
          <span
            className="clickable"
            onClick={() => window.__setPage && window.__setPage('history')}
            style={{
              float: 'right',
              fontSize: 'var(--font-xs)',
              fontWeight: 'var(--fw-regular)',
              color: 'var(--primary)',
              cursor: 'pointer',
            }}
          >
            {t('oms.to_history')}
          </span>
        </div>
        {/* 2026-10-27 无感切回: 有旧数据(store 持久)时先显旧, 后台刷新不闪骨架; 仅无数据可显示才骨架 */}
        {orderLoading && orders.length === 0 ? (
          <OrderSkeleton />
        ) : !dataLoaded && orders.length === 0 ? (
          <OrderSkeleton />
        ) : orders.length === 0 ? (
          orderLoadErr ? (
            <ErrorRetry error={orderLoadErr} onRetry={() => useAppStore.getState().loadAll()} />
          ) : (
            <EmptyState
              icon="clipboard"
              title={s ? t('order.empty_matched') : t('order.empty')}
              desc={s ? t('oms.try_other_kw') : t('oms.import_hint')}
              action={
                !s && (
                  <button
                    className="btn btn-primary"
                    onClick={() => window.__setPage && window.__setPage('cleansing')}
                  >
                    {t('oms.go_import')}
                  </button>
                )
              }
            />
          )
        ) : (
          <div
            key={orderLoading && orders.length === 0 ? 'skeleton' : 'data'}
            className="fade-in"
            style={{ overflow: 'auto', maxHeight: 'calc(100vh - 180px)' }}
          >
            <table>
              <colgroup>
                {visCols.map(id => {
                  const col = COLS.find(c => c.id === id)
                  return col ? <col key={col.id} style={codeW[col.id]?.style} /> : null
                })}
              </colgroup>
              <thead style={{ position: 'sticky', top: 0, background: 'var(--card)', zIndex: 1 }}>
                <tr>
                  {orderBatch && <th style={{ width: 32 }} />}
                  {visCols.map(id => {
                    const col = COLS.find(c => c.id === id)
                    return col ? (
                      <th key={col.id} style={codeW[col.id]?.style}>
                        {colLabel(col.id)}
                      </th>
                    ) : null
                  })}
                </tr>
              </thead>
              <tbody>
                {orders.map(x => {
                  return (
                    <tr
                      key={x.id}
                      style={{
                        background:
                          orderBatch && orderSelIds.includes(x.id) ? 'rgba(29,78,216,0.06)' : '',
                      }}
                    >
                      {orderBatch && (
                        <td
                          style={{ width: 32, textAlign: 'center' }}
                          onClick={e => e.stopPropagation()}
                        >
                          <span
                            onClick={() =>
                              setOrderSelIds(
                                orderSelIds.includes(x.id)
                                  ? orderSelIds.filter(i => i !== x.id)
                                  : [...orderSelIds, x.id],
                              )
                            }
                            style={{
                              cursor: 'pointer',
                              display: 'inline-flex',
                              alignItems: 'center',
                              justifyContent: 'center',
                            }}
                          >
                            {orderSelIds.includes(x.id) ? (
                              <IconCircleCheck size={20} />
                            ) : (
                              <IconCircle size={20} />
                            )}
                          </span>
                        </td>
                      )}
                      {visCols.map(id => {
                        const col = COLS.find(c => c.id === id)
                        if (!col) return null
                        if (col.id === 'order_no')
                          return (
                            <td
                              key={col.id}
                              className="mono col-sku col-code"
                              title={x.order_no || undefined}
                            >
                              {x.order_no}
                            </td>
                          )
                        if (col.id === 'barcode')
                          return (
                            <td
                              key={col.id}
                              className="mono col-code"
                              title={x.barcode || undefined}
                              style={{ fontSize: 'var(--font-xs)' }}
                            >
                              {x.barcode || '-'}
                            </td>
                          )
                        if (col.id === 'store')
                          return (
                            <td key={col.id} className="col-store">
                              {x.store || '-'}
                            </td>
                          )
                        if (col.id === 'brand')
                          return (
                            <td key={col.id} className="col-store">
                              {x.brand || '-'}
                            </td>
                          )
                        if (col.id === 'warehouse')
                          return (
                            <td key={col.id} className="col-store">
                              {x.warehouse || '-'}
                            </td>
                          )
                        if (col.id === 'product')
                          return (
                            <td key={col.id} className="col-name">
                              {x.product_name}
                            </td>
                          )
                        if (col.id === 'quantity')
                          return (
                            <td key={col.id} className="col-qty">
                              {x.quantity}
                            </td>
                          )
                        if (col.id === 'unit_price')
                          return (
                            <td key={col.id} className="col-price">
                              ¥
                              {Number(
                                x.unit_price || x.total_amount / (x.quantity || 1),
                              ).toLocaleString()}
                            </td>
                          )
                        if (col.id === 'amount')
                          return (
                            <td key={col.id} className="col-price">
                              ¥{Number(x.total_amount).toLocaleString()}
                            </td>
                          )
                        if (col.id === 'status')
                          return (
                            <td key={col.id} style={{ textAlign: 'center' }}>
                              <span className={'pill ' + orderStatusTone(x.order_status)}>
                                {orderStatusLabel(x.order_status)}
                              </span>
                            </td>
                          )
                        if (col.id === 'date')
                          return (
                            <td key={col.id} className="col-date">
                              {String(x.ordered_at || '')
                                .replace('T', ' ')
                                .slice(0, 16)}
                            </td>
                          )
                        if (col.id === 'paid_at')
                          return (
                            <td key={col.id} className="col-date">
                              {String(x.paid_at || '')
                                .replace('T', ' ')
                                .slice(0, 16) || '-'}
                            </td>
                          )

                        return (
                          <td
                            key={col.id}
                            className="small muted"
                            style={{ fontSize: 'var(--font-xs)' }}
                          >
                            -
                          </td>
                        )
                      })}
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>{' '}
      {/* end card */}
      <ConfirmDialog
        open={!!confirmDel}
        title={t('oms.confirm_del_title')}
        desc={t('oms.confirm_del_desc')}
        confirmLabel={t('common.delete_btn')}
        onConfirm={delOrder}
        onCancel={() => setConfirmDel(null)}
      />
      {orderTotal > 30 && (
        <div
          style={{
            display: 'flex',
            justifyContent: 'center',
            alignItems: 'center',
            gap: 8,
            marginTop: 12,
            flexWrap: 'wrap',
          }}
        >
          <button
            onClick={() => setOrderPage(1)}
            disabled={orderPage <= 1}
            className="page-btn"
            style={{ fontSize: 'var(--font-sm)' }}
          >
            ‹‹
          </button>
          <button
            onClick={() => setOrderPage(orderPage - 1)}
            disabled={orderPage <= 1}
            className="page-btn"
            style={{ fontSize: 'var(--font-md)' }}
          >
            ‹
          </button>
          <span className="small muted" style={{ fontSize: 'var(--font-sm)' }}>
            {t('oms.page_of', { a: orderPage, b: totalPages })}
          </span>
          <button
            onClick={() => setOrderPage(orderPage + 1)}
            disabled={orderPage >= totalPages}
            className="page-btn"
            style={{ fontSize: 'var(--font-md)' }}
          >
            ›
          </button>
          <button
            onClick={() => setOrderPage(totalPages)}
            disabled={orderPage >= totalPages}
            className="page-btn"
            style={{ fontSize: 'var(--font-sm)' }}
          >
            ››
          </button>
          <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            <span className="small muted">{t('oms.jump_to')}</span>
            <input
              type="number"
              min={1}
              max={totalPages}
              defaultValue={orderPage}
              onKeyDown={e => {
                if (e.key === 'Enter') {
                  const v = parseInt((e.target as HTMLInputElement).value)
                  if (v >= 1 && v <= totalPages) setOrderPage(v)
                }
              }}
              style={{
                width: 50,
                fontSize: 'var(--font-sm)',
                padding: '9px 12px',
                minHeight: 32,
                border: 'none',
                borderRadius: 'var(--radius-lg)',
                textAlign: 'center',
                background: 'var(--card)',
                color: 'var(--text)',
                boxSizing: 'border-box',
                outline: 'none',
              }}
            />
            <span className="small muted">{t('oms.page_suffix')}</span>
          </span>
        </div>
      )}
    </div>
  )
}
