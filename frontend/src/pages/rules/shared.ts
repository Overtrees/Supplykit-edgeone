import type React from 'react'

/** 规则页各处输入框的统一内联样式(从 RulesPage.tsx 搬出, 供各参数面板共用) */
export const IS: React.CSSProperties = {
  width: '100%',
  padding: '8px 12px',
  fontSize: 'var(--font-lg)',
  border: '1px solid var(--border)',
  borderRadius: 'var(--radius-lg)',
  marginTop: 4,
  outline: 'none',
  background: 'var(--card)',
  boxSizing: 'border-box',
}
