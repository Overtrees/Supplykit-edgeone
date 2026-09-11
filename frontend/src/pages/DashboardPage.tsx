import React, { useState, useMemo, useEffect, useRef } from "react"
import { createPortal } from 'react-dom'
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

// 濒临断货三级(P0): red=击穿补货周期(紧急) / orange=逼近且缓冲破位(预警) / yellow=缓冲破位时间尚够(关注)
const alertAge = (c) => { if (!c) return ''; try { const d = Math.floor((Date.now() - new Date(String(c).replace(' ', 'T')))/86400000); return d >= 1 ? ' · 持续' + d + '天' : '' } catch(e) { return '' } }

const RISK_LV = {
  red: { c: 'var(--danger)', t: '紧急' },
  orange: { c: 'var(--warning)', t: '预警' },
  yellow: { c: 'var(--accent-yellow)', t: '关注' },
}

interface DashboardPageProps { onAlert?: (sku: string, whType?: string, wh?: string) => void; onGoInsights?: (tab: string, sku?: string) => void }

export default function DashboardPage({ onAlert, onGoInsights }: DashboardPageProps) {
  const {dashboard, inventory, qualityLogs, alerts, stockRisk, alertCounts, bcOutOfStock, channel, hammerDashPeriod: periodTab, hammerReplenMode, pageVersion} = useAppStore()
  const [healthTab, setHealthTab] = useState(() => { try { const h = localStorage.getItem('health_tab') || (channel === 'jd' ? 'own' : 'platform'); return ((channel !== 'jd' || (hammerReplenMode || (channel === 'jd' ? 'bbcc' : 'traditional')) !== 'bbcc') && h === 'platform_b') ? 'platform' : h } catch { return channel === 'jd' ? 'own' : 'platform' } })
  // 渠道切换归一化: platform_b(B 仓)为 jd BBCC 专属维度, other 渠道强制 platform(避免残留空维度显示)
  // 渠道/模式切换归一化: platform_b(B仓)为 jd+bbcc 专属, 其他组合强制 platform
  // (用 hammerReplenMode 而非 _replMode —— 本 useEffect 在 _replMode 声明前, 避免 TDZ)
  useEffect(() => {
    const _m = (channel !== 'jd' && (hammerReplenMode || '') !== 'traditional') ? 'traditional' : (hammerReplenMode || (channel === 'jd' ? 'bbcc' : 'traditional'))
    if ((channel !== 'jd' || _m !== 'bbcc') && healthTab === 'platform_b') setHealthWithSave('platform')
  }, [channel, hammerReplenMode])
  const setHealthWithSave = (tab) => { try { localStorage.setItem('health_tab', tab) } catch {} setHealthTab(tab) }
  // GMV 视角切换: total=总GMV(含退款流水) / net=净GMV(剔除退款)——GMV小卡+店铺GMV卡共用
  const [gmvView, setGmvView] = useState('total')
  const [bcMenuOpen, setBcMenuOpen] = useState(false)
  const [showAllLowStock, setShowAllLowStock] = useState(false)
  // 采购&补货告警卡(重构): 补货建议需补(接口) + 采购建议需采(接口), 行标签区分, 跟随补货模式
  const [procList, setProcList] = useState([])
  const [procLoading, setProcLoading] = useState(true)
  const [showAllProc, setShowAllProc] = useState(false)
  const [showAllOther, setShowAllOther] = useState(false)
  const [showAllRisk, setShowAllRisk] = useState(false)
  const [_riskTab] = useState('c')   // 传统模式子视图: c=C仓 / own=自有三方仓
  const [_storeDim, setStoreDim] = useState('store')  // 店铺GMV卡维度: store=店铺(盘子) / brand=品牌(渗透)
  const [showAllOut, setShowAllOut] = useState(false)
  const [, setFullOut] = useState(null)        // 缺货弹窗完整数据(按当前视图维度)
  const [oosList, setOosList] = useState(null)        // 当前维度缺货全量(随 healthTab 拉取, 预览+计数+弹窗同源)
  const [healthTrend, setHealthTrend] = useState([])   // 健康分数趋势(近14天, health-trend 接口)
  const [fullAlerts, setFullAlerts] = useState(null)   // 告警弹窗完整数据(点击时拉取)
  const [fullRisk, setFullRisk] = useState(null)       // 濒临断货完整列表
  const [chLoading, setChLoading] = useState(false)
  const [dashErr, setDashErr] = useState('')
  const dashFailRef = useRef(0)  // 兜底重试失败计数: 连续 >=2 次后提供"刷新页面"强刷兜底(WebView/实例连接异常时重连恢复)
  // 弹窗数据加载(四维完整性): 告警用大 limit 分组配额拿全量; 濒临断货用 full=1; 缺货按维度拉全量
  const loadFullAlerts = async () => { try { const r = await api.get('/api/alerts?channel=' + channel + '&limit=20000', {timeout: 60000}); setFullAlerts(r.data || []) } catch(e) { setFullAlerts([]) } }
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
  const loadFullOut = async () => { const _wh = healthTab === 'own' ? 'own' : healthTab === 'bc' ? 'bc' : healthTab === 'platform' ? 'platform' : 'platform_b'; try { const r = await api.get('/api/inventory/out-of-stock?channel=' + channel + '&wh=' + _wh + '&limit=5000', {timeout: 60000}); const d = Array.isArray(r.data) ? r.data : ((r.data && r.data.items) || []); setFullOut(d) } catch(e) { setFullOut([]) } }
  // 健康卡缺货列表: 随视图维度(own/平台/bc)拉取全量——与 healthData.out_of_stock 计数口径一致
  // (曾用 stockOverview.items(全渠道LIMIT100)过滤, 维度缺货SKU在窗口外时预览/计数漏显)
  useEffect(() => {
    const _wh = healthTab === 'own' ? 'own' : healthTab === 'bc' ? 'bc' : healthTab === 'platform' ? 'platform' : 'platform_b'
    api.get('/api/inventory/out-of-stock?channel=' + channel + '&wh=' + _wh + '&limit=5000', {timeout: 60000}).then(r => {
      const d = Array.isArray(r.data) ? r.data : ((r.data && r.data.items) || [])
      setOosList(d)
    }).catch(() => setOosList([]))
    // 健康分数趋势(近14天, health-trend 接口; 快照由 summary/cron 每日记录)
    api.get('/api/dashboard/health-trend?channel=' + channel + '&days=14&t=' + Date.now(), {timeout: 30000}).then(r => {
      setHealthTrend(Array.isArray(r.data) ? r.data : [])
    }).catch(() => setHealthTrend([]))
  }, [healthTab, channel])
  const reqSeq = useRef(0)
  // 补货模式(看板卡片/接口参数跟随): bbcc(仅 jd) / traditional(other 强制 + jd 可选)
  const _replMode = (channel !== 'jd' && (hammerReplenMode || '') !== 'traditional') ? 'traditional' : (hammerReplenMode || (channel === 'jd' ? 'bbcc' : 'traditional'))
  // —— 采购&补货告警数据(与补货/采购建议页同源, 模式跟随; 补货=建议需补, 采购=建议需采) ——
  const loadProc = async () => {
    setProcLoading(true)
    try {
      const [r, p] = await Promise.all([
        api.get('/api/insights/replenishment?days=28&mode=' + _replMode + '&channel=' + channel + '&need_only=1', {timeout: 90000}),
        api.get('/api/insights/purchase?days=28&mode=' + _replMode + '&channel=' + channel + '&need_only=1', {timeout: 90000}),
      ])
      const repData = r.data || {}
      const repItems = Array.isArray(repData) ? repData : (repData.items || [])
      const repNeed = (Array.isArray(repItems) ? repItems : [])
        .filter(x => (x.suggested_qty || 0) > 0 || (x.b_suggested || 0) > 0)
        .map(x => ({ sku: x.sku, product_name: x.product_name, tag: '补货',
          qty: (x.suggested_qty || 0) + (x.b_suggested || 0),
          note: x.note || '', days_to_empty: x.days_to_empty || 999, wh: x.warehouse || '' }))
      const purObj = p.data || {}
      const purItems = (purObj && purObj.suggestions) || []
      const purNeed = (Array.isArray(purItems) ? purItems : [])
        .filter(x => (x.actual_purchase || 0) > 0)
        .map(x => ({ sku: x.sku, product_name: x.product_name, tag: '采购',
          qty: x.actual_purchase || 0,
          note: x.note || '', days_to_empty: x.days_to_empty || 999, wh: x.warehouse || '' }))
      // 采购在前(更紧急: 需向供应商采且可撑<14天), 随后补货按缺口
      const sorted = [...purNeed, ...repNeed].sort((a, b) => {
        if (a.tag !== b.tag) return a.tag === '采购' ? -1 : 1
        return a.days_to_empty - b.days_to_empty
      })
      setProcList(sorted)
    } catch (e) { setProcList([]) }
    setProcLoading(false)
  }
  useEffect(() => { loadProc() }, [channel, _replMode])
  // 规则/参数保存、任务完成 → 重拉(与 insights-refresh/rules-changed 联动)
  useEffect(() => {
    const h = () => { loadProc() }
    window.addEventListener('rules-changed', h)
    window.addEventListener('insights-refresh', h)
    return () => { window.removeEventListener('rules-changed', h); window.removeEventListener('insights-refresh', h) }
  }, [channel, _replMode])
  const procTotal = procList.length
  // 其他告警(规则引擎非低库存类: 超卖/濒临断货/健康/滞销/自定义) —— 可点开明细, 不再只有计数
  const otherTotal = Object.entries((alertCounts && alertCounts.by_type) || {})
    .filter(([k]) => !['low_stock', 'replenish', 'purchase_need'].includes(k))
    .reduce((sum, [, v]) => sum + (v || 0), 0)
  useEffect(() => {
    const seq = ++reqSeq.current
    // 无感刷新: 仅当无 dashboard 数据(首次/清空后)才骨架, 有旧数据则不骨架(先显示旧值, 后台拉新替换)
    setChLoading(!useAppStore.getState().dashboard)
    const load = () => {
      // 首屏拆流: summary+aux 先渲染(不阻塞骨架屏), stock-risk 后置到达后更新断货卡
      // (stock-risk 含 OTIF/逐仓日销/动态SS/加速判定较重, 首屏先看 GMV/告警/健康, 断货 1-2s 补齐)
      Promise.allSettled([
        api.get('/api/dashboard/summary?t=' + Date.now(), {timeout: 60000}),
        api.get('/api/dashboard/aux?channel=' + channel + '&mode=' + _replMode + '&t=' + Date.now(), {timeout: 60000}),
      ]).then(([s, ax]) => {
        if (seq !== reqSeq.current) { setChLoading(false); return }  // 竞态丢弃
        // 兜底: summary 必须 fulfilled 且 data.summary 存在才算成功(seed填充/表重建期间
        // 可能返回异常结构 → dash=null 且无ErrorRetry → 看板空白缺口)
        const dashOk = s.status === 'fulfilled' && s.value.data && s.value.data.summary
        const dash = dashOk ? s.value.data : null
        setDashErr((s.status === 'rejected' || !dashOk) ? '加载失败，可能是网络异常或数据正在处理中' : '')
        const aux = (ax && ax.status === 'fulfilled') ? (ax.value.data || {}) : {}
        const alerts = aux.alerts || []
        const stockRisk = useAppStore.getState().stockRisk || {}
        const ov = aux.stockOverview || {}
        useAppStore.setState({ dashboard: dash, alerts, stockRisk, alertCounts: aux.alertCounts || null, bcOutOfStock: aux.bcOutOfStock || [], inventory: ov.items || [], _stockOverview: ov, loading: false, dataLoaded: true })
        setChLoading(false)
      // 首次加载关键数据为空时自动重试（进程重启后缓存未就绪/慢接口超时兜底），最多 3 次
      // B 维度(items)已移除(bbcc 用 bcItems / traditional 用 cItems+ownItems) → 空检查按各维度数组
      const _srEmpty = Array.isArray(stockRisk) ? stockRisk.length === 0 : !(stockRisk && (((stockRisk.bcItems || []).length) || ((stockRisk.cItems || []).length) || ((stockRisk.ownItems || []).length)))
      if ((!dash || _srEmpty) && seq === reqSeq.current) {
        let retries = 0
        const timer = setInterval(() => {
          retries += 1
          if (retries > 3 || seq !== reqSeq.current) { clearInterval(timer); return }
          Promise.allSettled([
            api.get('/api/dashboard/summary?t=' + Date.now()),
            api.get('/api/dashboard/stock-risk?t=' + Date.now()),
          ]).then(([s2, r2]) => {
            if (seq !== reqSeq.current) { clearInterval(timer); return }
            const d2 = s2.status === 'fulfilled' ? s2.value.data : null
            const rv2 = r2.status === 'fulfilled' ? (r2.value.data || []) : []
            useAppStore.setState({
              dashboard: d2 || useAppStore.getState().dashboard,
              stockRisk: (Array.isArray(rv2) ? rv2.length : (rv2 && (((rv2.bcItems || []).length) || ((rv2.cItems || []).length) || ((rv2.ownItems || []).length)))) ? rv2 : useAppStore.getState().stockRisk,
            })
            if (d2 && rv2.length) clearInterval(timer)
          })
        }, 3000)
      }
    }).catch(() => setChLoading(false))
      // stock-risk 后置(独立 30s TTL 接口, 不阻塞首屏骨架; 到达后更新断货卡)
      api.get('/api/dashboard/stock-risk?channel=' + channel + '&t=' + Date.now(), {timeout: 60000})
        .then(r => { if (seq === reqSeq.current && r.data) useAppStore.setState({ stockRisk: r.data }) })
        .catch(() => {})
    }
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
        const [s, ax, sr] = await Promise.all([
          api.get('/api/dashboard/summary?' + _t, {timeout: 60000}),
          api.get('/api/dashboard/aux?channel=' + channel + '&mode=' + _replMode + '&' + _t, {timeout: 60000}),
          api.get('/api/dashboard/stock-risk?channel=' + channel + '&' + _t, {timeout: 60000}),
        ])
        const aux = ax.data || {}
        useAppStore.setState({ dashboard: s.data, alerts: aux.alerts || [], stockRisk: (sr && sr.data) || useAppStore.getState().stockRisk || {}, alertCounts: aux.alertCounts || null, bcOutOfStock: aux.bcOutOfStock || [], inventory: (aux.stockOverview || {}).items || [], loading: false, dataLoaded: true })
      } catch {} finally { silentBusy.current = false }
    }, 30000)
    return () => clearInterval(timer)
  }, [channel])
  const periodTrend = dashboard?.periods?.[periodTab + '_trend'] || dashboard?.trend || []
  const periodMeta = dashboard?.periods?.[periodTab] || {}
  // 店铺/品牌 GMV 数据量(横向滚动+自动采样判定, 渲染层可用)
  const storeDataLen = (_storeDim === 'brand' ? ((dashboard?.period_brands?.[periodTab] || dashboard?.brands || []).length) : ((dashboard?.period_stores?.[periodTab] || dashboard?.stores || []).length)) || 0

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
    const storeData = _storeDim === 'brand'
      ? (dashboard?.period_brands?.[periodTab] || dashboard?.brands || [])
      : (dashboard?.period_stores?.[periodTab] || dashboard?.stores || [])
    // GMV 视角切换: 净GMV=总GMV-退款(后端 stores/brands 已带 net_gmv)
    const _g = (i) => gmvView === 'net' ? (i.net_gmv != null ? i.net_gmv : i.gmv)
        : gmvView === 'payout' ? (i.payout != null ? i.payout : i.gmv) : i.gmv
    return {
    tooltip: { trigger: 'axis', valueFormatter: (v) => '¥' + Number(v).toLocaleString('zh-CN', {minimumFractionDigits:2,maximumFractionDigits:2}), extraCssText: 'z-index:1000', hideDelay: 100 },
    // 底部店铺/品牌名: 全量显示不截断; 长名截断+换行; 数量多(品牌35+)时旋转避免重叠
    xAxis: { type: 'category', data: storeData.map(i => i.name) || [],
      // 品牌维度标签太多(35+)重叠→隐藏, 用悬浮/点击 tooltip 显示名称; 店铺维度保留(数量少, 截断+旋转)
      axisLabel: (storeData.length > 8 || _storeDim === 'brand')
        ? { show: true, interval: 'auto', fontSize: 8, margin: 4,
            formatter: (v) => { const t = String(v||''); return t.length > 5 ? t.slice(0,5)+'…' : t } }
        : { fontSize: 8, interval: 0, rotate: storeData.length > 8 ? 35 : 0, margin: 6,
            formatter: (v) => { const t = String(v||''); if (t.length > 6) return t.slice(0,6)+'…'; return t },
            width: 64, overflow: 'truncate' } },
    yAxis: { type: 'value',
      axisLabel: { fontSize: 8, formatter: (v) => Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 0 }) }, splitNumber: 4,
      max: (v) => Math.ceil(v.max * 1.15 / 1000) * 1000 },
    series: [{ type: 'bar', barMaxWidth: 26, data: storeData.map((i, idx) => ({ value: Math.round(_g(i) * 100) / 100, itemStyle: { color: ['#f59e0b','#06b6d4','#8b5cf6','#ec4899','#10b981','#f97316'][idx % 6] } })) || [] }],
    grid: { containLabel: true, top: 8, bottom: (storeData.length > 8 || _storeDim === 'brand') ? 30 : 30, left: 8, right: 12 }
  }}, [dashboard, periodTab, gmvView, _storeDim])

  const barOption = useMemo(() => {
    const f = dashboard?.period_funnel?.[periodTab] || dashboard?.funnel || []
    const names = f.map(x => x.name)
    const values = f.map(x => x.value)
    const ftotal = (f[0] && f[0].value) || 1
    return {
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, extraCssText: 'z-index:1000', hideDelay: 100, formatter: (p) => {
        const idx = p[0]?.dataIndex ?? 0; const item = f[idx]
        return `${item.name}<br/>数量: ${item.value}单<br/>占比: ${item.percentage}%<br/>转化率: ${item.conversion}%`
      }},
      grid: { containLabel: true, top: 4, bottom: 6, right: 58 },
      xAxis: { type: 'value', show: false, max: ftotal * 1.15 },
      yAxis: { type: 'category', data: names, axisLabel: { fontSize: 10 } },
      series: [{
        type: 'bar', data: values.map((v, i) => ({ value: v, itemStyle: { color: ['#f59e0b','#06b6d4','#8b5cf6','#ec4899','#10b981'][i % 5] } })),
        barWidth: '60%',
        label: { show: true, position: 'right', fontSize: 10, formatter: (p) => p.value >= 10000 ? (p.value / 10000).toFixed(1) + '万单' : `${p.value}单`, textBorderColor: 'transparent' }
      }]
    }
  }, [dashboard, periodTab])

  const lowStock = (inventory||[]).filter(x => Number(x.available_qty) < Number(x.safety_qty)).length
  const errCount = (qualityLogs||[]).length
  const alertsList = Array.isArray(alerts) ? alerts.filter(x => x.status === 'active') : []
  const lowStockAlerts = alertsList.filter(x => x.alert_type === 'low_stock')
  // 看板「(N 严重)」等计数一律取后端 alertCounts(独立 COUNT)，不得从截断列表 filter 得出——
  // 列表每组各取 200 条，总数可能远大于此，filter 计数会系统性漏报
  const _acSev = (alertCounts || {}).by_severity || {}
  const criticalAlerts = _acSev.error != null ? _acSev.error : alertsList.filter(x => x.severity === 'error').length
  // 拆分类: 低库存(纯low_stock)与滞销(slow_moving)独立计数(曾合并为non_replenish导致"低库存N"含滞销误导)
  const _acByType2 = (alertCounts && alertCounts.by_type) || {}
  const lowStockTotal = _acByType2.low_stock != null ? _acByType2.low_stock : lowStockAlerts.filter(x => x.alert_type === 'low_stock').length
  const slowMovingTotal = _acByType2.slow_moving != null ? _acByType2.slow_moving : lowStockAlerts.filter(x => x.alert_type === 'slow_moving').length
  const periodDays = periodTab === 'custom' ? (periodMeta?.days || 30) : ({today:1,week:7,month:30}[periodTab]||30)
  // 濒临断货: 兼容旧数组/新{items,total,critical,warning}结构——卡上大数字/紧急警告用全量计数(完整性)
  // 补货模式联动: bbcc→BC 合计维度(对齐库存卡 bc tab: B+C 按 SKU 合计); traditional→C 仓维度
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
    ? (function(){ const _merged = [].concat(_sr.items||[], _srOwn.items||[]).sort(function(a,b){return (a.days_to_empty||999)-(b.days_to_empty||999)}); const _tot = ( _sr.total||0 ) + ( _srOwn.total||0 ); const _c = ( _sr.critical||0 ) + ( _srOwn.critical||0 ); const _w = ( _sr.warning||0 ) + ( _srOwn.warning||0 ); const _full = [].concat(_sr.items||[], _srOwn.items||[]).sort(function(a,b){return (a.days_to_empty||999)-(b.days_to_empty||999)}); return {items: _merged.slice(0,10), total: _tot, critical: _c, warning: _w, _full: _full} })()
    : _sr
  const riskCritical = _r.critical != null ? _r.critical : (_r.items||[]).filter(x => x.days_to_empty < 3).length
  const riskWarning = _r.warning != null ? _r.warning : (_r.items||[]).filter(x => x.days_to_empty >= 3 && x.days_to_empty < 7).length
    // 缺货列表 = stockOverview.items(本身就是 avail<=0 的缺货SKU, 含warehouse_type)
  // 缺货列表 = 当前视图维度全量(oosList, 随 healthTab 拉取); 未加载时回退旧逻辑
  const _oosSrc = oosList || (healthTab === 'bc' ? (bcOutOfStock || []) : (healthTab === 'own' ? (inventory||[]).filter(x => x.warehouse_type === 'own') : (inventory||[]).filter(x => x.warehouse_type === 'platform')))
  const outOfStockItems = _oosSrc.slice(0,3)
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

  if (chLoading) return <div className="card" style={{padding:16}}>{[1,2,3,4,5,6,7].map(i=><div key={i} className="skeleton" style={{height:80,marginBottom:8,borderRadius:'var(--radius-card)'}}/>)}</div>
  if (dashErr && !dashboard) return <ErrorRetry error={dashErr} onRetry={() => { dashFailRef.current += 1; window.__setPage && window.__setPage('dash') }} onReload={dashFailRef.current >= 2 ? () => { location.reload() } : null} />
  return <>
    <div className="card-grid" style={{marginBottom:16}}>
      {/* 1. GMV 卡 — 加环比微趋势线 + 日均 */}
      <div className="card stat-card">
        <div style={{display:'flex',alignItems:'center',justifyContent:'space-between'}}>
          <div className="small muted" style={{fontSize:'var(--font-sm)',lineHeight:1.2}}>{periodTab === 'custom' ? '自定义' : periodLabel[periodTab]} GMV</div>
          {/* GMV 视角切换(总/净/回款), 样式对齐健康小卡 tab: 紧凑segmented pill + 短标签 */}
          <div style={{display:'flex',gap:2,background:'var(--bg)',borderRadius:'var(--radius-full)',padding:2}}>
            {[['total','总'],['net','净'],['payout','回款']].map(([v,l]) => (
              <span key={v} onClick={function(){setGmvView(v)}} className="clickable"
                style={{fontSize:'var(--font-9)',padding:'2px 6px',borderRadius:'var(--radius-full)',cursor:'pointer',fontWeight:gmvView===v?600:400,background:gmvView===v?'var(--card)':'transparent',color:gmvView===v?'var(--text)':'var(--muted2)',whiteSpace:'nowrap'}}>{l}</span>
            ))}
          </div>
        </div>
        <div style={{flex:1,display:'flex',flexDirection:'column',justifyContent:'flex-end',marginBottom:4}}>
          <div className="card-value" style={{fontSize:'clamp(17px,8cqi,28px)',fontWeight:700,lineHeight:1.15,whiteSpace:'nowrap',overflow:'hidden',textOverflow:'ellipsis',fontVariantNumeric:'tabular-nums'}}>
            {(() => { const _g = gmvView === 'net' ? (periodMeta.net_gmv != null ? periodMeta.net_gmv : ((periodMeta.gmv||0) - (dashboard?.summary?.refund_amount||0))) : (gmvView === 'payout' ? (periodMeta.payout != null ? periodMeta.payout : ((periodMeta.gmv||0) - (dashboard?.summary?.refund_amount||0) - (dashboard?.summary?.subsidy_amount||0))) : periodMeta.gmv); return '¥' + Number(_g||0).toLocaleString() })()}
          </div>
          <div className="card-sub" style={{marginTop:6,display:'flex',alignItems:'center',gap:8}}>
            <span>{periodMeta.orders} 单</span>
            {(() => {
              const last = Number(periodMeta.gmv||0), prev = Number(periodMeta.prev_gmv||0)
              if (!prev) return null
              const pct = ((last - prev) / prev * 100)
              const _cmpLabel = periodTab === 'today' ? '较昨日' : (periodTab === 'week' ? '较上周' : '较上月')
              return <span style={{fontSize:'var(--font-xs)',fontWeight:600,color:pct >= 0 ? 'var(--success)' : 'var(--danger)'}}>
                {pct >= 0 ? '↑' : '↓'} {Math.abs(pct).toFixed(1)}% <span style={{fontSize:'var(--font-9)',fontWeight:400,color:'var(--muted2)'}}>{_cmpLabel}</span>
              </span>
            })()}
            <span style={{color:'var(--muted2)',fontSize:'var(--font-10)'}}>· 日均 ¥{(() => { const _g = gmvView === 'net' ? (periodMeta.net_gmv != null ? periodMeta.net_gmv : ((periodMeta.gmv||0) - (dashboard?.summary?.refund_amount||0))) : (gmvView === 'payout' ? (periodMeta.payout != null ? periodMeta.payout : ((periodMeta.gmv||0) - (dashboard?.summary?.refund_amount||0) - (dashboard?.summary?.subsidy_amount||0))) : periodMeta.gmv); return Math.round((_g||0)/periodDays).toLocaleString() })()}</span>
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

      {/* 2. {t("dash.pending")}卡 — iOS 18 天气风: 数字主角/语义状态行/分级明细 */}
      <div className="card stat-card">
        <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',minHeight:14}}>
          <div style={{fontSize:'var(--font-sm)',fontWeight:600,color:'var(--muted)',letterSpacing:0.2,lineHeight:1.2}}>{t("dash.pending")}</div>
          {criticalAlerts > 0 && <span style={{fontSize:'var(--font-10)',fontWeight:600,color:'var(--danger)'}}>{t("dash.critical")} {criticalAlerts}</span>}
        </div>
        {((errCount + (dashboard?.summary?.active_alerts||0)) === 0)
          ? <div style={{flex:1,display:'flex',flexDirection:'column',alignItems:'center',justifyContent:'center',gap:6}}>
              <div style={{width:44,height:44,borderRadius:'50%',background:'var(--bg)',display:'flex',alignItems:'center',justifyContent:'center',color:'var(--success)'}}>
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
              </div>
              <div style={{fontSize:'var(--font-sm)',fontWeight:500,color:'var(--muted2)'}}>{t("dash.no_alerts")}</div>
            </div>
          : <>
              <div style={{marginTop:4}}>
                <div className="card-value" style={{fontSize:'clamp(17px,8cqi,28px)',fontWeight:700,lineHeight:1.15,color:errCount+(dashboard?.summary?.active_alerts||0) > 10 ? 'var(--danger)' : (errCount+(dashboard?.summary?.active_alerts||0) > 5 ? 'var(--warning)' : 'var(--text)'),fontVariantNumeric:'tabular-nums',marginBottom:1}}>
                  {errCount+(dashboard?.summary?.active_alerts||0)}
                </div>
                <div className="card-sub" style={{marginTop:2,display:'flex',gap:10,flexWrap:'wrap'}}>
                  <span style={{fontSize:'var(--font-xs)',fontWeight:600,color:'var(--danger)'}}>● {errCount} 异常</span>
                  <span style={{fontSize:'var(--font-xs)',fontWeight:600,color:'var(--warning)'}}>● {dashboard?.summary?.active_alerts||0} 告警</span>
                </div>
              </div>
              {(lowStockAlerts.length > 0 || procTotal > 0) && <>
                <div style={{fontSize:'var(--font-10)',display:'flex',gap:10,marginTop:8,flexWrap:'wrap',lineHeight:1.4}}>
                  <span style={{color:'var(--muted2)'}}>● 低库存 {lowStockTotal}</span>
                  {slowMovingTotal > 0 && <span style={{color:'var(--muted2)'}}>● 滞销 {slowMovingTotal}</span>}
                  <span style={{color:'var(--muted2)'}}>● 采购&补货 {procTotal}</span>
                </div>
                <div style={{fontSize:'var(--font-10)',display:'flex',gap:8,marginTop:4,alignItems:'center',color:'var(--muted)',flexWrap:'wrap'}}>
                  <span>{_replMode === 'bbcc' ? 'BC' : 'C'}{lsWhView.main} {t("dash.own")}{lsWhView.own}</span>
                  <span style={{color:'var(--border)'}}>|</span>
                  <span>采购{procList.filter(x=>x.tag==='采购').length} · 补货{procList.filter(x=>x.tag==='补货').length}</span>
                  {otherTotal > 0 && <span style={{color:'var(--muted)'}}>|</span>}
                  {otherTotal > 0 && <span onClick={function(e){e.stopPropagation();loadFullAlerts();setShowAllOther(true)}} className="clickable pill info" style={{cursor:'pointer',fontSize:'var(--font-10)',padding:'1px 8px',minHeight:'auto',lineHeight:'16px'}}>其他 {otherTotal}</span>}
                </div>
              </>}
            </>}
      </div>

      {/* 3. {t("dash.health")} — 加总 {t("dash.sku")} 数 */}
      <div className="card stat-card">
        {(()=>{
          const healthData = dashboard?.health_index?.[healthTab]||{}
          const isJd = channel === 'jd'
          const bcActive = healthTab === 'bc' || healthTab === 'platform'
          const bcLabel = healthTab === 'platform' ? 'C仓' : 'BC'
          return <>
            <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:4}}>
              <div className="small muted" style={{fontSize:'var(--font-sm)',lineHeight:1.2}}>库存{t("dash.healthy")}度</div>
              <div style={{display:'flex',gap:2,background:'var(--bg)',borderRadius:'var(--radius-full)',padding:2,position:'relative'}}>
                <span onClick={function(){setHealthWithSave('own')}}
                  className="clickable"
                  style={{fontSize:'var(--font-9)',padding:'2px 6px',borderRadius:'var(--radius-full)',cursor:'pointer',fontWeight:healthTab==='own'?600:400,background:healthTab==='own'?'var(--card)':'transparent',color:healthTab==='own'?'var(--text)':'var(--muted2)',whiteSpace:'nowrap'}}>自有</span>
                {isJd && _replMode === 'bbcc' && <span onClick={function(){
              if (healthTab === 'bc' || healthTab === 'platform') {
                setBcMenuOpen(!bcMenuOpen)
              } else {
                setHealthWithSave('bc')
              }
            }}
              className="clickable"
              style={{fontSize:'var(--font-9)',padding:'2px 6px',borderRadius:'var(--radius-full)',cursor:'pointer',fontWeight:bcActive?600:400,background:bcActive?'var(--card)':'transparent',color:bcActive?'var(--text)':'var(--muted2)',display:'flex',alignItems:'center',gap:1,whiteSpace:'nowrap'}}>
              {bcLabel}{bcActive && <svg width="6" height="6" viewBox="0 0 8 8" fill="none" style={{transform:'rotate('+(bcMenuOpen?'180':'0')+'deg)',transition:'transform 0.15s'}}><path d="M2 3l2 2 2-2" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/></svg>}
            </span>}
                {(!isJd || _replMode !== 'bbcc') && <span onClick={function(){setHealthWithSave('platform')}}
                  className="clickable"
                  style={{fontSize:'var(--font-10)',padding:'8px 10px',minHeight:30,borderRadius:'var(--radius-full)',cursor:'pointer',fontWeight:healthTab==='platform'?600:400,background:healthTab==='platform'?'var(--card)':'transparent',color:healthTab==='platform'?'var(--text)':'var(--muted2)'}}>平台</span>}
                {bcMenuOpen && <div onClick={function(){setBcMenuOpen(false)}} style={{position:'fixed',inset:0,zIndex:9}} />}
                {bcMenuOpen && <div style={{position:'absolute',top:'calc(100% + 4px)',right:0,background:'var(--card)',borderRadius:'var(--radius-sm)',border:'0.5px solid var(--border)',boxShadow:'0 4px 12px rgba(0,0,0,0.1)',overflow:'hidden',minWidth:64,zIndex:10}}>
                  {healthTab === 'bc'
                    ? <div onClick={function(){setHealthWithSave('platform');setBcMenuOpen(false)}}
                        className="clickable" style={{padding:'6px 12px',fontSize:'var(--font-xs)',color:'var(--text)',cursor:'pointer',whiteSpace:'nowrap'}}>C仓</div>
                    : <div onClick={function(){setHealthWithSave('bc');setBcMenuOpen(false)}}
                        className="clickable" style={{padding:'6px 12px',fontSize:'var(--font-xs)',color:'var(--text)',cursor:'pointer',whiteSpace:'nowrap'}}>BC</div>
                  }
                </div>}
              </div>
            </div>
            <div key={'h'+healthTab} style={{flex:1,display:'flex',flexDirection:'column',justifyContent:'flex-end',marginBottom:4,animation:'fadeIn 0.18s ease'}}>
              <div className="card-value" style={{fontSize:'clamp(17px,8cqi,28px)',fontWeight:700,lineHeight:1.15,fontVariantNumeric:'tabular-nums',color:healthData.level==='danger'?'var(--danger)':healthData.level==='warning'?'var(--warning)':'var(--success)'}}>{healthData.score != null ? (healthData.score + '分') : '—'}</div>
              <div className="card-sub" style={{marginTop:4}}>
                <div style={{display:'flex',alignItems:'center',gap:8,marginTop:2,flexWrap:'wrap'}}>
                  <span style={{fontSize:'var(--font-xs)',fontWeight:600,color:'var(--success)'}}>● {healthData.healthy||0}健康</span>
                  <span style={{fontSize:'var(--font-xs)',fontWeight:600,color:'var(--warning)'}}>● {healthData.warning||0}{t("dash.low")}</span>
                  <span onClick={function(){ if (_oosSrc.length > 0) setShowAllOut(true) }} className="clickable" style={{fontSize:'var(--font-xs)',fontWeight:600,color:'var(--danger)',cursor: _oosSrc.length > 0 ? 'pointer' : 'default'}}>● {healthData.out_of_stock||0}{t("dash.out_of_stock")}</span>
                </div>
                <div style={{fontSize:'var(--font-10)',marginTop:2,color:'var(--muted2)'}}>{healthData.total||0} SKU</div>
                {/* 健康分数趋势(近14天, 按当前维度) —— 原缺货前3+还有N条区 */}
                {(() => {
                  const _key = healthTab === 'own' ? 'own' : healthTab === 'bc' ? 'bc' : 'platform'
                  const _trend = (Array.isArray(healthTrend) ? healthTrend : []).filter(x => x[_key] != null && x[_key] > 0)
                  if (_trend.length < 2) return null
                  return <div style={{marginTop:6,height:40}}>
                    <Chart option={{
                      grid: { left: 0, right: 0, top: 4, bottom: 0 },
                      xAxis: { type: 'category', show: false, data: _trend.map(x => String(x.date).slice(5)) },
                      yAxis: { type: 'value', show: false, min: 0, max: 100 },
                      series: [{ type: 'line', data: _trend.map(x => x[_key]), smooth: true, symbol: 'none',
                        lineStyle: { width: 2, color: healthData.level === 'danger' ? 'var(--danger)' : healthData.level === 'warning' ? 'var(--warning)' : 'var(--success)' },
                        areaStyle: { opacity: 0.12, color: healthData.level === 'danger' ? 'var(--danger)' : healthData.level === 'warning' ? 'var(--warning)' : 'var(--success)' } }],
                      animationDuration: 400,
                    }} height={40} />
                  </div>
                })()}
              </div>
            </div>
          </>
        })()}
      </div>

      {/* 4. 濒临断货预警 — 全量计数, 弹窗看完整(iOS 18 天气小组件风, 紧凑版) */}
      <div className="card stat-card">
        <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',minHeight:14}}>
          <div style={{fontSize:'var(--font-sm)',fontWeight:600,color:'var(--muted)',letterSpacing:0.2,lineHeight:1.2}}>濒临断货预警{_replMode === 'bbcc' ? '（BC）' : ''}</div>
        </div>
        {(!_r.items || _r.items.length === 0)
          ? <div style={{flex:1,display:'flex',flexDirection:'column',alignItems:'center',justifyContent:'center',gap:6}}>
              <div style={{width:44,height:44,borderRadius:'50%',background:'var(--bg)',display:'flex',alignItems:'center',justifyContent:'center',color:'var(--success)'}}>
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
              </div>
              <div style={{fontSize:'var(--font-sm)',fontWeight:500,color:'var(--muted2)'}}>{t("dash.stock_ok")}</div>
            </div>
          : <>
              <div style={{marginTop:4}}>
                <div className="card-value" style={{fontSize:'clamp(17px,8cqi,28px)',fontWeight:700,lineHeight:1.15,color:'var(--danger)',fontVariantNumeric:'tabular-nums',marginBottom:1}}>{_r.total}</div>
                <div className="card-sub" style={{marginTop:0,fontSize:'var(--font-xs)',color:'var(--muted)',lineHeight:1.4}}>{t("dash.min_days")} {_r.items[0].days_to_empty} {t("dash.days_out")}</div>
                {(riskCritical > 0 || riskWarning > 0 || _r.total > riskCritical + riskWarning) && <div style={{fontSize:'var(--font-10)',display:'flex',gap:6,marginTop:4,flexWrap:'wrap',lineHeight:1.3}}>
                  {riskCritical > 0 && <span style={{color:'var(--danger)',fontWeight:600}}>● {riskCritical} {t("dash.critical")}</span>}
                  {riskWarning > 0 && <span style={{color:'var(--warning)',fontWeight:600}}>● {riskWarning} {t("dash.warning")}</span>}
                  {_r.total > riskCritical + riskWarning && <span style={{color:'var(--muted2)'}}>● {_r.total - riskCritical - riskWarning} 观察</span>}
                </div>}
              </div>
              <div style={{flexShrink:0,paddingTop:6}}>
              {_r.items.slice(0,3).map((x,i) => {
                const whLabel = fmtWh(x.warehouse) || (x.type === 'C' ? 'C仓' : (x.type === 'OWN' ? '自有' : (x.type === 'B' ? 'B仓' : (_replMode === 'bbcc' ? 'BC' : 'C仓'))))
                const lv = RISK_LV[x.level]
                return (
                <div key={i} title={x.product_name || x.sku} onClick={function(){ const _b = x.type==='BC' || x.warehouse==='BC'; onAlert && onAlert(x.sku, x.type==='OWN' ? 'own' : 'platform', _b ? '' : x.warehouse) }} className="clickable" style={{display:'flex',alignItems:'center',gap:4,fontSize:'var(--font-9)',color:'var(--muted2)',lineHeight:1.3,marginTop:i===0?0:2,overflow:'hidden',cursor:'pointer'}}>
                  {lv ? <span style={{color:lv.c,lineHeight:1}}>●</span> : null}
                  <span style={{flex:1,minWidth:0,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{x.product_name || x.sku}</span>
                  <span style={{flexShrink:0,fontSize:8,fontWeight:600,color:'var(--muted)',background:'var(--bg)',padding:'0 4px',borderRadius:4}}>{whLabel}</span>
                </div>)
              })}
              </div>
              {_r.total > 3 && <button onClick={()=>{loadFullRisk();setShowAllRisk(true)}} aria-label={`还有 ${_r.total - 3} 条`} className="clickable" style={{width:'100%',padding:'4px 0 0',border:'none',borderRadius:0,background:'transparent',color:'var(--primary)',cursor:'pointer',fontFamily:'inherit',textAlign:'left',flexShrink:0,display:'flex',alignItems:'center'}}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="5" cy="12" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="19" cy="12" r="1.8"/></svg>
              </button>}
            </>}
      </div>
    </div>

    <div className="mid-chart-grid">
      <div className="card" style={{height:'auto',overflow:'visible'}}><div className="section-title">{t("dash.funnel")}</div><Chart option={barOption} height={200} /></div>
      <div className="card" style={{height:'auto',overflow:'visible'}}><div className="section-title" style={{display:'flex',alignItems:'center',justifyContent:'space-between'}}>{_storeDim === 'brand' ? '品牌GMV' : t("dash.store_gmv")}
          <span style={{display:'inline-flex',gap:2,background:'var(--bg)',borderRadius:'var(--radius-full)',padding:2}}>
            <span onClick={function(){setStoreDim('store')}} className="clickable" style={{fontSize:'var(--font-9)',padding:'2px 6px',borderRadius:'var(--radius-full)',cursor:'pointer',fontWeight:_storeDim==='store'?600:400,background:_storeDim==='store'?'var(--card)':'transparent',color:_storeDim==='store'?'var(--text)':'var(--muted2)',whiteSpace:'nowrap'}}>店铺</span>
            <span onClick={function(){setStoreDim('brand')}} className="clickable" style={{fontSize:'var(--font-9)',padding:'2px 6px',borderRadius:'var(--radius-full)',cursor:'pointer',fontWeight:_storeDim==='brand'?600:400,background:_storeDim==='brand'?'var(--card)':'transparent',color:_storeDim==='brand'?'var(--text)':'var(--muted2)',whiteSpace:'nowrap'}}>品牌</span>
          </span>
        </div>
        <div style={{ overflowX: (storeDataLen > 8) ? 'auto' : 'visible', WebkitOverflowScrolling: 'touch' }}>
          <div style={{ width: (storeDataLen > 8) ? Math.max(storeDataLen * 30, 340) : '100%' }}>
            <Chart option={storeOption} height={170} />
          </div>
        </div>
        </div>
    </div>

    <div className="chart-row-3">
      <div className="card" style={{height:'auto',overflow:'visible'}}>
        <div className="section-title">{t("dash.low_stock")}{lowStockTotal > 0 ? ` (${lowStockTotal})` : ''}</div>
        {lowStockAlerts.length === 0
          ? <div className="small muted" style={{padding:12,textAlign:'center'}}>{t("dash.no_alerts")}</div>
          : lowStockAlerts.slice(0,5).map(x => (
              <div key={x.id} onClick={() => onAlert && onAlert(x.related_sku, x.warehouse_type, x.warehouse)} className="clickable" style={{padding:'8px 0',borderBottom:'1px solid var(--border)',fontSize:'var(--font-13)'}}>
                <div style={{display:'flex',justifyContent:'space-between',gap:8,alignItems:'flex-start'}}>
                  <span style={{fontWeight:600,fontSize:'var(--font-sm)',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',flex:1,minWidth:0}}>{x.title}</span>
                  <span className={'pill '+(x.severity==='error'?'danger':'warning')} style={{flexShrink:0}}>{x.severity==='warning'?'警告':t("dash.alert_overstock")}</span>
                </div>
                <div className="small muted" style={{fontSize:'var(--font-xs)',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',marginTop:2}}>{x.description}</div>
              </div>
            ))}
        {lowStockTotal > 5 && <button onClick={()=>{loadFullAlerts();setShowAllLowStock(true)}} aria-label={`还有 ${lowStockTotal - 5} 条`} className="clickable" style={{width:'100%',padding:'5px 0 2px',border:'none',borderRadius:0,background:'transparent',color:'var(--primary)',cursor:'pointer',fontFamily:'inherit',textAlign:'left',flexShrink:0,display:'flex',alignItems:'center'}}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="5" cy="12" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="19" cy="12" r="1.8"/></svg>
              </button>}
      </div>
      <div className="card" style={{height:'auto',overflow:'visible'}}>
        <div className="section-title">采购&补货告警{procTotal > 0 ? ` (${procTotal})` : ''}</div>
        {procLoading ? (
          <div className="small muted" style={{padding:12,textAlign:'center'}}>加载中...</div>
        ) : procTotal === 0 ? (
          <div className="small muted" style={{padding:12,textAlign:'center'}}>暂无采购/补货需求</div>
        ) : (
          procList.slice(0,5).map((x, i) => (
            <div key={x.tag + x.sku + i} onClick={() => onGoInsights && onGoInsights(x.tag === '采购' ? 'purchase' : 'replen', x.sku)} className="clickable" style={{padding:'8px 0',borderBottom:'1px solid var(--border)',fontSize:'var(--font-13)'}}>
              <div style={{display:'flex',justifyContent:'space-between',gap:8,alignItems:'flex-start'}}>
                <span style={{display:'inline-flex',alignItems:'center',gap:6,fontWeight:600,fontSize:'var(--font-sm)',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',flex:1,minWidth:0}}>
                  <span className={'pill ' + (x.tag === '采购' ? 'warning' : 'danger')} style={{flexShrink:0,fontSize:'var(--font-9)',padding:'1px 6px',minHeight:'auto',lineHeight:'16px'}}>{x.tag}</span>
                  {x.product_name || x.sku}
                </span>
                {x.qty > 0 && <span style={{flexShrink:0,fontWeight:700,fontSize:'var(--font-sm)',color:x.tag==='采购'?'var(--warning)':'var(--danger)'}}>+{x.qty}</span>}
              </div>
              <div className="small muted" style={{fontSize:'var(--font-10)',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',marginTop:2}}>{x.sku}{x.wh ? ' · ' + x.wh : ''} · {(x.note ? String(x.note).slice(0,36) : (x.days_to_empty > 999 ? '库存充足' : '可撑' + x.days_to_empty + '天'))}</div>
            </div>
          ))
        )}
        {procTotal > 5 && <button onClick={()=>{setShowAllProc(true)}} aria-label={`还有 ${procTotal - 5} 条`} className="clickable" style={{width:'100%',padding:'5px 0 2px',border:'none',borderRadius:0,background:'transparent',color:'var(--primary)',cursor:'pointer',fontFamily:'inherit',textAlign:'left',flexShrink:0,display:'flex',alignItems:'center'}}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="5" cy="12" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="19" cy="12" r="1.8"/></svg>
              </button>}
      </div>
    </div>
      {/* 低库存告警弹窗 */}
      {showAllLowStock && createPortal(
        <>
      <div onClick={function(){setShowAllLowStock(false)}} style={{position:'fixed',inset:0,zIndex:9998,background:'transparent'}} />
            <div style={{position:'fixed',left:0,right:0,bottom:'calc(env(safe-area-inset-bottom) + 14px)',zIndex:9999,display:'flex',justifyContent:'center',padding:'0 14px',pointerEvents:'none'}}>
        <div onClick={function(e){e.stopPropagation()}} className="material-regular" style={{width:"100%",maxWidth:600,borderRadius:'var(--radius-lg)',padding:"18px 14px calc(14px + env(safe-area-inset-bottom))",boxShadow:"var(--shadow-sheet), inset 0 1px 0 rgba(255,255,255,0.25)",pointerEvents:"auto",maxHeight:"70vh",overflowY:"auto"}}>
          <div style={{fontSize:'var(--font-18)',fontWeight:700,marginBottom:12,textAlign:'center',color:'var(--text)'}}>低库存告警 · 共 {lowStockTotal} 条</div>
          {(fullAlerts ? fullAlerts.filter(x => x.alert_type === 'low_stock' && (_replMode === 'bbcc' ? ['own','platform','platform_b'] : ['own','platform']).includes(x.warehouse_type)) : lowStockAlerts).map(function(x) {
            return <div key={x.id} onClick={function(){onAlert && onAlert(x.related_sku, x.warehouse_type, x.warehouse)}} className="clickable" style={{padding:'8px 12px',background:'var(--card)',borderRadius:'var(--radius-md)',marginBottom:6}}>
              <div style={{display:'flex',justifyContent:'space-between',gap:8,alignItems:'flex-start',marginBottom:2}}>
                <span style={{fontWeight:600,fontSize:'var(--font-sm)',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',flex:1,minWidth:0}}>{x.title}</span>
                <span style={{display:'inline-flex',gap:4,alignItems:'center',flexShrink:0}}>
                  {(x.warehouse ? fmtWh(x.warehouse) : _whTag(x.warehouse_type)) ? <span title={x.warehouse || ''} style={{fontSize:'var(--font-9)',padding:'1px 6px',borderRadius:'var(--radius-full)',background:'var(--bg)',color:'var(--muted)'}}>{(x.warehouse ? fmtWh(x.warehouse) : _whTag(x.warehouse_type))}</span> : null}
                  <span className={'pill '+(x.severity==='error'?'danger':'warning')} style={{fontSize:'var(--font-10)'}}>{x.severity==='warning'?'警告':'超储'}</span>
                </span>
              </div>
              <div className="small muted" style={{fontSize:'var(--font-xs)'}}>{x.description}<span style={{color:'var(--muted2)',fontSize:'var(--font-10)'}}>{alertAge(x.created_at)}</span></div>
            </div>
          })}
          <div onClick={function(){setShowAllLowStock(false)}} className="clickable sheet-close" style={{marginTop:8}}>
            <span style={{fontSize:'var(--font-15)',fontWeight:600,color:'#fff'}}>关闭</span>
          </div>
        </div>
      </div>
        </>,
        document.body
      )}

      {/* 采购&补货告警弹窗(重构: 补货建议需补 + 采购建议需采, 行标签区分, 模式跟随) */}
      {showAllProc && createPortal(
        <>
      <div onClick={function(){setShowAllProc(false)}} style={{position:'fixed',inset:0,zIndex:9998,background:'transparent'}} />
            <div style={{position:'fixed',left:0,right:0,bottom:'calc(env(safe-area-inset-bottom) + 14px)',zIndex:9999,display:'flex',justifyContent:'center',padding:'0 14px',pointerEvents:'none'}}>
        <div onClick={function(e){e.stopPropagation()}} className="material-regular" style={{width:"100%",maxWidth:600,borderRadius:'var(--radius-lg)',padding:"18px 14px calc(14px + env(safe-area-inset-bottom))",boxShadow:"var(--shadow-sheet), inset 0 1px 0 rgba(255,255,255,0.25)",pointerEvents:"auto",maxHeight:"70vh",overflowY:"auto"}}>
          <div style={{fontSize:'var(--font-18)',fontWeight:700,marginBottom:12,textAlign:'center',color:'var(--text)'}}>采购&补货告警 · 共 {procTotal} 条</div>
          {(procList || []).map(function(x, i) {
            return <div key={i} onClick={function(){onGoInsights && onGoInsights(x.tag === '采购' ? 'purchase' : 'replen', x.sku)}} className="clickable" style={{padding:'8px 12px',background:'var(--card)',borderRadius:'var(--radius-md)',marginBottom:6}}>
              <div style={{display:'flex',justifyContent:'space-between',gap:8,alignItems:'flex-start',marginBottom:2}}>
                <span style={{display:'inline-flex',alignItems:'center',gap:6,fontWeight:600,fontSize:'var(--font-sm)',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',flex:1,minWidth:0}}>
                  <span className={'pill ' + (x.tag === '采购' ? 'warning' : 'danger')} style={{flexShrink:0,fontSize:'var(--font-9)',padding:'1px 6px',minHeight:'auto',lineHeight:'16px'}}>{x.tag}</span>
                  {x.product_name || x.sku}
                </span>
                {x.qty > 0 && <span style={{flexShrink:0,fontWeight:700,color:x.tag==='采购'?'var(--warning)':'var(--danger)'}}>+{x.qty}</span>}
              </div>
              <div className="small muted" style={{fontSize:'var(--font-10)'}}>{x.sku}{x.wh ? ' · ' + x.wh : ''} · {(x.note ? String(x.note).slice(0,50) : (x.days_to_empty > 999 ? '库存充足' : '可撑' + x.days_to_empty + '天'))}</div>
            </div>
          })}
          <div onClick={function(){setShowAllProc(false)}} className="clickable sheet-close" style={{marginTop:8}}>
            <span style={{fontSize:'var(--font-15)',fontWeight:600,color:'#fff'}}>关闭</span>
          </div>
        </div>
      </div>
        </>,
        document.body
      )}

      {/* 其他告警弹窗(规则引擎非低库存类: 超卖/濒临断货/健康/滞销/自定义, 明细落点) */}
      {showAllOther && createPortal(
        <>
      <div onClick={function(){setShowAllOther(false)}} style={{position:'fixed',inset:0,zIndex:9998,background:'transparent'}} />
            <div style={{position:'fixed',left:0,right:0,bottom:'calc(env(safe-area-inset-bottom) + 14px)',zIndex:9999,display:'flex',justifyContent:'center',padding:'0 14px',pointerEvents:'none'}}>
        <div onClick={function(e){e.stopPropagation()}} className="material-regular" style={{width:"100%",maxWidth:600,borderRadius:'var(--radius-lg)',padding:"18px 14px calc(14px + env(safe-area-inset-bottom))",boxShadow:"var(--shadow-sheet), inset 0 1px 0 rgba(255,255,255,0.25)",pointerEvents:"auto",maxHeight:"70vh",overflowY:"auto"}}>
          <div style={{fontSize:'var(--font-18)',fontWeight:700,marginBottom:12,textAlign:'center',color:'var(--text)'}}>其他告警 · 共 {otherTotal} 条</div>
          {(fullAlerts ? fullAlerts.filter(x => !['low_stock','replenish','purchase_need'].includes(x.alert_type)) : []).map(function(x) {
            return <div key={x.id} onClick={function(){onAlert && onAlert(x.related_sku, x.warehouse_type, x.warehouse)}} className="clickable" style={{padding:'8px 12px',background:'var(--card)',borderRadius:'var(--radius-md)',marginBottom:6}}>
              <div style={{display:'flex',justifyContent:'space-between',gap:8,alignItems:'flex-start',marginBottom:2}}>
                <span style={{fontWeight:600,fontSize:'var(--font-sm)',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',flex:1,minWidth:0}}>{x.title}</span>
                <span style={{display:'inline-flex',gap:4,alignItems:'center',flexShrink:0}}>
                  {(x.warehouse ? fmtWh(x.warehouse) : _whTag(x.warehouse_type)) ? <span title={x.warehouse || ''} style={{fontSize:'var(--font-9)',padding:'1px 6px',borderRadius:'var(--radius-full)',background:'var(--bg)',color:'var(--muted)'}}>{(x.warehouse ? fmtWh(x.warehouse) : _whTag(x.warehouse_type))}</span> : null}
                  <span className={'pill ' + (x.severity === 'error' ? 'danger' : 'warning')} style={{fontSize:'var(--font-10)'}}>{x.severity === 'error' ? '紧急' : '警告'}</span>
                </span>
              </div>
              <div className="small muted" style={{fontSize:'var(--font-xs)'}}>{x.description}<span style={{color:'var(--muted2)',fontSize:'var(--font-10)'}}>{alertAge(x.created_at)}</span></div>
            </div>
          })}
          <div onClick={function(){setShowAllOther(false)}} className="clickable sheet-close" style={{marginTop:8}}>
            <span style={{fontSize:'var(--font-15)',fontWeight:600,color:'#fff'}}>关闭</span>
          </div>
        </div>
      </div>
        </>,
        document.body
      )}

      {/* 濒临断货完整列表弹窗 */}
      {showAllRisk && createPortal(
        <>
      <div onClick={function(){setShowAllRisk(false)}} style={{position:'fixed',inset:0,zIndex:9998,background:'transparent'}} />
            <div style={{position:'fixed',left:0,right:0,bottom:'calc(env(safe-area-inset-bottom) + 14px)',zIndex:9999,display:'flex',justifyContent:'center',padding:'0 14px',pointerEvents:'none'}}>
        <div onClick={function(e){e.stopPropagation()}} className="material-regular" style={{width:"100%",maxWidth:600,borderRadius:'var(--radius-lg)',padding:"18px 14px calc(14px + env(safe-area-inset-bottom))",boxShadow:"var(--shadow-sheet), inset 0 1px 0 rgba(255,255,255,0.25)",pointerEvents:"auto",maxHeight:"70vh",overflowY:"auto"}}>
          <div style={{fontSize:'var(--font-18)',fontWeight:700,marginBottom:12,textAlign:'center',color:'var(--text)'}}>濒临断货预警{_replMode === 'bbcc' ? '（BC）' : ''} · 共 {_r.total} 条</div>
          {(fullRisk && fullRisk.length ? fullRisk : (_r._full || _r.items || [])).map(function(x, i) {
            const whLabel = fmtWh(x.warehouse) || (x.type === 'C' ? 'C仓' : (x.type === 'OWN' ? '自有' : (x.type === 'B' ? 'B仓' : (_replMode === 'bbcc' ? 'BC' : 'C仓'))))
            const lv = RISK_LV[x.level]
            return <div key={i} onClick={function(){
              // bc 合计行(bbcc 专属): 跳 C 仓维度(platform) + 聚合高亮该 SKU 所有 C 仓行(不传具体仓)
              // —— 满足 bc 一盘棋语义: 看全国 C 仓分布, 而非单一仓
              const _isBC = x.type === 'BC' || x.warehouse === 'BC'
              onAlert && onAlert(x.sku, _isBC ? 'platform' : (_showOwn ? 'own' : 'platform'), _isBC ? '' : x.warehouse)
            }} className="clickable" style={{padding:'8px 12px',background:'var(--card)',borderRadius:'var(--radius-md)',marginBottom:6,display:'flex',justifyContent:'space-between',alignItems:'center',gap:8}}>
              <div style={{minWidth:0,flex:1}}>
                <div style={{display:'flex',alignItems:'center',gap:4,fontWeight:600,fontSize:'var(--font-sm)',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>
                  {lv ? <span className={'pill ' + (x.level === 'red' ? 'danger' : x.level === 'orange' ? 'warning' : 'info')} style={{flexShrink:0,fontSize:'var(--font-9)',padding:'1px 6px',minHeight:'auto',lineHeight:'16px'}}>{lv.t}</span> : null}
                  {x.product_name || x.sku}
                </div>
                <div className="small muted" style={{fontSize:'var(--font-10)'}}>日销 {x.daily_sales} · 可用 {x.available_qty}</div>
              </div>
              <span title={x.warehouse || ''} style={{fontSize:'var(--font-9)',padding:'1px 5px',borderRadius:4,background:'var(--bg)',color:'var(--muted)',flexShrink:0}}>{whLabel}</span>
              <span style={{fontSize:'var(--font-xs)',fontWeight:600,color:lv ? lv.c : 'var(--danger)',flexShrink:0,minWidth:38,textAlign:'right'}}>{x.days_to_empty} 天</span>
            </div>
          })}
          <div onClick={function(){setShowAllRisk(false)}} className="clickable sheet-close" style={{marginTop:8}}>
            <span style={{fontSize:'var(--font-15)',fontWeight:600,color:'#fff'}}>关闭</span>
          </div>
        </div>
      </div>
        </>,
        document.body
      )}

      {/* 缺货列表弹窗（按当前健康卡视图维度: own/平台行级, bc合计; 完整数据) */}
      {showAllOut && createPortal(
        <>
      <div onClick={function(){setShowAllOut(false)}} style={{position:'fixed',inset:0,zIndex:9998,background:'transparent'}} />
            <div style={{position:'fixed',left:0,right:0,bottom:'calc(env(safe-area-inset-bottom) + 14px)',zIndex:9999,display:'flex',justifyContent:'center',padding:'0 14px',pointerEvents:'none'}}>
        <div onClick={function(e){e.stopPropagation()}} className="material-regular" style={{width:"100%",maxWidth:600,borderRadius:'var(--radius-lg)',padding:"18px 14px calc(14px + env(safe-area-inset-bottom))",boxShadow:"var(--shadow-sheet), inset 0 1px 0 rgba(255,255,255,0.25)",pointerEvents:"auto",maxHeight:"70vh",overflowY:"auto"}}>
          <div style={{fontSize:'var(--font-18)',fontWeight:700,marginBottom:12,textAlign:'center',color:'var(--text)'}}>缺货 · 共 {_oosSrc.length} 条</div>
          {_oosSrc.map(function(x, i) {
            return <div key={i} className="clickable" style={{padding:'8px 12px',background:'var(--card)',borderRadius:'var(--radius-md)',marginBottom:6,display:'flex',justifyContent:'space-between',alignItems:'center',gap:8}}>
              <div style={{fontWeight:600,fontSize:'var(--font-sm)',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',minWidth:0}}>{i+1}. {x.product_name || x.sku}</div>
              <span title={x.warehouse || ''} style={{fontSize:'var(--font-10)',color:'var(--muted)',background:'var(--bg)',padding:'0 6px',borderRadius:'var(--radius-full)',flexShrink:0}}>{(x.warehouse ? fmtWh(x.warehouse) : (healthTab === 'bc' || x.warehouse_type === 'bc' ? 'BC' : (healthTab === 'own' ? '自有' : '平台')))}</span>
            </div>
          })}
          <div onClick={function(){setShowAllOut(false)}} className="clickable sheet-close" style={{marginTop:8}}>
            <span style={{fontSize:'var(--font-15)',fontWeight:600,color:'#fff'}}>关闭</span>
          </div>
        </div>
      </div>
        </>,
        document.body
      )}
</>
}
