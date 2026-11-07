import React, { useCallback, useState, useMemo, useEffect, useRef } from 'react'
import { daysSinceCn } from '../utils/date'
import { api, clearCache, abortScope, apiJson, isCanceled } from '../api/client'
import { useFreshness, StaleDot } from '../components/Freshness'
import EmptyState from '../components/EmptyState'
import ErrorRetry from '../components/ErrorRetry'
import { IconLoading, IconCircle, IconCircleCheck } from '../components/Icons'
import { useToast } from '../components/Toast'
import { useAppStore } from '../store/useAppStore'
import { INV_COLS } from '../components/hammer/configs'
import { colLabel } from '../components/colLabels'
import {
  batchUseLabel,
  expiryActionLabel,
  expiryStatusColor,
  expiryStatusLabel,
} from '../components/statusLabels'
import { t } from '../locale'

const API = import.meta.env.VITE_API_BASE_URL || ''
const COL_KEY = 'c_cols_inventory'
const getVis = (wt, ch) => {
  try {
    return JSON.parse(localStorage.getItem(COL_KEY + '_' + ch + '_' + wt) || 'null')
  } catch {
    return null
  }
}

interface InventoryPageProps {
  highlightSku?: string
  highlightWarehouse?: string
}

/** with-sales 列表响应 / 批次删除响应(动态字段走 unknown 索引签名, 边界处收敛) */
interface InvRowData {
  month_start?: string
  month_end?: string
  month_in_end?: string
  month_out_end?: string
  // 2026-11-04 账期: 当月未满时 period_days = 已过天数(< month_days), 表头要标"月内累计"
  period_days?: number
  month_days?: number
  is_open?: boolean
  [k: string]: unknown
}
interface WithSalesResp {
  summary?: unknown
  items?: InvRowData[]
  total?: number
  [k: string]: unknown
}
interface BatchDeleteResp {
  ok?: boolean
  error?: string
  data?: { deleted?: number; missing?: unknown[] }
}

// 2026-10-27 页面快照(切回秒显旧/后台静默更新; 无写事件场景): 进销存
let _snapInv = null

