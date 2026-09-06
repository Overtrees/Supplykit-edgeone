
// 类型定义
interface OrderItem {
  id: number; order_no: string; sku: string; barcode?: string; product_name: string
  store: string; warehouse?: string; quantity: number; unit_price: number
  total_amount: number; order_status: string; ordered_at: string; paid_at?: string; platform?: string
  deleted_at?: string | null
}

interface AppState {
  channel: string; channelVersion: number; pageVersion: number; dataLoaded: boolean
  dashboard: any; orders: OrderItem[]; orderTotal: number; orderPage: number
  inventory: any[]; qualityLogs: any[]; alerts: any[]; stockRisk: any[]; alertCounts: any; bcOutOfStock: any[]
  loading: boolean; orderLoading: boolean; orderLoadErr: string; orderSearch: string; orderStatus: string
  wsStatus: string; ws: WebSocket | null; importLogs: any[]
  hammerPanel: string | null; hammerSearch: string; hammerData: Record<string, any>
  hammerCleansingTarget: string; hammerCleansingConflict: string
  hammerCols: Record<string, string[]> | null
  prodBatch: boolean
  prodSelIds: number[]
  prodFilterLen: number
  prodBatchVersion: number
  prodBatchAllReq: number
  /** 批量面板按钮状态判断: id → is_active(1/0), 页面加载数据后同步 */
  batchStateMap: Record<number, number>
  hammerDashPeriod: string; hammerInsightsTab: string; hammerReplenMode: string
  hammerRulesTab: string; hammerRuleNewVersion: number; hammerRulesMode: string
  hammerWhType: string
  customDateStart: string; customDateEnd: string
}

import { create } from 'zustand'
import { api, clearCache, clearInflight } from '../api/client'

const POLL_MS = Number(import.meta.env.VITE_POLL_INTERVAL_MS || 60000)
const WS_URL = import.meta.env.VITE_WS_URL || ''

// 安全 localStorage 读取（Safari 隐私模式兼容）
const safeGet = (key, def = null) => { try { return localStorage.getItem(key) } catch { return def } }
const safeGetJSON = (key, def = null) => { try { return JSON.parse(localStorage.getItem(key) || 'null') } catch { return def } }

