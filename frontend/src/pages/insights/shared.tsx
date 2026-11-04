/**
 * 建议页(InsightsPage)的共享件: emoji 备注渲染、列可见性存取、骨架屏。
 * 2026-11-03 从 InsightsPage 外提(第一刀): 纯数据/纯函数/无状态组件, 与 tab 渲染无关。
 * pillStyle/safeGet/safeSet 为零调用死代码, 外提时删除(见 15.136)。
 */
import React from 'react'
import { useAppStore } from '../../store/useAppStore'
import { t } from '../../locale'

// 备注中 emoji 转 SVG 图标
const EMOJI_MAP = {
  '🔴': (
    <svg
      width="14"
      height="14"
      viewBox="0 0 14 14"
      fill="none"
      style={{ display: 'inline', verticalAlign: 'middle', marginRight: 2 }}
    >
      <circle cx="7" cy="7" r="6" fill="var(--danger)" />
    </svg>
  ),
  '⚠️': (
    <svg
      width="14"
      height="14"
      viewBox="0 0 14 14"
      fill="none"
      style={{ display: 'inline', verticalAlign: 'middle', marginRight: 2 }}
    >
      <path d="M7 1.5L1 12.5h12L7 1.5z" fill="var(--warning)" />
      <rect x="6.3" y="5.5" width="1.4" height="4" rx=".7" fill="#fff" />
      <circle cx="7" cy="11" r=".7" fill="#fff" />
    </svg>
  ),
  '⚪': (
    <svg
      width="14"
      height="14"
      viewBox="0 0 14 14"
      fill="none"
      style={{ display: 'inline', verticalAlign: 'middle', marginRight: 2 }}
    >
      <circle cx="7" cy="7" r="6" fill="var(--muted2)" />
    </svg>
  ),
  '✅': (
    <svg
      width="14"
      height="14"
      viewBox="0 0 14 14"
      fill="none"
      style={{ display: 'inline', verticalAlign: 'middle', marginRight: 2 }}
    >
      <circle cx="7" cy="7" r="6" fill="var(--success)" />
      <path
        d="M4.5 7l2 2 3.5-3.5"
        stroke="#fff"
        strokeWidth="1.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  ),
  '📈': (
    <svg
      width="14"
      height="14"
      viewBox="0 0 14 14"
      fill="none"
      style={{ display: 'inline', verticalAlign: 'middle', marginRight: 1 }}
    >
      <path
        d="M2 10l3.5-4 3 2.5L12 3"
        stroke="var(--success)"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M8.5 3H12v3.5"
        stroke="var(--success)"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  ),
  '📉': (
    <svg
      width="14"
      height="14"
      viewBox="0 0 14 14"
      fill="none"
      style={{ display: 'inline', verticalAlign: 'middle', marginRight: 1 }}
    >
      <path
        d="M2 4l3.5 4 3-2.5L12 11"
        stroke="var(--danger)"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M8.5 11H12V7.5"
        stroke="var(--danger)"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  ),
  '➡️': (
    <svg
      width="14"
      height="14"
      viewBox="0 0 14 14"
      fill="none"
      style={{ display: 'inline', verticalAlign: 'middle', marginRight: 1 }}
    >
      <path
        d="M1 7h12M9 3.5L12.5 7 9 10.5"
        stroke="var(--muted)"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  ),
}
/** 趋势方向 → emoji(所有语言通用, 不进词条; 后端 seg.t7/t14 取值 up/down/flat) */
const TREND_EMOJI: Record<string, string> = { up: '📈', down: '📉', flat: '➡️' }

/**
 * 结构化备注片段渲染(2026-11-04): 后端 note_segs = [{k:'b_sug', n:120, ...}, ...],
 * 文案走 `t('seg.'+k, params)` 出双语; 返回 null 表示无片段(调用方回落旧 note 渲染)。
 * 特殊键: trend(t7/t14 → emoji)、turn_over/turn_near(w=after/cur → 本地化周转名)。
 */
export function renderNoteSegs(segs?: any): React.ReactNode {
  if (!Array.isArray(segs) || segs.length === 0) return null
  // 防键名泄漏(本轮铁律): 后端可能先于前端部署, 或未来新增片段类型 —— 只要有一个键
  // 没词条, t() 会回落到 'seg.xxx' 原文, 界面就冒出英文键名(同 RiskSheet 的 dash.urgent)。
  // 此时整段返回 null, 由 renderNote 回落到完整可读的 note 字符串, 而不是显示半个键名。
  const hasSeg = (k: string) => t('seg.' + k) !== 'seg.' + k
  for (const sg of segs) {
    const k = String(sg?.k || '')
    if (!k || !hasSeg(k)) return null
    if (k === 'trend' && sg.x && !hasSeg('trend_x_' + sg.x)) return null
  }
  return segs.map((sg: any, i: number) => {
    const k = String(sg?.k || '')
    const tail = i < segs.length - 1 ? ' · ' : ''
    if (k === 'trend') {
      const s = t('seg.trend', {
        t7: TREND_EMOJI[sg.t7] || '➡️',
        t14: TREND_EMOJI[sg.t14] || '➡️',
      })
      const x = sg.x ? ' ' + t('seg.trend_x_' + sg.x) : ''
      return (
        <React.Fragment key={i}>
          {s}
          {x}
          {tail}
        </React.Fragment>
      )
    }
    if (k === 'turn_over' || k === 'turn_near') {
      return (
        <React.Fragment key={i}>
          {t('seg.' + k, {
            w: t(sg.w === 'after' ? 'seg.w_after' : 'seg.w_cur'),
            v: sg.v,
            t: sg.t,
          })}
          {tail}
        </React.Fragment>
      )
    }
    return (
      <React.Fragment key={i}>
        {t('seg.' + k, sg)}
        {tail}
      </React.Fragment>
    )
  })
}

export function renderNote(text, segs?) {
  // 2026-11-04 结构化优先: 后端新数据带 note_segs(键+参数) → i18n 双语;
  // 旧数据/导出兜底走原 note 字符串(emoji 转 SVG)
  const segNodes = renderNoteSegs(segs)
  if (segNodes) return segNodes
  if (!text) return '-'
  const parts = []
  let i = 0
  while (i < text.length) {
    // 检测多字符 emoji（如 ⚠️ 是 2 个字符）
    const c = text[i]
    const c2 = c + (text[i + 1] || '')
    if (EMOJI_MAP[c2]) {
      parts.push(<React.Fragment key={i}>{EMOJI_MAP[c2]}</React.Fragment>)
      i += 2
    } else if (EMOJI_MAP[c]) {
      parts.push(<React.Fragment key={i}>{EMOJI_MAP[c]}</React.Fragment>)
      i += 1
    } else {
      // 收集连续的非 emoji 文本
      let j = i
      while (j < text.length && !EMOJI_MAP[text[j]] && !EMOJI_MAP[text[j] + (text[j + 1] || '')])
        j++
      parts.push(<React.Fragment key={i}>{text.slice(i, j)}</React.Fragment>)
      i = j
    }
  }
  return parts.length === 1 ? parts[0] : parts
}

export const defVis = cols => {
  const { hammerInsightsTab, channel: globalChannel } = useAppStore.getState()
  try {
    const s = localStorage.getItem(
      'c_cols_insights_' + (hammerInsightsTab || 'replen') + '_' + (globalChannel || 'jd'),
    )
    if (s) {
      const p = JSON.parse(s)
      if (p.length > 0) return p
    }
  } catch {}
  return cols.map(c => c.id)
}
export const defVisTrad = cols => {
  const { channel: globalChannel } = useAppStore.getState()
  try {
    const s = localStorage.getItem('c_cols_insights_traditional_' + (globalChannel || 'jd'))
    if (s) {
      const p = JSON.parse(s)
      if (p.length > 0) return p
    }
  } catch {}
  return cols.map(c => c.id)
}

export const getVis = (m, ch) => {
  try {
    return JSON.parse(localStorage.getItem('c_cols_insights_' + ch + '_' + m) || 'null')
  } catch {
    return null
  }
}
const safeGet = (key, def) => {
  try {
    return localStorage.getItem(key) ?? def
  } catch {
    return def
  }
}
const safeSet = (key, val) => {
  try {
    localStorage.setItem(key, val)
  } catch {}
}

const pillStyle = (cond, yes = 'danger', no = 'info') => ({
  display: 'inline-block',
  padding: '2px 8px',
  borderRadius: 99,
  fontSize: 12,
  fontWeight: "var(--fw-semibold)",
  background: cond ? 'rgba(225,29,72,0.08)' : 'rgba(29,78,216,0.08)',
  color: cond ? 'var(--danger)' : 'var(--primary)',
})

export function Skeleton({ height = 16, width = '100%', style }) {
  return <div className="skeleton" style={{ height, width, ...style }} />
}
