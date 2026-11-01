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

/**
 * 自有仓健康小卡(2026-11-01 新增; 同日晚按 docs/UI_DESIGN.md §二 重构)
 *
 * 业务动因 —— 自有仓(集货仓)是供给侧, 不产生销售:
 *   daily_sales_snapshot 只记平台仓, 自有仓无销售行 → 逐仓日销 ds=0 →
 *   低库存/断货口径在自有仓维度结构性恒不触发。自有仓该判的是三件事:
 *   「下游要货备不备得出(调拨缺口)」「压太多(积压)」「会不会过期(效期)」。
 *   用户明确这三类不能塞进「采购&补货」卡(那张卡答"哪些要补/要买", 积压/过期是"别买了"),
 *   故单独一张卡。
 *
 * ⚠️ 重构要点(初版严重偏离规范, 逐条对齐):
 *  1. **删掉卡内 3 行明细**。小卡是 `aspect-ratio:1` + padding 16, minmax 140px 下内容区仅
 *     ~108px: 大数字(28)+副行+计数行已占 ~62, 再塞 3 行明细+「更多」pill 必然溢出裁切。
 *     规范 §二 明确小卡是"数字主角/语义状态行/紧凑分区", 明细属于大卡(chart-row-3)。
 *     → 明细全部收进「更多」弹窗(Sheet), 小卡只留两级: 大数字 + 语义计数行。
 *  2. **大数字用 `.card-value` + clamp(17px,8cqi,28px) + tabular-nums**。原 `var(--font-xl)`
 *     是固定值, 窄卡/横屏不流体, 与 GMV 卡不同规格。
 *  3. **计数行 font-9 + 语义色 ● **(与断货卡三级计数同构)。原用 pill 标签当列表前缀,
 *     pill 语义是状态(启用/停用), 误用成行首标签, 且视觉重量压过大数字。
 *  4. **数字区 flex-end 贴底 + 底部无差异化 margin**(规范 §二"上松下紧"、16 兜底)。
 *     原「更多」pill 在内容流末尾, 使底部留白取 pill 下沿而非 16, 4 卡底部几何不一致。
 *  5. **空态 SVG 图标**(规范 §十一 禁 emoji)。原用 '✓' 文本字符。
 *  6. **计数项为 0 时保留位占位不显示**(规范 §二"空态显示灰色 0 保持行位稳定") ——
 *     这里三项恒显示: 0 值走 --muted2 灰, 避免卡内行数随数据跳动。
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
        {/* 右上角严重数: 与「待处理」卡一致的 error 计数, 不重复列 warning */}
        {d.critical > 0 && (
          <span
            style={{
              fontSize: 'var(--font-9)',
              fontWeight: 600,
              color: 'var(--danger)',
              lineHeight: 1.2,
              flexShrink: 0,
            }}
          >
            ● 严重 {fmtInt(d.critical)}
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
          {/* 大数字: 内容贴底(上松下紧) */}
          <div style={{ marginTop: 4 }}>
            <div
              className="card-value"
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
          </div>
          {/* 语义计数行: 三项恒占位(0 → 灰), 与断货卡三级计数同构; 点击进弹窗 */}
          <div
            onClick={onShowAll}
            className="clickable"
            style={{
              display: 'flex',
              gap: 6,
              marginTop: 4,
              flexWrap: 'wrap',
              lineHeight: 1.2,
              cursor: 'pointer',
            }}
          >
            {segs.map(s => (
              <span
                key={s.k}
                style={{
                  fontSize: 'var(--font-9)',
                  fontWeight: 600,
                  color: s.n > 0 ? s.c : 'var(--muted2)',
                  whiteSpace: 'nowrap',
                }}
              >
                {s.n > 0 ? '● ' : ''}
                {s.k} {fmtInt(s.n)}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
