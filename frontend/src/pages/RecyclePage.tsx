import React, { useState, useEffect } from 'react'
import { useAppStore } from '../store/useAppStore'
import { IconCheck } from '../components/Icons'
import { Group, Row } from '../components/ListGroup'
import { t } from '../locale'

const API = import.meta.env.VITE_API_BASE_URL || ''

/** 回收站页(独立页, App header 显示; 批量操作由锤子菜单 HammerRecycle 触发, 事件联动) */
export default function RecyclePage() {
  const { setRecycleSel, setRecycleBusy } = useAppStore()
  const [rules, setRules] = useState<any[]>([])
  const [orders, setOrders] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [batchBusy, setBatchBusy] = useState(false)
  const [selected, setSelected] = useState<{ rules: Set<number>, orders: Set<number> }>({ rules: new Set(), orders: new Set() })

  const syncSel = (s: typeof selected) => {
    setSelected(s)
    setRecycleSel({ rules: s.rules.size, orders: s.orders.size })
  }

  const loadData = () => {
    setLoading(true)
    const _auth = { 'Authorization': 'Bearer ' + (() => { try { return localStorage.getItem('c_token') } catch { return '' } })() }
    Promise.all([
      fetch(API + '/api/rules?channel=all&include_deleted=1', { headers: _auth }).then(r => r.json()),
      fetch(API + '/api/orders?page=1&page_size=200', { headers: _auth }).then(r => r.json()),
    ]).then(([rData, oData]: any[]) => {
      const items = rData.data || rData || []
      setRules(items.filter((x: any) => x.deleted_at))
      const o = oData.data || oData || []
      setOrders(Array.isArray(o) ? o.filter((x: any) => x.deleted_at) : [])
      setLoading(false)
    }).catch(() => { setLoading(false) })
  }

  useEffect(() => { loadData() }, [])
  useEffect(() => { setRecycleBusy(batchBusy) }, [batchBusy, setRecycleBusy])

  const toggleSel = (type: 'rules' | 'orders', id: number) => {
    const next = { ...selected, [type]: new Set(selected[type]) }
    if (next[type].has(id)) next[type].delete(id); else next[type].add(id)
    syncSel(next)
  }
  const toggleAll = () => {
    const allR = rules.length > 0 && rules.every(x => selected.rules.has(x.id))
    const allO = orders.length > 0 && orders.every(x => selected.orders.has(x.id))
    const all = allR && allO
    syncSel({ rules: new Set(all ? [] : rules.map(x => x.id)), orders: new Set(all ? [] : orders.map(x => x.id)) })
  }

  const batchAction = async (action: string, label: string) => {
    const ruleIds = Array.from(selected.rules), orderIds = Array.from(selected.orders)
    const n = ruleIds.length + orderIds.length
    if (n === 0) { alert('请先勾选要' + label + '的项'); return }
    setBatchBusy(true)
    try {
      const _auth = { 'Authorization': 'Bearer ' + (() => { try { return localStorage.getItem('c_token') } catch { return '' } })(), 'Content-Type': 'application/json' }
      // 规则: 永久删除走 batch purge(批量接口), 其余走单条
      if (ruleIds.length) {
        if (action === 'permanent-delete') {
          await fetch(API + '/api/rules/batch', { method: 'POST', headers: _auth, body: JSON.stringify({ action: 'purge', ids: ruleIds }) })
        } else {
          await Promise.all(ruleIds.map(id => fetch(API + '/api/rules/' + id + '/' + action, { method: 'POST', headers: _auth })))
        }
      }
      // 订单: 单条(restore/permanent-delete)
      if (orderIds.length) {
        await Promise.all(orderIds.map(id => fetch(API + '/api/orders/' + id + '/' + action, { method: 'POST', headers: _auth })))
      }
      loadData()
      syncSel({ rules: new Set(), orders: new Set() })
    } catch (e: any) { alert(label + '失败: ' + e.message) }
    setBatchBusy(false)
  }
  const confirmPurge = () => {
    const n = selected.rules.size + selected.orders.size
    if (n === 0) { alert('请先勾选要永久删除的项'); return }
    if (window.confirm('永久删除 ' + n + ' 项？此操作不可撤销')) batchAction('permanent-delete', '永久删除')
  }

  // 锤子菜单事件联动(HammerRecycle dispatch)
  useEffect(() => {
    const hToggleAll = () => { toggleAll() }
    const hRestore = () => { batchAction('restore', '恢复') }
    const hPurge = () => { confirmPurge() }
    window.addEventListener('recycle-toggle-all', hToggleAll)
    window.addEventListener('recycle-restore', hRestore)
    window.addEventListener('recycle-purge', hPurge)
    return () => {
      window.removeEventListener('recycle-toggle-all', hToggleAll)
      window.removeEventListener('recycle-restore', hRestore)
      window.removeEventListener('recycle-purge', hPurge)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected, rules, orders])

  const renderList = (type: 'rules' | 'orders', items: any[]) => {
    if (items.length === 0) return <div className="small muted" style={{ padding: '20px', textAlign: 'center', fontSize: 'var(--font-13)' }}>{type === 'rules' ? t('recycle.empty_rules') : t('recycle.empty_orders')}</div>
    return (
      <div style={{ background: 'var(--card)', borderRadius: 'var(--radius-lg)', overflow: 'hidden' }}>
        {items.map((x: any) => {
          const isSel = selected[type].has(x.id)
          return (
            <div key={x.id} onClick={() => toggleSel(type, x.id)} className="clickable" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '12px 16px', borderBottom: '1px solid var(--border)', background: isSel ? 'rgba(29,78,216,0.08)' : 'transparent' }}>
              <span style={{ display: 'flex', alignItems: 'center', gap: 10, flex: 1, minWidth: 0 }}>
                <span style={{ width: 18, height: 18, borderRadius: 'var(--radius-xs)', border: '1.5px solid', borderColor: isSel ? 'var(--primary)' : 'var(--border)', background: isSel ? 'var(--primary)' : 'transparent', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', color: '#fff', fontSize: 'var(--font-xs)', flexShrink: 0 }}>{isSel ? <IconCheck size={12} /> : ''}</span>
                <span style={{ fontSize: 'var(--font-md)', color: 'var(--text)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{type === 'rules' ? x.name : (x.order_no + ' - ' + (x.product_name || ''))}</span>
              </span>
              {!isSel && <span style={{ fontSize: 'var(--font-xs)', color: 'var(--muted2)', flexShrink: 0, marginLeft: 8 }}>{x.deleted_at ? String(x.deleted_at).slice(0, 10) : ''}</span>}
            </div>
          )
        })}
      </div>
    )
  }

  return (
    <div style={{ padding: '16px 0', maxWidth: 500, margin: '0 auto' }}>
      {loading ? <div>{[1, 2, 3].map(i => <div key={i} className="skeleton" style={{ height: 48, borderRadius: 'var(--radius-lg)', marginBottom: 8 }} />)}</div> : <>
        <Group title={t('recycle.deleted_rules')}>
          <Row label={`已删除规则 ${selected.rules.size > 0 ? '· 已选 ' + selected.rules.size : ''}`} sub="点击行勾选，批量操作见右上角锤子" />
          {renderList('rules', rules)}
        </Group>
        <Group title={t('recycle.deleted_orders')}>
          <Row label={`已删除订单 ${selected.orders.size > 0 ? '· 已选 ' + selected.orders.size : ''}`} sub="点击行勾选，批量操作见右上角锤子" />
          {renderList('orders', orders)}
        </Group>
        {batchBusy && <div style={{ textAlign: 'center', padding: 16, fontSize: 'var(--font-sm)', color: 'var(--muted2)' }}>处理中...</div>}
      </>}
    </div>
  )
}
