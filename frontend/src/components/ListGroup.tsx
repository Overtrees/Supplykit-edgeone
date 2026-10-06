import React from 'react'
import { IconCheck } from './Icons'

// 全局列表分组组件(2026-09-15 抽公共: 设置页/开发者页/回收页三处复用, 视觉统一)
// 结构: Group(title uppercase) > card(radius-lg) > Row(padding 0 16 / minHeight 48 / borderBottom)
export const Group = ({ title, children }: any) => (
  <div style={{ marginBottom: 20 }}>
    {title && <div style={{ fontSize: 'var(--font-13)', fontWeight: 400, color: 'var(--muted2)', textTransform: 'uppercase', letterSpacing: 0.3, padding: '0 16px 6px 16px' }}>{title}</div>}
    <div style={{ background: 'var(--card)', borderRadius: 'var(--radius-lg)', overflow: 'hidden' }}>{children}</div>
  </div>
)

export const Row = ({ label, value, sub, onClick, danger, loading }: any) => (
  <div onClick={loading ? undefined : onClick} className={onClick && !loading ? 'clickable' : ''} style={{ padding: '0 16px', cursor: onClick && !loading ? 'pointer' : 'default', background: 'var(--card)', opacity: loading ? 0.5 : 1 }}>
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '14px 0', minHeight: 48, borderBottom: '1px solid var(--border)' }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 'var(--font-lg)', color: danger ? 'var(--danger)' : 'var(--text)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', display: 'flex', alignItems: 'center', gap: 6 }}>
          {loading && <span style={{ display: 'inline-block', width: 14, height: 14, border: '2px solid var(--primary)', borderTopColor: 'transparent', borderRadius: '50%', animation: 'spin 0.6s linear infinite' }} />}
          {label}
        </div>
        {sub && <div style={{ fontSize: 'var(--font-sm)', color: 'var(--muted2)', marginTop: 2 }}>{sub}</div>}
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0, marginLeft: 8 }}>
        {value && <span style={{ fontSize: 'var(--font-15)', color: 'var(--muted2)', maxWidth: 160, textAlign: 'right', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{value}</span>}
        {onClick && <span style={{ color: 'var(--muted2)', fontSize: 18 }}>›</span>}
      </div>
    </div>
  </div>
)

export const LastRow = ({ label, value, sub, onClick, danger }: any) => (
  <div onClick={onClick} className={onClick ? 'clickable' : ''} style={{ padding: '0 16px', cursor: onClick ? 'pointer' : 'default', background: 'var(--card)' }}>
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '14px 0', minHeight: 48 }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 'var(--font-lg)', color: danger ? 'var(--danger)' : 'var(--text)' }}>{label}</div>
        {sub && <div style={{ fontSize: 'var(--font-sm)', color: 'var(--muted2)', marginTop: 2 }}>{sub}</div>}
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0, marginLeft: 8 }}>
        {value && <span style={{ fontSize: 'var(--font-15)', color: 'var(--muted2)', maxWidth: 160, textAlign: 'right', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{value}</span>}
        {onClick && <span style={{ color: 'var(--muted2)', fontSize: 18 }}>›</span>}
      </div>
    </div>
  </div>
)

// 可选中列表行(2026-10-06: 回收页等批量选择场景)——与 Row 同构(padding 0 16 / 内层 14px 0 / minHeight 48 / borderBottom 缩进), 选中高亮 + 复选框
export const ListItem = ({ label, isSel, onClick, right }: any) => (
  <div onClick={onClick} className={onClick ? 'clickable' : ''} style={{ padding: '0 16px', cursor: onClick ? 'pointer' : 'default', background: isSel ? 'rgba(29,78,216,0.08)' : 'var(--card)' }}>
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '14px 0', minHeight: 48, borderBottom: '1px solid var(--border)' }}>
      <span style={{ display: 'flex', alignItems: 'center', gap: 10, flex: 1, minWidth: 0 }}>
        <span style={{ width: 18, height: 18, borderRadius: 'var(--radius-xs)', border: '1.5px solid', borderColor: isSel ? 'var(--primary)' : 'var(--border)', background: isSel ? 'var(--primary)' : 'transparent', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', color: '#fff', fontSize: 'var(--font-xs)', flexShrink: 0 }}>{isSel ? <IconCheck size={12} /> : ''}</span>
        <span style={{ fontSize: 'var(--font-md)', color: 'var(--text)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{label}</span>
      </span>
      {!isSel && right && <span style={{ fontSize: 'var(--font-xs)', color: 'var(--muted2)', flexShrink: 0, marginLeft: 8 }}>{right}</span>}
    </div>
  </div>
)
