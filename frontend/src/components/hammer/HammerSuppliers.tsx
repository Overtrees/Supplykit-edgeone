import React, { useState, useEffect } from 'react'
import { useAppStore } from '../../store/useAppStore'
import { useDebouncedSearch } from '../../hooks/useDebounce'
import { SUPPLIER_COLS, suppColKey, getSuppVis } from './configs'
import { t } from '../../locale'

interface HammerSuppliersProps {
  channel: string
}

export default function HammerSuppliers({ channel }: HammerSuppliersProps) {
  const { hammerPanel, setHammerPanel, hammerSearch, setHammerSearch, setHammerCols } =
    useAppStore()
  const [localSearch, setLocalSearch] = useDebouncedSearch(hammerSearch, setHammerSearch)
  const [visCols, setVisCols] = useState(() => getSuppVis(channel) || SUPPLIER_COLS.map(c => c.id))

  useEffect(() => {
    setVisCols(getSuppVis(channel) || SUPPLIER_COLS.map(c => c.id))
  }, [channel])

  const saveCols = cols => {
    setVisCols(cols)
    localStorage.setItem(suppColKey(channel), JSON.stringify(cols))
    setHammerCols('suppliers', cols)
  }

  return (
    <div>
      <div className="hammer-header">
        {channel === 'jd' ? t('channel.jd') : t('channel.other')} · {t('nav.suppliers')}
      </div>
      <div className="hammer-btn-row">
        <button
          onClick={() => setHammerPanel(hammerPanel === 'columns' ? null : 'columns')}
          className="btn-ghost hammer-btn"
        >
          列选择 ({visCols.length}/{SUPPLIER_COLS.length})
        </button>
        <button
          onClick={() => {
            setHammerPanel(hammerPanel === 'search' ? null : 'search')
            if (hammerPanel !== 'search')
              setTimeout(() => document.getElementById('hm-search-supp')?.focus(), 100)
          }}
          className="btn-ghost hammer-btn"
        >
          {t('common.search')}
        </button>
      </div>
      {hammerPanel === 'columns' && (
        <div className="hammer-panel hammer-panel-scroll">
          <div className="cols-top-bar">
            <button onClick={() => saveCols(SUPPLIER_COLS.map(c => c.id))} className="hammer-clear">
              {t('common.all')}
            </button>
            <button onClick={() => saveCols([])} className="hammer-clear">
              取消全选
            </button>
          </div>
          <div className="muted2 text-10" style={{ marginBottom: 2, padding: '0 4px' }}>
            {t('common.drag_hint')}
          </div>
          {visCols.length > 0 && (
            <div className="cols-group-title">
              <span>已显示</span>
              <span>{visCols.length}</span>
            </div>
          )}
          {visCols.map(id => {
            const col = SUPPLIER_COLS.find(c => c.id === id)
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
                <span className="flex-1 text-12">{col.label}</span>
                <span className="muted2 text-9">#{visCols.indexOf(col.id) + 1}</span>
              </div>
            )
          })}
          {(() => {
            const hidden = SUPPLIER_COLS.filter(c => !visCols.includes(c.id))
            if (hidden.length === 0) return null
            return (
              <>
                <div className="cols-group-title">
                  <span>已隐藏</span>
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
                    <span className="flex-1 text-12">{col.label}</span>
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
            id="hm-search-supp"
            value={localSearch}
            onChange={e => setLocalSearch(e.target.value)}
            placeholder="搜索供应商名称/编号..."
            className="hammer-input"
          />
          {hammerSearch && (
            <div className="text-right mt-8">
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
