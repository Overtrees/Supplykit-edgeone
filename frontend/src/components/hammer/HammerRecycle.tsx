import React from 'react'
import { useAppStore } from '../../store/useAppStore'
import { t } from '../../locale'

/** 回收站锤子菜单(精简 3 按钮, 混合规则+订单): 全选/取消 + 批量恢复 一排, 永久删除 单独一行 —— 事件联动 RecyclePage */
export default function HammerRecycle() {
  const { recycleSel, recycleBusy } = useAppStore()
  const total = (recycleSel?.rules || 0) + (recycleSel?.orders || 0)

  const dispatch = (name: string) => {
    window.dispatchEvent(new CustomEvent(name))
  }

  // 溢出保护: 按钮文本 nowrap + ellipsis, 计数合并显示
  const btnStyle: React.CSSProperties = { overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }

  return (
    <div>
      <div className="hammer-header">{t('settings.recycle_bin')}</div>
      {/* 第一排: 全选/取消 + 批量恢复 */}
      <div className="hammer-row">
        <button onClick={() => dispatch('recycle-toggle-all')} className="btn-ghost hammer-btn" style={btnStyle}>全选/取消</button>
        <button onClick={() => dispatch('recycle-restore')} disabled={recycleBusy || total === 0} className="btn-ghost hammer-btn" style={btnStyle}>
          批量恢复{total > 0 ? ` (${total})` : ''}
        </button>
      </div>
      {/* 第二排: 永久删除 单独一行 */}
      <div className="hammer-row">
        <button onClick={() => dispatch('recycle-purge')} disabled={recycleBusy || total === 0} className="btn-ghost hammer-btn" style={{ ...btnStyle, color: 'var(--danger)', width: '100%' }}>
          永久删除{total > 0 ? ` (${total})` : ''}
        </button>
      </div>
      <div style={{ fontSize: 'var(--font-10)', color: 'var(--muted2)', padding: '8px 2px 0', lineHeight: 1.5 }}>
        点击列表行勾选 → 锤子菜单批量操作。永久删除不可撤销。
      </div>
    </div>
  )
}
