import React from 'react'
import type { InventoryRow } from '../../api/types'

export interface RiskCardProps {
  replMode: string
  items: InventoryRow[]
  total: number
  riskCritical: number
  riskWarning: number
  riskFont: string
  onAlert?: (sku: string | undefined, whType: string, wh: string) => void
  onLoadFull: () => void
  onShowAllRisk: (v: boolean) => void
  onRiskFilter: (v: 'all' | 'red' | 'orange' | 'yellow') => void
  t: (k: string) => string
}

export default function RiskCard({
  replMode,
  items,
  total,
  riskCritical,
  riskWarning,
  riskFont,
  onAlert,
  onLoadFull,
  onShowAllRisk,
  onRiskFilter,
  t,
}: RiskCardProps) {
  return (
        <div className="card stat-card">
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              minHeight: 14,
            }}
          >
            <div
              style={{
                fontSize: 'var(--font-sm)',
                fontWeight: 600,
                color: 'var(--muted)',
                letterSpacing: 0.2,
                lineHeight: 1.2,
              }}
            >
              濒临断货预警{replMode === 'bbcc' ? '（BC）' : ''}
            </div>
          </div>
          {!items || items.length === 0 ? (
            <div
              style={{
                flex: 1,
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                gap: 6,
              }}
            >
              <div
                style={{
                  width: 44,
                  height: 44,
                  borderRadius: '50%',
                  background: 'var(--bg)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  color: 'var(--success)',
                }}
              >
                <svg
                  width="20"
                  height="20"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
                  <polyline points="22 4 12 14.01 9 11.01" />
                </svg>
              </div>
              <div style={{ fontSize: 'var(--font-sm)', fontWeight: 500, color: 'var(--muted2)' }}>
                {t('dash.stock_ok')}
              </div>
            </div>
          ) : (
            <div
              style={{
                flex: 1,
                display: 'flex',
                flexDirection: 'column',
                justifyContent: 'flex-end',
              }}
            >
              <div style={{ marginTop: 4 }}>
                <div
                  className="card-value"
                  style={{
                    fontSize: 'clamp(17px,8cqi,28px)',
                    fontWeight: 700,
                    lineHeight: 1.15,
                    color: 'var(--danger)',
                    fontVariantNumeric: 'tabular-nums',
                    marginBottom: 1,
                  }}
                >
                  {total}
                </div>
                <div
                  className="card-sub"
                  style={{
                    marginTop: 0,
                    fontSize: 'var(--font-xs)',
                    color: 'var(--muted)',
                    lineHeight: 1.4,
                  }}
                >
                  {t('dash.min_days')} {items[0].days_to_empty} {t('dash.days_out')}
                </div>
                {(riskCritical > 0 || riskWarning > 0 || total > riskCritical + riskWarning) && (
                  <div
                    style={{
                      fontSize: 'var(--font-10)',
                      display: 'flex',
                      gap: 6,
                      marginTop: 4,
                      flexWrap: 'wrap',
                      lineHeight: 1.2,
                    }}
                  >
                    {riskCritical > 0 && (
                      <span
                        onClick={function (e) {
                          e.stopPropagation()
                          onRiskFilter('red')
                          onLoadFull()
                          onShowAllRisk(true)
                        }}
                        className="clickable"
                        style={{
                          color: 'var(--danger)',
                          fontWeight: 600,
                          cursor: 'pointer',
                          fontSize: riskFont,
                        }}
                      >
                        ● {riskCritical} {t('dash.critical')}
                      </span>
                    )}
                    {riskWarning > 0 && (
                      <span
                        onClick={function (e) {
                          e.stopPropagation()
                          onRiskFilter('orange')
                          onLoadFull()
                          onShowAllRisk(true)
                        }}
                        className="clickable"
                        style={{
                          color: 'var(--warning)',
                          fontWeight: 600,
                          cursor: 'pointer',
                          fontSize: riskFont,
                        }}
                      >
                        ● {riskWarning} {t('dash.warning')}
                      </span>
                    )}
                    {total > riskCritical + riskWarning && (
                      <span
                        onClick={function (e) {
                          e.stopPropagation()
                          onRiskFilter('yellow')
                          onLoadFull()
                          onShowAllRisk(true)
                        }}
                        className="clickable"
                        style={{
                          color: 'var(--muted2)',
                          fontWeight: 600,
                          cursor: 'pointer',
                          fontSize: riskFont,
                        }}
                      >
                        ● {total - riskCritical - riskWarning} 观察
                      </span>
                    )}
                  </div>
                )}
              </div>
              <div style={{ flexShrink: 0, paddingTop: 6 }}>
                {items.slice(0, 3).map((x, i) => {
                  const whLabel =
                    fmtWh(x.warehouse) ||
                    (x.type === 'C'
                      ? 'C仓'
                      : x.type === 'OWN'
                        ? '自有'
                        : x.type === 'B'
                          ? 'B仓'
                          : replMode === 'bbcc'
                            ? 'BC'
                            : 'C仓')
                  const lv = RISK_LV[x.level]
                  return (
                    <div
                      key={i}
                      title={x.product_name || x.sku}
                      onClick={function () {
                        const _b = x.type === 'BC' || x.warehouse === 'BC'
                        onAlert &&
                          onAlert(
                            x.sku,
                            x.type === 'OWN' ? 'own' : 'platform',
                            _b ? '' : x.warehouse,
                          )
                      }}
                      className="clickable"
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 4,
                        fontSize: 'var(--font-9)',
                        color: 'var(--muted2)',
                        lineHeight: 1.3,
                        marginTop: i === 0 ? 0 : 2,
                        overflow: 'hidden',
                        cursor: 'pointer',
                      }}
                    >
                      {lv ? <span style={{ color: lv.c, lineHeight: 1 }}>●</span> : null}
                      <span
                        style={{
                          flex: 1,
                          minWidth: 0,
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                        }}
                      >
                        {x.product_name || x.sku}
                      </span>
                      <span
                        style={{
                          flexShrink: 0,
                          fontSize: 8,
                          fontWeight: 600,
                          color: 'var(--muted)',
                          background: 'var(--bg)',
                          padding: '0 4px',
                          borderRadius: 4,
                        }}
                      >
                        {whLabel}
                      </span>
                    </div>
                  )
                })}
              </div>
            </div>
          )}
        </div>
  )
}
