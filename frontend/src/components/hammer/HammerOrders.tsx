import React, { useState, useEffect } from 'react'
import { useAppStore } from '../../store/useAppStore'
import { useDebouncedSearch } from '../../hooks/useDebounce'
import { useToast } from '../../components/Toast'
import { ORDER_COLS, ORDER_STATUSES, orderColKey, getOrderVis } from './configs'
import { IconExport } from '../Icons'
import { t } from '../../locale'

interface HammerOrdersProps { channel: string }

export default function HammerOrders({ channel }: HammerOrdersProps) {
  const toast = useToast()
  const { hammerPanel, setHammerPanel, hammerSearch, setHammerSearch, setHammerCols, setOrderFilterLocal, orderStatus } = useAppStore()
  const [localSearch, setLocalSearch] = useDebouncedSearch(hammerSearch, setHammerSearch)
  const [visCols, setVisCols] = useState(() => getOrderVis(channel) || ORDER_COLS.map(c => c.id))
  const [exporting, setExporting] = useState(false)

  useEffect(() => { setVisCols(getOrderVis(channel) || ORDER_COLS.map(c => c.id)) }, [channel])

  const saveCols = (cols) => {
    setVisCols(cols)
    localStorage.setItem(orderColKey(channel), JSON.stringify(cols))
    setHammerCols('orders', cols)
  }

  const doExport = async () => {
    setExporting(true)
    try {
      const API = import.meta.env.VITE_API_BASE_URL || ''
      const r = await fetch(API + '/api/exports?type=orders&channel=' + channel, {method:'POST', headers:{'Authorization':'Bearer '+(()=>{try{return localStorage.getItem('c_token')}catch{return ''}})()}})
      const d = await r.json()
      if (d.ok && d.task_id) toast.add({type:'success', title:'导出任务已提交', duration:6000, action:{label:'查看进度 →', handler:()=>{ window.__setPage && window.__setPage('tasks') }}})
      else throw new Error(d.error || '提交失败')
    } catch(e) { toast.error(t('export.failed')) }
    setExporting(false)
  }

  return (
    <div>
      <div className="hammer-header">{channel === 'jd' ? t('channel.jd') : t('channel.other')} · {t('nav.orders')}</div>
      <div style={{marginBottom:hammerPanel?8:0}}>
        <div className="hammer-row-2x2">
          <div className="hammer-row">
            <button onClick={() => setHammerPanel(hammerPanel === 'columns' ? null : 'columns')}
              className="btn-ghost hammer-btn">{t('common.columns')} ({visCols.length}/{ORDER_COLS.length})</button>
            <button onClick={() => setHammerPanel(hammerPanel === 'search' ? null : 'search')}
              className="btn-ghost hammer-btn">{t('common.search')}</button>
          </div>
          <div className="hammer-row">
            <button onClick={() => setHammerPanel(hammerPanel === 'filter' ? null : 'filter')}
              className="btn-ghost hammer-btn">{t('common.filter')}{orderStatus ? ' ✓' : ''}</button>
            <button onClick={doExport} disabled={exporting}
              className="clickable btn-ghost hammer-btn" style={{opacity:exporting?0.5:1}}>
              {exporting ? <span className="hammer-spinner" /> : <IconExport size={13} />} {exporting ? t('common.exporting') : t('common.export')}
            </button>
          </div>
        </div>
      </div>
      {hammerPanel === 'columns' && (
        <div className="hammer-panel hammer-panel-scroll">
          <div className="cols-top-bar">
            <button onClick={()=>saveCols(ORDER_COLS.map(c=>c.id))} className="hammer-clear">{t('common.all')}</button>
            <button onClick={()=>saveCols([])} className="hammer-clear">取消全选</button>
          </div>
          <div className="muted2 text-10" style={{marginBottom:2,padding:'0 4px'}}>{t('common.drag_hint')}</div>
          {visCols.length > 0 && <div className="cols-group-title"><span>已显示</span><span>{visCols.length}</span></div>}
          {visCols.map(id=>{
            const col=ORDER_COLS.find(c=>c.id===id);if(!col)return null
            return <div key={col.id} draggable
              onDragStart={e=>{e.dataTransfer.setData('text/plain',col.id);e.target.style.opacity='0.4';e.currentTarget.parentNode._dragId=col.id}}
              onDragEnd={e=>e.target.style.opacity='1'}
              onDragOver={e=>{e.preventDefault();e.currentTarget.style.borderTop='2px solid var(--primary)';const from=e.currentTarget.parentNode._dragId;if(from&&from!==col.id){const nxt=visCols.filter(c=>c!==from);const toIdx=nxt.indexOf(col.id);nxt.splice(toIdx,0,from);saveCols(nxt)}}}
              onDragLeave={e=>e.currentTarget.style.borderTop='1px solid transparent'}
              onDrop={e=>{e.preventDefault();e.currentTarget.style.borderTop='1px solid transparent';const from=e.dataTransfer.getData('text/plain');if(from===col.id)return;const nxt=visCols.filter(c=>c!==from);const toIdx=nxt.indexOf(col.id);nxt.splice(toIdx,0,from);saveCols(nxt);e.currentTarget.parentNode._dragId=null}}
              className="col-drag visible">
              <span className="muted2 text-12" style={{width:16,flexShrink:0,textAlign:'center',cursor:'grab'}}>⠿</span>
              <input type="checkbox" checked onChange={e=>{saveCols(visCols.filter(c=>c!==col.id))}} className="accent-primary" />
              <span className="flex-1 text-12">{col.label}</span>
              <span className="muted2 text-9">#{visCols.indexOf(col.id)+1}</span>
            </div>
          })}
          {(()=>{
            const hidden=ORDER_COLS.filter(c=>!visCols.includes(c.id))
            if(hidden.length===0)return null
            return <>
              <div className="cols-group-title"><span>已隐藏</span><span>{hidden.length}</span></div>
              {hidden.map(col=>
                <div key={col.id} className="col-drag hidden">
                  <span className="muted2" style={{width:16,flexShrink:0,textAlign:'center'}}>○</span>
                  <input type="checkbox" onChange={e=>{saveCols([...visCols,col.id])}} className="accent-primary" />
                  <span className="flex-1 text-12">{col.label}</span>
                </div>
              )}
            </>
          })()}
        </div>
      )}
      {hammerPanel === 'search' && (
        <div className="hammer-panel">
          <input id="hm-search-orders" value={localSearch} onChange={e=>setLocalSearch(e.target.value)}
            placeholder="搜索单号/商品/SKU..." className="hammer-input" />
          {hammerSearch && <div className="text-right mt-8">
            <button className="hammer-clear" onClick={()=>setHammerSearch('')}>{t('common.clear')}</button>
          </div>}
        </div>
      )}
      {hammerPanel === 'filter' && (
        <div className="hammer-panel">
          <div className="muted2 text-10 mb-4">{t('common.order_status')}</div>
          <div className="flex flex-wrap gap-4">
            {ORDER_STATUSES.map(s => (
              <span key={s} onClick={() => setOrderFilterLocal('', s)}
                style={{fontSize:12,padding:'4px 10px',borderRadius:99,cursor:'pointer',
                  background: (orderStatus === s || (!orderStatus && !s)) ? 'var(--primary)' : 'var(--gray)',
                  color: (orderStatus === s || (!orderStatus && !s)) ? '#fff' : 'var(--text)',
                  fontWeight: orderStatus === s ? 600 : 400
                }}>
                {s || t('common.all')}
              </span>
            ))}
          </div>
          {orderStatus && <div className="text-right mt-8">
            <button onClick={()=>setOrderFilterLocal('','')} className="hammer-clear">{t('common.clear_filter')}</button>
          </div>}
        </div>
      )}
    </div>
  )
}