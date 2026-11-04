import React, { useEffect, useState, useRef } from 'react'
import { api, abortScope } from '../api/client'
import { mutate } from '../api/mutate'
import { useFreshness } from '../components/Freshness'
import { useToast } from '../components/Toast'
import { useAppStore } from '../store/useAppStore'

import { defVis, defVisTrad, getVis } from './insights/shared'
import PurchaseTab from './insights/PurchaseTab'
import SlowTab from './insights/SlowTab'
import ReplenTab from './insights/ReplenTab'

// ── 列配置 ──────────────────────────────────────────────────────────────
import {
  INS_BBCC_COLS,
  INS_TRAD_COLS,
  INS_PURCHASE_COLS,
  INS_SLOW_COLS,
} from '../components/hammer/configs'
const BBCC_COLS = INS_BBCC_COLS
const TRAD_COLS = INS_TRAD_COLS
const PURCHASE_COLS = INS_PURCHASE_COLS
const SLOW_COLS = INS_SLOW_COLS

// 2026-10-27 页面快照(切回秒显旧值/后台静默更新; 写事件清——不看旧): 建议页三态
let _snapIns: { replen?: any[]; purchase?: any[]; disposals?: any[] } = {}

export default function InsightsPage() {
  const toast = useToast()
  const fresh = useFreshness(useAppStore.getState().channel) // 2026-10-29 新鲜度
  const [replen, setReplen] = useState(() => _snapIns.replen || [])
  const [purchase, setPurchase] = useState(() => _snapIns.purchase || [])
  const [slowMoving] = useState([])
  // 滞销处置建议（SKU×仓库粒度 + 批量处置）
  const [disposals, setDisposals] = useState(() => _snapIns.disposals || [])
  const [disposalsLoading, setDisposalsLoading] = useState(true)
  const [slowPage, setSlowPage] = useState(1)
  const [slowTotal, setSlowTotal] = useState(0)
  const [slowLoadingMore, setSlowLoadingMore] = useState(false)
  const slowSentinelRef = useRef(null)
  const slowLoadingRef = useRef(false)
  // 快照写回(数据每次 set 同步到 module, 切回可先显)
  useEffect(() => {
    _snapIns.replen = replen
  }, [replen])
  useEffect(() => {
    _snapIns.purchase = purchase
  }, [purchase])
  useEffect(() => {
    _snapIns.disposals = disposals
  }, [disposals])
  const [showDisposed, setShowDisposed] = useState(false)

  // 各区块加载状态
  const [replenLoading, setReplenLoading] = useState(true)
  const [purchaseLoading, setPurchaseLoading] = useState(true)
  const [, setSlowLoading] = useState(true)
  const [, setReplenLimit] = useState(100)
  const [replenTotal, setReplenTotal] = useState(0)
  const [, setReplenPage] = useState(1)
  const replenPageRef = useRef(1)
  const orderedOkRef = useRef(false) // 本会话已下单是否成功加载过(失败收敛用)
  const [replenLoadingMore, setReplenLoadingMore] = useState(false)
  const [purchaseLimit, setPurchaseLimit] = useState(50)
  const [, setSlowLimit] = useState(50)

  const {
    channel: globalChannel,
    hammerInsightsTab: tab,
    hammerReplenMode,
    setHammerReplenMode,
    hammerCols,
    hammerData,
    dataVersion,
    prodBatch,
    prodSelIds,
    setProdBatch,
    setProdBatchSel,
    prodBatchAllReq,
  } = useAppStore()
  useEffect(() => {
    setProdBatch(false)
    setProdBatchSel([])
  }, [globalChannel, tab])
  // 批量模式全选(断言: 锤子面板"全选" requestProdBatchAll → 全选当前过滤列表)
  useEffect(() => {
    if (prodBatchAllReq > 0) {
      const s = useAppStore.getState()
      const all = filteredDisp.map(x => x.sku + '|' + x.warehouse)
      s.setProdBatchSel(s.prodSelIds.length === all.length && all.length > 0 ? [] : all)
    }
  }, [prodBatchAllReq])
  const replenMode =
    globalChannel !== 'jd' && hammerReplenMode === 'bbcc' ? 'traditional' : hammerReplenMode
  const currentCols = replenMode === 'bbcc' ? BBCC_COLS : TRAD_COLS
  const [visCols, setVisCols] = useState(() => {
    let saved = getVis(replenMode, globalChannel)
    const defaultCols = replenMode === 'bbcc' ? defVis(BBCC_COLS) : defVisTrad(TRAD_COLS)
    if (saved) {
      // 过滤掉已不存在的列ID（如旧版 combined_turn → cur_turn）
      const validIds = currentCols.map(function (c) {
        return c.id
      })
      saved = saved.filter(function (id) {
        return validIds.includes(id)
      })
      if (saved.length === 0) saved = defaultCols
    } else {
      saved = defaultCols
    }
    return saved
  })
  // 搜索：按 tab 和模式隔离
  const searchKey =
    tab === 'purchase'
      ? 'insights_search_purchase'
      : tab === 'slow'
        ? 'insights_search_slow'
        : 'insights_search_' + replenMode
  const insightSearch = hammerData?.[globalChannel]?.[searchKey] || ''
  // 搜索时重置分页
  useEffect(
    function () {
      setReplenLimit(50)
      setPurchaseLimit(50)
      setSlowLimit(50)
    },
    [insightSearch],
  )
  // 搜索变更→重新拉取(带search到后端, 避免只过滤已加载前100条致'搜不到')
  useEffect(
    function () {
      if (!replenLoading && !replenLoadingMore) {
        loadReplen(replenMode, globalChannel, 1)
      }
    },
    [insightSearch, replenMode],
  )
  const filterBySearch = items => {
    if (!insightSearch) return items
    const q = insightSearch.toLowerCase()
    return items.filter(
      x =>
        (x.sku || '').toLowerCase().includes(q) ||
        (x.product_name || '').toLowerCase().includes(q) ||
        (x.barcode || '').toLowerCase().includes(q),
    )
  }
  const filteredReplen = filterBySearch(Array.isArray(replen) ? replen : [])
  const filteredPurchase = filterBySearch(Array.isArray(purchase) ? purchase : [])
  const filteredSlow = filterBySearch(Array.isArray(slowMoving) ? slowMoving : [])
  // 滞销处置数据（SKU×仓库）融合进表格: 过滤已处理(除非查看已处置)
  const filteredDisp = (Array.isArray(disposals) ? disposals : [])
    .filter(x => showDisposed || !x.disposed)
    .filter(x => {
      if (!insightSearch) return true
      const q = insightSearch.toLowerCase()
      return (
        (x.sku || '').toLowerCase().includes(q) || (x.product_name || '').toLowerCase().includes(q)
      )
    })
  const [purchaseVisCols, setPurchaseVisCols] = useState(() => PURCHASE_COLS.map(c => c.id))
  const [slowVisCols, setSlowVisCols] = useState(() => SLOW_COLS.map(c => c.id))
  const reqSeq = useRef(0)
  const replenSeq = useRef(0)

  // 规则/参数保存或任务完成 → 即时刷新当前 tab(补货/采购/滞销联动)
  useEffect(() => {
    const h = () => {
      const ch = useAppStore.getState().channel || 'jd'
      const _tab = useAppStore.getState().hammerInsightsTab || 'replen'
      if (_tab === 'purchase') {
        useAppStore.getState().setSyncing(1)
        useAppStore.getState().setSyncOld(purchase.length > 0)
        api
          .get(
            '/api/insights/purchase?days=28&mode=' +
              (useAppStore.getState().hammerReplenMode || 'bbcc') +
              '&channel=' +
              ch,
          )
          .then(r => setPurchase(r.data?.suggestions || r.data || []))
          .catch(() => {})
          .finally(() => useAppStore.getState().setSyncing(-1))
      } else if (_tab === 'slow') {
        useAppStore.getState().setSyncing(1)
        useAppStore.getState().setSyncOld(disposals.length > 0)
        api
          .get('/api/insights/disposal-suggestions?channel=' + ch + '&page=1&page_size=100')
          .then(r => {
            const d = r.data || {}
            setDisposals(d.items || d || [])
            setSlowTotal(d.total || (d.items || []).length || 0)
          })
          .catch(() => {})
          .finally(() => useAppStore.getState().setSyncing(-1))
      } else {
        loadReplen(useAppStore.getState().hammerReplenMode || 'bbcc', ch, 1)
      }
    }
    // 写事件: 数据已变更, 旧快照不可信(清, 宁骨架不旧)
    _snapIns = {}
    window.addEventListener('rules-changed', h)
    window.addEventListener('insights-refresh', h)
    return () => {
      window.removeEventListener('rules-changed', h)
      window.removeEventListener('insights-refresh', h)
      // 2026-10-27 切走取消在途(根治快速切换卡骨架): 该页未完成请求 abort, 切回不再复用旧 promise
      abortScope('insights')
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    const saved = hammerCols?.['insights_' + replenMode]
    if (saved) {
      const validIds = currentCols.map(function (c) {
        return c.id
      })
      const filtered = saved.filter(function (id) {
        return validIds.includes(id)
      })
      setVisCols(
        filtered.length > 0
          ? filtered
          : replenMode === 'bbcc'
            ? defVis(BBCC_COLS)
            : defVisTrad(TRAD_COLS),
      )
    } else {
      const ls = getVis(replenMode, globalChannel)
      if (ls) setVisCols(ls)
      else setVisCols(replenMode === 'bbcc' ? defVis(BBCC_COLS) : defVisTrad(TRAD_COLS))
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
    useAppStore.getState().setSyncing(1)
    useAppStore.getState().setSyncOld(replen.length > 0) // 有旧值→同步中/首载→加载中
    // 2026-10-27 根治: 整表重拉(page=1)前记滚动锚点, 数据落地后还原——防 iOS Safari
    // 地址栏随高度突变联动导致"顶部白条/内容错位"(下单→insights-refresh 曾触发整表重拉+滚动偏移)
    const _sc = page === 1 ? (document.scrollingElement || document.documentElement).scrollTop : 0
    if (page === 1) setReplenLoading(true)
    else setReplenLoadingMore(true)
    setReplenError('')
    try {
      const r = await api.get(
        '/api/insights/replenishment?days=28&mode=' +
          mode +
          '&channel=' +
          ch +
          '&page=' +
          page +
          '&page_size=100&search=' +
          encodeURIComponent(insightSearch),
        { timeout: 90000, scope: 'insights' },
      )
      if (seq !== replenSeq.current) return // 竞态丢弃
      const data = r.data
      let total = 0
      let items = []
      // 分页结构 {items,total}
      if (data && typeof data === 'object' && Array.isArray(data.items)) {
        items = data.items
        total = data.total || items.length
      } else if (Array.isArray(data)) {
        items = data
        total = data.length
      }
      setReplen(prev => (page === 1 ? items : [...prev, ...items]))
      setReplenTotal(total)
      setReplenPage(page)
      replenPageRef.current = page
      setReplenLoading(false)
      setReplenLoadingMore(false)
      // 滚动锚点还原(page=1 重拉后, 双 rAF 等布局稳定)
      if (page === 1 && _sc > 0) {
        requestAnimationFrame(() =>
          requestAnimationFrame(() => {
            const _el = document.scrollingElement || document.documentElement
            _el.scrollTop = _sc
          }),
        )
      }
    } catch (e) {
      console.error('loadReplen:', e)
      if (seq === replenSeq.current) {
        if (page === 1) setReplen([])
        setReplenLoading(false)
        setReplenLoadingMore(false)
        setReplenError(
          e && (e.message || e.statusText) ? String(e.message || e.statusText) : String(e),
        )
      }
    } finally {
      // 2026-10-27 根治: settle 唯一在 finally(return 竞态/abort/catch/正常尾必执行——曾散点被 return 跳过致永久'加载中')
      useAppStore.getState().setSyncing(-1)
      if (seq === replenSeq.current) setReplenLoading(false)
    }
  }

  // 从后端加载已下单标记（按渠道隔离）——SSOT: 页面进入/操作后/轮询都以 GET 全量为准
  // 2026-10-27 失败收敛: silent(轮询)失败保留现值; 首载从未成功才用 localStorage fallback(离线首进);
  // 已有成功值失败 → 保留现值不覆盖(防旧 fallback 误导"已下单"角标)
  const loadOrdered = async (silent = false) => {
    try {
      const r = await api.get('/api/purchase-orders?channel=' + globalChannel)
      const items = r.data || []
      // 存两份：orderedKeys 用于快速判断，orderedItems 用于展示详情
      setOrderedKeys(items.map(x => x.sku + '|' + x.store))
      setOrderedItems(items)
      orderedOkRef.current = true
    } catch (e) {
      if (silent) return // 轮询失败: 保留现值, 下轮再试(不打断当前展示)
      if (!orderedOkRef.current) {
        try {
          const fallback = JSON.parse(localStorage.getItem('c_ordered_' + globalChannel) || '[]')
          setOrderedKeys(fallback)
        } catch {
          setOrderedKeys([])
        }
        // 2026-10-26: 拉取失败 → 清空明细(防乐观无 id 项残留 → 日期更新 PUT /undefined 422)
        setOrderedItems([])
      }
      // else: 已有成功值(本会话加载过)→ 保留现值, 不覆盖不误导
    }
  }

  // 2026-10-27 停留期实时: 建议页补货(tab)且京东(BBCC 有已下单)时, 页面可见状态下每 30s 静默轮询
  // 已下单列表(0.5 RU/次实测, 5人×8h×30天 ≈7.2万RU/月 = 额度0.14%/常态耗0.5%, RU 预算内)——多设备变更 ≤30s 收敛
  useEffect(() => {
    if (tab !== 'replen' || globalChannel !== 'jd') return
    const _tid = setInterval(() => {
      if (document.visibilityState === 'visible') loadOrdered(true)
    }, 30000)
    return () => clearInterval(_tid)
  }, [tab, globalChannel])

  const [orderedKeys, setOrderedKeys] = useState([])
  const [orderedItems, setOrderedItems] = useState([])

  // ─── BBCC 模式「已下单」功能 ──────────────────────────────────────────
  // 业务含义：京东 B 仓入库批次标记。点击「下单」= 给该 SKU 打上 B 仓入库批次
  // 标记，再填写「到 B 仓日期」，用于监控在库天数（避免超储被京东收取仓储费）。
  // 仅 BBCC 模式展示（replenMode==='bbcc' 控制），按渠道隔离持久化。
  const toggleOrdered = async (sku, store, product_name?, suggested_qty?) => {
    const key = sku + '|' + store
    const isOrdered = orderedKeys.includes(key)
    const rowBackup = orderedItems.find(x => x.sku === sku && x.store === store)
    // 乐观更新(满足即时反馈): 点击立即改 state; 成功 => 后端真实行替换 + 提示;
    // 失败 => 本地回滚到后端事实 + 提示(不做 loadOrdered GET 兜底——2026-10-27 用户拍板)
    if (isOrdered) {
      setOrderedKeys(prev => prev.filter(k => k !== key))
      setOrderedItems(prev => prev.filter(x => x.sku !== sku || x.store !== store))
      // 2026-11-03 走 mutate: 原来只弹 toast, 既不清缓存也不通知其它页 ——
      //   而采购建议接口会关闭 purchase_need 告警, 看板计数因此滞后到下一次 30s 静默刷新。
      mutate({
        label: 'ip.cancel_order',
        run: () =>
          api.delete(
            '/api/purchase-orders?sku=' +
              encodeURIComponent(sku) +
              '&store=' +
              encodeURIComponent(store) +
              '&channel=' +
              globalChannel,
          ),
        success: 'ip.cancel_ordered',
        errorPrefix: 'ip.cancel_failed',
        events: ['insights-refresh', 'rules-changed'],
      }).then(ok => {
        if (ok) return
        // 失败: 本地回滚(恢复该行)——状态立即回到后端事实; 不重拉
        setOrderedKeys(prev => (prev.includes(key) ? prev : [...prev, key]))
        setOrderedItems(prev =>
          rowBackup && !prev.some(x => x.sku === sku && x.store === store)
            ? [...prev, rowBackup]
            : prev,
        )
      })
    } else {
      const newItem = {
        sku,
        store,
        product_name: product_name || '',
        suggested_qty: suggested_qty || 0,
        arrival_date: '',
      }
      setOrderedKeys(prev => [...prev, key])
      setOrderedItems(prev => [...prev, newItem])
      mutate({
        label: 'ip.place_order',
        run: () =>
          api.post(
            '/api/purchase-orders?sku=' +
              encodeURIComponent(sku) +
              '&store=' +
              encodeURIComponent(store) +
              '&product_name=' +
              encodeURIComponent(product_name || '') +
              '&suggested_qty=' +
              (suggested_qty || 0) +
              '&channel=' +
              globalChannel,
          ),
        success: 'ip.placed',
        errorPrefix: 'ip.place_failed',
        events: ['insights-refresh', 'rules-changed'],
      }).then(r => {
        if (!r) {
          // 失败: 本地回滚(移除乐观项)——状态立即回到后端事实; 不重拉
          setOrderedKeys(prev => prev.filter(k => k !== key))
          setOrderedItems(prev => prev.filter(x => !(x.sku === sku && x.store === store)))
          return
        }
        // 后端真实行(含 id)→ 同键替换乐观项(日期更新 PUT 安全)
        const real = (r as { data?: { id?: number } })?.data
        const row =
          real && real.id != null
            ? real
            : {
                sku,
                store,
                product_name: product_name || '',
                suggested_qty: suggested_qty || 0,
                arrival_date: '',
              }
        setOrderedItems(prev => prev.map(x => (x.sku === sku && x.store === store ? row : x)))
      })
    }
  }

  // 设置到 B 仓日期（入库批次生效日，用于计算在库天数监控超储）
  const setArrivalDate = async (item, date) => {
    // 防御: item.id 缺失(乐观下单项且 loadOrdered 失败残留)→ 重拉真实列表, 不发 /undefined PUT
    if (!item || item.id == null) {
      loadOrdered()
      return
    }
    // 乐观更新
    setOrderedItems(prev => prev.map(x => (x.id === item.id ? { ...x, arrival_date: date } : x)))
    mutate({
      label: 'ip.update_arrival',
      run: () => api.put('/api/purchase-orders/' + item.id, { arrival_date: date }),
      events: ['insights-refresh', 'rules-changed'],
      errorPrefix: 'ip.update_failed',
    }).then(ok => {
      if (!ok) loadOrdered()
    })
  }

  const todayStr = new Date().toISOString().slice(0, 10)

  useEffect(() => {
    useAppStore.getState().setProdBatchSel([])
    const seq = ++reqSeq.current
    const mode = globalChannel === 'jd' ? replenMode : 'traditional'
    if (globalChannel !== 'jd' && replenMode === 'bbcc') setHammerReplenMode('traditional')
    if (tab === 'purchase') {
      // 仅拉采购建议
      setPurchaseLoading(true)
      api
        .get(
          '/api/insights/purchase?days=28&mode=' +
            replenMode +
            '&channel=' +
            globalChannel +
            '&search=' +
            encodeURIComponent(insightSearch),
        )
        .then(r => {
          if (seq !== reqSeq.current) {
            setPurchaseLoading(false)
            return
          }
          setPurchase(r.data?.suggestions || r.data || [])
          setPurchaseLoading(false)
        })
        .catch(() => setPurchaseLoading(false))
    } else if (tab === 'slow') {
      // 仅拉滞销处置建议（分页，第1页）
      setDisposalsLoading(true)
      setSlowPage(1)
      api
        .get(
          '/api/insights/disposal-suggestions?channel=' +
            globalChannel +
            '&page=1&page_size=100&search=' +
            encodeURIComponent(insightSearch),
        )
        .then(r => {
          if (seq !== reqSeq.current) {
            setDisposalsLoading(false)
            return
          }
          const d = r.data || {}
          const items = d.items || d || []
          setDisposals(items)
          setSlowTotal(d.total || items.length || 0)
          setDisposalsLoading(false)
        })
        .catch(() => setDisposalsLoading(false))
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
    api
      .get(
        '/api/insights/disposal-suggestions?channel=' +
          globalChannel +
          '&page=' +
          (slowPage + 1) +
          '&page_size=100&search=' +
          encodeURIComponent(insightSearch),
      )
      .then(r => {
        const d = r.data || {}
        const items = d.items || []
        setDisposals(prev => [...prev, ...items])
        setSlowPage(prev => prev + 1)
        setSlowTotal(d.total || slowTotal)
        slowLoadingRef.current = false
        setSlowLoadingMore(false)
      })
      .catch(() => {
        slowLoadingRef.current = false
        setSlowLoadingMore(false)
      })
    // 不 reobserve——IntersectionObserver 触发后表增长哨兵下移(状态变化),
    // 用户滚动再进入视口触发(同补货模式, 无循环无卡)
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {/* 补货建议 */}
      {tab === 'replen' && (
        <ReplenTab
          replen={replen}
          replenLoading={replenLoading}
          replenTotal={replenTotal}
          replenLoadingMore={replenLoadingMore}
          replenError={replenError}
          filteredReplen={filteredReplen}
          currentCols={currentCols}
          visCols={visCols}
          replenMode={replenMode}
          orderedKeys={orderedKeys}
          orderedItems={orderedItems}
          loadReplen={loadReplen}
          toggleOrdered={toggleOrdered}
          setArrivalDate={setArrivalDate}
          replenPageRef={replenPageRef}
          insightSearch={insightSearch}
          globalChannel={globalChannel}
          fresh={fresh}
        />
      )}

      {/* 采购建议 */}
      {tab === 'purchase' && (
        <PurchaseTab
          purchase={purchase}
          filteredPurchase={filteredPurchase}
          purchaseLoading={purchaseLoading}
          purchaseLimit={purchaseLimit}
          setPurchaseLimit={setPurchaseLimit}
          purchaseVisCols={purchaseVisCols}
          insightSearch={insightSearch}
          replenMode={replenMode}
          globalChannel={globalChannel}
        />
      )}

      {/* 滞销预警 */}
      {tab === 'slow' && (
        <SlowTab
          disposals={disposals}
          disposalsLoading={disposalsLoading}
          filteredDisp={filteredDisp}
          showDisposed={showDisposed}
          setShowDisposed={setShowDisposed}
          slowVisCols={slowVisCols}
          slowTotal={slowTotal}
          slowLoadingMore={slowLoadingMore}
          slowLoadingRef={slowLoadingRef}
          slowSentinelRef={slowSentinelRef}
          loadSlowMore={loadSlowMore}
          insightSearch={insightSearch}
          prodSelIds={prodSelIds}
          prodBatch={prodBatch}
        />
      )}
    </div>
  )
}
