import { IconCheck } from '../../components/Icons'
import EmptyState from '../../components/EmptyState'
import { useAppStore } from '../../store/useAppStore'
import { INS_SLOW_COLS as SLOW_COLS } from '../../components/hammer/configs'
import { Skeleton } from './shared'
import { t } from '../../locale'
import { colLabel } from '../../components/colLabels'

/** 滞销处置建议行(SKU×仓库粒度): 已知字段精确类型, 动态扩展字段收敛为 unknown */
export interface DisposalRow {
  sku?: string
  warehouse?: string
  product_name?: string
  brand?: string
  level?: string
  reason?: string[]
  suggestion?: string
  disposed?: boolean
  days_zero?: number | null   // null = 从未销售(不是 999)
  stock?: number
  [k: string]: unknown
}

interface Props {
  disposals: DisposalRow[]
  disposalsLoading: boolean
  filteredDisp: DisposalRow[]
  showDisposed: boolean
  setShowDisposed: (v: boolean) => void
  slowVisCols: string[]
  slowTotal: number
  slowLoadingMore: boolean
  /** 分页门闩(ref, 页面持有), 传引用不复制 */
  slowLoadingRef: { current: boolean }
  slowSentinelRef: { current: unknown }
  loadSlowMore: () => void
  insightSearch: string
  prodSelIds: string[]
  prodBatch: boolean
  [k: string]: unknown
}

/** 滞销预警 tab(SLU×仓库处置建议)。从 InsightsPage 外提(第三刀, 见 15.137):
 *  全受控。行选择直接走 useAppStore.getState().setProdBatchSel(与页面同源),
 *  分页翻页/加载更多由页面的 loadSlowMore 承担, 这里只调用。 */
