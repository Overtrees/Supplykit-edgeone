import React, { useState, useMemo, useEffect, useRef } from 'react'
import RiskCard from '../components/dash/RiskCard'
import { GmvCard, AlertSummaryCard, HealthCard } from './dashboard/StatCards'
import { MidCharts, ChartRow3 } from './dashboard/ChartRows'
import { api, freshStamp } from '../api/client'
import type { ApiObject, InventoryRow, ListResponse, StockRiskShim } from '../api/types'
import { useAppStore } from '../store/useAppStore'
import ErrorRetry from '../components/ErrorRetry'
import { t } from '../locale'
import { orderStageLabel } from '../components/statusLabels'

import { fmtBig } from './dashboard/format'
import LowStockSheet from './dashboard/sheets/LowStockSheet'
import OwnHealthSheet from './dashboard/sheets/OwnHealthSheet'
import ProcSheet from './dashboard/sheets/ProcSheet'
import OtherAlertsSheet from './dashboard/sheets/OtherAlertsSheet'
import RiskSheet from './dashboard/sheets/RiskSheet'
import OosSheet from './dashboard/sheets/OosSheet'

/** /api/dashboard/summary 响应: 已知键精确声明, 动态扩展字段 unknown */
interface SummaryResponse {
  summary?: unknown
  [k: string]: unknown
}

/** alertCounts 的形状(与 store 内声明一致) */
interface AlertCountsData {
  by_type?: Record<string, number>
  by_severity?: Record<string, number>
  ls_warehouse?: Record<string, number>
  rp_warehouse?: Record<string, number>
}

/** /api/dashboard/aux 响应 */
interface AuxResponse {
  evaluating?: boolean
  alerts?: ApiObject[]
  stockOverview?: { items?: ApiObject[]; [k: string]: unknown }
  alertCounts?: AlertCountsData | null
  ownHealth?: unknown
  bcOutOfStock?: ApiObject[]
  [k: string]: unknown
}

/** /api/insights/replenishment|purchase 行 */
interface ProcRow {
  sku?: string
  product_name?: string
  suggested_qty?: number
  b_suggested?: number
  actual_purchase?: number
  note?: string
  days_to_empty?: number | null
  warehouse?: string
  [k: string]: unknown
}

/** /api/insights/replenishment|purchase 响应(数组或对象形态) */
interface ProcResponse {
  items?: ProcRow[]
  suggestions?: ProcRow[]
  [k: string]: unknown
}

interface DashboardPageProps {
  onAlert?: (sku: string, whType?: string, wh?: string) => void
  onGoInsights?: (tab: string, sku?: string) => void
}

