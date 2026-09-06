import React, { useEffect, useState, useRef } from 'react'
import { api } from '../api/client'
import { useToast } from '../components/Toast'
import { useAppStore } from '../store/useAppStore'
import { IconTrendUp, IconTrendDown, IconTrendFlat, IconUndo } from '../components/Icons'
import ErrorRetry from '../components/ErrorRetry'
import { t } from "../locale"

// 备注中 emoji 转 SVG 图标
const EMOJI_MAP = {
  '🔴': <svg width="14" height="14" viewBox="0 0 14 14" fill="none" style={{display:'inline',verticalAlign:'middle',marginRight:2}}><circle cx="7" cy="7" r="6" fill="#ef4444"/></svg>,
  '⚠️': <svg width="14" height="14" viewBox="0 0 14 14" fill="none" style={{display:'inline',verticalAlign:'middle',marginRight:2}}><path d="M7 1.5L1 12.5h12L7 1.5z" fill="#f59e0b"/><rect x="6.3" y="5.5" width="1.4" height="4" rx=".7" fill="#fff"/><circle cx="7" cy="11" r=".7" fill="#fff"/></svg>,
  '⚪': <svg width="14" height="14" viewBox="0 0 14 14" fill="none" style={{display:'inline',verticalAlign:'middle',marginRight:2}}><circle cx="7" cy="7" r="6" fill="#94a3b8"/></svg>,
  '✅': <svg width="14" height="14" viewBox="0 0 14 14" fill="none" style={{display:'inline',verticalAlign:'middle',marginRight:2}}><circle cx="7" cy="7" r="6" fill="#22c55e"/><path d="M4.5 7l2 2 3.5-3.5" stroke="#fff" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round"/></svg>,
  '📈': <svg width="14" height="14" viewBox="0 0 14 14" fill="none" style={{display:'inline',verticalAlign:'middle',marginRight:1}}><path d="M2 10l3.5-4 3 2.5L12 3" stroke="#22c55e" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/><path d="M8.5 3H12v3.5" stroke="#22c55e" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>,
  '📉': <svg width="14" height="14" viewBox="0 0 14 14" fill="none" style={{display:'inline',verticalAlign:'middle',marginRight:1}}><path d="M2 4l3.5 4 3-2.5L12 11" stroke="#ef4444" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/><path d="M8.5 11H12V7.5" stroke="#ef4444" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>,
  '➡️': <svg width="14" height="14" viewBox="0 0 14 14" fill="none" style={{display:'inline',verticalAlign:'middle',marginRight:1}}><path d="M1 7h12M9 3.5L12.5 7 9 10.5" stroke="#64748b" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>,
}
// ── 列配置 ──────────────────────────────────────────────────────────────
import { INS_BBCC_COLS, INS_TRAD_COLS, INS_PURCHASE_COLS, INS_SLOW_COLS } from '../components/hammer/configs'
const BBCC_COLS = INS_BBCC_COLS
const TRAD_COLS = INS_TRAD_COLS
const PURCHASE_COLS = INS_PURCHASE_COLS
const SLOW_COLS = INS_SLOW_COLS
function renderNote(text) {
  if (!text) return '-'
  const parts = []
  let i = 0
  while (i < text.length) {
    // 检测多字符 emoji（如 ⚠️ 是 2 个字符）
    const c = text[i]
    const c2 = c + (text[i+1] || '')
    if (EMOJI_MAP[c2]) {
      parts.push(<React.Fragment key={i}>{EMOJI_MAP[c2]}</React.Fragment>)
      i += 2
    } else if (EMOJI_MAP[c]) {
      parts.push(<React.Fragment key={i}>{EMOJI_MAP[c]}</React.Fragment>)
      i += 1
    } else {
      // 收集连续的非 emoji 文本
      let j = i
      while (j < text.length && !EMOJI_MAP[text[j]] && !EMOJI_MAP[text[j] + (text[j+1] || '')]) j++
      parts.push(<React.Fragment key={i}>{text.slice(i, j)}</React.Fragment>)
      i = j
    }
  }
  return parts.length === 1 ? parts[0] : parts
}

const defVis = (cols) => { try { const s = localStorage.getItem('c_cols_insights_' + (hammerInsightsTab || 'replen') + '_' + (globalChannel || 'jd')); if (s) { const p = JSON.parse(s); if (p.length > 0) return p } } catch {} return cols.map(c => c.id) }
const defVisTrad = (cols) => { try { const s = localStorage.getItem('c_cols_insights_traditional_' + (globalChannel || 'jd')); if (s) { const p = JSON.parse(s); if (p.length > 0) return p } } catch {} return cols.map(c => c.id) }
const API = import.meta.env.VITE_API_BASE_URL || ''
const getVis = (m, ch) => { try { return JSON.parse(localStorage.getItem('c_cols_insights_' + ch + '_' + m) || 'null') } catch{return null} }
const safeGet = (key, def) => { try { return localStorage.getItem(key) ?? def } catch { return def } }
const safeSet = (key, val) => { try { localStorage.setItem(key, val) } catch {} }

const pillStyle = (cond, yes = 'danger', no = 'info') => ({
  display: 'inline-block', padding: '2px 8px', borderRadius: 99,
  fontSize: 12, fontWeight: 600,
  background: cond ? 'rgba(225,29,72,0.08)' : 'rgba(29,78,216,0.08)',
  color: cond ? 'var(--danger)' : 'var(--primary)',
})

function Skeleton({ height = 16, width = '100%', style }) {
  return <div className="skeleton" style={{ height, width, ...style }} />
}

