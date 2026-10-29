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
      <div
        onClick={onClose}
        style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.28)', zIndex: 9998 }}
      />
      <div
        className="material-regular"
        style={{
          position: 'fixed',
          left: 0,
          right: 0,
          bottom: 0,
          zIndex: 9999,
          borderTopLeftRadius: 'var(--radius-lg)',
          borderTopRightRadius: 'var(--radius-lg)',
          padding: '16px 16px calc(16px + env(safe-area-inset-bottom))',
          boxShadow: 'var(--shadow-sheet)',
          maxHeight: '70dvh',
          overflowY: 'auto',
        }}
      >
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            marginBottom: 10,
          }}
        >
          <span style={{ fontSize: 16, fontWeight: 700 }}>数据新鲜度</span>
          <span
            className="clickable"
            onClick={onClose}
            style={{ fontSize: 13, color: 'var(--muted2)', padding: '4px 6px' }}
          >
            关闭
          </span>
        </div>
        <div style={{ fontSize: 11, color: 'var(--muted2)', marginBottom: 12, lineHeight: 1.5 }}>
          数据来自 ERP / 平台导出快照，超期建议重新导入对应类型
        </div>
        <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--muted)', marginBottom: 6 }}>
          看板指标
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', marginBottom: 14 }}>
          {(data.metrics || []).map(m => (
            <div
              key={m.key}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                padding: '7px 0',
                borderBottom: '1px solid var(--border-light)',
              }}
            >
              <span
                style={{
                  flex: '1 1 auto',
                  minWidth: 0,
                  fontSize: 13,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
              >
                {m.label}
              </span>
              <span
                style={{
                  flex: '0 0 auto',
                  maxWidth: 160,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                  fontSize: 12,
                  color: LEVEL_COLOR[m.level] || 'var(--muted2)',
                }}
              >
                {fmtDays(m.days)}
                {m.from ? ` · ${m.from}` : ''}
              </span>
            </div>
          ))}
        </div>
        <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--muted)', marginBottom: 6 }}>
          原始数据源
        </div>
        <div style={{ display: 'flex', flexDirection: 'column' }}>
          {(data.sources || []).map(s2 => (
            <div
              key={s2.doc_type}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                padding: '7px 0',
                borderBottom: '1px solid var(--border-light)',
              }}
            >
              <span
                style={{
                  flex: '1 1 auto',
                  minWidth: 0,
                  fontSize: 13,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
              >
                {s2.label}
              </span>
              <span
                style={{
                  flex: '0 0 auto',
                  maxWidth: 160,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                  fontSize: 12,
                  color: LEVEL_COLOR[s2.level] || 'var(--muted2)',
                }}
              >
                {s2.last_biz_date || '—'} · {fmtDays(s2.days)}
              </span>
            </div>
          ))}
          {!(data.sources || []).length && (
            <div style={{ fontSize: 12, color: 'var(--muted2)', padding: '8px 0' }}>
              暂无导入记录
            </div>
          )}
        </div>
      </div>
    </>,
    document.body,
  )
}
