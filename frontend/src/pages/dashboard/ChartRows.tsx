import { t } from '../../locale'
import Chart from '../../components/Chart'
import OwnHealthCard from '../../components/dash/OwnHealthCard'
import type { OwnHealthData } from '../../components/dash/OwnHealthCard'
import { useAppStore } from '../../store/useAppStore'
import { fmtInt, fmtWh } from './format'

/**
 * 看板图表区(全受控) —— 自 DashboardPage.tsx 外提(S3-1 续3)。
 * 中图两卡(GMV 走势/店铺盘子) + 第三行(低库存明细/自有仓健康)。
 */

/** 低库存/告警明细行: 已知字段精确声明, 动态扩展字段 unknown */
interface LowStockRow {
  id?: number | string
  title?: string
  description?: string
  severity?: string
  related_sku?: string
  warehouse?: string
  warehouse_type?: string
  [k: string]: unknown
}

/** 采购/补货卡行 */
interface ProcRow {
  sku?: string
  product_name?: string
  kind?: string
  qty?: number
  note?: string
  wh?: string
  days_to_empty?: number
  [k: string]: unknown
}

interface MidChartsProps {
  dashboard?: unknown
  gmvView?: string
  /** ECharts 动态配置对象(第三方结构, 不造假严格) */
  barOption?: Record<string, unknown>
  storeOption?: Record<string, unknown>
  storeDataLen?: number
  _storeDim?: string
  setStoreDim?: (v: string) => void
  funnelLen?: number
  [k: string]: unknown
}

interface ChartRow3Props {
  dashboard?: unknown
  /** 供给侧健康数据(OwnHealthData 形状, 边界处断言收敛) */
  ownHealth?: unknown
  lowStockShown?: LowStockRow[]
  lowStockTotal?: number
  procList?: ProcRow[]
  procLoading?: boolean
  procTotal?: number
  loadFullAlerts?: () => void
  setShowAllLowStock?: (v: boolean) => void
  setShowAllOwn?: (v: boolean) => void
  setShowAllProc?: (v: boolean) => void
  onAlert?: (sku?: string, whType?: string, wh?: string) => void
  onGoInsights?: (tab: string, sku?: string) => void
  [k: string]: unknown
}

