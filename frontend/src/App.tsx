import React, { useState, useEffect, useCallback, useRef } from "react"
import { useAppStore } from './store/useAppStore'
import { clearCache, clearInflight } from './api/client'
import { api } from './api/client'
import { ToastProvider, useToast } from './components/Toast'
import ProductPage from './pages/ProductPage'
import SupplierPage from './pages/SupplierPage'
import InsightsPage from './pages/InsightsPage'
import CleansingPage from './pages/CleansingPage'
import RulesPage from './pages/RulesPage'
import DashboardPage from './pages/DashboardPage'
import ErrorBoundary from './components/ErrorBoundary'
import OrdersPage from './pages/OrdersPage'
import InventoryPage from './pages/InventoryPage'
import QualityPage from './pages/QualityPage'
import SettingsPage from './pages/SettingsPage'
import TaskPage from './pages/TaskPage'
import LoginPage from './pages/LoginPage'
import Sidebar from './components/Sidebar'
import HistorySheet from './components/hammer/HistorySheet'

import HammerProducts from './components/hammer/HammerProducts'
import HammerInsights from './components/hammer/HammerInsights'
import HammerCleansing from './components/hammer/HammerCleansing'
import HammerRules from './components/hammer/HammerRules'
import HammerDashboard from './components/hammer/HammerDashboard'
import HammerInventory from './components/hammer/HammerInventory'
import HammerOrders from './components/hammer/HammerOrders'
import HammerSuppliers from './components/hammer/HammerSuppliers'
import useKeyboard from './hooks/useKeyboard'
import { t } from "./locale"
import { IconStatusOnline, IconStatusWarning, IconStatusOffline, IconExport } from './components/Icons'
import { PRODUCT_COLS, prodColKey, getProdVis, SUPPLIER_COLS, suppColKey, getSuppVis, ORDER_COLS, ORDER_STATUSES, orderColKey, getOrderVis, INS_BBCC_COLS, INS_TRAD_COLS, INS_PURCHASE_COLS, INS_SLOW_COLS, insColKey, getInsVis, insDefVis, insDefVisTrad, INV_COLS, INV_COL_KEY, getInvVis, INV_WH_LABEL } from './components/hammer/configs'

export const NAV = [
  { id:'dash',label:t('nav.dash')},{id:'products',label:t('nav.products')},{id:'suppliers',label:t('nav.suppliers')},
  { id:'orders',label:'订单明细'},{id:'inv',label:t('nav.inv')},{id:'insights',label:t('nav.insights')},
  { id:'cleansing',label:'数据清洗及导入'},{id:'rules',label:t('nav.rules')},
  { id:'quality',label:t('nav.quality')},
  {id:'settings',label:t('nav.settings')},
]


/* 进销存页: 锤子菜单列选择器 + 搜索 + 仓库筛选 + 导出 */
const INV_COLS = {
  own: [
    {id:'warehouse',label:'仓库'},{id:'sku',label:'SKU'},{id:'barcode',label:'69码'},{id:'name',label:'商品'},
    {id:'begin',label:'期初库存'},{id:'transit',label:'在途'},{id:'month_in',label:'当月采购入库'},
    {id:'month_out',label:'当月出库'},{id:'avail',label:'可用'},{id:'turnover',label:'在库周转'},
  ],
  platform: [
    {id:'channel',label:'平台'},{id:'warehouse',label:'仓库'},{id:'sku',label:'SKU'},{id:'barcode',label:'69码'},{id:'name',label:'商品'},
    {id:'transit',label:'在途'},{id:'avail',label:'可用'},
  ],
  platform_b: [
    {id:'channel',label:'平台'},{id:'warehouse',label:'仓库'},{id:'sku',label:'SKU'},{id:'barcode',label:'69码'},{id:'name',label:'商品'},
    {id:'transit',label:'供应商-B仓'},{id:'c_transit',label:'B-C调拨在途'},{id:'avail',label:'可用'},
  ],
}
const INV_COL_KEY = 'c_cols_inventory'
const getInvVis = (wt) => { try { return JSON.parse(localStorage.getItem(INV_COL_KEY + '_' + wt) || 'null') } catch{return null} }
const INV_WH_LABEL = { own:'自有仓', platform:'平台仓', platform_b:'B仓' }

