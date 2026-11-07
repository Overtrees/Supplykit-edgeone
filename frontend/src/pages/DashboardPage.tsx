import React, { useState, useMemo, useEffect, useRef } from 'react'
import RiskCard from '../components/dash/RiskCard'
import { GmvCard, AlertSummaryCard, HealthCard } from './dashboard/StatCards'
import { MidCharts, ChartRow3 } from './dashboard/ChartRows'
import { api, freshStamp } from '../api/client'
import { registerPoll, POLL_EVERY } from '../api/poller'
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
    _stockOverview,
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
  // 2026-11-03 D5: 后端规则评估是否仍在跑(aux.evaluating) —— 在跑就提示并自动重拉。
  // 2026-11-07 S0: 改为读 store(boot 的 aux 段)。原来本地 state 只能由"页面自己再拉一次 aux"
  //   维护, 首屏合并成一个请求后那次拉取已删除, 否则等于把刚消掉的重复请求加回来。
  const evaluating = useAppStore(s => s.auxEvaluating)
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
  // 2026-11-07 S0-b: limit 20000 → 3000 + 排除自有仓三条。
  //   线上实测: 20000 时返回 **3.9MB / 4.69s**, 而 jd 全量告警 10884 条里 `own_overstock` 一家
  //   7215 条(占 2/3) —— 这批货由「自有仓健康」卡专属承载, 看板的告警列表**从来不显示它**
  //   (后端 aux 的 `_block_alerts` 也排除了 own_transfer_gap/own_overstock/own_expiry_risk)。
  //   代价是首屏 4 个重接口并发时, 这 3.9MB 把浏览器同域连接占满 → boot 从 1.2s 拖到 6.6s。
  //   排除后单跑 4.69s → 0.8s 量级; 3000/组 仍覆盖 low_stock(jd 2151 / other 5271 的 57%+),
  //   卡面 5 行与弹窗滚动都够用 —— 真要看第 3000 条以后的用户不存在。
  const loadFullAlerts = async () => {
    try {
      const r = await api.get<ApiObject[]>(
        '/api/alerts?channel=' +
          channel +
          '&limit=3000' +
          '&exclude=own_overstock,own_transfer_gap,own_expiry_risk',
        {
          timeout: 60000,
        },
      )
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
  /**
   * summary URL 的**唯一构造点**(2026-11-06 P0-2)
   *
   * 原来三处(首屏 load / 失败重试 / 30s 静默)各自手拼 URL, 全都只拼了日期没拼 mode。
   * `channel` 有 axios 拦截器统一注入(`api/client.ts:144` 从 `c_channel` 取), 但 **mode 没有**;
   * 而后端 `dashboard_summary` 的缺省是硬编码 `"bbcc"` —— 于是:
   *   · `other` 等非京东渠道的 summary 一直按 BBCC 周期算(健康分/低库存口径错);
   *   · 同一屏还有 `store.loadAll()` 那份**带了**正确 mode 的 summary, 谁后返回谁覆盖
   *     `dashboard`, 用户看到的就是数字偶发跳变;
   *   · 更隐蔽的是 `_sync_health_snapshot` 在 builder 内跑, 同一个 (date, channel)
   *     被两种口径交替写, 健康趋势图出现无规律锯齿。
   *
   * 所以收敛成一个函数, 禁止再手拼。
   */
  const dashUrl = (t: string) => {
    const _st = useAppStore.getState()
    let u = '/api/dashboard/summary?' + t
    if (_st.hammerDashPeriod === 'custom' && _st.customDateStart && _st.customDateEnd)
      u += '&start_date=' + _st.customDateStart + '&end_date=' + _st.customDateEnd
    return u + '&channel=' + channel + '&mode=' + _replMode
  }
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
            '&need_only=1&page=1&page_size=100', // 2026-11-06 服务端筛+分页(原拉 6MB 全量再前端过滤)
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
  // ── 2026-11-07 S0-c: 采购/补货取数**串行**到首屏主数据之后 ────────────────────
  // 线上实测(真实浏览器, 同一屏): replenishment 单跑 **1.40s**、purchase **0.91s**,
  //   但与 boot 同时发时分别变成 **15.9s / 15.4s**, boot 也从 2.5s 变 10.7s —— 四个重接口
  //   (replenishment/purchase/alerts/boot) 同时抢实例 CPU 与 TiDB 连接, 每个都慢 4~10 倍。
  //   这四条都不会被 `db.heavy` 的预算拦住(replenishment/purchase 走的是 insights 路由,
  //   不在那 3 个名额里), 所以只能在**调用侧**错开: 主数据落地后再拉这两条。
  // 现在: boot 先跑完(≈2.5s) → 再拉采购/补货(≈1.4s 并发) → 卡面补齐。总数不变, 峰值降一半。
  const _procKey = useRef('')
  const _storeLoading = useAppStore(s => s.loading)
  useEffect(() => {
    if (_storeLoading) return // 首屏主数据还在路上, 这时候发只会互相踩
    const k = channel + '|' + _replMode
    if (_procKey.current === k) return
    _procKey.current = k
    loadProc()
  }, [channel, _replMode, _storeLoading])
  // 2026-11-01 根治「低库存卡明细丢失」: 进入看板即预拉全量告警(limit=20000),
  //   卡面在此之前吃的是 alertsList —— 那是 `/api/alerts` 每组 200 条的**首屏截断快照**,
  //   与后端独立 COUNT 的 lowStockTotal 天然对不上(实测 160 vs 200), 且 id DESC 的前 N 条
  //   可能被同仓同 SKU 占满 → 卡上明细缺失/重复。预拉后卡面与弹窗同源同口径, 不再依赖用户点「更多」。
  // 2026-11-06 P2-9: 记"已预拉过的渠道"而不是布尔量。
  //   原来是个 boolean ref, 只在首次挂载跑一次 —— 而切渠道时本组件**不卸载**,
  //   于是新渠道不再预拉全量告警, 卡面回落到 `/api/alerts` 的 200 条/组截断快照,
  //   表现为"标题 160、卡面只有 5 行"且明细重复。按渠道记 + 用完即弃, 既不会漏也不会重。
  const fullAlertsChannel = useRef('')
  useEffect(() => {
    if (fullAlertsChannel.current === channel) return
    fullAlertsChannel.current = channel
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
    // 无感刷新: 仅当无 dashboard 数据(首次/清空后)才骨架; 有旧值先渲染旧值(用户要的"瞬时")
    setChLoading(!useAppStore.getState().dashboard)
    setDashErr('')
    // ── 2026-11-07 S0: 首屏只剩 1 个请求 ──────────────────────────────────────
    // 原来这里自己发 summary(**全量, 带 charts**)+aux+stock-risk, 而同一时刻
    //   store.loadAll 也发 summary(part=main)+orders+alerts+stock-risk —— 同一屏 8 个请求、
    //   两份重复。线上端口实测入口固定成本 **0.88s/请求**(404 探测), 8 个请求就是 7s 地板,
    //   再叠加原 loadAll 的三批串行编排, 就是用户看到的"看板 10 秒以上"。
    // 现在: 数据只要一处真源(store.loadAll → /api/dashboard/boot), 页面只负责骨架与错误态;
    //   charts 由 loadAll 内部并行拉取后合并, 到达即渲染, 不阻塞主项。
    useAppStore
      .getState()
      .loadAll()
      .catch(() => {
        if (seq === reqSeq.current) setDashErr(t('dash.load_fail_processing'))
      })
      .finally(() => {
        if (seq === reqSeq.current) setChLoading(false)
      })
    // 兜底: 网络挂住时 35s 收起骨架(与请求 timeout 同量级, 防骨架永久不消失)
    const _tt = setTimeout(() => {
      if (seq === reqSeq.current) setChLoading(false)
    }, 35000)
    return () => clearTimeout(_tt)
  }, [channel, pageVersion, _replMode])
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
    // 2026-11-06 S1: 交给统一调度器(可见才跑 / 隐藏降 5min / 在途去重 / 回前台补一次)。
    // 2026-11-07 S0: 刷新内容统一走 store.loadAll()(单请求聚合 /api/dashboard/boot)。
    //   原来这里另发 summary+aux+stock-risk **三个**重接口, 还带 `t=` 破缓存 —— 与首屏那套
    //   完全重复, 且 30s 一轮 × 3 个请求 × 0.88s 入口成本 = 持续白烧。一处真源。
    const stop = registerPoll('dash:silent', POLL_EVERY.dash, async () => {
      if (silentBusy.current) return
      silentBusy.current = true
      useAppStore.getState().setSyncing(1)
      useAppStore.getState().setSyncOld(true)
      try {
        await useAppStore.getState().loadAll()
      } catch {
      } finally {
        silentBusy.current = false
        useAppStore.getState().setSyncing(-1)
      }
    })
    return stop
    // 2026-11-06: 加 _replMode —— 否则切模式后静默刷新仍按旧 mode 请求
  }, [channel, _replMode])
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

  // 2026-11-06 口径统一: 原先是"前端拿库存明细行再比一遍静态 safety_qty" ——
  //   ① 这是第 6 份口径(与后端 5 份都不同); ② seed 之后 safety_qty 恒 0 → 这个数恒 0,
  //   侧边栏的"低库存"红点从来没亮过。改为直接读后端 `core.metrics` 的判定结果,
  //   粒度跟随渠道补货模式(bbcc=BC 盘 / traditional=逐仓)。
  const lowStock = Number(
    (_stockOverview as { low_stock_count?: number } | null)?.low_stock_count ?? 0,
  )
  // 码级断货(2026-11-06): 后端 code_risk 复核 —— 多码 SKU 部分 69码 可用=0 却被
  //   SKU 合计判 ok/low, 单列计数作为可见性补充, 不混进互斥的 out/low 两档
  const codeOutCount = Number(
    (_stockOverview as { code_out_count?: number } | null)?.code_out_count ?? 0,
  )
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
  const _srSrc: StockRiskShim = Array.isArray(stockRisk)
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
  /**
   * 2026-11-06 P2-10: **构造新对象**, 不再就地改。
   *
   * 原实现 `const _sr0 = stockRisk || {...}` 之后直接 `_sr0.items = ...`:
   * 当 `stockRisk` 是对象形态时, `_sr0` 与 Zustand store 里那份是**同一引用** ——
   * 等于在**渲染期改写 store state**。后果是订阅比较失真(渲染函数改了 state,
   * 却没有 setState, 订阅方看不到变化)、StrictMode/并发渲染下尤其不稳。
   * 现在用展开构造副本, store 那份一个字段都不碰。
   */
  let _sr0: StockRiskShim
  if (_replMode === 'bbcc') {
    const _it = _srSrc.bcItems || []
    _sr0 = {
      ..._srSrc,
      items: _it,
      total: _srSrc.bcTotal != null ? _srSrc.bcTotal : _it.length,
      critical:
        _srSrc.bcCritical != null ? _srSrc.bcCritical : _it.filter(x => x.days_to_empty < 3).length,
      warning:
        _srSrc.bcWarning != null
          ? _srSrc.bcWarning
          : _it.filter(x => x.days_to_empty >= 3 && x.days_to_empty < 7).length,
    }
  } else {
    // 传统多仓: C 仓维度(主) + 自有/三方仓维度(own, 由采购管控)
    const _it = _srSrc.cItems || []
    const _own = _srSrc.ownItems || []
    _sr0 = {
      ..._srSrc,
      items: _it,
      total: _srSrc.cTotal != null ? _srSrc.cTotal : _it.length,
      critical:
        _srSrc.cCritical != null ? _srSrc.cCritical : _it.filter(x => x.days_to_empty < 3).length,
      warning:
        _srSrc.cWarning != null
          ? _srSrc.cWarning
          : _it.filter(x => x.days_to_empty >= 3 && x.days_to_empty < 7).length,
      ownItems: _own,
      ownTotal: _srSrc.ownTotal != null ? _srSrc.ownTotal : _own.length,
      ownCritical:
        _srSrc.ownCritical != null
          ? _srSrc.ownCritical
          : _own.filter(x => x.days_to_empty < 3).length,
      ownWarning:
        _srSrc.ownWarning != null
          ? _srSrc.ownWarning
          : _own.filter(x => x.days_to_empty >= 3 && x.days_to_empty < 7).length,
    }
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
        codeOutCount={codeOutCount}
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
