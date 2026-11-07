import { createPortal } from 'react-dom'
import { fmtInt, fmtWh, RISK_LV } from '../format'
import { t } from '../../../locale'
import { usePaged, atBottom } from './usePaged'

type RiskFilter = 'all' | 'red' | 'orange' | 'yellow'

/** 濒临断货明细行: 已知字段精确声明, 动态扩展字段 unknown(索引签名兜底) */
interface RiskRow {
  sku?: string
  product_name?: string
  level?: string
  warehouse?: string
  type?: string
  daily_sales?: number
  available_qty?: number
  days_to_empty?: number
  [k: string]: unknown
}

/** 弹窗数据源: 看板派生的 StockRiskShim / C+自有合并对象 */
interface RiskData {
  total?: number
  critical?: number
  warning?: number
  items?: RiskRow[]
  _full?: RiskRow[]
  bcItems?: RiskRow[]
  bcTotal?: number
  bcCritical?: number
  bcWarning?: number
  cItems?: RiskRow[]
  cTotal?: number
  cCritical?: number
  cWarning?: number
  ownItems?: RiskRow[]
  ownTotal?: number
  ownCritical?: number
  ownWarning?: number
}

interface Props {
  open: boolean
  onClose: () => void
  replMode?: string
  riskFilter: RiskFilter
  setRiskFilter: (v: RiskFilter) => void
  riskCritical: number
  riskWarning: number
  riskTabs: Array<[RiskFilter, string, string]>
  fullRisk: RiskRow[] | null
  riskData: RiskData
  showOwn: boolean
  onAlert?: (sku?: string, whType?: string, wh?: string) => void
  channel?: string
}

