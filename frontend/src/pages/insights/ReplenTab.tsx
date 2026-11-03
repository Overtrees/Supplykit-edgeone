import {
  IconPackage,
  IconTrendDown,
  IconTrendFlat,
  IconTrendUp,
  IconUndo,
} from '../../components/Icons'
import EmptyState from '../../components/EmptyState'
import { StaleDot } from '../../components/Freshness'
import ErrorRetry from '../../components/ErrorRetry'
import { t } from '../../locale'
import { renderNote, Skeleton } from './shared'
import type { MutableRefObject } from 'react'

interface Props {
  /** 列表数据与加载态 */
  replen: any[]
  replenLoading: boolean
  replenTotal: number
  replenLoadingMore: boolean
  replenError: any
  filteredReplen: any[]
  /** 列配置与可见列 */
  currentCols: any[]
  visCols: string[]
  replenMode: string
  /** 订购台账(已下单的行做标记/撤销) */
  orderedKeys: string[]
  orderedItems: any[]
  /** 交互回调: 触底加载/标记已订/撤销/改到货日期, 闭包都在页面 */
  loadReplen: (mode?: string, ch?: string, page?: number) => void
  toggleOrdered: (sku: string, store: string, name?: string, qty?: number) => void
  setArrivalDate: (item: any, date: string) => void
  replenPageRef: MutableRefObject<number>
  insightSearch: string
  globalChannel: string
  /** 新鲜度对象(StaleDot 渲染用) */
  fresh: any
}

/** 补货建议 tab。从 InsightsPage 外提(第四刀, 见 15.137): 全受控, 写操作
 *  (toggleOrdered/setArrivalDate)全部上抛页面, 这里只渲染与触发。
 *  口径: seq 列是建议序号; 已订行 40% 透明 + 可撤销; 到货日期就地编辑。 */
