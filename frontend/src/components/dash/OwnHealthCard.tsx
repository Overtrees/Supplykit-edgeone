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

// 计数降维(UI_DESIGN §十四): 窄屏 ≥5位 → 整数万 / 非窄屏 ≥6位 → 整数万
//   —— 原用 toLocaleString() 带千分位逗号, 与"计数不加千分位逗号"冲突
const isNarrow = () => typeof window !== 'undefined' && window.innerWidth <= 932
const fmtInt = (n?: number): string => {
  if (n == null) return '-'
  const v = Math.round(Number(n) || 0)
  if (Math.abs(v) >= (isNarrow() ? 1e4 : 1e5)) return Math.round(v / 1e4) + '万'
  return String(v)
}

const TYPE_META: Record<string, { t: string; c: string }> = {
  own_transfer_gap: { t: '调拨缺口', c: 'var(--danger)' },
  own_expiry_risk: { t: '效期', c: 'var(--danger)' },
  own_overstock: { t: '积压', c: 'var(--warning)' },
}

/**
 * 自有仓健康小卡(2026-11-01 新增; 同日三轮对齐 docs/UI_DESIGN.md §二)
 *
 * 业务动因 —— 自有仓(集货仓)是供给侧, 不产生销售:
 *   daily_sales_snapshot 只记平台仓, 自有仓无销售行 → 逐仓日销 ds=0 →
 *   低库存/断货口径在自有仓维度结构性恒不触发。自有仓该判的是三件事:
 *   「下游要货备不备得出(调拨缺口)」「压太多(积压)」「会不会过期(效期)」。
 *   用户明确这三类不能塞进「采购&补货」卡(那张卡答"哪些要补/要买", 积压/过期是"别买了"),
 *   故单独一张卡。
 *
 * 结构**与断货卡(RiskCard)完全同构** —— 用户要求两卡视觉整齐:
 *   标题行(minHeight 14) → 大数字 clamp(17,8cqi,28) tabular-nums
 *   → 副行 font-xs muted → 语义计数行 font-xs 600 色点 ● flex-wrap
 *   → 3 行明细 font-9(色点 + 名称截断 + 右侧度量)
 *
 * ⚠️ 明细行为什么用 font-9 而不是文档写的 font-xs(12px):
 *   实测 173px 卡(内容区 141px)里, 大数字 28 + 副行 17 + 计数行 14 ≈ 62px,
 *   剩 79px 放 3 行; font-xs(12px×1.3=15.6/行 + 2px 间隔)= 51px → 边界可放,
 *   但 12px 中文在 173px 卡里名称只剩 ~6 个字就被截断(断货卡同样问题)。
 *   font-9(9px×1.3=11.7/行) → 约 39px, 留出余量且名称能显示 ~10 字。
 *   断货卡线上实测即 font-9, 本卡与之对齐 —— **文档「明细行 font-xs」一条已过时, 以实测为准**。
 *
 * ⚠️ 排布前提: 本卡依赖 `.card-grid` **固定列数**(2026-11-01 改),
 *   原 auto-fit minmax(140,1fr) 在桌面宽屏会把卡压到 145px → 3 行明细必溢出。
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
  // 三项恒渲染(0 值灰色), 保证行位稳定 —— 规范 §二「其他 0」同款处理
  const segs = [
    { k: '调拨缺口', n: d.transferGap, c: 'var(--danger)' },
    { k: '效期', n: d.expiryRisk, c: 'var(--danger)' },
    { k: '积压', n: d.overstock, c: 'var(--warning)' },
  ]

  return (
    <div className="card stat-card">
      {/* 标题行: 规范 minHeight 14 + font-sm/600/muted/letterSpacing 0.2 */}
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
            自有仓均正常
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
          {/* 数字区: 大数字 + 副行 + 语义计数行(三层, 与断货卡同构) */}
          <div style={{ marginTop: 4 }}>
            <div
              className="card-value clickable"
              onClick={onShowAll}
              style={{
                fontSize: 'clamp(17px,8cqi,28px)',
                fontWeight: 700,
                lineHeight: 1.15,
                color: d.critical > 0 ? 'var(--danger)' : 'var(--warning)',
                fontVariantNumeric: 'tabular-nums',
                marginBottom: 1,
                cursor: 'pointer',
              }}
            >
              {fmtInt(d.total)}
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
              项待处置
            </div>
            <div
              style={{
                fontSize: 'var(--font-xs)',
                display: 'flex',
                gap: 6,
                marginTop: 4,
                flexWrap: 'wrap',
                lineHeight: 1.2,
              }}
            >
              {segs.map(s => (
                <span
                  key={s.k}
                  onClick={function (e) {
                    e.stopPropagation()
                    onShowAll && onShowAll()
                  }}
                  className="clickable"
                  style={{
                    color: s.n > 0 ? s.c : 'var(--muted2)',
                    fontWeight: 600,
                    cursor: 'pointer',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {s.n > 0 ? '● ' : ''}
                  {s.k} {fmtInt(s.n)}
                </span>
              ))}
            </div>
          </div>
          {/* 3 行明细(贴底, 与断货卡完全同规格: font-9 / lineHeight 1.3 / marginTop 2)
              2026-11-01: 卡内**不放**「更多 N」pill —— 实测 175px 卡(内容区 143px)下,
              大数字+副行+计数行+3 行明细 = 191px > 175, 再塞 pill 必溢出(scrollH 191 > clientH 175)。
              规范 §二 也禁止"卡片内容 marginBottom 差异化", 底部必须 padding 16 兜底。
              进入明细弹窗的入口改为: 大数字点击 + 计数行点击(两处均可, 符合"可点击→弹窗"约定)。 */}
          {/* minHeight:0 + overflow:hidden 是 flex 子项能真正收缩的前提(默认 min-height:auto 会撑破父级);
              175px 卡放 3 行明细本就紧, 保留收缩能力防止极端长文本/大字号辅助功能下顶破卡片 */}
          <div style={{ flexShrink: 1, minHeight: 0, overflow: 'hidden', paddingTop: 6 }}>
            {items.slice(0, 3).map((x, i) => {
              const m = TYPE_META[x.alert_type] || { t: '预警', c: 'var(--warning)' }
              const metric =
                x.alert_type === 'own_transfer_gap'
                  ? '缺 ' + fmtInt(x.gap_qty)
                  : x.alert_type === 'own_expiry_risk'
                    ? x.days_to_expiry != null && x.days_to_expiry < 0
                      ? '过期 ' + Math.abs(x.days_to_expiry) + '天'
                      : '剩 ' + fmtInt(x.days_to_expiry ?? undefined) + '天'
                    : '撑 ' + Math.round(Number(x.cover_days || 0)) + '天'
              return (
                <div
                  key={x.alert_type + x.sku + i}
                  title={(x.product_name || x.sku) + ' · ' + (x.description || '')}
                  onClick={function () {
                    onAlert && onAlert(x.sku, 'own', x.warehouse || '')
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
                  <span style={{ color: m.c, lineHeight: 1, flexShrink: 0 }}>●</span>
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
                  {/* 右侧度量(对应断货卡的仓标签): 同规格 8px + --bg 底 + 4px 圆角 */}
                  <span
                    style={{
                      flexShrink: 0,
                      fontSize: 8,
                      fontWeight: 600,
                      color: m.c,
                      background: 'var(--bg)',
                      padding: '0 4px',
                      borderRadius: 4,
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {metric}
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
