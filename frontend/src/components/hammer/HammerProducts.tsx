import React, { useState, useEffect } from 'react'
import { useAppStore } from '../../store/useAppStore'
import { useToast } from '../../components/Toast'
import { useDebouncedSearch } from '../../hooks/useDebounce'
import { PRODUCT_COLS, prodColKey, getProdVis } from './configs'
import { t } from '../../locale'
import { api } from '../../api/client'
import { colLabel } from '../colLabels'

interface HammerProductsProps {
  channel: string
}

export default function HammerProducts({ channel }: HammerProductsProps) {
  const toast = useToast()
  const {
    hammerPanel,
    setHammerPanel,
    hammerSearch,
    setHammerSearch,
    setHammerCols,
    prodSelIds,
    batchStateMap,
  } = useAppStore()
  // 批量按钮状态判断: 所选全部启用→批量启用禁用; 全部停用→批量停用禁用; 混合→都可用
  const selSt = prodSelIds || []
  const stMap = batchStateMap || {}
  const allActive = selSt.length > 0 && selSt.every(id => stMap[id] === 1)
  const allInactive = selSt.length > 0 && selSt.every(id => stMap[id] === 0)
  const [localSearch, setLocalSearch] = useDebouncedSearch(hammerSearch, setHammerSearch)
  const [visCols, setVisCols] = useState(() => getProdVis(channel) || PRODUCT_COLS.map(c => c.id))

  useEffect(() => {
    setVisCols(getProdVis(channel) || PRODUCT_COLS.map(c => c.id))
  }, [channel])

  const [batchBusy, setBatchBusy] = useState(false)
  const runBatch = async (action, label) => {
    const s = useAppStore.getState()
    const ids = s.prodSelIds || []
    if (ids.length === 0) {
      toast.error(t('hx.pick_prod'))
      return
    }
    if (action === 'delete' && !window.confirm(t('hx.prod_del_confirm', { n: ids.length })))
      return
    setBatchBusy(true)
    try {
      // 统一走 api.post：自动注入 token/channel + 响应解包 + 缓存失效
      await api.postHeavy('/api/products/batch', { action, ids })
      if (action === 'delete') {
        // 批量删除可撤销（5s 窗口）
        toast.add({
          type: 'success',
          title: t('hx.done_n', { label, n: ids.length }),
          duration: 5000,
          action: {
            label: t('hx.undo'),
            handler: async () => {
              try {
                await api.postHeavy('/api/products/batch', { action: 'restore', ids })
                toast.success(t('hx.undone'))
                s.setProdBatchSel([])
                s.setProdBatch(false)
                s.bumpProdBatchVersion()
              } catch (e) {
                toast.error(t('hx.undo_failed', { msg: e.message || '' }))
              }
            },
          },
        })
      } else {
        toast.success(t('hx.done_n', { label, n: ids.length }))
      }
      s.setProdBatchSel([])
      s.setProdBatch(false)
      s.bumpProdBatchVersion()
    } catch (e) {
      toast.error(t('hx.fail', { label, msg: e.message || '' }))
    }
    setBatchBusy(false)
  }
  const saveCols = cols => {
    setVisCols(cols)
    localStorage.setItem(prodColKey(channel), JSON.stringify(cols))
    setHammerCols('products', cols)
  }

  return (
    <div>
      <div className="hammer-header">
        {channel === 'jd' ? t('channel.jd') : t('channel.other')} · {t('nav.products')}
      </div>
      <div className="hammer-btn-row" style={{ marginBottom: hammerPanel ? 8 : 0 }}>
        <button
          onClick={() => setHammerPanel(hammerPanel === 'columns' ? null : 'columns')}
          className="btn-ghost hammer-btn"
        >
          {t('common.columns')} ({visCols.length}/{PRODUCT_COLS.length})
        </button>
        <button
          onClick={() => {
            setHammerPanel(hammerPanel === 'search' ? null : 'search')
            if (hammerPanel !== 'search')
              setTimeout(() => document.getElementById('hm-search-prod')?.focus(), 100)
          }}
          className="btn-ghost hammer-btn"
        >
          {t('common.search')}
        </button>
        <button
          onClick={() => setHammerPanel(hammerPanel === 'batch' ? null : 'batch')}
          className="btn-ghost hammer-btn"
          style={
            useAppStore.getState().prodBatch
              ? { borderColor: 'var(--danger)', color: 'var(--danger)' }
              : undefined
          }
        >
          {t('hx.batch')}
        </button>
      </div>
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
              {t('hx.selected_pre')}{' '}
              <b style={{ color: 'var(--text)' }}>
                {(useAppStore.getState().prodSelIds || []).length}
              </b>{' '}
              {t('hx.selected_suf')}
            </span>
            {useAppStore.getState().prodBatch ? (
              <button
                className="hammer-clear"
                onClick={() => {
                  useAppStore.getState().setProdBatch(false)
                  useAppStore.getState().setProdBatchSel([])
                }}
              >
                {t('hx.exit_batch')}
              </button>
            ) : (
              <button
                className="hammer-clear"
                onClick={() => useAppStore.getState().setProdBatch(true)}
              >
                {t('hx.enter_batch')}
              </button>
            )}
          </div>
          <div className="hm-group">
            <button
              className="hammer-btn btn-ghost"
              onClick={() => {
                const s = useAppStore.getState()
                if (!s.prodBatch) s.setProdBatch(true)
                s.requestProdBatchAll()
              }}
            >
              {t('hx.select_all_toggle')}
            </button>
          </div>
          <div className="hammer-btn-row" style={{ marginTop: 8 }}>
            <button
              className="hammer-btn btn-ghost"
              style={{
                color: 'var(--success)',
                opacity: batchBusy || selSt.length === 0 || allActive ? 0.4 : 1,
              }}
              disabled={batchBusy || selSt.length === 0 || allActive}
              onClick={() => runBatch('active', t('hx.act_enable'))}
            >
              {t('hx.batch_enable')}
            </button>
            <button
              className="hammer-btn btn-ghost"
              style={{
                color: 'var(--warning)',
                opacity: batchBusy || selSt.length === 0 || allInactive ? 0.4 : 1,
              }}
              disabled={batchBusy || selSt.length === 0 || allInactive}
              onClick={() => runBatch('inactive', t('hx.act_disable'))}
            >
              {t('hx.batch_disable')}
            </button>
            <button
              className="hammer-btn btn-ghost"
              style={{ color: 'var(--danger)', opacity: batchBusy || selSt.length === 0 ? 0.4 : 1 }}
              disabled={batchBusy || selSt.length === 0}
              onClick={() => runBatch('delete', t('hx.act_delete'))}
            >
              {t('hx.batch_delete')}
            </button>
          </div>
          <div className="muted2 text-10" style={{ marginTop: 8 }}>
            {t('hx.prod_batch_hint')}
            {allActive
              ? ' · ' + t('hx.prod_all_active')
              : allInactive
                ? ' · ' + t('hx.prod_all_inactive')
                : ''}
          </div>
        </div>
      )}
      {hammerPanel === 'columns' && (
        <div className="hammer-panel hammer-panel-scroll">
          <div className="cols-top-bar">
            <button onClick={() => saveCols(PRODUCT_COLS.map(c => c.id))} className="hammer-clear">
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
            const col = PRODUCT_COLS.find(c => c.id === id)
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
            const hidden = PRODUCT_COLS.filter(c => !visCols.includes(c.id))
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
      {hammerPanel === 'search' && (
        <div className="hammer-panel">
          <input
            id="hm-search-prod"
            value={localSearch}
            onChange={e => setLocalSearch(e.target.value)}
            placeholder={t('hx.search_prod_ph')}
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
    </div>
  )
}
