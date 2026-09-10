import React from 'react'

type IconProps = { size?: number; className?: string; style?: React.CSSProperties }

const s = (p: IconProps) => ({ width: p.size ?? 20, height: p.size ?? 20 })

// ─── 导航图标 ───

export const IconChart: React.FC<IconProps> = (p) => (
  <svg {...s(p)} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <rect x="3" y="3" width="8" height="8" rx="1.5" />
    <rect x="13" y="3" width="8" height="4" rx="1.5" />
    <rect x="3" y="13" width="8" height="8" rx="1.5" />
    <rect x="13" y="9" width="8" height="12" rx="1.5" />
  </svg>
)

export const IconTag: React.FC<IconProps> = (p) => (
  <svg {...s(p)} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <path d="M6 2L3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4z" />
    <line x1="3" y1="6" x2="21" y2="6" />
    <path d="M16 10a4 4 0 0 1-8 0" />
  </svg>
)

export const IconFactory: React.FC<IconProps> = (p) => (
  <svg {...s(p)} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <rect x="2" y="7" width="20" height="14" rx="2" />
    <path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16" />
    <path d="M7 11h2M15 11h2M7 15h2M15 15h2" />
  </svg>
)

export const IconClipboard: React.FC<IconProps> = (p) => (
  <svg {...s(p)} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
    <polyline points="14 2 14 8 20 8" />
    <line x1="8" y1="13" x2="16" y2="13" />
    <line x1="8" y1="17" x2="16" y2="17" />
    <circle cx="10" cy="10" r="1" fill="currentColor" />
  </svg>
)

export const IconPackage: React.FC<IconProps> = (p) => (
  <svg {...s(p)} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <path d="M4 4h16v4H4zM4 10h16v4H4zM4 16h12v4H4z" />
    <line x1="18" y1="16" x2="22" y2="20" />
    <line x1="18" y1="20" x2="22" y2="16" />
  </svg>
)

export const IconLightbulb: React.FC<IconProps> = (p) => (
  <svg {...s(p)} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <path d="M3 21V3h18" strokeWidth="1.5" />
    <polyline points="5 17 10 11 14 14 20 7" strokeWidth="2.5" />
    <polyline points="15 7 20 7 20 12" strokeWidth="2.5" />
    <circle cx="20" cy="7" r="2" fill="currentColor" />
  </svg>
)

export const IconBroom: React.FC<IconProps> = (p) => (
  <svg {...s(p)} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <polygon points="22 3 21 5 19 6 21 7 22 9 23 7 25 6 23 5 22 3" />
    <polygon points="9 3 8 5 6 6 8 7 9 9 10 7 12 6 10 5 9 3" />
    <path d="M6 12l-4 8h20l-4-8" />
    <line x1="10" y1="20" x2="10" y2="16" />
    <line x1="14" y1="20" x2="14" y2="16" />
  </svg>
)

export const IconGear: React.FC<IconProps> = (p) => (
  <svg {...s(p)} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <rect x="1" y="5" width="22" height="14" rx="2" />
    <circle cx="7" cy="12" r="3" fill="currentColor" />
    <line x1="10" y1="12" x2="20" y2="12" />
  </svg>
)

export const IconAlert: React.FC<IconProps> = (p) => (
  <svg {...s(p)} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="10" />
    <line x1="12" y1="8" x2="12" y2="13" />
    <circle cx="12" cy="16" r="1" fill="currentColor" />
  </svg>
)

// ─── 通用图标 ───

export const IconSearch: React.FC<IconProps> = (p) => (
  <svg {...s(p)} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="11" cy="11" r="8" /><path d="m21 21-4.35-4.35" />
  </svg>
)

export const IconTrash: React.FC<IconProps> = (p) => (
  <svg {...s(p)} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M3 6h18" /><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" />
    <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
    <line x1="10" y1="11" x2="10" y2="17" /><line x1="14" y1="11" x2="14" y2="17" />
  </svg>
)

