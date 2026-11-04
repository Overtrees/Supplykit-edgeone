import React, { useState, useEffect } from 'react'
import { apiJson } from '../../api/client'
import type { OkResponse } from '../../api/types'
import { t } from '../../locale'
import { useAppStore } from '../../store/useAppStore'
import { useDebouncedSearch } from '../../hooks/useDebounce'
import { useToast } from '../../components/Toast'
import { INV_COLS, getInvVis, invColKey } from './configs'
import { whTypeLabel } from '../statusLabels'
import { IconExport, IconClipboard } from '../Icons'
import ConfirmDialog from '../ConfirmDialog'
import FlowRecordsSheet from '../FlowRecordsSheet'
import { colLabel } from '../colLabels'

interface HammerInventoryProps {
  channel: string
}

export default function HammerInventory({ channel }: HammerInventoryProps) {
  const toast = useToast()
  const {
    hammerPanel,
    setHammerPanel,
    hammerSearch,
    setHammerSearch,
    setHammerCols,
    hammerWhType,
    setHammerWhType,
    invBatch,
    invSelIds,
  } = useAppStore()
  const [localSearch, setLocalSearch] = useDebouncedSearch(hammerSearch, setHammerSearch)
  const [confirmBatchDel, setConfirmBatchDel] = useState(false)
  const [visCols, setVisCols] = useState(
    () => getInvVis(hammerWhType, channel) || INV_COLS[hammerWhType].map(c => c.id),
  )
  const [exporting, setExporting] = useState(false)
  const [flowOpen, setFlowOpen] = useState(false) // 2026-10-29 出入库明细弹窗

  useEffect(() => {
    const saved = getInvVis(hammerWhType, channel) || INV_COLS[hammerWhType].map(c => c.id)
    setVisCols(saved)
    setHammerCols('inventory_' + hammerWhType, saved)
  }, [hammerWhType])

  const saveCols = cols => {
    setVisCols(cols)
    localStorage.setItem(invColKey(hammerWhType, channel), JSON.stringify(cols))
    setHammerCols('inventory_' + hammerWhType, cols)
  }

  const switchWh = v => {
    setHammerWhType(v)
    const saved = getInvVis(v, channel) || INV_COLS[v].map(c => c.id)
    setVisCols(saved)
    setHammerCols('inventory_' + v, saved)
  }

  const doExport = async () => {
    setExporting(true)
    try {
      const API = import.meta.env.VITE_API_BASE_URL || ''
      // S3-2 收口: 导出是重操作, 超时 90s
      const d = await apiJson<OkResponse>(
        'POST',
        API + '/api/exports?type=inventory&channel=' + channel + '&wh_type=' + hammerWhType,
        { timeout: 90000 },
      )
      if (d.ok && d.task_id)
        toast.add({
          type: 'success',
          title: t('hx.export_submitted'),
          duration: 6000,
          action: {
            label: t('hx.view_progress'),
            handler: () => {
              window.__setPage && window.__setPage('tasks')
            },
          },
        })
      else throw new Error(d.error || '提交失败')
    } catch (e) {
      toast.error(t('export.failed'))
    }
    setExporting(false)
  }
  return (
    <div>
      <div className="hammer-header">
        {channel === 'jd' ? t('channel.jd') : t('channel.other')} · {t('nav.inv')}
      </div>
      {/* 功能按钮行 — B 型布局 2×2 */}
      <div className="hammer-row-2x2" style={{ marginBottom: hammerPanel ? 8 : 0 }}>
        <div className="hammer-row">
          <button
            onClick={() => setHammerPanel(hammerPanel === 'columns' ? null : 'columns')}
            className="hammer-btn btn-ghost"
          >
            {t('common.columns')} ({visCols.length}/{INV_COLS[hammerWhType].length})
          </button>
          <button
            onClick={() => setHammerPanel(hammerPanel === 'search' ? null : 'search')}
            className="hammer-btn btn-ghost"
          >
            {t('common.search')}
          </button>
        </div>
        <div className="hammer-row">
          <button
            onClick={() => setHammerPanel(hammerPanel === 'wh' ? null : 'wh')}
            className="hammer-btn btn-ghost"
          >
            {whTypeLabel(hammerWhType)}
          </button>
          <button
            onClick={doExport}
            disabled={exporting}
            className="clickable hammer-btn btn-ghost"
            style={{ opacity: exporting ? 0.5 : 1 }}
          >
            {exporting ? <span className="hammer-spinner" /> : <IconExport size={13} />}{' '}
            {exporting ? t('common.exporting') : t('common.export')}
          </button>
        </div>
        {/* 2026-10-29 出入库明细(流水台账, 含效期/数量/供应商或目标仓) */}
        <div className="hammer-row">
          <button onClick={() => setFlowOpen(true)} className="hammer-btn btn-ghost">
            <IconClipboard size={13} /> {t('hi.flow_detail')}
          </button>
          <button
            onClick={() => setHammerPanel(hammerPanel === 'batch' ? null : 'batch')}
            className="hammer-btn btn-ghost"
            style={
              useAppStore.getState().invBatch
                ? { borderColor: 'var(--danger)', color: 'var(--danger)' }
                : undefined
            }
          >
            {t('inv.batch.open')}
          </button>
        </div>
      </div>
      {/* 批量操作面板(2026-11-04 订单同款交互, 按库存语义适配) */}
      {hammerPanel === 'batch' && (
        <div className="hammer-panel">
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              marginBottom: 8,
            }}
          >
            <span className="text-12 muted2">
              {t('inv.batch.selected_pre')}{' '}
              <b style={{ color: 'var(--text)' }}>{invSelIds.length}</b>{' '}
              {t('inv.batch.selected_post')}
            </span>
            {invBatch ? (
              <button
                className="hammer-clear"
                onClick={() => {
                  useAppStore.getState().setInvBatch(false)
                  useAppStore.getState().setInvSelIds([])
                }}
              >
                {t('inv.batch.exit')}
              </button>
            ) : (
              <button
                className="hammer-clear"
                onClick={() => useAppStore.getState().setInvBatch(true)}
              >
                {t('inv.batch.enter')}
              </button>
            )}
          </div>
          <div className="hm-group">
            <button
              className="hammer-btn btn-ghost"
              onClick={() => {
                const s = useAppStore.getState()
                if (!s.invBatch) s.setInvBatch(true)
                window.dispatchEvent(new Event('inventory-toggle-all'))
              }}
            >
              {t('inv.batch.toggle_all')}
            </button>
          </div>
          <div className="hammer-btn-row" style={{ marginTop: 8 }}>
            <button
              className="hammer-btn btn-ghost"
              style={{ color: 'var(--danger)', opacity: invSelIds.length === 0 ? 0.4 : 1 }}
              disabled={invSelIds.length === 0}
              onClick={() => setConfirmBatchDel(true)}
            >
              {t('inv.batch.delete')}
            </button>
            <button
              className="hammer-btn btn-ghost"
              onClick={() => {
                setHammerPanel(null)
                window.dispatchEvent(new Event('inventory-goto-recycle'))
              }}
              title={t('inv.batch.goto_restore_title')}
            >
              {t('inv.batch.goto_restore')}
            </button>
          </div>
          <div className="muted2 text-10" style={{ marginTop: 8, lineHeight: 1.6 }}>
            {t('inv.batch.hint_1')}
            <br />
            {t('inv.batch.hint_2')}
          </div>
        </div>
      )}
      {confirmBatchDel && (
        <ConfirmDialog
          open
          title={t('inv.batch.confirm_title')}
          desc={t('inv.batch.confirm_desc', { n: useAppStore.getState().invSelIds.length })}
          confirmLabel={t('common.delete')}
          onConfirm={() => {
            setConfirmBatchDel(false)
            window.dispatchEvent(new Event('inventory-batch-delete'))
          }}
          onCancel={() => setConfirmBatchDel(false)}
        />
      )}
      {/* 列选择面板 */}
      {hammerPanel === 'columns' && (
        <div className="hammer-panel hammer-panel-scroll">
          <div className="cols-top-bar">
            <button
              onClick={() => saveCols(INV_COLS[hammerWhType].map(c => c.id))}
              className="hammer-clear"
            >
              {t('common.all')}
            </button>
            <button onClick={() => saveCols([])} className="hammer-clear">
              {t('common.deselect')}
            </button>
          </div>
          <div className="muted2 text-10" style={{ marginBottom: 2, padding: '0 4px' }}>
            {t('common.drag_hint')}
          </div>
          {visCols.length > 0 && (
            <div className="cols-group-title">
              <span>{t('common.visible')}</span>
              <span>{visCols.length}</span>
            </div>
          )}
          {visCols.map(id => {
            const col = INV_COLS[hammerWhType].find(c => c.id === id)
            if (!col) return null
            return (
              <div
                key={col.id}
                draggable
                onDragStart={e => {
                  e.dataTransfer.setData('text/plain', col.id)
                  ;(e.target as HTMLElement).style.opacity = '0.4'
                  ;(
                    e.currentTarget.parentNode as HTMLElement & { _dragId?: string | null }
                  )._dragId = col.id
                }}
                onDragEnd={e => ((e.target as HTMLElement).style.opacity = '1')}
                onDragOver={e => {
                  e.preventDefault()
                  e.currentTarget.style.borderTop = '2px solid var(--primary)'
                  const from = (
                    e.currentTarget.parentNode as HTMLElement & { _dragId?: string | null }
                  )._dragId
                  if (from && from !== col.id) {
                    const nxt = visCols.filter(c => c !== from)
                    const toIdx = nxt.indexOf(col.id)
                    nxt.splice(toIdx, 0, from)
                    saveCols(nxt)
                  }
                }}
                onDragLeave={e => (e.currentTarget.style.borderTop = '1px solid transparent')}
                onDrop={e => {
                  e.preventDefault()
                  e.currentTarget.style.borderTop = '1px solid transparent'
                  const from = e.dataTransfer.getData('text/plain')
                  if (from === col.id) return
                  const nxt = visCols.filter(c => c !== from)
                  const toIdx = nxt.indexOf(col.id)
                  nxt.splice(toIdx, 0, from)
                  saveCols(nxt)
                  ;(
                    e.currentTarget.parentNode as HTMLElement & { _dragId?: string | null }
                  )._dragId = null
                }}
                className="col-drag visible"
              >
                <span
                  className="muted2 text-12"
                  style={{ width: 16, flexShrink: 0, textAlign: 'center', cursor: 'grab' }}
                >
                  ⠿
                </span>
                <input
                  type="checkbox"
                  checked
                  onChange={e => {
                    saveCols(visCols.filter(c => c !== col.id))
                  }}
                  className="accent-primary"
                />
                <span className="flex-1 text-12">{colLabel(col.id)}</span>
                <span className="muted2 text-9">#{visCols.indexOf(col.id) + 1}</span>
              </div>
            )
          })}
          {(() => {
            const hidden = INV_COLS[hammerWhType].filter(c => !visCols.includes(c.id))
            if (hidden.length === 0) return null
            return (
              <>
                <div className="cols-group-title">
                  <span>{t('common.hidden')}</span>
                  <span>{hidden.length}</span>
                </div>
                {hidden.map(col => (
                  <div key={col.id} className="col-drag hidden">
                    <span
                      className="muted2"
                      style={{ width: 16, flexShrink: 0, textAlign: 'center' }}
                    >
                      ○
                    </span>
                    <input
                      type="checkbox"
                      onChange={e => {
                        saveCols([...visCols, col.id])
                      }}
                      className="accent-primary"
                    />
                    <span className="flex-1 text-12">{colLabel(col.id)}</span>
                  </div>
                ))}
              </>
            )
          })()}
        </div>
      )}
      {/* 搜索面板 */}
      {hammerPanel === 'search' && (
        <div className="hammer-panel">
          <input
            id="hm-search-inv"
            value={localSearch}
            onChange={e => setLocalSearch(e.target.value)}
            placeholder={t('hi.search_ph')}
            className="hammer-input"
          />
          {hammerSearch && (
            <div style={{ marginTop: 4, textAlign: 'right' }}>
              <button className="hammer-clear" onClick={() => setHammerSearch('')}>
                {t('common.clear')}
              </button>
            </div>
          )}
        </div>
      )}
      {/* 仓库类型面板 */}
      {hammerPanel === 'wh' && (
        <div className="hammer-panel">
          <div className="muted2 text-10 mb-4">{t('hi.wh_type')}</div>
          <div className="hammer-btn-row">
            {Object.keys(INV_COLS).map(k => {
              if (
                k === 'platform_b' &&
                (channel !== 'jd' || useAppStore.getState().hammerReplenMode !== 'bbcc')
              )
                return null
              return (
                <span
                  key={k}
                  onClick={() => switchWh(k)}
                  className={'hammer-tab' + (hammerWhType === k ? ' active' : '')}
                >
                  {whTypeLabel(k)}
                </span>
              )
            })}
          </div>
        </div>
      )}
      <FlowRecordsSheet open={flowOpen} onClose={() => setFlowOpen(false)} channel={channel} />
    </div>
  )
}
