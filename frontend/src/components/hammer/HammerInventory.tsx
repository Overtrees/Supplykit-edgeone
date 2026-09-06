import React, { useState, useEffect } from 'react'
import { t } from "../../locale"
import { useAppStore } from '../../store/useAppStore'
import { useDebouncedSearch } from '../../hooks/useDebounce'
import { useToast } from '../../components/Toast'
import { INV_COLS, INV_COL_KEY, getInvVis, invColKey, INV_WH_LABEL } from './configs'
import { IconExport } from '../Icons'

interface HammerInventoryProps { channel: string }

export default function HammerInventory({ channel }: HammerInventoryProps) {
  const toast = useToast()
  const { hammerPanel, setHammerPanel, hammerSearch, setHammerSearch, setHammerCols, hammerWhType, setHammerWhType } = useAppStore()
  const [localSearch, setLocalSearch] = useDebouncedSearch(hammerSearch, setHammerSearch)
  const [visCols, setVisCols] = useState(() => getInvVis(hammerWhType, channel) || INV_COLS[hammerWhType].map(c => c.id))
  const [exporting, setExporting] = useState(false)

  useEffect(() => {
    const saved = getInvVis(hammerWhType, channel) || INV_COLS[hammerWhType].map(c => c.id)
    setVisCols(saved)
    setHammerCols('inventory_' + hammerWhType, saved)
  }, [hammerWhType])

  const saveCols = (cols) => {
    setVisCols(cols)
    localStorage.setItem(invColKey(hammerWhType, channel), JSON.stringify(cols))
    setHammerCols('inventory_' + hammerWhType, cols)
  }

  const switchWh = (v) => {
    setHammerWhType(v)
    const saved = getInvVis(v, channel) || INV_COLS[v].map(c => c.id)
    setVisCols(saved)
    setHammerCols('inventory_' + v, saved)
  }

  const doExport = async () => {
    setExporting(true)
    try {
      const API = import.meta.env.VITE_API_BASE_URL || ''
      const r = await fetch(API + '/api/exports?type=inventory&channel=' + channel + '&wh_type=' + hammerWhType, {method:'POST', headers:{'Authorization':'Bearer '+(()=>{try{return localStorage.getItem('c_token')}catch{return ''}})()}})
      const d = await r.json()
      if (d.ok && d.task_id) toast.add({type:'success', title:'导出任务已提交', duration:6000, action:{label:'查看进度 →', handler:()=>{ window.__setPage && window.__setPage('tasks') }}})
      else throw new Error(d.error || '提交失败')
    } catch(e) { toast.error(t('export.failed')) }
    setExporting(false)
  }
  return (
    <div>
      <div className="hammer-header">{channel === 'jd' ? t('channel.jd') : t('channel.other')} · {t('nav.inv')}</div>
      {/* 功能按钮行 — B 型布局 2×2 */}
      <div className="hammer-row-2x2" style={{marginBottom:hammerPanel?8:0}}>
        <div className="hammer-row">
          <button onClick={() => setHammerPanel(hammerPanel === 'columns' ? null : 'columns')}
            className="hammer-btn btn-ghost">{t('common.columns')} ({visCols.length}/{INV_COLS[hammerWhType].length})</button>
          <button onClick={() => setHammerPanel(hammerPanel === 'search' ? null : 'search')}
            className="hammer-btn btn-ghost">{t('common.search')}</button>
        </div>
        <div className="hammer-row">
          <button onClick={() => setHammerPanel(hammerPanel === 'wh' ? null : 'wh')}
            className="hammer-btn btn-ghost">仓库 {INV_WH_LABEL[hammerWhType]}</button>
          <button onClick={doExport} disabled={exporting}
            className="clickable hammer-btn btn-ghost" style={{opacity:exporting?0.5:1}}>
            {exporting ? <span className="hammer-spinner" /> : <IconExport size={13} />} {exporting ? t('common.exporting') : t('common.export')}
          </button>
        </div>
      </div>
      {/* 列选择面板 */}
      {hammerPanel === 'columns' && (
        <div className="hammer-panel hammer-panel-scroll">
          <div className="cols-top-bar">
            <button onClick={()=>saveCols(INV_COLS[hammerWhType].map(c=>c.id))} className="hammer-clear">{t("common.all")}</button>
            <button onClick={()=>saveCols([])} className="hammer-clear">取消全选</button>
          </div>
          <div className="muted2 text-10" style={{marginBottom:2,padding:'0 4px'}}>{t("common.drag_hint")}</div>
          {visCols.length > 0 && <div className="cols-group-title"><span>已显示</span><span>{visCols.length}</span></div>}
          {visCols.map(id=>{
            const col=INV_COLS[hammerWhType].find(c=>c.id===id);if(!col)return null
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
            const hidden=INV_COLS[hammerWhType].filter(c=>!visCols.includes(c.id))
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
      {/* 搜索面板 */}
      {hammerPanel === 'search' && (
        <div className="hammer-panel">
          <input id="hm-search-inv" value={localSearch} onChange={e=>setLocalSearch(e.target.value)}
            placeholder="搜索SKU/商品名..." className="hammer-input" />
          {hammerSearch && <div style={{marginTop:4,textAlign:'right'}}>
            <button className="hammer-clear" onClick={()=>setHammerSearch('')}>{t("common.clear")}</button>
          </div>}
        </div>
      )}
      {/* 仓库类型面板 */}
      {hammerPanel === 'wh' && (
        <div className="hammer-panel">
          <div className="muted2 text-10 mb-4">仓库类型</div>
          <div className="hammer-btn-row">
            {Object.keys(INV_COLS).map(k => {
              if (k === 'platform_b' && channel !== 'jd') return null
              return <span key={k} onClick={() => switchWh(k)}
                className={'hammer-tab' + (hammerWhType === k ? ' active' : '')}>
                {INV_WH_LABEL[k]}
              </span>
            })}
          </div>
        </div>
      )}
    </div>
  )
}