export const useAppStore = create((set, get) => ({
  channel: safeGet('c_channel') || 'jd',
  channelVersion: 0,
  pageVersion: 0,
  dashboard: null,
  orders: [],
  orderTotal: 0,
  orderPage: 1,
  inventory: [],
  qualityLogs: [],
  alerts: [],
  stockRisk: [],
  bcOutOfStock: [],
  alertCounts: null,
  loading: false,  // 统一 loading 状态
  wsStatus: 'idle',
  importLogs: [],
  poller: null,
  ws: null,

  dataVersion: 0,
  bumpDataVersion: () => set((s) => ({ dataVersion: s.dataVersion + 1 })),
  orderSearch: '',
  orderStatus: '',
  orderLoading: false,
  orderLoadErr: '',
  dataLoaded: false,
  sidebarOpen: false,
  setSidebarOpen: (v) => set({ sidebarOpen: v }),
  hammerPanel: null,
  setHammerPanel: (panel) => set({ hammerPanel: panel }),
  hammerSearch: '',
  setHammerSearch: (text) => set({ hammerSearch: text }),
  hammerData: safeGetJSON('c_hammer_data_' + (safeGet('c_channel') || 'jd')) || {},
  hammerWhType: (() => { const _ch0 = safeGet('c_channel') || 'jd'; const _wh0 = safeGet('c_wh_type_' + _ch0) || 'own'; return (_ch0 !== 'jd' && _wh0 === 'platform_b') ? 'own' : _wh0 })(),
  setHammerWhType: (v) => { try { localStorage.setItem('c_wh_type_' + get().channel, v) } catch {} set({ hammerWhType: v }) },
  hammerInsightsTab: 'replen',
  setHammerInsightsTab: (t) => set({ hammerInsightsTab: t }),
  hammerCleansingChannel: 'jd',
  setHammerCleansingChannel: (c) => set({ hammerCleansingChannel: c }),
  hammerCleansingTarget: 'order',
  setHammerCleansingTarget: (t) => set({ hammerCleansingTarget: t }),
  hammerCleansingConflict: 'sum',
  setHammerCleansingConflict: (m) => set({ hammerCleansingConflict: m }),
  hammerRulesTab: 'rules',
  setHammerRulesTab: (t) => set({ hammerRulesTab: t }),
  hammerRuleNewVersion: 0,
  bumpHammerRuleNew: () => set((s) => ({ hammerRuleNewVersion: s.hammerRuleNewVersion + 1 })),
  hammerRulesMode: safeGet('c_replen_mode_' + (safeGet('c_channel') || 'jd')) || ((safeGet('c_channel') || 'jd') === 'jd' ? 'bbcc' : 'traditional'),
  setHammerRulesMode: (m) => { try { localStorage.setItem('c_replen_mode_' + get().channel, m) } catch {} set({ hammerRulesMode: m, hammerReplenMode: m }) },
  hammerDashPeriod: safeGet('c_dash_period_' + (safeGet('c_channel') || 'jd')) || 'month',
  customDateStart: '',
  customDateEnd: '',
  setHammerDashPeriod: (p) => { try { localStorage.setItem('c_dash_period_' + get().channel, p) } catch {} set({ hammerDashPeriod: p, customDateStart: '', customDateEnd: '' }); get().loadAll() },
  setCustomDate: (start, end) => { set({ customDateStart: start, customDateEnd: end, hammerDashPeriod: 'custom' }); get().loadAll() },
  hammerReplenMode: safeGet('c_replen_mode_' + (safeGet('c_channel') || 'jd')) || ((safeGet('c_channel') || 'jd') === 'jd' ? 'bbcc' : 'traditional'),
  setHammerReplenMode: (m) => { try { localStorage.setItem('c_replen_mode_' + get().channel, m) } catch {} set({ hammerReplenMode: m, hammerRulesMode: m }) },
  hammerCols: {},
  setHammerCols: (pageKey, cols) => set((s) => ({ hammerCols: { ...s.hammerCols, [pageKey]: cols } })),
  prodBatch: false,
  setProdBatch: (v) => set({ prodBatch: v }),

  prodSelIds: [],
  prodFilterLen: 0,
  prodBatchVersion: 0,
  batchStateMap: {},
  setProdBatchSel: (ids) => set({ prodSelIds: ids }),
  setProdBatchFilterLen: (n) => set({ prodFilterLen: n }),
  bumpProdBatchVersion: () => set((s) => ({ prodBatchVersion: s.prodBatchVersion + 1 })),
  prodBatchAllReq: 0,
  requestProdBatchAll: () => set((s) => ({ prodBatchAllReq: s.prodBatchAllReq + 1 })),
  setHammerData: (page, data) => {
    const ch = get().channel
    const channelData = get().hammerData[ch] || {}
    const hd = { ...get().hammerData, [ch]: { ...channelData, [page]: data } }
    try { localStorage.setItem('c_hammer_data', JSON.stringify(hd)) } catch {}
    set({ hammerData: hd })
  },
  setChannel: (ch) => { try { localStorage.setItem('c_channel', ch) } catch {} clearCache(); clearInflight(); set({ hammerSearch: '' }); const _wh = safeGet('c_wh_type_' + ch) || 'own'; set({ channel: ch, dataLoaded: false, loading: true, hammerWhType: (ch !== 'jd' && _wh === 'platform_b') ? 'own' : _wh, hammerDashPeriod: safeGet('c_dash_period_' + ch) || 'month', hammerReplenMode: safeGet('c_replen_mode_' + ch) || (ch === 'jd' ? 'bbcc' : 'traditional'), hammerRulesMode: safeGet('c_replen_mode_' + ch) || (ch === 'jd' ? 'bbcc' : 'traditional'), hammerCleansingChannel: ch, prodBatch: false, prodSelIds: [], batchStateMap: {} }); get().loadAll() },
  bumpPageVersion: () => set(s => ({ pageVersion: s.pageVersion + 1 })),

  async loadAll(page, opts) {
    set({ loading: true, orderLoading: true })
    const ch = get().channel
    const s = get().hammerSearch || ''
    const st = get().orderStatus || ''
    const p = page || get().orderPage || 1
    const ds = get().hammerDashPeriod
    const cds = get().customDateStart
    const cde = get().customDateEnd
    // opts.refresh=true: dashboard 请求带 refresh=1（填充/导入完成后强制同步重建，不用旧值）
    var dashUrl = '/api/dashboard/summary'
    if (ds === 'custom' && cds && cde) dashUrl += '?start_date=' + cds + '&end_date=' + cde
    if (opts && opts.refresh) dashUrl += (dashUrl.includes('?') ? '&' : '?') + 'refresh=1'
    try {
      const results = await Promise.allSettled([
        api.get(dashUrl),
        api.get('/api/orders?page=' + p + '&page_size=30&search=' + encodeURIComponent(s) + '&status=' + encodeURIComponent(st)),
        api.get('/api/quality-logs'),
        api.get('/api/alerts'),
        api.get('/api/dashboard/stock-risk'),
      ])
      const [dashboard, orders, qualityLogs, alerts, stockRisk] = results.map(r =>
        r.status === 'fulfilled' ? r.value : { data: null }
      )
      set({
        dashboard: dashboard.data,
        orders: orders.data?.items || orders.data || [],
        orderTotal: orders.data?.total || (orders.data || []).length || 0,
        orderPage: orders.data?.page || p,
                qualityLogs: qualityLogs.data || [],
        alerts: alerts.data || [],
        stockRisk: stockRisk.data || [],
        dataLoaded: true,
        loading: false,
        orderLoading: false,
        orderLoadErr: results[1] && results[1].status === 'rejected' ? '加载失败，可能是网络异常或服务暂不可用' : '',
      })
    } catch (e) {
      console.error('loadAll failed:', e)
      set({ loading: false, orderLoading: false })
    }
  },

  connectWebSocket() {
    const oldWs = get().ws
    if (oldWs) { try { oldWs.close() } catch(e) {} }

    try {
      const ws = new WebSocket(WS_URL)
      ws.onopen = () => {
        set({ wsStatus: 'connected', ws })
        get().loadAll().catch(() => {})
      }
      ws.onmessage = (evt) => {
        // 按事件类型分发: 清洗进度不触发全局刷新(避免导入期间频繁loadAll), 只通知进度
        try {
          const msg = JSON.parse(evt.data || '{}')
          if (msg && msg.type === 'cleansing_progress') {
            window.dispatchEvent(new CustomEvent('cleansing-progress', { detail: msg.payload || msg }))
            return
          }
        } catch {}
        // 其他 WS 事件 → reload data for real-time updates
        get().loadAll().catch(() => {})
        get().bumpDataVersion()
      }
      ws.onclose = () => {
        set({ wsStatus: 'polling', ws: null })
        setTimeout(() => connectWebSocket(), 10000)
      }
      ws.onerror = () => {
        set({ wsStatus: 'polling', ws: null })
        setTimeout(() => connectWebSocket(), 10000)
      }
    } catch(e) {
      set({ wsStatus: 'polling', ws: null })
    }
  },

  addImportLog(item) {
    set((state) => ({ importLogs: [item, ...state.importLogs].slice(0, 20) }))
  },

  setOrderPage(p, search, status) {
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
    get().loadAll().catch(() => {})
    // Try WebSocket first, fall back to polling
    get().connectWebSocket()
    const timer = setInterval(() => {
      // Only poll if WS is not connected
      if (get().wsStatus !== 'connected') {
        get().loadAll().catch(() => {})
      }
    }, POLL_MS)
    set({ poller: timer })
  },

  stopAll() {
    const oldPoller = get().poller
    if (oldPoller) clearInterval(oldPoller)
    const oldWs = get().ws
    if (oldWs) { try { oldWs.close() } catch(e) {} }
    set({ poller: null, ws: null })
  },
}))
