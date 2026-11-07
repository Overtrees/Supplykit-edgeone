// 类型定义
interface OrderItem {
  id: number
  order_no: string
  sku: string
  barcode?: string
  product_name: string
  store: string
  warehouse?: string
  quantity: number
  unit_price: number
  total_amount: number
  order_status: string
  ordered_at: string
  paid_at?: string
  platform?: string
  deleted_at?: string | null
}

// 渠道注册表条目(2026-10-30 P0: 渠道数据驱动)
export interface Channel {
  code: string
  name: string
  enabled?: number
  supports_b?: boolean
  default_mode?: string
  sort?: number
}

import { create } from 'zustand'
import { api, clearCache, clearInflight, freshStamp, isCanceled } from '../api/client'
import { registerPoll } from '../api/poller'
import type { StockRiskShim } from '../api/types'
import { errText } from '../components/ErrorRetry'
import { daysAgoCn, todayCn } from '../utils/date'

/**
 * 全局操作态(2026-11-03)
 *
 * 与 syncing 的分工必须分清, 否则会重犯"一个变量表达两件事"的错:
 *   syncing  = 前端正在取数(→ 胶囊「同步中/加载中」)
 *   ops      = 后端正在算/在导(→ 胶囊「规则重算中/N 项处理中」)
 *
 * 不塞进 syncing 的三个理由:
 *   1. syncing 有 20s 强制归零兜底(App.tsx), 长任务会被"显示完成";
 *   2. syncing 只是计数, 说不出在算什么、算多久;
 *   3. 二者生命周期不同 —— 取数秒级, 计算/导入分钟级, 混用后无法分别判断。
 */
export type AppOp = {
  id: string
  kind: 'rules' | 'ingest' | 'write'
  label: string
  scope?: string
  /** 后端任务 id(用于轮询真实状态, 不靠前端计时猜) */
  taskId?: string | number
  startedAt: number
}

const OPS_KEY = 'c_ops'
// 刷新页面后仍显示"处理中"的上限: 超过即视为陈旧(后端任务早已结束或失败), 不再纠缠
const OPS_TTL_MS = 15 * 60 * 1000

function loadOps(): AppOp[] {
  try {
    const arr = JSON.parse(localStorage.getItem(OPS_KEY) || '[]')
    if (!Array.isArray(arr)) return []
    const now = Date.now()
    return arr.filter(
      (o: AppOp) => o && o.id && now - Number(o.startedAt || 0) < OPS_TTL_MS,
    ) as AppOp[]
  } catch {
    return []
  }
}

function saveOps(ops: AppOp[]) {
  try {
    localStorage.setItem(OPS_KEY, JSON.stringify(ops))
  } catch {
    /* 隐私模式/超配额: 只影响刷新后恢复, 不影响当前会话 */
  }
}

const POLL_MS = Number(import.meta.env.VITE_POLL_INTERVAL_MS || 60000)
const WS_URL = import.meta.env.VITE_WS_URL || ''

// 安全 localStorage 读取（Safari 隐私模式兼容）
const safeGet = (key, def = null) => {
  try {
    return localStorage.getItem(key)
  } catch {
    return def
  }
}
const safeGetJSON = (key, def = null) => {
  try {
    return JSON.parse(localStorage.getItem(key) || 'null')
  } catch {
    return def
  }
}

// 2026-11-05 并发序号(模块级, 不在 store state 里): 只用于"丢弃过期响应", 不参与渲染,
//   所以不该进 state(进 state 会额外触发订阅者重渲染, 且 reset() 时还得记着复位)
const _seqBox = { v: 0 }
// 订单列表单独一条序号(2026-11-07): 翻页/搜索只刷订单, 与 loadAll 的全站刷新互不干扰。
const _orderSeqBox = { v: 0 }

