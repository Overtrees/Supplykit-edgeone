import React, { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { api } from '../api/client'

/**
 * 数据新鲜度(2026-10-29)
 * · StaleDot: 各列表页标题末尾的小圆点(仅超期显示, ≤7天不渲染 → 零布局影响)
 * · FreshPopover: 点击圆点看指标级 + 源级明细(文本过长一律 ellipsis)
 */
export type FreshData = {
  sources: Array<{
    doc_type: string
    label: string
    days: number | null
    level: number
    last_biz_date: string
  }>
  metrics: Array<{ key: string; label: string; days: number | null; level: number; from: string }>
  staleLevel: number
}

const DOT_COLOR = ['transparent', 'var(--warning)', 'var(--danger)']
const LEVEL_COLOR = ['var(--muted2)', 'var(--warning)', 'var(--danger)']

const fmtDays = (d: number | null): string => {
  if (d === null || d === undefined) return '未知'
  if (d <= 0) return '今天'
  return d + ' 天前'
}

export function useFreshness(channel?: string) {
  const [data, setData] = useState<FreshData | null>(null)
  useEffect(() => {
    let alive = true
    api
      .get(`/api/data-freshness?channel=${channel || 'jd'}&t=${Date.now()}`, { timeout: 20000 })
      .then((r: any) => {
        if (alive) setData(r?.data || null)
      })
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [channel])
  return data
}

/** 标题末尾小圆点: 仅超期时渲染(7px, 不挤布局) */
export function StaleDot({
  level,
  days,
  label,
}: {
  level: number
  days?: number | null
  label?: string
}) {
  if (!level) return null
  return (
    <span
      title={label ? `${label}数据 ${days ?? '?'} 天未更新` : undefined}
      style={{
        width: 7,
        height: 7,
        borderRadius: '50%',
        background: DOT_COLOR[level],
        display: 'inline-block',
        marginLeft: 5,
        flexShrink: 0,
        verticalAlign: 'middle',
      }}
    />
  )
}

/** 胶囊内左侧圆点(看板): 与「同步中」徽标同位置逻辑, 无条件占位小(有则显示) */
export function StaleDotInline({
  level,
  days,
  onClick,
}: {
  level: number
  days?: number | null
  onClick?: () => void
}) {
  if (!level) return null
  return (
    <span
      onClick={onClick}
      title={`数据 ${days ?? '?'} 天未更新, 点击查看`}
      style={{
        width: 7,
        height: 7,
        borderRadius: '50%',
        background: DOT_COLOR[level],
        display: 'inline-block',
        flexShrink: 0,
        marginRight: 3,
        cursor: 'pointer',
      }}
    />
  )
}

/** 点击圆点后的明细浮层: 指标级 + 源级, 严格 ellipsis 防溢出 */
export function FreshPopover({
  data,
  onClose,
}: {
  data: FreshData | null
  onClose: () => void
  anchorTop?: number
}) {
  // 2026-10-29 改底部弹窗(portal → body): 原 absolute 定位于 header 胶囊内, 被 overflow:hidden 裁断
  if (!data) return null
  return createPortal(
    <>
      {/* 遮罩(项目范式: ConfirmDialog / Dashboard 底部弹窗一致) */}
      <div
        onClick={onClose}
        style={{ position: 'fixed', inset: 0, zIndex: 4000, background: 'transparent' }}
      />
      {/* 面板容器 — 底部居中浮起(非贴底全宽) */}
      <div
        style={{
          position: 'fixed',
          left: 0,
          right: 0,
          bottom: 'calc(env(safe-area-inset-bottom) + 14px)',
          zIndex: 4001,
          display: 'flex',
          justifyContent: 'center',
          padding: '0 14px',
          pointerEvents: 'none',
        }}
      >
        <div
          onClick={e => e.stopPropagation()}
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
          <div className="fresh-title">
            <span>数据新鲜度</span>
            <span className="fresh-close clickable" onClick={onClose}>
              关闭
            </span>
          </div>
          <div className="fresh-hint">数据来自 ERP / 平台导出快照，超期建议重新导入对应类型</div>

          <div className="fresh-section">看板指标</div>
          <div className="fresh-list">
            {(data.metrics || []).map(m => (
              <div key={m.key} className="fresh-row">
                <span className="fresh-label">{m.label}</span>
                <span className="fresh-value" style={{ color: LEVEL_COLOR[m.level] }}>
                  {fmtDays(m.days)}
                  {m.from ? ` · ${m.from}` : ''}
                </span>
              </div>
            ))}
          </div>

          <div className="fresh-section">原始数据源</div>
          <div className="fresh-list">
            {(data.sources || []).map(s2 => (
              <div key={s2.doc_type} className="fresh-row">
                <span className="fresh-label">{s2.label}</span>
                <span className="fresh-value" style={{ color: LEVEL_COLOR[s2.level] }}>
                  {s2.last_biz_date || '—'} · {fmtDays(s2.days)}
                </span>
              </div>
            ))}
            {!(data.sources || []).length && <div className="fresh-empty">暂无导入记录</div>}
          </div>
        </div>
      </div>
    </>,
    document.body,
  )
}