export default function InsightsPage() {
  const toast = useToast()
  const [replen, setReplen] = useState([])
  const [purchase, setPurchase] = useState([])
  const [slowMoving, setSlowMoving] = useState([])
  // 滞销处置建议（SKU×仓库粒度 + 批量处置）
  const [disposals, setDisposals] = useState([])
  const [disposalsLoading, setDisposalsLoading] = useState(true)
  const [slowPage, setSlowPage] = useState(1)
  const [slowTotal, setSlowTotal] = useState(0)
  const [slowLoadingMore, setSlowLoadingMore] = useState(false)
  const slowSentinelRef = useRef(null)
  const slowLoadingRef = useRef(false)
  const [showDisposed, setShowDisposed] = useState(false)

  // 各区块加载状态
  const [replenLoading, setReplenLoading] = useState(true)
  const [purchaseLoading, setPurchaseLoading] = useState(true)
  const [slowLoading, setSlowLoading] = useState(true)
  const [replenLimit, setReplenLimit] = useState(100)
  const [replenTotal, setReplenTotal] = useState(0)
  const [replenPage, setReplenPage] = useState(1)
  const replenPageRef = useRef(1)
  const [replenLoadingMore, setReplenLoadingMore] = useState(false)
  const [purchaseLimit, setPurchaseLimit] = useState(50)
  const [slowLimit, setSlowLimit] = useState(50)

  const { channel: globalChannel, hammerInsightsTab: tab, hammerReplenMode, setHammerReplenMode, hammerCols, hammerData, dataVersion, prodBatch, prodSelIds, setProdBatch, setProdBatchSel, requestProdBatchAll, prodBatchAllReq } = useAppStore()
  useEffect(() => { setProdBatch(false); setProdBatchSel([]) }, [globalChannel, tab])
  // 批量模式全选(断言: 锤子面板"全选" requestProdBatchAll → 全选当前过滤列表)
  useEffect(() => { if (prodBatchAllReq > 0) { const s = useAppStore.getState(); const all = filteredDisp.map(x => x.sku + '|' + x.warehouse); s.setProdBatchSel(s.prodSelIds.length === all.length && all.length > 0 ? [] : all) } }, [prodBatchAllReq])
  const replenMode = (globalChannel !== 'jd' && hammerReplenMode === 'bbcc') ? 'traditional' : hammerReplenMode
  const currentCols = replenMode === 'bbcc' ? BBCC_COLS : TRAD_COLS
  const [visCols, setVisCols] = useState(() => {
    var saved = getVis(replenMode, globalChannel)
    var defaultCols = replenMode==='bbcc'?defVis(BBCC_COLS):defVisTrad(TRAD_COLS)
    if (saved) {
      // 过滤掉已不存在的列ID（如旧版 combined_turn → cur_turn）
      var validIds = currentCols.map(function(c) { return c.id })
      saved = saved.filter(function(id) { return validIds.includes(id) })
      if (saved.length === 0) saved = defaultCols
    } else {
      saved = defaultCols
    }
    return saved
  })
  // 搜索：按 tab 和模式隔离
  const searchKey = tab === 'purchase' ? 'insights_search_purchase' : (tab === 'slow' ? 'insights_search_slow' : 'insights_search_' + replenMode)
  const insightSearch = hammerData?.[globalChannel]?.[searchKey] || ''
  // 搜索时重置分页
  useEffect(function() { setReplenLimit(50); setPurchaseLimit(50); setSlowLimit(50) }, [insightSearch])
  // 搜索变更→重新拉取(带search到后端, 避免只过滤已加载前100条致'搜不到')
  useEffect(function() { if (!replenLoading && !replenLoadingMore) { loadReplen(replenMode, globalChannel, 1) } }, [insightSearch, replenMode])
  const filterBySearch = (items) => {
    if (!insightSearch) return items
    const q = insightSearch.toLowerCase()
    return items.filter(x => (x.sku||'').toLowerCase().includes(q) || (x.product_name||'').toLowerCase().includes(q) || (x.barcode||'').toLowerCase().includes(q))
  }
  const filteredReplen = filterBySearch(Array.isArray(replen) ? replen : [])
  const filteredPurchase = filterBySearch(Array.isArray(purchase) ? purchase : [])
  const filteredSlow = filterBySearch(Array.isArray(slowMoving) ? slowMoving : [])
  // 滞销处置数据（SKU×仓库）融合进表格: 过滤已处理(除非查看已处置)
  const filteredDisp = (Array.isArray(disposals) ? disposals : []).filter(x => showDisposed || !x.disposed)
    .filter(x => { if (!insightSearch) return true; const q = insightSearch.toLowerCase(); return (x.sku||'').toLowerCase().includes(q) || (x.product_name||'').toLowerCase().includes(q) })
  const [purchaseVisCols, setPurchaseVisCols] = useState(() => PURCHASE_COLS.map(c => c.id))
  const [slowVisCols, setSlowVisCols] = useState(() => SLOW_COLS.map(c => c.id))
  const reqSeq = useRef(0)
  const replenSeq = useRef(0)

  // 规则/参数保存或任务完成 → 即时刷新当前 tab(补货/采购/滞销联动)
  useEffect(() => {
    const h = () => {
      const ch = useAppStore.getState().channel || 'jd'
      const t = useAppStore.getState().hammerInsightsTab || 'replen'
      if (t === 'purchase') {
        api.get('/api/insights/purchase?days=28&mode=' + (useAppStore.getState().hammerReplenMode || 'bbcc') + '&channel=' + ch)
          .then(r => setPurchase(r.data?.suggestions || r.data || [])).catch(() => {})
      } else if (t === 'slow') {
        api.get('/api/insights/disposal-suggestions?channel=' + ch + '&page=1&page_size=100')
          .then(r => { const d = r.data || {}; setDisposals(d.items || d || []); setSlowTotal(d.total || (d.items || []).length || 0) }).catch(() => {})
      } else {
        loadReplen(useAppStore.getState().hammerReplenMode || 'bbcc', ch, 1)
      }
    }
    window.addEventListener('rules-changed', h)
    window.addEventListener('insights-refresh', h)
    return () => { window.removeEventListener('rules-changed', h); window.removeEventListener('insights-refresh', h) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    const saved = hammerCols?.['insights_'+replenMode]
    if (saved) {
      var validIds = currentCols.map(function(c) { return c.id })
      var filtered = saved.filter(function(id) { return validIds.includes(id) })
      setVisCols(filtered.length > 0 ? filtered : (replenMode==='bbcc'?defVis(BBCC_COLS):defVisTrad(TRAD_COLS)))
    } else {
      const ls = getVis(replenMode, globalChannel)
      if (ls) setVisCols(ls)
      else setVisCols(replenMode==='bbcc'?defVis(BBCC_COLS):defVisTrad(TRAD_COLS))
    }
  }, [hammerCols, replenMode])
  // {t("insights.purchase")}列同步
  useEffect(() => {
    const saved = hammerCols?.['insights_' + globalChannel + '_purchase']
    if (saved) setPurchaseVisCols(saved)
    else setPurchaseVisCols(PURCHASE_COLS.map(c => c.id))
  }, [hammerCols, globalChannel])
  // {t("insights.slow")}列同步
  useEffect(() => {
    const saved = hammerCols?.['insights_' + globalChannel + '_slow']
    if (saved) setSlowVisCols(saved)
    else setSlowVisCols(SLOW_COLS.map(c => c.id))
  }, [hammerCols, globalChannel])
  const [replenError, setReplenError] = useState('')
  const loadReplen = async (mode, ch, page = 1) => {
    const seq = ++replenSeq.current
    if (page === 1) setReplenLoading(true)
    else setReplenLoadingMore(true)
    setReplenError('')
    try {
      const r = await api.get('/api/insights/replenishment?days=28&mode=' + mode + '&channel=' + ch + '&page=' + page + '&page_size=100&search=' + encodeURIComponent(insightSearch), {timeout: 90000})
      if (seq !== replenSeq.current) return
      let data = r.data
      let total = 0
      let items = []
      // 分页结构 {items,total}
      if (data && typeof data === 'object' && Array.isArray(data.items)) { items = data.items; total = data.total || items.length }
      else if (Array.isArray(data)) { items = data; total = data.length }
      setReplen(prev => page === 1 ? items : [...prev, ...items])
      setReplenTotal(total)
      setReplenPage(page)
      replenPageRef.current = page
      setReplenLoading(false); setReplenLoadingMore(false)
    } catch(e) {
      console.error('loadReplen:', e)
      if (seq === replenSeq.current) {
        if (page === 1) setReplen([])
        setReplenLoading(false); setReplenLoadingMore(false)
        setReplenError((e && (e.message || e.statusText)) ? String(e.message || e.statusText) : String(e))
      }
    }
    if (seq === replenSeq.current) setReplenLoading(false)
  }

  // 从后端加载已下单标记（按渠道隔离）
  const loadOrdered = async () => {
    try {
      const r = await api.get('/api/purchase-orders?channel=' + globalChannel)
      const items = r.data || []
      // 存两份：orderedKeys 用于快速判断，orderedItems 用于展示详情
      setOrderedKeys(items.map(x => x.sku + "|" + x.store))
      setOrderedItems(items)
    } catch(e) {
      try { const fallback = JSON.parse(localStorage.getItem('c_ordered_' + globalChannel) || '[]'); setOrderedKeys(fallback) } catch { setOrderedKeys([]) }
    }
  }

  const [orderedKeys, setOrderedKeys] = useState([])
  const [orderedItems, setOrderedItems] = useState([])

  // ─── BBCC 模式「已下单」功能 ──────────────────────────────────────────
  // 业务含义：京东 B 仓入库批次标记。点击「下单」= 给该 SKU 打上 B 仓入库批次
  // 标记，再填写「到 B 仓日期」，用于监控在库天数（避免超储被京东收取仓储费）。
  // 仅 BBCC 模式展示（replenMode==='bbcc' 控制），按渠道隔离持久化。
  const toggleOrdered = async (sku, store, product_name, suggested_qty) => {
    const key = sku + '|' + store
    const isOrdered = orderedKeys.includes(key)
    // 乐观更新：立即更新本地状态，不等 API 返回
    if (isOrdered) {
      setOrderedKeys(prev => prev.filter(k => k !== key))
      setOrderedItems(prev => prev.filter(x => x.sku !== sku || x.store !== store))
      api.delete('/api/purchase-orders?sku=' + encodeURIComponent(sku) + '&store=' + encodeURIComponent(store) + '&channel=' + globalChannel)
        .then(() => toast.success('已取消下单'))
        .catch(() => { toast.error('取消失败'); loadOrdered() })
    } else {
      const newItem = {sku, store, product_name: product_name || '', suggested_qty: suggested_qty || 0, arrival_date: ''}
      setOrderedKeys(prev => [...prev, key])
      setOrderedItems(prev => [...prev, newItem])
      api.post('/api/purchase-orders?sku=' + encodeURIComponent(sku) + '&store=' + encodeURIComponent(store) + '&product_name=' + encodeURIComponent(product_name || '') + '&suggested_qty=' + (suggested_qty || 0) + '&channel=' + globalChannel)
        .then(() => toast.success('已下单'))
        .catch(() => { toast.error('下单失败'); loadOrdered() })
    }
  }

  // 设置到 B 仓日期（入库批次生效日，用于计算在库天数监控超储）
  const setArrivalDate = async (item, date) => {
    // 乐观更新
    setOrderedItems(prev => prev.map(x => x.id === item.id ? {...x, arrival_date: date} : x))
    api.put('/api/purchase-orders/' + item.id, {arrival_date: date}).catch(() => loadOrdered())
  }

  const todayStr = new Date().toISOString().slice(0, 10)

  useEffect(() => {
    setDispSel([])
    const seq = ++reqSeq.current
    const mode = globalChannel === 'jd' ? replenMode : 'traditional'
    if (globalChannel !== 'jd' && replenMode === 'bbcc') setHammerReplenMode('traditional')
    if (tab === 'purchase') {
      // 仅拉采购建议
      setPurchaseLoading(true)
      api.get('/api/insights/purchase?days=28&mode=' + replenMode + '&channel=' + globalChannel + '&search=' + encodeURIComponent(insightSearch)).then(r => {
        if (seq !== reqSeq.current) { setPurchaseLoading(false); return }
        setPurchase(r.data?.suggestions || r.data || [])
        setPurchaseLoading(false)
      }).catch(() => setPurchaseLoading(false))
    } else if (tab === 'slow') {
      // 仅拉滞销处置建议（分页，第1页）
      setDisposalsLoading(true)
      setSlowPage(1)
      api.get('/api/insights/disposal-suggestions?channel=' + globalChannel + '&page=1&page_size=100&search=' + encodeURIComponent(insightSearch)).then(r => {
        if (seq !== reqSeq.current) { setDisposalsLoading(false); return }
        const d = r.data || {}
        const items = d.items || d || []
        setDisposals(items)
        setSlowTotal(d.total || items.length || 0)
        setDisposalsLoading(false)
      }).catch(() => setDisposalsLoading(false))
    } else {
      // 补货 tab（默认）：补货建议 + 采购订单
      loadOrdered()
      setReplenLoading(true)
      loadReplen(mode, globalChannel)
    }
  }, [globalChannel, replenMode, dataVersion, tab, insightSearch])

  const loadSlowMore = () => {
    // ref 门闩(同步最新)——避免 IntersectionObserver 闭包旧 state 导致并发/循环
    if (slowLoadingRef.current || (slowTotal > 0 && disposals.length >= slowTotal)) return
    slowLoadingRef.current = true
    setSlowLoadingMore(true)
    api.get('/api/insights/disposal-suggestions?channel=' + globalChannel + '&page=' + (slowPage + 1) + '&page_size=100&search=' + encodeURIComponent(insightSearch)).then(r => {
      const d = r.data || {}
      const items = d.items || []
      setDisposals(prev => [...prev, ...items])
      setSlowPage(prev => prev + 1)
      setSlowTotal(d.total || slowTotal)
      slowLoadingRef.current = false
      setSlowLoadingMore(false)
    }).catch(() => { slowLoadingRef.current = false; setSlowLoadingMore(false) })
    // 不 reobserve——IntersectionObserver 触发后表增长哨兵下移(状态变化),
    // 用户滚动再进入视口触发(同补货模式, 无循环无卡)
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>

      {/* {t("nav.insights")} */}
      {tab === 'replen' && (
        <div className="card">
          <div className="section-title" style={{display:'flex',flexWrap:'wrap',gap:6,alignItems:'center'}}>
            <span>补货建议</span>
            <span className="muted2" style={{fontSize:11,fontWeight:400}}>已加载 {Math.min(replen.length, replenTotal || replen.length)}/{replenTotal || replen.length} 条 · 显示 {visCols.length}/{currentCols.length} 列{insightSearch ? ` · "${insightSearch}"` : ''}</span>
            {replenMode==='bbcc' && orderedKeys.length > 0 && <span className="pill success" style={{fontSize:10}}>已下单 {orderedKeys.length} 项</span>}
          </div>
          {replenLoading ? (
            <div>
              <Skeleton height={14} width="30%" style={{ marginBottom: 8 }} />
              {[1,2,3,4,5].map(i => <Skeleton key={i} height={36} style={{ marginBottom: 4 }} />)}
            </div>
          ) : !Array.isArray(replen) || replen.length === 0 ? (
            <div style={{ padding: 12, textAlign: 'center' }}>
              {replenError ? (
                <ErrorRetry error={'加载失败：' + replenError} onRetry={() => loadReplen(replenMode, globalChannel)} />
              ) : (
                <div className="muted">{t("insights.no_replenish")}</div>
              )}
            </div>
          ) : (
            <div style={{overflow:'auto',maxHeight:'calc(100vh - 180px)'}}>
              <table>
                <colgroup>{visCols.map(id => {const col = currentCols.find(c => c.id === id); return col ? <col key={col.id} /> : null})}</colgroup>
                <thead style={{position:'sticky',top:0,background:'var(--card)',zIndex:1}}><tr>{visCols.map(id => {const col = currentCols.find(c => c.id === id); return col ? <th style={{whiteSpace:'nowrap',fontSize:11,padding:'8px 4px'}} key={col.id}>{col.label}</th> : null})}</tr></thead>
                <tbody>
                  {Array.isArray(filteredReplen) && filteredReplen.map((x, i) => {
                    const isOrdered = orderedKeys.includes(x.sku+'|'+x.store)
                    const rowStyle = isOrdered ? {opacity:0.55,background:'var(--bg)'} : {}
                    return (
                    <tr key={i} style={rowStyle}>
                      {visCols.map(id => {
                        const col = currentCols.find(c => c.id === id)
                        if (!col) return <td key={id}></td>
                        // 序号列
                        if (col.id === 'seq') return <td key={col.id} className="text-11 muted2">{i+1}</td>
                        // SKU
                        if (col.id === 'brand') return <td key={col.id} style={{fontSize:12}}>{x.brand||'-'}</td>
                        if (col.id === 'sku') return <td key={col.id} className="mono" style={{fontSize:12,textDecoration:isOrdered?'line-through':'none'}}>{x.sku}</td>
                        if (col.id === 'barcode') return <td key={col.id} className='mono' style={{fontSize:11}}>{x.barcode||'-'}</td>
                        // 商品名
                        if (col.id === 'name') return <td key={col.id} style={{textDecoration:isOrdered?'line-through':'none'}}>{x.product_name}</td>
                        // 仓库(BBCC) / 店铺(TRAD)
                        if (col.id === 'warehouse' || col.id === 'store') return <td key={col.id} className="col-store">{replenMode==='bbcc' ? 'B仓' : (x.warehouse || x.store || '-')}</td>
                        // 现有(TRAD)
                        if (col.id === 'avail') return <td key={col.id} style={{fontWeight:600}}>{x.available_qty}</td>
                        // 供应商-B仓(在途, 与进销存B仓维度同源)
                        if (col.id === 'b_transit') return <td key={col.id} style={{color:'var(--muted)',fontSize:11}}>{x.b_transit ?? '-'}</td>
                        // B仓可用库存
                        if (col.id === 'b_stock') return <td key={col.id} style={{color:'var(--primary)',fontWeight:600}}>{x.b_stock ?? '-'}</td>
                        // B仓周转
                        if (col.id === 'b_turn') return <td key={col.id} style={{fontSize:11,fontWeight:600,color:x.b_stock > 0 && (x.daily_sales > 0 ? (x.b_stock/x.daily_sales) : Infinity) > 15 ? '#ef4444' : x.b_stock > 0 && x.daily_sales > 0 && (x.b_stock/x.daily_sales) > 10 ? 'var(--warning)' : 'var(--text)'}}>{x.b_stock > 0 ? (x.daily_sales > 0 ? (x.b_stock/x.daily_sales).toFixed(1)+'天' : '∞') : '-'}</td>
                        // C仓总和可用
                        if (col.id === 'c_stock') return <td key={col.id} style={{fontWeight:600}}>{x.c_stock ?? x.available_qty}</td>
                        // 在途
                        if (col.id === 'transit') return <td key={col.id}>{replenMode === 'bbcc' ? (x.c_transit ?? '-') : (x.in_transit_qty ?? '-')}</td>
                        // 日销
                        if (col.id === 'sales') return <td key={col.id} style={{fontSize:11,fontWeight:600,whiteSpace:'nowrap'}}>{x.daily_sales}<span style={{fontSize:10,fontWeight:400,color:'var(--muted2)'}}>
                          /{(x.daily_sales_7||0) > (x.daily_sales_14||0)*1.15 ? <IconTrendUp size={12} style={{display:'inline',verticalAlign:'middle',color:'#22c55e'}} /> : (x.daily_sales_7||0) < (x.daily_sales_14||0)*0.85 ? <IconTrendDown size={12} style={{display:'inline',verticalAlign:'middle',color:'#ef4444'}} /> : <IconTrendFlat size={12} style={{display:'inline',verticalAlign:'middle',color:'#64748b'}} />}{(x.daily_sales_7||0).toFixed(1)}
                          /{(x.daily_sales_14||0) > (x.daily_sales_28||0)*1.15 ? <IconTrendUp size={12} style={{display:'inline',verticalAlign:'middle',color:'#22c55e'}} /> : (x.daily_sales_14||0) < (x.daily_sales_28||0)*0.85 ? <IconTrendDown size={12} style={{display:'inline',verticalAlign:'middle',color:'#ef4444'}} /> : <IconTrendFlat size={12} style={{display:'inline',verticalAlign:'middle',color:'#64748b'}} />}{(x.daily_sales_14||0).toFixed(1)}
                          /{(x.daily_sales_28||0) > (x.daily_sales_60||0)*1.15 ? <IconTrendUp size={12} style={{display:'inline',verticalAlign:'middle',color:'#22c55e'}} /> : (x.daily_sales_28||0) < (x.daily_sales_60||0)*0.85 ? <IconTrendDown size={12} style={{display:'inline',verticalAlign:'middle',color:'#ef4444'}} /> : <IconTrendFlat size={12} style={{display:'inline',verticalAlign:'middle',color:'#64748b'}} />}{(x.daily_sales_28||0).toFixed(1)}</span></td>
                        // C仓周转
                        if (col.id === 'c_turn') return <td key={col.id} className="text-11 font-600">{x.c_turnover != null ? x.c_turnover+'天' : '∞'}</td>
                        // 在途周转
                        if (col.id === 'transit_turn') return <td key={col.id} style={{fontSize:11}}>{x.transit_turnover != null ? x.transit_turnover+'天' : '∞'}</td>
                        // 安全线(TRAD)
                        if (col.id === 'safety') return <td key={col.id}>{x.safety_qty}</td>
                        // 在库周转(TRAD)
                        if (col.id === 'turn') return <td key={col.id} style={{color: x.days_to_empty < 5 ? '#ef4444' : x.days_to_empty < 10 ? 'var(--warning)' : 'var(--text)'}}>{x.days_to_empty > 999 ? '∞' : x.days_to_empty}</td>
                        // C仓建议补
                        if (col.id === 'suggest') return <td key={col.id} style={{color:'var(--primary)',fontWeight:600}}>{x.suggested_qty > 0 ? x.suggested_qty : '-'}</td>
                        // B仓需补
                        if (col.id === 'b_suggest') return <td key={col.id} style={{color:'var(--success)',fontWeight:700}}>{x.b_suggested > 0 ? x.b_suggested : '-'}</td>
                        // 当前综转
                        if (col.id === 'cur_turn') return <td key={col.id} style={{fontSize:11}}>{x.combined_turnover_current != null ? x.combined_turnover_current+'天' : '∞'}</td>
                        // 补后综转(BBCC) / 补后周转(TRAD)
                        if (col.id === 'after_turn') return <td key={col.id} style={{fontSize:11,fontWeight:700,color:replenMode==='bbcc'?(x.combined_turnover!=null&&x.combined_turnover>90?'#ef4444':x.combined_turnover!=null&&x.combined_turnover>15?'var(--warning)':'var(--text)'):(x.after_turnover!=null&&x.after_turnover>90?'#ef4444':x.after_turnover!=null&&x.after_turnover>15?'var(--warning)':'var(--text)')}}>{replenMode==='bbcc'?(x.suggested_qty>0||x.b_suggested>0)&&x.combined_turnover!=null?x.combined_turnover+'天':'-':x.suggested_qty>0&&x.after_turnover!=null?x.after_turnover+'天':'-'}</td>
                        // 备注
                        if (col.id === 'note') return <td key={col.id} className="col-name" style={{color:'var(--muted2)',fontSize:12}}>{renderNote(x.note)}</td>
                        // 标记操作
                        if (col.id === 'action') return <td key={col.id}>{isOrdered
                          ? <span onClick={()=>toggleOrdered(x.sku, x.store, x.product_name, x.suggested_qty || x.b_suggested)} style={{cursor:'pointer',fontSize:16,color:'var(--success)',display:'inline-flex',alignItems:'center',gap:2}}><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" style={{display:'inline',verticalAlign:'middle'}}><polyline points="4 12 10 18 20 6"/></svg><span className="text-9 muted2">{t("undo.undo")}</span></span>
                          : <span onClick={()=>{
                            if ((x.suggested_qty > 0 || x.b_suggested > 0) && x.combined_turnover > 90 && !window.confirm(`补后综合周转${x.combined_turnover}天，已超90天考核红线，仍标记操作？`)) return
                            toggleOrdered(x.sku, x.store, x.product_name, x.suggested_qty || x.b_suggested)
                          }} style={{cursor:'pointer',fontSize:18,opacity:0.5}}>☐</span>}</td>
                        return <td key={col.id} className="small muted" style={{fontSize:11}}>-</td>
                      })}
                    </tr>
                  )})}
                </tbody>
              </table>
              {replenTotal > 0 && replen.length < replenTotal && (
                <div className="text-center mt-8" ref={function(el) {
                  if (el && !el._observer) {
                    el._observer = new IntersectionObserver(function(entries) {
                      // 闭包旧replenPage bug: observer只创建一次, 回调捕获创建时旧值→反复加载同页
                      // 修复: 用 ref 实时读最新 page, 避免重复加载同一页(数据重复致'滚到3100条还是那批在途0')
                      if (entries[0].isIntersecting && !replenLoadingMore) {
                        var next = replenPageRef.current + 1
                        replenPageRef.current = next
                        loadReplen(replenMode, globalChannel, next)
                      }
                    }, {rootMargin: '200px'})
                    el._observer.observe(el)
                  }
                }}>
                  <span className="btn btn-ghost" style={{fontSize:12,padding:'6px 16px',cursor:'pointer'}}>{replenLoadingMore ? '加载中...' : ''}</span>
                </div>
              )}
            </div>
          )}
          {/* 已下单明细（仅BBCC模式）：B 仓入库批次 + 在库天数监控，超储预警用 */}
          {replenMode==='bbcc' && orderedItems.length > 0 && <details style={{marginTop:12}} open>
            <summary className="small muted" style={{cursor:'pointer',fontSize:12,fontWeight:600}}>📦 已下单 {orderedItems.length} 项 · 点击查看入库日期与仓储天数</summary>
            <div style={{fontSize:12,marginTop:8}}>
              {orderedItems.map((po, i) => {
                const daysSinceArrival = po.arrival_date ? Math.floor((new Date() - new Date(po.arrival_date)) / (1000*60*60*24)) : null
                const stayColor = daysSinceArrival != null ? (daysSinceArrival > 90 ? '#ef4444' : daysSinceArrival > 15 ? '#f59e0b' : 'var(--text)') : 'var(--muted)'
                return <div key={i} style={{display:'flex',justifyContent:'space-between',alignItems:'center',padding:'6px 10px',border:'1px solid var(--border)',borderRadius:32,marginBottom:4,flexWrap:'wrap',gap:4}}>
                  <span style={{flex:1,minWidth:120}}>{po.sku} {po.product_name} <span className="pill success" style={{fontSize:10}}>+{(po.actual_qty||po.suggested_qty)}</span></span>
                  <span style={{display:'flex',alignItems:'center',gap:6,flexWrap:'wrap'}}>
                    <span className="small" style={{color:stayColor,fontWeight:600}}>
                      {daysSinceArrival != null ? daysSinceArrival + '天' : '待入仓'}
                    </span>
                    <input type="date" value={po.arrival_date || ''}
                      onChange={e => setArrivalDate(po, e.target.value)}
                      style={{fontSize:11,padding:'2px 6px',border:'1px solid var(--border)',borderRadius:4,width:130}} />
                    <span onClick={()=>toggleOrdered(po.sku, po.store)} style={{cursor:'pointer',color:'var(--danger)',opacity:0.6,display:'inline-flex'}}><IconUndo size={14} /></span>
                  </span>
                </div>
              })}
            </div>
          </details>}
        </div>
      )}

      {/* 采购建议 */}
      {tab === 'purchase' && (
        <div className="card">
          <div className="section-title" style={{display:'flex',flexWrap:'wrap',gap:6,alignItems:'center'}}>
            <span>采购建议</span>
            {globalChannel==='jd' && <span className="pill" style={{fontSize:10,padding:'2px 8px',minHeight:'auto',lineHeight:'18px'}}>{replenMode==='bbcc'?'BBCC 口径(含B仓)':'传统口径(不含B仓)'}</span>}
            <span className="muted2" style={{fontSize:11,fontWeight:400}}>显示 {purchaseVisCols.length}/{PURCHASE_COLS.length} 列 · 已加载 {Math.min(purchaseLimit, filteredPurchase.length)}/{filteredPurchase.length} 条{insightSearch ? ` · "${insightSearch}"` : ''}</span>
          </div>
          {purchaseLoading ? (
            <div>
              {[1,2,3,4].map(i => <Skeleton key={i} height={36} style={{ marginBottom: 4 }} />)}
            </div>
          ) : (purchase.length === 0 ? (
            <div className="muted" style={{ padding: 12, textAlign: 'center' }}>{t("insights.no_purchase")}</div>
          ) : (
            <div style={{overflow:'auto',maxHeight:"calc(100vh - 180px)"}}>
              <table>
                <colgroup>{purchaseVisCols.map(id => {const col = PURCHASE_COLS.find(c => c.id === id); return col ? <col key={col.id} /> : null})}</colgroup>
                <thead style={{position:'sticky',top:0,background:'var(--card)',zIndex:1}}><tr>{purchaseVisCols.map(id => {const col = PURCHASE_COLS.find(c => c.id === id); return col ? <th style={{whiteSpace:'nowrap',fontSize:11,padding:'8px 4px'}} key={col.id}>{col.label}</th> : null})}</tr></thead>
                <tbody>
                  {filteredPurchase.slice(0, purchaseLimit).map((x, i) => {
                    const timing = !x.purchase_qty || x.purchase_qty <= 0 ? '充足' : (x.after_turnover && (x.target_turnover || 15) > 0 && x.after_turnover <= (x.target_turnover || 15) ? '建议' : '充足')
                    return (
                    <tr key={i}>
                      {purchaseVisCols.map(id => {
                        const col = PURCHASE_COLS.find(c => c.id === id)
                        if (!col) return <td key={id}></td>
                        if (col.id === 'barcode') return <td key={col.id} className="mono text-11 muted2">{x.barcode || '-'}</td>
                        if (col.id === 'brand') return <td key={col.id} style={{fontSize:12}}>{x.brand||'-'}</td>
                        if (col.id === 'sku') return <td key={col.id} className="mono" style={{fontSize:12}}>{x.sku}</td>
                        if (col.id === 'name') return <td key={col.id} className="col-name">{x.product_name}</td>
                        if (col.id === 'warehouse') return <td key={col.id} className="col-store">{x.warehouse || x.store || '-'}</td>
                        // 系统可用 / 系统在途(与进销存对应仓维度同源: inventory in_transit_qty)
                        if (col.id === 'sys_available') return <td key={col.id} style={{fontSize:12,fontWeight:600,whiteSpace:'nowrap'}}>{x.sys_available ?? '-'}<span style={{fontSize:10,fontWeight:400,color:'var(--muted2)'}}> (自有{x.own_available || 0}{replenMode === 'bbcc' ? `+B${x.b_available || 0}` : ''}+C{x.plat_available || 0})</span></td>
                        if (col.id === 'sys_transit') return <td key={col.id} style={{fontSize:12,color:'var(--muted)',fontWeight:400,whiteSpace:'nowrap'}}>{x.sys_transit ?? '-'}<span style={{fontSize:10,color:'var(--muted2)'}}> (自有在途{x.own_transit || 0}{replenMode === 'bbcc' ? `+B在途${x.b_transit || 0}` : ''}+C在途{x.plat_transit || 0})</span></td>
                        if (col.id === 'daily_sales') return <td key={col.id} style={{fontSize:12,fontWeight:600,whiteSpace:'nowrap'}}>{x.daily_sales}<span style={{fontSize:10,fontWeight:400,color:'var(--muted2)'}}>
                          {tab === 'purchase'
                            ? <>/{((x.daily_sales_14||0) > (x.daily_sales_28||0)*1.15) ? <IconTrendUp size={12} style={{display:'inline',verticalAlign:'middle',color:'#22c55e'}} /> : ((x.daily_sales_14||0) < (x.daily_sales_28||0)*0.85) ? <IconTrendDown size={12} style={{display:'inline',verticalAlign:'middle',color:'#ef4444'}} /> : <IconTrendFlat size={12} style={{display:'inline',verticalAlign:'middle',color:'#64748b'}} />}{(x.daily_sales_14||0).toFixed(1)}/{(x.daily_sales_28||0).toFixed(1)}</>
                            : <>/{((x.daily_sales_7||0) > (x.daily_sales_14||0)*1.15) ? <IconTrendUp size={12} style={{display:'inline',verticalAlign:'middle',color:'#22c55e'}} /> : ((x.daily_sales_7||0) < (x.daily_sales_14||0)*0.85) ? <IconTrendDown size={12} style={{display:'inline',verticalAlign:'middle',color:'#ef4444'}} /> : <IconTrendFlat size={12} style={{display:'inline',verticalAlign:'middle',color:'#64748b'}} />}{(x.daily_sales_7||0).toFixed(1)}/{((x.daily_sales_14||0) > (x.daily_sales_28||0)*1.15) ? <IconTrendUp size={12} style={{display:'inline',verticalAlign:'middle',color:'#22c55e'}} /> : ((x.daily_sales_14||0) < (x.daily_sales_28||0)*0.85) ? <IconTrendDown size={12} style={{display:'inline',verticalAlign:'middle',color:'#ef4444'}} /> : <IconTrendFlat size={12} style={{display:'inline',verticalAlign:'middle',color:'#64748b'}} />}{(x.daily_sales_14||0).toFixed(1)}/{((x.daily_sales_28||0) > (x.daily_sales_60||0)*1.15) ? <IconTrendUp size={12} style={{display:'inline',verticalAlign:'middle',color:'#22c55e'}} /> : ((x.daily_sales_28||0) < (x.daily_sales_60||0)*0.85) ? <IconTrendDown size={12} style={{display:'inline',verticalAlign:'middle',color:'#ef4444'}} /> : <IconTrendFlat size={12} style={{display:'inline',verticalAlign:'middle',color:'#64748b'}} />}{(x.daily_sales_28||0).toFixed(1)}</>}</span></td>
                        if (col.id === 'actual_purchase') return <td key={col.id} style={{fontWeight:700,color:x.actual_purchase > 0 ? 'var(--success)' : 'var(--muted2)'}}>{x.actual_purchase > 0 ? '+'+x.actual_purchase : (x.actual_purchase === 0 ? '0' : '-')}</td>
                        if (col.id === 'after_turnover') return <td key={col.id} style={{fontWeight:600,color: x.actual_purchase > 0 ? (x.target_turnover > 0 && x.after_turnover > x.target_turnover ? '#ef4444' : 'var(--text)') : 'var(--muted2)'}}>{x.actual_purchase > 0 ? x.after_turnover+'天' : '-'}</td>
                        if (col.id === 'note') return <td key={col.id} className="col-name" style={{color:'var(--muted2)',fontSize:12}}>{renderNote(x.note) || t("insights.no_purchase_needed")}</td>
                        if (col.id === 'timing') return <td key={col.id}><span className={`pill ${timing==='建议'?'warning':'info'}`}>{timing}</span></td>
                        return <td key={col.id}></td>
                      })}
                    </tr>
                    )
                  })}
                </tbody>
                <tfoot>
                  <tr style={{fontWeight:700,borderTop:'2px solid var(--border)'}}>
                    {purchaseVisCols.includes('actual_purchase') && <>
                      <td colSpan={purchaseVisCols.indexOf('actual_purchase')} style={{textAlign:'right',fontSize:12}}>合计</td>
                      <td style={{color:'var(--success)',fontSize:13}}>+{filteredPurchase.reduce((s,x)=>s+(x.actual_purchase||0),0)}</td>
                      {purchaseVisCols.includes('after_turnover') && purchaseVisCols.indexOf('after_turnover') > purchaseVisCols.indexOf('actual_purchase') && <td colSpan={purchaseVisCols.length - purchaseVisCols.indexOf('after_turnover') - 1} className="text-11 muted2">
                        {(() => {
                          const withPurchase = filteredPurchase.filter(x => x.purchase_qty > 0)
                          const avgTurnover = withPurchase.length > 0
                            ? (withPurchase.reduce((s,x)=>s+(x.after_turnover||0),0) / withPurchase.length).toFixed(1)
                            : ''
                          return '平均周转 ' + (avgTurnover || '—') + ' 天'
                        })()}
                      </td>}
                    </>}
                  </tr>
                </tfoot>
              </table>
              {filteredPurchase.length > purchaseLimit && (
                <div className="text-center mt-8" ref={function(el) {
                  if (el && !el._observer) {
                    el._observer = new IntersectionObserver(function(entries) {
                      if (entries[0].isIntersecting) setPurchaseLimit(function(prev) { return prev + 50 })
                    }, {rootMargin: '200px'})
                    el._observer.observe(el)
                  }
                }}>
                  <span className="btn btn-ghost" style={{fontSize:12,padding:'6px 16px',cursor:'pointer'}}>加载中...</span>
                </div>
              )}
            </div>
        ))}
      </div>
    )}

      {/* 滞销预警 */}
      {tab === 'slow' && (
        <div className="card">
          <div className="section-title" style={{display:'flex',flexWrap:'wrap',gap:6,alignItems:'center'}}>
            <span>滞销预警</span>
            <span className="muted2" style={{fontSize:11,fontWeight:400}}>已加载 {Math.min(filteredDisp.length, slowTotal || filteredDisp.length)}/{slowTotal || filteredDisp.length} 条 · 显示 {slowVisCols.length}/{SLOW_COLS.length} 列{insightSearch ? ` · "${insightSearch}"` : ''}</span>
          </div>

          <div style={{display:'flex',alignItems:'center',gap:6,flexWrap:'wrap',marginBottom:8}}>
            <span className="muted2" style={{fontSize:11}}>按 SKU×仓库 · 品类滞销线 + 临期 + B仓仓储费</span>
            <span onClick={()=>setShowDisposed(!showDisposed)} className="clickable" style={{marginLeft:'auto',fontSize:12,padding:'4px 12px',borderRadius:99,border:'1px solid var(--border)',background:'var(--card)',cursor:'pointer'}}>{showDisposed?'隐藏已处理':'查看已处理'}</span>
          </div>
          {disposalsLoading ? (
            <div>
              {[1,2,3].map(i => <Skeleton key={i} height={36} style={{ marginBottom: 4 }} />)}
            </div>
          ) : (filteredDisp.length === 0 ? (
            <div className="muted" style={{ padding: 12, textAlign: 'center' }}>{showDisposed ? '暂无处置记录' : '暂无滞销 🎉'}</div>
          ) : (
            <>
              <div style={{overflow:'auto',maxHeight:"calc(100vh - 180px)"}} onScroll={function(e){
                  var el = e.target
                  // IntersectionObserver 仅在交叉状态变化时回调——持续在视口不重复触发导致卡加载
                  // 改用滚动监听: 每次滚动检测接近底部即加载下一页
                  if (el.scrollTop + el.clientHeight >= el.scrollHeight - 250 && !slowLoadingMore && (slowTotal === 0 || filteredDisp.length < slowTotal)) {
                    loadSlowMore()
                  }
                }}>
                <table>
                  <colgroup>{slowVisCols.map(id => {const col = SLOW_COLS.find(c => c.id === id); return col ? <col key={col.id} /> : null})}</colgroup>
                  <thead style={{position:'sticky',top:0,background:'var(--card)',zIndex:1}}><tr>{prodBatch && <th style={{width:30}}></th>}{slowVisCols.map(id => {const col = SLOW_COLS.find(c => c.id === id); return col ? <th style={{whiteSpace:'nowrap',fontSize:11,padding:'8px 4px'}} key={col.id}>{col.label}</th> : null})}</tr></thead>
                  <tbody>
                    {filteredDisp.map((x, i) => {
                      const key = x.sku + '|' + x.warehouse
                      const isSel = (prodSelIds || []).includes(key)
                      const s = useAppStore.getState()
                      return <tr key={key} onClick={()=>{ if (prodBatch) { s.setProdBatchSel(isSel ? s.prodSelIds.filter(k=>k!==key) : [...s.prodSelIds, key]) } }} style={{opacity:x.disposed?0.5:1,background:prodBatch&&isSel?'rgba(29,78,216,0.08)':'transparent',cursor:prodBatch?'pointer':'default'}}>
                        {prodBatch && <td onClick={(e)=>{e.stopPropagation(); s.setProdBatchSel(isSel ? s.prodSelIds.filter(k=>k!==key) : [...s.prodSelIds, key])}} style={{padding:'4px 8px',textAlign:'center'}}><span style={{width:18,height:18,borderRadius:6,border:'1.5px solid',borderColor:isSel?'var(--primary)':'var(--border)',background:isSel?'var(--primary)':'transparent',display:'inline-flex',alignItems:'center',justifyContent:'center',color:'#fff',fontSize:11}}>{isSel?'✓':''}</span></td>}
                        {slowVisCols.map(id => {
                          const col = SLOW_COLS.find(c => c.id === id)
                          if (!col) return <td key={id}></td>
                          if (col.id === 'processed') return <td key={id}>{x.disposed ? <span style={{fontSize:11,color:'var(--muted2)'}}>✓ 已处理</span> : <span style={{fontSize:11,color:'var(--muted2)'}}>-</span>}</td>
                          if (col.id === 'brand') return <td key={id} style={{fontSize:12}}>{x.brand||'-'}</td>
                          if (col.id === 'sku') return <td key={id} className="mono" style={{fontSize:12}}>{x.sku}</td>
                          if (col.id === 'name') return <td key={id} style={{fontSize:13}}>{x.product_name}</td>
                          if (col.id === 'warehouse') return <td key={id} style={{fontSize:12}}>{x.warehouse}</td>
                          if (col.id === 'days') return <td key={id} style={{fontWeight:600,color:x.days_zero>=90?'#ef4444':(x.days_zero>=30?'var(--warning)':'var(--muted)'),fontSize:12}}>{x.days_zero==999?'∞':x.days_zero}天</td>
                          if (col.id === 'stock') return <td key={id} style={{fontSize:12}}>{x.stock}</td>
                          if (col.id === 'level') return <td key={id}><span className={`pill ${x.level==='black'?'danger':x.level==='red'?'danger':x.level==='yellow'?'warning':'info'}`} style={{fontSize:10,padding:'2px 8px',minHeight:'auto',lineHeight:'18px'}}>{x.level==='black'?'紧急':x.level==='red'?'处置':x.level==='yellow'?'滞销':'观察'}</span></td>
                          if (col.id === 'note') return <td key={id} style={{fontSize:11,color:'var(--muted2)'}}>{(x.reason||[]).join(' · ')}<span style={{color:'var(--text)',fontWeight:600}}> → {x.suggestion}</span></td>
                          return <td key={id}></td>
                        })}
                      </tr>
                    })}
                  </tbody>
                </table>
              </div>
              {/* IntersectionObserver 哨兵(自动加载, 无固定占位): 触发时 disconnect 防重复,
                  加载完成 finally 重新 observe(状态变化循环)。onScroll 冗余已去 */}
              <div style={{height:1}} ref={function(el){
                if (el && !el._obs) {
                  el._obs = new IntersectionObserver(function(entries){
                    // ref 门闩(最新, 非闭包旧state)——触发后表增长哨兵移出视口,
                    // 用户滚动再进入触发(补货同模式), 无需 reobserve
                    if (entries[0].isIntersecting && !slowLoadingRef.current && (slowTotal === 0 || filteredDisp.length < slowTotal)) {
                      el._obs.disconnect()
                      loadSlowMore()
                    }
                  }, {rootMargin: '200px'})
                  el._obs.observe(el)
                  slowSentinelRef.current = el
                }
              }} />
              {slowLoadingMore && <div style={{textAlign:'center',padding:'6px 0',fontSize:11,color:'var(--muted2)'}}>加载中...</div>}
            </>
          ))}
        </div>
      )}

    </div>
  )
}
