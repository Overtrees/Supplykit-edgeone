import React, { useEffect, useState } from 'react'
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
  anchorTop = 38,
}: {
  data: FreshData | null
  onClose: () => void
  anchorTop?: number
}) {
  const [expanded, setExpanded] = useState(false)
  if (!data) return null
  const lineStyle: React.CSSProperties = {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    fontSize: 11,
    padding: '4px 0',
    borderBottom: '1px solid var(--border)',
    minWidth: 0,
  }
  const nameStyle: React.CSSProperties = {
    flex: '1 1 auto',
    minWidth: 0,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
    color: 'var(--text-secondary)',
  }
  const valStyle = (lv: number): React.CSSProperties => ({
    flex: '0 0 auto',
    maxWidth: 96,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
    fontVariantNumeric: 'tabular-nums',
    fontWeight: 600,
    color: lv === 2 ? 'var(--danger)' : lv === 1 ? 'var(--warning)' : 'var(--success)',
  })
  const fmt = (d: number | null) => (d === null ? '—' : d === 0 ? '今天' : `${d} 天`)

  return (
    <>
      <div style={{ position: 'fixed', inset: 0, zIndex: 9997 }} onClick={onClose} />
      <div
        style={{
          position: 'absolute',
          top: anchorTop,
          left: 0,
          zIndex: 9998,
          width: 232,
          maxWidth: '82vw',
          background: 'var(--card)',
          border: '1px solid var(--border)',
          borderRadius: 14,
          padding: '9px 11px',
          boxShadow: '0 8px 26px rgba(0,0,0,.14)',
        }}
        onClick={e => e.stopPropagation()}
      >
        <div style={{ fontSize: 9, color: 'var(--muted2)', fontWeight: 600, marginBottom: 2 }}>
          看板指标
        </div>
        {data.metrics.map(m => (
          <div key={m.key} style={lineStyle}>
            <span style={nameStyle}>{m.label}</span>
            <span style={valStyle(m.level)}>{fmt(m.days)}</span>
          </div>
        ))}
        <div
          onClick={() => setExpanded(v => !v)}
          style={{
            fontSize: 9,
            color: 'var(--muted2)',
            fontWeight: 600,
            margin: '7px 0 2px',
            cursor: 'pointer',
            userSelect: 'none',
          }}
        >
          原始数据源 {expanded ? '▾' : '▸'}
        </div>
        {expanded &&
          data.sources.map(s => (
            <div key={s.doc_type} style={lineStyle}>
              <span style={nameStyle}>{s.label}</span>
              <span style={valStyle(s.level)}>{fmt(s.days)}</span>
            </div>
          ))}
        <div style={{ fontSize: 9, color: 'var(--muted2)', marginTop: 6, lineHeight: 1.5 }}>
          超期项建议重新导入
        </div>
      </div>
    </>
  )
}
