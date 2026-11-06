import { IconTrendDown, IconTrendFlat, IconTrendUp, IconLightbulb } from '../../components/Icons'
import EmptyState from '../../components/EmptyState'
import { t } from '../../locale'
import { colLabel } from '../../components/colLabels'
import { INS_PURCHASE_COLS as PURCHASE_COLS } from '../../components/hammer/configs'
import { renderNote, Skeleton } from './shared'
import type { NoteSeg } from '../../api/types'

/** 采购建议行: 已知字段精确类型, 动态扩展字段收敛为 unknown */
export interface PurchaseRow {
  sku?: string
  store?: string
  brand?: string
  barcode?: string
  product_name?: string
  warehouse?: string
  note?: string
  note_segs?: unknown
  purchase_qty?: number
  after_turnover?: number
  target_turnover?: number
  sys_available?: number
  own_available?: number
  b_available?: number
  plat_available?: number
  sys_transit?: number
  own_transit?: number
  b_transit?: number
  plat_transit?: number
  daily_sales?: number
  daily_sales_14?: number
  daily_sales_28?: number
  actual_purchase?: number
  [k: string]: unknown
}

interface Props {
  /** 原始数组(判断 loading/空态) */
  purchase: PurchaseRow[]
  filteredPurchase: PurchaseRow[]
  purchaseLoading: boolean
  purchaseLimit: number
  setPurchaseLimit: (fn: (p: number) => number) => void
  /** 服务端分页: 后端给的真实总数(收窄后)。为 0 时退回"本地全量"判定 */
  purchaseTotal?: number
  /** 触底加载下一页(2026-11-06 服务端分页) */
  onLoadMore: () => void
  purchaseLoadingMore?: boolean
  purchaseVisCols: string[]
  insightSearch: string
  replenMode: string
  globalChannel: string
  [k: string]: unknown
}

/** 采购建议 tab。从 InsightsPage 外提(第二刀, 见 15.136): 全受控, 唯一写操作是
 *  触底加载 onLoadMore(→ 后端 page+1)。
 *  2026-11-06: 原先是"先把 6MB 全量拉进浏览器, 再 slice(0, purchaseLimit)" ——
 *  首屏只渲染 50 行却传了 9,684 条, 既顶穿 TiDB 缓存单条上限, 又把实例执行槽占满
 *  (队头阻塞)。现在改为服务端分页 + 触底追加。
 *  口径: 采购闭环 = 导出建议 → 下单 → 清洗导入更新在途 → 建议自动抵扣;
 *  timing 三态「充足/建议」由 purchase_qty 与周转对比推出。 */
