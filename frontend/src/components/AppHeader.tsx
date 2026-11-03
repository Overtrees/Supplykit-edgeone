import type { MutableRefObject } from 'react'
import { StaleDotInline, FreshPopover } from './Freshness'
import HammerDashboard from './hammer/HammerDashboard'
import HammerProducts from './hammer/HammerProducts'
import HammerSuppliers from './hammer/HammerSuppliers'
import HammerOrders from './hammer/HammerOrders'
import HammerInventory from './hammer/HammerInventory'
import HammerInsights from './hammer/HammerInsights'
import HammerCleansing from './hammer/HammerCleansing'
import HammerRules from './hammer/HammerRules'
import HammerRecycle from './hammer/HammerRecycle'
import HammerHistory from './hammer/HammerHistory'

/**
 * 顶栏 + 锤子菜单抽屉 —— 自 App.tsx 外提(全受控, 状态一行不搬)。
 * 看板页: 左渠道筛选+锤子按钮, 右菜单按钮; 其他页: 左返回, 右锤子+渠道筛选。
 * 抽屉按 page 渲染对应 Hammer* 面板, 未覆盖页面显示占位。
 */
export default function AppHeader({
  page,
  channel,
  setChannel,
  channels,
  fresh,
  freshOpen,
  setFreshOpen,
  headerPill,
  showWelcome,
  showHammerMenu,
  hammerMenuClosing,
  hammerMenuRef,
  toggleHammerMenu,
  closeHammerMenu,
  toggleEditorMenu,
  navigateTo,
  loadHistory,
  hammerData,
  _chLabel,
}: {
  page: string
  channel: string
  setChannel: (v: string) => void
  channels: any[]
  fresh: any
  freshOpen: boolean
  setFreshOpen: (v: any) => void
  headerPill: React.ReactNode
  showWelcome: boolean
  showHammerMenu: boolean
  hammerMenuClosing: boolean
  hammerMenuRef: MutableRefObject<HTMLDivElement | null>
  toggleHammerMenu: () => void
  closeHammerMenu: () => void
  toggleEditorMenu: () => void
  navigateTo: (p: string) => void
  loadHistory: (ch?: any) => void
  hammerData: any
  _chLabel: string
}) {
  return (
    <>
      <header style={{ display: showWelcome ? 'none' : '' }}>
        <div className="header-inner">
          {page === 'dash' ? (
            /* 看板页：左侧渠道筛选+锤子按钮，右侧菜单按钮 */
            <>
              <div className="header-left">
                <span className="header-status" style={{ position: 'relative' }}>
                  <StaleDotInline
                    level={fresh?.staleLevel || 0}
                    days={Math.max(...(fresh?.sources || []).map(s => s.days ?? 0), 0)}
                    onClick={() => setFreshOpen(v => !v)}
                  />
                  {freshOpen && <FreshPopover data={fresh} onClose={() => setFreshOpen(false)} />}
                  <select
                    value={channel}
                    onChange={e => setChannel(e.target.value)}
                    style={{
                      background: 'transparent',
                      border: 'none',
                      outline: 'none',
                      color: 'inherit',
                      fontSize: 'inherit',
                      fontWeight: 'inherit',
                      cursor: 'pointer',
                      padding: 0,
                      margin: 0,
                      appearance: 'none',
                      WebkitAppearance: 'none',
                      MozAppearance: 'none',
                    }}
                  >
                    {(channels && channels.length
                      ? channels.filter(c => c.enabled !== 0)
                      : [
                          { code: 'jd', name: '京东渠道' },
                          { code: 'other', name: '其他渠道' },
                        ]
                    ).map(c => (
                      <option key={c.code} value={c.code}>
                        {c.name}
                      </option>
                    ))}
                  </select>
                  {/* 2026-10-27 同步中徽标(变量 syncBadge 统一, 与渠道文本同字号——曾看板分支残留旧内联 fontSize:12) */}
                  {headerPill}
                </span>
                <button className="hammer-icon-btn" onClick={toggleHammerMenu}>
                  <svg
                    width="18"
                    height="18"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    <path d="M15 12a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v4a4 4 0 0 0 4 4h5a4 4 0 0 0 4-4v-4Z" />
                    <path d="M12 12h9" />
                    <path d="m22 3-3 3" />
                    <path d="m19 3-3 3" />
                    <path d="M12 3v3" />
                    <path d="M12 18v3" />
                  </svg>
                </button>
              </div>
              <button className="menu-btn" onClick={toggleEditorMenu}>
                <svg width="14" height="14" viewBox="0 0 20 20" fill="none">
                  <rect x="2" y="4" width="16" height="1.5" rx=".75" fill="currentColor" />
                  <rect x="2" y="9.25" width="16" height="1.5" rx=".75" fill="currentColor" />
                  <rect x="2" y="14.5" width="16" height="1.5" rx=".75" fill="currentColor" />
                </svg>
              </button>
            </>
          ) : (
            /* 其他页：左侧返回按钮，右侧锤子按钮 + 渠道筛选 */
            <>
              <div className="header-left">
                <button
                  className="back-btn"
                  onClick={() =>
                    navigateTo(
                      page === 'devmode' || page === 'recycle' || page === 'channels'
                        ? 'settings'
                        : 'dash',
                    )
                  }
                >
                  <svg
                    width="18"
                    height="18"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2.5"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    <polyline points="19 12 5 12" />
                    <polyline points="11 18 5 12 11 6" />
                  </svg>
                </button>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <button className="hammer-icon-btn" onClick={toggleHammerMenu}>
                  <svg
                    width="18"
                    height="18"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    <path d="M15 12a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v4a4 4 0 0 0 4 4h5a4 4 0 0 0 4-4v-4Z" />
                    <path d="M12 12h9" />
                    <path d="m22 3-3 3" />
                    <path d="m19 3-3 3" />
                    <path d="M12 3v3" />
                    <path d="M12 18v3" />
                  </svg>
                </button>
                <span className="header-status" style={{ position: 'relative' }}>
                  <select
                    value={channel}
                    onChange={e => setChannel(e.target.value)}
                    style={{
                      background: 'transparent',
                      border: 'none',
                      outline: 'none',
                      color: 'inherit',
                      fontSize: 'inherit',
                      fontWeight: 'inherit',
                      cursor: 'pointer',
                      padding: 0,
                      margin: 0,
                      appearance: 'none',
                      WebkitAppearance: 'none',
                      MozAppearance: 'none',
                    }}
                  >
                    {(channels && channels.length
                      ? channels.filter(c => c.enabled !== 0)
                      : [
                          { code: 'jd', name: '京东渠道' },
                          { code: 'other', name: '其他渠道' },
                        ]
                    ).map(c => (
                      <option key={c.code} value={c.code}>
                        {c.name}
                      </option>
                    ))}
                  </select>
                  {/* 2026-10-27 其他页同步中徽标(与看板页同款, 按本布局 header-status 显示) */}
                  {headerPill}
                </span>
              </div>
            </>
          )}
        </div>
      </header>
      {showHammerMenu && (
        <>
          <div
            onPointerDown={closeHammerMenu}
            style={{
              position: 'fixed',
              inset: 0,
              zIndex: 3001,
              background: 'transparent',
              transition: 'background 220ms ease',
            }}
          />
          <div
            ref={hammerMenuRef}
            onPointerDown={e => e.stopPropagation()}
            className="glass-edge"
            style={{
              position: 'fixed',
              zIndex: 3002,
              right: 16,
              top: 'calc(env(safe-area-inset-top, 0px) + 7px + 46px + 6px)',
              width: 240,
              background: 'var(--glass-bg)',
              backdropFilter: 'var(--glass-filter)',
              WebkitBackdropFilter: 'var(--glass-filter)',
              border: '1px solid var(--glass-border)',
              boxShadow: 'var(--glass-shadow)',
              borderRadius: 26,
              overflow: 'hidden auto',
              maxHeight: 'calc(100vh - 120px)',
              opacity: hammerMenuClosing ? 0 : 1,
              transform: hammerMenuClosing
                ? 'translateY(-10px) scale(0.92)'
                : 'translateY(0) scale(1)',
              transformOrigin: '85% -18px',
              transition: 'opacity 180ms ease, transform 220ms cubic-bezier(0.34,1.56,0.64,1)',
              willChange: 'opacity, transform',
              // 2026-10-13 PWA 底部留空根治: 面板 maxHeight 100vh-120(底不在 home indicator 区)——底部 safe-area padding 多余致 PWA 下按钮下 50px 空白; 统一 16px
              padding: '16px 16px 12px' /* 底部 16→12(导入等矮面板底部白条减) */,
            }}
          >
            {page === 'dash' ? (
              <HammerDashboard channel={channel} />
            ) : page === 'products' ? (
              <HammerProducts channel={channel} />
            ) : page === 'suppliers' ? (
              <HammerSuppliers channel={channel} />
            ) : page === 'orders' ? (
              <HammerOrders channel={channel} />
            ) : page === 'inv' ? (
              <HammerInventory channel={channel} />
            ) : page === 'insights' ? (
              <HammerInsights channel={channel} />
            ) : page === 'cleansing' ? (
              <HammerCleansing channel={channel} />
            ) : page === 'rules' ? (
              <HammerRules channel={channel} onShowHistory={loadHistory} />
            ) : page === 'recycle' ? (
              <HammerRecycle />
            ) : page === 'history' ? (
              <HammerHistory channel={channel} />
            ) : (
              <div
                style={{
                  color: 'var(--muted)',
                  fontSize: 'var(--font-13)',
                  textAlign: 'center',
                }}
              >
                <div
                  style={{
                    fontSize: 'var(--font-xs)',
                    color: 'var(--muted2)',
                    marginBottom: 4,
                  }}
                >
                  {_chLabel} · {page}
                </div>
                <div style={{ fontSize: 'var(--font-13)', color: 'var(--text)', marginBottom: 4 }}>
                  {hammerData[channel]?.[page]
                    ? `${hammerData[channel]?.[page]?.length ?? 0} 条记录`
                    : '暂无数据'}
                </div>
                <div style={{ fontSize: 'var(--font-xs)', color: 'var(--muted2)', marginTop: 8 }}>
                  功能待添加
                </div>
              </div>
            )}
          </div>
        </>
      )}
    </>
  )
}