const initAppStore = (set, get) => ({
  channel: safeGet('c_channel') || 'jd',
  // 2026-10-30 渠道注册表(数据驱动): 下拉/渠道名/默认模式都读它, 新增渠道无需改代码
  // 2026-10-30 channels 不做本地缓存: 属配置类数据(禁用/改名需即时生效),
  //   首屏正确性由文案兜底保证(兜底='京东渠道'), 无需用缓存换实时性
  channels: [] as Channel[],
  setChannels: (list: Channel[]) => set({ channels: Array.isArray(list) ? list : [] }),
  channelVersion: 0,
  pageVersion: 0,
  dashboard: null,
  orders: [],
  orderTotal: 0,
  orderPage: 1,
  inventory: [],
  qualityLogs: [],
  alerts: [],
  stockRisk: [] as unknown[] | StockRiskShim,
  bcOutOfStock: [],
  _stockOverview: null,
  // 自有仓健康(2026-11-01): 供给侧口径(调拨缺口/积压/效期) —— 自有仓无销售行, 低库存卡不适用
  ownHealth: null as unknown,
  alertCounts: null as {
    by_type?: Record<string, number>
    by_severity?: Record<string, number>
    ls_warehouse?: Record<string, number>
    rp_warehouse?: Record<string, number>
  } | null,
  loading: false, // 统一 loading 状态
  wsStatus: 'idle',
  importLogs: [],
  poller: null,
  ws: null,

  dataVersion: 0,
  bumpDataVersion: () => set(s => ({ dataVersion: s.dataVersion + 1 })),
  orderSearch: '',
  orderStatus: '',
  orderLoading: false,
  orderLoadErr: '',
  dataLoaded: false,
  // 2026-10-27 全局同步徽标计数(header 胶囊): 任一拉新 start +1 / settle -1; syncOld 决定文案(同步中/加载中)
  //   2026-11-05 根治: 这个计数**只是页面声明的"整轮取数"**, 不再是胶囊的唯一依据。
  //   见下方 badgeBusy 派生值 —— 网络层还有一份"真实在途请求数"兜底。
  syncing: 0,
  setSyncing: (d: number) => set(s => ({ syncing: Math.max(0, s.syncing + d) })),
  // 2026-10-27 同步场景: true=有旧值渲染后台拉新(徽标'同步中') / false=首载骨架(徽标'加载中')
  syncOld: false,
  setSyncOld: (v: boolean) => set({ syncOld: v }),
  // 2026-11-03 全局操作态: 后端在算/在导(跨页可见, 刷新可恢复)
  ops: loadOps(),
  opAdd: (o: AppOp) =>
    set(s => {
      // 同 id 重复登记(任务轮询 effect 重建)保留原始开始时间, 否则耗时显示会被重置成 0s
      const old = s.ops.find(x => x.id === o.id)
      const next = [
        ...s.ops.filter(x => x.id !== o.id),
        old ? { ...o, startedAt: old.startedAt } : o,
      ]
      saveOps(next)
      return { ops: next }
    }),
  opDone: (id: string) =>
    set(s => {
      const next = s.ops.filter(x => x.id !== id)
      if (next.length !== s.ops.length) saveOps(next)
      return { ops: next }
    }),
  opClear: () => {
    saveOps([])
    set({ ops: [] })
  },
  sidebarOpen: false,
  // ── 历史归档页共享状态(2026-11-02 S2-9: 时间控件收敛到锤子菜单, 页面与锤子同源) ──
  // 与看板 setHammerDashPeriod 同思路: 落 localStorage(按渠道), 切页面/刷新都不丢。
  // 不放组件本地的原因见 HammerHistory 头注释 —— 锤子面板卸载即丢, 页面拿不到值。
  histPreset: safeGet('c_hist_preset_' + (safeGet('c_channel') || 'jd')) || '30d',
  histStart: (() => {
    const _c = safeGet('c_channel') || 'jd'
    const _s = safeGet('c_hist_start_' + _c) || ''
    if (_s) return _s
    return daysAgoCn(29) // 北京时间近 30 天(UTC 会在 00:00-08:00 差一天)
  })(),
  histEnd: (() => {
    const _c = safeGet('c_channel') || 'jd'
    return safeGet('c_hist_end_' + _c) || todayCn()
  })(),
  histBy: (safeGet('c_hist_by_' + (safeGet('c_channel') || 'jd')) as 'day' | 'month') || 'day',
  histShowCustom: false,
  setHistShowCustom: v => set({ histShowCustom: v }),
  // 预设: 天数 > 0 时同步回写 start/end(否则页面拿到新 preset 却用旧区间 → 显示与数据不一致)
  setHistPreset: p => {
    const ch = get().channel
    const days = p === '7d' ? 7 : p === '30d' ? 30 : p === '90d' ? 90 : 0
    let start = get().histStart
    let end = get().histEnd
    if (days > 0) {
      start = daysAgoCn(days - 1)
      end = todayCn()
    }
    try {
      localStorage.setItem('c_hist_preset_' + ch, p)
      if (days > 0) {
        localStorage.setItem('c_hist_start_' + ch, start)
        localStorage.setItem('c_hist_end_' + ch, end)
      }
    } catch {}
    set({ histPreset: p, histStart: start, histEnd: end })
  },
  // 自定义日期: 逐字段改(不强制切 custom, 由"确认"决定是否生效), commit=true 时落库并切 custom
  setHistRange: (start, end, commit) => {
    const ch = get().channel
    if (commit) {
      try {
        localStorage.setItem('c_hist_start_' + ch, start)
        localStorage.setItem('c_hist_end_' + ch, end)
        localStorage.setItem('c_hist_preset_' + ch, 'custom')
      } catch {}
      set({ histStart: start, histEnd: end, histPreset: 'custom' })
    } else {
      set({ histStart: start, histEnd: end })
    }
  },
  setHistBy: b => {
    try {
      localStorage.setItem('c_hist_by_' + get().channel, b)
    } catch {}
    set({ histBy: b })
  },
  setSidebarOpen: v => set({ sidebarOpen: v }),
  hammerPanel: null,
  // 回收页共享状态(2026-09-15: 页面与锤子菜单联动)——selected 计数 + 忙碌标志
  recycleSel: { rules: 0, orders: 0, inventory: 0 },
  recycleBusy: false,
  setRecycleSel: sel => set({ recycleSel: sel }),
  setRecycleBusy: b => set({ recycleBusy: b }),
  setHammerPanel: panel => set({ hammerPanel: panel }),
  hammerSearch: '',
  setHammerSearch: text => set({ hammerSearch: text }),
  hammerData: safeGetJSON('c_hammer_data_' + (safeGet('c_channel') || 'jd')) || {},
  hammerWhType: (() => {
    const _ch0 = safeGet('c_channel') || 'jd'
    const _wh0 = safeGet('c_wh_type_' + _ch0) || 'own'
    const _m0 = safeGet('c_replen_mode_' + _ch0) || (_ch0 === 'jd' ? 'bbcc' : 'traditional')
    return (_ch0 !== 'jd' || _m0 !== 'bbcc') && _wh0 === 'platform_b' ? 'own' : _wh0
  })(),
  setHammerWhType: v => {
    try {
      localStorage.setItem('c_wh_type_' + get().channel, v)
    } catch {}
    set({ hammerWhType: v })
  },
  hammerInsightsTab: 'replen',
  setHammerInsightsTab: t => set({ hammerInsightsTab: t }),
  hammerCleansingChannel: (() => {
    try {
      return localStorage.getItem('c_channel') || 'jd'
    } catch {
      return 'jd'
    }
  })(),
  setHammerCleansingChannel: c => set({ hammerCleansingChannel: c }),
  hammerCleansingTarget: 'order',
  setHammerCleansingTarget: t => set({ hammerCleansingTarget: t }),
  hammerCleansingConflict: 'sum',
  setHammerCleansingConflict: m => set({ hammerCleansingConflict: m }),
  // 清洗流程当前步骤(锤子菜单按步骤条件显示按钮: 模版管理/状态映射仅映射页 0=选文件 1=映射 2=预览 3=完成)
  hammerCleansingStep: 0,
  setHammerCleansingStep: s => set({ hammerCleansingStep: s }),
  hammerRulesTab: 'rules',
  setHammerRulesTab: t => set({ hammerRulesTab: t }),
  hammerRuleNewVersion: 0,
  bumpHammerRuleNew: () => set(s => ({ hammerRuleNewVersion: s.hammerRuleNewVersion + 1 })),
  hammerRulesMode:
    safeGet('c_replen_mode_' + (safeGet('c_channel') || 'jd')) ||
    ((safeGet('c_channel') || 'jd') === 'jd' ? 'bbcc' : 'traditional'),
  setHammerRulesMode: m => {
    try {
      localStorage.setItem('c_replen_mode_' + get().channel, m)
    } catch {}
    set({ hammerRulesMode: m, hammerReplenMode: m })
  },
  hammerDashPeriod: safeGet('c_dash_period_' + (safeGet('c_channel') || 'jd')) || 'month',
  customDateStart: (() => {
    try {
      const _c = safeGet('c_channel') || 'jd'
      return safeGet('c_dash_custom_' + _c + '_start') || ''
    } catch {
      return ''
    }
  })(),
  customDateEnd: (() => {
    try {
      const _c = safeGet('c_channel') || 'jd'
      return safeGet('c_dash_custom_' + _c + '_end') || ''
    } catch {
      return ''
    }
  })(),
  setHammerDashPeriod: p => {
    try {
      localStorage.setItem('c_dash_period_' + get().channel, p)
      localStorage.removeItem('c_dash_custom_' + get().channel + '_start')
      localStorage.removeItem('c_dash_custom_' + get().channel + '_end')
    } catch {}
    set({ hammerDashPeriod: p, customDateStart: '', customDateEnd: '' })
    get().loadAll()
  },
  setCustomDate: (start, end) => {
    try {
      localStorage.setItem('c_dash_period_' + get().channel, 'custom')
      localStorage.setItem('c_dash_custom_' + get().channel + '_start', start)
      localStorage.setItem('c_dash_custom_' + get().channel + '_end', end)
    } catch {}
    set({ customDateStart: start, customDateEnd: end, hammerDashPeriod: 'custom' })
    get().loadAll()
  },
  hammerReplenMode:
    safeGet('c_replen_mode_' + (safeGet('c_channel') || 'jd')) ||
    ((safeGet('c_channel') || 'jd') === 'jd' ? 'bbcc' : 'traditional'),
  setHammerReplenMode: m => {
    try {
      localStorage.setItem('c_replen_mode_' + get().channel, m)
    } catch {}
    set({ hammerReplenMode: m, hammerRulesMode: m })
  },
  hammerCols: {} as Record<string, string[]>,
  setHammerCols: (pageKey, cols) =>
    set(s => ({ hammerCols: { ...s.hammerCols, [pageKey]: cols } })),
  prodBatch: false,
  setProdBatch: v => set({ prodBatch: v }),

  // 订单批量模式(2026-10-09 复用商品页同款体系)
  orderBatch: false,
  setOrderBatch: v => set({ orderBatch: v }),
  orderSelIds: [] as number[],
  setOrderSelIds: ids => set({ orderSelIds: ids }),
  orderBatchVersion: 0,
  bumpOrderBatchVersion: () => set(s => ({ orderBatchVersion: s.orderBatchVersion + 1 })),

  // 库存批量模式(2026-11-04): 与订单同款交互, 但语义不同 —— 删除是"整行归档快照",
  // 恢复在回收站页; 所以这里只负责"勾选 + 批量删除", 不承载恢复。
  invBatch: false,
  setInvBatch: v => set({ invBatch: v }),
  invSelIds: [] as number[],
  setInvSelIds: ids => set({ invSelIds: ids }),

  prodSelIds: [],
  prodFilterLen: 0,
  prodBatchVersion: 0,
  batchStateMap: {},
  setProdBatchSel: ids => set({ prodSelIds: ids }),
  setProdBatchFilterLen: n => set({ prodFilterLen: n }),
  bumpProdBatchVersion: () => set(s => ({ prodBatchVersion: s.prodBatchVersion + 1 })),
  prodBatchAllReq: 0,
  requestProdBatchAll: () => set(s => ({ prodBatchAllReq: s.prodBatchAllReq + 1 })),
  setHammerData: (page, data) => {
    const ch = get().channel
    const channelData = get().hammerData[ch] || {}
    const hd = { ...get().hammerData, [ch]: { ...channelData, [page]: data } }
    try {
      localStorage.setItem('c_hammer_data', JSON.stringify(hd))
    } catch {}
    set({ hammerData: hd })
  },
  setChannel: ch => {
    try {
      localStorage.setItem('c_channel', ch)
    } catch {}
    clearCache()
    clearInflight()
    set({ hammerSearch: '' })
    const _wh = safeGet('c_wh_type_' + ch) || 'own'
    set({
      channel: ch,
      dataLoaded: false,
      loading: true,
      hammerWhType: ch !== 'jd' && _wh === 'platform_b' ? 'own' : _wh,
      hammerDashPeriod: safeGet('c_dash_period_' + ch) || 'month',
      hammerReplenMode: safeGet('c_replen_mode_' + ch) || (ch === 'jd' ? 'bbcc' : 'traditional'),
      hammerRulesMode: safeGet('c_replen_mode_' + ch) || (ch === 'jd' ? 'bbcc' : 'traditional'),
      hammerCleansingChannel: ch,
      // 2026-11-02 S2-9: 历史归档区间也按渠道恢复(与看板周期同规则), 否则切渠道后
      // 页面沿用了上一个渠道的日期区间 —— 用户会以为数据错
      histPreset: safeGet('c_hist_preset_' + ch) || '30d',
      histStart: (() => {
        const _s = safeGet('c_hist_start_' + ch) || ''
        if (_s) return _s
        return daysAgoCn(29) // 北京时间近 30 天(UTC 会在 00:00-08:00 差一天)
      })(),
      histEnd: safeGet('c_hist_end_' + ch) || todayCn(),
      histBy: (safeGet('c_hist_by_' + ch) as 'day' | 'month') || 'day',
      histShowCustom: false,
      prodBatch: false,
      prodSelIds: [],
      batchStateMap: {},
    })
    get().loadAll()
  },
  bumpPageVersion: () => set(s => ({ pageVersion: s.pageVersion + 1 })),

  /**
   * 让"已渲染出去的快照"整体失效(2026-11-05 修「重置后健康卡片还有数据」)
   *
   * 为什么不能只 clearCache: 请求缓存是**输入**, store 里的 dashboard/ownHealth/stockRisk
   * 是**输出** —— 已经渲染在页面上了。清输入不会让输出消失, 下一次 loadAll 又是浅合并
   * （dashboard: {..._prev, ...data}）, 新响应里没带的字段会永远留旧值。
   * 重置/清库这类"服务端数据被整体改写"的场景, 必须把输出也清掉, 让页面回到真实空态。
   * pageVersion 递增用于驱动页面级 useEffect 重新拉取(那些不读 store 的模块快照)。
   */
  invalidateSnapshots: () =>
    set(s => ({
      dashboard: null,
      orders: [],
      orderTotal: 0,
      inventory: [],
      qualityLogs: [],
      alerts: [],
      stockRisk: [],
      bcOutOfStock: [],
      _stockOverview: null,
      ownHealth: null,
      alertCounts: null,
      dataLoaded: false,
      pageVersion: s.pageVersion + 1,
    })),

  /** 2026-11-07 按视图裁剪: 只有看板/订单页需要"看板域", 其它页(商品/供应商/规则/清洗…)
   *  根本不用 summary/aux/stock-risk/alerts。原来这里无条件 loadAll(), 与看板页**自拉**
   *  (DashboardPage 带 t= 破缓存的权威路径)同时发同一批重接口 —— 服务端把 summary、aux、
   *  stock-risk 各算两遍, 两个请求就能把 10 条连接池吃满, 全站跟着卡 10~40s。
   *  这里只留"页头角标"这一条轻查询(0.4s), 重接口回归各自的页面。
   */
  async loadChrome() {
    try {
      const r = await api.get('/api/quality-logs?scope=user')
      set({ qualityLogs: r.data || [] })
    } catch {
      /* 角标失败不影响页面 */
    }
  },

  /** 看板落地时的订单域(订单卡/顶栏)+ 角标; 看板域交给 DashboardPage 自拉, 不再重复 */
  async loadOrdersChrome() {
    const _seq = _seqBox
    const _mySeq = ++_seq.v
    const s = get().hammerSearch || ''
    const st = get().orderStatus || ''
    try {
      const [o, q] = await Promise.allSettled([
        api.get(
          '/api/orders?page=1&page_size=30&search=' +
            encodeURIComponent(s) +
            '&status=' +
            encodeURIComponent(st),
        ),
        api.get('/api/quality-logs?scope=user'),
      ])
      if (_mySeq !== _seq.v) return
      if (o.status === 'fulfilled') {
        const d = (o.value.data || {}) as { items?: unknown[]; total?: number }
        set({ orders: d.items || [], orderTotal: d.total || 0, orderPage: 1 })
      }
      if (q.status === 'fulfilled') set({ qualityLogs: q.value.data || [] })
    } catch {
      /* 同上 */
    }
  },

  async loadAll(page?, opts?) {
    // 2026-11-05: 序号放在闭包对象里而不是 this 上 —— `this` 在 setState 回调/延迟执行里
    //   不一定还指向 store, 而竞态判定必须用同一个引用
    const _seq = _seqBox
    const _mySeq = ++_seq.v
    set({
      loading: true,
      orderLoading: true,
      syncOld: Boolean(get().dashboard || (get().orders || []).length), // 看板/订单有旧值→同步中
    })
    // 2026-11-05 修: 这里原写 `syncing: get().syncing + 1` 直接赋值, 而收尾用的是
    //   `Math.max(0, get().syncing - 1)`。两条路径对"计数"的语义不一致 —— 当其它页面
    //   (Inventory/Insights/History) 的 -1 先把计数压到 0, 本函数收尾时又 -1 再夹 0,
    //   于是"它减掉的是别人的名额", 剩余的那次真实取数就没人计数了 → 胶囊提前停。
    //   统一走 setSyncing, 语义只有一处。
    get().setSyncing(1)
    const ch = get().channel
    const s = get().hammerSearch || ''
    const st = get().orderStatus || ''
    const p = page || get().orderPage || 1
    const ds = get().hammerDashPeriod
    const cds = get().customDateStart
    const cde = get().customDateEnd
    // opts.refresh=true: dashboard 请求带 refresh=1（填充/导入完成后强制同步重建，不用旧值）
    let dashUrl = '/api/dashboard/summary'
    if (ds === 'custom' && cds && cde) dashUrl += '?start_date=' + cds + '&end_date=' + cde
    // 动态安全线按补货模式区分周期(健康指数判定)
    dashUrl += (dashUrl.includes('?') ? '&' : '?') + 'mode=' + get().hammerReplenMode
    if (opts && opts.refresh) dashUrl += '&refresh=1'
    try {
      // 首屏错峰(2026-10-11 三批): summary 先行渲染 → orders/alerts 0.3s 后 → stock-risk 最后——
      // 避免 5 接口并发竞争 TiDB(10s+ → 3-5s; 数据最终一致)
      // 2026-10-28 超时分级: 重接口 35s(实测峰值 13.9s + 余量, 兼顾响应性——60s 过长)
      // 2026-10-28 两阶段加载(根治组装慢): part=main(卡片数字/健康, 组装轻)先渲染 →
      // part=charts(趋势/店铺/品牌/漏斗, 组装重)后到合并。同一缓存键派生两份视野(同源同快照, 四维一致)
      const _sep = dashUrl.includes('?') ? '&' : '?'
      const _getMain = () => api.get(dashUrl + _sep + 'part=main', { timeout: 35000 })
      let r0 = await Promise.allSettled([_getMain()])
      // 2026-10-28 部署窗口冷启动加固: 实例重启期首个请求可能前端超时而后端已算完
      // (15:58/16:25 两次 35s 超时, 同时刻后端 perf_debug 显示 1.1s 正常)——静默重试 1 次(1.2s 后, 破缓存)
      if (r0[0].status === 'rejected') {
        await new Promise<void>(res => setTimeout(res, 1200))
        if (_mySeq !== _seq.v) return
        r0 = await Promise.allSettled([
          api.get(dashUrl + _sep + 'part=main&t=' + freshStamp(), { timeout: 35000 }),
        ])
      }
      const dashboard = r0[0].status === 'fulfilled' ? r0[0].value : { data: null }
      if (_mySeq !== _seq.v) return // 快速切换: 旧 loadAll 结果丢弃(最新点击优先)
      // ⚠️ 合并而非替换: part=main 不含 charts —— 若整体替换, 静默刷新时图表会闪空等 charts 回填
      const _prev: Record<string, unknown> = get().dashboard || {}
      set({
        dashboard: dashboard.data ? { ..._prev, ...dashboard.data } : _prev,
        loading: false,
        dataLoaded: true,
      })
      // 图表数据并行拉取(不阻塞主项渲染), 到达后合并到 dashboard(同快照键)
      const _getCharts = () => api.get(dashUrl + _sep + 'part=charts', { timeout: 60000 })
      _getCharts()
        .catch(() => {
          // 部署窗口冷启动: 静默重试一次(带 t= 破缓存)
          return new Promise(res => setTimeout(res, 1200)).then(() =>
            _getCharts().catch(() => null),
          )
        })
        .then(rc => {
          if (_mySeq !== _seq.v) return
          const cur: Record<string, unknown> = get().dashboard || {}
          set({ dashboard: { ...cur, ...(rc?.data || {}) } })
        })
        .catch(() => {})
      await new Promise<void>(resolve => setTimeout(resolve, 300))
      const r1 = await Promise.allSettled([
        api.get(
          '/api/orders?page=' +
            p +
            '&page_size=30&search=' +
            encodeURIComponent(s) +
            '&status=' +
            encodeURIComponent(st),
        ),
        api.get('/api/quality-logs?scope=user'), // 2026-10-17 角标按用户维度(曾全量 200 截断——含 dev 排查日志误导)
        api.get('/api/alerts'),
      ])
      const arr = r1.map(r => (r.status === 'fulfilled' ? r.value : { data: null }))
      if (_mySeq !== _seq.v) return // 旧 loadAll 丢弃
      set({
        orders: arr[0].data?.items || arr[0].data || [],
        orderTotal: arr[0].data?.total || (arr[0].data || []).length || 0,
        orderPage: arr[0].data?.page || p,
        qualityLogs: arr[1].data || [],
        alerts: arr[2].data || [],
        orderLoadErr:
          r1[0] && r1[0].status === 'rejected'
            ? errText((r1[0] as { reason?: unknown }).reason as string)
            : '',
        // 2026-11-05 修(用户报「多页面一直加载中/同步中」的关键一环):
        //   原来 orderLoading 只在这一处(以及 stock-risk 之后)被置 false, 且**没有序号保护**。
        //   场景: 用户在 0.5s 内从订单页切到别的页, 第二次 loadAll 已把 orderLoading 置 true,
        //   而第一次的这次 set 无条件把它刷成 false → 列表闪一下骨架又空白; 反过来当第一次
        //   卡在下面 stock-risk(35s 超时)时, orderLoading 会一直 true 长达 35s。
        //   订单数据已经写入, 同一次 set 里收掉它 —— 既不依赖后续慢请求, 也不会互相踩。
        orderLoading: false,
      })
      // 最后: stock-risk 断货卡(重接口, 不阻塞渲染; 失败保留旧值不闪空)
      //   2026-11-05: 这里原有一次 `set({ orderLoading: false })` —— 位置在慢请求之后且无序号
      //   保护, 是上面那条 bug 的第二个写入点。已随上面并入, 不再单独写。
      const sr = await Promise.allSettled([
        api.get('/api/dashboard/stock-risk', { timeout: 35000 }),
      ])
      const srData = sr[0].status === 'fulfilled' ? sr[0].value.data : null
      if (srData && _mySeq === _seq.v) set({ stockRisk: srData })
    } catch (e) {
      console.error('loadAll failed:', e)
      // 2026-11-05: 只在"自己仍是最新那次"时收起 loading —— 否则旧请求的失败会把
      //   新请求正在加载的界面误判为"已加载完"(反过来同样致命, 见 finally 注释)
      if (_mySeq === _seq.v) set({ loading: false, orderLoading: false })
    } finally {
      // 2026-10-27 根治: settle 唯一在 finally——无论 return 竞态/abort/catch/正常尾必执行, 数不可能泄漏(曾散点 settle 被 return 跳过 → 永久'加载中')
      get().setSyncing(-1)
      // 2026-11-05 修(用户报「多数页面一直在加载中」): `loading` / `orderLoading` 此前只在
      //   **正常走到** set 时才置 false。而上面 4 处 `if (_mySeq !== _seq.v) return`
      //   全都在 finally 之前 —— 一旦发生"快速切换页面/连续两次 loadAll", 被丢弃的那次
      //   会把 `loading: true` 留在 state 里, 而胜出的那次只写 dashboard 不写 loading……
      //   结果就是骨架屏永不消失。这里以"自己仍是最新那次"为条件统一兜底:
      //   最新那次无论走 return 还是正常结束, 都必须把 loading 收掉。
      if (_mySeq === _seq.v) {
        const st = get()
        if (st.loading) set({ loading: false })
        if (st.orderLoading) set({ orderLoading: false })
      }
    }
  },

  connectWebSocket() {
    if (!WS_URL) {
      set({ wsStatus: 'polling', ws: null })
      return
    } // Makers 无 WebSocket, 直接轮询
    const oldWs = get().ws
    if (oldWs) {
      try {
        oldWs.close()
      } catch (e) {}
    }

    try {
      const ws = new WebSocket(WS_URL)
      ws.onopen = () => {
        set({ wsStatus: 'connected', ws })
        get()
          .loadAll()
          .catch(() => {})
      }
      ws.onmessage = evt => {
        // 按事件类型分发: 清洗进度不触发全局刷新(避免导入期间频繁loadAll), 只通知进度
        try {
          const msg = JSON.parse(evt.data || '{}')
          if (msg && msg.type === 'cleansing_progress') {
            window.dispatchEvent(
              new CustomEvent('cleansing-progress', { detail: msg.payload || msg }),
            )
            return
          }
        } catch {}
        // 其他 WS 事件 → reload data for real-time updates
        get()
          .loadAll()
          .catch(() => {})
        get().bumpDataVersion()
      }
      ws.onclose = () => {
        set({ wsStatus: 'polling', ws: null })
        setTimeout(() => get().connectWebSocket(), 10000)
      }
      ws.onerror = () => {
        set({ wsStatus: 'polling', ws: null })
        setTimeout(() => get().connectWebSocket(), 10000)
      }
    } catch (e) {
      set({ wsStatus: 'polling', ws: null })
    }
  },

  addImportLog(item) {
    set(state => ({ importLogs: [item, ...state.importLogs].slice(0, 20) }))
  },

  /**
   * 只刷新订单列表(2026-11-07)
   *
   * 订单页的翻页/搜索/状态筛选原来调 `loadAll()` —— 那会把看板 summary、stock-risk、
   * 告警、质检日志全部重拉一遍(6~8 个接口), 只为换一页 30 条订单。用户输入一次搜索词
   * 就触发一次全站重算; 叠加"切页不清缓存"之前的行为, 单个实例被打满。
   * 现在: 订单页的高频操作只发 `/api/orders` 一条, 全站数据留给看板/各页自己刷新。
   * 序号独立于 loadAll —— 两者并发时, 后发起的订单请求赢(不会被旧的 loadAll 订单段覆盖)。
   */
  async loadOrdersOnly(page?, search?, status?) {
    const p = page || get().orderPage || 1
    // 搜索词真源是 hammerSearch(订单页输入框); orderSearch 只是历史字段, 恒空 —— 用它会导致翻页丢条件
    const s = search ?? (get().hammerSearch || '')
    const st = status ?? (get().orderStatus || '')
    const _my = ++_orderSeqBox.v
    set({ orderLoading: true })
    try {
      const r = await api.get(
        '/api/orders?page=' +
          p +
          '&page_size=30&search=' +
          encodeURIComponent(s) +
          '&status=' +
          encodeURIComponent(st),
        { scope: 'orders', timeout: 35000 },
      )
      if (_my !== _orderSeqBox.v) return
      const d = (r.data || {}) as {
        items?: unknown[]
        total?: number
        page?: number
      }
      set({
        orders: d.items || (d as unknown[]) || [],
        orderTotal: d.total || (d.items || []).length || 0,
        orderPage: d.page || p,
        orderLoadErr: '',
        orderLoading: false,
      })
    } catch (e) {
      if (isCanceled(e)) return
      if (_my === _orderSeqBox.v) {
        set({ orderLoadErr: errText(e as string), orderLoading: false })
      }
    }
  },

  setOrderPage(p, search?, status?) {
    const s = search ?? (get().hammerSearch || '')
    const st = status ?? get().orderStatus
    set({ orderPage: p, orderSearch: s, orderStatus: st, orderLoading: true })
    get().loadOrdersOnly(p, s, st)
  },

  setOrderFilterLocal(search, status) {
    set({ orderSearch: search, orderStatus: status, orderPage: 1 })
    get().loadOrdersOnly(1, search, status)
  },

  startPolling() {
    // 2026-11-06 S1: 交给统一调度器 —— 原来裸 setInterval, 不看可见性, 且与看板/任务页
    //   各自为政。这里保留"WS 没连上才轮询"的语义(WS 连上时不该重复拉)。
    const old = get().poller
    if (old) old()
    get()
      .loadAll()
      .catch(() => {})
    // Try WebSocket first, fall back to polling
    get().connectWebSocket()
    const stop = registerPoll(
      'store:refresh',
      POLL_MS,
      () => {
        // Only poll if WS is not connected
        if (get().wsStatus !== 'connected') {
          get()
            .loadAll()
            .catch(() => {})
        }
      },
      // 上面已经手动 loadAll() 过一次, 首轮不必重复
      { immediate: false },
    )
    set({ poller: stop })
  },

  stopAll() {
    const oldPoller = get().poller
    if (oldPoller) oldPoller()
    const oldWs = get().ws
    if (oldWs) {
      try {
        oldWs.close()
      } catch (e) {}
    }
    set({ poller: null, ws: null })
  },
})

export const useAppStore = create(initAppStore)