export default function PurchaseTab(props: Props) {
  const {
    purchase,
    filteredPurchase,
    purchaseLoading,
    purchaseLimit,
    setPurchaseLimit: _setPurchaseLimit,
    purchaseTotal = 0,
    onLoadMore,
    purchaseLoadingMore = false,
    purchaseVisCols,
    insightSearch,
    replenMode,
    globalChannel,
  } = props
  return (
    <div className="card">
      <div
        style={{
          fontSize: 'var(--font-xs)',
          color: 'var(--muted2)',
          background: 'var(--bg)',
          borderRadius: 'var(--radius-card)',
          padding: '8px 12px',
          marginBottom: 10,
          lineHeight: 1.7,
        }}
      >
        <IconLightbulb size={13} style={{ verticalAlign: -2 }} /> <b>{t('ins_p.banner_1')}</b>：
        {t('ins_p.banner_pre')}
        <b>{t('ins_p.banner_2')}</b>
        {t('ins_p.banner_mid')}
      </div>
      <div
        className="section-title"
        style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center' }}
      >
        <span>{t('insights.purchase')}</span>
        {globalChannel === 'jd' && (
          <span
            className="pill"
            style={{
              fontSize: 'var(--font-10)',
              padding: '8px 10px',
              minHeight: 'auto',
              lineHeight: '18px',
            }}
          >
            {replenMode === 'bbcc' ? t('ins_p.mode_bbcc') : t('ins_p.mode_trad')}
          </span>
        )}
        <span
          className="muted2"
          style={{ fontSize: 'var(--font-xs)', fontWeight: 'var(--fw-regular)' }}
        >
          {t('ins.showing_cols', {
            v: purchaseVisCols.length,
            all: PURCHASE_COLS.length,
          })}{' '}
          ·{' '}
          {t('ins.loaded_n_of_m', {
            n: Math.min(purchaseLimit, purchaseTotal || filteredPurchase.length),
            m: purchaseTotal || filteredPurchase.length,
          })}
          {insightSearch ? ` · "${insightSearch}"` : ''}
        </span>
      </div>
      {purchaseLoading && purchase.length === 0 ? (
        <div>
          {[1, 2, 3, 4].map(i => (
            <Skeleton key={i} height={36} style={{ marginBottom: 4 }} />
          ))}
        </div>
      ) : purchase.length === 0 ? (
        <EmptyState icon="factory" title={t('insights.no_purchase')} />
      ) : (
        <div style={{ overflow: 'auto', maxHeight: 'calc(100vh - 180px)' }}>
          <table>
            <colgroup>
              {purchaseVisCols.map(id => {
                const col = PURCHASE_COLS.find(c => c.id === id)
                return col ? <col key={col.id} /> : null
              })}
            </colgroup>
            <thead style={{ position: 'sticky', top: 0, background: 'var(--card)', zIndex: 1 }}>
              <tr>
                {purchaseVisCols.map(id => {
                  const col = PURCHASE_COLS.find(c => c.id === id)
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
              {filteredPurchase.slice(0, purchaseLimit).map((x, i) => {
                // 判据用 code, 显示层再映射(中文值不能兼作判据, 否则翻译即失效)
                const timing =
                  !x.purchase_qty || x.purchase_qty <= 0
                    ? 'enough'
                    : x.after_turnover &&
                        (x.target_turnover || 15) > 0 &&
                        x.after_turnover <= (x.target_turnover || 15)
                      ? 'suggest'
                      : 'enough'
                return (
                  <tr key={i}>
                    {purchaseVisCols.map(id => {
                      const col = PURCHASE_COLS.find(c => c.id === id)
                      if (!col) return <td key={id}></td>
                      if (col.id === 'barcode')
                        return (
                          <td key={col.id} className="mono text-11 muted2">
                            {x.barcode || '-'}
                          </td>
                        )
                      if (col.id === 'brand')
                        return (
                          <td key={col.id} style={{ fontSize: 'var(--font-sm)' }}>
                            {x.brand || '-'}
                          </td>
                        )
                      if (col.id === 'sku')
                        return (
                          <td key={col.id} className="mono" style={{ fontSize: 'var(--font-sm)' }}>
                            {x.sku}
                          </td>
                        )
                      if (col.id === 'name')
                        return (
                          <td key={col.id} className="col-name">
                            {x.product_name}
                          </td>
                        )
                      if (col.id === 'warehouse')
                        return (
                          <td key={col.id} className="col-store">
                            {x.warehouse || x.store || '-'}
                          </td>
                        )
                      // 系统可用 / 系统在途(与进销存对应仓维度同源: inventory in_transit_qty)
                      if (col.id === 'sys_available')
                        return (
                          <td
                            key={col.id}
                            style={{
                              fontSize: 'var(--font-sm)',
                              fontWeight: 'var(--fw-semibold)',
                              whiteSpace: 'nowrap',
                            }}
                          >
                            {x.sys_available ?? '-'}
                            <span
                              style={{
                                fontSize: 'var(--font-10)',
                                fontWeight: 'var(--fw-regular)',
                                color: 'var(--muted2)',
                              }}
                            >
                              {' '}
                              {t('ins_p.own_avail', { a: x.own_available || 0 })}
                              {replenMode === 'bbcc' ? `+B${x.b_available || 0}` : ''}+C
                              {x.plat_available || 0})
                            </span>
                          </td>
                        )
                      if (col.id === 'sys_transit')
                        return (
                          <td
                            key={col.id}
                            style={{
                              fontSize: 'var(--font-sm)',
                              color: 'var(--muted)',
                              fontWeight: 'var(--fw-regular)',
                              whiteSpace: 'nowrap',
                            }}
                          >
                            {x.sys_transit ?? '-'}
                            <span style={{ fontSize: 'var(--font-10)', color: 'var(--muted2)' }}>
                              {' '}
                              {t('ins_p.own_transit', { a: x.own_transit || 0 })}
                              {replenMode === 'bbcc'
                                ? t('ins_p.b_in_transit', { b: x.b_transit || 0 })
                                : ''}
                              {t('ins_p.c_in_transit', { c: x.plat_transit || 0 })})
                            </span>
                          </td>
                        )
                      if (col.id === 'daily_sales')
                        return (
                          <td
                            key={col.id}
                            style={{
                              fontSize: 'var(--font-sm)',
                              fontWeight: 'var(--fw-semibold)',
                              whiteSpace: 'nowrap',
                            }}
                          >
                            {x.daily_sales}
                            <span
                              style={{
                                fontSize: 'var(--font-10)',
                                fontWeight: 'var(--fw-regular)',
                                color: 'var(--muted2)',
                              }}
                            >
                              {
                                <>
                                  /
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
                                  {(x.daily_sales_28 || 0).toFixed(1)}
                                </>
                              }
                            </span>
                          </td>
                        )
                      if (col.id === 'actual_purchase')
                        return (
                          <td
                            key={col.id}
                            style={{
                              fontWeight: 'var(--fw-bold)',
                              color: x.actual_purchase > 0 ? 'var(--success)' : 'var(--muted2)',
                            }}
                          >
                            {x.actual_purchase > 0
                              ? '+' + x.actual_purchase
                              : x.actual_purchase === 0
                                ? '0'
                                : '-'}
                          </td>
                        )
                      if (col.id === 'after_turnover')
                        return (
                          <td
                            key={col.id}
                            style={{
                              fontWeight: 'var(--fw-semibold)',
                              color:
                                x.actual_purchase > 0
                                  ? x.target_turnover > 0 && x.after_turnover > x.target_turnover
                                    ? 'var(--danger)'
                                    : 'var(--text)'
                                  : 'var(--muted2)',
                            }}
                          >
                            {x.actual_purchase > 0 ? t('ins.days_n', { n: x.after_turnover }) : '-'}
                          </td>
                        )
                      if (col.id === 'note')
                        return (
                          <td
                            key={col.id}
                            className="col-note"
                            onClick={e => e.currentTarget.classList.toggle('col-note-open')}
                            title={x.note || ''}
                          >
                            {renderNote(x.note, x.note_segs as NoteSeg[] | null) ||
                              t('insights.no_purchase_needed')}
                          </td>
                        )
                      if (col.id === 'timing')
                        return (
                          <td key={col.id} style={{ textAlign: 'center' }}>
                            <span className={`pill ${timing === 'suggest' ? 'warning' : 'info'}`}>
                              {timing === 'suggest'
                                ? t('ins_p.timing_suggest')
                                : t('ins_p.timing_enough')}
                            </span>
                          </td>
                        )
                      return <td key={col.id}></td>
                    })}
                  </tr>
                )
              })}
            </tbody>
            <tfoot>
              <tr style={{ fontWeight: 'var(--fw-bold)', borderTop: '2px solid var(--border)' }}>
                {purchaseVisCols.includes('actual_purchase') && (
                  <>
                    <td
                      colSpan={purchaseVisCols.indexOf('actual_purchase')}
                      style={{ textAlign: 'right', fontSize: 'var(--font-sm)' }}
                    >
                      {t('ins.total_row')}
                    </td>
                    <td style={{ color: 'var(--success)', fontSize: 'var(--font-13)' }}>
                      +{filteredPurchase.reduce((s, x) => s + (x.actual_purchase || 0), 0)}
                    </td>
                    {purchaseVisCols.includes('after_turnover') &&
                      purchaseVisCols.indexOf('after_turnover') >
                        purchaseVisCols.indexOf('actual_purchase') && (
                        <td
                          colSpan={
                            purchaseVisCols.length - purchaseVisCols.indexOf('after_turnover') - 1
                          }
                          className="text-11 muted2"
                        >
                          {(() => {
                            const withPurchase = filteredPurchase.filter(x => x.purchase_qty > 0)
                            const avgTurnover =
                              withPurchase.length > 0
                                ? (
                                    withPurchase.reduce((s, x) => s + (x.after_turnover || 0), 0) /
                                    withPurchase.length
                                  ).toFixed(1)
                                : ''
                            return t('ins.avg_turnover', { v: avgTurnover || '—' })
                          })()}
                        </td>
                      )}
                  </>
                )}
              </tr>
            </tfoot>
          </table>
          {purchaseTotal > purchase.length && (
            <div
              className="text-center mt-8"
              ref={function (el) {
                if (el && !el._observer) {
                  el._observer = new IntersectionObserver(
                    function (entries) {
                      if (entries[0].isIntersecting) onLoadMore()
                    },
                    { rootMargin: '200px' },
                  )
                  el._observer.observe(el)
                }
              }}
            >
              <span
                className="btn btn-ghost"
                style={{ fontSize: 'var(--font-sm)', padding: '6px 16px', cursor: 'pointer' }}
              >
                {purchaseLoadingMore ? t('ins.loading') : t('ins.load_more')}
              </span>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
