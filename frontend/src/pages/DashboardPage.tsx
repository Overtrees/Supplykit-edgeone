import React, { useState, useMemo, useEffect, useRef } from "react"
import { api } from '../api/client'
import { useAppStore } from '../store/useAppStore'
import ErrorRetry from '../components/ErrorRetry'
import Chart from '../components/Chart'
import { t } from "../locale"

const periodLabel = { today:'今日', week:'本周', month:'本月' }

// 仓库集合标签截断: 后端 warehouse 可能为 "北京仓,上海仓,成都仓" 集合(逗号连接) → 前2仓+等N仓, title 给完整
const fmtWh = (w) => {
  if (!w) return ''
  const p = String(w).split(',').filter(Boolean)
  if (p.length <= 2) return p.join(',')
  return p.slice(0, 2).join(',') + '等' + p.length + '仓'
}

interface DashboardPageProps { onAlert?: (sku: string) => void }

export default function DashboardPage({ onAlert }: DashboardPageProps) {
  const { dashboard, inventory, qualityLogs, alerts, stockRisk, alertCounts, bcOutOfStock, channel, loading, hammerDashPeriod: periodTab, hammerReplenMode, pageVersion } = useAppStore()
  const [healthTab, setHealthTab] = useState(() => { try { const h = localStorage.getItem('health_tab') || (channel === 'jd' ? 'own' : 'platform'); return (channel !== 'jd' && h === 'platform_b') ? 'platform' : h } catch { return channel === 'jd' ? 'own' : 'platform' } })
  // 渠道切换归一化: platform_b(B 仓)为 jd BBCC 专属维度, other 渠道强制 platform(避免残留空维度显示)
  useEffect(() => { if (channel !== 'jd' && healthTab === 'platform_b') setHealthWithSave('platform') }, [channel])
  const setHealthWithSave = (tab) => { try { localStorage.setItem('health_tab', tab) } catch {} setHealthTab(tab) }
  // GMV 视角切换: total=总GMV(含退款流水) / net=净GMV(剔除退款)——GMV小卡+店铺GMV卡共用
  const [gmvView, setGmvView] = useState('total')
  const [bcMenuOpen, setBcMenuOpen] = useState(false)
  const [showAllLowStock, setShowAllLowStock] = useState(false)
  const [showAllReplenish, setShowAllReplenish] = useState(false)
  const [showAllRisk, setShowAllRisk] = useState(false)
  const [_riskTab, setRiskTab] = useState('c')   // 传统模式子视图: c=C仓 / own=自有三方仓
  const [_storeDim, setStoreDim] = useState('store')  // 店铺GMV卡维度: store=店铺(盘子) / brand=品牌(渗透)
  const [showAllOut, setShowAllOut] = useState(false)
  const [fullOut, setFullOut] = useState(null)        // 缺货弹窗完整数据(按当前视图维度)
  const [oosList, setOosList] = useState(null)        // 当前维度缺货全量(随 healthTab 拉取, 预览+计数+弹窗同源)
  const [fullAlerts, setFullAlerts] = useState(null)   // 告警弹窗完整数据(点击时拉取)
  const [fullRisk, setFullRisk] = useState(null)       // 濒临断货完整列表
  const [chLoading, setChLoading] = useState(false)
  const [dashErr, setDashErr] = useState('')
  // 弹窗数据加载(四维完整性): 告警用大 limit 分组配额拿全量; 濒临断货用 full=1; 缺货按维度拉全量
  const loadFullAlerts = async () => { try { const r = await api.get('/api/alerts?channel=' + channel + '&limit=5000', {timeout: 60000}); setFullAlerts(r.data || []) } catch(e) { setFullAlerts([]) } }
  const loadFullRisk = async () => {
    try {
      const r = await api.get('/api/dashboard/stock-risk?channel=' + channel + '&full=1', {timeout: 60000})
      const _m = (channel !== 'jd' && (hammerReplenMode || '') !== 'traditional') ? 'traditional' : (hammerReplenMode || (channel === 'jd' ? 'bbcc' : 'traditional'))
      const _d = r.data || {}
      // bbcc→BC合计(b仓+全国c); traditional→C仓+自有/三方仓(不涉B)——取数必须与模式维度一致
      const _lst = Array.isArray(_d) ? _d : (_m === 'bbcc' ? (_d.bcItems || []) : ((_d.cItems || []).concat(_d.ownItems || [])))
      setFullRisk(_lst)
    } catch(e) { setFullRisk([]) }
  }
  const loadFullOut = async () => { const _wh = healthTab === 'own' ? 'own' : healthTab === 'bc' ? 'bc' : healthTab === 'platform' ? 'platform' : 'platform_b'; try { const r = await api.get('/api/inventory/out-of-stock?channel=' + channel + '&wh=' + _wh, {timeout: 60000}); const d = Array.isArray(r.data) ? r.data : ((r.data && r.data.items) || []); setFullOut(d) } catch(e) { setFullOut([]) } }
  // 健康卡缺货列表: 随视图维度(own/平台/bc)拉取全量——与 healthData.out_of_stock 计数口径一致
  // (曾用 stockOverview.items(全渠道LIMIT100)过滤, 维度缺货SKU在窗口外时预览/计数漏显)
  useEffect(() => {
    const _wh = healthTab === 'own' ? 'own' : healthTab === 'bc' ? 'bc' : healthTab === 'platform' ? 'platform' : 'platform_b'
    api.get('/api/inventory/out-of-stock?channel=' + channel + '&wh=' + _wh, {timeout: 60000}).then(r => {
      const d = Array.isArray(r.data) ? r.data : ((r.data && r.data.items) || [])
      setOosList(d)
    }).catch(() => setOosList([]))
  }, [healthTab, channel])
  const reqSeq = useRef(0)
  useEffect(() => {
    const seq = ++reqSeq.current
    // 无感刷新: 仅当无 dashboard 数据(首次/清空后)才骨架, 有旧数据则不骨架(先显示旧值, 后台拉新替换)
    setChLoading(!useAppStore.getState().dashboard)
    const load = () => Promise.allSettled([
      api.get('/api/dashboard/summary?t=' + Date.now(), {timeout: 60000}),  // PA慢时段summary重建可能9-30s, 90s不超时
      api.get('/api/dashboard/aux?channel=' + channel + '&t=' + Date.now(), {timeout: 60000}),
    ]).then(([s, ax]) => {
      if (seq !== reqSeq.current) { setChLoading(false); return }  // 竞态丢弃
      // 兜底: summary 必须 fulfilled 且 data.summary 存在才算成功(seed填充/表重建期间
      // 可能返回异常结构 → dash=null 且无ErrorRetry → 看板空白缺口)
      const dashOk = s.status === 'fulfilled' && s.value.data && s.value.data.summary
      const dash = dashOk ? s.value.data : null
      setDashErr((s.status === 'rejected' || !dashOk) ? '加载失败，可能是网络异常或数据正在处理中' : '')
      const aux = (ax && ax.status === 'fulfilled') ? (ax.value.data || {}) : {}
      const alerts = aux.alerts || []
      const stockRisk = aux.stockRisk || []
      const ov = aux.stockOverview || {}
      useAppStore.setState({ dashboard: dash, alerts, stockRisk, alertCounts: aux.alertCounts || null, bcOutOfStock: aux.bcOutOfStock || [], inventory: ov.items || [], _stockOverview: ov, loading: false, dataLoaded: true })
      setChLoading(false)
      // 首次加载关键数据为空时自动重试（进程重启后缓存未就绪/慢接口超时兜底），最多 3 次
      const _srEmpty = Array.isArray(stockRisk) ? stockRisk.length === 0 : !(stockRisk && stockRisk.items && stockRisk.items.length)
      if ((!dash || _srEmpty) && seq === reqSeq.current) {
        let retries = 0
        const timer = setInterval(() => {
          retries += 1
          if (retries > 3 || seq !== reqSeq.current) { clearInterval(timer); return }
          Promise.allSettled([
            api.get('/api/dashboard/summary'),
            api.get('/api/dashboard/stock-risk'),
          ]).then(([s2, r2]) => {
            if (seq !== reqSeq.current) { clearInterval(timer); return }
            const d2 = s2.status === 'fulfilled' ? s2.value.data : null
            const rv2 = r2.status === 'fulfilled' ? (r2.value.data || []) : []
            useAppStore.setState({
              dashboard: d2 || useAppStore.getState().dashboard,
              stockRisk: (Array.isArray(rv2) ? rv2.length : (rv2 && rv2.items && rv2.items.length)) ? rv2 : useAppStore.getState().stockRisk,
            })
            if (d2 && rv2.length) clearInterval(timer)
          })
        }, 3000)
      }
    }).catch(() => setChLoading(false))
    load()
  }, [channel, pageVersion])
  // 30s 静默自动刷新（不显示 loading 骨架屏，避免闪烁）
  // 规则保存/删除/批量启用停用完成 → 'rules-changed' 事件 → 立即重拉看板。
  // 覆盖场景: 操作进行中先回到看板(拿到旧值), 操作完成后事件触发即时刷新——无需手动 F5。
  // 复用 pageVersion 路径(与 navigateTo bumpPageVersion 同一个 useEffect)，改动最小。
  useEffect(() => {
    const h = () => { useAppStore.getState().bumpPageVersion() }
    window.addEventListener('rules-changed', h)
    return () => window.removeEventListener('rules-changed', h)
  }, [])
  // 兜底: 从后台回到前台/focus 时重拉一次——覆盖 iOS 后台 JS 挂起导致事件延迟执行、
  // 以及非事件源的数据变更(其他会话/API 级修改)等一切时序; 保证回来看板必是最新
  useEffect(() => {
    const onVis = () => { if (document.visibilityState === 'visible') useAppStore.getState().bumpPageVersion() }
    document.addEventListener('visibilitychange', onVis)
    window.addEventListener('focus', onVis)
    return () => { document.removeEventListener('visibilitychange', onVis); window.removeEventListener('focus', onVis) }
  }, [])
  const silentBusy = useRef(false)
  useEffect(() => {
    const timer = setInterval(async () => {
      if (silentBusy.current) return
      silentBusy.current = true
      try {
        const _t = 't=' + Date.now()
        const [s, ax] = await Promise.all([
          api.get('/api/dashboard/summary?' + _t, {timeout: 60000}),
          api.get('/api/dashboard/aux?channel=' + channel + '&' + _t, {timeout: 60000}),
        ])
        const aux = ax.data || {}
        useAppStore.setState({ dashboard: s.data, alerts: aux.alerts || [], stockRisk: aux.stockRisk || [], alertCounts: aux.alertCounts || null, bcOutOfStock: aux.bcOutOfStock || [], inventory: (aux.stockOverview || {}).items || [], loading: false, dataLoaded: true })
      } catch {} finally { silentBusy.current = false }
    }, 30000)
    return () => clearInterval(timer)
  }, [channel])
  const periodTrend = dashboard?.periods?.[periodTab + '_trend'] || dashboard?.trend || []
  const periodMeta = dashboard?.periods?.[periodTab] || {}

  const periodTrendOption = useMemo(() => ({
    tooltip: { trigger: 'axis', valueFormatter: (v) => '¥' + Number(v).toLocaleString('zh-CN', {minimumFractionDigits:2,maximumFractionDigits:2}), extraCssText: 'z-index:1000', hideDelay: 100 },
    xAxis: { type: 'category', data: periodTrend.map(i => i['日期']) || [], axisLabel: { fontSize: 9 } },
    yAxis: [
      { type: 'value', axisLabel: { fontSize: 9, formatter: (v) => v >= 10000 ? (v/10000).toFixed(0) + 'W' : v }, max: (v) => Math.ceil(v.max * 1.2 / 1000) * 1000 },
      { type: 'value', axisLabel: { fontSize: 9 } }
    ],
    grid: { containLabel: true, top: 8, bottom: 42 },
    series: [
      { type: 'line', smooth: true, areaStyle: { opacity: 0.15 }, data: periodTrend.map(i => i['GMV']) || [], color: 'var(--primary)', name: 'GMV' },
      { type: 'bar', data: periodTrend.map(i => i['订单数']) || [], color: '#0f766e', yAxisIndex: 1, name: '订单数' }
    ],
    legend: { data: ['GMV', '订单数'], bottom: 6, left: 'center', icon: 'circle', itemWidth: 8, itemHeight: 8, textStyle: { fontSize: 9 } }
  }), [periodTrend])

  const storeOption = useMemo(() => {
    // 店铺看盘子 / 品牌看渗透(跨店按brand归集, join products.brand)
    var storeData = _storeDim === 'brand'
      ? (dashboard?.period_brands?.[periodTab] || dashboard?.brands || [])
      : (dashboard?.period_stores?.[periodTab] || dashboard?.stores || [])
    // GMV 视角切换: 净GMV=总GMV-退款(后端 stores/brands 已带 net_gmv)
    var _g = (i) => gmvView === 'net' ? (i.net_gmv != null ? i.net_gmv : i.gmv)
        : gmvView === 'payout' ? (i.payout != null ? i.payout : i.gmv) : i.gmv
    return {
    tooltip: { trigger: 'axis', valueFormatter: (v) => '¥' + Number(v).toLocaleString('zh-CN', {minimumFractionDigits:2,maximumFractionDigits:2}), extraCssText: 'z-index:1000', hideDelay: 100 },
    // 底部店铺/品牌名: 全量显示不截断; 长名截断+换行; 数量多(品牌35+)时旋转避免重叠
    xAxis: { type: 'category', data: storeData.map(i => i.name) || [],
      // 品牌维度标签太多(35+)重叠→隐藏, 用悬浮/点击 tooltip 显示名称; 店铺维度保留(数量少, 截断+旋转)
      axisLabel: _storeDim === 'brand'
        ? { show: false }
        : { fontSize: 8, interval: 0, rotate: storeData.length > 8 ? 35 : 0, margin: 6,
            formatter: (v) => { const t = String(v||''); if (t.length > 6) return t.slice(0,6)+'…'; return t },
            width: 64, overflow: 'truncate' } },
    yAxis: { type: 'value',
      axisLabel: { fontSize: 8, formatter: (v) => Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 0 }) }, splitNumber: 4,
      max: (v) => Math.ceil(v.max * 1.15 / 1000) * 1000 },
    series: [{ type: 'bar', barMaxWidth: 26, data: storeData.map((i, idx) => ({ value: Math.round(_g(i) * 100) / 100, itemStyle: { color: ['#f59e0b','#06b6d4','#8b5cf6','#ec4899','#10b981','#f97316'][idx % 6] } })) || [] }],
    grid: { containLabel: true, top: 8, bottom: _storeDim === 'brand' ? 8 : (storeData.length > 8 ? 42 : 30), left: 8, right: 12 }
  }}, [dashboard, periodTab, gmvView, _storeDim])

  const barOption = useMemo(() => {
    const f = dashboard?.period_funnel?.[periodTab] || dashboard?.funnel || []
    const names = f.map(x => x.name)
    const values = f.map(x => x.value)
    return {
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, extraCssText: 'z-index:1000', hideDelay: 100, formatter: (p) => {
        const idx = p[0]?.dataIndex ?? 0; const item = f[idx]
        return `${item.name}<br/>数量: ${item.value}单<br/>占比: ${item.percentage}%<br/>转化率: ${item.conversion}%`
      }},
      grid: { containLabel: true, top: 4, bottom: 6 },
      xAxis: { type: 'value', show: false },
      yAxis: { type: 'category', data: names, axisLabel: { fontSize: 10 } },
      series: [{
        type: 'bar', data: values.map((v, i) => ({ value: v, itemStyle: { color: ['#f59e0b','#06b6d4','#8b5cf6','#ec4899','#10b981'][i % 5] } })),
        barWidth: '60%',
        label: { show: true, position: 'right', fontSize: 10, formatter: (p) => `${p.value}单`, textBorderColor: 'transparent' }
      }]
    }
  }, [dashboard, periodTab])

  const lowStock = (inventory||[]).filter(x => Number(x.available_qty) < Number(x.safety_qty)).length
  const errCount = (qualityLogs||[]).length
  const alertsList = Array.isArray(alerts) ? alerts.filter(x => x.status === 'active') : []
  const lowStockAlerts = alertsList.filter(x => x.alert_type === 'low_stock')
  const replenishAlerts = alertsList.filter(x => x.alert_type === 'replenish')
  // 看板「(N 严重)」等计数一律取后端 alertCounts(独立 COUNT)，不得从截断列表 filter 得出——
  // 列表每组各取 200 条，总数可能远大于此，filter 计数会系统性漏报
  const _acType = (alertCounts || {}).by_type || {}
  const _acSev = (alertCounts || {}).by_severity || {}
  const criticalAlerts = _acSev.error != null ? _acSev.error : alertsList.filter(x => x.severity === 'error').length
  const nonReplenishTotal = (alertCounts && alertCounts.non_replenish != null) ? alertCounts.non_replenish : lowStockAlerts.length
  // 拆分类: 低库存(纯low_stock)与滞销(slow_moving)独立计数(曾合并为non_replenish导致"低库存N"含滞销误导)
  const _acByType2 = (alertCounts && alertCounts.by_type) || {}
  const lowStockTotal = _acByType2.low_stock != null ? _acByType2.low_stock : lowStockAlerts.filter(x => x.alert_type === 'low_stock').length
  const slowMovingTotal = _acByType2.slow_moving != null ? _acByType2.slow_moving : lowStockAlerts.filter(x => x.alert_type === 'slow_moving').length
  const replenishTotal = _acType.replenish != null ? _acType.replenish : replenishAlerts.length
  const periodDays = periodTab === 'custom' ? (periodMeta?.days || 30) : ({today:1,week:7,month:30}[periodTab]||30)
  // 濒临断货: 兼容旧数组/新{items,total,critical,warning}结构——卡上大数字/紧急警告用全量计数(完整性)
  // 补货模式联动: bbcc→BC 合计维度(对齐库存卡 bc tab: B+C 按 SKU 合计); traditional→C 仓维度
  const _replMode = (channel !== 'jd' && (hammerReplenMode || '') !== 'traditional') ? 'traditional' : (hammerReplenMode || (channel === 'jd' ? 'bbcc' : 'traditional'))
  const _sr0 = Array.isArray(stockRisk) ? {items: stockRisk, total: stockRisk.length} : (stockRisk || {items: [], total: 0, critical: 0, warning: 0, bcItems: [], bcTotal: 0, bcCritical: 0, bcWarning: 0, cItems: [], cTotal: 0, cCritical: 0, cWarning: 0, ownItems: [], ownTotal: 0, ownCritical: 0, ownWarning: 0})
  if (_replMode === 'bbcc') {
    _sr0.items = _sr0.bcItems || []
    _sr0.total = _sr0.bcTotal != null ? _sr0.bcTotal : (_sr0.items.length)
    _sr0.critical = _sr0.bcCritical != null ? _sr0.bcCritical : (_sr0.items.filter(x => x.days_to_empty < 3).length)
    _sr0.warning = _sr0.bcWarning != null ? _sr0.bcWarning : (_sr0.items.filter(x => x.days_to_empty >= 3 && x.days_to_empty < 7).length)
  } else {
    // 传统多仓: C 仓维度(主) + 自有/三方仓维度(own, 由采购管控)
    _sr0.items = _sr0.cItems || []
    _sr0.total = _sr0.cTotal != null ? _sr0.cTotal : (_sr0.items.length)
    _sr0.critical = _sr0.cCritical != null ? _sr0.cCritical : (_sr0.items.filter(x => x.days_to_empty < 3).length)
    _sr0.warning = _sr0.cWarning != null ? _sr0.cWarning : (_sr0.items.filter(x => x.days_to_empty >= 3 && x.days_to_empty < 7).length)
    _sr0.ownItems = _sr0.ownItems || []
    _sr0.ownTotal = _sr0.ownTotal != null ? _sr0.ownTotal : (_sr0.ownItems.length)
    _sr0.ownCritical = _sr0.ownCritical != null ? _sr0.ownCritical : (_sr0.ownItems.filter(x => x.days_to_empty < 3).length)
    _sr0.ownWarning = _sr0.ownWarning != null ? _sr0.ownWarning : (_sr0.ownItems.filter(x => x.days_to_empty >= 3 && x.days_to_empty < 7).length)
  }
  const _sr = _sr0
  // 传统多仓: C 仓 + 自有/三方仓 混合显示(同一列表, 标签区分)——曾用 C仓/自有三方 tab 分开
  const _srOwn = { items: _sr0.ownItems || [], total: _sr0.ownTotal || 0, critical: _sr0.ownCritical || 0, warning: _sr0.ownWarning || 0 }
  const _showOwn = false
  const _r = _replMode === 'traditional'
    ? (function(){ var _merged = [].concat(_sr.items||[], _srOwn.items||[]).sort(function(a,b){return (a.days_to_empty||999)-(b.days_to_empty||999)}); var _tot = ( _sr.total||0 ) + ( _srOwn.total||0 ); var _c = ( _sr.critical||0 ) + ( _srOwn.critical||0 ); var _w = ( _sr.warning||0 ) + ( _srOwn.warning||0 ); var _full = [].concat(_sr.items||[], _srOwn.items||[]).sort(function(a,b){return (a.days_to_empty||999)-(b.days_to_empty||999)}); return {items: _merged.slice(0,10), total: _tot, critical: _c, warning: _w, _full: _full} })()
    : _sr
  const riskCritical = _r.critical != null ? _r.critical : (_r.items||[]).filter(x => x.days_to_empty < 3).length
  const riskWarning = _r.warning != null ? _r.warning : (_r.items||[]).filter(x => x.days_to_empty >= 3 && x.days_to_empty < 7).length
    // 缺货列表 = stockOverview.items(本身就是 avail<=0 的缺货SKU, 含warehouse_type)
  // 缺货列表 = 当前视图维度全量(oosList, 随 healthTab 拉取); 未加载时回退旧逻辑
  var _oosSrc = oosList || (healthTab === 'bc' ? (bcOutOfStock || []) : (healthTab === 'own' ? (inventory||[]).filter(x => x.warehouse_type === 'own') : (inventory||[]).filter(x => x.warehouse_type === 'platform')))
  var outOfStockItems = _oosSrc.slice(0,3)
  // 待处理卡 告警×仓库维度: 用后端精确 by_warehouse(全量), 按补货模式聚合展示——
  // BBCC 看全盘 B+C(与健康卡bc一致), 传统多仓不涉及B仓。曾用截断列表filter(200样本 vs 全量)
  function _whView(w) {
    w = w || {}
    if (_replMode === 'bbcc') {
      return { main: (w.b || 0) + (w.c || 0), own: (w.own || 0) }   // BC合计 + 自有
    }
    return { main: (w.c || 0), own: (w.own || 0) }                   // C仓 + 自有/三方
  }
  const _acLsWh = (alertCounts && alertCounts.ls_warehouse) || null   // 低库存/其他 组分布
  const _acRpWh = (alertCounts && alertCounts.rp_warehouse) || null   // 补货 组分布
  // 弹窗内 SKU 仓库标签(跟随补货模式): bbcc→BC合并+自有; 传统→C+自有(不涉B)
  const _whTag = (wt) => {
    if (wt === 'own') return '自有'
    if (wt === 'platform_b' || wt === 'platform') return _replMode === 'bbcc' ? 'BC' : (wt === 'platform' ? 'C' : 'B')
    return ''
  }
  const lsWhView = _whView(_acLsWh)
  const rpWhView = _whView(_acRpWh)

  if (chLoading) return <div className="card" style={{padding:16}}>{[1,2,3,4,5,6,7].map(i=><div key={i} className="skeleton" style={{height:80,marginBottom:8,borderRadius:24}}/>)}</div>
  if (dashErr && !dashboard) return <ErrorRetry error={dashErr} onRetry={() => { window.__setPage && window.__setPage('dash') }} />
  return <>
    <div className="card-grid" style={{marginBottom:16}}>
      {/* 1. GMV 卡 — 加环比微趋势线 + 日均 */}
      <div className="card" style={{borderRadius:26,boxShadow:'0 1px 6px rgba(0,0,0,0.04)',containerType:'inline-size',aspectRatio:'1',display:'flex',flexDirection:'column',padding:16,overflow:'hidden'}}>
        <div style={{display:'flex',alignItems:'center',justifyContent:'space-between'}}>
          <div className="small muted" style={{fontSize:12,lineHeight:1.2}}>{periodTab === 'custom' ? '自定义' : periodLabel[periodTab]} GMV</div>
          {/* GMV 视角切换(总/净/回款), 样式对齐健康小卡 tab: 紧凑segmented pill + 短标签 */}
          <div style={{display:'flex',gap:2,background:'var(--bg)',borderRadius:99,padding:2}}>
            {[['total','总'],['net','净'],['payout','回款']].map(([v,l]) => (
              <span key={v} onClick={function(){setGmvView(v)}} className="clickable"
                style={{fontSize:9,padding:'2px 6px',borderRadius:99,cursor:'pointer',fontWeight:gmvView===v?600:400,background:gmvView===v?'var(--card)':'transparent',color:gmvView===v?'var(--text)':'var(--muted2)',whiteSpace:'nowrap'}}>{l}</span>
            ))}
          </div>
        </div>
        <div style={{flex:1,display:'flex',flexDirection:'column',justifyContent:'flex-end',marginBottom:4}}>
          <div className="card-value" style={{fontSize:'clamp(18px,9cqi,30px)',fontWeight:700,lineHeight:1.1,whiteSpace:'nowrap',overflow:'hidden',textOverflow:'ellipsis'}}>
            {(() => { const _g = gmvView === 'net' ? (periodMeta.net_gmv != null ? periodMeta.net_gmv : ((periodMeta.gmv||0) - (dashboard?.summary?.refund_amount||0))) : (gmvView === 'payout' ? (periodMeta.payout != null ? periodMeta.payout : ((periodMeta.gmv||0) - (dashboard?.summary?.refund_amount||0) - (dashboard?.summary?.subsidy_amount||0))) : periodMeta.gmv); return '¥' + Number(_g||0).toLocaleString() })()}
          </div>
          <div className="card-sub" style={{marginTop:6,display:'flex',alignItems:'center',gap:6}}>
            <span>{periodMeta.orders} 单</span>
            {(() => {
              const last = Number(periodMeta.gmv||0), prev = Number(periodMeta.prev_gmv||0)
              if (!prev) return null
              const pct = ((last - prev) / prev * 100)
              const _cmpLabel = periodTab === 'today' ? '较昨日' : (periodTab === 'week' ? '较上周' : '较上月')
              return <span style={{fontSize:11,fontWeight:600,color:pct >= 0 ? 'var(--success)' : '#ef4444'}}>
                {pct >= 0 ? '↑' : '↓'} {Math.abs(pct).toFixed(1)}% <span style={{fontSize:9,fontWeight:400,color:'var(--muted2)'}}>{_cmpLabel}</span>
              </span>
            })()}
            <span style={{color:'var(--muted2)',fontSize:10}}>· 日均 ¥{(() => { const _g = gmvView === 'net' ? (periodMeta.net_gmv != null ? periodMeta.net_gmv : ((periodMeta.gmv||0) - (dashboard?.summary?.refund_amount||0))) : (gmvView === 'payout' ? (periodMeta.payout != null ? periodMeta.payout : ((periodMeta.gmv||0) - (dashboard?.summary?.refund_amount||0) - (dashboard?.summary?.subsidy_amount||0))) : periodMeta.gmv); return Math.round((_g||0)/periodDays).toLocaleString() })()}</span>
          </div>
        </div>
        {/* 微趋势线 */}
        {periodTrend.length >= 3 && <div style={{height:22,marginTop:8,display:'flex',alignItems:'flex-end',gap:1.5}}>
          {periodTrend.map((i,idx) => {
            const v = Number(i['GMV'])||0
            const max = Math.max(...periodTrend.map(x => Number(x['GMV'])||0), 1)
            const h = Math.max(v / max * 18, 2)
            return <div key={idx} style={{flex:1,height:h,borderRadius:'2px 2px 0 0',background:'var(--primary)',opacity:0.3+0.7*(v/max)}} />
          })}
        </div>}
      </div>

      {/* 2. {t("dash.pending")}卡 — 按仓库维度拆分 */}
      <div className="card" style={{borderRadius:26,boxShadow:'0 1px 6px rgba(0,0,0,0.04)',containerType:'inline-size',aspectRatio:'1',display:'flex',flexDirection:'column',padding:16}}>
        <div className="small muted" style={{fontSize:12,lineHeight:1.2}}>待处理</div>
        <div style={{flex:1,display:'flex',flexDirection:'column',justifyContent:'flex-end',marginBottom:4}}>
          <div className="card-value" style={{fontSize:'clamp(18px,9cqi,30px)',fontWeight:700,lineHeight:1.1,color:errCount+(dashboard?.summary?.active_alerts||0) > 10 ? '#ef4444' : (errCount+(dashboard?.summary?.active_alerts||0) > 5 ? '#f59e0b' : 'var(--text)')}}>
            {errCount+(dashboard?.summary?.active_alerts||0)}
          </div>
          <div className="card-sub" style={{marginTop:6}}>
            <div style={{display:'flex',alignItems:'center',gap:6,flexWrap:'wrap'}}>
              <span style={{display:'inline-flex',alignItems:'center',gap:3}}><span style={{width:6,height:6,borderRadius:3,background:'#ef4444'}}/>{errCount} 异常</span>
              <span style={{display:'inline-flex',alignItems:'center',gap:3}}><span style={{width:6,height:6,borderRadius:3,background:'#f59e0b'}}/>{dashboard?.summary?.active_alerts||0} 告警{criticalAlerts > 0 ? <span style={{color:'#ef4444',fontSize:10}}>({criticalAlerts} 严重)</span> : ''}</span>
            </div>
            {(lowStockAlerts.length > 0 || replenishAlerts.length > 0) && <>
              <div style={{fontSize:10,display:'flex',gap:8,marginTop:6}}>
                <span style={{color:'var(--muted2)'}}>● 低库存 {lowStockTotal}</span>
                {slowMovingTotal > 0 && <span style={{color:'var(--muted2)'}}>● 滞销 {slowMovingTotal}</span>}
                <span style={{color:'var(--muted2)'}}>● 需{t("dash.replenish")} {replenishTotal}</span>
              </div>
              <div style={{fontSize:9,display:'flex',gap:6,marginTop:5,color:'var(--muted)'}}>
                <span>{_replMode === 'bbcc' ? 'BC' : 'C'}{lsWhView.main} {t("dash.own")}{lsWhView.own}</span>
                <span style={{color:'var(--border)'}}>|</span>
                <span>{_replMode === 'bbcc' ? 'BC' : 'C'}{rpWhView.main} 自有{rpWhView.own}</span>
              </div>
            </>}
          </div>
        </div>
      </div>

      {/* 3. {t("dash.health")} — 加总 {t("dash.sku")} 数 */}
      <div className="card" style={{borderRadius:26,boxShadow:'0 1px 6px rgba(0,0,0,0.04)',containerType:'inline-size',aspectRatio:'1',display:'flex',flexDirection:'column',padding:16}}>
        {(()=>{
          var healthData = dashboard?.health_index?.[healthTab]||{}
          var isJd = channel === 'jd'
          var bcActive = healthTab === 'bc' || healthTab === 'platform'
          var bcLabel = healthTab === 'platform' ? 'C仓' : 'BC'
          return <>
            <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:4}}>
              <div className="small muted" style={{fontSize:12,lineHeight:1.2}}>库存{t("dash.healthy")}度</div>
              <div style={{display:'flex',gap:2,background:'var(--bg)',borderRadius:99,padding:2,position:'relative'}}>
                <span onClick={function(){setHealthWithSave('own')}}
                  className="clickable"
                  style={{fontSize:9,padding:'2px 6px',borderRadius:99,cursor:'pointer',fontWeight:healthTab==='own'?600:400,background:healthTab==='own'?'var(--card)':'transparent',color:healthTab==='own'?'var(--text)':'var(--muted2)',whiteSpace:'nowrap'}}>自有</span>
                {isJd && _replMode === 'bbcc' && <span onClick={function(){
              if (healthTab === 'bc' || healthTab === 'platform') {
                setBcMenuOpen(!bcMenuOpen)
              } else {
                setHealthWithSave('bc')
              }
            }}
              className="clickable"
              style={{fontSize:9,padding:'2px 6px',borderRadius:99,cursor:'pointer',fontWeight:bcActive?600:400,background:bcActive?'var(--card)':'transparent',color:bcActive?'var(--text)':'var(--muted2)',display:'flex',alignItems:'center',gap:1,whiteSpace:'nowrap'}}>
              {bcLabel}{bcActive && <svg width="6" height="6" viewBox="0 0 8 8" fill="none" style={{transform:'rotate('+(bcMenuOpen?'180':'0')+'deg)',transition:'transform 0.15s'}}><path d="M2 3l2 2 2-2" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/></svg>}
            </span>}
                {(!isJd || _replMode !== 'bbcc') && <span onClick={function(){setHealthWithSave('platform')}}
                  className="clickable"
                  style={{fontSize:10,padding:'2px 8px',borderRadius:99,cursor:'pointer',fontWeight:healthTab==='platform'?600:400,background:healthTab==='platform'?'var(--card)':'transparent',color:healthTab==='platform'?'var(--text)':'var(--muted2)'}}>平台</span>}
                {bcMenuOpen && <div onClick={function(){setBcMenuOpen(false)}} style={{position:'fixed',inset:0,zIndex:9}} />}
                {bcMenuOpen && <div style={{position:'absolute',top:'calc(100% + 4px)',right:0,background:'var(--card)',borderRadius:12,border:'0.5px solid var(--border)',boxShadow:'0 4px 12px rgba(0,0,0,0.1)',overflow:'hidden',minWidth:64,zIndex:10}}>
                  {healthTab === 'bc'
                    ? <div onClick={function(){setHealthWithSave('platform');setBcMenuOpen(false)}}
                        className="clickable" style={{padding:'6px 12px',fontSize:11,color:'var(--text)',cursor:'pointer',whiteSpace:'nowrap'}}>C仓</div>
                    : <div onClick={function(){setHealthWithSave('bc');setBcMenuOpen(false)}}
                        className="clickable" style={{padding:'6px 12px',fontSize:11,color:'var(--text)',cursor:'pointer',whiteSpace:'nowrap'}}>BC</div>
                  }
                </div>}
              </div>
            </div>
            <div style={{flex:1,display:'flex',flexDirection:'column',justifyContent:'flex-end',marginBottom:4}}>
              <div className="card-value" style={{fontSize:'clamp(18px,9cqi,30px)',fontWeight:700,lineHeight:1.1,color:healthData.level==='danger'?'#ef4444':healthData.level==='warning'?'#f59e0b':'var(--success)'}}>{healthData.score != null ? (healthData.score + '分') : '—'}</div>
              <div className="card-sub" style={{marginTop:4}}>
                <div style={{display:'flex',alignItems:'center',gap:6,flexWrap:'wrap'}}>
                  <span style={{color:'var(--success)'}}>● {healthData.healthy||0}健康</span>
                  <span style={{color:'var(--warning)'}}>● {healthData.warning||0}{t("dash.low")}</span>
                </div>
                <div style={{fontSize:10,marginTop:3}}>
                  <span style={{color:'#ef4444'}}>● {healthData.out_of_stock||0}{t("dash.out_of_stock")}</span>
                  <span style={{color:'var(--muted2)'}}> · {healthData.total||0} SKU</span>
                </div>
              </div>
            </div>
            {healthData.out_of_stock > 0 && outOfStockItems.length > 0 && <div style={{marginTop:4}}>
              {outOfStockItems.map((x,i) => (
                <div key={i} style={{fontSize:9,color:'var(--muted2)',lineHeight:1.25,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',marginTop:i===0?2:0}}>
                  <span style={{color:'var(--muted)'}}>{i+1}.</span> {x.product_name || x.sku} <span style={{fontSize:8,color:'var(--muted)',background:'var(--bg)',padding:'0 4px',borderRadius:4}}>{x.warehouse ? fmtWh(x.warehouse) : (healthTab === 'own' ? '自有' : healthTab === 'bc' ? 'BC' : (channel === 'jd' ? 'C仓' : '平台'))}</span>
                </div>
              ))}
              {_oosSrc.length > 3 && <div onClick={function(){setShowAllOut(true)}} className="clickable" style={{textAlign:'left',fontSize:10,color:'var(--muted)',padding:'4px 0',cursor:'pointer'}}>还有 {_oosSrc.length - 3} 条...</div>}
            </div>}
          </>
        })()}
      </div>

      {/* 4. 濒临断货预警 — 全量计数, 弹窗看完整 */}
      <div className="card" style={{borderRadius:26,boxShadow:'0 1px 6px rgba(0,0,0,0.04)',containerType:'inline-size',aspectRatio:'1',display:'flex',flexDirection:'column',padding:16,overflow:'hidden'}}>
        <div style={{display:'flex',alignItems:'center',justifyContent:'space-between'}}>
          <div className="small muted" style={{fontSize:12,lineHeight:1.2}}>濒临断货预警{_replMode === 'bbcc' ? '（BC）' : ''}</div>

        </div>
        {(!_r.items || _r.items.length === 0)
          ? <div style={{flex:1,display:'flex',alignItems:'center',justifyContent:'center',marginBottom:2,flexDirection:'column'}}>
              <div style={{fontSize:12,fontWeight:400,color:'var(--muted2)'}}>{t("dash.stock_ok")}</div>
            </div>
          : <>
              <div style={{marginBottom:4,paddingTop:6,minHeight:0}}>
                <div className="card-value" style={{fontSize:'clamp(17px,8cqi,28px)',fontWeight:700,lineHeight:1.15,color:'#ef4444',marginBottom:1,whiteSpace:'nowrap'}}>{_r.total}</div>
                <div className="card-sub" style={{marginTop:0,fontSize:11,lineHeight:1.4}}>{t("dash.min_days")} {_r.items[0].days_to_empty} {t("dash.days_out")}</div>
                {(riskCritical > 0 || riskWarning > 0 || _r.total > riskCritical + riskWarning) && <div style={{fontSize:10,display:'flex',gap:4,marginTop:1,flexWrap:'wrap',lineHeight:1.3}}>
                  {riskCritical > 0 && <span style={{color:'#ef4444'}}>● {riskCritical} {t("dash.critical")}</span>}
                  {riskWarning > 0 && <span style={{color:'var(--warning)'}}>● {riskWarning} {t("dash.warning")}</span>}
                  {_r.total > riskCritical + riskWarning && <span style={{color:'var(--muted2)'}}>● {_r.total - riskCritical - riskWarning} 观察</span>}
                </div>}
              </div>
              <div style={{flexShrink:0}}>
              {_r.items.slice(0,3).map((x,i) => {
                var whLabel = fmtWh(x.warehouse) || (x.type === 'C' ? 'C仓' : (x.type === 'OWN' ? '自有' : (x.type === 'B' ? 'B仓' : (_replMode === 'bbcc' ? 'BC' : 'C仓'))))
                return (
                <div key={i} style={{fontSize:9,color:'var(--muted2)',lineHeight:1.25,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',marginTop:i===0?2:0}}>
                  <span style={{color:'var(--muted)'}}>{i+1}.</span> {x.product_name || x.sku} <span style={{fontSize:8,color:'var(--muted)',background:'var(--bg)',padding:'0 4px',borderRadius:4,verticalAlign:'1px'}}>{whLabel}</span>
                </div>)
              })}
              </div>
              {_r.total > 3 && <button onClick={()=>{loadFullRisk();setShowAllRisk(true)}} className="clickable" style={{width:'100%',padding:'5px 0 2px',border:'none',borderRadius:0,background:'transparent',fontSize:10,color:'var(--muted)',cursor:'pointer',fontFamily:'inherit',textAlign:'left',flexShrink:0}}>还有 {_r.total - 3} 条...</button>}
            </>}
      </div>
    </div>

    <div className="mid-chart-grid">
      <div className="card" style={{height:'auto',overflow:'visible'}}><div className="section-title">{t("dash.funnel")}</div><Chart option={barOption} height={200} /></div>
      <div className="card" style={{height:'auto',overflow:'visible'}}><div className="section-title" style={{display:'flex',alignItems:'center',justifyContent:'space-between'}}>{_storeDim === 'brand' ? '品牌GMV' : t("dash.store_gmv")}
          <span style={{display:'inline-flex',gap:2,background:'var(--bg)',borderRadius:99,padding:2}}>
            <span onClick={function(){setStoreDim('store')}} className="clickable" style={{fontSize:9,padding:'2px 6px',borderRadius:99,cursor:'pointer',fontWeight:_storeDim==='store'?600:400,background:_storeDim==='store'?'var(--card)':'transparent',color:_storeDim==='store'?'var(--text)':'var(--muted2)',whiteSpace:'nowrap'}}>店铺</span>
            <span onClick={function(){setStoreDim('brand')}} className="clickable" style={{fontSize:9,padding:'2px 6px',borderRadius:99,cursor:'pointer',fontWeight:_storeDim==='brand'?600:400,background:_storeDim==='brand'?'var(--card)':'transparent',color:_storeDim==='brand'?'var(--text)':'var(--muted2)',whiteSpace:'nowrap'}}>品牌</span>
          </span>
        </div><Chart option={storeOption} height={170} /></div>
    </div>

    <div className="chart-row-3">
      <div className="card" style={{height:'auto',overflow:'visible'}}>
        <div className="section-title">{t("dash.low_stock")}{lowStockTotal > 0 ? ` (${lowStockTotal})` : ''}</div>
        {lowStockAlerts.length === 0
          ? <div className="small muted" style={{padding:12,textAlign:'center'}}>{t("dash.no_alerts")}</div>
          : lowStockAlerts.slice(0,5).map(x => (
              <div key={x.id} onClick={() => onAlert && onAlert(x.related_sku)} className="clickable" style={{padding:'8px 0',borderBottom:'1px solid var(--border)',fontSize:13}}>
                <div style={{display:'flex',justifyContent:'space-between',gap:8,alignItems:'flex-start'}}>
                  <span style={{fontWeight:600,fontSize:12,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',flex:1,minWidth:0}}>{x.title}</span>
                  <span className={'pill '+(x.severity==='error'?'danger':'warning')} style={{flexShrink:0}}>{x.severity==='warning'?'警告':t("dash.alert_overstock")}</span>
                </div>
                <div className="small muted" style={{fontSize:11,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',marginTop:2}}>{x.description}</div>
              </div>
            ))}
        {lowStockTotal > 5 && <button onClick={()=>{loadFullAlerts();setShowAllLowStock(true)}} className="clickable" style={{width:'100%',padding:8,border:'none',borderRadius:0,background:'transparent',fontSize:12,color:'var(--muted)',cursor:'pointer',fontFamily:'inherit'}}>还有 {lowStockTotal - 5} 条...</button>}
      </div>
      <div className="card" style={{height:'auto',overflow:'visible'}}>
        <div className="section-title">{t("dash.replenish_alert")}{replenishTotal > 0 ? ` (${replenishTotal})` : ''}</div>
        {replenishAlerts.length === 0
          ? <div className="small muted" style={{padding:12,textAlign:'center'}}>暂无告警</div>
          : replenishAlerts.slice(0,5).map(x => (
              <div key={x.id} onClick={() => onAlert && onAlert(x.related_sku)} className="clickable" style={{padding:'8px 0',borderBottom:'1px solid var(--border)',fontSize:13}}>
                <div style={{display:'flex',justifyContent:'space-between',gap:8,alignItems:'flex-start'}}>
                  <span style={{fontWeight:600,fontSize:12,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',flex:1,minWidth:0}}>{x.title}</span>
                  <span className="pill danger" style={{flexShrink:0}}>补货</span>
                </div>
                <div className="small muted" style={{fontSize:11,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',marginTop:2}}>{x.description}</div>
              </div>
            ))}
        {replenishTotal > 5 && <button onClick={()=>{loadFullAlerts();setShowAllReplenish(true)}} className="clickable" style={{width:'100%',padding:8,border:'none',borderRadius:0,background:'transparent',fontSize:12,color:'var(--muted)',cursor:'pointer',fontFamily:'inherit'}}>还有 {replenishTotal - 5} 条...</button>}
      </div>
    </div>
      {/* 低库存告警弹窗 */}
      {showAllLowStock && <div onClick={function(){setShowAllLowStock(false)}} style={{position:'fixed',inset:0,zIndex:9998,background:'transparent'}} />}
      {showAllLowStock && <div style={{position:'fixed',left:0,right:0,bottom:'calc(env(safe-area-inset-bottom) + 14px)',zIndex:9999,display:'flex',justifyContent:'center',padding:'0 14px',pointerEvents:'none'}}>
        <div onClick={function(e){e.stopPropagation()}} className="material-regular" style={{width:"100%",maxWidth:600,borderRadius:32,padding:"18px 14px calc(14px + env(safe-area-inset-bottom))",boxShadow:"var(--shadow-sheet), inset 0 1px 0 rgba(255,255,255,0.25)",pointerEvents:"auto",maxHeight:"70vh",overflowY:"auto"}}>
          <div style={{fontSize:18,fontWeight:700,marginBottom:12,textAlign:'center',color:'var(--text)'}}>低库存告警 · 共 {lowStockTotal} 条</div>
          {(fullAlerts ? fullAlerts.filter(x => x.alert_type !== 'replenish') : lowStockAlerts).map(function(x) {
            return <div key={x.id} onClick={function(){onAlert && onAlert(x.related_sku, x.warehouse_type)}} className="clickable" style={{padding:'8px 12px',background:'var(--card)',borderRadius:16,marginBottom:6}}>
              <div style={{display:'flex',justifyContent:'space-between',gap:8,alignItems:'flex-start',marginBottom:2}}>
                <span style={{fontWeight:600,fontSize:12,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',flex:1,minWidth:0}}>{x.title}</span>
                <span style={{display:'inline-flex',gap:4,alignItems:'center',flexShrink:0}}>
                  {(x.warehouse ? fmtWh(x.warehouse) : _whTag(x.warehouse_type)) ? <span title={x.warehouse || ''} style={{fontSize:9,padding:'1px 6px',borderRadius:99,background:'var(--bg)',color:'var(--muted)'}}>{(x.warehouse ? fmtWh(x.warehouse) : _whTag(x.warehouse_type))}</span> : null}
                  <span className={'pill '+(x.severity==='error'?'danger':'warning')} style={{fontSize:10}}>{x.severity==='warning'?'警告':'超储'}</span>
                </span>
              </div>
              <div className="small muted" style={{fontSize:11}}>{x.description}</div>
            </div>
          })}
          <div onClick={function(){setShowAllLowStock(false)}} className="clickable" style={{borderRadius:22,padding:12,marginTop:8,background:'var(--primary)',textAlign:'center',cursor:'pointer'}}>
            <span style={{fontSize:15,fontWeight:600,color:'#fff'}}>关闭</span>
          </div>
        </div>
      </div>}

      {/* 补货告警弹窗 */}
      {showAllReplenish && <div onClick={function(){setShowAllReplenish(false)}} style={{position:'fixed',inset:0,zIndex:9998,background:'transparent'}} />}
      {showAllReplenish && <div style={{position:'fixed',left:0,right:0,bottom:'calc(env(safe-area-inset-bottom) + 14px)',zIndex:9999,display:'flex',justifyContent:'center',padding:'0 14px',pointerEvents:'none'}}>
        <div onClick={function(e){e.stopPropagation()}} className="material-regular" style={{width:"100%",maxWidth:600,borderRadius:32,padding:"18px 14px calc(14px + env(safe-area-inset-bottom))",boxShadow:"var(--shadow-sheet), inset 0 1px 0 rgba(255,255,255,0.25)",pointerEvents:"auto",maxHeight:"70vh",overflowY:"auto"}}>
          <div style={{fontSize:18,fontWeight:700,marginBottom:12,textAlign:'center',color:'var(--text)'}}>补货告警 · 共 {replenishTotal} 条</div>
          {(fullAlerts ? fullAlerts.filter(x => x.alert_type === 'replenish') : replenishAlerts).map(function(x) {
            return <div key={x.id} onClick={function(){onAlert && onAlert(x.related_sku, x.warehouse_type)}} className="clickable" style={{padding:'8px 12px',background:'var(--card)',borderRadius:16,marginBottom:6}}>
              <div style={{display:'flex',justifyContent:'space-between',gap:8,alignItems:'flex-start',marginBottom:2}}>
                <span style={{fontWeight:600,fontSize:12,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',flex:1,minWidth:0}}>{x.title}</span>
                <span style={{display:'inline-flex',gap:4,alignItems:'center',flexShrink:0}}>
                  {(x.warehouse ? fmtWh(x.warehouse) : _whTag(x.warehouse_type)) ? <span title={x.warehouse || ''} style={{fontSize:9,padding:'1px 6px',borderRadius:99,background:'var(--bg)',color:'var(--muted)'}}>{(x.warehouse ? fmtWh(x.warehouse) : _whTag(x.warehouse_type))}</span> : null}
                  <span className="pill danger" style={{fontSize:10}}>补货</span>
                </span>
              </div>
              <div className="small muted" style={{fontSize:11}}>{x.description}</div>
            </div>
          })}
          <div onClick={function(){setShowAllReplenish(false)}} className="clickable" style={{borderRadius:22,padding:12,marginTop:8,background:'var(--primary)',textAlign:'center',cursor:'pointer'}}>
            <span style={{fontSize:15,fontWeight:600,color:'#fff'}}>关闭</span>
          </div>
        </div>
      </div>}

      {/* 濒临断货完整列表弹窗 */}
      {showAllRisk && <div onClick={function(){setShowAllRisk(false)}} style={{position:'fixed',inset:0,zIndex:9998,background:'transparent'}} />}
      {showAllRisk && <div style={{position:'fixed',left:0,right:0,bottom:'calc(env(safe-area-inset-bottom) + 14px)',zIndex:9999,display:'flex',justifyContent:'center',padding:'0 14px',pointerEvents:'none'}}>
        <div onClick={function(e){e.stopPropagation()}} className="material-regular" style={{width:"100%",maxWidth:600,borderRadius:32,padding:"18px 14px calc(14px + env(safe-area-inset-bottom))",boxShadow:"var(--shadow-sheet), inset 0 1px 0 rgba(255,255,255,0.25)",pointerEvents:"auto",maxHeight:"70vh",overflowY:"auto"}}>
          <div style={{fontSize:18,fontWeight:700,marginBottom:12,textAlign:'center',color:'var(--text)'}}>濒临断货预警{_replMode === 'bbcc' ? '（BC）' : ''} · 共 {_r.total} 条</div>
          {(fullRisk && fullRisk.length ? fullRisk : (_r._full || _r.items || [])).map(function(x, i) {
            var whLabel = fmtWh(x.warehouse) || (x.type === 'C' ? 'C仓' : (x.type === 'OWN' ? '自有' : (x.type === 'B' ? 'B仓' : (_replMode === 'bbcc' ? 'BC' : 'C仓'))))
            return <div key={i} onClick={function(){onAlert && onAlert(x.sku, _showOwn ? 'own' : 'platform')}} className="clickable" style={{padding:'8px 12px',background:'var(--card)',borderRadius:16,marginBottom:6,display:'flex',justifyContent:'space-between',alignItems:'center',gap:8}}>
              <div style={{minWidth:0,flex:1}}>
                <div style={{fontWeight:600,fontSize:12,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{x.product_name || x.sku}</div>
                <div className="small muted" style={{fontSize:10}}>日销 {x.daily_sales} · 可用 {x.available_qty}</div>
              </div>
              <span title={x.warehouse || ''} style={{fontSize:9,padding:'1px 5px',borderRadius:4,background:'var(--bg)',color:'var(--muted)',flexShrink:0}}>{whLabel}</span>
              <span style={{fontSize:11,fontWeight:600,color:'#ef4444',flexShrink:0,minWidth:38,textAlign:'right'}}>{x.days_to_empty} 天</span>
            </div>
          })}
          <div onClick={function(){setShowAllRisk(false)}} className="clickable" style={{borderRadius:22,padding:12,marginTop:8,background:'var(--primary)',textAlign:'center',cursor:'pointer'}}>
            <span style={{fontSize:15,fontWeight:600,color:'#fff'}}>关闭</span>
          </div>
        </div>
      </div>}

      {/* 缺货列表弹窗（按当前健康卡视图维度: own/平台行级, bc合计; 完整数据) */}
      {showAllOut && <div onClick={function(){setShowAllOut(false)}} style={{position:'fixed',inset:0,zIndex:9998,background:'transparent'}} />}
      {showAllOut && <div style={{position:'fixed',left:0,right:0,bottom:'calc(env(safe-area-inset-bottom) + 14px)',zIndex:9999,display:'flex',justifyContent:'center',padding:'0 14px',pointerEvents:'none'}}>
        <div onClick={function(e){e.stopPropagation()}} className="material-regular" style={{width:"100%",maxWidth:600,borderRadius:32,padding:"18px 14px calc(14px + env(safe-area-inset-bottom))",boxShadow:"var(--shadow-sheet), inset 0 1px 0 rgba(255,255,255,0.25)",pointerEvents:"auto",maxHeight:"70vh",overflowY:"auto"}}>
          <div style={{fontSize:18,fontWeight:700,marginBottom:12,textAlign:'center',color:'var(--text)'}}>缺货 · 共 {_oosSrc.length} 条</div>
          {_oosSrc.map(function(x, i) {
            return <div key={i} className="clickable" style={{padding:'8px 12px',background:'var(--card)',borderRadius:16,marginBottom:6,display:'flex',justifyContent:'space-between',alignItems:'center',gap:8}}>
              <div style={{fontWeight:600,fontSize:12,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',minWidth:0}}>{i+1}. {x.product_name || x.sku}</div>
              <span title={x.warehouse || ''} style={{fontSize:10,color:'var(--muted)',background:'var(--bg)',padding:'0 6px',borderRadius:99,flexShrink:0}}>{(x.warehouse ? fmtWh(x.warehouse) : (healthTab === 'bc' || x.warehouse_type === 'bc' ? 'BC' : (healthTab === 'own' ? '自有' : '平台')))}</span>
            </div>
          })}
          <div onClick={function(){setShowAllOut(false)}} className="clickable" style={{borderRadius:22,padding:12,marginTop:8,background:'var(--primary)',textAlign:'center',cursor:'pointer'}}>
            <span style={{fontSize:15,fontWeight:600,color:'#fff'}}>关闭</span>
          </div>
        </div>
      </div>}
</>
}