export default function App() {
  const [page, setPage] = useState('dash')
  const navigateTo = (p: string) => { setPage(p); clearCache(); clearInflight(); const _s = useAppStore.getState(); if (_s.prodBatch || _s.prodSelIds?.length) { _s.setProdBatch(false); _s.setProdBatchSel([]) }; if (p === 'dash') { useAppStore.getState().bumpPageVersion() } }
  ;(window as any).__setPage = (p: string) => { navigateTo(p); closeHammerMenu() }
  const [highlightSku, setHighlightSku] = useState('')
  const { inventory, qualityLogs, startPolling, stopAll, wsStatus, channel, setChannel, hammerData, setHammerPanel } = useAppStore()
  const toast = useToast()
  const API = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/+$/, '')
  // 全局后台任务轮询（跨页面、挂后台均有效）
  // 每 3 秒检查 localStorage 任务标记变化，设置页/清洗页提交任务后自动感知启动轮询
  const [taskVersion, setTaskVersion] = useState(0)
  const lastTaskSig = useRef('')
  useEffect(() => {
    const check = setInterval(() => {
      const seed = (() => { try { return localStorage.getItem('c_seed_task') } catch { return null } })()
      const cleansing = (() => { try { return JSON.parse(localStorage.getItem('c_cleansing_task') || 'null') } catch { return null } })()
      const sig = (seed || '') + '|' + (cleansing ? cleansing.task_id : '')
      if (sig !== lastTaskSig.current) {
        lastTaskSig.current = sig
        setTaskVersion(v => v + 1)  // 任务变化时重启轮询
      }
    }, 2000)
    return () => clearInterval(check)
  }, [])
  useEffect(() => {
    const polls = []
    // 种子填充任务
    const seedTask = (() => { try { return localStorage.getItem('c_seed_task') } catch { return null } })()
    if (seedTask) {
      const poll = setInterval(async () => {
        try {
          const r = await fetch(API + '/api/seed/fill/status?task_id=' + seedTask, {headers:{'Authorization':'Bearer '+(()=>{try{return localStorage.getItem('c_token')}catch{return ''}})()}})
          const d = await r.json()
          if (d.data?.status === 'done') {
            clearInterval(poll); try { localStorage.removeItem('c_seed_task') } catch {}
            toast.success('种子数据填充完成，即将刷新')
            setTimeout(() => window.location.reload(), 1500)
          } else if (d.data?.status === 'error' || d.data?.status === 'not_found') {
            // not_found 容错：任务可能刚提交数据库写入有延迟，重试 3 次才清理
            const missCount = (window.__seedMissCount || 0) + 1
            window.__seedMissCount = missCount
            if (d.data?.status === 'not_found' && missCount < 3) { return }
            clearInterval(poll); try { localStorage.removeItem('c_seed_task') } catch {}; window.__seedMissCount = 0
            if (d.data?.status === 'error') toast.error('种子数据填充失败')
          }
        } catch {}
      }, 5000)
      polls.push(poll)
    }
    // 清洗导入任务
    const cleansingTask = (() => { try { return JSON.parse(localStorage.getItem('c_cleansing_task') || 'null') } catch { return null } })()
    if (cleansingTask && cleansingTask.task_id) {
      const poll = setInterval(async () => {
        try {
          const r = await fetch(API + '/api/cleansing/task/' + cleansingTask.task_id, {headers:{'Authorization':'Bearer '+(()=>{try{return localStorage.getItem('c_token')}catch{return ''}})()}})
          const d = await r.json()
          if (d.status === 'done') {
            clearInterval(poll); try { localStorage.removeItem('c_cleansing_task') } catch {}
            toast.success('数据清洗完成，即将刷新')
            setTimeout(() => window.location.reload(), 1500)
          } else if (d.status === 'error') {
            clearInterval(poll); try { localStorage.removeItem('c_cleansing_task') } catch {}
            toast.error('数据清洗失败')
          } else if (d.status === 'not_found') {
            // 容错：刚提交的任务可能还没入库，重试 3 次
            const missCount = (window.__cleanMissCount || 0) + 1
            window.__cleanMissCount = missCount
            if (missCount < 3) return
            clearInterval(poll); try { localStorage.removeItem('c_cleansing_task') } catch {}; window.__cleanMissCount = 0
          }
        } catch {}
      }, 5000)
      polls.push(poll)
    }
    // 重置任务轮询
    const resetTask = (() => { try { return localStorage.getItem('c_reset_task') } catch { return null } })()
    if (resetTask) {
      const poll = setInterval(async () => {
        try {
          const r = await fetch(API + '/api/seed/fill/status?task_id=' + resetTask, {headers:{'Authorization':'Bearer '+(()=>{try{return localStorage.getItem('c_token')}catch{return ''}})()}})
          const d = await r.json()
          if (d.data?.status === 'done' || d.data?.status === 'error') {
            clearInterval(poll); try { localStorage.removeItem('c_reset_task') } catch {}
            if (d.data?.status === 'done') { window.location.reload() }
          }
        } catch {}
      }, 3000)
      polls.push(poll)
    }
    // 导出任务轮询
    const exportTask = (() => { try { return JSON.parse(localStorage.getItem('c_export_task') || 'null') } catch { return null } })()
    if (exportTask && exportTask.task_id) {
      const poll = setInterval(async () => {
        try {
          const r = await fetch(API + '/api/seed/fill/status?task_id=' + exportTask.task_id, {headers:{'Authorization':'Bearer '+(()=>{try{return localStorage.getItem('c_token')}catch{return ''}})()}})
          const d = await r.json()
          if (d.data?.status === 'done') {
            clearInterval(poll); try { localStorage.removeItem('c_export_task') } catch {}
            toast.success('导出完成，可在质量日志查看下载')
          } else if (d.data?.status === 'error') {
            clearInterval(poll); try { localStorage.removeItem('c_export_task') } catch {}
            toast.error('导出失败')
          }
        } catch {}
      }, 5000)
      polls.push(poll)
    }
    // 页面从后台回到前台时立即检查任务状态（不等下一次轮询）
    const onVis = () => { if (document.visibilityState === 'visible') setTaskVersion(v => v + 1) }
    document.addEventListener('visibilitychange', onVis)
    window.addEventListener('focus', onVis)
    return () => { polls.forEach(p => clearInterval(p)); document.removeEventListener('visibilitychange', onVis); window.removeEventListener('focus', onVis) }
  }, [taskVersion])
  const [apiStatus, setApiStatus] = useState('checking')
  const [showHistory, setShowHistory] = useState(false)
  const [history, setHistory] = useState([])
  const [histLoading, setHistLoading] = useState(false)
  const [showWelcome, setShowWelcome] = useState(() => { try { return !localStorage.getItem('c_welcome_seen') } catch { return false } })
  const [loggedIn, setLoggedIn] = useState(() => { try { return !!localStorage.getItem('c_token') } catch { return false } })
  // 启动时验证 token 有效性（失效则清除并显示登录页）
  useEffect(() => {
    if (!loggedIn) return
    const token = (() => { try { return localStorage.getItem('c_token') } catch { return null } })()
    if (!token) { setLoggedIn(false); return }
    fetch(API + '/api/auth/check', { headers: { 'Authorization': 'Bearer ' + token } })
      .then(r => { if (r.status === 401) { try { localStorage.removeItem('c_token') } catch {}; setLoggedIn(false) } })
      .catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  const loadHistory = useCallback(async (ch) => {
    setShowHistory(true)
    setHistLoading(true)
    try {
      const API = import.meta.env.VITE_API_BASE_URL || ''
      const r = await fetch(API + '/api/replenishment-config/history?channel=' + (ch||channel) + '&limit=50', {headers:{'Authorization':'Bearer '+(()=>{try{return localStorage.getItem('c_token')}catch{return ''}})()}})
      const d = await r.json()
      setHistory(d.data || [])
    } catch(e) { setHistory([]) }
    setHistLoading(false)
  }, [channel])
  const checkApi = useCallback(async() => {
    try {
      const ctrl = new AbortController(); setTimeout(() => ctrl.abort(), 5000)
      const r = await fetch(API + '/api/insights/ping', {signal: ctrl.signal})
      const d = await r.json()
      setApiStatus(d.ok ? 'ok' : 'slow')
    } catch { setApiStatus('error') }
  }, [])
  useEffect(() => { checkApi(); const t = setInterval(checkApi, 30000); return () => clearInterval(t) }, [checkApi])

  // 数据版本轮询：后端_ cache_version 变化时自动刷新
  const [dbVersion, setDbVersion] = useState(0)
  const versionRef = useRef(0)
  useEffect(() => {
    const poll = setInterval(async () => {
      try {
        const r = await fetch(API + '/api/health')
        const d = await r.json()
        const v = d.version || 0
        if (versionRef.current !== 0 && versionRef.current !== v) {
          // 版本变化，清除前端缓存
          clearCache()
          useAppStore.getState().loadAll().catch(() => {})
        }
        versionRef.current = v
        setDbVersion(v)
      } catch {}
    }, 30000)
    return () => clearInterval(poll)
  }, [])

  const [showMenu, setShowMenu] = useState(false)
  const [menuClosing, setMenuClosing] = useState(false)
  const menuCloseTimerRef = useRef(null)
  const [showHammerMenu, setShowHammerMenu] = useState(false)
  const [hammerMenuClosing, setHammerMenuClosing] = useState(false)
  const hammerMenuTimerRef = useRef(null)

  const openEditorMenu = useCallback(() => {
    clearTimeout(menuCloseTimerRef.current)
    setMenuClosing(true)
    setShowMenu(true)
    requestAnimationFrame(() => {
      requestAnimationFrame(() => setMenuClosing(false))
    })
  }, [])

  const closeEditorMenu = useCallback(() => {
    clearTimeout(menuCloseTimerRef.current)
    setMenuClosing(true)
    menuCloseTimerRef.current = setTimeout(() => {
      setShowMenu(false)
      setMenuClosing(false)
    }, 220)
  }, [])

  const toggleEditorMenu = useCallback(() => {
    if (showMenu && !menuClosing) closeEditorMenu()
    else openEditorMenu()
  }, [showMenu, menuClosing, closeEditorMenu, openEditorMenu])

  const openHammerMenu = useCallback(() => {
    clearTimeout(hammerMenuTimerRef.current)
    setHammerMenuClosing(true)
    setShowHammerMenu(true)
    requestAnimationFrame(() => {
      requestAnimationFrame(() => setHammerMenuClosing(false))
    })
  }, [])

  const closeHammerMenu = useCallback(() => {
    clearTimeout(hammerMenuTimerRef.current)
    setHammerMenuClosing(true)
    setHammerPanel(null)
    hammerMenuTimerRef.current = setTimeout(() => {
      setShowHammerMenu(false)
      setHammerMenuClosing(false)
    }, 220)
  }, [setHammerPanel])

  const toggleHammerMenu = useCallback(() => {
    if (showHammerMenu && !hammerMenuClosing) closeHammerMenu()
    else openHammerMenu()
  }, [showHammerMenu, hammerMenuClosing, closeHammerMenu, openHammerMenu])

  const hammerMenuRef = useRef(null)

  useEffect(() => {
    if (!showHammerMenu) return
    const handler = (e) => {
      if (hammerMenuRef.current && !hammerMenuRef.current.contains(e.target) && !e.target.closest('.hammer-icon-btn') && !e.target.closest('.history-sheet')) {
        closeHammerMenu()
      }
    }
    setTimeout(() => document.addEventListener('pointerdown', handler), 0)
    return () => document.removeEventListener('pointerdown', handler)
  }, [showHammerMenu, closeHammerMenu])

  useEffect(() => {
    if (!showMenu) return
    const close = () => closeEditorMenu()
    window.addEventListener('scroll', close, { passive: true })
    return () => window.removeEventListener('scroll', close)
  }, [showMenu, closeEditorMenu])

  const navAndClose = useCallback((id, sku) => {
    closeEditorMenu()
    if (sku) setHighlightSku(sku)
    navigateTo(id)
  }, [closeEditorMenu])

  useKeyboard({
    'meta+b': () => toggleEditorMenu(),
    'esc': () => { if (showMenu) closeEditorMenu() },
  })
  useEffect(() => { startPolling(); return () => stopAll() }, [])
  // channel 切换时自动加载数据（不再依赖 page——切页由各页面挂载自拉, 避免每次切页全量 loadAll 含慢接口）
  useEffect(() => { useAppStore.getState().loadAll().catch(() => {}) }, [channel])

  // 同步 html/body 背景色 + browser chrome 色
  useEffect(() => {
    const themeMeta = document.querySelector('meta[name="theme-color"]')
    if (themeMeta) {
      const resolved = getComputedStyle(document.documentElement).getPropertyValue('--bg').trim()
      themeMeta.setAttribute('content', resolved)
    }
  }, [])
  // 监听系统主题变化，更新 theme-color
  useEffect(() => {
    const syncMeta = () => {
      const themeMeta = document.querySelector('meta[name="theme-color"]')
      if (themeMeta) {
        const resolved = getComputedStyle(document.documentElement).getPropertyValue('--bg').trim()
        themeMeta.setAttribute('content', resolved)
      }
    }
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    mq.addEventListener('change', syncMeta)
    return () => mq.removeEventListener('change', syncMeta)
  }, [])

  const navigate = useCallback((newPage, sku, whType) => {
    if (sku) setHighlightSku(sku)
    // 从告警跳进销存时同步切到对应仓库维度(own/platform/platform_b), 保证高亮可见
    if (whType === 'own' || whType === 'platform' || whType === 'platform_b') {
      useAppStore.getState().setHammerWhType(whType)
    }
    navigateTo(newPage)
  }, [])

  const lowStock = (inventory||[]).filter(x => Number(x.available_qty) < Number(x.safety_qty)).length
  const errCount = (qualityLogs||[]).length

  const renderPage = (pageId) => {
    const wrap = (el) => <ErrorBoundary key={pageId}>{el}</ErrorBoundary>
    switch (pageId) {
      case 'dash': return wrap(<DashboardPage key={pageId} onAlert={(s,wt)=>{navigate('inv',s,wt)}} />)
      case 'products': return wrap(<ProductPage key={pageId} />)
      case 'suppliers': return wrap(<SupplierPage key={pageId} />)
      case 'orders': return wrap(<OrdersPage key={pageId} />)
      case 'inv': return wrap(<InventoryPage key={pageId} highlightSku={highlightSku || ''} />)
      case 'insights': return wrap(<InsightsPage key={pageId} />)
      case 'cleansing': return wrap(<CleansingPage key={pageId} />)
      case 'rules': return wrap(<RulesPage key={pageId} />)
      case 'quality': return wrap(<QualityPage key={pageId} />)
      case 'tasks': return wrap(<TaskPage key={pageId} />)
      case 'settings': return wrap(<SettingsPage key={pageId} />)
      default: return null
    }
  }

  return (
    <>
      {!loggedIn ? <LoginPage onLogin={() => { try { localStorage.removeItem('c_welcome_seen') } catch {} setLoggedIn(true); window.location.reload() }} />
      : <ToastProvider>
      {/* 主内容 — 侧边栏打开时显示菜单，关闭时显示页面 */}
      <header style={{display:showWelcome?'none':''}}>
        <div className="header-inner">
          {page === 'dash' ? (
            /* 看板页：左侧渠道筛选+锤子按钮，右侧菜单按钮 */
            <>
              <div className="header-left">
                <span className="header-status">
                  <select value={channel} onChange={e=>setChannel(e.target.value)} style={{background:'transparent',border:'none',outline:'none',color:'inherit',fontSize:'inherit',fontWeight:'inherit',cursor:'pointer',padding:0,margin:0,appearance:'none',WebkitAppearance:'none',MozAppearance:'none'}}>
                    <option value='jd'>京东渠道</option>
                    <option value='other'>其他渠道</option>
                  </select>
                </span>
                <button className="hammer-icon-btn" onClick={toggleHammerMenu}>
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M15 12a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v4a4 4 0 0 0 4 4h5a4 4 0 0 0 4-4v-4Z"/>
                    <path d="M12 12h9"/>
                    <path d="m22 3-3 3"/>
                    <path d="m19 3-3 3"/>
                    <path d="M12 3v3"/>
                    <path d="M12 18v3"/>
                  </svg>
                </button>
              </div>
              <button className="menu-btn" onClick={toggleEditorMenu}>
                <svg width="14" height="14" viewBox="0 0 20 20" fill="none"><rect x="2" y="4" width="16" height="1.5" rx=".75" fill="currentColor"/><rect x="2" y="9.25" width="16" height="1.5" rx=".75" fill="currentColor"/><rect x="2" y="14.5" width="16" height="1.5" rx=".75" fill="currentColor"/></svg>
              </button>
            </>
          ) : (
            /* 其他页：左侧返回按钮，右侧锤子按钮 + 渠道筛选 */
            <>
              <div className="header-left">
                <button className="back-btn" onClick={() => navigateTo('dash')}>
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><polyline points="19 12 5 12"/><polyline points="11 18 5 12 11 6"/></svg>
                </button>
              </div>
              <div style={{display:'flex',alignItems:'center',gap:8}}>
                <button className="hammer-icon-btn" onClick={toggleHammerMenu}>
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M15 12a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v4a4 4 0 0 0 4 4h5a4 4 0 0 0 4-4v-4Z"/>
                    <path d="M12 12h9"/>
                    <path d="m22 3-3 3"/>
                    <path d="m19 3-3 3"/>
                    <path d="M12 3v3"/>
                    <path d="M12 18v3"/>
                  </svg>
                </button>
                <span className="header-status">
                  <select value={channel} onChange={e=>setChannel(e.target.value)} style={{background:'transparent',border:'none',outline:'none',color:'inherit',fontSize:'inherit',fontWeight:'inherit',cursor:'pointer',padding:0,margin:0,appearance:'none',WebkitAppearance:'none',MozAppearance:'none'}}>
                    <option value='jd'>京东渠道</option>
                    <option value='other'>其他渠道</option>
                  </select>
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
              position: 'fixed', inset: 0, zIndex: 3001,
              background: 'transparent',
              transition: 'background 220ms ease'
            }}
          />
          <div
            ref={hammerMenuRef}
            onPointerDown={e => e.stopPropagation()}
            style={{
              position: 'fixed', zIndex: 3002,
              right: 16,
              top: 'calc(env(safe-area-inset-top, 0px) + 7px + 46px + 6px)',
              width: 240,
              background: 'var(--glass-bg)',
              backdropFilter: 'blur(var(--glass-blur)) saturate(var(--glass-saturate)) brightness(var(--glass-brightness))',
              WebkitBackdropFilter: 'blur(var(--glass-blur)) saturate(var(--glass-saturate)) brightness(var(--glass-brightness))',
              border: '0.5px solid var(--glass-border)',
              boxShadow: '0 2px 20px rgba(0,0,0,0.12), inset 0 1px 0 rgba(255,255,255,0.25)',
              borderRadius: 26,
              overflow: 'hidden auto',
              maxHeight: 'calc(100vh - 120px)',
              opacity: hammerMenuClosing ? 0 : 1,
              transform: hammerMenuClosing ? 'translateY(-10px) scale(0.92)' : 'translateY(0) scale(1)',
              transformOrigin: '85% -18px',
              transition: 'opacity 180ms ease, transform 220ms cubic-bezier(0.34,1.56,0.64,1)',
              willChange: 'opacity, transform',
              padding: '16px 16px calc(16px + env(safe-area-inset-bottom, 0px))'
            }}
          >
            {page === 'dash' ? <HammerDashboard channel={channel} /> :
            page === 'products' ? <HammerProducts channel={channel} /> :
             page === 'suppliers' ? <HammerSuppliers channel={channel} /> :
             page === 'orders' ? <HammerOrders channel={channel} /> :
             page === 'inv' ? <HammerInventory channel={channel} /> :
             page === 'insights' ? <HammerInsights channel={channel} /> :
             page === 'cleansing' ? <HammerCleansing channel={channel} /> :
             page === 'rules' ? <HammerRules channel={channel} onShowHistory={loadHistory} /> : (
            <div style={{color:'var(--muted)',fontSize:13,textAlign:'center'}}>
              <div style={{fontSize:11,color:'var(--muted2)',marginBottom:4}}>
                {channel === 'jd' ? '京东' : '其他'} · {page}
              </div>
              <div style={{fontSize:13,color:'var(--text)',marginBottom:4}}>
                {hammerData[channel]?.[page] ? `${(hammerData[channel]?.[page]?.length ?? 0)} 条记录` : '暂无数据'}
              </div>
              <div style={{fontSize:11,color:'var(--muted2)',marginTop:8}}>
                功能待添加
              </div>
            </div>
          )}
          </div>
        </>
      )}
      <Sidebar page={page} onClose={closeEditorMenu} onNavigate={navAndClose} lowStock={lowStock} errCount={errCount} apiStatus={apiStatus} open={showMenu} menuClosing={menuClosing} onBackdrop={closeEditorMenu} />
      {/* 欢迎页 — 首次使用 */}
      {showWelcome && (
        <div style={{display:'flex',flexDirection:'column',minHeight:'100svh',padding:'calc(env(safe-area-inset-top, 0px) + 40px) 24px calc(24px + env(safe-area-inset-bottom, 20px))',overflowY:'auto',boxSizing:'border-box'}}>
          <div style={{flex:1,display:'flex',flexDirection:'column',justifyContent:'center',maxWidth:360,margin:'0 auto',width:'100%'}}>
          <div style={{textAlign:'center',marginBottom:32}}>
            <div style={{fontSize:32,fontWeight:800,color:'var(--text)',marginBottom:8,letterSpacing:'-0.5px'}}>{t("welcome.title")}</div>
            <div style={{fontSize:15,color:'var(--muted2)',lineHeight:1.5}}>电商供应链数据清洗<br/>与补货决策看板</div>
          </div>
          <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:14,marginBottom:32}}>
            {[
              {svg:'M3 12h4l2-9 4 18 2-9h4',title:'看数据',desc:'多维看板总览',page:'dash'},
              {svg:'M9 18h6M10 22h4M15.09 14c.6-.77 1.05-1.6 1.32-2.5A5.4 5.4 0 0 0 12 6a5.4 5.4 0 0 0-4.41 5.5c.27.9.72 1.73 1.32 2.5M9 18c0-1.5.5-2.9 1.5-4h3c1 1.1 1.5 2.5 1.5 4',title:'看补货',desc:'补货/采购建议',page:'insights'},
              {svg:'M20 4 8 16M16 20 4 8M14 6a3 3 0 0 0-6 0v5h6V6ZM6 14c0 2 1.5 4 3 5M14 14c0 2-1.5 4-3 5M4 8h16',title:'导数据',desc:'数据清洗导入',page:'cleansing'},
              {svg:'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42',title:'设规则',desc:'规则引擎配置',page:'rules'},
            ].map(function(card) {
              return <div key={card.page} className="clickable" style={{background:'var(--card)',borderRadius:26,padding:18,textAlign:'center',cursor:'default',border:'0.5px solid var(--border)',boxShadow:'0 1px 4px rgba(0,0,0,0.04)'}}>
                <div style={{marginBottom:8}}><svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="var(--primary)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d={card.svg}/></svg></div>
                <div style={{fontSize:15,fontWeight:600,color:'var(--text)',marginBottom:3}}>{card.title}</div>
                <div style={{fontSize:12,color:'var(--muted2)'}}>{card.desc}</div>
              </div>
            })}
          </div>
          <button onClick={async function(){
            try { localStorage.setItem('c_welcome_seen','1') } catch {}
            setShowWelcome(false)
            try {
              var r = await fetch(API + '/api/seed/fill', {headers:{'Authorization':'Bearer '+(()=>{try{return localStorage.getItem('c_token')}catch{return ''}})()},method:'POST'})
              var d = await r.json()
              if (d.ok) {
                if (d.data?.requires_reset) {
                  useToast().error('已有数据，请在设置页先「一键重置」')
                  return
                }
                var taskId = d.data?.task_id
                if (taskId) {
                  try { localStorage.setItem('c_seed_task', taskId) } catch {}
                  useToast().success('种子数据填充中，可前往任务管理查看进度')
                }
              } else useToast().error('填充失败: ' + (d.error || ''))
            } catch(e) {}
          }} className="btn btn-primary" style={{width:'100%',padding:'14px',fontSize:16,fontWeight:600,marginBottom:10}}>{t("welcome.start")}</button>
          <button onClick={function(){localStorage.setItem('c_welcome_seen','1');setShowWelcome(false)}}
            className="btn btn-ghost clickable" style={{width:'100%',padding:'10px',fontSize:14,color:'var(--muted2)'}}>{t("welcome.skip")}</button>
          </div>
        </div>
      )}
      <main className="container" style={{display:showWelcome?'none':'',animation:'fadeIn 0.2s ease'}} key={page}>
        {renderPage(page)}
      </main>

      {/* 变更历史底部弹窗 */}
      <HistorySheet
        show={showHistory}
        loading={histLoading}
        data={history}
        onClose={() => setShowHistory(false)}
      />
    </ToastProvider>}
    </>
  )
}
