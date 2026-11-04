import React from 'react'
import { useAppStore } from '../../store/useAppStore'
import { t } from '../../locale'

/** 回收站锤子菜单(精简 3 按钮, 混合规则+订单+库存): 全选/取消 + 批量恢复 一排, 永久删除 单独一行 —— 事件联动 RecyclePage
 *
 * 2026-11-04 加库存归档: 计数拆两套 —— 恢复吃三类(规则/订单/库存快照),
 * 永久删除只吃规则+订单(库存归档是快照行, 只有"恢复"语义, 没有"永久删除"入口)。
 * 用同一个 total 会让"永久删除 (5)"里混进 3 条库存, 点了必然失败。
 */
export default function HammerRecycle() {
  const { recycleSel, recycleBusy } = useAppStore()
  const nRules = recycleSel?.rules || 0
  const nOrders = recycleSel?.orders || 0
  const nInv = recycleSel?.inventory || 0
  const totalRestore = nRules + nOrders + nInv
  const totalPurge = nRules + nOrders

  const dispatch = (name: string) => {
    window.dispatchEvent(new CustomEvent(name))
  }

  // 溢出保护: 按钮文本 nowrap + ellipsis, 计数合并显示
  const btnWidth: React.CSSProperties = {
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  }

  return (
    <div>
      <div className="hammer-header">{t('settings.recycle_bin')}</div>
      {/* 与规则页同构: hammer-row-2x2(行间距8 + 行内gap8 + 按钮flex:1) */}
      <div className="hammer-row-2x2">
        {/* 第一排: 全选/取消 + 批量恢复 */}
        <div className="hammer-row">
          <button
            onClick={() => dispatch('recycle-toggle-all')}
            className="btn-ghost hammer-btn"
            style={btnWidth}
          >
            全选/取消
          </button>
          <button
            onClick={() => dispatch('recycle-restore')}
            disabled={recycleBusy || totalRestore === 0}
            className="btn-ghost hammer-btn"
            style={btnWidth}
          >
            批量恢复{totalRestore > 0 ? ` (${totalRestore})` : ''}
          </button>
        </div>
        {/* 第二排: 永久删除 单独一行(不含库存归档) */}
        <div className="hammer-row">
          <button
            onClick={() => dispatch('recycle-purge')}
            disabled={recycleBusy || totalPurge === 0}
            className="btn-ghost hammer-btn"
            style={{ ...btnWidth, color: 'var(--danger)' }}
          >
            永久删除{totalPurge > 0 ? ` (${totalPurge})` : ''}
          </button>
        </div>
      </div>
      <div
        style={{
          fontSize: 'var(--font-10)',
          color: 'var(--muted2)',
          padding: '8px 2px 0',
          lineHeight: 1.5,
        }}
      >
        点击列表行勾选 → 锤子菜单批量操作。永久删除不可撤销。
        {nInv > 0 && <br />}
        {nInv > 0 && '库存归档只支持恢复，不参与永久删除。'}
      </div>
    </div>
  )
}