export const IconClose: React.FC<IconProps> = (p) => (
  <svg {...s(p)} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
    <line x1="6" y1="6" x2="18" y2="18" /><line x1="18" y1="6" x2="6" y2="18" />
  </svg>
)

export const IconCheck: React.FC<IconProps> = (p) => (
  <svg {...s(p)} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="4 12 10 18 20 6" />
  </svg>
)

export const IconUndo: React.FC<IconProps> = (p) => (
  <svg {...s(p)} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="1 4 1 10 7 10" /><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10" />
  </svg>
)

export const IconSave: React.FC<IconProps> = (p) => (
  <svg {...s(p)} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2Z" />
    <polyline points="17 21 17 13 7 13 7 21" /><polyline points="7 3 7 8 15 8" />
  </svg>
)

export const IconLightning: React.FC<IconProps> = (p) => (
  <svg {...s(p)} viewBox="0 0 24 24" fill="currentColor" stroke="none">
    <path d="M13 2L4 14h6v8l9-12h-6V2Z" />
  </svg>
)

export const IconFolder: React.FC<IconProps> = (p) => (
  <svg {...s(p)} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2v11Z" />
  </svg>
)

export const IconEmpty: React.FC<IconProps> = (p) => (
  <svg {...s(p)} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <rect x="2" y="4" width="20" height="16" rx="2" />
    <path d="M2 10h20" /><path d="M6 14h2" /><path d="M10 14h2" /><path d="M14 14h2" />
  </svg>
)

export const IconCart: React.FC<IconProps> = (p) => (
  <svg {...s(p)} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="9" cy="21" r="1" /><circle cx="20" cy="21" r="1" />
    <path d="M1 1h4l2.68 13.39a2 2 0 0 0 2 1.61h9.72a2 2 0 0 0 2-1.61L23 6H6" />
  </svg>
)

export const IconScale: React.FC<IconProps> = (p) => (
  <svg {...s(p)} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 3v18" /><path d="M3 12h18" />
    <path d="M9 3h6" /><path d="M9 21h6" />
  </svg>
)

export const IconImport: React.FC<IconProps> = (p) => (
  <svg {...s(p)} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><polyline points="7 10 12 15 17 10" /><line x1="12" y1="15" x2="12" y2="3" />
  </svg>
)

export const IconExport: React.FC<IconProps> = (p) => (
  <svg {...s(p)} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><polyline points="17 8 12 3 7 8" /><line x1="12" y1="3" x2="12" y2="15" />
  </svg>
)

// ─── 趋势箭头 ───

export const IconTrendUp: React.FC<IconProps> = (p) => (
  <svg {...s(p)} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="23 6 13.5 15.5 8.5 10.5 1 18" /><polyline points="17 6 23 6 23 12" />
  </svg>
)

export const IconTrendDown: React.FC<IconProps> = (p) => (
  <svg {...s(p)} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="23 18 13.5 8.5 8.5 13.5 1 6" /><polyline points="17 18 23 18 23 12" />
  </svg>
)

export const IconTrendFlat: React.FC<IconProps> = (p) => (
  <svg {...s(p)} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
    <line x1="1" y1="12" x2="23" y2="12" /><polyline points="18 9 23 12 18 15" />
  </svg>
)

// ─── 状态指示 ───

export const IconStatusOnline: React.FC<IconProps> = (p) => (
  <svg {...s(p)} viewBox="0 0 24 24" fill="var(--success)">
    <circle cx="12" cy="12" r="6" />
  </svg>
)

export const IconStatusWarning: React.FC<IconProps> = (p) => (
  <svg {...s(p)} viewBox="0 0 24 24" fill="var(--accent-yellow)">
    <circle cx="12" cy="12" r="6" />
  </svg>
)

export const IconStatusOffline: React.FC<IconProps> = (p) => (
  <svg {...s(p)} viewBox="0 0 24 24" fill="var(--danger)">
    <circle cx="12" cy="12" r="6" />
  </svg>
)