export default function SlowTab(props: Props) {
  const {
    disposals,
    disposalsLoading,
    filteredDisp,
    showDisposed,
    setShowDisposed,
    slowVisCols,
    slowTotal,
    slowLoadingMore,
    slowLoadingRef,
    slowSentinelRef,
    loadSlowMore,
    insightSearch,
    prodSelIds,
    prodBatch,
  } = props
  return (
    <div className="card">
      <div
        className="section-title"
        style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center' }}
      >
        <span>{t('insights.slow')}</span>
        <span
          className="muted2"
          style={{ fontSize: 'var(--font-xs)', fontWeight: 'var(--fw-regular)' }}
        >
          {t('ins.loaded_n_of_m', {
            n: Math.min(filteredDisp.length, slowTotal || filteredDisp.length),
            m: slowTotal || filteredDisp.length,
          })}{' '}
          · {t('ins.showing_cols', { v: slowVisCols.length, all: SLOW_COLS.length })}
          {insightSearch ? ` · "${insightSearch}"` : ''}
        </span>
      </div>

      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          flexWrap: 'wrap',
          marginBottom: 8,
        }}
      >
        <span className="muted2" style={{ fontSize: 'var(--font-xs)' }}>
          {t('ins_s.subtitle')}
        </span>
        <span
          onClick={() => setShowDisposed(!showDisposed)}
          className="clickable"
          style={{
            marginLeft: 'auto',
            fontSize: 'var(--font-sm)',
            padding: '4px 12px',
            borderRadius: 'var(--radius-full)',
            border: '1px solid var(--border)',
            background: 'var(--card)',
            cursor: 'pointer',
          }}
        >
          {showDisposed ? t('ins_s.hide_disposed') : t('ins_s.show_disposed')}
        </span>
      </div>
      {disposalsLoading && disposals.length === 0 ? (
        <div>
          {[1, 2, 3].map(i => (
            <Skeleton key={i} height={36} style={{ marginBottom: 4 }} />
          ))}
        </div>
      ) : filteredDisp.length === 0 ? (
        <EmptyState
          icon="tag"
          title={showDisposed ? t('ins_s.empty_disposed') : t('ins_s.empty_slow')}
        />
      ) : (
        <>
          <div
            style={{ overflow: 'auto', maxHeight: 'calc(100vh - 180px)' }}
            onScroll={function (e) {
              const el = e.target as HTMLElement
              // IntersectionObserver 仅在交叉状态变化时回调——持续在视口不重复触发导致卡加载
              // 改用滚动监听: 每次滚动检测接近底部即加载下一页
              if (
                el.scrollTop + el.clientHeight >= el.scrollHeight - 250 &&
                !slowLoadingMore &&
                (slowTotal === 0 || filteredDisp.length < slowTotal)
              ) {
                loadSlowMore()
              }
            }}
          >
            <table>
              <colgroup>
                {slowVisCols.map(id => {
                  const col = SLOW_COLS.find(c => c.id === id)
                  return col ? <col key={col.id} /> : null
                })}
              </colgroup>
              <thead style={{ position: 'sticky', top: 0, background: 'var(--card)', zIndex: 1 }}>
                <tr>
                  {prodBatch && <th style={{ width: 30 }}></th>}
                  {slowVisCols.map(id => {
                    const col = SLOW_COLS.find(c => c.id === id)
                    return col ? (
                      <th
                        style={{
                          whiteSpace: 'nowrap',
                          fontSize: 'var(--font-xs)',
                          padding: '8px 4px',
                        }}
                        key={col.id}
                      >
                        {colLabel(col.id)}
                      </th>
                    ) : null
                  })}
                </tr>
              </thead>
              <tbody>
                {filteredDisp.map((x, i) => {
                  const key = x.sku + '|' + x.warehouse
                  const isSel = (prodSelIds || []).includes(key)
                  const s = useAppStore.getState()
                  const done = !!x.disposed
                  return (
                    <tr
                      key={key}
                      onClick={() => {
                        if (prodBatch && !done) {
                          s.setProdBatchSel(
                            isSel ? s.prodSelIds.filter(k => k !== key) : [...s.prodSelIds, key],
                          )
                        }
                      }}
                      style={{
                        opacity: done ? 0.4 : 1,
                        background: prodBatch && isSel ? 'rgba(29,78,216,0.08)' : 'transparent',
                        cursor: prodBatch && !done ? 'pointer' : 'default',
                      }}
                    >
                      {prodBatch && (
                        <td
                          onClick={e => {
                            e.stopPropagation()
                            if (!done) {
                              s.setProdBatchSel(
                                isSel
                                  ? s.prodSelIds.filter(k => k !== key)
                                  : [...s.prodSelIds, key],
                              )
                            }
                          }}
                          style={{ padding: '9px 12px', minHeight: 32, textAlign: 'center' }}
                        >
                          <span
                            style={{
                              width: 18,
                              height: 18,
                              borderRadius: 'var(--radius-xs)',
                              border: '1.5px solid',
                              borderColor: isSel
                                ? 'var(--primary)'
                                : done
                                  ? 'var(--border)'
                                  : 'var(--border)',
                              background: isSel
                                ? 'var(--primary)'
                                : done
                                  ? 'rgba(148,163,184,0.15)'
                                  : 'transparent',
                              display: 'inline-flex',
                              alignItems: 'center',
                              justifyContent: 'center',
                              color: '#fff',
                              fontSize: 'var(--font-xs)',
                              opacity: done ? 0.35 : 1,
                            }}
                          >
                            {done ? <IconCheck size={12} /> : isSel ? <IconCheck size={12} /> : ''}
                          </span>
                        </td>
                      )}
                      {slowVisCols.map(id => {
                        const col = SLOW_COLS.find(c => c.id === id)
                        if (!col) return <td key={id}></td>
                        if (col.id === 'processed')
                          return (
                            <td key={id}>
                              {x.disposed ? (
                                <span
                                  style={{ fontSize: 'var(--font-xs)', color: 'var(--muted2)' }}
                                >
                                  <IconCheck size={12} style={{ verticalAlign: -2 }} />{' '}
                                  {t('ins_s.disposed')}
                                </span>
                              ) : (
                                <span
                                  style={{ fontSize: 'var(--font-xs)', color: 'var(--muted2)' }}
                                >
                                  -
                                </span>
                              )}
                            </td>
                          )
                        if (col.id === 'brand')
                          return (
                            <td key={id} style={{ fontSize: 'var(--font-sm)' }}>
                              {x.brand || '-'}
                            </td>
                          )
                        if (col.id === 'sku')
                          return (
                            <td key={id} className="mono" style={{ fontSize: 'var(--font-sm)' }}>
                              {x.sku}
                            </td>
                          )
                        if (col.id === 'name')
                          return (
                            <td key={id} style={{ fontSize: 'var(--font-13)' }}>
                              {x.product_name}
                            </td>
                          )
                        if (col.id === 'warehouse')
                          return (
                            <td key={id} style={{ fontSize: 'var(--font-sm)' }}>
                              {x.warehouse}
                            </td>
                          )
                        if (col.id === 'days')
                          return (
                            <td
                              key={id}
                              style={{
                                fontWeight: 'var(--fw-semibold)',
                                color:
                                  x.days_zero == null || x.days_zero >= 90
                                    ? 'var(--danger)'
                                    : x.days_zero >= 30
                                      ? 'var(--warning)'
                                      : 'var(--muted)',
                                fontSize: 'var(--font-sm)',
                              }}
                            >
                              {x.days_zero == null
                                ? t('ins.never_sold')
                                : t('ins.days_n', { n: x.days_zero })}
                            </td>
                          )
                        if (col.id === 'stock')
                          return (
                            <td key={id} style={{ fontSize: 'var(--font-sm)' }}>
                              {x.stock}
                            </td>
                          )
                        if (col.id === 'level')
                          return (
                            <td key={id} style={{ textAlign: 'center' }}>
                              <span
                                className={`pill ${x.level === 'black' ? 'danger' : x.level === 'red' ? 'danger' : x.level === 'yellow' ? 'warning' : 'info'}`}
                                style={{
                                  fontSize: 'var(--font-10)',
                                  padding: '8px 10px',
                                  minHeight: 'auto',
                                  lineHeight: '18px',
                                }}
                              >
                                {x.level === 'black'
                                  ? t('ins_s.lv_urgent')
                                  : x.level === 'red'
                                    ? t('ins_s.lv_dispose')
                                    : x.level === 'yellow'
                                      ? t('ins_s.lv_slow')
                                      : t('ins_s.lv_watch')}
                              </span>
                            </td>
                          )
                        if (col.id === 'note')
                          return (
                            <td
                              key={id}
                              className="col-note"
                              onClick={e => e.currentTarget.classList.toggle('col-note-open')}
                              title={[(x.reason || []).join(' · '), x.suggestion]
                                .filter(Boolean)
                                .join(' → ')}
                            >
                              {(x.reason || []).join(' · ')}
                              <span
                                style={{ color: 'var(--text)', fontWeight: 'var(--fw-semibold)' }}
                              >
                                {' '}
                                → {x.suggestion}
                              </span>
                            </td>
                          )
                        return <td key={id}></td>
                      })}
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          {/* IntersectionObserver 哨兵(自动加载, 无固定占位): 触发时 disconnect 防重复,
                  加载完成 finally 重新 observe(状态变化循环)。onScroll 冗余已去 */}
          <div
            style={{ height: 1 }}
            ref={function (el) {
              if (el && !el._obs) {
                el._obs = new IntersectionObserver(
                  function (entries) {
                    // ref 门闩(最新, 非闭包旧state)——触发后表增长哨兵移出视口,
                    // 用户滚动再进入触发(补货同模式), 无需 reobserve
                    if (
                      entries[0].isIntersecting &&
                      !slowLoadingRef.current &&
                      (slowTotal === 0 || filteredDisp.length < slowTotal)
                    ) {
                      el._obs.disconnect()
                      loadSlowMore()
                    }
                  },
                  { rootMargin: '200px' },
                )
                el._obs.observe(el)
                slowSentinelRef.current = el
              }
            }}
          />
          {slowLoadingMore && (
            <div
              style={{
                textAlign: 'center',
                padding: '6px 0',
                fontSize: 'var(--font-xs)',
                color: 'var(--muted2)',
              }}
            >
              {t('ins.loading')}
            </div>
          )}
        </>
      )}
    </div>
  )
}
