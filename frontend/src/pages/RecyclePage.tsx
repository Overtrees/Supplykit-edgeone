import React, { useState, useEffect, useRef } from 'react'
import { apiJson } from '../api/client'
import { useAppStore } from '../store/useAppStore'
import { Group, Row, ListItem } from '../components/ListGroup'
import EmptyState from '../components/EmptyState'
import { t } from '../locale'

const API = import.meta.env.VITE_API_BASE_URL || ''

/** 已选条数后缀(0 条不显示, 避免"已选 0"噪音); 文案走 i18n, 不拼中文字面量 */
const selSuffix = (n: number) => (n > 0 ? ' ' + t('recycle.selected_suffix', { n }) : '')

/** 回收站页(独立页, App header 显示; 批量操作由锤子菜单 HammerRecycle 触发, 事件联动)
 *
 * 2026-11-04 增补「已删除库存」: 库存删除与订单不同 —— 订单是软删(deleted_at),
 * 库存是整行归档快照(inventory_archive, 保留删除时的数量)。所以这里不是"取消删除标记",
 * 而是"把快照原样插回"; 且恢复会被业务键(sku×69码×仓)冲突拦截, 故必须逐条回报结果,
 * 不能像订单那样 Promise.all 一把梭(任一失败会整批抛出, 已成功的也看不到)。
 */
