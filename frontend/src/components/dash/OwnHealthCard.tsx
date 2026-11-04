import React from 'react'
import { t } from '../../locale'

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
const isNarrow = () => typeof window !== 'undefined' && window.innerWidth <= 932
const fmtInt = (n?: number): string => {
  if (n == null) return '-'
  const v = Math.round(Number(n) || 0)
  if (Math.abs(v) >= (isNarrow() ? 1e4 : 1e5)) return Math.round(v / 1e4) + t('dash.unit_wan')
  return String(v)
}

// 三类元信息: 标签文案 / pill 色 / 度量语义
//   pill 配色对齐「采购&补货」大卡范式(tag='采购'→warning, '补货'→danger)
const TYPE_META: Record<
  string,
  { tag: string; cls: string; c: string; order: number; desc: string }
> = {
  own_transfer_gap: {
    tag: t('own.gap'),
    cls: 'danger',
    c: 'var(--danger)',
    order: 0,
    desc: t('own.gap_desc'),
  },
  own_expiry_risk: {
    tag: t('own.expiry'),
    cls: 'danger',
    c: 'var(--danger)',
    order: 1,
    desc: t('own.expiry_desc'),
  },
  own_overstock: {
    tag: t('own.overstock'),
    cls: 'warning',
    c: 'var(--warning)',
    order: 2,
    desc: t('own.overstock_desc'),
  },
}

// 每类的右侧度量(与 pill 同色的加粗数字)
const metricOf = (x: OwnHealthItem): { v: string; hint: string } => {
  if (x.alert_type === 'own_transfer_gap')
    return { v: t('own.lack', { n: fmtInt(x.gap_qty) }), hint: fn(x) }
  if (x.alert_type === 'own_expiry_risk') {
    const d = x.days_to_expiry
    if (d == null) return { v: '—', hint: '' }
    return {
      v: d < 0 ? t('own.expired_d', { n: Math.abs(d) }) : t('own.left_d', { n: fmtInt(d) }),
      hint: '',
    }
  }
  return { v: t('own.cover_d', { n: Math.round(Number(x.cover_days || 0)) }), hint: '' }
}
const fn = (x: OwnHealthItem) =>
  x.need_qty != null && x.available_qty != null
    ? t('own.need_can', { a: fmtInt(x.need_qty), b: fmtInt(x.available_qty) })
    : ''

/**
 * 自有仓健康大卡(2026-11-01 由小卡改大卡)
 *
 * 为什么从 `stat-card` 小卡改成 `chart-row-3` 大卡:
 *  1. **数据体量不匹配** —— 实测 90 项(缺口 9 / 效期 46 / 积压 35), 小卡内容区
 *     134~192px 扣掉标题/大数字/副行/计数行的固定开销 80px 后, 只剩 54~112px,
 *     最多 3~8 行(且只放了 3 行), 信息损耗 >95%;
 *  2. **三类语义不同质, 不能混排** —— 缺口=要行动, 积压=要止损, 效期=要调拨/报损。
 *     小卡里按 sort_key 混排后前 3 行全是缺口, 效期 46 项一条都看不到;
 *  3. **性质是运营处置清单**, 与低库存/采购补货同类, 本就该是大卡;
 *  4. **小卡已有 5 张**, 那是"概览"层; 这张是"清单"层。
 *
 * 结构对齐「采购&补货」大卡(规范 §四):
 *   `.section-title`(标题+计数) → 5 行明细(行首 pill 标签区分类型 + 名称 + 右侧度量
 *   + 第二行元信息) → 底部「更多 N」pill info → 全量弹窗。
 *
 * 交互(用户定): 计数/大数字/行 任一点击 → **统一进全量弹窗**, 弹窗内用标签区分类型
 *   (参考采购补货卡), 不做按类型预筛选。
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
  // 按类型分组(组内保持后端 sort_key 顺序: 缺口大的/到期近的/积压久的)
  const order = ['own_transfer_gap', 'own_expiry_risk', 'own_overstock']
  const groups = order
    .map(k => ({ k, rows: items.filter(x => x.alert_type === k) }))
    .filter(g => g.rows.length > 0)

  // 大卡显示前 5 行, 但**保证三类都露头**(否则又回到小卡"只看得到缺口"的老问题):
  //   先每类取 1 条(若有), 再按剩余额度顺序补足到 5 条
  const shown: OwnHealthItem[] = []
  groups.forEach(g => {
    if (g.rows[0]) shown.push(g.rows[0])
  })
  const shownKeys = new Set(shown.map(x => x.alert_type + x.sku))
  for (const g of groups) {
    for (const x of g.rows) {
      if (shown.length >= 5) break
      const key = x.alert_type + x.sku
      if (shownKeys.has(key)) continue
      shown.push(x)
      shownKeys.add(key)
    }
  }
  shown.length = Math.min(shown.length, 5)

  return (
    <div className="card" style={{ height: 'auto', overflow: 'visible' }}>
      <div className="section-title">
        {t('own.title')}
        {d.total > 0 ? ` (${fmtInt(d.total)})` : ''}
      </div>

      {d.total === 0 ? (
        <div className="small muted" style={{ padding: 12, textAlign: 'center' }}>
          {t('own.all_ok')}
        </div>
      ) : (
        <>
          {/* 2026-11-01 汇总计数行已整行移除(用户: "这行也要去除")。
              理由: 三类计数在 .section-title 的总数 (N) 之外属重复信息, 且每行行首 pill
              已标类型、右侧已带度量, 卡面不需要再复述一遍分类小计。
              标题 (N) 即总量, 类型分布看 5 行明细或「更多」弹窗。 */}

          {shown.map((x, i) => {
            const m = TYPE_META[x.alert_type] || {
              tag: t('own.warn'),
              cls: 'warning',
              c: 'var(--warning)',
              order: 9,
              desc: '',
            }
            const mt = metricOf(x)
            return (
              <div
                key={x.alert_type + x.sku + i}
                title={x.description || ''}
                onClick={() => onAlert && onAlert(x.sku, 'own', x.warehouse || '')}
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
                      className={'pill ' + m.cls}
                      style={{
                        flexShrink: 0,
                        fontSize: 'var(--font-9)',
                        padding: '1px 6px',
                        minHeight: 'auto',
                        lineHeight: '16px',
                      }}
                    >
                      {m.tag}
                    </span>
                    {x.product_name || x.sku}
                  </span>
                  <span
                    style={{
                      flexShrink: 0,
                      fontWeight: 'var(--fw-bold)',
                      fontSize: 'var(--font-sm)',
                      color: m.c,
                      fontVariantNumeric: 'tabular-nums',
                    }}
                  >
                    {mt.v}
                  </span>
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
                  {x.warehouse ? ' · ' + x.warehouse : ''}
                  {mt.hint ? ' · ' + mt.hint : ''}
                  {x.description ? ' · ' + String(x.description).slice(0, 40) : ''}
                </div>
              </div>
            )
          })}

          {d.total > shown.length && (
            <div
              onClick={onShowAll}
              aria-label={t('dash.more_aria', { n: d.total - shown.length })}
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
              {t('dash.more_n', { n: fmtInt(d.total - shown.length) })}
            </div>
          )}
        </>
      )}
    </div>
  )
}
