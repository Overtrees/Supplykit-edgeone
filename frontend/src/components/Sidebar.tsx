import React, {useEffect, useRef} from 'react'
import { t } from '../locale'
import { NAV } from '../App'
import { NAV_ICONS } from './Icons'

interface SidebarProps {
  page: string; onClose: () => void; onNavigate: (id: string) => void
  lowStock: number; errCount: number; apiStatus: string
  open: boolean; menuClosing: boolean; onBackdrop: () => void
}

export default function Sidebar({ page, onClose, onNavigate, lowStock, errCount, apiStatus, open, menuClosing, onBackdrop }: SidebarProps) {
  const ref = useRef(null)

  useEffect(() => {
    if (!open) return
    const handler = (e) => {
      if (ref.current && !ref.current.contains(e.target) && !e.target.closest('.menu-btn')) {
        onBackdrop()
      }
    }
    setTimeout(() => document.addEventListener('pointerdown', handler), 0)
    return () => document.removeEventListener('pointerdown', handler)
  }, [open])

  if (!open) return null

  return (
    <>
      {/* 遮罩 */}
      <div
        onPointerDown={onBackdrop}
        style={{
          position: 'fixed', inset: 0, zIndex: 3001,
          background: 'transparent',
          transition: 'background 220ms ease'
        }}
      />
      {/* 菜单面板 */}
      <div
        ref={ref}
        onPointerDown={e => e.stopPropagation()}
        style={{
          position: 'fixed', zIndex: 3002,
          right: 16,
          top: 'calc(env(safe-area-inset-top) + 7px + 46px + 6px)',
          width: 246,
          background: 'var(--glass-bg)',
          backdropFilter: 'blur(var(--glass-blur)) saturate(var(--glass-saturate)) brightness(var(--glass-brightness))',
          WebkitBackdropFilter: 'blur(var(--glass-blur)) saturate(var(--glass-saturate)) brightness(var(--glass-brightness))',
          border: '0.5px solid var(--glass-border)',
          boxShadow: '0 2px 20px rgba(0,0,0,0.12), inset 0 1px 0 rgba(255,255,255,0.25)',
          borderRadius: 26,
          overflow: 'hidden',
          opacity: menuClosing ? 0 : 1,
          transform: menuClosing ? 'translateY(-10px) scale(0.92)' : 'translateY(0) scale(1)',
          transformOrigin: '85% -18px',
          transition: 'opacity 180ms ease, transform 220ms cubic-bezier(0.34,1.56,0.64,1)',
          willChange: 'opacity, transform'
        }}
      >
        <div style={{ padding: 7, maxHeight:'calc(100vh - 140px)', overflowY:'auto' }}>
          {NAV.filter(function(item) { return !(page === 'dash' && item.id === 'dash') }).map(function(item, idx) {
            const IconComp = NAV_ICONS[item.id]
            return <div key={item.id}>{idx > 0 ? <div style={{height:'0.5px',background:'var(--glass-border)',margin:'3px 10px'}} /> : null}
                <button onClick={() => onNavigate(item.id)}
                style={{
                  width:'100%', minHeight:50, border:'none',
                  background:'transparent',
                  borderRadius:17, display:'flex', alignItems:'center', gap:11,
                  padding:'8px 10px', color:'var(--text)', fontSize:'var(--font-md)',
                  fontFamily:'inherit', cursor:'pointer', textAlign:'left',
                  fontWeight:400, marginBottom:2
                }}>
                {IconComp && <span style={{ width:24, height:24, display:'flex', alignItems:'center', justifyContent:'center', flex:'none', color:'var(--primary)' }}>
                  <IconComp size={20} />
                </span>}
                <span style={{ minWidth:0, flex:1 }}>
                  <span style={{ display:'block', fontSize:'var(--font-15)', fontWeight:400, letterSpacing:'-0.1px' }}>
                    {item.label}
                  </span>
                  <span style={{ display:'block', fontSize:'var(--font-xs)', color:'var(--muted2)', marginTop:2 }}>
                    {item.id === 'dash' && t('nav.dash')}
                    {item.id === 'insights' && t('nav.insights')}
                    {item.id === 'orders' && t('nav.orders')}
                    {item.id === 'inv' && t('nav.inv')}
                    {item.id === 'products' && t('nav.products')}
                    {item.id === 'suppliers' && t('nav.suppliers')}
                    {item.id === 'cleansing' && t('nav.cleansing')}
                    {item.id === 'rules' && t('nav.rules')}
                    {item.id === 'quality' && t('nav.quality')}
                    {item.id === 'settings' && t('nav.settings')}
                  </span>
                </span>
                {item.id === 'quality' && errCount > 0 &&
                  <span style={{ background:'var(--danger)', color:'#fff', borderRadius:'var(--radius-full)', fontSize:'var(--font-xs)', fontWeight:700, padding:'1px 7px', minWidth:20, textAlign:'center' }}>{errCount}</span>}
                {item.id === 'inv' && lowStock > 0 &&
                  <span style={{ background:'var(--warning)', color:'#fff', borderRadius:'var(--radius-full)', fontSize:'var(--font-xs)', fontWeight:700, padding:'1px 7px', minWidth:20, textAlign:'center' }}>{lowStock}</span>}
              </button>
            </div>
          })}
        </div>
      </div>
    </>
  )
}