export default function InventoryPage({ highlightSku, highlightWarehouse }: InventoryPageProps) {
  const fresh = useFreshness(useAppStore.getState().channel) // 2026-10-29 新鲜度
  const [batchOpen, setBatchOpen] = useState([])
  const [batchData, setBatchData] = useState({})
  const [batchLoading, setBatchLoading] = useState({})
  const toggleBatch = x => {
    const key = x.sku + '|' + x.warehouse
    setBatchOpen(prev => (prev.includes(key) ? prev.filter(k => k !== key) : [...prev, key]))
    if (!batchData[key] && !batchLoading[key]) {
      setBatchLoading(prev => ({ ...prev, [key]: true }))
      api
        .get(
          '/api/batches?channel=' +
            (x.channel || 'jd') +
            '&sku=' +
            encodeURIComponent(x.sku) +
            '&warehouse=' +
            encodeURIComponent(x.warehouse) +
            '&warehouse_type=' +
            encodeURIComponent(whType),
        )
        .then(r => {
          setBatchData(prev => ({ ...prev, [key]: r.data || [] }))
          setBatchLoading(prev => ({ ...prev, [key]: false }))
        })
        .catch(() => setBatchLoading(prev => ({ ...prev, [key]: false })))
    }
  }
  const toast = useToast()
  const [inventory, setInventory] = useState(() => _snapInv || [])
  const [loading, setLoading] = useState(true)
  const [invPage, setInvPage] = useState(1)
  const invPageRef = useRef(1)
  const [invTotal, setInvTotal] = useState(0)
  const [loadingMore, setLoadingMore] = useState(false)
  const [loadErr, setLoadErr] = useState('')
  // 2026-10-30 全量汇总(该维度所有 SKU): 底部总计不用"已加载行之和"
  const [invSummary, setInvSummary] = useState(null)
  // 2026-10-30 刷新失败: 复用现有 syncing 胶囊 + loadErr(不新造状态机)
  const invLenRef = useRef(0)
  useEffect(() => {
    invLenRef.current = inventory.length
  }, [inventory, invSummary])
  const [visCols, setVisCols] = useState([])
  useEffect(() => {
    _snapInv = inventory
  }, [inventory])
  // 切走取消在途(根治快速切换卡骨架)
  useEffect(() => () => abortScope('inventory'), [])
  // 表头区间(自然月闭合 11-01 至 11-30; 各 SKU 流水末日不同, 不按各自末日显示)
  const [inRange, setInRange] = useState('')
  const [outRange, setOutRange] = useState('')
  // 账期是否"月内未满"(true → 周转按已过天数折算, 表头标注)
  const [ledgerOpen, setLedgerOpen] = useState<{ d: number; m: number } | null>(null)
  const reqSeq = useRef(0)
  const {
    channel: globalChannel,
    hammerWhType,
    hammerCols,
    hammerSearch,
    invBatch,
    invSelIds,
  } = useAppStore()
  const whType = hammerWhType
  useEffect(() => {
    if (visCols.length === 0) {
      // 2026-10-30 新增列自动可见: localStorage 里存的是旧列集, 直接用它会让新列(keep_days)永不出现
      const _saved = getVis(whType, globalChannel)
      const _all = INV_COLS[whType].map(c => c.id)
      // 仅自有仓维度存在的列(用户定); 其他维度不追加。
      // 2026-11-04 改成清单: 原实现只处理 keep_days 一列, 新增期末/平均库存后
      //   老用户 localStorage 里的旧列集会让新列永不出现
      const _ownOnly = whType === 'own' ? ['keep_days', 'end_stock', 'avg_stock'] : []
      const _missing = _ownOnly.filter(id => _all.includes(id) && !(_saved || []).includes(id))
      if (!_saved || !_saved.length) {
        setVisCols(_all)
      } else {
        // 新列必须插到**定义位置**, 不能一律追加到末尾 —— 否则「期末/平均」跑到在库金额后面,
        //   期初→入→出→期末→平均→周转 的对账链被拆开, 用户还得自己去挪列
        const _cols = _saved.filter(id => _all.includes(id))
        for (const id of _missing) {
          const pos = _all.indexOf(id)
          let at = _cols.length
          for (let i = 0; i < _cols.length; i++) {
            if (_all.indexOf(_cols[i]) > pos) {
              at = i
              break
            }
          }
          _cols.splice(at, 0, id)
        }
        setVisCols(_cols)
      }
      return
    }
  }, [globalChannel])

  useEffect(() => {
    const saved = hammerCols?.['inventory_' + whType]
    if (saved) setVisCols(saved)
    else {
      const ls = getVis(whType, globalChannel)
      if (ls) setVisCols(ls)
      else setVisCols(INV_COLS[whType].map(c => c.id))
    }
  }, [hammerCols, whType])

  const s = hammerSearch || ''
  const loadInv = async (p?) => {
    const seq = ++reqSeq.current
    useAppStore.getState().setSyncing(1)
    useAppStore.getState().setSyncOld(inventory.length > 0) // 有旧值→同步中/首载→加载中
    if (p === 1) setLoading(true)
    else setLoadingMore(true)
    try {
      const r = await api.get<WithSalesResp>(
        '/api/insights/with-sales?wh_type=' +
          whType +
          '&channel=' +
          globalChannel +
          '&mode=' +
          useAppStore.getState().hammerReplenMode +
          '&page=' +
          p +
          '&page_size=' +
          100 +
          '&search=' +
          encodeURIComponent(s),
        { timeout: 90000, scope: 'inventory' },
      )
      if (seq !== reqSeq.current) {
        // 2026-11-05 修: 竞态丢弃分支原本会 setLoading(false) —— 但"被丢弃"意味着**已经
        //   有更新的一次请求在跑**, 那次的 loading 是 true, 这里把它刷成 false 会让列表先
        //   闪一下空白骨架再被新数据填上(用户看到的就是"加载中 → 空白 → 重试/数据"跳变)。
        //   只有胜出的那次才有权改 loading。
        return
      }
      setLoadErr('')
      const d: WithSalesResp = r.data || {}
      if (p === 1 && d.summary) setInvSummary(d.summary)
      const items = (d.items || d || []) as InvRowData[]
      setInvTotal(d.total || items.length || 0)
      setInvPage(p)
      invPageRef.current = p
      setInventory(prev => (p === 1 ? items : [...prev, ...items]))
      if (p === 1 && items.length > 0) {
        // 表头区间按**自然月闭合**(11-01 至 11-30), 入库/出库同一区间:
        //   各 SKU 流水覆盖天数不同, 按"各自最后一天"显示会让同页两列区间不一致
        const s = items[0].month_start?.slice(5) || ''
        const e = items[0].month_end?.slice(5) || s
        setInRange(`${s}${t('col.range_to')}${e}`)
        setOutRange(`${s}${t('col.range_to')}${e}`)
        // 账期口径(2026-11-04): 当月未满时周转按"已过天数"折算, 表头必须把这件事说出来,
        //   否则用户会拿 4 天的周转跟整月的比, 以为数据错了
        setLedgerOpen(
          items[0].is_open
            ? { d: Number(items[0].period_days) || 0, m: Number(items[0].month_days) || 0 }
            : null,
        )
      }
    } catch (e) {
      if (isCanceled(e)) return // 切仓型/搜索/渠道时主动取消旧请求
      if (seq !== reqSeq.current) return
      if (invLenRef.current > 0) {
        // 有旧数据: 保留列表(不清空) + loadErr 作非阻塞提示; syncing 胶囊由 finally 归零
        setLoadErr(t('inv.load_fail_keep'))
      } else {
        setInventory([])
        setLoadErr(t('common.load_failed'))
      }
    } finally {
      // 根治: settle 唯一在 finally(return 竞态/abort/catch/正常尾必执行——曾散点被 return 跳过致永久'加载中')
      useAppStore.getState().setSyncing(-1)
    }
    if (seq === reqSeq.current) {
      setLoading(false)
      setLoadingMore(false)
    }
  }
  useEffect(() => {
    // 2026-11-07 最新请求优先: 旧筛选的整页读还在跑就先取消(它要 1~2s, 回来只会写旧数据)
    abortScope('inventory')
    clearCache('with-sales')
    setInvPage(1)
    loadInv(1)
    // 2026-10-30 去掉 highlightSku 依赖: 从告警/断货卡跳转时 highlightSku 与 s 同时变化 →
    //   原依赖会触发第二次 effect(再 clearCache 会 abort 上一次 in-flight 请求) →
    //   catch 里 setInventory([]) → 页面空白(用户实证"跳转进销存没数据")
    //   s(搜索) 变化已能覆盖"按 SKU 定位"场景
  }, [whType, globalChannel, s])
  // 从告警跳转: 高亮 SKU(可带具体仓)滚动到可视区(等数据渲染后, 多页时也定位);
  // 数据首次到达检查一次, 未命中 → 提示(空 warehouse_type 存量告警/维度不匹配兜底)
  const locateCheckedRef = useRef(false)
  useEffect(() => {
    if (!highlightSku || locateCheckedRef.current) return
    if (inventory.length === 0) return // 数据未加载完, 等 inventory 变化再触发
    // 2026-10-30 修复竞态误报: 跳转瞬间 inventory 还是**上一次的列表**(新请求未回) →
    //   原逻辑立刻判"找不到"并弹「可能不在当前仓库维度」, 几秒后新数据到达才出现高亮。
    //   现在: ①加载中不做判定 ②列表必须已包含搜索词(说明是本次跳转后的新数据) 才判定
    if (loading) return
    const _has = !s || inventory.some(x => String(x.sku || '').includes(s))
    if (!_has) {
      locateCheckedRef.current = true
      toast.error(t('inv.not_found', { sku: highlightSku }))
      return
    }
    locateCheckedRef.current = true
    const _tm = setTimeout(() => {
      try {
        let el = null
        if (highlightWarehouse) {
          el = document.getElementById('hl-' + highlightSku + '-' + highlightWarehouse)
        }
        if (!el) el = document.querySelector('[id^="hl-' + highlightSku + '-"]') // 宽匹配: SKU 任意仓行
        if (el) el.scrollIntoView({ block: 'center', behavior: 'smooth' })
      } catch (e) {}
    }, 300)
    return () => clearTimeout(_tm)
  }, [highlightSku, highlightWarehouse, inventory, whType, loading, s])
  const handleScroll = e => {
    const el = e.target
    if (
      el.scrollTop + el.clientHeight >= el.scrollHeight - 200 &&
      !loadingMore &&
      inventory.length > 0 &&
      (!invTotal || inventory.length < invTotal)
    ) {
      loadInv(invPage + 1)
    }
  }

  const fl = useMemo(() => inventory, [inventory])

  // 2026-11-04 仓周转(用户定案, 平均库存法):
  //   总期末 = Σ期初 + Σ当月入库 - Σ当月出库
  //   总平均库存 = (Σ期初 + 总期末) / 2
  //   仓周转 = 总平均库存 × 当月自然天数 / Σ当月出库
  //   期初取导入原值(beginning_stock_raw / summary.beg), 不做反推
  const totalTurnover = useMemo(() => {
    // 2026-11-05 平台仓/ B 仓不做进销存推算(用户定案): 该维度没有期初口径、没有流水,
    //   月入出恒 0 → 仓周转算出来只有假值。列集本就没有 turnover 列,
    //   这里再设一道护栏, 将来若给平台仓加列也不会显示 0 天。
    if (whType !== 'own') return null
    // 优先后端全量汇总(该维度所有 SKU); 无 summary(搜索/翻页)时回退已加载行
    const beg = invSummary
      ? invSummary.beg || 0
      : inventory.reduce((s, x) => s + (x.beginning_stock_raw || 0), 0)
    const mi = invSummary
      ? invSummary.mi || 0
      : inventory.reduce((s, x) => s + (x.month_inbound || 0), 0)
    const mo = invSummary
      ? invSummary.mo || 0
      : inventory.reduce((s, x) => s + (x.month_outbound || 0), 0)
    if (mo <= 0 || inventory.length === 0) return null
    // 期末不兜底: 仓总平均库存为负时, 仓周转如实为负并标异常(见渲染处)
    const end = beg + mi - mo
    const avg = (beg + end) / 2
    // 期间天数由后端给「当月自然月天数」(11 月=30), 与行值同一口径
    const days = invSummary?.pd || inventory[0].period_days || 30
    return ((avg * days) / mo).toFixed(2)
  }, [inventory, invSummary, whType])

  // 底部总计取值: 优先后端全量 summary(该维度所有 SKU), 缺失回退已加载行
  // 2026-11-01 修正签名: 原声明 3 个形参却从不使用 fallback, 而 4 处调用只传 2 个
  //   → tsc 报 TS2554(Expected 3 arguments, but got 2)。按实际用法收敛为 (key, pick)。
  const _sumVal = useCallback(
    (key, pick) => {
      const v = invSummary ? invSummary[key] : null
      return v != null ? v : inventory.reduce((s, x) => s + (pick(x) || 0), 0)
    },
    [invSummary, inventory],
  )
  // 批量删除(2026-11-04): 交互与订单同款, 语义按库存适配 —— 后端是"整行归档快照",
  // 删掉的不是消失而是进回收站可恢复, 所以提示写"已归档"而不是"已删除"。
  // 单条删除也走这条链路(原 setConfirmDel 全模块无任何入口, 是一段死代码, 已移除)。
  const doBatchDelete = async () => {
    const ids = useAppStore.getState().invSelIds
    if (!ids.length) return
    try {
      const d = await apiJson<BatchDeleteResp>('POST', API + '/api/inventory/batch', {
        timeout: 90000,
        data: { action: 'delete', ids },
      })
      if (d.ok) {
        const n = (d.data && d.data.deleted) || 0
        const miss = (d.data && d.data.missing) || []
        useAppStore.getState().setInvSelIds([])
        useAppStore.getState().setInvBatch(false)
        clearCache('inventory')
        loadInv(1)
        toast.success(
          miss.length ? t('inv.archived_miss', { n, m: miss.length }) : t('inv.archived_ok', { n }),
        )
      } else toast.error(t('inv.delete_failed', { msg: d.error || '' }))
    } catch (e) {
      toast.error(t('inv.delete_failed', { msg: e.message }))
    }
  }

  // 锤子菜单事件联动(与订单页同构): ref 转发最新闭包, 单次绑定, 避免 useEffect 重绑
  const flRef = useRef(fl)
  flRef.current = fl
  const batchDelRef = useRef(doBatchDelete)
  batchDelRef.current = doBatchDelete
  useEffect(() => {
    const hToggleAll = () => {
      const s = useAppStore.getState()
      const all = flRef.current.map(x => x.id)
      const cur = s.invSelIds || []
      s.setInvSelIds(cur.length === all.length && all.length > 0 ? [] : all)
    }
    const hDel = () => batchDelRef.current()
    const hGoRecycle = () => window.__setPage && window.__setPage('recycle')
    window.addEventListener('inventory-toggle-all', hToggleAll)
    window.addEventListener('inventory-batch-delete', hDel)
    window.addEventListener('inventory-goto-recycle', hGoRecycle)
    return () => {
      window.removeEventListener('inventory-toggle-all', hToggleAll)
      window.removeEventListener('inventory-batch-delete', hDel)
      window.removeEventListener('inventory-goto-recycle', hGoRecycle)
    }
  }, [])
  // 退出批量模式时清空选择, 防止"下次进来还勾着上次的行"
  useEffect(() => {
    if (!invBatch && invSelIds.length) useAppStore.getState().setInvSelIds([])
  }, [invBatch, invSelIds.length])

  return (
    <div className="card">
      <div
        className="section-title"
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          flexWrap: 'wrap',
          gap: 8,
        }}
      >
        <span>
          {t('inv.title')}{' '}
          <span
            className="small muted"
            style={{ fontSize: 'var(--font-xs)', fontWeight: 'var(--fw-regular)' }}
          >
            {t('inv.loaded', {
              a: Math.min(inventory.length, invTotal || inventory.length),
              b: invTotal || inventory.length,
              c: visCols.length,
              d: INV_COLS[whType].length,
            })}
            {s ? ` · "${s}"` : ''}
            <StaleDot
              level={fresh?.staleLevel || 0}
              days={Math.max(...(fresh?.sources || []).map(s => s.days ?? 0), 0)}
            />
          </span>
        </span>
      </div>
      {loadErr && inventory.length > 0 && (
        <div
          style={{
            fontSize: 'var(--font-xs)',
            color: 'var(--warning, #b45309)',
            background: 'var(--bg)',
            borderRadius: 'var(--radius-md)',
            padding: '6px 10px',
            marginBottom: 6,
            display: 'flex',
            justifyContent: 'space-between',
            gap: 8,
          }}
        >
          <span>{loadErr}</span>
          <span
            className="clickable"
            style={{ color: 'var(--muted)', flexShrink: 0 }}
            onClick={() => loadInv(1)}
          >
            {t('inv.refresh_now')}
          </span>
        </div>
      )}
      {loading && inventory.length === 0 ? (
        <div>
          {[1, 2, 3, 4].map(i => (
            <div key={i} className="skeleton" style={{ height: 36, marginBottom: 4 }} />
          ))}
        </div>
      ) : fl.length === 0 ? (
        loadErr ? (
          <ErrorRetry error={loadErr} onRetry={() => loadInv(1)} />
        ) : (
          <EmptyState
            icon="package"
            title={s ? t('inv.empty_matched') : t('common.empty')}
            desc={s ? t('inv.try_other_kw') : t('inv.import_hint')}
            action={
              !s && (
                <button
                  className="btn btn-primary"
                  onClick={() => window.__setPage && window.__setPage('cleansing')}
                >
                  {t('inv.go_import')}
                </button>
              )
            }
          />
        )
      ) : (
        <div
          key={loading && inventory.length === 0 ? 'skeleton' : 'data'}
          className="fade-in"
          style={{ overflow: 'auto', maxHeight: 'calc(100vh - 180px)' }}
        >
          <table>
            <colgroup>
              {/* 勾选列必须占一个 col: 加了 th 却不加 col, colgroup 列数与实际列数不匹配, 整段会被浏览器忽略 */}
              {invBatch && <col style={{ width: 32 }} />}
              {visCols.map(id => {
                const col = INV_COLS[whType].find(c => c.id === id)
                return col ? <col key={col.id} /> : null
              })}
            </colgroup>
            <thead style={{ position: 'sticky', top: 0, background: 'var(--card)', zIndex: 1 }}>
              <tr>
                {/* 全局 th{min-width:72px} 会把 32px 撑回 72px, 必须显式 minWidth:0 才真正收窄 */}
                {invBatch && <th style={{ width: 32, minWidth: 0 }} />}
                {visCols.map(id => {
                  const col = INV_COLS[whType].find(c => c.id === id)
                  if (!col) return null
                  let el
                  if (col.id === 'month_in')
                    el = (
                      <th key={col.id}>
                        {colLabel(col.id)}
                        <br />
                        <span className="small" style={{ fontWeight: 'var(--fw-regular)' }}>
                          {inRange}
                          {ledgerOpen
                            ? ` · ${t('inv.month_open')} ${ledgerOpen.d}/${ledgerOpen.m}`
                            : ''}
                        </span>
                      </th>
                    )
                  else if (col.id === 'month_out')
                    el = (
                      <th key={col.id}>
                        {colLabel(col.id)}
                        <br />
                        <span className="small" style={{ fontWeight: 'var(--fw-regular)' }}>
                          {outRange}
                        </span>
                      </th>
                    )
                  else el = <th key={col.id}>{colLabel(col.id)}</th>
                  return el
                })}
              </tr>
            </thead>
            <tbody>
              {fl.map(x => {
                const isHL =
                  highlightSku &&
                  x.sku === highlightSku &&
                  (!highlightWarehouse || x.warehouse === highlightWarehouse)
                const visCells = visCols.map(function (id) {
                  const col = INV_COLS[whType].find(function (c) {
                    return c.id === id
                  })
                  if (!col) return null
                  let el
                  if (col.id === 'warehouse') {
                    const isOpen = batchOpen.includes(x.sku + '|' + x.warehouse)
                    const hasBatch = (x.batch_count || 0) > 1
                    el = React.createElement(
                      'td',
                      { key: col.id, className: 'col-store' },
                      x.warehouse || '-',
                      hasBatch
                        ? React.createElement(
                            'span',
                            {
                              style: {
                                fontSize: 'var(--font-10)',
                                marginLeft: 6,
                                color: 'var(--primary)',
                              },
                            },
                            isOpen ? '▴' : t('inv.batch_badge'),
                          )
                        : null,
                    )
                  } else if (col.id === 'channel')
                    el = React.createElement(
                      'td',
                      { key: col.id, style: { fontSize: 'var(--font-xs)' } },
                      x.channel === 'other' ? t('inv.channel_other') : t('inv.channel_jd'),
                    )
                  else if (col.id === 'brand')
                    el = React.createElement(
                      'td',
                      { key: col.id, style: { fontSize: 'var(--font-xs)' } },
                      x.brand || '-',
                    )
                  else if (col.id === 'sku')
                    el = React.createElement(
                      'td',
                      { key: col.id, className: 'mono col-sku' },
                      x.sku,
                    )
                  else if (col.id === 'barcode')
                    el = React.createElement(
                      'td',
                      { key: col.id, className: 'mono', style: { fontSize: 'var(--font-xs)' } },
                      x.barcode || '-',
                    )
                  else if (col.id === 'name')
                    el = React.createElement(
                      'td',
                      { key: col.id, className: 'col-name' },
                      x.product_name,
                    )
                  else if (col.id === 'begin') {
                    // 2026-10-30 期初库存改**真实导入值** beginning_stock_raw(后端返回原列值);
                    //   原前端自算 available − 月入 + 月出 是"本月期初"反推, 与导入单据对不上
                    const _beg = x.beginning_stock_raw ?? 0
                    el = React.createElement(
                      'td',
                      {
                        key: col.id,
                        className: 'col-qty',
                        style: { fontWeight: 'var(--fw-semibold)' },
                      },
                      _beg,
                    )
                  } else if (col.id === 'keep_days') {
                    // 库存可撑天数(前瞻): 可用/日销; 后端 ds=0 给 999 → 显示 —
                    const _kd = x.days_to_empty
                    el = React.createElement(
                      'td',
                      { key: col.id, className: 'col-qty' },
                      _kd != null && _kd < 999 ? _kd : '—',
                    )
                  } else if (col.id === 'transit')
                    el = React.createElement(
                      'td',
                      { key: col.id, className: 'col-qty' },
                      x.in_transit_qty,
                    )
                  else if (col.id === 'c_transit')
                    el = React.createElement(
                      'td',
                      { key: col.id, className: 'col-qty' },
                      x.c_transit || 0,
                    )
                  else if (col.id === 'month_in')
                    el = React.createElement(
                      'td',
                      { key: col.id, className: 'col-qty' },
                      x.month_inbound ?? 0,
                    )
                  else if (col.id === 'month_out')
                    el = React.createElement(
                      'td',
                      {
                        key: col.id,
                        className: 'col-qty',
                        style: { fontWeight: 'var(--fw-semibold)' },
                      },
                      x.month_outbound ?? 0,
                    )
                  else if (col.id === 'end_stock') {
                    // 2026-11-04 期末 = 期初 + 当月入库 − 当月出库(后端算, 前端只渲染)
                    //   负数 = 出库超过可售量, 属于数据异常 → 红色标出, 与周转列的口径一致
                    const _end = x.end_stock ?? 0
                    el = React.createElement(
                      'td',
                      {
                        key: col.id,
                        className: 'col-qty',
                        title: t('inv.end_stock_hint'),
                        style: {
                          color: _end < 0 ? 'var(--danger)' : undefined,
                          fontWeight: 'var(--fw-semibold)',
                        },
                      },
                      _end,
                    )
                  } else if (col.id === 'avg_stock') {
                    const _avg = x.avg_stock ?? 0
                    el = React.createElement(
                      'td',
                      {
                        key: col.id,
                        className: 'col-qty',
                        title: t('inv.avg_stock_hint'),
                        style: { color: _avg < 0 ? 'var(--danger)' : undefined },
                      },
                      typeof _avg === 'number' ? _avg.toFixed(1) : _avg,
                    )
                  } else if (col.id === 'prod_date')
                    el = React.createElement(
                      'td',
                      { key: col.id, className: 'col-qty', style: { fontSize: 'var(--font-xs)' } },
                      x.batch_prod_date || '-',
                    )
                  else if (col.id === 'exp_date')
                    el = React.createElement(
                      'td',
                      { key: col.id, className: 'col-qty', style: { fontSize: 'var(--font-xs)' } },
                      x.batch_exp_date || '-',
                    )
                  else if (col.id === 'batch_days')
                    el = React.createElement(
                      'td',
                      { key: col.id, className: 'col-qty', style: { fontSize: 'var(--font-xs)' } },
                      x.batch_days || '-',
                    )
                  else if (col.id === 'eff_status') {
                    const es = x.batch_status
                    const ecolor = expiryStatusColor(es)
                    el = React.createElement(
                      'td',
                      { key: col.id },
                      React.createElement(
                        'span',
                        {
                          style: {
                            fontSize: 'var(--font-xs)',
                            fontWeight: 'var(--fw-semibold)',
                            color: ecolor,
                          },
                        },
                        expiryStatusLabel(es),
                      ),
                      x.batch_pct
                        ? React.createElement(
                            'span',
                            {
                              style: {
                                fontSize: 'var(--font-10)',
                                color: 'var(--muted2)',
                                marginLeft: 4,
                              },
                            },
                            x.batch_pct + '%',
                          )
                        : null,
                    )
                  } else if (col.id === 'over_third') {
                    const _ot_st = x.batch_status
                    el = React.createElement(
                      'td',
                      { key: col.id, style: { fontSize: 'var(--font-xs)' } },
                      _ot_st === 'no'
                        ? React.createElement(
                            'span',
                            { style: { color: 'var(--danger)', fontWeight: 'var(--fw-semibold)' } },
                            t('inv.exp.over_third_icon'),
                          )
                        : _ot_st === 'expired'
                          ? React.createElement(
                              'span',
                              { style: { color: 'var(--accent-purple)' } },
                              t('inv.exp.expired_short'),
                            )
                          : _ot_st === 'warn'
                            ? React.createElement(
                                'span',
                                {
                                  style: {
                                    color: 'var(--warning)',
                                    fontWeight: 'var(--fw-semibold)',
                                  },
                                },
                                t('inv.exp.warn_icon'),
                              )
                            : _ot_st === 'ok'
                              ? React.createElement(
                                  'span',
                                  { style: { color: 'var(--muted2)' } },
                                  t('inv.exp.no'),
                                )
                              : '-',
                    )
                  } else if (col.id === 'note') {
                    const _ns = x.batch_status
                    const _nb = x.batch_pct || 0
                    const _nclr = expiryStatusColor(_ns)
                    const _nlbl = expiryStatusLabel(_ns, false)
                    const _nact = expiryActionLabel(_ns, _nb)
                    el = React.createElement(
                      'td',
                      { key: col.id, style: { fontSize: 'var(--font-xs)' } },
                      _nb > 0 || _ns
                        ? [
                            React.createElement(
                              'span',
                              {
                                key: 's',
                                style: { color: _nclr, fontWeight: 'var(--fw-semibold)' },
                              },
                              _nb + '% ' + _nlbl,
                            ),
                            ' → ',
                            React.createElement(
                              'span',
                              { key: 'a', style: { color: 'var(--muted2)' } },
                              _nact,
                            ),
                          ]
                        : '-',
                    )
                  } else if (col.id === 'avail')
                    el = React.createElement(
                      'td',
                      {
                        key: col.id,
                        className: 'col-qty',
                        style: { fontWeight: 'var(--fw-semibold)' },
                      },
                      x.available_qty,
                    )
                  else if (col.id === 'turnover') {
                    const tc = x.turnover_days
                    // 期末为负 → 负周转, 标「负库存异常」; 负值不能当正常周转读
                    const abn = tc != null && tc < 0
                    el = React.createElement(
                      'td',
                      {
                        key: col.id,
                        className: 'col-qty',
                        title: abn ? t('inv.negative_stock') : t('inv.turnover_hint'),
                        style: {
                          fontWeight: 'var(--fw-semibold)',
                          color:
                            tc != null && (tc > 30 || abn)
                              ? 'var(--danger)'
                              : tc != null && tc > 15
                                ? 'var(--warning)'
                                : 'var(--text)',
                        },
                      },
                      // 2026-11-04 不再渲染 ∞: 当月出库为 0 时公式无解,
                      //   给明确文案「未出库」(走词条, 不硬编码中文)
                      tc != null
                        ? [
                            // 用户定案: 周转天必须两位小数 —— 后端 round(2) 后 17.0 会被
                            //   JSON 序列化成 17, 直接插值就成"17 天", 与 "16.07 天" 不齐
                            t('common.day_unit', { n: Number(tc).toFixed(2) }),
                            abn
                              ? React.createElement(
                                  'span',
                                  {
                                    key: 'abn',
                                    style: {
                                      display: 'block',
                                      fontSize: 'var(--font-10)',
                                      fontWeight: 'var(--fw-regular)',
                                    },
                                  },
                                  t('inv.negative_stock'),
                                )
                              : null,
                          ]
                        : t('inv.turnover_na'),
                    )
                  } else if (col.id === 'price')
                    el = React.createElement(
                      'td',
                      {
                        key: col.id,
                        className: 'col-price',
                        style: { fontSize: 'var(--font-sm)' },
                      },
                      x.price ? '¥' + Number(x.price).toFixed(1) : '-',
                    )
                  else if (col.id === 'stock_amount') {
                    const sa = (x.available_qty || 0) * (x.price || 0)
                    el = React.createElement(
                      'td',
                      {
                        key: col.id,
                        className: 'col-price',
                        style: { fontWeight: 'var(--fw-semibold)', fontSize: 'var(--font-sm)' },
                      },
                      sa ? '¥' + sa.toLocaleString() : '-',
                    )
                  } else
                    el = React.createElement(
                      'td',
                      {
                        key: col.id,
                        className: 'small muted',
                        style: { fontSize: 'var(--font-xs)' },
                      },
                      '-',
                    )
                  return el
                })
                const bk = x.sku + '|' + x.warehouse
                const isOpen = batchOpen.includes(bk)
                const batchTrs = []
                const _span = visCols.length + (invBatch ? 1 : 0)
                if (isOpen && batchLoading[bk]) {
                  // 展开加载中: 给出视觉反馈(此前无任何提示, 慢网络下像"点了没反应")
                  batchTrs.push(
                    React.createElement(
                      'tr',
                      { key: x.id + '-bl', className: 'tr-batch' },
                      React.createElement(
                        'td',
                        { colSpan: _span, className: 'td-loading' },
                        React.createElement(IconLoading, { size: 12 }),
                        t('inv.batch_loading'),
                      ),
                    ),
                  )
                } else if (isOpen && !batchData[bk]) {
                  // 批次拉取失败/异常: 提示重试(收起后再展开会重新拉取)
                  batchTrs.push(
                    React.createElement(
                      'tr',
                      { key: x.id + '-be', className: 'tr-err' },
                      React.createElement(
                        'td',
                        {
                          colSpan: _span,
                          className: 'td-loading',
                          style: { color: 'var(--danger)' },
                        },
                        t('inv.batch_failed'),
                      ),
                    ),
                  )
                } else if (isOpen && batchData[bk]) {
                  batchData[bk].forEach(function (b, bi) {
                    let pct = 0
                    if (b.exp_date && b.prod_date) {
                      const dp = Math.abs(
                        (new Date(b.exp_date).getTime() - new Date(b.prod_date).getTime()) /
                          86400000,
                      )
                      pct =
                        dp > 0
                          ? Math.max(
                              0,
                              Math.min(100, Math.round((daysSinceCn(b.prod_date) / dp) * 100)),
                            )
                          : 0
                    }
                    const bcolor =
                      pct >= 67 ? 'var(--danger)' : pct >= 40 ? 'var(--warning)' : 'var(--success)'
                    const note = batchUseLabel(pct)
                    const bcells = visCols.map(function (id) {
                      const col = INV_COLS[whType].find(function (c) {
                        return c.id === id
                      })
                      if (!col) return null
                      let el
                      if (col.id === 'prod_date')
                        el = React.createElement(
                          'td',
                          { key: col.id, style: { fontSize: 'var(--font-xs)' } },
                          b.prod_date || '-',
                        )
                      else if (col.id === 'exp_date')
                        el = React.createElement(
                          'td',
                          { key: col.id, style: { fontSize: 'var(--font-xs)' } },
                          b.exp_date || '-',
                        )
                      else if (col.id === 'batch_days') {
                        let td = 0
                        if (b.exp_date && b.prod_date)
                          td = Math.round(
                            (new Date(b.exp_date).getTime() - new Date(b.prod_date).getTime()) /
                              86400000,
                          )
                        el = React.createElement(
                          'td',
                          { key: col.id, style: { fontSize: 'var(--font-xs)' } },
                          td || '-',
                        )
                      } else if (col.id === 'eff_status') {
                        let es = ''
                        if (b.exp_date && b.prod_date) {
                          const cv = Math.round(
                            (daysSinceCn(b.prod_date) /
                              ((new Date(b.exp_date).getTime() - new Date(b.prod_date).getTime()) /
                                86400000)) *
                              100,
                          )
                          // 用**码值**而不是显示文案做比较(文案会被 i18n 改, 拿它当键必炸)
                          es = cv >= 100 ? 'expired' : cv >= 67 ? 'no' : cv > 40 ? 'warn' : 'ok'
                        }
                        el = React.createElement(
                          'td',
                          {
                            key: col.id,
                            style: {
                              fontSize: 'var(--font-xs)',
                              fontWeight: 'var(--fw-semibold)',
                              color: expiryStatusColor(es),
                            },
                          },
                          expiryStatusLabel(es),
                        )
                      } else if (col.id === 'avail')
                        el = React.createElement(
                          'td',
                          {
                            key: col.id,
                            style: { fontWeight: 'var(--fw-semibold)', fontSize: 'var(--font-xs)' },
                          },
                          b.qty,
                        )
                      else if (col.id === 'over_third') {
                        let ot_st = ''
                        if (b.exp_date && b.prod_date) {
                          const td = Math.round(
                            (new Date(b.exp_date).getTime() - new Date(b.prod_date).getTime()) /
                              86400000,
                          )
                          if (td > 0) {
                            const cv = Math.round((daysSinceCn(b.prod_date) / td) * 100)
                            ot_st = cv >= 100 ? 'expired' : cv > 33 ? 'no' : 'ok'
                          }
                        }
                        el = React.createElement(
                          'td',
                          {
                            key: col.id,
                            style: {
                              fontSize: 'var(--font-xs)',
                              color:
                                ot_st === 'expired'
                                  ? 'var(--accent-purple)'
                                  : expiryStatusColor(ot_st),
                            },
                          },
                          ot_st === 'expired'
                            ? t('inv.exp.expired_short')
                            : ot_st === 'no'
                              ? t('inv.exp.over_third_icon')
                              : ot_st
                                ? t('inv.exp.ok')
                                : '-',
                        )
                      } else if (col.id === 'note')
                        el = React.createElement(
                          'td',
                          {
                            key: col.id,
                            style: {
                              fontSize: 'var(--font-xs)',
                              color: bcolor,
                              fontWeight: 'var(--fw-semibold)',
                            },
                          },
                          pct + '% ' + note,
                        )
                      else if (
                        col.id === 'warehouse' ||
                        col.id === 'sku' ||
                        col.id === 'name' ||
                        col.id === 'barcode' ||
                        col.id === 'channel' ||
                        col.id === 'brand'
                      ) {
                        const v =
                          col.id === 'name'
                            ? x.product_name
                            : col.id === 'brand'
                              ? x.brand || '-'
                              : x[col.id] || '-'
                        el = React.createElement(
                          'td',
                          { key: col.id, style: { fontSize: 'var(--font-xs)' } },
                          v,
                        )
                      } else if (col.id === 'price')
                        el = React.createElement(
                          'td',
                          { key: col.id, style: { fontSize: 'var(--font-xs)' } },
                          x.price ? '¥' + Number(x.price).toFixed(1) : '-',
                        )
                      else if (col.id === 'month_in')
                        el = React.createElement(
                          'td',
                          {
                            key: col.id,
                            style: { fontSize: 'var(--font-xs)', color: 'var(--muted2)' },
                          },
                          '-',
                        )
                      else if (col.id === 'month_out')
                        el = React.createElement(
                          'td',
                          {
                            key: col.id,
                            style: { fontSize: 'var(--font-xs)', color: 'var(--muted2)' },
                          },
                          '-',
                        )
                      else if (col.id === 'begin')
                        el = React.createElement(
                          'td',
                          {
                            key: col.id,
                            style: { fontSize: 'var(--font-xs)', color: 'var(--muted2)' },
                          },
                          '-',
                        )
                      else if (col.id === 'stock_amount') {
                        const _amt = b.qty * (x.price || 0)
                        el = React.createElement(
                          'td',
                          {
                            key: col.id,
                            style: {
                              fontSize: 'var(--font-xs)',
                              fontWeight: 'var(--fw-semibold)',
                              color: _amt > 0 ? 'var(--text)' : 'var(--muted2)',
                            },
                          },
                          _amt > 0 ? '¥' + _amt.toLocaleString() : '-',
                        )
                      } else if (col.id === 'turnover')
                        el = React.createElement(
                          'td',
                          {
                            key: col.id,
                            style: { fontSize: 'var(--font-xs)', color: 'var(--muted2)' },
                          },
                          '-',
                        )
                      else
                        el = React.createElement(
                          'td',
                          { key: col.id, style: { fontSize: 'var(--font-xs)' } },
                          '-',
                        )
                      return el
                    })
                    batchTrs.push(
                      React.createElement(
                        'tr',
                        { key: x.id + '-b' + bi, className: 'tr-batch' },
                        bcells,
                      ),
                    )
                  })
                }
                const _hb = (x.batch_count || 0) > 1
                const _isSel = invBatch && (invSelIds || []).indexOf(x.id) >= 0
                // 勾选列固定最左 32px。注意: 表格是 auto layout, 单靠 td width:32 不生效 ——
                // 列宽下限由单元格 min-content 决定, 20px 图标不带盒子就只剩 20px(实测), 点击热区太小。
                // 所以给 span 撑一个 32x32 的盒, td padding 归零。
                const _cells = invBatch
                  ? [
                      <td
                        key="__sel"
                        // minWidth:0 抵全局 th{min-width:72px}; padding 归零, 行高交给文字行(≈26px)决定,
                        // 勾选盒 32x24 不撑高行 —— 100 行列表里每行多 6px 就是 600px 无谓滚动
                        style={{ width: 32, minWidth: 0, padding: 0, textAlign: 'center' }}
                        onClick={e => e.stopPropagation()}
                      >
                        <span
                          onClick={() => {
                            const _s = useAppStore.getState()
                            const _cur = _s.invSelIds || []
                            _s.setInvSelIds(
                              _cur.indexOf(x.id) >= 0
                                ? _cur.filter(function (i) {
                                    return i !== x.id
                                  })
                                : _cur.concat([x.id]),
                            )
                          }}
                          style={{
                            cursor: 'pointer',
                            display: 'inline-flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            width: 32,
                            height: 24,
                          }}
                        >
                          {_isSel ? <IconCircleCheck size={20} /> : <IconCircle size={20} />}
                        </span>
                      </td>,
                    ].concat(visCells)
                  : visCells
                return [
                  React.createElement(
                    'tr',
                    {
                      key: x.id,
                      id: 'hl-' + x.sku + '-' + (x.warehouse || ''),
                      onClick: _hb
                        ? function () {
                            toggleBatch(x)
                          }
                        : null,
                      className: _hb ? (isOpen ? 'tr-click tr-open' : 'tr-click') : '',
                      style: isHL
                        ? {
                            background: 'rgba(245,158,11,0.15)',
                            outline: '2px solid var(--warning)',
                          }
                        : _isSel
                          ? { background: 'rgba(29,78,216,0.06)' }
                          : {},
                    },
                    _cells,
                  ),
                ].concat(batchTrs)
              })}
            </tbody>
            {inventory.length > 0 && (
              <tfoot>
                <tr style={{ fontWeight: 'var(--fw-bold)', borderTop: '2px solid var(--border)' }}>
                  {invBatch && <td key="__sel" style={{ width: 32 }} />}
                  {visCols.map(function (id) {
                    const col = INV_COLS[whType].find(function (c) {
                      return c.id === id
                    })
                    if (!col) return null
                    if (col.id === 'begin')
                      return React.createElement(
                        'td',
                        { key: col.id, style: { textAlign: 'right', fontSize: 'var(--font-sm)' } },
                        _sumVal('beg', x => x.beginning_stock_raw),
                      )
                    if (col.id === 'month_in')
                      return React.createElement(
                        'td',
                        { key: col.id, className: 'col-qty' },
                        _sumVal('mi', x => x.month_inbound),
                      )
                    if (col.id === 'month_out')
                      return React.createElement(
                        'td',
                        {
                          key: col.id,
                          className: 'col-qty',
                          style: { fontWeight: 'var(--fw-semibold)' },
                        },
                        _sumVal('mo', x => x.month_outbound),
                      )
                    if (col.id === 'end_stock' || col.id === 'avg_stock') {
                      // 合计与行值同口径: 先合计再推算, 不是把各行期末/平均做算术平均
                      //   总期末 = Σ期初 + Σ月入 − Σ月出; 总平均 = (Σ期初 + 总期末)/2
                      const _b = _sumVal('beg', x => x.beginning_stock_raw)
                      const _i = _sumVal('mi', x => x.month_inbound)
                      const _o = _sumVal('mo', x => x.month_outbound)
                      const _e = _b + _i - _o
                      const _a = (_b + _e) / 2
                      const _v = col.id === 'end_stock' ? _e : _a
                      return React.createElement(
                        'td',
                        {
                          key: col.id,
                          className: 'col-qty',
                          title:
                            col.id === 'end_stock'
                              ? t('inv.end_stock_hint')
                              : t('inv.avg_stock_hint'),
                          style: {
                            fontWeight: 'var(--fw-bold)',
                            color: _v < 0 ? 'var(--danger)' : undefined,
                          },
                        },
                        col.id === 'end_stock' ? _e : _a.toFixed(1),
                      )
                    }
                    if (col.id === 'avail')
                      return React.createElement(
                        'td',
                        {
                          key: col.id,
                          className: 'col-qty',
                          style: { fontWeight: 'var(--fw-semibold)' },
                        },
                        _sumVal('av', x => x.available_qty),
                      )
                    if (col.id === 'keep_days') {
                      const _q = inventory.reduce((acc, x) => acc + (x.available_qty || 0), 0)
                      const _d = inventory.reduce((acc, x) => acc + (x.daily_sales || 0), 0)
                      return React.createElement(
                        'td',
                        {
                          key: col.id,
                          style: { fontSize: 'var(--font-13)', fontWeight: 'var(--fw-bold)' },
                        },
                        _d > 0 ? t('common.day_unit', { n: (_q / _d).toFixed(1) }) : '—',
                      )
                    }
                    if (col.id === 'turnover') {
                      // 仓总平均库存为负 → 仓周转为负, 同样标异常, 不当正常值
                      const _tabn = totalTurnover != null && Number(totalTurnover) < 0
                      return React.createElement(
                        'td',
                        {
                          key: col.id,
                          title: _tabn ? t('inv.negative_stock') : t('inv.total_turnover_hint'),
                          style: {
                            fontSize: 'var(--font-13)',
                            fontWeight: 'var(--fw-bold)',
                            color: _tabn ? 'var(--danger)' : undefined,
                          },
                        },
                        totalTurnover != null
                          ? [
                              t('common.day_unit', { n: totalTurnover }),
                              _tabn
                                ? React.createElement(
                                    'span',
                                    {
                                      key: 'tabn',
                                      style: {
                                        display: 'block',
                                        fontSize: 'var(--font-10)',
                                        fontWeight: 'var(--fw-regular)',
                                      },
                                    },
                                    t('inv.negative_stock'),
                                  )
                                : null,
                            ]
                          : t('inv.turnover_na'),
                      )
                    }
                    if (col.id === 'price')
                      return React.createElement(
                        'td',
                        {
                          key: col.id,
                          className: 'col-price',
                          style: { fontSize: 'var(--font-sm)' },
                        },
                        '',
                      )
                    if (col.id === 'stock_amount')
                      return React.createElement(
                        'td',
                        {
                          key: col.id,
                          className: 'col-price',
                          style: { fontWeight: 'var(--fw-semibold)', fontSize: 'var(--font-sm)' },
                        },
                        '¥' +
                          inventory
                            .reduce(function (s, x) {
                              return s + (x.available_qty || 0) * (x.price || 0)
                            }, 0)
                            .toLocaleString(),
                      )
                    if (id === visCols[0])
                      return React.createElement(
                        'td',
                        {
                          key: col.id,
                          colSpan: 1,
                          style: {
                            textAlign: 'right',
                            fontSize: 'var(--font-sm)',
                            color: 'var(--text)',
                          },
                        },
                        t('inv.total'),
                      )
                    return React.createElement('td', { key: col.id })
                  })}
                </tr>
              </tfoot>
            )}
          </table>
          {invTotal > 0 && inventory.length < invTotal && (
            <div
              style={{ textAlign: 'center', padding: '10px 0' }}
              ref={function (el) {
                if (el && !el._obs) {
                  el._obs = new IntersectionObserver(
                    function (entries) {
                      if (entries[0].isIntersecting && !loadingMore) {
                        const np = invPageRef.current + 1
                        invPageRef.current = np
                        loadInv(np)
                      }
                    },
                    { rootMargin: '200px' },
                  )
                  el._obs.observe(el)
                }
              }}
            >
              <span
                className="btn btn-ghost"
                style={{ fontSize: 'var(--font-sm)', padding: '6px 16px', cursor: 'pointer' }}
              >
                {loadingMore ? t('common.loading') : ''}({Math.min(inventory.length, invTotal)}/
                {invTotal})
              </span>
            </div>
          )}
          {invTotal > 0 && inventory.length >= invTotal && (
            <div
              style={{
                textAlign: 'center',
                padding: '10px 0',
                fontSize: 'var(--font-xs)',
                color: 'var(--muted2)',
              }}
            >
              {t('inv.all_loaded', { n: invTotal })}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
