import React from 'react'

/** 自有仓健康单项(后端 routes/dashboard_own_health.py 产出) */
export interface OwnHealthItem {
  sku: string
  barcode?: string
  product_name?: string
  warehouse?: string
  alert_type: 'own_transfer_gap' | 'own_overstock' | 'own_expiry_risk'
  level: 'error' | 'warning'
  title: string
  description: string
  available_qty?: number
  need_qty?: number
  gap_qty?: number
  cover_days?: number | null
  days_to_expiry?: number | null
  batch_no?: string
  exp_date?: string
}

export interface OwnHealthData {
  items: OwnHealthItem[]
  total: number
  transferGap: number
  overstock: number
  expiryRisk: number
  critical: number
  warning: number
  limits?: { overstock_days?: number; expiry_buffer_days?: number; b2c_days?: number }
}

export interface OwnHealthCardProps {
  data?: OwnHealthData | null
  onAlert?: (sku: string | undefined, whType: string, wh: string) => void
  onShowAll?: () => void
}

const TYPE_META: Record<string, { t: string; cls: string; c: string }> = {
  own_transfer_gap: { t: '调拨缺口', cls: 'danger', c: 'var(--danger)' },
  own_expiry_risk: { t: '效期', cls: 'danger', c: 'var(--danger)' },
  own_overstock: { t: '积压', cls: 'warning', c: 'var(--warning)' },
}

const fmtInt = (n: number | undefined) => (n == null ? '-' : Number(n).toLocaleString())

/**
 * 自有仓健康卡(2026-11-01 新增)
 *
 * 为什么独立成卡 —— 自有仓(集货仓)是供给侧, 不产生销售:
 *   daily_sales_snapshot 只记平台仓, 自有仓名「集货仓」无销售行 → 逐仓日销 ds=0 →
 *   低库存/断货口径在自有仓维度结构性恒不触发。所以自有仓要判的是三件事:
 *   「下游要货备不备得出(调拨缺口)」「压在仓里是不是太多(积压)」「会不会过期(效期)」。
 *   用户明确: 这三类塞进「采购&补货」卡会形成噪音(那张卡回答"哪些要补/要买"),
 *   语义相反(积压/过期是"别买了"), 故单独一张卡。
 */
export default function OwnHealthCard({ data, onAlert, onShowAll }: OwnHealthCardProps) {
  const d: OwnHealthData = data || {
    items: [],
    total: 0,
    transferGap: 0,
    overstock: 0,
    expiryRisk: 0,
    critical: 0,
    warning: 0,
  }
  const items = d.items || []
  const empty = !d.total

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
          自有仓健康
        </div>
        {/* 右上角只放 error 计数 —— 与「待处理」卡一致, 不重复列 warning */}
        {d.critical > 0 && (
          <span
            className="pill danger"
            style={{ fontSize: 'var(--font-9)', padding: '1px 6px', minHeight: 'auto', lineHeight: '16px' }}
          >
            严重 {fmtInt(d.critical)}
          </span>
        )}
      </div>

      {empty ? (
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
              fontSize: 20,
            }}
          >
            ✓
          </div>
          <div className="small muted" style={{ fontSize: 'var(--font-xs)' }}>
            自有仓均正常
          </div>
        </div>
      ) : (
        <>
          {/* 大数字 = 总条数; 下面一行拆三类(与「待处理」卡同一信息层级) */}
          <div
            style={{
              fontSize: 'var(--font-xl)',
              fontWeight: 700,
              lineHeight: 1.1,
              marginTop: 4,
              color: d.critical > 0 ? 'var(--danger)' : 'var(--warning)',
            }}
          >
            {fmtInt(d.total)}
            <span
              style={{
                fontSize: 'var(--font-xs)',
                fontWeight: 400,
                color: 'var(--muted)',
                marginLeft: 4,
              }}
            >
              项待处置
            </span>
          </div>
          <div
            className="small muted"
            style={{
              display: 'flex',
              flexWrap: 'wrap',
              gap: 6,
              fontSize: 'var(--font-9)',
              marginTop: 2,
            }}
          >
            <span style={{ color: d.transferGap > 0 ? 'var(--danger)' : 'var(--muted)' }}>
              ● 调拨缺口 {fmtInt(d.transferGap)}
            </span>
            <span style={{ color: d.expiryRisk > 0 ? 'var(--danger)' : 'var(--muted)' }}>
              ● 效期 {fmtInt(d.expiryRisk)}
            </span>
            <span style={{ color: d.overstock > 0 ? 'var(--warning)' : 'var(--muted)' }}>
              ● 积压 {fmtInt(d.overstock)}
            </span>
          </div>

          {/* 明细 TOP3 —— 点行进库存页对应 SKU(与断货卡同一交互) */}
          <div style={{ marginTop: 6 }}>
            {items.slice(0, 3).map((x, i) => {
              const m = TYPE_META[x.alert_type] || { t: '预警', cls: 'warning', c: 'var(--warning)' }
              return (
                <div
                  key={x.alert_type + x.sku + i}
                  onClick={() => onAlert && onAlert(x.sku, 'own', x.warehouse || '')}
                  className="clickable"
                  style={{
                    padding: '5px 0',
                    borderTop: i === 0 ? 'none' : '1px solid var(--border)',
                    display: 'flex',
                    alignItems: 'center',
                    gap: 6,
                    minWidth: 0,
                  }}
                >
                  <span
                    className={'pill ' + m.cls}
                    style={{
                      flexShrink: 0,
                      fontSize: 'var(--font-9)',
                      padding: '1px 5px',
                      minHeight: 'auto',
                      lineHeight: '14px',
                    }}
                  >
                    {m.t}
                  </span>
                  <span
                    style={{
                      flex: 1,
                      minWidth: 0,
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                      fontSize: 'var(--font-xs)',
                    }}
                  >
                    {x.product_name || x.sku}
                  </span>
                  <span
                    style={{
                      flexShrink: 0,
                      fontSize: 'var(--font-9)',
                      fontWeight: 600,
                      color: m.c,
                    }}
                  >
                    {x.alert_type === 'own_transfer_gap'
                      ? '缺 ' + fmtInt(x.gap_qty)
                      : x.alert_type === 'own_expiry_risk'
                        ? (x.days_to_expiry != null && x.days_to_expiry < 0
                            ? '过期' + Math.abs(x.days_to_expiry) + '天'
                            : '剩' + fmtInt(x.days_to_expiry ?? undefined) + '天')
                        : '撑' + Math.round(Number(x.cover_days || 0)) + '天'}
                  </span>
                </div>
              )
            })}
          </div>

          {d.total > 3 && (
            <div
              onClick={onShowAll}
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
              更多 {fmtInt(d.total - 3)}
            </div>
          )}
        </>
      )}
    </div>
  )
}