export default function RiskSheet(props: Props) {
  const {
    open,
    onClose,
    replMode: _replMode,
    riskFilter,
    setRiskFilter,
    riskCritical,
    riskWarning,
    riskTabs,
    fullRisk,
    riskData: _r,
    showOwn: _showOwn,
    onAlert,
    channel,
  } = props
  /**
   * 2026-11-07 P2 真分页: 原来点击时拉 `full=1` 一次搬 897KB / 2.19s(4656 行),
   * 而弹窗一屏只看 6 行。现在 300/页, 维度内按 `ord`(重建时排序落库)切页,
   * 第 1 页 + 第 2 页正好接上卡面 TOP N —— 不重不漏。
   * 级别过滤放后端(SQL): 否则"标题 54 条、列表 3 条"(第 1 页里只有 3 条红色)。
   */
  const _lv = riskFilter === 'all' ? '' : riskFilter
  const pg = usePaged<RiskRow>(
    open,
    function (off, lim) {
      return (
        '/api/dashboard/stock-risk?channel=' +
        encodeURIComponent(channel || 'jd') +
        '&full=1&limit=' +
        lim +
        '&offset=' +
        off +
        (_lv ? '&level=' + _lv : '')
      )
    },
    300,
    function (d) {
      const o = (d || {}) as RiskData
      const items =
        _replMode === 'bbcc' ? o.bcItems || [] : (o.cItems || []).concat(o.ownItems || [])
      const total = _replMode === 'bbcc' ? o.bcTotal || 0 : (o.cTotal || 0) + (o.ownTotal || 0)
      return { items, total }
    },
    _lv,
  )
  if (!open) return null
  return createPortal(
    <>
      <div
        onClick={function () {
          onClose()
        }}
        style={{ position: 'fixed', inset: 0, zIndex: 9998, background: 'transparent' }}
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
          onClick={function (e) {
            e.stopPropagation()
          }}
          onScroll={function (e) {
            if (atBottom(e)) pg.more()
          }}
          className="material-regular"
          style={{
            width: '100%',
            maxWidth: 600,
            borderRadius: 'var(--radius-lg)',
            padding: '18px 14px calc(14px + env(safe-area-inset-bottom))',
            boxShadow: 'var(--shadow-sheet)',
            pointerEvents: 'auto',
            maxHeight: '70vh',
            overflowY: 'auto',
          }}
        >
          <div
            style={{
              fontSize: 'var(--font-18)',
              fontWeight: 'var(--fw-bold)',
              marginBottom: 6,
              textAlign: 'center',
              color: 'var(--text)',
            }}
          >
            {_replMode === 'bbcc' ? t('dash.risk_title_bc') : t('dash.risk_title')} ·{' '}
            {riskFilter === 'red'
              ? t('dash.risk_red')
              : riskFilter === 'orange'
                ? t('dash.risk_orange')
                : riskFilter === 'yellow'
                  ? t('dash.risk_yellow')
                  : t('dash.all')}{' '}
            {t('dash.risk_n', {
              n: fmtInt(
                (riskFilter === 'red'
                  ? riskCritical
                  : riskFilter === 'orange'
                    ? riskWarning
                    : riskFilter === 'yellow'
                      ? _r.total - riskCritical - riskWarning
                      : _r.total) || 0,
              ),
            })}
          </div>
          {/* 级别切换 tab(三级 pill 入口联动, 弹窗内可切换) */}
          <div
            style={{
              display: 'flex',
              gap: 6,
              justifyContent: 'center',
              marginBottom: 10,
              flexWrap: 'wrap',
            }}
          >
            {riskTabs.map(([lv, lb, lc]) => (
              <span
                key={lv}
                onClick={() => setRiskFilter(lv)}
                className="clickable pill"
                style={{
                  cursor: 'pointer',
                  fontSize: 'var(--font-10)',
                  padding: '2px 10px',
                  minHeight: 'auto',
                  lineHeight: '18px',
                  fontWeight: riskFilter === lv ? 700 : 400,
                  color: riskFilter === lv ? '#fff' : lc,
                  background: riskFilter === lv ? lc : 'var(--bg)',
                  borderColor: lc,
                }}
              >
                {/* 2026-11-04: lb 是**词条键**(如 'dash.urgent'), 必须 t() 解析 ——
                    原来直接渲染 {lb}, 中文界面弹出的是英文键名"dash.urgent" */}
                {t(lb)}
              </span>
            ))}
          </div>
          {(pg.rows.length
            ? pg.rows
            : fullRisk && fullRisk.length
              ? fullRisk
              : _r._full || _r.items || []
          )
            .filter(x => riskFilter === 'all' || x.level === riskFilter)
            .map(function (x, i) {
              const whLabel =
                fmtWh(x.warehouse) ||
                (x.type === 'C'
                  ? t('dash.wh_c')
                  : x.type === 'OWN'
                    ? t('inv.own')
                    : x.type === 'B'
                      ? t('dash.wh_b')
                      : _replMode === 'bbcc'
                        ? 'BC'
                        : t('dash.wh_c'))
              const lv = RISK_LV[x.level]
              return (
                <div
                  key={i}
                  onClick={function () {
                    // bc 合计行(bbcc 专属): 跳 C 仓维度(platform) + 聚合高亮该 SKU 所有 C 仓行(不传具体仓)
                    // —— 满足 bc 一盘棋语义: 看全国 C 仓分布, 而非单一仓
                    const _isBC = x.type === 'BC' || x.warehouse === 'BC'
                    onAlert &&
                      onAlert(
                        x.sku,
                        _isBC ? 'platform' : _showOwn ? 'own' : 'platform',
                        _isBC ? '' : x.warehouse,
                      )
                  }}
                  className="clickable"
                  style={{
                    padding: '8px 12px',
                    background: 'var(--card)',
                    borderRadius: 'var(--radius-md)',
                    marginBottom: 6,
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    gap: 8,
                  }}
                >
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <div
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 4,
                        fontWeight: 'var(--fw-semibold)',
                        fontSize: 'var(--font-sm)',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}
                    >
                      {lv ? (
                        <span
                          className={
                            'pill ' +
                            (x.level === 'red'
                              ? 'danger'
                              : x.level === 'orange'
                                ? 'warning'
                                : 'info')
                          }
                          style={{
                            flexShrink: 0,
                            fontSize: 'var(--font-9)',
                            padding: '1px 6px',
                            minHeight: 'auto',
                            lineHeight: '16px',
                          }}
                        >
                          {/* lv.t 是词条键(dash.risk_red 等), 必须 t() 解析 —— 直接渲染会
                              在中文界面显示英文键名(2026-11-04 用户实测) */}
                          {lv ? t(lv.t) : ''}
                        </span>
                      ) : null}
                      {x.product_name || x.sku}
                    </div>
                    <div className="small muted" style={{ fontSize: 'var(--font-10)' }}>
                      {t('dash.daily_sales_avail', { a: x.daily_sales, b: x.available_qty })}
                    </div>
                  </div>
                  <span
                    title={x.warehouse || ''}
                    style={{
                      fontSize: 'var(--font-9)',
                      padding: '1px 5px',
                      borderRadius: 4,
                      background: 'var(--bg)',
                      color: 'var(--muted)',
                      flexShrink: 0,
                    }}
                  >
                    {whLabel}
                  </span>
                  <span
                    style={{
                      fontSize: 'var(--font-xs)',
                      fontWeight: 'var(--fw-semibold)',
                      color: lv ? lv.c : 'var(--danger)',
                      flexShrink: 0,
                      minWidth: 38,
                      textAlign: 'right',
                    }}
                  >
                    {t('dash.days_n', { n: x.days_to_empty })}
                  </span>
                </div>
              )
            })}
          <div
            onClick={function () {
              onClose()
            }}
            className="clickable sheet-close"
            style={{ marginTop: 8 }}
          >
            <span
              style={{
                fontSize: 'var(--font-15)',
                fontWeight: 'var(--fw-semibold)',
                color: '#fff',
              }}
            >
              {t('dash.close')}
            </span>
          </div>
        </div>
      </div>
    </>,
    document.body,
  )
}