export default function ReplenTab(props: Props) {
  const {
    replen,
    replenLoading,
    replenTotal,
    replenLoadingMore,
    replenError,
    filteredReplen,
    currentCols,
    visCols,
    replenMode,
    orderedKeys,
    orderedItems,
    loadReplen,
    toggleOrdered,
    setArrivalDate,
    replenPageRef,
    insightSearch,
    globalChannel,
    fresh,
  } = props
  return (
    <div className="card">
      <div
        className="section-title"
        style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center' }}
      >
        <span>补货建议</span>
        <span className="muted2" style={{ fontSize: 'var(--font-xs)', fontWeight: 400 }}>
          已加载 {Math.min(replen.length, replenTotal || replen.length)}/
          {replenTotal || replen.length} 条 · 显示 {visCols.length}/{currentCols.length} 列
          {insightSearch ? ` · "${insightSearch}"` : ''}
          <StaleDot
            level={fresh?.staleLevel || 0}
            days={Math.max(...(fresh?.sources || []).map(s => s.days ?? 0), 0)}
          />
        </span>
        {replenMode === 'bbcc' && orderedKeys.length > 0 && (
          <span className="pill success" style={{ fontSize: 'var(--font-10)' }}>
            已下单 {orderedKeys.length} 项
          </span>
        )}
      </div>
      {replenLoading && replen.length === 0 ? (
        <div>
          <Skeleton height={14} width="30%" style={{ marginBottom: 8 }} />
          {[1, 2, 3, 4, 5].map(i => (
            <Skeleton key={i} height={36} style={{ marginBottom: 4 }} />
          ))}
        </div>
      ) : !Array.isArray(replen) || replen.length === 0 ? (
        <div style={{ padding: 12, textAlign: 'center' }}>
          {replenError ? (
            <ErrorRetry error={'加载失败'} onRetry={() => loadReplen(replenMode, globalChannel)} />
          ) : (
            <EmptyState icon="package" title={t('insights.no_replenish')} />
          )}
        </div>
      ) : (
        <div
          key={replenLoading && replen.length === 0 ? 'skeleton' : 'data'}
          className="fade-in"
          style={{ overflow: 'auto', maxHeight: 'calc(100vh - 180px)' }}
        >
          <table>
            <colgroup>
              {visCols.map(id => {
                const col = currentCols.find(c => c.id === id)
                return col ? <col key={col.id} /> : null
              })}
            </colgroup>
            <thead style={{ position: 'sticky', top: 0, background: 'var(--card)', zIndex: 1 }}>
              <tr>
                {visCols.map(id => {
                  const col = currentCols.find(c => c.id === id)
                  return col ? (
                    <th
                      style={{
                        whiteSpace: 'nowrap',
                        fontSize: 'var(--font-xs)',
                        padding: '8px 4px',
                      }}
                      key={col.id}
                    >
                      {col.label}
                    </th>
                  ) : null
                })}
              </tr>
            </thead>
            <tbody>
              {Array.isArray(filteredReplen) &&
                filteredReplen.map((x, i) => {
                  // 已下单压暗仅 BBCC(该 SKU 已在 B 仓入厂批次)——曾未按模式隔离: orderedKeys 渠道级,
                  // 传统模式同 SKU+store 命中被压暗(视图污染); 传统无此业务列(TRAD_COLS), 不应用标记
                  const isOrdered =
                    replenMode === 'bbcc' && orderedKeys.includes(x.sku + '|' + x.store)
                  const rowStyle = isOrdered ? { opacity: 0.55, background: 'var(--bg)' } : {}
                  return (
                    <tr key={i} style={rowStyle}>
                      {visCols.map(id => {
                        const col = currentCols.find(c => c.id === id)
                        if (!col) return <td key={id}></td>
                        // 序号列
                        if (col.id === 'seq')
                          return (
                            <td key={col.id} className="text-11 muted2">
                              {i + 1}
                            </td>
                          )
                        // SKU
                        if (col.id === 'brand')
                          return (
                            <td key={col.id} style={{ fontSize: 'var(--font-sm)' }}>
                              {x.brand || '-'}
                            </td>
                          )
                        if (col.id === 'sku')
                          return (
                            <td
                              key={col.id}
                              className="mono"
                              style={{
                                fontSize: 'var(--font-sm)',
                                textDecoration: isOrdered ? 'line-through' : 'none',
                              }}
                            >
                              {x.sku}
                            </td>
                          )
                        if (col.id === 'barcode')
                          return (
                            <td
                              key={col.id}
                              className="mono"
                              style={{ fontSize: 'var(--font-xs)' }}
                            >
                              {x.barcode || '-'}
                            </td>
                          )
                        // 商品名
                        if (col.id === 'name')
                          return (
                            <td
                              key={col.id}
                              style={{ textDecoration: isOrdered ? 'line-through' : 'none' }}
                            >
                              {x.product_name}
                            </td>
                          )
                        // 仓库(BBCC) / 店铺(TRAD)
                        if (col.id === 'warehouse' || col.id === 'store')
                          return (
                            <td key={col.id} className="col-store">
                              {replenMode === 'bbcc' ? 'B仓' : x.warehouse || x.store || '-'}
                            </td>
                          )
                        // 现有(TRAD)
                        if (col.id === 'avail')
                          return (
                            <td key={col.id} style={{ fontWeight: 600 }}>
                              {x.available_qty}
                            </td>
                          )
                        // 供应商-B仓(在途, 与进销存B仓维度同源)
                        if (col.id === 'b_transit')
                          return (
                            <td
                              key={col.id}
                              style={{ color: 'var(--muted)', fontSize: 'var(--font-xs)' }}
                            >
                              {x.b_transit ?? '-'}
                            </td>
                          )
                        // B仓可用库存
                        if (col.id === 'b_stock')
                          return (
                            <td key={col.id} style={{ color: 'var(--primary)', fontWeight: 600 }}>
                              {x.b_stock ?? '-'}
                            </td>
                          )
                        // B仓周转
                        if (col.id === 'b_turn')
                          return (
                            <td
                              key={col.id}
                              style={{
                                fontSize: 'var(--font-xs)',
                                fontWeight: 600,
                                color:
                                  x.b_stock > 0 &&
                                  (x.daily_sales > 0 ? x.b_stock / x.daily_sales : Infinity) > 15
                                    ? 'var(--danger)'
                                    : x.b_stock > 0 &&
                                        x.daily_sales > 0 &&
                                        x.b_stock / x.daily_sales > 10
                                      ? 'var(--warning)'
                                      : 'var(--text)',
                              }}
                            >
                              {x.b_stock > 0
                                ? x.daily_sales > 0
                                  ? (x.b_stock / x.daily_sales).toFixed(1) + '天'
                                  : '∞'
                                : '-'}
                            </td>
                          )
                        // C仓总和可用
                        if (col.id === 'c_stock')
                          return (
                            <td key={col.id} style={{ fontWeight: 600 }}>
                              {x.c_stock ?? x.available_qty}
                            </td>
                          )
                        // 在途
                        if (col.id === 'transit')
                          return (
                            <td key={col.id}>
                              {replenMode === 'bbcc'
                                ? (x.c_transit ?? '-')
                                : (x.in_transit_qty ?? '-')}
                            </td>
                          )
                        // 日销
                        if (col.id === 'sales')
                          return (
                            <td
                              key={col.id}
                              style={{
                                fontSize: 'var(--font-xs)',
                                fontWeight: 600,
                                whiteSpace: 'nowrap',
                              }}
                            >
                              {x.daily_sales}
                              <span
                                style={{
                                  fontSize: 'var(--font-10)',
                                  fontWeight: 400,
                                  color: 'var(--muted2)',
                                }}
                              >
                                /
                                {(x.daily_sales_7 || 0) > (x.daily_sales_14 || 0) * 1.15 ? (
                                  <IconTrendUp
                                    size={12}
                                    style={{
                                      display: 'inline',
                                      verticalAlign: 'middle',
                                      color: 'var(--success)',
                                    }}
                                  />
                                ) : (x.daily_sales_7 || 0) < (x.daily_sales_14 || 0) * 0.85 ? (
                                  <IconTrendDown
                                    size={12}
                                    style={{
                                      display: 'inline',
                                      verticalAlign: 'middle',
                                      color: 'var(--danger)',
                                    }}
                                  />
                                ) : (
                                  <IconTrendFlat
                                    size={12}
                                    style={{
                                      display: 'inline',
                                      verticalAlign: 'middle',
                                      color: 'var(--muted)',
                                    }}
                                  />
                                )}
                                {(x.daily_sales_7 || 0).toFixed(1)}/
                                {(x.daily_sales_14 || 0) > (x.daily_sales_28 || 0) * 1.15 ? (
                                  <IconTrendUp
                                    size={12}
                                    style={{
                                      display: 'inline',
                                      verticalAlign: 'middle',
                                      color: 'var(--success)',
                                    }}
                                  />
                                ) : (x.daily_sales_14 || 0) < (x.daily_sales_28 || 0) * 0.85 ? (
                                  <IconTrendDown
                                    size={12}
                                    style={{
                                      display: 'inline',
                                      verticalAlign: 'middle',
                                      color: 'var(--danger)',
                                    }}
                                  />
                                ) : (
                                  <IconTrendFlat
                                    size={12}
                                    style={{
                                      display: 'inline',
                                      verticalAlign: 'middle',
                                      color: 'var(--muted)',
                                    }}
                                  />
                                )}
                                {(x.daily_sales_14 || 0).toFixed(1)}/
                                {(x.daily_sales_28 || 0) > (x.daily_sales_60 || 0) * 1.15 ? (
                                  <IconTrendUp
                                    size={12}
                                    style={{
                                      display: 'inline',
                                      verticalAlign: 'middle',
                                      color: 'var(--success)',
                                    }}
                                  />
                                ) : (x.daily_sales_28 || 0) < (x.daily_sales_60 || 0) * 0.85 ? (
                                  <IconTrendDown
                                    size={12}
                                    style={{
                                      display: 'inline',
                                      verticalAlign: 'middle',
                                      color: 'var(--danger)',
                                    }}
                                  />
                                ) : (
                                  <IconTrendFlat
                                    size={12}
                                    style={{
                                      display: 'inline',
                                      verticalAlign: 'middle',
                                      color: 'var(--muted)',
                                    }}
                                  />
                                )}
                                {(x.daily_sales_28 || 0).toFixed(1)}
                              </span>
                            </td>
                          )
                        // C仓周转
                        if (col.id === 'c_turn')
                          return (
                            <td key={col.id} className="text-11 font-600">
                              {x.c_turnover != null ? x.c_turnover + '天' : '∞'}
                            </td>
                          )
                        // 在途周转
                        if (col.id === 'transit_turn')
                          return (
                            <td key={col.id} style={{ fontSize: 'var(--font-xs)' }}>
                              {x.transit_turnover != null ? x.transit_turnover + '天' : '∞'}
                            </td>
                          )
                        // 安全线(TRAD)
                        if (col.id === 'safety') return <td key={col.id}>{x.safety_qty}</td>
                        // 在库周转(TRAD)
                        if (col.id === 'turn')
                          return (
                            <td
                              key={col.id}
                              style={{
                                color:
                                  x.days_to_empty < 5
                                    ? 'var(--danger)'
                                    : x.days_to_empty < 10
                                      ? 'var(--warning)'
                                      : 'var(--text)',
                              }}
                            >
                              {x.days_to_empty > 999 ? '∞' : x.days_to_empty}
                            </td>
                          )
                        // C仓建议补
                        if (col.id === 'suggest')
                          return (
                            <td key={col.id} style={{ color: 'var(--primary)', fontWeight: 600 }}>
                              {x.suggested_qty > 0 ? x.suggested_qty : '-'}
                            </td>
                          )
                        // B仓需补
                        if (col.id === 'b_suggest')
                          return (
                            <td key={col.id} style={{ color: 'var(--success)', fontWeight: 700 }}>
                              {x.b_suggested > 0 ? x.b_suggested : '-'}
                            </td>
                          )
                        // 当前综转
                        if (col.id === 'cur_turn')
                          return (
                            <td key={col.id} style={{ fontSize: 'var(--font-xs)' }}>
                              {x.combined_turnover_current != null
                                ? x.combined_turnover_current + '天'
                                : '∞'}
                            </td>
                          )
                        // 补后综转(BBCC) / 补后周转(TRAD)
                        if (col.id === 'after_turn')
                          return (
                            <td
                              key={col.id}
                              style={{
                                fontSize: 'var(--font-xs)',
                                fontWeight: 700,
                                color:
                                  replenMode === 'bbcc'
                                    ? x.combined_turnover != null && x.combined_turnover > 90
                                      ? 'var(--danger)'
                                      : x.combined_turnover != null && x.combined_turnover > 15
                                        ? 'var(--warning)'
                                        : 'var(--text)'
                                    : x.after_turnover != null && x.after_turnover > 90
                                      ? 'var(--danger)'
                                      : x.after_turnover != null && x.after_turnover > 15
                                        ? 'var(--warning)'
                                        : 'var(--text)',
                              }}
                            >
                              {replenMode === 'bbcc'
                                ? (x.suggested_qty > 0 || x.b_suggested > 0) &&
                                  x.combined_turnover != null
                                  ? x.combined_turnover + '天'
                                  : '-'
                                : x.suggested_qty > 0 && x.after_turnover != null
                                  ? x.after_turnover + '天'
                                  : '-'}
                            </td>
                          )
                        // 备注
                        if (col.id === 'note')
                          return (
                            <td
                              key={col.id}
                              className="col-name"
                              style={{ color: 'var(--muted2)', fontSize: 'var(--font-sm)' }}
                            >
                              {renderNote(x.note)}
                            </td>
                          )
                        // 标记操作
                        if (col.id === 'action')
                          return (
                            <td key={col.id}>
                              {isOrdered ? (
                                <span
                                  onClick={() =>
                                    toggleOrdered(
                                      x.sku,
                                      x.store,
                                      x.product_name,
                                      x.suggested_qty || x.b_suggested,
                                    )
                                  }
                                  style={{
                                    cursor: 'pointer',
                                    fontSize: 'var(--font-lg)',
                                    color: 'var(--success)',
                                    display: 'inline-flex',
                                    alignItems: 'center',
                                    gap: 2,
                                  }}
                                >
                                  <svg
                                    width="14"
                                    height="14"
                                    viewBox="0 0 24 24"
                                    fill="none"
                                    stroke="currentColor"
                                    strokeWidth="3"
                                    strokeLinecap="round"
                                    strokeLinejoin="round"
                                    style={{ display: 'inline', verticalAlign: 'middle' }}
                                  >
                                    <polyline points="4 12 10 18 20 6" />
                                  </svg>
                                  <span className="text-9 muted2">{t('undo.undo')}</span>
                                </span>
                              ) : (
                                <span
                                  onClick={() => {
                                    if (
                                      (x.suggested_qty > 0 || x.b_suggested > 0) &&
                                      x.combined_turnover > 90 &&
                                      !window.confirm(
                                        `补后综合周转${x.combined_turnover}天，已超90天考核红线，仍标记操作？`,
                                      )
                                    )
                                      return
                                    toggleOrdered(
                                      x.sku,
                                      x.store,
                                      x.product_name,
                                      x.suggested_qty || x.b_suggested,
                                    )
                                  }}
                                  style={{
                                    cursor: 'pointer',
                                    fontSize: 'var(--font-18)',
                                    opacity: 0.5,
                                  }}
                                >
                                  ☐
                                </span>
                              )}
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
          {replenTotal > 0 && replen.length < replenTotal && (
            <div
              className="text-center mt-8"
              ref={function (el) {
                if (el && !el._observer) {
                  el._observer = new IntersectionObserver(
                    function (entries) {
                      // 闭包旧replenPage bug: observer只创建一次, 回调捕获创建时旧值→反复加载同页
                      // 修复: 用 ref 实时读最新 page, 避免重复加载同一页(数据重复致'滚到3100条还是那批在途0')
                      if (entries[0].isIntersecting && !replenLoadingMore) {
                        const next = replenPageRef.current + 1
                        replenPageRef.current = next
                        loadReplen(replenMode, globalChannel, next)
                      }
                    },
                    { rootMargin: '200px' },
                  )
                  el._observer.observe(el)
                }
              }}
            >
              {replenLoadingMore ? (
                <div style={{ padding: '4px 0' }}>
                  {[1, 2, 3].map(k => (
                    <Skeleton key={k} height={28} style={{ marginBottom: 4 }} />
                  ))}
                </div>
              ) : (
                <span
                  className="btn btn-ghost"
                  style={{ fontSize: 'var(--font-sm)', padding: '6px 16px', cursor: 'pointer' }}
                >
                  加载更多
                </span>
              )}
            </div>
          )}
        </div>
      )}
      {/* 已下单明细（仅BBCC模式）：B 仓入库批次 + 在库天数监控，超储预警用
              2026-10-27 根治: 去默认 open(初始折叠)——防下单后明细区整块插入的高度突变
              (iOS Safari 地址栏联动致顶部白条/错位); 点开才展开 */}
      {replenMode === 'bbcc' && orderedItems.length > 0 && (
        <details style={{ marginTop: 12 }}>
          <summary
            className="small muted"
            style={{ cursor: 'pointer', fontSize: 'var(--font-sm)', fontWeight: 600 }}
          >
            <IconPackage size={14} style={{ verticalAlign: -3 }} /> 已下单 {orderedItems.length} 项
            · 点击查看入库日期与仓储天数
          </summary>
          <div style={{ fontSize: 'var(--font-sm)', marginTop: 8 }}>
            {orderedItems.map((po, i) => {
              const daysSinceArrival = po.arrival_date
                ? Math.floor(
                    (new Date().getTime() - new Date(po.arrival_date).getTime()) /
                      (1000 * 60 * 60 * 24),
                  )
                : null
              const stayColor =
                daysSinceArrival != null
                  ? daysSinceArrival > 90
                    ? 'var(--danger)'
                    : daysSinceArrival > 15
                      ? 'var(--warning)'
                      : 'var(--text)'
                  : 'var(--muted)'
              return (
                <div
                  key={i}
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    padding: '6px 10px',
                    border: '1px solid var(--border)',
                    borderRadius: 'var(--radius-lg)',
                    marginBottom: 4,
                    flexWrap: 'wrap',
                    gap: 4,
                  }}
                >
                  <span style={{ flex: 1, minWidth: 120 }}>
                    {po.sku} {po.product_name}{' '}
                    <span className="pill success" style={{ fontSize: 'var(--font-10)' }}>
                      +{po.actual_qty || po.suggested_qty}
                    </span>
                  </span>
                  <span style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                    <span className="small" style={{ color: stayColor, fontWeight: 600 }}>
                      {daysSinceArrival != null ? daysSinceArrival + '天' : '待入仓'}
                    </span>
                    <input
                      type="date"
                      value={po.arrival_date || ''}
                      onChange={e => setArrivalDate(po, e.target.value)}
                      style={{
                        fontSize: 'var(--font-xs)',
                        padding: '2px 6px',
                        border: '1px solid var(--border)',
                        borderRadius: 4,
                        width: 130,
                      }}
                    />
                    <span
                      onClick={() => toggleOrdered(po.sku, po.store)}
                      style={{
                        cursor: 'pointer',
                        color: 'var(--danger)',
                        opacity: 0.6,
                        display: 'inline-flex',
                      }}
                    >
                      <IconUndo size={14} />
                    </span>
                  </span>
                </div>
              )
            })}
          </div>
        </details>
      )}
    </div>
  )
}