export const IconStatusInfo: React.FC<IconProps> = (p) => (
  <svg {...s(p)} viewBox="0 0 24 24" fill="#3b82f6">
    <circle cx="12" cy="12" r="6" />
  </svg>
)

// ─── Loading spinner ───

export const IconLoading: React.FC<IconProps> = (p) => (
  <svg {...s(p)} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" style={{ animation: 'spin 1s linear infinite', ...p.style }}>
    <path d="M21 12a9 9 0 1 1-6.22-8.56" />
  </svg>
)

export const IconRefresh: React.FC<IconProps> = (p) => (
  <svg {...s(p)} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
    <path d="M23 4v6h-6" /><path d="M1 20v-6h6" /><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
  </svg>
)

// ─── 设置图标 ───

export const IconSettings: React.FC<IconProps> = (p) => (
  <svg {...s(p)} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="3" strokeWidth="1.5" />
    <path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42" strokeWidth="1.5" />
    <line x1="15" y1="9" x2="19" y2="5" strokeWidth="2" strokeLinecap="round" />
  </svg>
)

// ─── 额外图标 ───

export const IconWarning: React.FC<IconProps> = (p) => (
  <svg {...s(p)} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0Z" />
    <path d="M12 9v4" /><path d="M12 17h.01" />
  </svg>
)

export const IconCircle: React.FC<IconProps> = (p) => (
  <svg {...s(p)} viewBox="0 0 24 24" fill="currentColor" stroke="none">
    <circle cx="12" cy="12" r="6" />
  </svg>
)

export const IconPlus: React.FC<IconProps> = (p) => (
  <svg {...s(p)} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
    <line x1="12" y1="5" x2="12" y2="19" /><line x1="5" y1="12" x2="19" y2="12" />
  </svg>
)

export const IconBulb: React.FC<IconProps> = (p) => (
  <svg {...s(p)} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M9 18h6" /><path d="M10 22h4" />
    <path d="M15.09 14c.6-.77 1.05-1.6 1.32-2.5A5.4 5.4 0 0 0 12 6a5.4 5.4 0 0 0-4.41 5.5c.27.9.72 1.73 1.32 2.5" />
    <path d="M9 18c0-1.5.5-2.9 1.5-4h3c1 1.1 1.5 2.5 1.5 4" />
  </svg>
)

// ─── 图标查找表 ───

export const NAV_ICONS: Record<string, React.FC<IconProps>> = {
  dash: IconChart,
  products: IconTag,
  suppliers: IconFactory,
  orders: IconClipboard,
  inv: IconPackage,
  insights: IconLightbulb,
  cleansing: IconBroom,
  rules: IconGear,
  quality: IconAlert,
  settings: IconSettings,
}

// 根据 key 选择趋势图标
export const trendIcon = (current: number, prev: number): React.ReactNode => {
  const Icon = current > prev * 1.15 ? IconTrendUp : current < prev * 0.85 ? IconTrendDown : IconTrendFlat
  return <Icon size={12} />
}

// status display text
export const STATUS_LABEL: Record<string, { label: string; Icon: React.FC<IconProps> }> = {
  connected: { label: '实时', Icon: IconStatusOnline },
  polling: { label: '轮询', Icon: IconStatusWarning },
  disconnected: { label: '断开', Icon: IconStatusOffline },
}

// ─── 滞销处置动作图标(2026-09-06) ───

export const IconReturn: React.FC<IconProps> = (p) => (
  <svg {...s(p)} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="9 14 4 9 9 4" />
    <path d="M20 20v-7a4 4 0 0 0-4-4H4" />
  </svg>
)

export const IconMoney: React.FC<IconProps> = (p) => (
  <svg {...s(p)} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <rect x="2" y="6" width="20" height="12" rx="2" />
    <circle cx="12" cy="12" r="2.5" />
    <path d="M6 12h.01M18 12h.01" />
  </svg>
)