export default function DashboardPage({ onAlert, onGoInsights }: DashboardPageProps) {
  const {
    dashboard,
    inventory,
    qualityLogs,
    alerts,
    stockRisk,
    alertCounts,
    ownHealth,
    bcOutOfStock,
    channel,
    hammerDashPeriod: periodTab,
    hammerReplenMode,
    pageVersion,
  } = useAppStore()
  const [healthTab, setHealthTab] = useState(() => {
    try {
      const h = localStorage.getItem('health_tab') || (channel === 'jd' ? 'own' : 'platform')
      return (channel !== 'jd' ||
        (hammerReplenMode || (channel === 'jd' ? 'bbcc' : 'traditional')) !== 'bbcc') &&
        h === 'platform_b'
        ? 'platform'
        : h
    } catch {
      return channel === 'jd' ? 'own' : 'platform'
    }
  })
  // 渠道切换归一化: platform_b(B 仓)为 jd BBCC 专属维度, other 渠道强制 platform(避免残留空维度显示)
  // 渠道/模式切换归一化: platform_b(B仓)为 jd+bbcc 专属, 其他组合强制 platform
  // (用 hammerReplenMode 而非 _replMode —— 本 useEffect 在 _replMode 声明前, 避免 TDZ)
  useEffect(() => {
    const _m =
      channel !== 'jd' && (hammerReplenMode || '') !== 'traditional'
        ? 'traditional'
        : hammerReplenMode || (channel === 'jd' ? 'bbcc' : 'traditional')
    if ((channel !== 'jd' || _m !== 'bbcc') && healthTab === 'platform_b')
      setHealthWithSave('platform')
  }, [channel, hammerReplenMode])
  const setHealthWithSave = tab => {
    try {
      localStorage.setItem('health_tab', tab)
    } catch {}
    setHealthTab(tab)
  }
  // GMV 视角切换: total=总GMV(含退款流水) / net=净GMV(剔除退款)——GMV小卡+店铺GMV卡共用
  const [gmvView, setGmvView] = useState('total')
  // 2026-10-16 GMV 矩阵: 回款按到账时间归属——仅月度/完整月可用(today/week/custom 弱化禁点)
  const gmvPayoutOk = periodTab === 'month'
  const [bcMenuOpen, setBcMenuOpen] = useState(false)
  const [showAllLowStock, setShowAllLowStock] = useState(false)
  const [showAllOwn, setShowAllOwn] = useState(false) // 自有仓健康全量弹窗(2026-11-01)
  // 采购&补货告警卡(重构): 补货建议需补(接口) + 采购建议需采(接口), 行标签区分, 跟随补货模式
  const [procList, setProcList] = useState([])
  const [procLoading, setProcLoading] = useState(true)
  // 2026-11-03 D5: 后端规则评估是否仍在跑(aux.evaluating) —— 在跑就提示并自动重拉,
  //   否则用户看到的是"评估前"的告警, 会误判规则没生效。
  const [evaluating, setEvaluating] = useState(false)
  const _evalPolls = useRef(0)
  const [showAllProc, setShowAllProc] = useState(false)
  const [showAllOther, setShowAllOther] = useState(false)
  const [showAllRisk, setShowAllRisk] = useState(false)
  const [riskFilter, setRiskFilter] = useState<'all' | 'red' | 'orange' | 'yellow'>('all')
  // 存词条键, 渲染时 t() 解析(级别值本身是判据, 用 code 不用文案)
  const riskTabs: Array<['all' | 'red' | 'orange' | 'yellow', string, string]> = [
    ['all', 'dash.all', 'var(--muted)'],
    ['red', 'dash.urgent', 'var(--danger)'],
    ['orange', 'dash.warning', 'var(--warning)'],
    ['yellow', 'dash.watch', 'var(--muted2)'],
  ]
  const [_riskTab] = useState('c') // 传统模式子视图: c=C仓 / own=自有三方仓
  const [_storeDim, setStoreDim] = useState('store') // 店铺GMV卡维度: store=店铺(盘子) / brand=品牌(渗透)
  const [showAllOut, setShowAllOut] = useState(false)
  const [fullOut, setFullOut] = useState<InventoryRow[] | null>(null) // 缺货弹窗完整数据(点击时按当前维度拉取)
  const [oosList, setOosList] = useState(null) // 当前维度缺货全量(随 healthTab 拉取, 预览+计数+弹窗同源)
  const [healthTrend, setHealthTrend] = useState([]) // 健康分数趋势(近14天, health-trend 接口)
  const [fullAlerts, setFullAlerts] = useState(null) // 告警弹窗完整数据(点击时拉取)
  const [fullRisk, setFullRisk] = useState<InventoryRow[] | null>(null) // 濒临断货完整列表
  // 2026-10-28 首帧骨架: 初始即按'有无旧值'决定(曾 false → 首帧渲染空卡, 用户看到无骨架闪一下)
  const [chLoading, setChLoading] = useState(() => !useAppStore.getState().dashboard)
  const [dashErr, setDashErr] = useState('')
  const dashFailRef = useRef(0) // 兜底重试失败计数: 连续 >=2 次后提供"刷新页面"强刷兜底(WebView/实例连接异常时重连恢复)
  // 弹窗数据加载(四维完整性): 告警用大 limit 分组配额拿全量; 濒临断货用 full=1; 缺货按维度拉全量
  const loadFullAlerts = async () => {
    try {
      const r = await api.get<ApiObject[]>('/api/alerts?channel=' + channel + '&limit=20000', {
        timeout: 60000,
      })
      const _d = r.data
      // 只在拿到**非空数组**时覆盖 —— 请求失败/返回空时保持 null, 让卡面回落 alertsList。
      // 曾 `catch → setFullAlerts([])`: 网络抖动一次, 卡面(fullAlerts 优先)立刻变空,
      // 表现为"明细忽然没了", 且不会自愈(直到下次点击「更多」)。
      if (Array.isArray(_d) && _d.length > 0) setFullAlerts(_d)
    } catch {
      // 静默: 保持 fullAlerts 原值(首次为 null → 卡面回落 alertsList)
    }
  }
  const loadFullRisk = async () => {
    try {
      const r = await api.get<StockRiskShim | InventoryRow[]>(
        '/api/dashboard/stock-risk?channel=' + channel + '&full=1',
        {
          timeout: 60000,
        },
      )
      const _m =
        channel !== 'jd' && (hammerReplenMode || '') !== 'traditional'
          ? 'traditional'
          : hammerReplenMode || (channel === 'jd' ? 'bbcc' : 'traditional')
      const _d = (r.data || {}) as StockRiskShim | InventoryRow[]
      // bbcc→BC合计(b仓+全国c); traditional→C仓+自有/三方仓(不涉B)——取数必须与模式维度一致
      const _lst = Array.isArray(_d)
        ? _d
        : _m === 'bbcc'
          ? _d.bcItems || []
          : (_d.cItems || []).concat(_d.ownItems || [])
      setFullRisk(_lst)
    } catch (e) {
      setFullRisk([])
    }
  }
  const loadFullOut = async () => {
    const _wh =
      healthTab === 'own'
        ? 'own'
        : healthTab === 'bc'
          ? 'bc'
          : healthTab === 'platform'
            ? 'platform'
            : 'platform_b'
    try {
      const r = await api.get<InventoryRow[] | ListResponse<InventoryRow>>(
        '/api/inventory/out-of-stock?channel=' + channel + '&wh=' + _wh + '&limit=5000',
        { timeout: 60000 },
      )
      const d = Array.isArray(r.data) ? r.data : (r.data && r.data.items) || []
      setFullOut(d)
    } catch (e) {
      setFullOut([])
    }
  }
  // 健康卡缺货列表: 随视图维度(own/平台/bc)拉取全量——与 healthData.out_of_stock 计数口径一致
  // (曾用 stockOverview.items(全渠道LIMIT100)过滤, 维度缺货SKU在窗口外时预览/计数漏显)
  useEffect(() => {
    const _wh =
      healthTab === 'own'
        ? 'own'
        : healthTab === 'bc'
          ? 'bc'
          : healthTab === 'platform'
            ? 'platform'
            : 'platform_b'
    api
      .get<InventoryRow[] | ListResponse<InventoryRow>>(
        '/api/inventory/out-of-stock?channel=' + channel + '&wh=' + _wh + '&limit=5000',
        {
          timeout: 60000,
        },
      )
      .then(r => {
        const d = Array.isArray(r.data) ? r.data : (r.data && r.data.items) || []
        setOosList(d)
      })
      .catch(() => setOosList([]))
    // 健康分数趋势(近14天, health-trend 接口; 快照由 summary/cron 每日记录)
    api
      .get<ApiObject[]>(
        '/api/dashboard/health-trend?channel=' + channel + '&days=14&t=' + freshStamp(),
        {
          timeout: 30000,
        },
      )
      .then(r => {
        setHealthTrend(Array.isArray(r.data) ? r.data : [])
      })
      .catch(() => setHealthTrend([]))
  }, [healthTab, channel])
  const reqSeq = useRef(0)
  // 补货模式(看板卡片/接口参数跟随): bbcc(仅 jd) / traditional(other 强制 + jd 可选)
  const _replMode =
    channel !== 'jd' && (hammerReplenMode || '') !== 'traditional'
      ? 'traditional'
      : hammerReplenMode || (channel === 'jd' ? 'bbcc' : 'traditional')
  // —— 采购&补货告警数据(与补货/采购建议页同源, 模式跟随; 补货=建议需补, 采购=建议需采) ——
  const loadProc = async () => {
    setProcLoading(true)
    try {
      const [r, p] = await Promise.allSettled([
        api.get<ProcResponse>(
          '/api/insights/replenishment?days=28&mode=' +
            _replMode +
            '&channel=' +
            channel +
            '&need_only=1',
          { timeout: 90000 },
        ),
        api.get<ProcResponse>(
          '/api/insights/purchase?days=28&mode=' +
            _replMode +
            '&channel=' +
            channel +
            '&need_only=1',
          { timeout: 90000 },
        ),
      ])
      const repData = (r.status === 'fulfilled' ? r.value.data : null) || {}
      const repItems = Array.isArray(repData) ? repData : repData.items || []
      const repNeed = (Array.isArray(repItems) ? repItems : [])
        .filter(x => (x.suggested_qty || 0) > 0 || (x.b_suggested || 0) > 0)
        .map(x => ({
          sku: x.sku,
          product_name: x.product_name,
          kind: 'replen',
          qty: (x.suggested_qty || 0) + (x.b_suggested || 0),
          note: x.note || '',
          days_to_empty: x.days_to_empty ?? 999, // 排序哨兵; null=无销量
          wh: x.warehouse || '',
        }))
      // 2026-10-17 allSettled: 采购/补货单接口失败不再拖累另一卡(曾 purchase 崩 → Promise.all reject → 双卡空)
      const purObj = (p.status === 'fulfilled' ? p.value.data : null) || {}
      const purItems = (purObj && purObj.suggestions) || []
      const purNeed = (Array.isArray(purItems) ? purItems : [])
        .filter(x => (x.actual_purchase || 0) > 0)
        .map(x => ({
          sku: x.sku,
          product_name: x.product_name,
          kind: 'purchase',
          qty: x.actual_purchase || 0,
          note: x.note || '',
          days_to_empty: x.days_to_empty ?? 999, // 排序哨兵; null=无销量
          wh: x.warehouse || '',
        }))
      // 采购在前(更紧急: 需向供应商采且可撑<14天), 随后补货按缺口
      const sorted = [...purNeed, ...repNeed].sort((a, b) => {
        if (a.kind !== b.kind) return a.kind === 'purchase' ? -1 : 1
        return a.days_to_empty - b.days_to_empty
      })
      setProcList(sorted)
    } catch (e) {
      setProcList([])
    }
    setProcLoading(false)
  }
  useEffect(() => {
    loadProc()
  }, [channel, _replMode])
  // 2026-11-01 根治「低库存卡明细丢失」: 进入看板即预拉全量告警(limit=20000),
  //   卡面在此之前吃的是 alertsList —— 那是 `/api/alerts` 每组 200 条的**首屏截断快照**,
  //   与后端独立 COUNT 的 lowStockTotal 天然对不上(实测 160 vs 200), 且 id DESC 的前 N 条
  //   可能被同仓同 SKU 占满 → 卡上明细缺失/重复。预拉后卡面与弹窗同源同口径, 不再依赖用户点「更多」。
  const fullAlertsLoaded = useRef(false)
  useEffect(() => {
    if (fullAlertsLoaded.current) return
    fullAlertsLoaded.current = true
    loadFullAlerts()
  }, [channel])
  // 规则/参数保存、任务完成 → 重拉(与 insights-refresh/rules-changed 联动)
  useEffect(() => {
    const h = () => {
      loadProc()
    }
    window.addEventListener('rules-changed', h)
    window.addEventListener('insights-refresh', h)
    return () => {
      window.removeEventListener('rules-changed', h)
      window.removeEventListener('insights-refresh', h)
    }
  }, [channel, _replMode])
  const procTotal = procList.length
  // 其他告警(规则引擎非低库存类: 超卖/濒临断货/健康/滞销/自定义) —— 可点开明细, 不再只有计数
  // 2026-11-01 排除自有仓三条: 它们有专属卡「自有仓健康」承载, 若再进"其他告警"
  //   会双重计数(pendingTotal 里同一批货算两次), 且语义不同(供给侧 vs 销售侧)。
  const _OWN_TYPES = ['own_transfer_gap', 'own_overstock', 'own_expiry_risk']
  const byType: Record<string, number> = (alertCounts && alertCounts.by_type) || {}
  const otherTotal = Object.entries(byType)
    .filter(([k]) => !['low_stock', 'replenish', 'purchase_need'].concat(_OWN_TYPES).includes(k))
    .reduce((sum, [, v]) => sum + (v || 0), 0)
  useEffect(() => {
    const seq = ++reqSeq.current
    // 无感刷新: 仅当无 dashboard 数据(首次/清空后)才骨架, 有旧数据则不骨架(先显示旧值, 后台拉新替换)
    setChLoading(!useAppStore.getState().dashboard)
    const load = () => {
      useAppStore.getState().setSyncing(1) // 2026-10-27 看板拉新(进入/切回/事件/重试)计入徽标
      useAppStore.getState().setSyncOld(Boolean(useAppStore.getState().dashboard)) // 有旧值→同步中/无→加载中
      // 首屏拆流: summary+aux 先渲染(不阻塞骨架屏), stock-risk 后置到达后更新断货卡
      // (stock-risk 含 OTIF/逐仓日销/动态SS/加速判定较重, 首屏先看 GMV/告警/健康, 断货 1-2s 补齐)
      Promise.allSettled([
        (() => {
          const _st = useAppStore.getState()
          let _u = '/api/dashboard/summary?t=' + freshStamp()
          if (_st.hammerDashPeriod === 'custom' && _st.customDateStart && _st.customDateEnd)
            _u += '&start_date=' + _st.customDateStart + '&end_date=' + _st.customDateEnd
          return api.get<SummaryResponse>(_u, { timeout: 60000 })
        })(),
        api.get<AuxResponse>(
          '/api/dashboard/aux?channel=' + channel + '&mode=' + _replMode + '&t=' + freshStamp(),
          { timeout: 60000 },
        ),
      ])
        .then(([s, ax]) => {
          if (seq !== reqSeq.current) {
            setChLoading(false)
            return
          } // 竞态丢弃
          // 兜底: summary 必须 fulfilled 且 data.summary 存在才算成功(seed填充/表重建期间
          // 可能返回异常结构 → dash=null 且无ErrorRetry → 看板空白缺口)
          const dashOk = s.status === 'fulfilled' && s.value.data && s.value.data.summary
          const dash = dashOk ? s.value.data : null
          setDashErr(s.status === 'rejected' || !dashOk ? t('dash.load_fail_processing') : '')
          const aux = (ax && ax.status === 'fulfilled' ? ax.value.data || {} : {}) as AuxResponse
          setEvaluating(Boolean(aux.evaluating))
          const alerts = aux.alerts || []
          // stockRisk 双形态(数组|对象) → 归一化后统一消费; 对象形态缺省键消费前补齐
          const stockRisk: unknown[] | StockRiskShim =
            useAppStore.getState().stockRisk || ({} as StockRiskShim)
          const ov = aux.stockOverview || {}
          useAppStore.setState({
            dashboard: dash,
            alerts,
            stockRisk,
            alertCounts: aux.alertCounts || useAppStore.getState().alertCounts || null, // 2026-10-30 抖动修复: 失败时保留上次计数
            // 自有仓健康(2026-11-01): aux 新增块; 同样抖动兜底保留上次值
            ownHealth: aux.ownHealth || useAppStore.getState().ownHealth || null,
            bcOutOfStock: aux.bcOutOfStock || [],
            inventory: ov.items || [],
            _stockOverview: ov,
            loading: false,
            dataLoaded: true,
          })
          setChLoading(false)
          // 首次加载关键数据为空时自动重试（进程重启后缓存未就绪/慢接口超时兜底），最多 3 次
          // B 维度(items)已移除(bbcc 用 bcItems / traditional 用 cItems+ownItems) → 空检查按各维度数组
          const _srEmpty = Array.isArray(stockRisk)
            ? stockRisk.length === 0
            : !(
                stockRisk &&
                ((stockRisk.bcItems || []).length ||
                  (stockRisk.cItems || []).length ||
                  (stockRisk.ownItems || []).length)
              )
          if ((!dash || _srEmpty) && seq === reqSeq.current) {
            let retries = 0
            const timer = setInterval(() => {
              retries += 1
              if (retries > 3 || seq !== reqSeq.current) {
                clearInterval(timer)
                return
              }
              // 2026-11-02 重试必须绕开缓存: 走到这里说明上一轮拿到的不是可用数据(异常结构),
              //   而那次 200 响应已经写进 30s 缓存 —— 若沿用 freshStamp() 的时间窗 key,
              //   3s/6s/9s 三次重试会全部命中同一份坏数据, 重试等于没做。此处用真实时间戳。
              const _rt = 't=' + Date.now()
              Promise.allSettled([
                (() => {
                  const _st = useAppStore.getState()
                  let _u = '/api/dashboard/summary?' + _rt
                  if (_st.hammerDashPeriod === 'custom' && _st.customDateStart && _st.customDateEnd)
                    _u += '&start_date=' + _st.customDateStart + '&end_date=' + _st.customDateEnd
                  return api.get<SummaryResponse>(_u)
                })(),
                api.get<StockRiskShim | InventoryRow[]>('/api/dashboard/stock-risk?' + _rt),
              ]).then(([s2, r2]) => {
                if (seq !== reqSeq.current) {
                  clearInterval(timer)
                  return
                }
                const d2 = s2.status === 'fulfilled' ? s2.value.data : null
                const rv2 = r2.status === 'fulfilled' ? r2.value.data || [] : []
                useAppStore.setState({
                  dashboard: d2 || useAppStore.getState().dashboard,
                  stockRisk: (
                    Array.isArray(rv2)
                      ? rv2.length
                      : rv2 &&
                        ((rv2.bcItems || []).length ||
                          (rv2.cItems || []).length ||
                          (rv2.ownItems || []).length)
                  )
                    ? rv2
                    : useAppStore.getState().stockRisk,
                })
                if (d2 && (rv2 as InventoryRow[]).length) clearInterval(timer)
              })
            }, 3000)
          }
        })
        .catch(() => setChLoading(false))
        .finally(() => useAppStore.getState().setSyncing(-1))
      // stock-risk 后置(独立 30s TTL 接口, 不阻塞首屏骨架; 到达后更新断货卡)
      api
        .get<StockRiskShim | InventoryRow[]>(
          '/api/dashboard/stock-risk?channel=' + channel + '&t=' + freshStamp(),
          {
            timeout: 60000,
          },
        )
        .then(r => {
          if (seq === reqSeq.current && r.data) useAppStore.setState({ stockRisk: r.data })
        })
        .catch(() => {})
        .finally(() => useAppStore.getState().setSyncing(-1))
    }
    load()
  }, [channel, pageVersion])
  // 30s 静默自动刷新（不显示 loading 骨架屏，避免闪烁）
  // 规则保存/删除/批量启用停用完成 → 'rules-changed' 事件 → 立即重拉看板。
  // 覆盖场景: 操作进行中先回到看板(拿到旧值), 操作完成后事件触发即时刷新——无需手动 F5。
  // 复用 pageVersion 路径(与 navigateTo bumpPageVersion 同一个 useEffect)，改动最小。
  useEffect(() => {
    const h = () => {
      useAppStore.getState().bumpPageVersion()
    }
    window.addEventListener('rules-changed', h)
    return () => window.removeEventListener('rules-changed', h)
  }, [])
  // 兜底: 从后台回到前台/focus 时重拉一次——覆盖 iOS 后台 JS 挂起导致事件延迟执行、
  // 以及非事件源的数据变更(其他会话/API 级修改)等一切时序; 保证回来看板必是最新
  useEffect(() => {
    const onVis = () => {
      if (document.visibilityState === 'visible') useAppStore.getState().bumpPageVersion()
    }
    document.addEventListener('visibilitychange', onVis)
    window.addEventListener('focus', onVis)
    return () => {
      document.removeEventListener('visibilitychange', onVis)
      window.removeEventListener('focus', onVis)
    }
  }, [])
  const silentBusy = useRef(false)
  useEffect(() => {
    const timer = setInterval(async () => {
      if (silentBusy.current) return
      silentBusy.current = true
      useAppStore.getState().setSyncing(1) // 30s 静默刷新接入徽标提示
      useAppStore.getState().setSyncOld(true) // 静默刷新必然有旧值→同步中
      try {
        const _t = 't=' + freshStamp()
        const [s, ax, sr] = await Promise.all([
          (() => {
            const _st = useAppStore.getState()
            let _u = '/api/dashboard/summary?' + _t
            if (_st.hammerDashPeriod === 'custom' && _st.customDateStart && _st.customDateEnd)
              _u += '&start_date=' + _st.customDateStart + '&end_date=' + _st.customDateEnd
            return api.get<SummaryResponse>(_u, { timeout: 60000 })
          })(),
          api.get<AuxResponse>(
            '/api/dashboard/aux?channel=' + channel + '&mode=' + _replMode + '&' + _t,
            {
              timeout: 60000,
            },
          ),
          api.get<StockRiskShim | InventoryRow[]>(
            '/api/dashboard/stock-risk?channel=' + channel + '&' + _t,
            { timeout: 60000 },
          ),
        ])
        const aux = (ax.data || {}) as AuxResponse
        setEvaluating(Boolean(aux.evaluating))
        useAppStore.setState({
          dashboard: s.data,
          // 2026-10-30 抖动修复: aux 偶发失败时不再把 alerts 置空(数字取自 alertCounts 保留值,
          //   列表却被清空 → 卡片「有数字无明细」); 只在 aux 成功返回时覆盖
          alerts: aux.alerts || useAppStore.getState().alerts || [],
          stockRisk: ((sr && sr.data) || useAppStore.getState().stockRisk || {}) as
            StockRiskShim | unknown[],
          alertCounts: aux.alertCounts || useAppStore.getState().alertCounts || null, // 2026-10-30 抖动修复: 失败时保留上次计数
          // 自有仓健康(2026-11-01): 供给侧口径块(调拨缺口/积压/效期), 抖动时保留上次值
          ownHealth: aux.ownHealth || useAppStore.getState().ownHealth || null,
          bcOutOfStock: aux.bcOutOfStock || [],
          inventory: (aux.stockOverview || {}).items || [],
          loading: false,
          dataLoaded: true,
        })
      } catch {
      } finally {
        silentBusy.current = false
        useAppStore.getState().setSyncing(-1) // 静默刷新完成/失败均消失提示
      }
    }, 30000)
    return () => clearInterval(timer)
  }, [channel])
  // 2026-11-03 D5: 规则重算中 → 每 4s 重拉(最多 6 次), 评估完成后告警自动出现。
  //   没有这个, 用户保存规则后看到的是"评估前"的告警, 会重复保存(越改越乱)。
  useEffect(() => {
    if (!evaluating) {
      _evalPolls.current = 0
      return
    }
    if (_evalPolls.current >= 6) return
    const _tid = setTimeout(() => {
      _evalPolls.current += 1
      window.dispatchEvent(new Event('rules-changed'))
    }, 4000)
    return () => clearTimeout(_tid)
  }, [evaluating])
  const periodTrend = dashboard?.periods?.[periodTab + '_trend'] || dashboard?.trend || []
  // GMV 卡维度字段映射(总/净/回款 → 趋势字段名+显示名)
  const gmvKey = gmvView === 'net' ? 'net_gmv' : gmvView === 'payout' ? 'payout' : 'GMV'
  const gmvName =
    gmvView === 'net' ? t('dash.net_gmv') : gmvView === 'payout' ? t('dash.payout') : 'GMV'
  // 维度色(切换感知: 总=蓝/净=绿/回款=紫, 现有微趋势条/折线改色)
  const gmvColor =
    gmvView === 'net' ? 'var(--success)' : gmvView === 'payout' ? '#8b5cf6' : 'var(--primary)'
  const periodMeta = dashboard?.periods?.[periodTab] || {}
  // 店铺/品牌 GMV 数据量(横向滚动+自动采样判定, 渲染层可用)
  const storeDataLen =
    (_storeDim === 'brand'
      ? (dashboard?.period_brands?.[periodTab] || dashboard?.brands || []).length
      : (dashboard?.period_stores?.[periodTab] || dashboard?.stores || []).length) || 0

  const periodTrendOption = useMemo(
    () => ({
      tooltip: {
        trigger: 'axis',
        valueFormatter: v =>
          '¥' +
          Number(v).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }),
        extraCssText: 'z-index:1000',
        hideDelay: 100,
      },
      xAxis: {
        type: 'category',
        data: periodTrend.map(i => i['日期']) || [],
        axisLabel: { fontSize: 9 },
      },
      yAxis: [
        {
          type: 'value',
          axisLabel: {
            fontSize: 9,
            formatter: v => fmtBig(v), // 2026-10-27 统一降维(曾自定义 W 缩写, 无亿档/小数)
          },
          max: v => Math.ceil((v.max * 1.2) / 1000) * 1000,
        },
        { type: 'value', axisLabel: { fontSize: 9 } },
      ],
      grid: { containLabel: true, top: 8, bottom: 42 },
      series: [
        {
          type: 'line',
          smooth: true,
          areaStyle: { opacity: 0.15 },
          data: periodTrend.map(i => i[gmvKey]) || [],
          color: gmvColor,
          name: gmvName,
        },
        {
          type: 'bar',
          data: periodTrend.map(i => i['订单数']) || [],
          color: '#0f766e',
          yAxisIndex: 1,
          name: t('dash.order_count'),
        },
      ],
      legend: {
        data: [gmvName, t('dash.order_count')],
        bottom: 6,
        left: 'center',
        icon: 'circle',
        itemWidth: 8,
        itemHeight: 8,
        textStyle: { fontSize: 9 },
      },
    }),
    [periodTrend, gmvView],
  )

  const storeOption = useMemo(() => {
    // 店铺看盘子 / 品牌看渗透(跨店按brand归集, join products.brand)
    const storeData =
      _storeDim === 'brand'
        ? dashboard?.period_brands?.[periodTab] || dashboard?.brands || []
        : dashboard?.period_stores?.[periodTab] || dashboard?.stores || []
    // GMV 视角切换: 净GMV=总GMV-退款(后端 stores/brands 已带 net_gmv)
    // 2026-10-16 去推算兜底: 回款维度无真实流水(payout 缺失)时不 fallback gmv——有就有没有就没有
    const _g = i => {
      if (gmvView === 'net')
        return i.net_gmv != null ? i.net_gmv : Number(i.gmv || 0) - Number(i.refund_amount || 0)
      if (gmvView === 'payout') return i.payout != null ? i.payout : null
      return i.gmv != null ? i.gmv : 0
    }
    return {
      tooltip: {
        trigger: 'axis',
        valueFormatter: v =>
          '¥' +
          Number(v).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }),
        extraCssText: 'z-index:1000',
        hideDelay: 100,
      },
      // 底部店铺/品牌名: 全量显示不截断; 长名截断+换行; 数量多(品牌35+)时旋转避免重叠
      xAxis: {
        type: 'category',
        data: storeData.map(i => i.name) || [],
        // 品牌维度标签太多(35+)重叠→隐藏, 用悬浮/点击 tooltip 显示名称; 店铺维度保留(数量少, 截断+旋转)
        axisLabel:
          storeData.length > 8 || _storeDim === 'brand'
            ? {
                show: true,
                interval: 'auto',
                fontSize: 8,
                margin: 4,
                formatter: v => {
                  const _lb = String(v || '')
                  return _lb.length > 5 ? _lb.slice(0, 5) + '…' : _lb
                },
              }
            : {
                fontSize: 8,
                interval: 0,
                rotate: storeData.length > 8 ? 35 : 0,
                margin: 6,
                formatter: v => {
                  const _lb = String(v || '')
                  if (_lb.length > 6) return _lb.slice(0, 6) + '…'
                  return _lb
                },
                width: 64,
                overflow: 'truncate',
              },
      },
      yAxis: {
        type: 'value',
        axisLabel: {
          fontSize: 8,
          // 2026-10-27 轴标签金额降维(≥10万 万/亿)——曾 toLocaleString 全量百万位挤爆; tooltip 悬浮保留完整值
          formatter: v => fmtBig(v),
        },
        splitNumber: 4,
        max: v => Math.ceil((v.max * 1.15) / 1000) * 1000,
      },
      series: [
        {
          type: 'bar',
          barMaxWidth: 26,
          data:
            storeData.map((i, idx) => ({
              value: Math.round(_g(i) * 100) / 100,
              itemStyle: {
                color: ['#f59e0b', '#06b6d4', '#8b5cf6', '#ec4899', '#10b981', '#f97316'][idx % 6],
              },
            })) || [],
        },
      ],
      grid: {
        containLabel: true,
        top: 8,
        bottom: storeData.length > 8 || _storeDim === 'brand' ? 30 : 30,
        left: 8,
        right: 12,
      },
    }
  }, [dashboard, periodTab, gmvView, _storeDim])

  const barOption = useMemo(() => {
    const f = dashboard?.period_funnel?.[periodTab] || dashboard?.funnel || []
    const names = f.map(x => orderStageLabel(x.name))
    const values = f.map(x => x.value)
    const ftotal = (f[0] && f[0].value) || 1
    return {
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        extraCssText: 'z-index:1000',
        hideDelay: 100,
        formatter: p => {
          const idx = p[0]?.dataIndex ?? 0
          const item = f[idx]
          return [
            orderStageLabel(item.name),
            t('dash.funnel_tip_fmt', { n: item.value }),
            t('dash.funnel_rate', { p: item.percentage }),
            t('dash.funnel_conv', { c: item.conversion }),
          ].join('<br/>')
        },
      },
      grid: { containLabel: true, top: 4, bottom: 6, right: 58 },
      xAxis: { type: 'value', show: false, max: ftotal * 1.15 },
      yAxis: { type: 'category', data: names, axisLabel: { fontSize: 10 } },
      series: [
        {
          type: 'bar',
          data: values.map((v, i) => ({
            value: v,
            itemStyle: { color: ['#f59e0b', '#06b6d4', '#8b5cf6', '#ec4899', '#10b981'][i % 5] },
          })),
          barWidth: '60%',
          label: {
            show: true,
            position: 'right',
            fontSize: 10,
            formatter: p =>
              p.value >= 10000
                ? t('dash.unit_wan_orders', { n: (p.value / 10000).toFixed(1) })
                : t('dash.unit_orders', { n: p.value }),
            textBorderColor: 'transparent',
          },
        },
      ],
    }
  }, [dashboard, periodTab])

  const lowStock = (inventory || []).filter(
    x => Number(x.available_qty) < Number(x.safety_qty),
  ).length
  const errCount = (qualityLogs || []).length
  // 2026-11-02 S0: 这两层派生原先每次渲染都重算(且返回新引用, 让下游 useMemo 全部失效)。
  const alertsList = useMemo(
    () => (Array.isArray(alerts) ? alerts.filter(x => x.status === 'active') : []),
    [alerts],
  )
  // 2026-11-01 根治: 卡面数据源优先级 —— fullAlerts(点击时拉的全量) > alertsList(首屏截断 200/组)
  //   截断列表只是"首屏快照", 与后端独立 COUNT 的 lowStockTotal 天然对不上(实测 160 vs 200 条);
  //   卡面此前直接吃截断列表, 一旦 id DESC 的前 N 条被同类其它仓占满, 卡上明细就会缺失或重复。
  //   现在: ① 进入页面自动拉一次全量(不用等用户点「更多」) ② 卡面与弹窗同源同口径。
  // 2026-11-02 补: 还要跟随补货模式的**仓型口径** ——
  //   bbcc → B+C 合计行(bc); traditional → C 仓逐仓(platform); own 两种都看。
  //   此前只按 alert_type 过滤, bbcc 下卡面拿到的是 /api/alerts 全量里 id 更新的 platform 行,
  //   显示成"郑州仓"等单仓数据 —— 跟标题(160 = bc 计数)对不上, 也违背"BC 合计才有意义"的业务口径。
  //   筛完为空时回落不过滤(历史数据仓型不规范时不能把卡面变空)。
  const lowStockAlerts = useMemo(() => {
    const types = _replMode === 'bbcc' ? ['own', 'bc'] : ['own', 'platform']
    const raw = fullAlerts
      ? fullAlerts.filter(x => x.alert_type === 'low_stock')
      : alertsList.filter(x => x.alert_type === 'low_stock')
    const byMode = raw.filter(x => types.includes(String(x.warehouse_type || '')))
    return byMode.length ? byMode : raw
  }, [fullAlerts, alertsList, _replMode])
  // 卡面 5 行取数(2026-11-02 改「按仓轮转 + 轮内 SKU 去重」):
  //   旧法问题: 接口 id DESC → 最新告警集中在同一仓, 5 行全是郑州仓。
  //   但**只按仓轮转还不够**: 规则引擎对各仓是同一批 SKU 顺序评估的, 每个仓的"第一条"
  //   往往是同一个 SKU(线上实测 7 个仓 top1 都是 SKU-0987-J) → 卡面会变成"同一 SKU × 5 个仓",
  //   仍然像重复行。所以每轮取数时**优先挑本仓还没出现过的 SKU**, 只有该仓确实没有新 SKU 才允许重复。
  //   注意**不能**改成"每仓最多 1 条"的硬配额: 只有 3 个仓时卡面就只剩 3 行, 白白空掉。
  //   现方案: ① 去重键「SKU × 仓」防重复告警; ② 按仓分桶(桶内 id DESC);
  //          ③ 逐轮、逐仓取 1 条, 轮内跳过已上卡面的 SKU。3 仓 = 3+2 共 5 行; ≥5 仓 = 每仓 1 行。
  // 2026-11-02 S0: 原来是渲染期 IIFE —— 每次 setState/父组件重渲染都重跑一遍整套分桶+轮转
  // (线上 1150 条 low_stock 全量参与)。改为 useMemo, 只在数据源变化时重算。
  const lowStockShown = useMemo(() => {
    const seen = new Set<string>()
    const buckets: Record<string, typeof lowStockAlerts> = {}
    const order: string[] = []
    for (const x of lowStockAlerts) {
      const k = `${x.related_sku || x.id}|${x.warehouse || ''}`
      if (seen.has(k)) continue
      seen.add(k)
      const w = x.warehouse || '-'
      if (!buckets[w]) {
        buckets[w] = []
        order.push(w)
      }
      buckets[w].push(x)
    }
    const out: typeof lowStockAlerts = []
    const usedSku = new Set<string>()
    const ptr: Record<string, number> = {}
    for (let round = 0; out.length < 5 && round < 500; round++) {
      let added = false
      for (const w of order) {
        if (out.length >= 5) break
        const arr = buckets[w]
        const start = ptr[w] || 0
        if (start >= arr.length) continue
        let idx = start
        const skuOf = (r: (typeof arr)[number]) => String(r.related_sku || r.id)
        while (idx < arr.length && usedSku.has(skuOf(arr[idx]))) idx++
        // 本仓找不到新 SKU 时退回本轮首条(同 SKU 多仓缺货仍是有效信息, 不该留空)
        const pick = idx < arr.length ? idx : start
        const row = arr[pick]
        ptr[w] = pick + 1
        out.push(row)
        usedSku.add(skuOf(row))
        added = true
      }
      if (!added) break
    }
    return out
  }, [lowStockAlerts])
  // 弹窗与卡面同源(2026-11-02): lowStockAlerts 已按补货模式筛过仓型, 直接用即可 ——
  //   早前这里是独立再筛一遍, 结果卡面(未筛)与弹窗口径不一致; 现在两处都从 lowStockAlerts 出。
  const lowStockModalRows = lowStockAlerts
  // 看板「(N 严重)」等计数一律取后端 alertCounts(独立 COUNT)，不得从截断列表 filter 得出——
  // 列表每组各取 200 条，总数可能远大于此，filter 计数会系统性漏报
  const _acSev: Record<string, number> = (alertCounts && alertCounts.by_severity) || {}
  const criticalAlerts =
    _acSev.error != null ? _acSev.error : alertsList.filter(x => x.severity === 'error').length
  // 拆分类: 低库存(纯low_stock)与滞销(slow_moving)独立计数(曾合并为non_replenish导致"低库存N"含滞销误导)
  const _acByType2: Record<string, number> = (alertCounts && alertCounts.by_type) || {}
  const lowStockTotal =
    _acByType2.low_stock != null
      ? _acByType2.low_stock
      : lowStockAlerts.filter(x => x.alert_type === 'low_stock').length
  const slowMovingTotal =
    _acByType2.slow_moving != null
      ? _acByType2.slow_moving
      : lowStockAlerts.filter(x => x.alert_type === 'slow_moving').length
  // 2026-10-27 总览聚合: 待处理卡大数字 = 全部待处理动作(低库存/采购/补货/其他/滞销)——
  // 分卡=明细; 采购/补货条数来自实时接口 procList(同"采购&补货"卡源, tag 区分)
  const procBuyN = (Array.isArray(procList) ? procList : []).filter(
    x => x.kind === 'purchase',
  ).length
  const procRepN = (Array.isArray(procList) ? procList : []).filter(x => x.kind === 'replen').length
  const pendingTotal = lowStockTotal + procBuyN + procRepN + otherTotal + slowMovingTotal
  // 2026-10-16 自然口径: 后端 periods.days 已是实际窗口天数(上周=7/上月=自然月实际天数 28-31)
  // —— 曾写死 {month:30} 致自然月 31 天时日均算错; 优先取后端, 兜底按窗口类型
  const periodDays = periodMeta?.days || { today: 1, week: 7, month: 30 }[periodTab] || 30
  // 濒临断货: 兼容旧数组/新{items,total,critical,warning}结构——卡上大数字/紧急警告用全量计数(完整性)
  // 补货模式联动: bbcc→BC 合计维度(对齐库存卡 bc tab: B+C 按 SKU 合计); traditional→C 仓维度
  // stockRisk 归一化兼容层: 后端可能返回 数组(旧, 仅 items/total) / 对象(新, bc/c/own 维度)
  // 数组形态只有 items/total, 对象形态含三级分类字段 → 用统一接口描述, 缺省键消费前补齐
  const _sr0: StockRiskShim = Array.isArray(stockRisk)
    ? { items: stockRisk as InventoryRow[], total: stockRisk.length }
    : stockRisk || {
        items: [],
        total: 0,
        critical: 0,
        warning: 0,
        bcItems: [],
        bcTotal: 0,
        bcCritical: 0,
        bcWarning: 0,
        cItems: [],
        cTotal: 0,
        cCritical: 0,
        cWarning: 0,
        ownItems: [],
        ownTotal: 0,
        ownCritical: 0,
        ownWarning: 0,
      }
  if (_replMode === 'bbcc') {
    _sr0.items = _sr0.bcItems || []
    _sr0.total = _sr0.bcTotal != null ? _sr0.bcTotal : _sr0.items.length
    _sr0.critical =
      _sr0.bcCritical != null ? _sr0.bcCritical : _sr0.items.filter(x => x.days_to_empty < 3).length
    _sr0.warning =
      _sr0.bcWarning != null
        ? _sr0.bcWarning
        : _sr0.items.filter(x => x.days_to_empty >= 3 && x.days_to_empty < 7).length
  } else {
    // 传统多仓: C 仓维度(主) + 自有/三方仓维度(own, 由采购管控)
    _sr0.items = _sr0.cItems || []
    _sr0.total = _sr0.cTotal != null ? _sr0.cTotal : _sr0.items.length
    _sr0.critical =
      _sr0.cCritical != null ? _sr0.cCritical : _sr0.items.filter(x => x.days_to_empty < 3).length
    _sr0.warning =
      _sr0.cWarning != null
        ? _sr0.cWarning
        : _sr0.items.filter(x => x.days_to_empty >= 3 && x.days_to_empty < 7).length
    _sr0.ownItems = _sr0.ownItems || []
    _sr0.ownTotal = _sr0.ownTotal != null ? _sr0.ownTotal : _sr0.ownItems.length
    _sr0.ownCritical =
      _sr0.ownCritical != null
        ? _sr0.ownCritical
        : _sr0.ownItems.filter(x => x.days_to_empty < 3).length
    _sr0.ownWarning =
      _sr0.ownWarning != null
        ? _sr0.ownWarning
        : _sr0.ownItems.filter(x => x.days_to_empty >= 3 && x.days_to_empty < 7).length
  }
  const _sr = _sr0
  // 传统多仓: C 仓 + 自有/三方仓 混合显示(同一列表, 标签区分)——曾用 C仓/自有三方 tab 分开
  const _srOwn = {
    items: _sr0.ownItems || [],
    total: _sr0.ownTotal || 0,
    critical: _sr0.ownCritical || 0,
    warning: _sr0.ownWarning || 0,
  }
  const _showOwn = false
  const _r =
    _replMode === 'traditional'
      ? (function () {
          const _merged = [].concat(_sr.items || [], _srOwn.items || []).sort(function (a, b) {
            return (a.days_to_empty || 999) - (b.days_to_empty || 999)
          })
          const _tot = (_sr.total || 0) + (_srOwn.total || 0)
          const _c = (_sr.critical || 0) + (_srOwn.critical || 0)
          const _w = (_sr.warning || 0) + (_srOwn.warning || 0)
          const _full = [].concat(_sr.items || [], _srOwn.items || []).sort(function (a, b) {
            return (a.days_to_empty || 999) - (b.days_to_empty || 999)
          })
          return {
            items: _merged.slice(0, 10),
            total: _tot,
            critical: _c,
            warning: _w,
            _full: _full,
          }
        })()
      : _sr
  const riskCritical =
    _r.critical != null ? _r.critical : (_r.items || []).filter(x => x.days_to_empty < 3).length
  const riskWarning =
    _r.warning != null
      ? _r.warning
      : (_r.items || []).filter(x => x.days_to_empty >= 3 && x.days_to_empty < 7).length
  // 断货三级行字号(1 行放大/3 行紧凑)——依赖 riskCritical/riskWarning 定义后
  // 断货三级行字号统一 font-xs(与明细一致)
  const riskFont = 'var(--font-xs)'
  // 明细行字号统一 font-xs(恢复原大小——少行放大曾致告警并排截断)
  const detailFont = 'var(--font-xs)'
  // 缺货列表 = stockOverview.items(本身就是 avail<=0 的缺货SKU, 含warehouse_type)
  // 缺货列表 = 当前视图维度全量(oosList, 随 healthTab 拉取); 未加载时回退旧逻辑
  const _oosSrc =
    oosList ||
    (healthTab === 'bc'
      ? bcOutOfStock || []
      : healthTab === 'own'
        ? (inventory || []).filter(x => x.warehouse_type === 'own')
        : (inventory || []).filter(x => x.warehouse_type === 'platform'))
  const outOfStockItems = _oosSrc.slice(0, 3)
  // 待处理卡 告警×仓库维度: 用后端精确 by_warehouse(全量), 按补货模式聚合展示——
  // BBCC 看全盘 B+C(与健康卡bc一致), 传统多仓不涉及B仓。曾用截断列表filter(200样本 vs 全量)
  function _whView(w) {
    w = w || {}
    if (_replMode === 'bbcc') {
      // 2026-10-30: bbcc 评估产出的就是 bc 类型(BC 合计一条); b+c 仅作历史数据兜底
      return { main: w.bc || (w.b || 0) + (w.c || 0), own: w.own || 0 }
    }
    return { main: w.c || 0, own: w.own || 0 } // C仓 + 自有/三方
  }
  const _acLsWh = (alertCounts && alertCounts.ls_warehouse) || null // 低库存/其他 组分布
  const _acRpWh = (alertCounts && alertCounts.rp_warehouse) || null // 补货 组分布
  const lsWhView = _whView(_acLsWh)
  const rpWhView = _whView(_acRpWh)

  if (chLoading)
    return (
      <div className="card" style={{ padding: 16 }}>
        {[1, 2, 3, 4, 5, 6, 7].map(i => (
          <div
            key={i}
            className="skeleton"
            style={{ height: 80, marginBottom: 8, borderRadius: 'var(--radius-card)' }}
          />
        ))}
      </div>
    )
  if (dashErr && !dashboard)
    return (
      <ErrorRetry
        error={dashErr}
        onRetry={() => {
          dashFailRef.current += 1
          window.__setPage && window.__setPage('dash')
        }}
        onReload={
          dashFailRef.current >= 2
            ? () => {
                location.reload()
              }
            : null
        }
      />
    )
  return (
    <>
      {evaluating && (
        <div
          className="small muted"
          role="status"
          style={{ marginBottom: 8, display: 'flex', alignItems: 'center', gap: 6 }}
        >
          <span
            style={{
              width: 6,
              height: 6,
              borderRadius: 3,
              background: 'var(--accent, #4c8dff)',
              display: 'inline-block',
            }}
          />
          {t('dash.recalc_running')}
        </div>
      )}
      <div className="card-grid" style={{ marginBottom: 16 }}>
        {/* 1. GMV 卡 — 加环比微趋势线 + 日均 */}
        {/* 1. GMV 卡 — 加环比微趋势线 + 日均 */}
        <GmvCard
          dashboard={dashboard}
          gmvView={gmvView}
          setGmvView={setGmvView}
          gmvColor={gmvColor}
          gmvKey={gmvKey}
          gmvPayoutOk={gmvPayoutOk}
          periodMeta={periodMeta}
          periodTrend={periodTrend}
          periodDays={periodDays}
        />

        {/* 2. 待处理卡 — iOS 18 天气风: 数字主角/语义状态行/分级明细 */}
        <AlertSummaryCard
          dashboard={dashboard}
          detailFont={detailFont}
          loadFullAlerts={loadFullAlerts}
          lowStockTotal={lowStockTotal}
          otherTotal={otherTotal}
          pendingTotal={pendingTotal}
          procBuyN={procBuyN}
          procRepN={procRepN}
          setShowAllOther={setShowAllOther}
          slowMovingTotal={slowMovingTotal}
        />

        {/* 3. 健康度 — 加总 SKU 数 */}
        <HealthCard
          dashboard={dashboard}
          healthTab={healthTab}
          healthTrend={healthTrend}
          channel={channel}
          bcMenuOpen={bcMenuOpen}
          setBcMenuOpen={setBcMenuOpen}
          setHealthWithSave={setHealthWithSave}
          setShowAllOut={setShowAllOut}
          loadFullOut={loadFullOut}
          _oosSrc={_oosSrc}
          _replMode={_replMode}
        />

        {/* 4. 濒临断货预警 — 全量计数, 弹窗看完整(iOS 18 天气小组件风, 紧凑版) */}
        <RiskCard
          replMode={_replMode}
          items={_r.items || []}
          total={_r.total}
          riskCritical={riskCritical}
          riskWarning={riskWarning}
          riskFont={riskFont}
          onAlert={function (sku, whType, wh) {
            // 2026-10-30 修复串页: 原直调 __setPage('inventory'), 而 App 路由是 page === 'inv'
            //   → 落到"功能待添加"默认分支(用户实证: 锤子菜单显示「其他 · inventory 暂无数据」);
            //   且没传 sku, 即使进入也不会高亮。改走父级 onAlert → App.navigate('inv', sku, whType, wh)
            if (onAlert) {
              onAlert(sku, whType, wh)
              return
            }
            useAppStore
              .getState()
              .setHammerWhType(whType === 'own' ? 'own' : wh === 'B' ? 'platform_b' : 'platform')
            window.__setPage && window.__setPage('inv')
          }}
          onLoadFull={loadFullRisk}
          onShowAllRisk={setShowAllRisk}
          onRiskFilter={setRiskFilter}
          t={t}
        />
        {/* 自有仓健康卡 2026-11-01 由小卡改大卡, 已移至下方 chart-row-3 区
            (小卡内容区 134~192px 放不下 90 项/300 行明细; 且三类语义不同质不能混排) */}
      </div>

      <MidCharts
        dashboard={dashboard}
        gmvView={gmvView}
        barOption={barOption}
        storeOption={storeOption}
        storeDataLen={storeDataLen}
        _storeDim={_storeDim}
        setStoreDim={setStoreDim}
        funnelLen={(dashboard?.period_funnel?.[periodTab] || dashboard?.funnel || []).length}
      />

      <ChartRow3
        dashboard={dashboard}
        ownHealth={ownHealth}
        lowStockShown={lowStockShown}
        lowStockTotal={lowStockTotal}
        procList={procList}
        procLoading={procLoading}
        procTotal={procTotal}
        loadFullAlerts={loadFullAlerts}
        setShowAllLowStock={setShowAllLowStock}
        setShowAllOwn={setShowAllOwn}
        setShowAllProc={setShowAllProc}
        onAlert={onAlert}
        onGoInsights={onGoInsights}
      />
      {/* 低库存告警弹窗 */}
      <LowStockSheet
        open={showAllLowStock}
        onClose={function () {
          setShowAllLowStock(false)
        }}
        total={lowStockTotal}
        rows={lowStockModalRows}
        replMode={_replMode}
        onAlert={onAlert}
      />

      {/* 自有仓健康全量弹窗(2026-11-01) —— 沿用低库存/采购弹窗的 sheet 结构, 三类分组展示 */}
      <OwnHealthSheet
        open={showAllOwn}
        onClose={function () {
          setShowAllOwn(false)
        }}
        ownHealth={ownHealth}
        channel={channel}
        onAlert={onAlert}
      />

      {/* 采购&补货告警弹窗(重构: 补货建议需补 + 采购建议需采, 行标签区分, 模式跟随) */}
      <ProcSheet
        open={showAllProc}
        onClose={function () {
          setShowAllProc(false)
        }}
        procTotal={procTotal}
        procList={procList}
        onGoInsights={onGoInsights}
      />

      {/* 其他告警弹窗(规则引擎非低库存类: 超卖/濒临断货/健康/滞销/自定义, 明细落点) */}
      <OtherAlertsSheet
        open={showAllOther}
        onClose={function () {
          setShowAllOther(false)
        }}
        otherTotal={otherTotal}
        fullAlerts={fullAlerts}
        replMode={_replMode}
        onAlert={onAlert}
      />

      {/* 濒临断货完整列表弹窗 */}
      <RiskSheet
        open={showAllRisk}
        onClose={function () {
          setShowAllRisk(false)
        }}
        replMode={_replMode}
        riskFilter={riskFilter}
        setRiskFilter={setRiskFilter}
        riskCritical={riskCritical}
        riskWarning={riskWarning}
        riskTabs={riskTabs}
        fullRisk={fullRisk}
        riskData={_r}
        showOwn={_showOwn}
        onAlert={onAlert}
      />

      {/* 缺货列表弹窗（按当前健康卡视图维度: own/平台行级, bc合计; 完整数据) */}
      <OosSheet
        open={showAllOut}
        onClose={function () {
          setShowAllOut(false)
        }}
        fullOut={fullOut}
        oosSrc={_oosSrc}
        healthTab={healthTab}
      />
    </>
  )
}
