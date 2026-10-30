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
import { api, clearCache, clearInflight } from '../api/client'
import type { StockRiskShim } from '../api/types'
import { errText } from '../components/ErrorRetry'

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
  syncing: 0,
  setSyncing: (d: number) => set(s => ({ syncing: Math.max(0, s.syncing + d) })),
  // 2026-10-27 同步场景: true=有旧值渲染后台拉新(徽标'同步中') / false=首载骨架(徽标'加载中')
  syncOld: false,
  setSyncOld: (v: boolean) => set({ syncOld: v }),
  sidebarOpen: false,
  setSidebarOpen: v => set({ sidebarOpen: v }),
  hammerPanel: null,
  // 回收页共享状态(2026-09-15: 页面与锤子菜单联动)——selected 计数 + 忙碌标志
  recycleSel: { rules: 0, orders: 0 },
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
      prodBatch: false,
      prodSelIds: [],
      batchStateMap: {},
    })
    get().loadAll()
  },
  bumpPageVersion: () => set(s => ({ pageVersion: s.pageVersion + 1 })),

  _loadSeq: 0,
  async loadAll(page?, opts?) {
    const _mySeq = ++this._loadSeq
    set({
      loading: true,
      orderLoading: true,
      syncing: get().syncing + 1,
      syncOld: Boolean(get().dashboard || (get().orders || []).length), // 看板/订单有旧值→同步中
    })
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
        if (_mySeq !== this._loadSeq) return
        r0 = await Promise.allSettled([
          api.get(dashUrl + _sep + 'part=main&t=' + Date.now(), { timeout: 35000 }),
        ])
      }
      const dashboard = r0[0].status === 'fulfilled' ? r0[0].value : { data: null }
      if (_mySeq !== this._loadSeq) return // 快速切换: 旧 loadAll 结果丢弃(最新点击优先)
      // ⚠️ 合并而非替换: part=main 不含 charts —— 若整体替换, 静默刷新时图表会闪空等 charts 回填
      const _prev: any = get().dashboard || {}
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
        .then((rc: any) => {
          if (_mySeq !== this._loadSeq) return
          const cur: any = get().dashboard || {}
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
      if (_mySeq !== this._loadSeq) return // 旧 loadAll 丢弃
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
      })
      // 最后: stock-risk 断货卡(重接口, 不阻塞; 失败保留旧值不闪空)
      const sr = await Promise.allSettled([
        api.get('/api/dashboard/stock-risk', { timeout: 35000 }),
      ])
      const srData = sr[0].status === 'fulfilled' ? sr[0].value.data : null
      if (srData && _mySeq === this._loadSeq) set({ stockRisk: srData })
      set({ orderLoading: false })
    } catch (e) {
      console.error('loadAll failed:', e)
      set({ loading: false, orderLoading: false })
    } finally {
      // 2026-10-27 根治: settle 唯一在 finally——无论 return 竞态/abort/catch/正常尾必执行, 数不可能泄漏(曾散点 settle 被 return 跳过 → 永久'加载中')
      set({ syncing: Math.max(0, get().syncing - 1) })
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

  setOrderPage(p, search?, status?) {
    const s = search ?? get().orderSearch
    const st = status ?? get().orderStatus
    set({ orderPage: p, orderSearch: s, orderStatus: st, orderLoading: true })
    get().loadAll(p)
  },

  setOrderFilterLocal(search, status) {
    set({ orderSearch: search, orderStatus: status, orderPage: 1 })
    get().loadAll(1)
  },

  startPolling() {
    const old = get().poller
    if (old) clearInterval(old)
    get()
      .loadAll()
      .catch(() => {})
    // Try WebSocket first, fall back to polling
    get().connectWebSocket()
    const timer = setInterval(() => {
      // Only poll if WS is not connected
      if (get().wsStatus !== 'connected') {
        get()
          .loadAll()
          .catch(() => {})
      }
    }, POLL_MS)
    set({ poller: timer })
  },

  stopAll() {
    const oldPoller = get().poller
    if (oldPoller) clearInterval(oldPoller)
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