/** mid-chart-grid: GMV 趋势 + 店铺盘子(维度切换 _storeDim 留页面) */
export function MidCharts({
  dashboard,
  gmvView,
  barOption,
  storeOption,
  storeDataLen,
  _storeDim,
  setStoreDim,
  funnelLen = 6,
}: MidChartsProps) {
  // 订单阶段分布: 档数随库里的 order_status 取值变化(现为 6: 总订单+5 状态)。
  // 固定 200px 在 6 档时每档只剩 30px, 轴标签会挤在一起 —— 按档数给高, 每档保底 30px。
  const funnelH = Math.max(200, funnelLen * 30 + 24)
  return (
    <div className="mid-chart-grid">
      <div className="card" style={{ height: 'auto', overflow: 'visible' }}>
        <div className="section-title">{t('dash.funnel')}</div>
        <Chart option={barOption} height={funnelH} />
      </div>
      <div className="card" style={{ height: 'auto', overflow: 'visible' }}>
        <div
          className="section-title"
          style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}
        >
          {_storeDim === 'brand' ? t('dash.brand_gmv_title') : t('dash.store_gmv')}
          <span
            style={{
              display: 'inline-flex',
              gap: 2,
              background: 'var(--bg)',
              borderRadius: 'var(--radius-full)',
              padding: 2,
            }}
          >
            <span
              onClick={function () {
                setStoreDim('store')
              }}
              className="clickable"
              style={{
                fontSize: 'var(--font-9)',
                padding: '2px 6px',
                borderRadius: 'var(--radius-full)',
                cursor: 'pointer',
                fontWeight: _storeDim === 'store' ? 600 : 400,
                background: _storeDim === 'store' ? 'var(--card)' : 'transparent',
                color: _storeDim === 'store' ? 'var(--text)' : 'var(--muted2)',
                whiteSpace: 'nowrap',
              }}
            >
              {t('dash.dim_store')}
            </span>
            <span
              onClick={function () {
                setStoreDim('brand')
              }}
              className="clickable"
              style={{
                fontSize: 'var(--font-9)',
                padding: '2px 6px',
                borderRadius: 'var(--radius-full)',
                cursor: 'pointer',
                fontWeight: _storeDim === 'brand' ? 600 : 400,
                background: _storeDim === 'brand' ? 'var(--card)' : 'transparent',
                color: _storeDim === 'brand' ? 'var(--text)' : 'var(--muted2)',
                whiteSpace: 'nowrap',
              }}
            >
              {t('dash.dim_brand')}
            </span>
          </span>
        </div>
        <div
          style={{
            overflowX: storeDataLen > 8 ? 'auto' : 'visible',
            WebkitOverflowScrolling: 'touch',
          }}
        >
          <div style={{ width: storeDataLen > 8 ? Math.max(storeDataLen * 30, 340) : '100%' }}>
            <div className="fade-in" key={gmvView + _storeDim}>
              <Chart option={storeOption} height={200} />
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

/** chart-row-3: 低库存明细(截断 5 行) + 自有仓健康大卡 */
export function ChartRow3({
  dashboard,
  ownHealth,
  lowStockShown,
  lowStockTotal,
  procList,
  procLoading,
  procTotal,
  loadFullAlerts,
  setShowAllLowStock,
  setShowAllOwn,
  setShowAllProc,
  onAlert,
  onGoInsights,
}: ChartRow3Props) {
  return (
    <div className="chart-row-3">
      <div className="card" style={{ height: 'auto', overflow: 'visible' }}>
        <div className="section-title">
          {t('dash.low_stock')}
          {lowStockTotal > 0 ? ` (${fmtInt(lowStockTotal)})` : ''}
        </div>
        {/* 2026-11-01 去掉"共 N 条，点击查看全部"降级分支 —— 用户反馈"明细呢?"。
              实测接口按分组各 LIMIT 200 返回, low_stock 能拿到满 200 条, 明细一直都在,
              之前看到空白是瞬时态(登录后 alerts 未到时)被误判成"列表里没有", 不该用提示顶替明细。
              空态只保留 lowStockTotal === 0 一种。 */}
        {lowStockTotal === 0 ? (
          <div className="small muted" style={{ padding: 12, textAlign: 'center' }}>
            {t('dash.no_alerts')}
          </div>
        ) : (
          lowStockShown.map(x => (
            <div
              key={x.id}
              onClick={() => onAlert && onAlert(x.related_sku, x.warehouse_type, x.warehouse)}
              className="clickable"
              style={{
                padding: '8px 0',
                borderBottom: '1px solid var(--border)',
                fontSize: 'var(--font-13)',
              }}
            >
              <div
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  gap: 8,
                  alignItems: 'flex-start',
                }}
              >
                <span
                  style={{
                    fontWeight: 'var(--fw-semibold)',
                    fontSize: 'var(--font-sm)',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                    flex: 1,
                    minWidth: 0,
                  }}
                >
                  {x.related_sku || x.title}
                </span>
                <span
                  className={'pill ' + (x.severity === 'error' ? 'danger' : 'warning')}
                  style={{ flexShrink: 0 }}
                >
                  {x.severity === 'warning' ? t('dash.sev_warning') : t('dash.alert_overstock')}
                </span>
              </div>
              <div
                className="small muted"
                style={{
                  fontSize: 'var(--font-xs)',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                  marginTop: 2,
                }}
              >
                {/* 副行必须带仓库 —— 告警是"SKU×仓"粒度, 不带仓库时同一 SKU 多仓
                      的几行副行完全一样, 用户看到的仍是"重复的明细" */}
                {x.warehouse ? fmtWh(x.warehouse) + ' · ' : ''}
                {x.description}
              </div>
            </div>
          ))
        )}
        {/* 更多 N 以"卡上已展示条数"为基准, 不再写死 5 —— lowStockShown 可能不足 5 条 */}
        {lowStockTotal > lowStockShown.length && (
          <div
            onClick={() => {
              loadFullAlerts()
              setShowAllLowStock(true)
            }}
            aria-label={t('dash.more_aria_n', { n: lowStockTotal - lowStockShown.length })}
            className="clickable pill info"
            style={{
              cursor: 'pointer',
              fontSize: 'var(--font-10)',
              padding: '1px 8px',
              minHeight: 'auto',
              lineHeight: '16px',
              marginTop: 6,
              width: 'fit-content',
            }}
          >
            {t('dash.more_btn', { n: fmtInt(lowStockTotal - lowStockShown.length) })}
          </div>
        )}
      </div>
      <div className="card" style={{ height: 'auto', overflow: 'visible' }}>
        <div className="section-title">
          {t('dash.proc_card_title')}
          {procTotal > 0 ? ` (${procTotal})` : ''}
        </div>
        {procLoading ? (
          <div className="small muted" style={{ padding: 12, textAlign: 'center' }}>
            {t('dash.loading_ellipsis')}
          </div>
        ) : procTotal === 0 ? (
          <div className="small muted" style={{ padding: 12, textAlign: 'center' }}>
            {t('dash.no_proc_demand')}
          </div>
        ) : (
          procList.slice(0, 5).map((x, i) => (
            <div
              key={x.kind + x.sku + i}
              onClick={() =>
                onGoInsights && onGoInsights(x.kind === 'purchase' ? 'purchase' : 'replen', x.sku)
              }
              className="clickable"
              style={{
                padding: '8px 0',
                borderBottom: '1px solid var(--border)',
                fontSize: 'var(--font-13)',
              }}
            >
              <div
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  gap: 8,
                  alignItems: 'flex-start',
                }}
              >
                <span
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 6,
                    fontWeight: 'var(--fw-semibold)',
                    fontSize: 'var(--font-sm)',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                    flex: 1,
                    minWidth: 0,
                  }}
                >
                  <span
                    className={'pill ' + (x.kind === 'purchase' ? 'warning' : 'danger')}
                    style={{
                      flexShrink: 0,
                      fontSize: 'var(--font-9)',
                      padding: '1px 6px',
                      minHeight: 'auto',
                      lineHeight: '16px',
                    }}
                  >
                    {x.kind === 'purchase' ? t('dash.tag_purchase') : t('dash.tag_replenish')}
                  </span>
                  {x.product_name || x.sku}
                </span>
                {x.qty > 0 && (
                  <span
                    style={{
                      flexShrink: 0,
                      fontWeight: 'var(--fw-bold)',
                      fontSize: 'var(--font-sm)',
                      color: x.kind === 'purchase' ? 'var(--warning)' : 'var(--danger)',
                    }}
                  >
                    +{x.qty}
                  </span>
                )}
              </div>
              <div
                className="small muted"
                style={{
                  fontSize: 'var(--font-10)',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                  marginTop: 2,
                }}
              >
                {x.sku}
                {x.wh ? ' · ' + x.wh : ''} ·{' '}
                {x.note
                  ? String(x.note).slice(0, 36)
                  : x.days_to_empty > 999
                    ? t('dash.stock_sufficient')
                    : t('dash.cover_for_days', { n: x.days_to_empty })}
              </div>
            </div>
          ))
        )}
        {procTotal > 5 && (
          <div
            onClick={() => {
              setShowAllProc(true)
            }}
            aria-label={t('dash.more_aria_n', { n: procTotal - 5 })}
            className="clickable pill info"
            style={{
              cursor: 'pointer',
              fontSize: 'var(--font-10)',
              padding: '1px 8px',
              minHeight: 'auto',
              lineHeight: '16px',
              marginTop: 6,
              width: 'fit-content',
            }}
          >
            {t('dash.more_btn', { n: procTotal - 5 })}
          </div>
        )}
      </div>

      {/* 自有仓健康大卡(2026-11-01 由小卡改大卡) —— 供给侧口径
            为什么单列一张卡: 自有仓(集货仓)不产生销售(daily_sales_snapshot 只有平台仓行),
            逐仓日销 ds=0 → 低库存/断货口径在自有仓维度恒不触发, 属口径错配而非 bug。
            自有仓真正该判的是「下游要货备不备得出(调拨缺口)」「压得太多(积压)」「会不会过期(效期)」。
            用户明确否决并入「采购&补货」卡: 那张卡回答"哪些要补/要买", 而积压/过期语义相反, 会形成噪音。
            为什么是大卡而非小卡: 实测 90 项(缺口9/效期46/积压35), 小卡内容区 134~192px
            扣掉固定开销 80px 只剩 54~112px, 最多 3~8 行且三类混排会互相掩盖。 */}
      <OwnHealthCard
        data={ownHealth as OwnHealthData | null}
        onAlert={function (sku, whType, wh) {
          if (onAlert) {
            onAlert(sku, 'own', wh)
            return
          }
          useAppStore.getState().setHammerWhType('own')
          window.__setPage && window.__setPage('inv')
        }}
        onShowAll={function () {
          setShowAllOwn(true)
        }}
      />
    </div>
  )
}