export default function RecyclePage() {
  const { setRecycleSel, setRecycleBusy } = useAppStore()
  const [rules, setRules] = useState<any[]>([])
  const [orders, setOrders] = useState<any[]>([])
  const [inventories, setInventories] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [batchBusy, setBatchBusy] = useState(false)
  const [selected, setSelected] = useState<{
    rules: Set<number>
    orders: Set<number>
    inventory: Set<number>
  }>({
    rules: new Set(),
    orders: new Set(),
    inventory: new Set(),
  })

  const syncSel = (s: typeof selected) => {
    setSelected(s)
    setRecycleSel({ rules: s.rules.size, orders: s.orders.size, inventory: s.inventory.size })
  }

  const loadData = () => {
    setLoading(true)
    // S3-2 收口: apiJson 直接返回 json(不再 .then(r => r.json()))
    // 库存归档按 channel 存储, 回收站要"看全部"就两个渠道各取一次再合并
    Promise.all([
      apiJson('GET', API + '/api/rules?channel=all&include_deleted=1'),
      apiJson('GET', API + '/api/orders?page=1&page_size=200&include_deleted=1'),
      apiJson('GET', API + '/api/inventory/archive?channel=jd&limit=200'),
      apiJson('GET', API + '/api/inventory/archive?channel=other&limit=200'),
    ])
      .then(([rData, oData, iJd, iOt]: any[]) => {
        const items = rData.data || rData || []
        setRules(items.filter((x: any) => x.deleted_at))
        const o = oData.data || oData || []
        // 响应结构 {items,total}——取 items(原生 fetch 未解包 axios 层)
        const orderItems = Array.isArray(o) ? o : Array.isArray(o.items) ? o.items : []
        setOrders(orderItems.filter((x: any) => x.deleted_at))
        const _inv = [...((iJd && iJd.data) || []), ...((iOt && iOt.data) || [])]
        _inv.sort((a: any, b: any) => String(b.archived_at).localeCompare(String(a.archived_at)))
        setInventories(_inv)
        setLoading(false)
      })
      .catch(() => {
        setLoading(false)
      })
  }

  useEffect(() => {
    loadData()
  }, [])
  useEffect(() => {
    setRecycleBusy(batchBusy)
  }, [batchBusy, setRecycleBusy])

  const toggleSel = (type: 'rules' | 'orders' | 'inventory', id: number) => {
    const next = { ...selected, [type]: new Set(selected[type]) }
    if (next[type].has(id)) next[type].delete(id)
    else next[type].add(id)
    syncSel(next)
  }
  const toggleAll = () => {
    // 空类型视为已全选(不参与 all 判断——否则某类型无数据时 all 恒 false, 无法取消全选)
    const allR = rules.length === 0 || rules.every(x => selected.rules.has(x.id))
    const allO = orders.length === 0 || orders.every(x => selected.orders.has(x.id))
    const allI = inventories.length === 0 || inventories.every(x => selected.inventory.has(x.id))
    const all = allR && allO && allI
    syncSel({
      rules: new Set(all ? [] : rules.map(x => x.id)),
      orders: new Set(all ? [] : orders.map(x => x.id)),
      inventory: new Set(all ? [] : inventories.map(x => x.id)),
    })
  }

  const batchAction = async (action: string, label: string) => {
    const ruleIds = Array.from(selected.rules),
      orderIds = Array.from(selected.orders),
      invIds = Array.from(selected.inventory)
    const n = ruleIds.length + orderIds.length + invIds.length
    if (n === 0) {
      alert(t('recycle.err_nothing_selected', { label }))
      return
    }
    setBatchBusy(true)
    try {
      // 规则: 永久删除走 batch purge(批量接口), 其余走单条
      if (ruleIds.length) {
        if (action === 'permanent-delete') {
          await apiJson('POST', API + '/api/rules/batch', {
            timeout: 90000,
            data: { action: 'purge', ids: ruleIds },
          })
        } else {
          await Promise.all(
            ruleIds.map(id => apiJson('POST', API + '/api/rules/' + id + '/' + action)),
          )
        }
      }
      // 订单: 单条(restore/permanent-delete)
      if (orderIds.length) {
        await Promise.all(
          orderIds.map(id => apiJson('POST', API + '/api/orders/' + id + '/' + action)),
        )
      }
      // 库存: 只有"恢复"一种动作(归档行没有永久删除入口), 走批量接口拿逐条结果
      if (invIds.length) {
        if (action !== 'restore') throw new Error(t('recycle.err_inv_action'))
        const d = await apiJson('POST', API + '/api/inventory/batch', {
          timeout: 90000,
          data: { action: 'restore', ids: invIds },
        })
        if (!d.ok) throw new Error(d.error || t('recycle.err_restore_failed'))
        const _ok = (d.data && d.data.restored) || 0
        const _failed = (d.data && d.data.failed) || []
        if (_failed.length) {
          // 部分成功要如实回报 —— 这是与订单批量最大的差异(订单任一失败整批抛出)
          const _head = _failed
            .slice(0, 8)
            .map((f: any) => '· id ' + f.id + ': ' + f.reason)
            .join('\n')
          alert(
            t('recycle.inv_restore_partial', { ok: _ok, fail: _failed.length }) +
              '\n' +
              _head +
              (_failed.length > 8
                ? '\n' + t('recycle.inv_restore_more', { n: _failed.length - 8 })
                : ''),
          )
        }
      }
      loadData()
      syncSel({ rules: new Set(), orders: new Set(), inventory: new Set() })
    } catch (e: any) {
      alert(t('recycle.action_failed', { label, msg: e.message }))
    }
    setBatchBusy(false)
  }
  const confirmPurge = () => {
    const n = selected.rules.size + selected.orders.size
    if (n === 0 && selected.inventory.size > 0) {
      alert(t('recycle.err_inv_restore_only'))
      return
    }
    if (n === 0) {
      alert(t('recycle.err_no_selection'))
      return
    }
    if (window.confirm(t('recycle.confirm_purge', { n })))
      batchAction('permanent-delete', t('recycle.purge'))
  }

  // 锤子菜单事件联动(HammerRecycle dispatch) —— ref 转发+单次绑定(根治 useEffect 重绑双 handler 抵消竞态)
  const toggleAllRef = useRef(toggleAll)
  toggleAllRef.current = toggleAll
  const batchActionRef = useRef(batchAction)
  batchActionRef.current = batchAction
  const confirmPurgeRef = useRef(confirmPurge)
  confirmPurgeRef.current = confirmPurge
  useEffect(() => {
    const hToggleAll = () => {
      toggleAllRef.current()
    }
    const hRestore = () => {
      batchActionRef.current('restore', t('common.restore'))
    }
    const hPurge = () => {
      confirmPurgeRef.current()
    }
    window.addEventListener('recycle-toggle-all', hToggleAll)
    window.addEventListener('recycle-restore', hRestore)
    window.addEventListener('recycle-purge', hPurge)
    return () => {
      window.removeEventListener('recycle-toggle-all', hToggleAll)
      window.removeEventListener('recycle-restore', hRestore)
      window.removeEventListener('recycle-purge', hPurge)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const renderList = (type: 'rules' | 'orders' | 'inventory', items: any[]) => {
    if (items.length === 0)
      return (
        <EmptyState
          icon="clipboard"
          title={
            type === 'rules'
              ? t('recycle.empty_rules')
              : type === 'orders'
                ? t('recycle.empty_orders')
                : t('recycle.empty_inventory')
          }
        />
      )
    return (
      <>
        {items.map((x: any, i: number) => (
          <ListItem
            key={x.id}
            // 库存是四维键(仓型×SKU×69码×仓): 只写 SKU 会分不清"同 SKU 多仓"
            label={
              type === 'rules'
                ? x.name
                : type === 'orders'
                  ? x.order_no + ' - ' + (x.product_name || '')
                  : (x.warehouse || '—') +
                    ' · ' +
                    x.sku +
                    (x.barcode ? ' · ' + x.barcode : '') +
                    ' - ' +
                    (x.product_name || '')
            }
            isSel={selected[type].has(x.id)}
            onClick={() => toggleSel(type, x.id)}
            right={
              type === 'inventory'
                ? x.archived_at
                  ? String(x.archived_at).slice(0, 10)
                  : ''
                : x.deleted_at
                  ? String(x.deleted_at).slice(0, 10)
                  : ''
            }
            last={i === items.length - 1}
          />
        ))}
      </>
    )
  }

  return (
    <div className="form-page">
      {loading ? (
        <div>
          {[1, 2, 3].map(i => (
            <div
              key={i}
              className="skeleton"
              style={{ height: 48, borderRadius: 'var(--radius-lg)', marginBottom: 8 }}
            />
          ))}
        </div>
      ) : (
        <>
          <Group title={t('recycle.deleted_rules')}>
            <Row
              label={t('recycle.row_rules') + selSuffix(selected.rules.size)}
              sub={t('recycle.row_hint')}
            />
            {renderList('rules', rules)}
          </Group>
          <Group title={t('recycle.deleted_orders')}>
            <Row
              label={t('recycle.row_orders') + selSuffix(selected.orders.size)}
              sub={t('recycle.row_hint')}
            />
            {renderList('orders', orders)}
          </Group>
          {/* 2026-11-04 已删除库存(归档快照): 与订单同款勾选 + 批量, 但只能恢复不能永久删 */}
          <Group title={t('recycle.deleted_inventory')}>
            <Row
              label={t('recycle.row_inventory') + selSuffix(selected.inventory.size)}
              sub={t('recycle.row_hint_inv')}
            />
            {renderList('inventory', inventories)}
          </Group>
          {batchBusy && (
            <div
              style={{
                textAlign: 'center',
                padding: 16,
                fontSize: 'var(--font-sm)',
                color: 'var(--muted2)',
              }}
            >
              {t('recycle.processing')}
            </div>
          )}
        </>
      )}
    </div>
  )
}
