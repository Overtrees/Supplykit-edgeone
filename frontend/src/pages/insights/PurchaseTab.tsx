import { IconTrendDown, IconTrendFlat, IconTrendUp, IconLightbulb } from '../../components/Icons'
import EmptyState from '../../components/EmptyState'
import { t } from '../../locale'
import { INS_PURCHASE_COLS as PURCHASE_COLS } from '../../components/hammer/configs'
import { renderNote, Skeleton } from './shared'

interface Props {
  /** 原始数组(判断 loading/空态) */
  purchase: any[]
  filteredPurchase: any[]
  purchaseLoading: boolean
  purchaseLimit: number
  setPurchaseLimit: (fn: (p: number) => number) => void
  purchaseVisCols: string[]
  insightSearch: string
  replenMode: string
  globalChannel: string
}

/** 采购建议 tab。从 InsightsPage 外提(第二刀, 见 15.136): 全受控, 唯一写操作是
 *  触底加载 setPurchaseLimit(+50)。口径: 采购闭环 = 导出建议 → 下单 → 清洗导入
 *  更新在途 → 建议自动抵扣; timing 三态「充足/建议」由 purchase_qty 与周转对比推出。 */
export default function PurchaseTab(props: Props) {
  const {
    purchase,
    filteredPurchase,
    purchaseLoading,
    purchaseLimit,
    setPurchaseLimit,
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
        <IconLightbulb size={13} style={{ verticalAlign: -2 }} /> <b>采购闭环</b>：导出采购建议 →
        内部评估执行下单 → <b>清洗导入库存（含在途列）</b>更新在途 → 建议自动抵扣，完成闭环
      </div>
      <div
        className="section-title"
        style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center' }}
      >
        <span>采购建议</span>
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
            {replenMode === 'bbcc' ? 'BBCC 口径(含B仓)' : '传统口径(不含B仓)'}
          </span>
        )}
        <span className="muted2" style={{ fontSize: 'var(--font-xs)', fontWeight: 400 }}>
          显示 {purchaseVisCols.length}/{PURCHASE_COLS.length} 列 · 已加载{' '}
          {Math.min(purchaseLimit, filteredPurchase.length)}/{filteredPurchase.length} 条
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
                      {col.label}
                    </th>
                  ) : null
                })}
              </tr>
            </thead>
            <tbody>
              {filteredPurchase.slice(0, purchaseLimit).map((x, i) => {
                const timing =
                  !x.purchase_qty || x.purchase_qty <= 0
                    ? '充足'
                    : x.after_turnover &&
                        (x.target_turnover || 15) > 0 &&
                        x.after_turnover <= (x.target_turnover || 15)
                      ? '建议'
                      : '充足'
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
                              fontWeight: 600,
                              whiteSpace: 'nowrap',
                            }}
                          >
                            {x.sys_available ?? '-'}
                            <span
                              style={{
                                fontSize: 'var(--font-10)',
                                fontWeight: 400,
                                color: 'var(--muted2)',
                              }}
                            >
                              {' '}
                              (自有{x.own_available || 0}
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
                              fontWeight: 400,
                              whiteSpace: 'nowrap',
                            }}
                          >
                            {x.sys_transit ?? '-'}
                            <span style={{ fontSize: 'var(--font-10)', color: 'var(--muted2)' }}>
                              {' '}
                              (自有在途{x.own_transit || 0}
                              {replenMode === 'bbcc' ? `+B在途${x.b_transit || 0}` : ''}+C在途
                              {x.plat_transit || 0})
                            </span>
                          </td>
                        )
                      if (col.id === 'daily_sales')
                        return (
                          <td
                            key={col.id}
                            style={{
                              fontSize: 'var(--font-sm)',
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
                              fontWeight: 700,
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
                              fontWeight: 600,
                              color:
                                x.actual_purchase > 0
                                  ? x.target_turnover > 0 && x.after_turnover > x.target_turnover
                                    ? 'var(--danger)'
                                    : 'var(--text)'
                                  : 'var(--muted2)',
                            }}
                          >
                            {x.actual_purchase > 0 ? x.after_turnover + '天' : '-'}
                          </td>
                        )
                      if (col.id === 'note')
                        return (
                          <td
                            key={col.id}
                            className="col-name"
                            style={{ color: 'var(--muted2)', fontSize: 'var(--font-sm)' }}
                          >
                            {renderNote(x.note) || t('insights.no_purchase_needed')}
                          </td>
                        )
                      if (col.id === 'timing')
                        return (
                          <td key={col.id} style={{ textAlign: 'center' }}>
                            <span className={`pill ${timing === '建议' ? 'warning' : 'info'}`}>
                              {timing}
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
              <tr style={{ fontWeight: 700, borderTop: '2px solid var(--border)' }}>
                {purchaseVisCols.includes('actual_purchase') && (
                  <>
                    <td
                      colSpan={purchaseVisCols.indexOf('actual_purchase')}
                      style={{ textAlign: 'right', fontSize: 'var(--font-sm)' }}
                    >
                      合计
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
                            return '平均周转 ' + (avgTurnover || '—') + ' 天'
                          })()}
                        </td>
                      )}
                  </>
                )}
              </tr>
            </tfoot>
          </table>
          {filteredPurchase.length > purchaseLimit && (
            <div
              className="text-center mt-8"
              ref={function (el) {
                if (el && !el._observer) {
                  el._observer = new IntersectionObserver(
                    function (entries) {
                      if (entries[0].isIntersecting)
                        setPurchaseLimit(function (prev) {
                          return prev + 50
                        })
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
                加载中...
              </span>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
