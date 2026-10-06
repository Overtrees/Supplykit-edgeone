import React from 'react'
import { useAppStore } from '../../store/useAppStore'
import { t } from '../../locale'

/** 回收站锤子菜单(批量操作): 全选/批量恢复/永久删除 × 规则/订单 —— 事件联动 RecyclePage */
export default function HammerRecycle() {
  const { recycleSel, recycleBusy, setHammerPanel, hammerPanel } = useAppStore()

  const dispatch = (name: string, type: 'rules' | 'orders') => {
    window.dispatchEvent(new CustomEvent(name, { detail: type }))
  }

  return (
    <div>
      <div className="hammer-header">{t('settings.recycle_bin')}</div>
      <div className="hammer-row-2x2">
        <div className="hammer-row">
          <button onClick={() => dispatch('recycle-toggle-all', 'rules')} className="btn-ghost hammer-btn">规则全选/取消</button>
          <button onClick={() => dispatch('recycle-toggle-all', 'orders')} className="btn-ghost hammer-btn">订单全选/取消</button>
        </div>
        <div className="hammer-row">
          <button onClick={() => dispatch('recycle-restore', 'rules')} disabled={recycleBusy} className="btn-ghost hammer-btn">恢复规则 ({recycleSel.rules})</button>
          <button onClick={() => dispatch('recycle-restore', 'orders')} disabled={recycleBusy} className="btn-ghost hammer-btn">恢复订单 ({recycleSel.orders})</button>
        </div>
        <div className="hammer-row">
          <button onClick={() => dispatch('recycle-purge', 'rules')} disabled={recycleBusy} className="btn-ghost hammer-btn" style={{ color: 'var(--danger)' }}>永久删除规则 ({recycleSel.rules})</button>
          <button onClick={() => dispatch('recycle-purge', 'orders')} disabled={recycleBusy} className="btn-ghost hammer-btn" style={{ color: 'var(--danger)' }}>永久删除订单 ({recycleSel.orders})</button>
        </div>
      </div>
      {hammerPanel === 'recycle-help' && (
        <div className="hammer-panel">
          <div style={{ fontSize: 'var(--font-xs)', color: 'var(--muted2)', lineHeight: 1.6 }}>
            点击列表行勾选 → 锤子菜单批量恢复/永久删除。永久删除不可撤销。
          </div>
        </div>
      )}
    </div>
  )
}
